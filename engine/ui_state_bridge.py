"""UI state bridge — Streamlit reads data/system_state.json."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from engine.config import (
    HANDSHAKE_EQUITY_BASE,
    STARTING_NAV,
    concurrent_risk_cap,
    max_daily_loss_cap,
)
from manus.capital_protection import RiskVerdict


def _unrealized_pnl(positions: list[dict[str, Any]], last_price: float | None) -> float:
    if not last_price or last_price <= 0:
        return 0.0
    from engine.config import POINT_VALUE

    total = 0.0
    for p in positions:
        entry = float(p.get("entry_price") or 0)
        contracts = int(p.get("contracts") or 0)
        direction = str(p.get("direction") or "LONG").upper()
        if entry <= 0 or contracts <= 0:
            continue
        points = (last_price - entry) if direction == "LONG" else (entry - last_price)
        total += points * POINT_VALUE * contracts
    return round(total, 2)


def build_system_state(orchestrator: Any, last_price: float | None = None) -> dict[str, Any]:
    session = orchestrator.session
    risk = orchestrator.risk
    pm = orchestrator.position_manager
    # Risk math stays on book NAV; dashboard account_nav is mark-to-market balance.
    risk_nav = risk.account_nav
    daily_pnl = session.realized_pnl_today
    max_conc = concurrent_risk_cap(risk_nav)
    open_risk = pm.total_open_risk()
    margin_pct = round((open_risk / max_conc) * 100, 1) if max_conc else 0.0
    hard_stop = max_daily_loss_cap(risk_nav)
    positions = pm.open_positions_list()
    unrealized = _unrealized_pnl(positions, last_price)
    nav = round(float(risk_nav) + float(unrealized), 2)

    # Detect data source
    broker = orchestrator.broker
    data_source = "sim"
    if hasattr(broker, "_using_alpaca") and broker._using_alpaca:
        data_source = "alpaca_spy_proxy"
    elif hasattr(broker, "_using_webull") and broker._using_webull:
        data_source = "webull_mes"

    return {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": orchestrator.config.symbol,
        "data_source": data_source,
        "last_price": last_price,
        "unrealized_pnl": unrealized,
        "session": {
            "realized_pnl_today": daily_pnl,
            "open_risk_notional": open_risk,
            "trades_today": session.trades_today,
            "halted": session.halted,
            "cycle_count": session.cycle_count,
            "last_risk_verdict": session.last_risk_verdict,
            "last_risk_reason": session.last_risk_reason,
        },
        "open_positions": positions,
        "account_nav": nav,
        "dashboard": {
            "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "symbol": orchestrator.config.symbol,
            "mode": "PAPER" if __import__("engine.config", fromlist=["forward_test_force_paper"]).forward_test_force_paper() else "LIVE",
            "data_source": data_source,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": round((daily_pnl / risk.starting_nav) * 100, 2) if risk.starting_nav else 0.0,
            "unrealized_pnl": unrealized,
            "last_price": last_price,
            "account_nav": nav,
            "starting_nav": risk.starting_nav,
            "hard_stop_limit": hard_stop,
            "hard_stop_distance": round(hard_stop + daily_pnl, 2),
            "open_risk_notional": open_risk,
            "max_concurrent_risk": max_conc,
            "margin_utilization_pct": margin_pct,
            "cycle_count": session.cycle_count,
            "trades_today": session.trades_today,
            "risk_verdict": session.last_risk_verdict or RiskVerdict.APPROVED.value,
            "risk_reason": session.last_risk_reason,
            "boot_status": "running",
            "heartbeat_state": "GREEN",
            "open_positions": positions,
        },
    }


def ensure_boot_system_state(path: Any = None) -> None:
    from pathlib import Path
    import json

    state_path = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "system_state.json"
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if float(raw.get("account_nav") or 0) > 0:
                return
        except Exception:
            pass
    equity = HANDSHAKE_EQUITY_BASE
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "MES",
        "account_nav": equity,
        "session": {"realized_pnl_today": 0.0, "cycle_count": 0, "trades_today": 0, "halted": False},
        "open_positions": [],
        "dashboard": {
            "account_nav": equity,
            "starting_nav": equity,
            "daily_pnl": 0.0,
            "boot_status": "starting",
            "mode": "PAPER",
        },
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[CELINE] Boot state seeded — handshake equity ${equity:,.2f}", flush=True)


def build_virtue_system_state(
    session: Any,
    *,
    last_price: float | None = None,
    regime: str = "",
    action: str = "",
    reason: str = "",
    adx: float = 0.0,
    atr_pct: float = 0.0,
    vwap_score: float = 50.0,
    twap_score: float = 50.0,
    blended_score: float = 50.0,
    data_source: str = "alpaca_spy_mes_proxy",
    last_risk_verdict: str = "",
    last_risk_reason: str = "",
) -> dict[str, Any]:
    """Dashboard payload for native virtue Wisdom loop (not VolumeWatch grade path)."""
    from engine.config import DEFAULT_STOP_TICKS, EXECUTION_SYMBOL, TICK_VALUE, forward_test_force_paper

    broker = session.broker
    risk = session.risk
    starting = float(getattr(risk, "starting_nav", STARTING_NAV) or STARTING_NAV)
    broker_equity = float(getattr(broker, "equity", 0.0) or 0.0)
    # Manus / sizing baseline unchanged — do not feed mark-to-market into risk caps.
    risk_nav = broker_equity or float(getattr(risk, "account_nav", 0.0) or 0.0) or starting
    net_dir, net_size = broker.net_exposure()
    positions: list[dict[str, Any]] = []
    for row in list(getattr(broker, "open_positions", None) or []):
        try:
            direction = str(row.get("direction") or "LONG").upper()
            contracts = int(row.get("size") or row.get("contracts") or 0)
            entry = float(row.get("price") or row.get("entry_price") or 0.0)
            if contracts <= 0:
                continue
            positions.append(
                {
                    "direction": direction,
                    "contracts": contracts,
                    "entry_price": entry,
                    "symbol": str(row.get("symbol") or EXECUTION_SYMBOL),
                    "order_id": str(row.get("order_id") or ""),
                }
            )
        except Exception:
            continue
    if not positions and net_size > 0:
        positions = [
            {
                "direction": net_dir,
                "contracts": int(net_size),
                "entry_price": float(last_price or 0.0),
                "symbol": EXECUTION_SYMBOL,
            }
        ]

    unrealized = _unrealized_pnl(positions, last_price)
    daily_pnl = float(getattr(session, "realized_pnl_today", 0.0) or 0.0)
    # Paper equity is frozen at STARTING_NAV; show current balance = start + realized + open P&L.
    # Live Webull equity is already broker mark-to-market when remote equity > 0.
    if forward_test_force_paper() or broker_equity <= 0:
        nav = round(starting + daily_pnl + unrealized, 2)
    else:
        nav = round(broker_equity, 2)
    open_risk = float(abs(net_size) * DEFAULT_STOP_TICKS * TICK_VALUE)
    max_conc = concurrent_risk_cap(risk_nav)
    hard_stop = max_daily_loss_cap(risk_nav)
    mode = "PAPER" if forward_test_force_paper() else "LIVE"
    now = datetime.now(timezone.utc).isoformat()

    return {
        "version": 1,
        "updated_at": now,
        "symbol": EXECUTION_SYMBOL,
        "strategy": "virtue_wisdom",
        "data_source": data_source,
        "last_price": last_price,
        "unrealized_pnl": unrealized,
        "account_nav": nav,
        "session": {
            "realized_pnl_today": daily_pnl,
            "open_risk_notional": open_risk,
            "trades_today": int(getattr(session, "trades_today", 0) or 0),
            "halted": bool(getattr(session, "halted", False)),
            "cycle_count": int(getattr(session, "cycle", 0) or 0),
            "last_risk_verdict": last_risk_verdict,
            "last_risk_reason": last_risk_reason,
            "last_action": str(getattr(session, "last_action", "") or action),
        },
        "open_positions": positions,
        "virtue": {
            "regime": regime,
            "action": action,
            "reason": reason,
            "adx": round(float(adx), 2),
            "atr_pct": round(float(atr_pct), 4),
            "vwap_score": round(float(vwap_score), 2),
            "twap_score": round(float(twap_score), 2),
            "blended_score": round(float(blended_score), 2),
            "exposure": net_dir,
            "contracts": int(net_size),
        },
        "dashboard": {
            "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "symbol": EXECUTION_SYMBOL,
            "mode": mode,
            "data_source": data_source,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": round((daily_pnl / max(starting, 1.0)) * 100, 2),
            "unrealized_pnl": unrealized,
            "last_price": last_price,
            "account_nav": nav,
            "starting_nav": starting,
            "hard_stop_limit": hard_stop,
            "hard_stop_distance": round(hard_stop + daily_pnl, 2),
            "open_risk_notional": open_risk,
            "max_concurrent_risk": max_conc,
            "margin_utilization_pct": round((open_risk / max_conc) * 100, 1) if max_conc else 0.0,
            "cycle_count": int(getattr(session, "cycle", 0) or 0),
            "trades_today": int(getattr(session, "trades_today", 0) or 0),
            "risk_verdict": last_risk_verdict or RiskVerdict.APPROVED.value,
            "risk_reason": last_risk_reason,
            "boot_status": "running",
            "heartbeat_state": "GREEN",
            "open_positions": positions,
            "strategy": "virtue_wisdom",
            "regime": regime,
            "action": action,
            "vwap_score": round(float(vwap_score), 2),
            "twap_score": round(float(twap_score), 2),
            "blended_score": round(float(blended_score), 2),
        },
    }


def persist_virtue_system_state(session: Any, **kwargs: Any) -> None:
    """Write data/system_state.json for Streamlit / cloud dashboard."""
    from pathlib import Path
    import json
    import logging

    log = logging.getLogger("virtue.ui_state")
    state_path = Path(__file__).resolve().parent.parent / "data" / "system_state.json"
    try:
        payload = build_virtue_system_state(session, **kwargs)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        log.exception("persist_virtue_system_state_failed err=%s", exc)
