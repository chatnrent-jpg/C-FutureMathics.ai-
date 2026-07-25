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
    nav = risk.account_nav
    daily_pnl = session.realized_pnl_today
    max_conc = concurrent_risk_cap(nav)
    open_risk = pm.total_open_risk()
    margin_pct = round((open_risk / max_conc) * 100, 1) if max_conc else 0.0
    hard_stop = max_daily_loss_cap(nav)
    positions = pm.open_positions_list()
    unrealized = _unrealized_pnl(positions, last_price)

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
