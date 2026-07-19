"""
FutureMathics engine orchestrator — MES micro E-mini futures loop.

Cycle: POSITION MONITOR → HEARTBEAT → CELINE (VWAP trend) → MANUS RISK → ROUTE
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from celine.position_sizing import FuturesSizeResult, size_mes_position
from celine.signals import FuturesSignalEngine, FuturesTradeSignal, SignalDirection
from engine.config import EXECUTION_SYMBOL, MAX_ALLOWED_SPREAD_TICKS, MIN_SECONDS_BETWEEN_TRADES, STARTING_NAV, forward_test_force_paper, paper_max_open_mes_positions, ticks_to_dollars
from engine.futures_broker_adapter import FuturesBrokerAdapter, OrderExecutionResult, RoutingMode
from engine.futures_position_manager import FuturesPositionManager
from engine.trade_history import TradeHistoryDB, TradeRecord
from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict
from manus.heartbeat import BrokerHeartbeatAgent, HeartbeatState

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    symbol: str = EXECUTION_SYMBOL
    loop_interval_s: float = 2.0
    print_state: bool = True
    demo_cycles: int = 10


@dataclass
class SessionState:
    realized_pnl_today: float = 0.0
    open_risk_notional: float = 0.0
    trades_today: int = 0
    halted: bool = False
    cycle_count: int = 0
    last_risk_verdict: str = ""
    last_risk_reason: str = ""
    last_heartbeat: dict[str, Any] = field(default_factory=dict)
    last_trade_time: float = 0.0  # Timestamp of last trade for cooldown


class FuturesOrchestrator:
    def __init__(
        self,
        *,
        config: OrchestratorConfig | None = None,
        celine: FuturesSignalEngine | None = None,
        broker: FuturesBrokerAdapter | None = None,
        risk: CapitalProtectionMatrix | None = None,
    ) -> None:
        self.config = config or OrchestratorConfig()
        self.celine = celine or FuturesSignalEngine(symbol=self.config.symbol)
        self.broker = broker or FuturesBrokerAdapter()
        self.risk = risk or CapitalProtectionMatrix(starting_nav=STARTING_NAV, account_nav=STARTING_NAV)
        self.position_manager = FuturesPositionManager()
        self.session = SessionState()
        self.heartbeat = BrokerHeartbeatAgent(
            brokers={"mes_feed": self.broker.health_check},
            interval_s=5.0,
        )
        self._state_path = ROOT / "data" / "system_state.json"
        self.trade_db = TradeHistoryDB()

    def _print(self, label: str, fields: dict[str, Any]) -> None:
        if not self.config.print_state:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {label}", flush=True)
        for k, v in fields.items():
            print(f"  {k:<22} {v}", flush=True)

    async def _persist_state(self) -> None:
        from engine.ui_state_bridge import build_system_state

        payload = build_system_state(self)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _phase_heartbeat(self) -> tuple[bool, dict[str, Any]]:
        await self.heartbeat.run_once()
        snap = self.heartbeat.snapshot()
        self.session.last_heartbeat = snap
        ok = all(e.get("state") != HeartbeatState.DEAD.value for e in snap.values()) if snap else True
        self._print("HEARTBEAT OK", {"mes_feed": "GREEN" if ok else "DEAD"})
        return ok, snap

    async def _phase_position_monitor(self, last_price: float) -> float:
        result = self.position_manager.monitor_tick(last_price)
        if not result.exit_requests:
            return 0.0
        
        # Log completed trades to database
        for exit_req in result.exit_requests:
            self._log_completed_trade(exit_req, last_price)
        
        total = sum(e.realized_pnl for e in result.exit_requests)
        self.session.realized_pnl_today = self.position_manager.realized_pnl_today
        self.session.open_risk_notional = self.position_manager.total_open_risk()
        self.risk.update_nav(self.risk.account_nav + total)
        if self.session.realized_pnl_today <= -self.risk.max_daily_loss_hard:
            self.session.halted = True
            self.session.last_risk_verdict = RiskVerdict.HALT.value
            self.session.last_risk_reason = "hard_daily_loss_breach_2pct_nav"
        self._print(
            "POSITION MONITOR",
            {
                "exits": len(result.exit_requests),
                "pnl": f"${total:.2f}",
                "daily_pnl": f"${self.session.realized_pnl_today:.2f}",
            },
        )
        return total

    def _phase_manus_risk(self, proposed_risk: float) -> tuple[RiskVerdict, str]:
        open_risk = self.position_manager.total_open_risk()
        return self.risk.evaluate(
            realized_pnl_today=self.session.realized_pnl_today,
            open_risk_notional=open_risk,
            proposed_trade_risk=proposed_risk,
            vrp_regime="BULL",
            sandbox_fallback=True,
        )
    
    def _log_completed_trade(self, exit_req: Any, exit_price: float) -> None:
        """Log a completed trade to the history database."""
        try:
            exit_time = datetime.now(timezone.utc)
            session_date = exit_time.strftime("%Y-%m-%d")
            
            # Calculate R-multiple (actual P&L / risk)
            r_multiple = exit_req.realized_pnl / exit_req.max_loss if exit_req.max_loss > 0 else 0.0
            
            # Calculate reward amount (theoretical max profit at target - assume 2:1 R:R)
            reward_amount = exit_req.max_loss * 2.0
            
            # Estimate entry time (position likely opened recently - estimate 60s ago)
            entry_dt = exit_time.replace(second=max(0, exit_time.second - 60))
            duration_seconds = max(1.0, (exit_time - entry_dt).total_seconds())
            
            trade_record = TradeRecord(
                trade_id=exit_req.trade_id,
                position_id=exit_req.position_id,
                symbol=self.config.symbol,
                direction=exit_req.direction,
                contracts=exit_req.contracts,
                entry_price=exit_req.entry_price,
                exit_price=exit_price,
                stop_price=0.0,  # Not available in exit_req
                target_price=0.0,  # Not available in exit_req
                entry_time=entry_dt.isoformat(),
                exit_time=exit_time.isoformat(),
                duration_seconds=duration_seconds,
                realized_pnl=exit_req.realized_pnl,
                risk_amount=exit_req.max_loss,
                reward_amount=reward_amount,
                r_multiple=r_multiple,
                exit_reason=exit_req.reason,
                nav_at_entry=self.risk.account_nav - exit_req.realized_pnl,
                nav_at_exit=self.risk.account_nav,
                daily_pnl_before=self.session.realized_pnl_today - exit_req.realized_pnl,
                session_date=session_date,
                cycle_number=self.session.cycle_count,
            )
            
            self.trade_db.insert_trade(trade_record)
            logger.info("Trade logged to database: %s P&L=%.2f R=%.2f", 
                       exit_req.trade_id, exit_req.realized_pnl, r_multiple)
        except Exception as e:
            logger.error("Failed to log trade to database: %s", e)

    async def run_cycle(self) -> dict[str, Any]:
        self.session.cycle_count += 1
        self._print("CYCLE START", {"n": self.session.cycle_count, "symbol": self.config.symbol})

        if self.session.halted:
            await self._persist_state()
            return {"action": "HALT", "reason": self.session.last_risk_reason or "session_halted"}

        ctx = await self.broker.resolve_market_context()
        tick = ctx.get("tick") or {}
        last_price = float(tick.get("price") or tick.get("last") or 0)

        await self._phase_position_monitor(last_price)
        if self.session.halted:
            await self._persist_state()
            return {"action": "HALT", "reason": "session_halted"}

        await self._phase_heartbeat()
        snapshot, signal = await self.celine.refresh(ctx)

        if snapshot is None or not snapshot.data_valid:
            self._print("CELINE SKIP", {"reason": "no_tick"})
            await self._persist_state()
            return {"action": "SKIP", "reason": "no_tick"}

        spread_ok = snapshot.spread_ticks <= MAX_ALLOWED_SPREAD_TICKS
        self._print(
            "CELINE SIGNAL",
            {
                "price": f"{snapshot.last_price:.2f}",
                "vwap": f"{snapshot.vwap:.2f}",
                "regime": snapshot.trend_regime.value,
                "spread_ticks": snapshot.spread_ticks,
            },
        )

        if signal is None or signal.direction == SignalDirection.FLAT:
            await self._persist_state()
            return {"action": "SKIP", "reason": "neutral_regime"}
        
        # NEW: Check confidence threshold (now enforced in signal generation, but double-check)
        if signal.confidence < 0.60:
            await self._persist_state()
            return {"action": "SKIP", "reason": "low_confidence"}

        if not spread_ok:
            await self._persist_state()
            return {"action": "SKIP", "reason": "spread_too_wide"}

        max_open = paper_max_open_mes_positions() if forward_test_force_paper() else 1
        if len(self.position_manager.positions) >= max_open:
            await self._persist_state()
            return {"action": "SKIP", "reason": "max_open_positions"}
        
        # NEW: Trade cooldown - prevent overtrading
        import time
        now = time.time()
        if now - self.session.last_trade_time < MIN_SECONDS_BETWEEN_TRADES:
            await self._persist_state()
            return {"action": "SKIP", "reason": "trade_cooldown_active"}

        budget = self.risk.per_trade_risk_budget()
        size = size_mes_position(direction=signal.direction.value, risk_budget=budget)
        if size.contracts <= 0:
            self._print("MANUS RISK", {"verdict": "SKIP", "reason": "zero_contracts"})
            await self._persist_state()
            return {"action": "SKIP", "reason": "zero_contracts"}

        verdict, reason = self._phase_manus_risk(size.total_trade_risk)
        self.session.last_risk_verdict = verdict.value
        self.session.last_risk_reason = reason
        self._print(
            "MANUS RISK",
            {
                "verdict": verdict.value,
                "reason": reason,
                "proposed_risk": f"${size.total_trade_risk:.2f}",
                "contracts": size.contracts,
            },
        )

        if verdict == RiskVerdict.HALT:
            self.session.halted = True
            self._print("ROUTE HALT", {"reason": reason})
            await self._persist_state()
            return {"action": "HALT", "reason": reason}

        paper = forward_test_force_paper()
        execution = await self.broker.submit_order(
            direction=signal.direction.value,
            contracts=size.contracts,
            limit_price=signal.entry_price,
        )
        trade_id = f"FM-{uuid.uuid4().hex[:10].upper()}"
        self.position_manager.open_position(
            symbol=self.config.symbol,
            direction=signal.direction.value,
            contracts=size.contracts,
            entry_price=execution.fill_price,
            stop_ticks=size.stop_ticks,
            target_ticks=size.target_ticks,
            max_loss=size.total_trade_risk,
            trade_id=trade_id,
            nav_at_entry=self.risk.account_nav,
            daily_pnl_at_entry=self.session.realized_pnl_today,
            cycle_at_entry=self.session.cycle_count,
        )
        self.session.trades_today += 1
        self.session.open_risk_notional = self.position_manager.total_open_risk()
        
        # NEW: Update last trade time for cooldown
        import time
        self.session.last_trade_time = time.time()

        action = "PAPER_ROUTE" if paper else "LIVE_ROUTE"
        self._print(
            action,
            {
                "direction": signal.direction.value,
                "contracts": size.contracts,
                "total_risk": f"${size.total_trade_risk:.2f}",
                "fill_price": execution.fill_price,
                "execution_status": execution.status,
                "order_id": execution.order_id,
            },
        )
        await self._persist_state()
        return {"action": action, "execution": execution.status, "order_id": execution.order_id}

    async def run_demo(self, cycles: int | None = None) -> None:
        n = cycles if cycles is not None else self.config.demo_cycles
        for _ in range(n):
            await self.run_cycle()
            await asyncio.sleep(self.config.loop_interval_s)


async def _main() -> None:
    orch = FuturesOrchestrator()
    print("FutureMathics — MES paper demo", flush=True)
    await orch.run_demo()


if __name__ == "__main__":
    asyncio.run(_main())
