"""UI state bridge — Streamlit reads data/system_state.json."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from engine.config import (
    HANDSHAKE_EQUITY_BASE,
    STARTING_NAV,
    VIRTUE_MULTI_TP_COOLDOWN_S,
    VIRTUE_MULTI_TP_COOLDOWN_STREAK,
    VIRTUE_PIPELINE_BULL_SHORT_PENALTY,
    VIRTUE_PIPELINE_LONG_BLEND_BASE,
    VIRTUE_PIPELINE_SHORT_BLEND_BASE,
    VIRTUE_PNL_LOCK_ARM_PEAK,
    VIRTUE_PNL_LOCK_HARD_FLOOR,
    VIRTUE_PNL_LOCK_FLOOR_FRAC,
    VIRTUE_SCORE_LONG_ENTER,
    VIRTUE_SCORE_SHORT_ENTER,
    VIRTUE_TEMPERANCE_BASE_CONTRACTS,
    VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER,
    VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER,
    VIRTUE_TEMPERANCE_LOSS_STREAK_MIN,
    VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE,
    VIRTUE_TIME_DECAY_MAX_CYCLES,
    VIRTUE_VELOCITY_ADX_FLOOR,
    VIRTUE_VELOCITY_PENALTY_PER_ADX,
    concurrent_risk_cap,
    max_daily_loss_cap,
)
from manus.capital_protection import RiskVerdict


def _pipeline_remaining(session: Any) -> int:
    resume = int(getattr(session, "pipeline_resume_cycle", 0) or 0)
    cur = int(getattr(session, "cycle", 0) or 0)
    if resume <= 0 or cur >= resume:
        return 0
    return max(0, resume - cur)


def _pipeline_locked(session: Any) -> bool:
    return _pipeline_remaining(session) > 0


def _layer1_cooldown_fields(session: Any) -> dict[str, Any]:
    """
    Layer-1 dashboard fields: absolute cycle lock takes priority over multi-TP seconds.
    remaining_s carries cycle delta while the absolute lock is active (poll contract).
    """
    pipe_rem = _pipeline_remaining(session)
    wall_rem = _multi_tp_cooldown_remaining_s(session)
    if pipe_rem > 0:
        return {
            "multi_tp_cooldown_active": True,
            "multi_tp_cooldown_remaining_s": pipe_rem,
        }
    return {
        "multi_tp_cooldown_active": wall_rem > 0,
        "multi_tp_cooldown_remaining_s": wall_rem,
    }


def _entry_pipeline_layer1_fields(session: Any) -> dict[str, Any]:
    pipe_rem = _pipeline_remaining(session)
    wall_active = _multi_tp_cooldown_active(session)
    clear = pipe_rem <= 0 and not wall_active
    return {
        "layer1_streak_clear": clear,
        "pipeline_resume_cycle": int(getattr(session, "pipeline_resume_cycle", 0) or 0),
        "pipeline_locked": pipe_rem > 0,
        "pipeline_remaining_cycles": pipe_rem,
    }


def _time_decay_pipeline_fields(session: Any) -> dict[str, Any]:
    marker = getattr(session, "entry_cycle_marker", None)
    cur = int(getattr(session, "cycle", 0) or 0)
    max_c = int(VIRTUE_TIME_DECAY_MAX_CYCLES)
    if marker is None:
        return {
            "entry_cycle_marker": None,
            "time_decay_elapsed_cycles": 0,
            "time_decay_max_cycles": max_c,
        }
    elapsed = max(0, cur - int(marker))
    return {
        "entry_cycle_marker": int(marker),
        "time_decay_elapsed_cycles": elapsed,
        "time_decay_max_cycles": max_c,
    }


def _velocity_penalty(session: Any) -> float:
    """Unknown/zero ADX → full floor penalty (Justice: do not invent strength)."""
    try:
        adx = float(getattr(session, "last_adx", 0.0) or 0.0)
    except (TypeError, ValueError):
        adx = 0.0
    floor = float(VIRTUE_VELOCITY_ADX_FLOOR)
    if adx <= 0:
        return floor * float(VIRTUE_VELOCITY_PENALTY_PER_ADX)
    if adx >= floor:
        return 0.0
    return (floor - adx) * float(VIRTUE_VELOCITY_PENALTY_PER_ADX)


def _temperance_pipeline_fields(session: Any) -> dict[str, Any]:
    """Mirror temperance + velocity gates for system_state.json (no main import)."""
    losses = int(getattr(session, "consecutive_losses", 0) or 0)
    reason = str(getattr(session, "last_reason", "") or "").strip().lower()
    raw_buf = 0.0
    if losses >= int(VIRTUE_TEMPERANCE_LOSS_STREAK_MIN):
        raw_buf = max(raw_buf, float(VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER))
    if reason == "course_correct":
        raw_buf = max(raw_buf, float(VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER))
    base = max(1, int(VIRTUE_TEMPERANCE_BASE_CONTRACTS))
    bias = str(getattr(session, "macro_bias", "NEUTRAL") or "NEUTRAL").upper()
    vel = _velocity_penalty(session)
    try:
        adx = float(getattr(session, "last_adx", 0.0) or 0.0)
    except (TypeError, ValueError):
        adx = 0.0
    # Strong with-trend: waive blend widen (matches main.effective_temperance_blend_buffer).
    long_buf = 0.0 if (
        adx > 0
        and adx >= float(VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE)
        and bias == "BULL"
    ) else raw_buf
    short_buf = 0.0 if (
        adx > 0
        and adx >= float(VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE)
        and bias == "BEAR"
    ) else raw_buf
    pipe_long = float(VIRTUE_PIPELINE_LONG_BLEND_BASE) + vel + long_buf
    pipe_short = float(VIRTUE_PIPELINE_SHORT_BLEND_BASE) - vel - short_buf
    if bias == "BULL":
        pipe_short -= float(VIRTUE_PIPELINE_BULL_SHORT_PENALTY)
    return {
        "temperance_base_contracts": base,
        "temperance_blend_buffer": raw_buf,
        "temperance_effective_long_buffer": long_buf,
        "temperance_effective_short_buffer": short_buf,
        "temperance_strong_trend_waive": bool(
            adx >= float(VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE)
            and bias in {"BULL", "BEAR"}
        ),
        "temperance_course_correct_friction": reason == "course_correct",
        "temperance_long_enter": float(VIRTUE_SCORE_LONG_ENTER) + long_buf + vel,
        "temperance_short_enter": float(VIRTUE_SCORE_SHORT_ENTER) - short_buf - vel,
        "velocity_adx_penalty": round(vel, 2),
        "pipeline_long_blend_required": pipe_long,
        "pipeline_short_blend_required": pipe_short,
        "pipeline_bull_short_penalty": bias == "BULL",
    }


def _multi_tp_cooldown_remaining_s(session: Any) -> int:
    """Mirror Layer-1 streak lock for system_state.json poll (no import of main)."""
    streak = int(getattr(session, "consecutive_tp_streak", 0) or 0)
    if streak < int(VIRTUE_MULTI_TP_COOLDOWN_STREAK):
        return 0
    last_tp = float(getattr(session, "last_tp_timestamp", 0.0) or 0.0)
    if last_tp <= 0:
        return 0
    elapsed = float(time.time()) - last_tp
    remaining = float(VIRTUE_MULTI_TP_COOLDOWN_S) - elapsed
    return max(0, int(remaining))


def _multi_tp_cooldown_active(session: Any) -> bool:
    return _multi_tp_cooldown_remaining_s(session) > 0


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


def _data_dir() -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "data"


def paper_book_path() -> Any:
    return _data_dir() / "paper_book.json"


def _atomic_write_json(path: Any, payload: dict[str, Any]) -> None:
    from pathlib import Path
    import json
    import os

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def load_paper_book(default: float | None = None) -> dict[str, Any]:
    """
    Durable compounded paper book (Justice).

    Lives in data/paper_book.json — never wiped by day-roll, dashboard bootstrap,
    or a bad system_state.json rewrite. This is why Friday gains must survive Saturday.
    """
    from pathlib import Path
    import json
    import logging

    log = logging.getLogger("virtue.ui_state")
    base = float(default if default is not None else STARTING_NAV)
    empty = {
        "book_equity": base,
        "peak_equity": base,
        "updated_at": "",
        "source": "default",
        "restored": False,
    }
    path = paper_book_path()
    try:
        if path.is_file():
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            book = float(raw.get("book_equity") or 0.0)
            if book > 0:
                peak = float(raw.get("peak_equity") or book)
                return {
                    "book_equity": round(book, 2),
                    "peak_equity": round(max(peak, book), 2),
                    "updated_at": str(raw.get("updated_at") or ""),
                    "source": "paper_book.json",
                    "restored": True,
                }
    except Exception as exc:
        log.exception("load_paper_book_failed err=%s", exc)
    return empty


def save_paper_book(
    book_equity: float,
    *,
    peak_equity: float | None = None,
    source: str = "virtue",
) -> None:
    """Persist compounded book equity atomically (Temperance — protect principal record)."""
    import logging

    log = logging.getLogger("virtue.ui_state")
    book = round(float(book_equity), 2)
    if book <= 0:
        return
    try:
        prev = load_paper_book(book)
        peak = float(peak_equity) if peak_equity is not None else float(prev.get("peak_equity") or book)
        peak = round(max(peak, book, float(prev.get("book_equity") or 0.0)), 2)
        payload = {
            "book_equity": book,
            "peak_equity": peak,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source or "virtue"),
        }
        _atomic_write_json(paper_book_path(), payload)
    except Exception as exc:
        log.exception("save_paper_book_failed err=%s", exc)


def ensure_boot_system_state(path: Any = None) -> None:
    from pathlib import Path
    import json

    state_path = Path(path) if path else _data_dir() / "system_state.json"
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if float(raw.get("account_nav") or 0) > 0:
                return
        except Exception:
            pass
    # Prefer durable paper book over handshake default (never invent a wipe to $15k).
    equity = float(load_paper_book(HANDSHAKE_EQUITY_BASE).get("book_equity") or HANDSHAKE_EQUITY_BASE)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "MES",
        "account_nav": equity,
        "book_equity": equity,
        "session": {"realized_pnl_today": 0.0, "cycle_count": 0, "trades_today": 0, "halted": False},
        "open_positions": [],
        "dashboard": {
            "account_nav": equity,
            "starting_nav": HANDSHAKE_EQUITY_BASE,
            "daily_pnl": 0.0,
            "boot_status": "starting",
            "mode": "PAPER",
        },
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[CELINE] Boot state seeded — book equity ${equity:,.2f}", flush=True)


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
    vol_conviction: float = 50.0,
    market_lift: float = 50.0,
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
        virtue_session_mode,
    )
    from scripts.run_daily_session import virtue_session_label

    session_mode = virtue_session_mode()
    session_label = virtue_session_label()

    broker = session.broker
    risk = session.risk
    starting = float(getattr(risk, "starting_nav", STARTING_NAV) or STARTING_NAV)
    broker_equity = float(getattr(broker, "equity", 0.0) or 0.0)
    # Paper mode: never let a transient broker/default wipe undercut the durable ledger.
    if forward_test_force_paper():
        try:
            ledger_eq = float(load_paper_book(starting).get("book_equity") or 0.0)
            if ledger_eq > broker_equity + 0.009:
                broker_equity = ledger_eq
        except Exception:
            pass
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
        # Justice: do not invent entry=last_price (that forces 0 unrealized).
        positions = [
            {
                "direction": net_dir,
                "contracts": int(net_size),
                "entry_price": 0.0,
                "entry_unknown": True,
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
    open_risk = (
        float(VIRTUE_POSITION_STOP_DOLLARS) * abs(int(net_size))
        if abs(net_size) > 0
        else 0.0
    )
    max_conc = concurrent_risk_cap(risk_nav)
    hard_stop = max_daily_loss_cap(risk_nav)
    mode = "PAPER" if forward_test_force_paper() else "LIVE"
    now = datetime.now(timezone.utc).isoformat()

    try:
        from engine.dual_sleeve import build_dual_sleeve_state

        dual_sleeve = build_dual_sleeve_state(session, account_nav=nav)
    except Exception:
        dual_sleeve = {
            "account_nav": nav,
            "max_account_contract_ceiling": 2,
            "regime_engine": {
                "macro_structural_regime": str(
                    getattr(session, "macro_structural_regime", "STRUCTURAL_NEUTRAL")
                    or "STRUCTURAL_NEUTRAL"
                ),
                "micro_tactical_regime": str(regime or "CHOP_NO_TRADE"),
            },
            "core_anchor_sleeve": {"active": False, "side": "FLAT", "size": 0},
            "tactical_satellite_sleeve": {
                "engine_exposure": "FLAT",
                "size": 0,
            },
        }

    return {
        "version": 1,
        "updated_at": now,
        "symbol": EXECUTION_SYMBOL,
        "strategy": "virtue_wisdom",
        "data_source": data_source,
        "session_mode": session_mode,
        "session_label": session_label,
        "last_price": last_price,
        "unrealized_pnl": unrealized,
        "account_nav": nav,
        "dual_sleeve": dual_sleeve,
        "book_equity": round(broker_equity, 2) if broker_equity > 0 else round(starting, 2),
        "consecutive_tp_streak": int(getattr(session, "consecutive_tp_streak", 0) or 0),
        "last_tp_timestamp": float(getattr(session, "last_tp_timestamp", 0.0) or 0.0),
        "macro_bias": str(getattr(session, "macro_bias", "NEUTRAL") or "NEUTRAL"),
        **_layer1_cooldown_fields(session),
        "last_trade_outcome": {
            "last_result": str(getattr(session, "last_result", "") or "FLAT"),
            "last_reason": str(getattr(session, "last_reason", "") or "none"),
            "consecutive_wins": int(getattr(session, "consecutive_wins", 0) or 0),
            "consecutive_losses": int(getattr(session, "consecutive_losses", 0) or 0),
            "last_trade_pnl": round(float(getattr(session, "last_trade_pnl", 0.0) or 0.0), 2),
        },
        "entry_pipeline": {
            **_entry_pipeline_layer1_fields(session),
            "layer2_macro_bias": str(getattr(session, "macro_bias", "NEUTRAL") or "NEUTRAL"),
            "layer3_course_correct": "check_every_hold_cycle",
            "allow_new_entries": bool(getattr(session, "allow_new_entries", False)),
            "entry_windows_et": "09:45-11:30&13:45-15:55+extreme",
            "entry_structure_ok": bool(getattr(session, "last_structure_ok", False)),
            "entry_structure_reason": str(
                getattr(session, "last_structure_reason", "") or ""
            ),
            "sleeve_reconcile_ok": bool(getattr(session, "sleeve_reconcile_ok", True)),
            "sleeve_reconcile_detail": str(
                getattr(session, "sleeve_reconcile_detail", "") or ""
            ),
            "temperance_loss_friction": int(getattr(session, "consecutive_losses", 0) or 0) >= 1,
            "virtue_pnl_lock_active": bool(
                getattr(session, "virtue_pnl_lock_active", False)
            ),
            **_time_decay_pipeline_fields(session),
            **_temperance_pipeline_fields(session),
        },
        "session": {
            "realized_pnl_today": daily_pnl,
            "peak_realized_pnl_today": round(
                float(getattr(session, "peak_realized_pnl_today", 0.0) or 0.0), 2
            ),
            "virtue_pnl_lock_active": bool(
                getattr(session, "virtue_pnl_lock_active", False)
            ),
            "circuit_breaker_tripped": bool(
                getattr(session, "circuit_breaker_tripped", False)
            ),
            "virtue_pnl_lock_floor": (
                round(float(VIRTUE_PNL_LOCK_HARD_FLOOR), 2)
                if float(getattr(session, "peak_realized_pnl_today", 0.0) or 0.0)
                >= float(VIRTUE_PNL_LOCK_ARM_PEAK)
                else None
            ),
            "core_reentry_blocked_until": float(
                getattr(session, "core_reentry_blocked_until", 0.0) or 0.0
            ),
            "core_reentry_blocked_side": str(
                getattr(session, "core_reentry_blocked_side", "FLAT") or "FLAT"
            ),
            "session_date_et": str(getattr(session, "session_date_et", "") or ""),
            "open_risk_notional": open_risk,
            "trades_today": int(getattr(session, "trades_today", 0) or 0),
            "halted": bool(getattr(session, "halted", False)),
            "cycle_count": int(getattr(session, "cycle", 0) or 0),
            "last_risk_verdict": last_risk_verdict,
            "last_risk_reason": last_risk_reason,
            "last_action": str(getattr(session, "last_action", "") or action),
            "consecutive_tp_streak": int(getattr(session, "consecutive_tp_streak", 0) or 0),
            "last_tp_timestamp": float(getattr(session, "last_tp_timestamp", 0.0) or 0.0),
            "require_tp_pullback": bool(getattr(session, "require_tp_pullback", False)),
            "macro_bias": str(getattr(session, "macro_bias", "NEUTRAL") or "NEUTRAL"),
            "long_tps_today": int(getattr(session, "long_tps_today", 0) or 0),
            "short_tps_today": int(getattr(session, "short_tps_today", 0) or 0),
            "entry_cycle_marker": getattr(session, "entry_cycle_marker", None),
            "last_result": str(getattr(session, "last_result", "") or "FLAT"),
            "last_reason": str(getattr(session, "last_reason", "") or "none"),
            "consecutive_wins": int(getattr(session, "consecutive_wins", 0) or 0),
            "consecutive_losses": int(getattr(session, "consecutive_losses", 0) or 0),
            "last_trade_pnl": round(float(getattr(session, "last_trade_pnl", 0.0) or 0.0), 2),
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
            "vol_conviction": round(float(vol_conviction), 2),
            "market_lift": round(float(market_lift), 2),
            "exposure": net_dir,
            "contracts": int(net_size),
        },
        "dashboard": {
            "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "symbol": EXECUTION_SYMBOL,
            "mode": mode,
            "data_source": data_source,
            "session_mode": session_mode,
            "session_label": session_label,
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
            "vol_conviction": round(float(vol_conviction), 2),
            "market_lift": round(float(market_lift), 2),
        },
    }


def persist_virtue_system_state(session: Any, **kwargs: Any) -> None:
    """Write data/system_state.json for Streamlit / cloud dashboard."""
    from pathlib import Path
    import json
    import logging

    log = logging.getLogger("virtue.ui_state")
    state_path = _data_dir() / "system_state.json"
    try:
        payload = build_virtue_system_state(session, **kwargs)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Mirror compounded equity into the durable paper ledger ONLY in paper mode.
        # Live cash equity must not rewrite paper_book.json (Justice — no false ledger).
        from engine.config import forward_test_force_paper

        if forward_test_force_paper():
            broker = getattr(session, "broker", None)
            book = float(getattr(broker, "equity", 0.0) or 0.0)
            if book > 0:
                peak = float(getattr(getattr(session, "risk", None), "peak_nav", book) or book)
                save_paper_book(book, peak_equity=peak, source="system_state_persist")
    except Exception as exc:
        log.exception("persist_virtue_system_state_failed err=%s", exc)


def load_persisted_book_equity(default: float | None = None) -> float:
    """
    Load compounded paper book equity.

    Order (Justice — never invent a wipe to STARTING_NAV when a higher truth exists):
      0) PAPER_BOOK_EQUITY env (manual recovery of a known good book)
      1) data/paper_book.json (durable ledger)
      2) data/system_state.json book_equity / account_nav
      3) default STARTING_NAV

    Exactly-default system_state values are NOT sealed into the ledger (avoids
    locking in a wiped $15k dashboard after a bad rewrite).
    """
    import json
    import logging
    import os
    from pathlib import Path

    log = logging.getLogger("virtue.ui_state")
    base = float(default if default is not None else STARTING_NAV)

    forced = os.getenv("PAPER_BOOK_EQUITY", "").strip()
    if forced:
        try:
            val = round(float(forced), 2)
            if val > 0:
                save_paper_book(val, source="env_PAPER_BOOK_EQUITY")
                return val
        except ValueError:
            log.error("PAPER_BOOK_EQUITY invalid value=%r", forced)

    ledger = load_paper_book(base)
    if ledger.get("restored") and float(ledger.get("book_equity") or 0.0) > 0:
        return round(float(ledger["book_equity"]), 2)

    state_path = _data_dir() / "system_state.json"
    try:
        if state_path.is_file():
            raw = json.loads(Path(state_path).read_text(encoding="utf-8"))
            book = float(raw.get("book_equity") or 0.0)
            if book > 0:
                # Migrate only non-default books — a wiped $15k state must not seal the ledger.
                if abs(book - base) > 0.009:
                    save_paper_book(book, source="migrate_system_state")
                return round(book, 2)
            nav = float(raw.get("account_nav") or 0.0)
            unreal = float(raw.get("unrealized_pnl") or 0.0)
            if nav > 0:
                migrated = round(nav - unreal, 2)
                if migrated > 0 and abs(migrated - base) > 0.009:
                    save_paper_book(migrated, source="migrate_system_state_nav")
                    return migrated
                return round(max(base, migrated), 2) if migrated > 0 else base
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
        "peak_realized_pnl_today": 0.0,
        "virtue_pnl_lock_active": False,
        "circuit_breaker_tripped": False,
        "core_reentry_blocked_until": 0.0,
        "core_reentry_blocked_side": "FLAT",
        "trades_today": 0,
        "consecutive_tp_streak": 0,
        "last_tp_timestamp": 0.0,
        "require_tp_pullback": False,
        "macro_bias": "NEUTRAL",
        "long_tps_today": 0,
        "short_tps_today": 0,
        "last_result": "FLAT",
        "last_reason": "none",
        "consecutive_wins": 0,
        "consecutive_losses": 0,
        "last_trade_pnl": 0.0,
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
        peak_pnl = float(
            sess.get("peak_realized_pnl_today")
            if sess.get("peak_realized_pnl_today") is not None
            else pnl
        )
        virtue_lock = bool(sess.get("virtue_pnl_lock_active") or False)
        circuit_breaker = bool(
            sess.get("circuit_breaker_tripped") or virtue_lock or False
        )
        core_blocked_until = float(sess.get("core_reentry_blocked_until") or 0.0)
        core_blocked_side = str(sess.get("core_reentry_blocked_side") or "FLAT")
        trades = int(sess.get("trades_today") or 0)
        tp_streak = int(
            sess.get("consecutive_tp_streak")
            if sess.get("consecutive_tp_streak") is not None
            else raw.get("consecutive_tp_streak")
            or 0
        )
        last_tp = float(
            sess.get("last_tp_timestamp")
            if sess.get("last_tp_timestamp") is not None
            else raw.get("last_tp_timestamp")
            or 0.0
        )
        require_pullback = bool(sess.get("require_tp_pullback") or False)
        macro_bias = str(
            sess.get("macro_bias")
            or raw.get("macro_bias")
            or "NEUTRAL"
        ).upper()
        if macro_bias not in {"BULL", "BEAR", "NEUTRAL"}:
            macro_bias = "NEUTRAL"
        long_tps = int(sess.get("long_tps_today") or 0)
        short_tps = int(sess.get("short_tps_today") or 0)
        outcome = raw.get("last_trade_outcome") or {}
        last_result = str(
            outcome.get("last_result") or sess.get("last_result") or "FLAT"
        )
        last_reason = str(
            outcome.get("last_reason") or sess.get("last_reason") or "none"
        )
        consecutive_wins = int(
            outcome.get("consecutive_wins")
            if outcome.get("consecutive_wins") is not None
            else sess.get("consecutive_wins")
            or 0
        )
        consecutive_losses = int(
            outcome.get("consecutive_losses")
            if outcome.get("consecutive_losses") is not None
            else sess.get("consecutive_losses")
            or 0
        )
        last_trade_pnl = float(
            outcome.get("last_trade_pnl")
            if outcome.get("last_trade_pnl") is not None
            else sess.get("last_trade_pnl")
            or 0.0
        )
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
            "peak_realized_pnl_today": round(max(peak_pnl, pnl), 2),
            "virtue_pnl_lock_active": virtue_lock,
            "circuit_breaker_tripped": circuit_breaker,
            "core_reentry_blocked_until": max(0.0, core_blocked_until),
            "core_reentry_blocked_side": core_blocked_side,
            "trades_today": max(0, trades),
            "consecutive_tp_streak": max(0, tp_streak),
            "last_tp_timestamp": max(0.0, last_tp),
            "require_tp_pullback": require_pullback,
            "macro_bias": macro_bias,
            "long_tps_today": max(0, long_tps),
            "short_tps_today": max(0, short_tps),
            "last_result": last_result,
            "last_reason": last_reason,
            "consecutive_wins": max(0, consecutive_wins),
            "consecutive_losses": max(0, consecutive_losses),
            "last_trade_pnl": round(last_trade_pnl, 2),
            "restored": True,
        }
    except Exception as exc:
        log.exception("load_persisted_day_bucket_failed err=%s", exc)
        return empty


def restore_dual_sleeve_books(session: Any, *, state_path: Any | None = None) -> bool:
    """
    Restore core/tactical sleeve tags from system_state.json (Justice).

    Prevents restart from attributing a structural core runner to the tactical book
    (which would wrongly feed Temperance).
    """
    from pathlib import Path
    import json
    import logging

    log = logging.getLogger("virtue.ui_state")
    path = (
        Path(state_path)
        if state_path is not None
        else Path(__file__).resolve().parent.parent / "data" / "system_state.json"
    )
    try:
        if not path.is_file():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        ds = raw.get("dual_sleeve") or {}
        if not isinstance(ds, dict) or not ds:
            return False
        core = ds.get("core_anchor_sleeve") or {}
        tac = ds.get("tactical_satellite_sleeve") or {}
        regime = (ds.get("regime_engine") or {}).get("macro_structural_regime")
        if regime:
            session.macro_structural_regime = str(regime)

        if bool(core.get("active")) and str(core.get("side") or "").upper() in {
            "LONG",
            "SHORT",
        }:
            session.core_active = True
            session.core_side = str(core.get("side") or "FLAT").upper()
            session.core_size = max(1, int(core.get("size") or 1))
            session.core_entry_price = float(core.get("entry_price") or 0.0)
            session.core_realized_pnl_today = float(
                core.get("realized_pnl_today") or 0.0
            )
            session.core_reentry_blocked_until = float(
                core.get("reentry_blocked_until") or 0.0
            )
            session.core_reentry_blocked_side = str(
                core.get("reentry_blocked_side") or "FLAT"
            )
        else:
            session.core_active = False
            session.core_side = "FLAT"
            session.core_size = 0
            session.core_entry_price = 0.0
            # Keep invalidation cooldown even when core is flat.
            if core.get("reentry_blocked_until") is not None:
                session.core_reentry_blocked_until = float(
                    core.get("reentry_blocked_until") or 0.0
                )
                session.core_reentry_blocked_side = str(
                    core.get("reentry_blocked_side") or "FLAT"
                )

        eng = str(tac.get("engine_exposure") or "FLAT").upper()
        tac_sz = int(tac.get("size") or 0)
        if bool(tac.get("active", tac_sz > 0)) and eng in {"LONG", "SHORT"} and tac_sz > 0:
            session.tactical_active = True
            session.tactical_side = eng
            session.tactical_size = max(1, tac_sz)
            session.tactical_entry_price = float(tac.get("entry_price") or 0.0)
            marker = tac.get("entry_cycle_marker")
            session.entry_cycle_marker = (
                int(marker) if marker is not None else session.entry_cycle_marker
            )
            session.tactical_realized_pnl_today = float(
                tac.get("realized_pnl_today") or 0.0
            )
        else:
            session.tactical_active = False
            session.tactical_side = "FLAT"
            session.tactical_size = 0
            session.tactical_entry_price = 0.0

        if tac.get("pipeline_resume_cycle") is not None:
            session.pipeline_resume_cycle = int(tac.get("pipeline_resume_cycle") or 0)
        return True
    except Exception as exc:
        log.exception("restore_dual_sleeve_books_failed err=%s", exc)
        return False


__all__ = [
    "build_system_state",
    "build_virtue_system_state",
    "ensure_boot_system_state",
    "load_paper_book",
    "load_persisted_book_equity",
    "load_persisted_day_bucket",
    "load_persisted_open_positions",
    "paper_book_path",
    "persist_virtue_system_state",
    "restore_dual_sleeve_books",
    "save_paper_book",
]
