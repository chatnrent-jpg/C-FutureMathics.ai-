"""
Grade-path MES orchestrator — VolumeWatch score brain, futures execution body.

Cycle: VW GRADE → PATH ENGINE → POSITION MONITOR (hard stop) → MANUS → ROUTE
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from celine.grade_path_engine import (
    GradeAction,
    GradePathConfig,
    GradePathEngine,
)
from engine.config import (
    EXECUTION_SYMBOL,
    GRADE_ALLOW_STALE,
    GRADE_CONTRACTS,
    GRADE_CYCLE_INTERVAL_S,
    GRADE_DAILY_LOSS_HALT,
    GRADE_DAILY_PROFIT_LOCK,
    GRADE_HARD_STOP_TICKS,
    GRADE_LONG_ENTRY,
    GRADE_LONG_EXIT,
    GRADE_PATH_EPSILON,
    GRADE_SCORE_SOURCE,
    GRADE_SHORT_ENTRY,
    GRADE_SHORT_EXIT,
    GRADE_STALE_SECONDS,
    STARTING_NAV,
    forward_test_force_paper,
    ticks_to_dollars,
)
from engine.futures_broker_adapter import FuturesBrokerAdapter
from engine.futures_orchestrator import OrchestratorConfig, SessionState
from engine.futures_position_manager import FuturesPositionManager
from engine.trade_history import TradeHistoryDB, TradeRecord
from engine.volumewatch_grade_feed import fetch_volumewatch_grade
from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict
from manus.heartbeat import BrokerHeartbeatAgent

logger = logging.getLogger(__name__)


@dataclass
class GradeOrchestrator:
    config: OrchestratorConfig = field(
        default_factory=lambda: OrchestratorConfig(loop_interval_s=GRADE_CYCLE_INTERVAL_S)
    )
    broker: FuturesBrokerAdapter = field(default_factory=FuturesBrokerAdapter)
    risk: CapitalProtectionMatrix = field(
        default_factory=lambda: CapitalProtectionMatrix(
            starting_nav=STARTING_NAV, account_nav=STARTING_NAV
        )
    )
    position_manager: FuturesPositionManager = field(default_factory=FuturesPositionManager)
    session: SessionState = field(default_factory=SessionState)
    trade_db: TradeHistoryDB = field(default_factory=TradeHistoryDB)
    engine: GradePathEngine = field(default_factory=GradePathEngine)
    _state_path: Path = field(default_factory=lambda: ROOT / "data" / "system_state.json")
    _path_state_file: Path = field(default_factory=lambda: ROOT / "data" / "grade_path_state.json")

    def __post_init__(self) -> None:
        self.engine = GradePathEngine.load(
            self._path_state_file,
            GradePathConfig(
                long_entry=GRADE_LONG_ENTRY,
                long_exit=GRADE_LONG_EXIT,
                short_entry=GRADE_SHORT_ENTRY,
                short_exit=GRADE_SHORT_EXIT,
                path_epsilon=GRADE_PATH_EPSILON,
            ),
        )
        self.heartbeat = BrokerHeartbeatAgent(
            brokers={"mes_feed": self.broker.health_check},
            interval_s=5.0,
        )
        self._restore_open_positions()
        self._reconcile_exposure()

    def _restore_open_positions(self) -> None:
        """Keep paper/live open trades across service restarts."""
        import json

        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("restore_state_read_failed err=%s", exc)
            return
        rows = raw.get("open_positions") or (raw.get("dashboard") or {}).get("open_positions") or []
        n = self.position_manager.restore_from_dicts(rows)
        if n:
            logger.info("restored_open_positions count=%s", n)
        session = raw.get("session") or {}
        try:
            realized = float(session.get("realized_pnl_today") or 0)
            self.session.realized_pnl_today = realized
            # Keep position manager in sync — exits overwrite session from pm.realized_pnl_today
            self.position_manager.realized_pnl_today = realized
            self.session.trades_today = int(session.get("trades_today") or 0)
        except Exception:
            pass
        lp = raw.get("last_price") or (raw.get("dashboard") or {}).get("last_price")
        try:
            if lp is not None and float(lp) > 0:
                self._last_price = float(lp)
        except Exception:
            pass

    def _reconcile_exposure(self) -> None:
        has_long = any(p.direction.upper() == "LONG" for p in self.position_manager.positions.values())
        has_short = any(p.direction.upper() == "SHORT" for p in self.position_manager.positions.values())
        self.engine.sync_exposure_from_broker(has_long, has_short)

    def _print(self, label: str, fields: dict[str, Any]) -> None:
        if not self.config.print_state:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {label}", flush=True)
        for k, v in fields.items():
            print(f"  {k:<22} {v}", flush=True)

    async def _persist(self, last_price: float | None = None) -> None:
        from engine.ui_state_bridge import build_system_state
        import json

        if last_price is not None and last_price > 0:
            self._last_price = float(last_price)
        payload = build_system_state(self, last_price=getattr(self, "_last_price", None))
        payload["strategy"] = "volumewatch_grade_path"
        payload["grade"] = self.engine.to_dict()
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.engine.save(self._path_state_file)

    def _log_exit(self, exit_req: Any, exit_price: float) -> None:
        try:
            exit_time = datetime.now(timezone.utc)
            r_multiple = exit_req.realized_pnl / exit_req.max_loss if exit_req.max_loss > 0 else 0.0
            self.trade_db.insert_trade(
                TradeRecord(
                    trade_id=exit_req.trade_id,
                    position_id=exit_req.position_id,
                    symbol=self.config.symbol,
                    direction=exit_req.direction,
                    contracts=exit_req.contracts,
                    entry_price=exit_req.entry_price,
                    exit_price=exit_price,
                    stop_price=0.0,
                    target_price=0.0,
                    entry_time=exit_time.isoformat(),
                    exit_time=exit_time.isoformat(),
                    duration_seconds=1.0,
                    realized_pnl=exit_req.realized_pnl,
                    risk_amount=exit_req.max_loss,
                    reward_amount=exit_req.max_loss * 2.0,
                    r_multiple=r_multiple,
                    exit_reason=exit_req.reason,
                    nav_at_entry=self.risk.account_nav - exit_req.realized_pnl,
                    nav_at_exit=self.risk.account_nav,
                    daily_pnl_before=self.session.realized_pnl_today - exit_req.realized_pnl,
                    session_date=exit_time.strftime("%Y-%m-%d"),
                    cycle_number=self.session.cycle_count,
                )
            )
        except Exception as exc:
            logger.error("grade_trade_log_failed err=%s", exc)

    async def _apply_hard_stop_exits(self, last_price: float) -> None:
        result = self.position_manager.monitor_tick(last_price)
        for exit_req in result.exit_requests:
            close_dir = "SELL" if exit_req.direction.upper() == "LONG" else "BUY"
            await self.broker.submit_order(
                direction=close_dir,
                contracts=exit_req.contracts,
                limit_price=last_price,
            )
            self._log_exit(exit_req, last_price)
            self.risk.update_nav(self.risk.account_nav + exit_req.realized_pnl)
            self._print(
                "HARD STOP EXIT",
                {"reason": exit_req.reason, "pnl": f"${exit_req.realized_pnl:.2f}"},
            )
        self.session.realized_pnl_today = self.position_manager.realized_pnl_today
        self.session.open_risk_notional = self.position_manager.total_open_risk()
        if self.session.realized_pnl_today <= -GRADE_DAILY_LOSS_HALT:
            self.session.halted = True
            self.session.last_risk_reason = "grade_daily_loss_halt"
        self._reconcile_exposure()

    async def _force_grade_exit(self, *, reason: str, last_price: float) -> None:
        for pid in list(self.position_manager.positions.keys()):
            exit_req = self.position_manager.force_close(
                position_id=pid,
                exit_price=last_price,
                reason=reason,
            )
            if not exit_req:
                continue
            close_dir = "SELL" if exit_req.direction.upper() == "LONG" else "BUY"
            await self.broker.submit_order(
                direction=close_dir,
                contracts=exit_req.contracts,
                limit_price=last_price,
            )
            self._log_exit(exit_req, last_price)
            self.risk.update_nav(self.risk.account_nav + exit_req.realized_pnl)
            self._print("GRADE EXIT", {"reason": reason, "pnl": f"${exit_req.realized_pnl:.2f}"})
        self.session.realized_pnl_today = self.position_manager.realized_pnl_today
        self.session.open_risk_notional = self.position_manager.total_open_risk()
        self._reconcile_exposure()

    async def _enter(self, *, direction: str, last_price: float, score: float) -> dict[str, Any]:
        if self.session.realized_pnl_today >= GRADE_DAILY_PROFIT_LOCK:
            return {"action": "SKIP", "reason": "daily_profit_lock", "score": score}

        contracts = max(1, int(GRADE_CONTRACTS))
        stop_ticks = int(GRADE_HARD_STOP_TICKS)
        target_ticks = 10_000  # grade owns TP; hard stop only
        max_loss = ticks_to_dollars(stop_ticks, contracts)

        verdict, reason = self.risk.evaluate(
            realized_pnl_today=self.session.realized_pnl_today,
            open_risk_notional=self.position_manager.total_open_risk(),
            proposed_trade_risk=max_loss,
            vrp_regime="BULL",
            sandbox_fallback=True,
        )
        self.session.last_risk_verdict = verdict.value
        self.session.last_risk_reason = reason
        self._print("MANUS RISK", {"verdict": verdict.value, "reason": reason, "contracts": contracts})

        if verdict == RiskVerdict.HALT:
            self.session.halted = True
            return {"action": "HALT", "reason": reason}

        execution = await self.broker.submit_order(
            direction=direction,
            contracts=contracts,
            limit_price=last_price,
        )
        fill = float(getattr(execution, "fill_price", last_price) or last_price)
        trade_id = f"GW-{uuid.uuid4().hex[:10].upper()}"
        self.position_manager.open_position(
            symbol=self.config.symbol or EXECUTION_SYMBOL,
            direction=direction,
            contracts=contracts,
            entry_price=fill,
            stop_ticks=stop_ticks,
            target_ticks=target_ticks,
            max_loss=max_loss,
            trade_id=trade_id,
            nav_at_entry=self.risk.account_nav,
            daily_pnl_at_entry=self.session.realized_pnl_today,
            cycle_at_entry=self.session.cycle_count,
            manage_mode="grade",
        )
        self.session.trades_today += 1
        self.session.last_trade_time = time.time()
        self.session.open_risk_notional = self.position_manager.total_open_risk()
        self._print(
            "GRADE ROUTE",
            {
                "direction": direction,
                "fill": fill,
                "contracts": contracts,
                "score": f"{score:.2f}",
                "hard_stop_ticks": stop_ticks,
                "paper": forward_test_force_paper(),
                "order": execution.order_id,
            },
        )
        return {
            "action": "OPEN",
            "direction": direction,
            "fill": fill,
            "contracts": contracts,
            "score": score,
            "trade_id": trade_id,
        }

    async def run_cycle(self) -> dict[str, Any]:
        self.session.cycle_count += 1
        self._print("GRADE CYCLE", {"n": self.session.cycle_count, "mode": "volumewatch_path"})

        if self.session.halted:
            await self._persist()
            return {"action": "HALT", "reason": self.session.last_risk_reason or "halted"}

        grade = fetch_volumewatch_grade(
            score_source=GRADE_SCORE_SOURCE,  # type: ignore[arg-type]
            stale_seconds=GRADE_STALE_SECONDS,
            allow_stale=GRADE_ALLOW_STALE,
        )
        if grade is None:
            self._print("GRADE SKIP", {"reason": "vw_grade_unavailable_or_stale"})
            await self._persist()
            return {"action": "SKIP", "reason": "vw_grade_unavailable"}

        ctx = await self.broker.resolve_market_context()
        tick = ctx.get("tick") or {}
        last_price = float(tick.get("price") or tick.get("last") or 0)
        if last_price <= 0:
            await self._persist()
            return {"action": "SKIP", "reason": "no_price"}

        await self.heartbeat.run_once()
        await self._apply_hard_stop_exits(last_price)

        decision = self.engine.update(grade.score)
        self._print(
            "GRADE PATH",
            {
                "score": f"{grade.score:.2f}",
                "grade": grade.grade,
                "path": decision.path.value,
                "action": decision.action.value,
                "reason": decision.reason,
                "vw_age_s": grade.age_seconds,
            },
        )

        if decision.action == GradeAction.EXIT_LONG:
            await self._force_grade_exit(reason="grade_long_exit", last_price=last_price)
            await self._persist(last_price)
            return {"action": "EXIT_LONG", "score": grade.score, "reason": decision.reason}

        if decision.action == GradeAction.EXIT_SHORT:
            await self._force_grade_exit(reason="grade_short_exit", last_price=last_price)
            await self._persist(last_price)
            return {"action": "EXIT_SHORT", "score": grade.score, "reason": decision.reason}

        if self.position_manager.positions:
            await self._persist(last_price)
            return {"action": "HOLD", "reason": decision.reason, "score": grade.score}

        if decision.action == GradeAction.ENTER_LONG:
            out = await self._enter(direction="LONG", last_price=last_price, score=grade.score)
            if out.get("action") != "OPEN":
                self.engine.sync_exposure_from_broker(False, False)
            await self._persist(last_price)
            return out

        if decision.action == GradeAction.ENTER_SHORT:
            out = await self._enter(direction="SHORT", last_price=last_price, score=grade.score)
            if out.get("action") != "OPEN":
                self.engine.sync_exposure_from_broker(False, False)
            await self._persist(last_price)
            return out

        await self._persist(last_price)
        return {"action": "FLAT", "reason": decision.reason, "score": grade.score}
