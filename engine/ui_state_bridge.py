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
    if hasattr(broker, "data_source") and broker.data_source:
        data_source = str(broker.data_source)
    elif hasattr(broker, "_data_source") and broker._data_source:
        data_source = str(broker._data_source)
    elif hasattr(broker, "_using_alpaca") and broker._using_alpaca:
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
    heartbeat_state: str = "",
) -> dict[str, Any]:
    """Dashboard payload for native virtue Wisdom loop (not VolumeWatch grade path)."""
    from engine.config import (
        EXECUTION_SYMBOL,
        VIRTUE_POSITION_STOP_DOLLARS,
        forward_test_force_paper,
    )

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
    # Paper + live: book equity compounds across days; day PnL is a separate Temperance bucket.
    # Do NOT recompute NAV as starting + daily_pnl (that resets account_nav every day roll).
    if broker_equity > 0:
        nav = round(broker_equity + unrealized, 2)
    else:
        nav = round(starting + daily_pnl + unrealized, 2)
    open_risk = float(VIRTUE_POSITION_STOP_DOLLARS) if abs(net_size) > 0 else 0.0
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
        "book_equity": round(broker_equity, 2) if broker_equity > 0 else round(starting, 2),
        "session": {
            "realized_pnl_today": daily_pnl,
            "session_date_et": str(getattr(session, "session_date_et", "") or ""),
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
            "heartbeat_state": str(
                heartbeat_state
                or getattr(session, "last_heartbeat_state", None)
                or "UNKNOWN"
            ),
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


def load_persisted_book_equity(default: float | None = None) -> float:
    """
    Load compounded paper/live book equity from system_state.json.
    Falls back to account_nav − unrealized, then STARTING_NAV.
    """
    from pathlib import Path
    import json
    import logging

    log = logging.getLogger("virtue.ui_state")
    base = float(default if default is not None else STARTING_NAV)
    state_path = Path(__file__).resolve().parent.parent / "data" / "system_state.json"
    try:
        if not state_path.is_file():
            return base
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        book = float(raw.get("book_equity") or 0.0)
        if book > 0:
            return round(book, 2)
        nav = float(raw.get("account_nav") or 0.0)
        unreal = float(raw.get("unrealized_pnl") or 0.0)
        if nav > 0:
            return round(max(base, nav - unreal), 2)
    except Exception as exc:
        log.exception("load_persisted_book_equity_failed err=%s", exc)
    return base


def load_persisted_open_positions() -> list[dict[str, Any]]:
    """Restore paper open positions from system_state.json (survive restarts)."""
    from pathlib import Path
    import json
    import logging

    from engine.config import EXECUTION_SYMBOL

    log = logging.getLogger("virtue.ui_state")
    state_path = Path(__file__).resolve().parent.parent / "data" / "system_state.json"
    out: list[dict[str, Any]] = []
    try:
        if not state_path.is_file():
            return out
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        for row in list(raw.get("open_positions") or []):
            try:
                direction = str(row.get("direction") or "").upper()
                contracts = int(row.get("contracts") or row.get("size") or 0)
                entry = float(row.get("entry_price") or row.get("price") or 0.0)
                if direction not in {"LONG", "SHORT"} or contracts < 1 or entry <= 0:
                    continue
                out.append(
                    {
                        "direction": direction,
                        "size": contracts,
                        "price": entry,
                        "entry_price": entry,
                        "symbol": str(row.get("symbol") or EXECUTION_SYMBOL),
                        "order_id": str(row.get("order_id") or ""),
                        "source": "persisted_paper",
                    }
                )
            except Exception:
                continue
    except Exception as exc:
        log.exception("load_persisted_open_positions_failed err=%s", exc)
    return out


def load_persisted_day_bucket(
    today_et: str,
    *,
    state_path: Any | None = None,
) -> dict[str, Any]:
    """
    Restore same-ET-day Temperance counters after restart/deploy (Justice).

    If persisted session_date_et != today_et, returns zeros (true new day).
    Deploy must not wipe Closed-today PnL when the calendar day is unchanged.
    """
    from pathlib import Path
    import json
    import logging

    log = logging.getLogger("virtue.ui_state")
    empty = {
        "session_date_et": "",
        "realized_pnl_today": 0.0,
        "trades_today": 0,
        "restored": False,
    }
    path = (
        Path(state_path)
        if state_path is not None
        else Path(__file__).resolve().parent.parent / "data" / "system_state.json"
    )
    try:
        if not path.is_file():
            return empty
        raw = json.loads(path.read_text(encoding="utf-8"))
        sess = raw.get("session") or {}
        persisted_date = str(sess.get("session_date_et") or "").strip()
        pnl = float(sess.get("realized_pnl_today") or 0.0)
        trades = int(sess.get("trades_today") or 0)
        # Legacy payloads: no session_date_et — use updated_at ET date if present.
        if not persisted_date:
            updated = str(raw.get("updated_at") or "")
            if updated:
                try:
                    from datetime import datetime
                    from zoneinfo import ZoneInfo

                    ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    persisted_date = ts.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
                except Exception:
                    persisted_date = ""
        if not persisted_date or persisted_date != str(today_et):
            return empty
        return {
            "session_date_et": persisted_date,
            "realized_pnl_today": round(pnl, 2),
            "trades_today": max(0, trades),
            "restored": True,
        }
    except Exception as exc:
        log.exception("load_persisted_day_bucket_failed err=%s", exc)
        return empty


__all__ = [
    "build_system_state",
    "build_virtue_system_state",
    "ensure_boot_system_state",
    "load_persisted_book_equity",
    "load_persisted_day_bucket",
    "load_persisted_open_positions",
    "persist_virtue_system_state",
]
