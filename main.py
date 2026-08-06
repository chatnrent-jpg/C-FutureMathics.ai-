"""
FutureMathics virtue main loop — native Wisdom brain.

Execution: Webull futures (TradeClient)
Market data: Databento CME MES L1 (primary) → Alpaca SPY→MES proxy fallback
No Interactive Brokers / VolumeWatch grade feed / Webull US_FUTURES quotes required.

Production hardening:
  1. Seed WisdomStrategy from Databento MES bars (preferred) or Alpaca proxy
  2. Session gate: full CME hours when Databento primary; cash RTH when Alpaca-only
  3. Manus CapitalProtectionMatrix (daily halt / reduce size / concurrent risk)
  4. Position exclusivity, 5s timeouts, reconcile, infinite reconnect

Usage:
  cd C:\\FutureMathics.ai
  $env:PYTHONPATH = \"C:\\FutureMathics.ai\"
  python main.py --cycles 5
  python main.py --once
  python main.py --ignore-hours
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from broker import NETWORK_TIMEOUT_S, Order, SizeResult, VirtueBroker
from engine.dual_sleeve import (
    classify_structural_regime,
    core_should_open,
    core_size_default,
    evaluate_core_macro_safety,
    reconcile_sleeves_to_broker,
    structural_confirm_cycles,
    tactical_entry_allowed,
)
from engine.macromathics_core import (
    cooldown_cycles_for_reason,
    phase1_profit_guard_triggered,
    phase2_time_decay_triggered,
    phase3_layer1_clear,
    phase3_velocity_gates,
)
from engine.sleeve_order_router import (
    absolute_contract_footprint,
    calculate_net_account_exposure,
    close_all_sleeves_sequential,
    execute_core_action,
    execute_tactical_action,
)
from engine.entry_structure import (
    commit_entry_structure_memory,
    evaluate_entry_structure_gates,
)
from engine.observability import (
    notify_core_macro_invalidation,
    notify_course_correct,
    notify_position_opened,
    notify_profit_guard,
    notify_time_decay,
    notify_trade_closed,
    notify_velocity_gate_block,
)
from engine.config import (
    DEFAULT_STOP_TICKS,
    EXECUTION_SYMBOL,
    FORWARD_TEST_TIMEZONE,
    GRADE_DAILY_PROFIT_LOCK,
    STARTING_NAV,
    TICK_SIZE,
    TICK_VALUE,
    VIRTUE_PNL_LOCK_ARM_PEAK,
    VIRTUE_PNL_LOCK_FLOOR_FRAC,
    VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT,
    VIRTUE_FORCE_EVENT_MAX_S,
    VIRTUE_HEARTBEAT_EVERY_N_CYCLES,
    VIRTUE_MIN_PRICE_MOVE_TICKS,
    VIRTUE_POSITION_STOP_DOLLARS,
    VIRTUE_POSITION_TP_DOLLARS,
    VIRTUE_BASE_TP_COOLDOWN_CYCLES,
    VIRTUE_HARD_STOP_COOLDOWN_CYCLES,
    VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES,
    VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES,
    VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES,
    VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES,
    VIRTUE_POST_TP_PULLBACK_BLEND_LONG,
    VIRTUE_POST_TP_PULLBACK_BLEND_SHORT,
    VIRTUE_POST_TP_STREAK_COOLDOWN_EXTRA,
    VIRTUE_POST_TP_STREAK_PULLBACK_AFTER,
    VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES,
    VIRTUE_MULTI_TP_COOLDOWN_S,
    VIRTUE_MULTI_TP_COOLDOWN_STREAK,
    VIRTUE_POST_LOSS_EXTRA_STREAK,
    VIRTUE_TEMPERANCE_BASE_CONTRACTS,
    VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER,
    VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER,
    VIRTUE_TEMPERANCE_LOSS_STREAK_MIN,
    VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE,
    VIRTUE_PIPELINE_BULL_SHORT_PENALTY,
    VIRTUE_PIPELINE_LONG_BLEND_BASE,
    VIRTUE_PIPELINE_SHORT_BLEND_BASE,
    VIRTUE_VELOCITY_ADX_FLOOR,
    VIRTUE_VELOCITY_PENALTY_PER_ADX,
    VIRTUE_TACTICAL_ADX_MIN,
    VIRTUE_MAX_TACTICAL_TRADES_PER_DAY,
    VIRTUE_POST_TIME_DECAY_COOLDOWN_CYCLES,
    VIRTUE_TIME_DECAY_COOLDOWN_CYCLES,
    VIRTUE_TIME_DECAY_MAX_CYCLES,
    VIRTUE_TIME_DECAY_MIN_OPEN_PNL,
    VIRTUE_ADX_ENTER_MIN,
    VIRTUE_ADX_SHORT_ENTER_MIN,
    VIRTUE_BULL_DAY_SHORT_ADX_MIN,
    VIRTUE_BULL_DAY_SHORT_BLEND_MAX,
    VIRTUE_COURSE_CORRECT_LONG_BLEND,
    VIRTUE_COURSE_CORRECT_SHORT_BLEND,
    VIRTUE_REQUIRED_STREAK,
    VIRTUE_RTH_FLATTEN_MAX_ATTEMPTS,
    VIRTUE_RTH_FLATTEN_RETRY_S,
    VIRTUE_SCORE_LONG_CHASE_MAX,
    VIRTUE_SCORE_LONG_ENTER,
    VIRTUE_SCORE_LONG_EXIT,
    VIRTUE_SCORE_PRICE_PCT,
    VIRTUE_SCORE_SHORT_CHASE_MIN,
    VIRTUE_SCORE_SHORT_ENTER,
    VIRTUE_SCORE_SHORT_EXIT,
    VIRTUE_STATE_PERSIST_INTERVAL_S,
    VIRTUE_TICK_POLL_S,
    fixed_fractional_risk_pct,
    forward_test_force_paper,
    primary_data_source,
    virtue_session_mode,
)

_ET = ZoneInfo(FORWARD_TEST_TIMEZONE)
from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict
from manus.heartbeat import BrokerHeartbeatAgent, HeartbeatState
from engine.ui_state_bridge import (
    load_paper_book,
    load_persisted_book_equity,
    load_persisted_day_bucket,
    load_persisted_open_positions,
    persist_virtue_system_state,
    restore_dual_sleeve_books,
    save_paper_book,
)
from scripts.run_daily_session import (
    allow_new_entries,
    virtue_entries_allowed,
    virtue_session_label,
    virtue_session_open,
)
from strategy import Bar, SignalAction, WisdomStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("virtue.main")


@dataclass
class VirtueSession:
    cycle: int = 0
    halted: bool = False
    last_action: str = "FLAT"
    realized_pnl_today: float = 0.0
    peak_realized_pnl_today: float = 0.0  # high-water mark for trailing profit lock
    virtue_pnl_lock_active: bool = False  # day shut down after trailing floor breach
    trades_today: int = 0
    session_date_et: str = ""  # YYYY-MM-DD America/New_York — Temperance day bucket
    last_risk_verdict: str = ""
    last_risk_reason: str = ""
    last_regime: str = ""
    last_signal_reason: str = ""
    last_adx: float = 0.0
    last_atr_pct: float = 0.0
    last_vwap_score: float = 50.0
    last_twap_score: float = 50.0
    last_blended_score: float = 50.0
    # Prior-cycle structure memory (ATR expand / ADX rise / VWAP-TWAP spread widen)
    prev_atr: float = 0.0
    prev_adx: float = 0.0
    prev_anchor_spread_pct: float = 0.0
    last_structure_ok: bool = False
    last_structure_reason: str = "entry_structure:none"
    allow_new_entries: bool = False
    last_data_source: str = "alpaca_spy_mes_proxy"
    anchors_aligned: bool = False
    entry_cooldown_cycles: int = 0  # skip new entries after anchor rebase (non-trade)
    pipeline_resume_cycle: int = 0  # absolute engine cycle when entries may resume
    layer1_streak_clear: bool = True  # entry_pipeline Layer-1 readiness flag
    entry_cycle_marker: int | None = None  # engine cycle when current exposure opened
    consecutive_tp_streak: int = 0  # Temperance: cool re-entry after multi-TP runs
    last_tp_timestamp: float = 0.0  # unix time of last TP (system_state + wall-clock lock)
    require_tp_pullback: bool = False  # after N TPs, wait for blend cool-off
    macro_bias: str = "NEUTRAL"  # BULL | BEAR | NEUTRAL — day regime for asymmetric shorts
    long_tps_today: int = 0
    short_tps_today: int = 0
    # Outcome block — TACTICAL sleeve only (Temperance; core never writes here)
    last_result: str = "FLAT"  # WIN | LOSS | FLAT
    last_reason: str = "none"  # take_profit | stop | course_correct | ...
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    last_trade_pnl: float = 0.0
    long_streak: int = 0
    short_streak: int = 0
    # Dual-sleeve books (Unified Rules of Engagement)
    macro_structural_regime: str = "STRUCTURAL_NEUTRAL"
    structural_bull_streak: int = 0
    structural_bear_streak: int = 0
    core_active: bool = False
    core_side: str = "FLAT"
    core_size: int = 0
    core_entry_price: float = 0.0
    core_entry_cycle: int | None = None
    last_price: float = 0.0  # last accepted quote (core adverse failsafe)
    core_realized_pnl_today: float = 0.0
    core_realized_pnl_this_cycle: float = 0.0
    sleeve_reconcile_ok: bool = True
    sleeve_reconcile_detail: str = "sleeve_reconcile:pending"
    tactical_active: bool = False
    tactical_side: str = "FLAT"
    tactical_size: int = 0
    tactical_entry_price: float = 0.0
    tactical_realized_pnl_today: float = 0.0
    is_running: bool = True  # False stops background state writer (run.py pattern)
    cycles_since_heartbeat: int = 0
    last_heartbeat_ok: bool = True
    last_heartbeat_state: str = "GREEN"
    strategy: WisdomStrategy = field(
        default_factory=lambda: WisdomStrategy(
            long_enter=float(VIRTUE_SCORE_LONG_ENTER),
            short_enter=float(VIRTUE_SCORE_SHORT_ENTER),
            long_exit=float(VIRTUE_SCORE_LONG_EXIT),
            short_exit=float(VIRTUE_SCORE_SHORT_EXIT),
            score_price_pct=float(VIRTUE_SCORE_PRICE_PCT),
            adx_trend_min=float(VIRTUE_ADX_ENTER_MIN),
            adx_short_min=float(VIRTUE_ADX_SHORT_ENTER_MIN),
        )
    )
    broker: VirtueBroker = field(default_factory=VirtueBroker)
    risk: CapitalProtectionMatrix = field(
        default_factory=lambda: CapitalProtectionMatrix(
            starting_nav=STARTING_NAV,
            account_nav=STARTING_NAV,
            peak_nav=STARTING_NAV,
        )
    )


def check_virtue_pnl_lock(
    session: VirtueSession | dict,
) -> bool:
    """
    Virtue Balance — trailing daily profit lock (Temperance).

    Delegates Phase 1 math to engine.macromathics_core (single contract).
    """
    if isinstance(session, dict):
        was_active = bool(session.get("virtue_pnl_lock_active"))
        hit, floor_lock = phase1_profit_guard_triggered(session)
        if hit and not was_active:
            peak = float(session.get("peak_realized_pnl_today", 0.0) or 0.0)
            notify_profit_guard(
                realized=float(session.get("realized_pnl_today", 0.0) or 0.0),
                floor=floor_lock,
                peak=peak,
            )
        return hit

    # Already latched for the day — stay locked (Justice: no silent unlock).
    if bool(session.virtue_pnl_lock_active):
        return True

    state = {
        "realized_pnl_today": float(session.realized_pnl_today or 0.0),
        "peak_realized_pnl_today": float(session.peak_realized_pnl_today or 0.0),
        "virtue_pnl_lock_active": bool(session.virtue_pnl_lock_active),
    }
    hit, floor_lock = phase1_profit_guard_triggered(state)
    session.peak_realized_pnl_today = float(
        state.get("peak_realized_pnl_today", session.peak_realized_pnl_today) or 0.0
    )
    if hit:
        session.virtue_pnl_lock_active = True
        session.last_regime = "CHOP_NO_TRADE"
        session.last_signal_reason = (
            f"virtue_pnl_lock peak={session.peak_realized_pnl_today:.2f} "
            f"floor={floor_lock:.2f} realized={session.realized_pnl_today:.2f}"
        )
        logger.error(
            "VIRTUE_PNL_LOCK triggered peak=%.2f floor=%.2f realized=%.2f — "
            "day shut down (Temperance)",
            session.peak_realized_pnl_today,
            floor_lock,
            session.realized_pnl_today,
        )
        notify_profit_guard(
            realized=float(session.realized_pnl_today),
            floor=floor_lock,
            peak=float(session.peak_realized_pnl_today),
        )
        return True
    return False


def check_time_decay_exit(
    session: VirtueSession,
    current_engine_cycle: int,
    *,
    open_pnl: float,
    exposure: str | None = None,
) -> tuple[bool, str]:
    """
    Failsafe: flatten stagnant holds that never produce momentum toward TP.

    Phase 2 contract via macromathics_core; broker net size still gates FLAT.
    """
    net_dir, net_size = session.broker.net_exposure()
    exp = (exposure or net_dir or "FLAT").upper()
    if exp == "FLAT" or net_size <= 0:
        session.entry_cycle_marker = None
        return False, ""

    state = {
        "engine_exposure": exp,
        "entry_cycle_marker": session.entry_cycle_marker,
        "open_pnl": float(open_pnl),
    }
    hit = phase2_time_decay_triggered(state, int(current_engine_cycle))
    session.entry_cycle_marker = state.get("entry_cycle_marker")
    if not hit:
        return False, ""
    elapsed = int(current_engine_cycle) - int(session.entry_cycle_marker or current_engine_cycle)
    max_cycles = int(VIRTUE_TIME_DECAY_MAX_CYCLES)
    min_pnl = float(VIRTUE_TIME_DECAY_MIN_OPEN_PNL)
    reason = (
        f"time_decay elapsed={elapsed}>={max_cycles} "
        f"open_pnl={float(open_pnl):.2f}<={min_pnl:.2f}"
    )
    logger.warning(
        "CYCLE %s TIME_DECAY_TRIGGERED holding=%s elapsed=%s open_pnl=%.2f — %s",
        current_engine_cycle,
        exp,
        elapsed,
        float(open_pnl),
        reason,
    )
    notify_time_decay(elapsed=elapsed, open_pnl=float(open_pnl), exposure=exp)
    return True, reason


def check_course_correct(current_position: str, blend: float) -> tuple[bool, str]:
    """
    Layer 3 — Execution State (every loop iteration while holding tactical).

    Hysteresis exits (default 45/55) — mid-band chop must not scalp every dip,
    but a true thesis break still flattens before the dollar stop.
    """
    pos = (current_position or "").upper()
    b = float(blend)
    if pos == "SHORT" and b >= float(VIRTUE_COURSE_CORRECT_SHORT_BLEND):
        return (
            True,
            f"course_correct_short_vs_bull blend={b:.1f}>={float(VIRTUE_COURSE_CORRECT_SHORT_BLEND):.1f}",
        )
    if pos == "LONG" and b <= float(VIRTUE_COURSE_CORRECT_LONG_BLEND):
        return (
            True,
            f"course_correct_long_vs_bear blend={b:.1f}<={float(VIRTUE_COURSE_CORRECT_LONG_BLEND):.1f}",
        )
    return False, ""


def update_macro_bias(
    session: VirtueSession,
    *,
    regime: str = "",
    action: str = "",
    blend: float = 50.0,
    adx: float = 0.0,
    banked_side: str | None = None,
) -> str:
    """
    Sticky ET-day macro bias for asymmetric short friction.

    Latch BULL/BEAR from seed regime, strong structure, or banked TP side.
    Does not flip on single noisy cycles (Temperance).
    """
    bias = (session.macro_bias or "NEUTRAL").upper()
    if bias not in {"BULL", "BEAR", "NEUTRAL"}:
        bias = "NEUTRAL"
    reg = (regime or "").upper()
    act = (action or "").upper()
    side = (banked_side or "").upper()

    if side == "LONG":
        bias = "BULL"
    elif side == "SHORT":
        bias = "BEAR"
    elif (
        act == "LONG"
        and float(adx) >= float(VIRTUE_ADX_ENTER_MIN)
        and float(blend) >= 60.0
    ):
        bias = "BULL"
    elif (
        act == "SHORT"
        and float(adx) >= float(VIRTUE_ADX_SHORT_ENTER_MIN)
        and float(blend) <= 40.0
    ):
        bias = "BEAR"
    elif "BULL" in reg and bias == "NEUTRAL":
        bias = "BULL"
    elif "BEAR" in reg and bias == "NEUTRAL":
        bias = "BEAR"

    # Tape-only latch — do not override with TP day skew (Wisdom: market conditions).
    session.macro_bias = bias
    return bias


def evaluate_directional_gate(
    entry_signal: str,
    *,
    macro_bias: str,
    blend: float,
    adx: float,
) -> tuple[bool, str]:
    """
    Layer 2 — Asymmetric Filter (after a directional entry signal passes).

    Shorts on bull days require extreme confirmation. LONG path unchanged.
    Returns (allowed, reason).
    """
    side = (entry_signal or "").upper()
    bias = (macro_bias or "NEUTRAL").upper()
    if side == "SHORT" and bias == "BULL":
        thr = float(VIRTUE_BULL_DAY_SHORT_BLEND_MAX)
        min_adx = float(VIRTUE_BULL_DAY_SHORT_ADX_MIN)
        b = float(blend)
        a = float(adx)
        if b > thr or a < min_adx:
            return (
                False,
                (
                    f"bull_day_short_denied bias=BULL blend={b:.1f} need<={thr:.1f} "
                    f"adx={a:.1f} need>={min_adx:.1f}"
                ),
            )
    return True, ""


def calculate_temperance_parameters(
    system_state: dict | None = None,
    *,
    session: VirtueSession | None = None,
) -> tuple[int, float]:
    """
    Dynamically adjust size and entry criteria from recent performance.

    Returns (base_contracts, blend_buffer_modifier).
    MES irreducible lot stays 1 — friction is applied via wider enter bands.
    When both loss-streak and course_correct apply, use the stronger buffer (max).
    """
    outcome: dict = {}
    if isinstance(system_state, dict):
        outcome = system_state.get("last_trade_outcome") or {}
    if session is not None and not outcome:
        outcome = {
            "consecutive_losses": int(getattr(session, "consecutive_losses", 0) or 0),
            "last_reason": str(getattr(session, "last_reason", "") or ""),
        }

    consecutive_losses = int(outcome.get("consecutive_losses", 0) or 0)
    last_reason = str(outcome.get("last_reason", "") or "").strip().lower()

    base_contracts = max(1, int(VIRTUE_TEMPERANCE_BASE_CONTRACTS))
    blend_buffer_modifier = 0.0

    # Rule 1: losing streak → stronger trend required (cannot halve below 1 MES).
    if consecutive_losses >= int(VIRTUE_TEMPERANCE_LOSS_STREAK_MIN):
        blend_buffer_modifier = max(
            blend_buffer_modifier,
            float(VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER),
        )

    # Rule 2: post course_correct cooling — widen neutral band.
    if last_reason == "course_correct":
        blend_buffer_modifier = max(
            blend_buffer_modifier,
            float(VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER),
        )

    return base_contracts, float(blend_buffer_modifier)


def effective_temperance_blend_buffer(
    blend_buffer: float,
    *,
    side: str,
    adx: float,
    macro_bias: str,
) -> float:
    """
    Strong with-trend: drop blend widening; keep confirmation-streak Temperance.

    After a loss streak the raw +5 buffer raised live enter to 63 while the
    strategy still printed enter>=58 — streak stayed 0/4 through ADX 40+ rips.
    """
    try:
        adx_val = float(adx or 0.0)
    except (TypeError, ValueError):
        adx_val = 0.0
    if adx_val < float(VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE):
        return float(blend_buffer)
    bias = (macro_bias or "NEUTRAL").strip().upper()
    s = (side or "").strip().upper()
    if bias == "BULL" and s == "LONG":
        return 0.0
    if bias == "BEAR" and s == "SHORT":
        return 0.0
    return float(blend_buffer)


def calculate_dynamic_blend_thresholds(
    system_state: dict | None = None,
    *,
    adx: float | None = None,
) -> tuple[float, float]:
    """
    Velocity gate: widen entry thresholds when ADX is low to block slow drifts.

    Phase 3 contract via macromathics_core (single decision surface).
    """
    state = system_state if isinstance(system_state, dict) else {}
    long_threshold, short_threshold = phase3_velocity_gates(state, adx=adx)
    try:
        adx_val = float(
            adx
            if adx is not None
            else state.get("ADX", state.get("adx", 0.0)) or 0.0
        )
    except (TypeError, ValueError):
        adx_val = 0.0
    floor = float(VIRTUE_VELOCITY_ADX_FLOOR)
    if adx_val > 0 and adx_val < floor:
        volatility_penalty = float(long_threshold) - float(VIRTUE_PIPELINE_LONG_BLEND_BASE)
        logger.info(
            "VELOCITY_GATE low_adx=%.1f penalty=+%.1f → long>=%.1f short<=%.1f",
            adx_val,
            volatility_penalty,
            long_threshold,
            short_threshold,
        )
    return float(long_threshold), float(short_threshold)


def velocity_blend_penalty(
    system_state: dict | None = None,
    *,
    adx: float | None = None,
) -> float:
    """Points added to long / subtracted from short when ADX is weak."""
    long_thr, _ = calculate_dynamic_blend_thresholds(system_state, adx=adx)
    return max(0.0, float(long_thr) - float(VIRTUE_PIPELINE_LONG_BLEND_BASE))


def pipeline_system_state(
    session: VirtueSession,
    *,
    blend: float | None = None,
    adx: float | None = None,
) -> dict:
    """Build the system_state dict consumed by verify_pipeline_entry."""
    try:
        adx_val = float(
            adx
            if adx is not None
            else getattr(session, "last_adx", 14.0) or 14.0
        )
    except (TypeError, ValueError):
        adx_val = 14.0
    if adx_val <= 0:
        adx_val = 14.0
    return {
        "last_trade_outcome": {
            "last_result": str(getattr(session, "last_result", "FLAT") or "FLAT"),
            "last_reason": str(getattr(session, "last_reason", "none") or "none"),
            "consecutive_wins": int(getattr(session, "consecutive_wins", 0) or 0),
            "consecutive_losses": int(getattr(session, "consecutive_losses", 0) or 0),
            "last_trade_pnl": float(getattr(session, "last_trade_pnl", 0.0) or 0.0),
        },
        "vwap_twap_blend": float(
            blend
            if blend is not None
            else getattr(session, "last_blended_score", 50.0) or 50.0
        ),
        "macro_bias": str(getattr(session, "macro_bias", "NEUTRAL") or "NEUTRAL"),
        "ADX": adx_val,
        "adx": adx_val,
    }


def verify_pipeline_entry(
    entry_signal: str,
    system_state: dict | None,
) -> tuple[bool, str]:
    """
    Entry-pipeline validation with Temperance friction + ADX velocity gate.

    Blocks re-entry into chop after a bad stop / course_correct by widening
    the blend band. Low ADX widens further (slow-drift trap). Bull-day shorts
    get an extra structural penalty. Returns (allowed, reason).
    """
    state = system_state if isinstance(system_state, dict) else {}
    _, raw_blend_buffer = calculate_temperance_parameters(state)
    dyn_long, dyn_short = calculate_dynamic_blend_thresholds(state)
    velocity_penalty = max(
        0.0, float(dyn_long) - float(VIRTUE_PIPELINE_LONG_BLEND_BASE)
    )

    try:
        current_blend = float(state.get("vwap_twap_blend", 50.0) or 50.0)
    except (TypeError, ValueError):
        return False, "pipeline_block:invalid_blend"
    macro_bias = str(state.get("macro_bias", "NEUTRAL") or "NEUTRAL").upper()
    if macro_bias in {"CHOP", ""}:
        macro_bias = "NEUTRAL"
    side = (entry_signal or "").strip().upper()
    try:
        adx_for_buf = float(state.get("ADX", state.get("adx", 14.0)) or 14.0)
    except (TypeError, ValueError):
        adx_for_buf = 14.0
    blend_buffer_modifier = effective_temperance_blend_buffer(
        raw_blend_buffer,
        side=side,
        adx=adx_for_buf,
        macro_bias=macro_bias,
    )

    if side == "LONG":
        required_long_blend = float(dyn_long) + float(blend_buffer_modifier)
        if current_blend < required_long_blend:
            reason = (
                f"pipeline_block:long_blend={current_blend:.1f}"
                f"<required={required_long_blend:.1f}"
                f"(base={float(VIRTUE_PIPELINE_LONG_BLEND_BASE):.1f}"
                f"+vel={velocity_penalty:.1f}"
                f"+buf={float(blend_buffer_modifier):.1f})"
            )
            logger.info("PIPELINE_BLOCK %s", reason)
            notify_velocity_gate_block(
                side="LONG",
                blend=current_blend,
                target=required_long_blend,
                velocity=velocity_penalty,
            )
            return False, reason
        return True, (
            f"pipeline_ok:long blend={current_blend:.1f}"
            f">={required_long_blend:.1f}"
        )

    if side == "SHORT":
        required_short_blend = float(dyn_short) - float(blend_buffer_modifier)
        if macro_bias == "BULL":
            required_short_blend -= float(VIRTUE_PIPELINE_BULL_SHORT_PENALTY)
            logger.info(
                "PIPELINE asymmetric_short_gate BULL day — "
                "demand structural breakdown blend<=%.1f "
                "(vel=%.1f buf=%.1f penalty=%.1f)",
                required_short_blend,
                velocity_penalty,
                float(blend_buffer_modifier),
                float(VIRTUE_PIPELINE_BULL_SHORT_PENALTY),
            )
        if current_blend > required_short_blend:
            reason = (
                f"pipeline_block:short_blend={current_blend:.1f}"
                f">required={required_short_blend:.1f}"
                f"(base={float(VIRTUE_PIPELINE_SHORT_BLEND_BASE):.1f}"
                f"-vel={velocity_penalty:.1f}"
                f"-buf={float(blend_buffer_modifier):.1f}"
                f"{'-bull5' if macro_bias == 'BULL' else ''})"
            )
            logger.info("PIPELINE_BLOCK %s", reason)
            notify_velocity_gate_block(
                side="SHORT",
                blend=current_blend,
                target=required_short_blend,
                velocity=velocity_penalty,
            )
            return False, reason
        return True, (
            f"pipeline_ok:short blend={current_blend:.1f}"
            f"<={required_short_blend:.1f}"
        )

    return False, f"pipeline_block:unsupported_signal={side or 'EMPTY'}"


def verify_cooldown_validity(
    session: VirtueSession,
    *,
    now: float | None = None,
) -> tuple[bool, int]:
    """
    Layer 1 — Streak Validation (first entry gate).

    If consecutive_tp_streak >= 3, block new entries until
    VIRTUE_MULTI_TP_COOLDOWN_S seconds have elapsed since last_tp_timestamp.
    When false, the entry pipeline must bypass all further entry calculations.
    Returns (clear_to_trade, remaining_seconds).
    """
    streak = int(getattr(session, "consecutive_tp_streak", 0) or 0)
    if streak < int(VIRTUE_MULTI_TP_COOLDOWN_STREAK):
        return True, 0
    last_tp = float(getattr(session, "last_tp_timestamp", 0.0) or 0.0)
    if last_tp <= 0:
        return True, 0
    ts = float(time.time() if now is None else now)
    elapsed = ts - last_tp
    cooldown = float(VIRTUE_MULTI_TP_COOLDOWN_S)
    if elapsed < cooldown:
        remaining = max(0, int(cooldown - elapsed))
        return False, remaining
    # Lock expired — clear streak so the brake does not latch forever.
    session.consecutive_tp_streak = 0
    session.last_tp_timestamp = 0.0
    session.require_tp_pullback = False
    return True, 0


def target_ticks_from_atr(atr: float) -> int:
    """
    Legacy ATR take-profit helper (compat/tests).
    Virtue live exits use VIRTUE_POSITION_TP_DOLLARS via take_profit_dollars_hit.
    """
    from engine.config import VIRTUE_TP_ATR_MULT, VIRTUE_TP_MIN_TICKS

    atr_pts = max(0.0, float(atr or 0.0))
    atr_ticks = atr_pts / float(TICK_SIZE) if TICK_SIZE > 0 else 0.0
    raw = int(round(atr_ticks * float(VIRTUE_TP_ATR_MULT)))
    return max(int(VIRTUE_TP_MIN_TICKS), raw)


def position_tp_ticks(contracts: int) -> int:
    """Ticks of favorable move so position PnL ≈ VIRTUE_POSITION_TP_DOLLARS."""
    n = max(1, int(contracts))
    tv = float(TICK_VALUE)
    if tv <= 0:
        return 1
    return max(1, int(math.ceil(float(VIRTUE_POSITION_TP_DOLLARS) / (tv * n))))


def position_stop_ticks(contracts: int) -> int:
    """Ticks of adverse move so position PnL ≈ -VIRTUE_POSITION_STOP_DOLLARS."""
    n = max(1, int(contracts))
    tv = float(TICK_VALUE)
    if tv <= 0:
        return 1
    return max(1, int(math.ceil(float(VIRTUE_POSITION_STOP_DOLLARS) / (tv * n))))


def et_session_date(now: datetime | None = None) -> str:
    """Calendar trading day in ET for daily profit-lock / trade counters."""
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    else:
        dt = dt.astimezone(_ET)
    return dt.strftime("%Y-%m-%d")


def roll_daily_counters_if_needed(session: VirtueSession, *, now: datetime | None = None) -> bool:
    """
    Reset Temperance day counters when the ET calendar date changes.

    Justice: yesterday's realized PnL must never lock today's entries.
    Temperance: book equity / account NAV must NOT reset — only the day bucket clears.
    """
    today = et_session_date(now)
    if session.session_date_et == today:
        return False
    prior_date = session.session_date_et or "(boot)"
    prior_pnl = float(session.realized_pnl_today)
    prior_trades = int(session.trades_today)
    session.session_date_et = today
    session.realized_pnl_today = 0.0
    session.peak_realized_pnl_today = 0.0
    session.virtue_pnl_lock_active = False
    session.trades_today = 0
    session.consecutive_tp_streak = 0
    session.last_tp_timestamp = 0.0
    session.require_tp_pullback = False
    session.macro_bias = "NEUTRAL"
    session.long_tps_today = 0
    session.short_tps_today = 0
    session.last_result = "FLAT"
    session.last_reason = "none"
    session.consecutive_wins = 0
    session.consecutive_losses = 0
    session.last_trade_pnl = 0.0
    session.pipeline_resume_cycle = 0
    session.entry_cycle_marker = None
    session.core_realized_pnl_today = 0.0
    session.core_realized_pnl_this_cycle = 0.0
    session.tactical_realized_pnl_today = 0.0
    # Structural confirm streaks reset with the ET day (core position may still be open).
    session.structural_bull_streak = 0
    session.structural_bear_streak = 0
    # Fresh RTH session VWAP/TWAP — sticky day anchor starts empty.
    try:
        session.strategy.reset_session_anchors()
        session.anchors_aligned = False
    except Exception:
        logger.exception("session_anchor_reset_failed on day roll")
    logger.info(
        "SESSION_DAY_ROLL et_date=%s prior_date=%s prior_realized=%.2f prior_trades=%s "
        "book_equity=%.2f — day counters + session VWAP/TWAP reset; NAV compounds",
        today,
        prior_date,
        prior_pnl,
        prior_trades,
        float(session.broker.equity),
    )
    return True


def _local_paper_book() -> bool:
    """True when local paper fills own the book (Webull equity is not truth)."""
    from engine.webull_openapi import webull_is_sandbox

    return bool(forward_test_force_paper() and not webull_is_sandbox())


def _normalize_outcome_reason(reason: str) -> str:
    """Map broker/engine exit reasons to stable last_trade_outcome tags."""
    r = (reason or "").strip().lower()
    if r.startswith("take_profit") or "take_profit" in r:
        return "take_profit"
    if r.startswith("stop_") or r.startswith("stop$") or r == "stop":
        return "stop"
    if "course_correct" in r:
        return "course_correct"
    if r.startswith("time_decay") or "time_decay" in r:
        return "time_decay"
    if "profit_guard" in r or "virtue_pnl_lock" in r:
        return "virtue_pnl_lock"
    if "structured_exit" in r:
        return "structured_exit"
    if "thesis_invalid" in r or r.startswith("wisdom_flat"):
        return "thesis_invalid"
    if not r or r == "none":
        return "none"
    return (reason or "unknown")[:48]


def pipeline_lock_remaining(session: VirtueSession) -> tuple[bool, int]:
    """
    Absolute-cycle pipeline lock from update_outcome_state.

    Returns (locked, remaining_cycles). Entries allowed when cycle >= resume.
    """
    resume = int(getattr(session, "pipeline_resume_cycle", 0) or 0)
    cur = int(getattr(session, "cycle", 0) or 0)
    if resume <= 0 or cur >= resume:
        return False, 0
    return True, max(0, resume - cur)


def is_entry_pipeline_clear(
    session_state: VirtueSession | dict,
    current_engine_cycle: int | None = None,
) -> bool:
    """
    Layer 1 — evaluate execution readiness before entry-signal work.

    Uses absolute pipeline_resume_cycle (no wall clock). Returns True when
    current_engine_cycle >= resume_cycle (or resume unset).
    """
    if isinstance(session_state, dict):
        cur = int(
            current_engine_cycle
            if current_engine_cycle is not None
            else session_state.get("cycle", 0) or 0
        )
        clear = phase3_layer1_clear(session_state, cur)
        pipeline = session_state.setdefault("entry_pipeline", {})
        remaining = int(session_state.get("multi_tp_cooldown_remaining_s", 0) or 0)
        pipeline["pipeline_remaining_cycles"] = remaining
        return clear

    session = session_state
    cur = int(
        current_engine_cycle
        if current_engine_cycle is not None
        else getattr(session, "cycle", 0) or 0
    )
    resume = int(getattr(session, "pipeline_resume_cycle", 0) or 0)
    if resume > 0 and cur < resume:
        remaining = resume - cur
        session.layer1_streak_clear = False
        logger.info(
            "CYCLE %s LAYER1_pipeline_locked resume_cycle=%s remaining=%s "
            "last_reason=%s — bypass entry calculations",
            cur,
            resume,
            remaining,
            getattr(session, "last_reason", "none"),
        )
        return False

    session.layer1_streak_clear = True
    return True


def update_outcome_state(
    session: VirtueSession,
    trade_pnl: float,
    exit_reason: str,
    current_engine_cycle: int | None = None,
    *,
    count_trade: bool = True,
) -> VirtueSession:
    """
    Synchronous post-fill state updater (flatten_all → credit → here).

    Updates W/L streaks and locks the entry pipeline until an absolute
    future engine cycle (no wall-clock dependency).
    """
    delta = round(float(trade_pnl or 0.0), 2)
    tag = _normalize_outcome_reason(exit_reason)
    cycle = int(
        session.cycle if current_engine_cycle is None else current_engine_cycle
    )
    session.last_trade_pnl = delta
    session.last_reason = tag
    if count_trade:
        session.trades_today = int(session.trades_today) + 1

    tp_streak_for_cd = int(session.consecutive_tp_streak)

    if tag == "take_profit":
        session.last_result = "WIN"
        session.consecutive_wins = int(session.consecutive_wins) + 1
        session.consecutive_losses = 0
        # Trailing streak penalty uses pre-increment streak: base + streak * bonus.
        streak = int(session.consecutive_tp_streak)
        tp_streak_for_cd = streak
        session.consecutive_tp_streak = streak + 1
        session.last_tp_timestamp = float(time.time())
        if int(session.consecutive_tp_streak) >= int(
            VIRTUE_POST_TP_STREAK_PULLBACK_AFTER
        ):
            session.require_tp_pullback = True

    elif tag == "course_correct":
        # Thesis broke — always LOSS friction even if exit printed green.
        session.last_result = "LOSS"
        session.consecutive_losses = int(session.consecutive_losses) + 1
        session.consecutive_wins = 0
        session.consecutive_tp_streak = 0
        session.last_tp_timestamp = 0.0
        session.require_tp_pullback = False

    elif tag == "stop":
        session.last_result = "LOSS"
        session.consecutive_losses = int(session.consecutive_losses) + 1
        session.consecutive_wins = 0
        session.consecutive_tp_streak = 0
        session.last_tp_timestamp = 0.0
        session.require_tp_pullback = False

    elif tag == "time_decay":
        # Stagnant exposure cut — fast cool-off (thesis did not break).
        if delta > 0:
            session.last_result = "WIN"
            session.consecutive_wins = int(session.consecutive_wins) + 1
            session.consecutive_losses = 0
        else:
            session.last_result = "LOSS"
            session.consecutive_losses = int(session.consecutive_losses) + 1
            session.consecutive_wins = 0
            session.consecutive_tp_streak = 0
            session.last_tp_timestamp = 0.0
            session.require_tp_pullback = False

    elif tag == "virtue_pnl_lock":
        # Day shut-down latch already set; cool-off if residual flattened.
        if delta > 0:
            session.last_result = "WIN"
            session.consecutive_wins = int(session.consecutive_wins) + 1
            session.consecutive_losses = 0
        else:
            session.last_result = "LOSS"
            session.consecutive_losses = int(session.consecutive_losses) + 1
            session.consecutive_wins = 0
            session.consecutive_tp_streak = 0
            session.last_tp_timestamp = 0.0
            session.require_tp_pullback = False
        session.virtue_pnl_lock_active = True
        session.last_regime = "CHOP_NO_TRADE"

    else:
        # structured_exit / thesis_invalid / unknown — safety rebase cool-off
        if delta > 0:
            session.last_result = "WIN"
            session.consecutive_wins = int(session.consecutive_wins) + 1
            session.consecutive_losses = 0
        else:
            session.last_result = "LOSS"
            session.consecutive_losses = int(session.consecutive_losses) + 1
            session.consecutive_wins = 0
            session.consecutive_tp_streak = 0
            session.last_tp_timestamp = 0.0
            session.require_tp_pullback = False

    # Single cooldown map (macromathics_core Phase contract).
    cooldown_applied = int(
        cooldown_cycles_for_reason(tag, tp_streak=tp_streak_for_cd)
    )
    if tag not in {
        "take_profit",
        "course_correct",
        "stop",
        "time_decay",
        "virtue_pnl_lock",
        "profit_guard",
    }:
        cooldown_applied = int(VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES)

    # Any confirmed close clears the exposed-cycle marker.
    session.entry_cycle_marker = None

    resume_at = cycle + max(0, int(cooldown_applied))
    session.pipeline_resume_cycle = max(
        int(session.pipeline_resume_cycle or 0),
        resume_at,
    )

    logger.info(
        "OUTCOME_STATE last_result=%s last_reason=%s pnl=%.2f "
        "wins=%s losses=%s tp_streak=%s pullback=%s "
        "cooldown=%s resume_cycle=%s (now=%s)",
        session.last_result,
        session.last_reason,
        session.last_trade_pnl,
        session.consecutive_wins,
        session.consecutive_losses,
        session.consecutive_tp_streak,
        session.require_tp_pullback,
        cooldown_applied,
        session.pipeline_resume_cycle,
        cycle,
    )
    notify_trade_closed(
        reason=str(session.last_reason or tag),
        pnl=float(session.last_trade_pnl),
        cooldown_cycles=int(cooldown_applied),
        cycle=cycle,
    )
    return session


def _record_trade_outcome(session: VirtueSession, pnl: float, reason: str) -> None:
    """Compat wrapper — prefer update_outcome_state."""
    update_outcome_state(session, pnl, reason, current_engine_cycle=session.cycle)


def _credit_realized_pnl(session: VirtueSession, pnl: float) -> None:
    """
    Bank realized PnL into the day bucket.

    Justice: update book equity only on local paper. Live/sandbox reconcile already
    set broker.equity — adding approx_pnl again would double-count NAV.
    """
    delta = float(pnl or 0.0)
    session.realized_pnl_today = round(session.realized_pnl_today + delta, 2)
    # High-water mark for Virtue Balance trailing lock.
    if session.realized_pnl_today > float(session.peak_realized_pnl_today or 0.0):
        session.peak_realized_pnl_today = float(session.realized_pnl_today)
    if abs(delta) >= 1e-12 and _local_paper_book():
        session.broker.update_equity(round(float(session.broker.equity) + delta, 2))
        # Durable ledger — day-roll / weekend / dashboard bootstrap must not erase gains.
        save_paper_book(
            float(session.broker.equity),
            peak_equity=float(session.risk.peak_nav),
            source="credit_realized_pnl",
        )
    session.risk.update_nav(session.broker.equity)
    session.risk.peak_nav = max(float(session.risk.peak_nav), float(session.broker.equity))
    # Evaluate trailing floor after every realized update (may latch day lock).
    check_virtue_pnl_lock(session)


def _tactical_open_pnl(session: VirtueSession, price: float) -> float:
    """Mark-to-market for the tactical sleeve only (Temperance exits)."""
    from engine.config import POINT_VALUE

    if not bool(session.tactical_active) or int(session.tactical_size) <= 0:
        return 0.0
    entry = float(session.tactical_entry_price or 0.0)
    if entry <= 0:
        return 0.0
    side = str(session.tactical_side or "FLAT").upper()
    size = int(session.tactical_size)
    points = (float(price) - entry) if side == "LONG" else (entry - float(price))
    return round(points * float(POINT_VALUE) * size, 2)


def _mark_tactical_open(
    session: VirtueSession, *, side: str, price: float, size: int, cycle: int
) -> None:
    session.tactical_active = True
    session.tactical_side = str(side or "FLAT").upper()
    session.tactical_size = max(1, int(size))
    session.tactical_entry_price = float(price)
    session.entry_cycle_marker = int(cycle)


def _mark_tactical_flat(session: VirtueSession) -> None:
    session.tactical_active = False
    session.tactical_side = "FLAT"
    session.tactical_size = 0
    session.tactical_entry_price = 0.0
    session.entry_cycle_marker = None


def _mark_core_open(
    session: VirtueSession, *, side: str, price: float, size: int
) -> None:
    session.core_active = True
    session.core_side = str(side or "FLAT").upper()
    session.core_size = max(1, int(size))
    session.core_entry_price = float(price)
    session.core_entry_cycle = int(getattr(session, "cycle", 0) or 0)
    session.core_realized_pnl_this_cycle = 0.0


def _mark_core_flat(session: VirtueSession) -> None:
    session.core_active = False
    session.core_side = "FLAT"
    session.core_size = 0
    session.core_entry_price = 0.0
    session.core_entry_cycle = None


def _credit_core_pnl(session: VirtueSession, pnl: float) -> None:
    """Bank core sleeve PnL — never touches tactical temperance streaks."""
    delta = round(float(pnl or 0.0), 2)
    session.core_realized_pnl_this_cycle = delta
    session.core_realized_pnl_today = round(
        float(session.core_realized_pnl_today or 0.0) + delta, 2
    )
    _credit_realized_pnl(session, delta)


def _credit_tactical_pnl(session: VirtueSession, pnl: float) -> None:
    delta = round(float(pnl or 0.0), 2)
    session.tactical_realized_pnl_today = round(
        float(session.tactical_realized_pnl_today or 0.0) + delta, 2
    )
    _credit_realized_pnl(session, delta)


def _on_tactical_router_close(
    session: VirtueSession, *, pnl: float, reason: str, side: str, **_: Any
) -> None:
    _credit_tactical_pnl(session, pnl)
    update_outcome_state(
        session, pnl, reason, current_engine_cycle=session.cycle
    )
    session.long_streak = 0
    session.short_streak = 0


def _on_core_router_close(
    session: VirtueSession, *, pnl: float, reason: str, side: str, **_: Any
) -> None:
    _credit_core_pnl(session, pnl)
    logger.warning(
        "CYCLE %s CORE_SLEEVE_CLOSED reason=%s pnl≈%.2f — temperance untouched "
        "net_exposure=%s",
        session.cycle,
        reason,
        pnl,
        calculate_net_account_exposure(session),
    )
    # Macro invalidation gets a dedicated portfolio advisory from the escaper.
    if str(reason or "").startswith("core_invalidation"):
        return
    notify_trade_closed(
        reason=reason, pnl=float(pnl), cooldown_cycles=0, cycle=session.cycle
    )


async def _close_tactical_sleeve(
    session: VirtueSession,
    *,
    price: float,
    stop_ticks: int,
    reason: str,
) -> tuple[bool, float]:
    """Close tactical only via multi-sleeve router (core never flattened)."""
    if not bool(session.tactical_active) or int(session.tactical_size) <= 0:
        return False, 0.0
    ok, pnl, detail = await execute_tactical_action(
        session,
        target_side="FLAT",
        target_size=0,
        current_cycle=int(session.cycle),
        price=price,
        stop_ticks=stop_ticks,
        reason=reason,
        mark_flat=_mark_tactical_flat,
        on_filled=_on_tactical_router_close,
    )
    if not ok:
        logger.error(
            "CYCLE %s TACTICAL_ROUTER_CLOSE_FAILED detail=%s", session.cycle, detail
        )
    return ok, float(pnl or 0.0)


async def _close_core_sleeve(
    session: VirtueSession,
    *,
    price: float,
    stop_ticks: int,
    reason: str,
) -> tuple[bool, float]:
    """Close core on structural invalidation — no tactical temperance write."""
    if not bool(session.core_active) or int(session.core_size) <= 0:
        return False, 0.0
    ok, pnl, detail = await execute_core_action(
        session,
        target_side="FLAT",
        target_size=0,
        price=price,
        stop_ticks=stop_ticks,
        reason=reason,
        mark_flat=_mark_core_flat,
        on_filled=_on_core_router_close,
    )
    if not ok:
        logger.error(
            "CYCLE %s CORE_ROUTER_CLOSE_FAILED detail=%s", session.cycle, detail
        )
    return ok, float(pnl or 0.0)


async def _manage_core_sleeve(
    session: VirtueSession,
    *,
    price: float,
    stop_ticks: int,
    blend: float,
    adx: float,
    ignore_hours: bool = False,
) -> None:
    """Update structural regime; Slow Invalidation Core Escaper + core open."""
    regime, bull_s, bear_s = classify_structural_regime(
        macro_bias=str(session.macro_bias or "NEUTRAL"),
        adx=float(adx),
        confirm_cycles=structural_confirm_cycles(),
        bull_streak=int(session.structural_bull_streak),
        bear_streak=int(session.structural_bear_streak),
        blend=float(blend),
    )
    session.structural_bull_streak = bull_s
    session.structural_bear_streak = bear_s
    session.macro_structural_regime = regime
    session.core_realized_pnl_this_cycle = 0.0

    # Slow Invalidation — sticky through NEUTRAL; exit only hard flip / deep breakdown.
    inv, inv_reason = evaluate_core_macro_safety(
        session,
        int(session.cycle),
        blend=float(blend),
        structural_regime=regime,
    )
    if inv:
        prior_side = str(session.core_side or "FLAT")
        logger.warning(
            "CYCLE %s MACRO_INVALIDATION_TRIGGERED side=%s regime=%s blend=%.1f "
            "reason=%s — closing core anchor (temperance untouched)",
            session.cycle,
            prior_side,
            regime,
            float(blend),
            inv_reason,
        )
        ok, pnl = await _close_core_sleeve(
            session, price=price, stop_ticks=stop_ticks, reason=inv_reason
        )
        if ok:
            notify_core_macro_invalidation(
                side=prior_side,
                reason=inv_reason,
                blend=float(blend),
                regime=str(regime),
                pnl=float(pnl),
                cycle=int(session.cycle),
            )
        return

    open_ok, side = core_should_open(regime, core_active=bool(session.core_active))
    if not open_ok:
        return
    # Same RTH entry windows + extreme override as tactical (Temperance/Courage).
    if not ignore_hours and not virtue_entries_allowed(
        adx=float(adx), blend=float(blend)
    ):
        logger.info(
            "CYCLE %s CORE_OPEN_BLOCKED outside_entry_window — manage/invalidation only",
            session.cycle,
        )
        return
    if not bool(session.last_structure_ok):
        logger.info(
            "CYCLE %s CORE_OPEN_BLOCKED %s",
            session.cycle,
            session.last_structure_reason,
        )
        return
    # Room under ceiling for core size.
    tac = int(session.tactical_size) if bool(session.tactical_active) else 0
    need = core_size_default()
    from engine.config import MAX_ACCOUNT_CONTRACT_CEILING

    if tac + need > int(MAX_ACCOUNT_CONTRACT_CEILING):
        logger.info(
            "CYCLE %s CORE_OPEN_BLOCKED ceiling tac=%s need=%s cap=%s",
            session.cycle,
            tac,
            need,
            int(MAX_ACCOUNT_CONTRACT_CEILING),
        )
        return
    # If tactical is opposite, do not open core (alignment / net rule).
    if tac > 0 and str(session.tactical_side).upper() != side:
        logger.info(
            "CYCLE %s CORE_OPEN_BLOCKED opposite_tactical tac=%s core_want=%s",
            session.cycle,
            session.tactical_side,
            side,
        )
        return

    ok, _, detail = await execute_core_action(
        session,
        target_side=side,
        target_size=need,
        price=price,
        stop_ticks=stop_ticks,
        reason=f"core_open:{regime}",
        mark_open=_mark_core_open,
    )
    if not ok:
        logger.info(
            "CYCLE %s CORE_ROUTER_OPEN_BLOCKED detail=%s", session.cycle, detail
        )
        return
    logger.info(
        "CYCLE %s CORE_SLEEVE_OPENED side=%s size=%s @ %s regime=%s net_exposure=%s",
        session.cycle,
        session.core_side,
        session.core_size,
        session.core_entry_price,
        regime,
        calculate_net_account_exposure(session),
    )
    notify_position_opened(
        side=session.core_side,
        client_order_id="",
        cycle=int(session.cycle),
        size=int(session.core_size),
    )


def _publish_ui(session: VirtueSession, *, last_price: float | None = None) -> None:
    """Persist to primary live data/system_state.json for Streamlit / cloud dashboard."""
    price = last_price if last_price is not None else float(getattr(session.broker, "_last_price", 0.0) or 0.0)
    persist_virtue_system_state(
        session,
        last_price=price if price > 0 else None,
        regime=session.last_regime,
        action=session.last_action,
        reason=session.last_signal_reason,
        adx=session.last_adx,
        atr_pct=session.last_atr_pct,
        vwap_score=session.last_vwap_score,
        twap_score=session.last_twap_score,
        blended_score=session.last_blended_score,
        data_source=session.last_data_source,
        last_risk_verdict=session.last_risk_verdict,
        last_risk_reason=session.last_risk_reason,
        heartbeat_state=session.last_heartbeat_state,
    )


async def save_state_throttled(
    session: VirtueSession,
    *,
    interval_s: float | None = None,
) -> None:
    """
    Background Justice writer — concurrent with the market event engine.
    Always writes the primary live path: data/system_state.json (via _publish_ui).
    Disk I/O stays off the hot path via asyncio.to_thread.
    """
    sleep_s = float(interval_s if interval_s is not None else VIRTUE_STATE_PERSIST_INTERVAL_S)
    sleep_s = max(0.5, sleep_s)
    while session.is_running:
        try:
            await asyncio.to_thread(_publish_ui, session)
        except Exception as exc:
            logger.error("Justice Layer write failure (system_state.json): %s", exc)
        await asyncio.sleep(sleep_s)


async def market_tick_listener(
    session: VirtueSession,
    tick_queue: asyncio.Queue,
    *,
    poll_s: float,
) -> None:
    """
    Produce market tick events onto the queue (event-driven source).
    Calm efficiency: poll at poll_s; skip enqueue when price is quiet unless
    a force-event timer fires (so open risk still gets stop/TP checks).
    """
    cadence = max(0.5, float(poll_s))
    min_move = max(1, int(VIRTUE_MIN_PRICE_MOVE_TICKS)) * float(TICK_SIZE)
    force_every = max(cadence, float(VIRTUE_FORCE_EVENT_MAX_S))
    last_emitted_px = 0.0
    last_emit_mono = 0.0
    logger.info(
        "MARKET_LISTENER start poll_s=%.1f force_event_max=%.1fs min_move=%.2f → event queue",
        cadence,
        force_every,
        min_move,
    )
    while session.is_running:
        try:
            ctx = await session.broker.resolve_market_context_timed()
            tick = (ctx or {}).get("tick") or {}
            px = float(tick.get("price") or tick.get("last") or 0.0)
            now_mono = time.monotonic()
            holding = session.broker.net_exposure()[1] > 0
            moved = last_emitted_px <= 0 or abs(px - last_emitted_px) >= min_move
            due = (now_mono - last_emit_mono) >= force_every
            # Always emit when holding (Temperance stops), on meaningful move, or force timer.
            if px > 0 and (holding or moved or due or last_emitted_px <= 0):
                while not tick_queue.empty():
                    try:
                        tick_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await tick_queue.put({"ok": True, "ctx": ctx})
                last_emitted_px = px
                last_emit_mono = now_mono
        except Exception as exc:
            logger.exception("market_tick_listener_failed err=%s", exc)
            try:
                await tick_queue.put({"ok": False, "error": str(exc)})
            except Exception:
                logger.exception("market_tick_enqueue_failed")
        await asyncio.sleep(cadence)
    try:
        await tick_queue.put(None)
    except Exception:
        pass


async def engine_event_loop(
    session: VirtueSession,
    tick_queue: asyncio.Queue,
    *,
    cycles: int | None,
    once: bool,
    ignore_hours: bool,
    stop_ticks: int = DEFAULT_STOP_TICKS,
) -> int:
    """
    Consume market tick events and run virtue cycles (Courage — act when signal arrives).
    Replaces the old sleep-then-poll main loop.
    """
    n = 0
    while session.is_running:
        try:
            item = await tick_queue.get()
        except Exception as exc:
            logger.exception("engine_event_queue_get_failed err=%s", exc)
            await asyncio.sleep(1.0)
            continue

        # Coalesce to latest event while running.
        while True:
            try:
                nxt = tick_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            item = nxt

        if item is None:
            break

        market_ctx = None
        if isinstance(item, dict) and item.get("ok") and isinstance(item.get("ctx"), dict):
            market_ctx = item["ctx"]
        elif isinstance(item, dict) and not item.get("ok"):
            logger.error(
                "CYCLE pending market_listener_error err=%s — outage survival",
                item.get("error"),
            )
            try:
                await _survive_outage(session, "market_listener_error")
            except Exception:
                logger.exception("reconnect_after_listener_error_failed")
            continue

        try:
            await run_cycle(
                session,
                stop_ticks=stop_ticks,
                ignore_hours=ignore_hours,
                market_ctx=market_ctx,
            )
        except Exception as exc:
            logger.exception("cycle_unhandled err=%s — infinite outage survival", exc)
            try:
                await _survive_outage(session, "cycle_exception")
            except Exception:
                logger.exception("reconnect_after_cycle_exception_failed — will retry next event")

        n += 1
        if once or (cycles is not None and n >= cycles):
            session.is_running = False
            break

    return n


def _should_run_heartbeat(session: VirtueSession) -> bool:
    """Calm efficiency: probe every N cycles; always on first / after prior failure / triage."""
    every = max(1, int(VIRTUE_HEARTBEAT_EVERY_N_CYCLES))
    session.cycles_since_heartbeat += 1
    if session.cycle <= 1:
        return True
    if not session.last_heartbeat_ok:
        return True
    if not session.broker.can_send_new_orders():
        return True
    return session.cycles_since_heartbeat >= every



async def _heartbeat_probe(broker: VirtueBroker) -> tuple[bool, str]:
    try:
        return await asyncio.wait_for(
            broker.health_check_timed(),
            timeout=NETWORK_TIMEOUT_S,
        )
    except Exception as exc:
        return False, str(exc)


async def seed_wisdom_from_market(session: VirtueSession, *, limit: int = 120) -> int:
    """Warm WisdomStrategy from Databento MES bars (preferred) or Alpaca SPY→MES proxy."""
    bars: list[Bar] = []
    seed_src = "none"
    mes_data = getattr(session.broker, "mes_data", None)
    if mes_data is not None and mes_data.is_configured():
        try:
            raw = await mes_data.fetch_ohlcv_bars(timeframe="1m", limit=limit, lookback_hours=36)
            ohlc = mes_data.bars_as_strategy_ohlc(raw)
            bars = [
                Bar(
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=max(1.0, float(b.get("volume") or 1.0)),
                )
                for b in ohlc
            ]
            if bars:
                seed_src = "databento_mes"
                session.last_data_source = "databento_mes"
        except Exception as exc:
            logger.exception("databento_bar_seed_failed err=%s", exc)

    if not bars:
        try:
            spy_bars = await session.broker.data.fetch_spy_bars(timeframe="5Min", limit=limit)
            mes_bars = session.broker.data.mes_proxy_bars_from_spy(spy_bars)
            bars = [
                Bar(
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=max(1.0, float(b.get("volume") or 1.0)),
                )
                for b in mes_bars
            ]
            if bars:
                seed_src = "alpaca_spy_mes_proxy"
                session.last_data_source = "alpaca_spy_mes_proxy"
        except Exception as exc:
            logger.exception("alpaca_bar_seed_failed err=%s", exc)
            return 0

    if not bars:
        logger.warning("bar_seed empty — Wisdom stays in WARMUP until live ticks accumulate")
        return 0
    session.strategy.seed(bars)
    session.broker._last_price = float(bars[-1].close)
    decision = session.strategy.evaluate()
    update_macro_bias(
        session,
        regime=decision.regime.value,
        action=decision.action.value,
        blend=float(decision.blended_score),
        adx=float(decision.adx),
    )
    logger.info(
        "WISDOM_SEEDED src=%s bars=%s regime=%s action=%s vwap=%.1f twap=%.1f blend=%.1f "
        "adx=%.1f macro_bias=%s",
        seed_src,
        len(bars),
        decision.regime.value,
        decision.action.value,
        decision.vwap_score,
        decision.twap_score,
        decision.blended_score,
        decision.adx,
        session.macro_bias,
    )
    return len(bars)


# Backward-compatible alias
async def seed_wisdom_from_alpaca(session: VirtueSession, *, limit: int = 120) -> int:
    return await seed_wisdom_from_market(session, limit=limit)


async def _survive_outage(session: VirtueSession, reason: str) -> bool:
    """Enter triage and retry forever until broker + reconcile succeed."""
    session.broker.enter_triage(reason)
    logger.warning("OUTAGE_SURVIVAL reason=%s — infinite reconnect (30–60s backoff)", reason)
    try:
        ok = await session.broker.poll_until_reconnected_forever()
    except Exception as exc:
        logger.exception("outage_survival_failed err=%s", exc)
        return False
    if ok:
        session.risk.update_nav(session.broker.equity)
        # Do NOT copy broker.realized_pnl into realized_pnl_today — it can be
        # process-lifetime paper PnL and would falsely trip daily_profit_lock.
        roll_daily_counters_if_needed(session)
        logger.info(
            "OUTAGE_RECOVERED equity=%.2f realized_pnl_today=%.2f positions=%s",
            session.broker.equity,
            session.realized_pnl_today,
            len(session.broker.open_positions),
        )
    return ok


def _open_risk_notional(session: VirtueSession, stop_ticks: int) -> float:
    """Open risk at the dollar stop (Temperance — matches live exit, not legacy tick stop)."""
    del stop_ticks  # legacy signature; dollar stop owns live risk
    _, size = session.broker.net_exposure()
    if size <= 0:
        return 0.0
    return float(VIRTUE_POSITION_STOP_DOLLARS)


async def _resolve_flatten_price(session: VirtueSession) -> float:
    price = float(getattr(session.broker, "_last_price", 0.0) or 0.0)
    if price > 0:
        return price
    try:
        ctx = await session.broker.resolve_market_context_timed()
        tick = (ctx or {}).get("tick") or {}
        return float(tick.get("price") or tick.get("last") or 0.0)
    except Exception as exc:
        logger.exception("rth_flatten_price_failed err=%s", exc)
        return 0.0


async def _flatten_until_flat(
    session: VirtueSession,
    *,
    stop_ticks: int,
    reason: str = "rth_close_flatten",
) -> bool:
    """
    Temperance: retry flatten until flat so residual size cannot ride overnight/weekend gaps.
    Returns True when exposure is flat.
    """
    attempts = max(1, int(VIRTUE_RTH_FLATTEN_MAX_ATTEMPTS))
    retry_s = max(0.5, float(VIRTUE_RTH_FLATTEN_RETRY_S))
    for attempt in range(1, attempts + 1):
        net_dir, net_size = session.broker.net_exposure()
        if net_size <= 0 and absolute_contract_footprint(session) <= 0:
            return True
        price = await _resolve_flatten_price(session)
        if price <= 0:
            logger.error(
                "CYCLE %s %s attempt=%s/%s no mark price — cannot flatten %s x%s",
                session.cycle,
                reason,
                attempt,
                attempts,
                net_dir,
                net_size,
            )
            await asyncio.sleep(retry_s)
            continue
        try:
            # Sleeve-sequential — tactical temperance first, core book second.
            ok, pnl = await close_all_sleeves_sequential(
                session,
                price=price,
                stop_ticks=stop_ticks,
                reason=reason,
                close_tactical=_close_tactical_sleeve,
                close_core=_close_core_sleeve,
                credit_residual=_credit_realized_pnl,
            )
        except Exception as exc:
            logger.exception(
                "CYCLE %s %s attempt=%s/%s exception err=%s",
                session.cycle,
                reason,
                attempt,
                attempts,
                exc,
            )
            await asyncio.sleep(retry_s)
            continue
        if ok:
            logger.info(
                "CYCLE %s %s attempt=%s sleeves_closed prior=%s x%s pnl≈%.2f "
                "resume_cycle=%s",
                session.cycle,
                reason,
                attempt,
                net_dir,
                net_size,
                pnl,
                session.pipeline_resume_cycle,
            )
        else:
            logger.error(
                "CYCLE %s %s attempt=%s/%s FAILED — retrying",
                session.cycle,
                reason,
                attempt,
                attempts,
            )
        if session.broker.net_exposure()[1] <= 0:
            return True
        await asyncio.sleep(retry_s)
    _, left = session.broker.net_exposure()
    if left > 0:
        logger.error(
            "CYCLE %s %s EXHAUSTED — exposure may remain overnight size=%s",
            session.cycle,
            reason,
            left,
        )
        return False
    return True


async def _rth_gate_or_flatten(
    session: VirtueSession,
    *,
    stop_ticks: int,
    ignore_hours: bool,
) -> bool:
    """
    Return True if trading cycle may continue.
    Outside session (RTH or CME): flatten residual risk and return False (Temperance).
    """
    if ignore_hours or virtue_session_open():
        return True
    session.last_regime = "OUTSIDE_SESSION"
    session.last_signal_reason = "outside_session_stand_aside"
    _, net_size = session.broker.net_exposure()
    if net_size > 0:
        await _flatten_until_flat(session, stop_ticks=stop_ticks, reason="session_close_flatten")
    else:
        logger.info(
            "CYCLE %s outside_session — stand aside (%s)",
            session.cycle,
            virtue_session_label(),
        )
    session.last_action = "FLAT"
    return False


async def run_cycle(
    session: VirtueSession,
    *,
    stop_ticks: int = DEFAULT_STOP_TICKS,
    ignore_hours: bool = False,
    market_ctx: dict | None = None,
) -> None:
    """One virtue cycle: hours → heartbeat → market → regime → exclusivity → Manus → fire."""
    session.cycle += 1
    # Temperance: new ET calendar day → clear yesterday's PnL/trade counters (Justice).
    roll_daily_counters_if_needed(session)
    # Tick post-rebase entry cooldown every cycle (even while holding).
    cooldown_blocks_entry = session.entry_cooldown_cycles > 0
    if session.entry_cooldown_cycles > 0:
        session.entry_cooldown_cycles -= 1

    # Session gate: RTH-only when Alpaca primary; full CME hours when Databento primary.
    # Live cash default: daytime-only (no overnight) unless FM_LIVE_ALLOW_OVERNIGHT=1.
    # Outside session → flatten residual risk (Temperance).
    if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
        return

    from engine.config import live_halt_flattens, trading_halted

    if trading_halted():
        session.last_regime = "OPERATOR_HALT"
        session.last_signal_reason = "FM_TRADING_HALTED"
        _, net_size = session.broker.net_exposure()
        if net_size > 0 and live_halt_flattens():
            await _flatten_until_flat(session, stop_ticks=stop_ticks, reason="operator_halt_flatten")
        else:
            logger.warning("CYCLE %s FM_TRADING_HALTED — stand aside (kill switch)", session.cycle)
        session.last_action = "FLAT"
        return

    if session.halted:
        logger.warning("CYCLE %s session_halted by Manus — stand aside", session.cycle)
        session.last_action = "FLAT"
        return

    # Justice: sleeve books must match broker net before any risk work.
    try:
        ok_sl, sl_detail = reconcile_sleeves_to_broker(session, session.broker)
        session.sleeve_reconcile_ok = bool(ok_sl)
        session.sleeve_reconcile_detail = str(sl_detail)
        if not ok_sl:
            logger.warning("CYCLE %s %s", session.cycle, sl_detail)
    except Exception as exc:
        session.sleeve_reconcile_ok = False
        session.sleeve_reconcile_detail = f"sleeve_reconcile:error:{exc}"
        logger.exception("sleeve_reconcile_failed err=%s", exc)

    if session.broker.has_pending_live_order():
        logger.warning(
            "CYCLE %s pending_live_order id=%s — stand aside new risk",
            session.cycle,
            session.broker._pending_live_order_id,
        )
        session.last_action = "FLAT"
        return

    # Calm efficiency: full heartbeat every N cycles (old-format load), not every event.
    if _should_run_heartbeat(session):
        hb = BrokerHeartbeatAgent(
            brokers={"primary": lambda: _heartbeat_probe(session.broker)},
            interval_s=5.0,
            degraded_ms=NETWORK_TIMEOUT_S * 1000.0,
            green_ms=min(2000.0, NETWORK_TIMEOUT_S * 1000.0),
        )
        try:
            events = await asyncio.wait_for(hb.run_once(), timeout=NETWORK_TIMEOUT_S + 1.0)
        except Exception as exc:
            logger.exception("heartbeat_run_failed err=%s", exc)
            session.last_heartbeat_ok = False
            session.cycles_since_heartbeat = 0
            await _survive_outage(session, "heartbeat_exception")
            session.last_action = "FLAT"
            return

        dead = any(e.state == HeartbeatState.DEAD for e in events)
        degraded = any(e.state == HeartbeatState.DEGRADED for e in events)
        session.cycles_since_heartbeat = 0
        if dead:
            session.last_heartbeat_ok = False
            session.last_heartbeat_state = "DEAD"
            recovered = await _survive_outage(session, "heartbeat_dead")
            if not recovered:
                session.last_action = "FLAT"
                return
            # Outage may have spanned the 16:00 boundary — re-gate before continuing.
            if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
                return
        else:
            session.last_heartbeat_ok = True
            session.last_heartbeat_state = "DEGRADED" if degraded else "GREEN"
    # else: skipped heartbeat this cycle — cycles_since_heartbeat already advanced

    if not session.broker.can_send_new_orders():
        logger.warning("CYCLE %s triage=%s — forcing reconcile path", session.cycle, session.broker.triage.value)
        recovered = await _survive_outage(session, "triage_not_ready")
        if not recovered or not session.broker.can_send_new_orders():
            session.last_action = "FLAT"
            return
        if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
            return

    # Event-driven path supplies market_ctx from the listener; fallback fetch for --once tools.
    if market_ctx is not None:
        ctx = market_ctx
    else:
        try:
            ctx = await session.broker.resolve_market_context_timed()
        except Exception as exc:
            logger.exception("market_context_failed err=%s", exc)
            await _survive_outage(session, "market_context_exception")
            session.last_action = "FLAT"
            return

    if ctx.get("stand_aside"):
        logger.error(
            "CYCLE %s market_data_stand_aside detail=%s — Justice flat (need live MES, not stale SPY)",
            session.cycle,
            ctx.get("detail"),
        )
        session.last_action = "FLAT"
        session.last_regime = "NO_FRESH_DATA"
        session.last_signal_reason = str(ctx.get("detail") or "stand_aside")
        return

    tick = ctx.get("tick") or {}
    session.last_data_source = str(tick.get("source") or session.last_data_source)
    price = float(tick.get("price") or tick.get("last") or 0.0)
    if price <= 0:
        logger.error("CYCLE %s invalid_price — Justice stand aside", session.cycle)
        session.last_action = "FLAT"
        return
    session.last_price = price

    # Re-check RTH after market context (cycle may have started in RTH then crossed close).
    if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
        return
    high = float(tick.get("ask") or price)
    low = float(tick.get("bid") or price)
    if high < low:
        high, low = low, high
    try:
        tick_vol = float(tick.get("size") or tick.get("volume") or 1.0)
    except (TypeError, ValueError):
        tick_vol = 1.0
    tick_vol = max(1.0, tick_vol)

    # Rebase ONLY on ghost/seed faults (Justice). Session price≠VWAP is the signal —
    # do not wipe anchors when VWAP/TWAP diverge a few ATRs (that is regime info).
    try:
        need_rebase = (not session.anchors_aligned) or session.strategy.anchor_gap_too_wide(
            price
        )
        if need_rebase:
            session.strategy.rebase_anchors_to_price(price)
            session.anchors_aligned = True
            session.entry_cooldown_cycles = max(
                int(session.entry_cooldown_cycles),
                max(0, int(VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES)),
            )
            # Same-cycle: do not fire new entries right after rebase (scores near 50).
            cooldown_blocks_entry = session.entry_cooldown_cycles > 0
            logger.info(
                "CYCLE %s anchors_rebased_to_live px=%.2f vwap=%.2f twap=%.2f entry_cooldown=%s",
                session.cycle,
                price,
                session.strategy.vwap_tracker.vwap if session.strategy.vwap_tracker else 0.0,
                session.strategy.twap_tracker.twap if session.strategy.twap_tracker else 0.0,
                session.entry_cooldown_cycles,
            )
    except Exception as exc:
        logger.exception("anchor_rebase_failed err=%s", exc)
        session.last_action = "FLAT"
        return

    # Use true bid/ask (or mid) — do not pad ±1 tick (that inflated ATR on proxy quotes).
    # Pass quote/trade size so VWAP is volume-weighted (TWAP stays equal-weight).
    session.strategy.update_price(price, high=high, low=low, volume=tick_vol)

    # Strategy hold path is tactical-only — core must not skip flat ADX gates.
    if bool(session.tactical_active) and int(session.tactical_size) > 0:
        holding = str(session.tactical_side or "").upper() or None
    else:
        holding = None
    decision = session.strategy.evaluate(holding=holding)
    session.last_regime = decision.regime.value
    session.last_signal_reason = decision.reason
    session.last_adx = float(decision.adx)
    session.last_atr_pct = float(decision.atr_pct)
    session.last_vwap_score = float(decision.vwap_score)
    session.last_twap_score = float(decision.twap_score)
    session.last_blended_score = float(decision.blended_score)
    # RTH windows (ET) + structure memory — compute before commit for this cycle's gates.
    session.allow_new_entries = bool(ignore_hours) or allow_new_entries()
    # allow_new_entries flag is window-only; extreme override applied at fire time.
    structure_ok, structure_reason = evaluate_entry_structure_gates(
        session,
        atr=float(decision.atr),
        adx=float(decision.adx),
        vwap=float(decision.vwap),
        twap=float(decision.twap),
        price=float(price),
    )
    session.last_structure_ok = bool(structure_ok)
    session.last_structure_reason = str(structure_reason)
    commit_entry_structure_memory(
        session,
        atr=float(decision.atr),
        adx=float(decision.adx),
        vwap=float(decision.vwap),
        twap=float(decision.twap),
        price=float(price),
    )
    _, size_for_tp = session.broker.net_exposure()
    size_ref = size_for_tp if size_for_tp > 0 else 2
    tp_ticks = position_tp_ticks(size_ref)
    sl_ticks = position_stop_ticks(size_ref)
    open_pnl = session.broker.unrealized_position_pnl(price=price)

    # Dual independent streaks from raw entry bands (build while flat OR holding).
    # Temperance + velocity: widen bands after losses / course_correct / low ADX.
    # Enables same-cycle flip when opposite streak is already ready (Courage).
    base_contracts, blend_buffer_raw = calculate_temperance_parameters(session=session)
    vel_penalty = velocity_blend_penalty(adx=float(decision.adx))
    update_macro_bias(
        session,
        regime=decision.regime.value,
        action=decision.action.value,
        blend=float(decision.blended_score),
        adx=float(decision.adx),
    )
    bias_now = str(session.macro_bias or "NEUTRAL")
    long_buf = effective_temperance_blend_buffer(
        blend_buffer_raw,
        side="LONG",
        adx=float(decision.adx),
        macro_bias=bias_now,
    )
    short_buf = effective_temperance_blend_buffer(
        blend_buffer_raw,
        side="SHORT",
        adx=float(decision.adx),
        macro_bias=bias_now,
    )
    long_enter_thr = (
        float(VIRTUE_SCORE_LONG_ENTER) + float(long_buf) + float(vel_penalty)
    )
    short_enter_thr = (
        float(VIRTUE_SCORE_SHORT_ENTER) - float(short_buf) - float(vel_penalty)
    )
    v_score = float(decision.vwap_score)
    t_score = float(decision.twap_score)
    is_raw_long = v_score >= long_enter_thr and t_score >= long_enter_thr
    is_raw_short = v_score <= short_enter_thr and t_score <= short_enter_thr
    # Temperance: do not build confirmation streaks during pipeline lock
    # (prevents instant re-fire the moment cool-off ends).
    pipe_resume = int(getattr(session, "pipeline_resume_cycle", 0) or 0)
    pipeline_locked = pipe_resume > 0 and int(session.cycle) < pipe_resume
    if pipeline_locked:
        session.long_streak = 0
        session.short_streak = 0
    else:
        session.long_streak = (session.long_streak + 1) if is_raw_long else 0
        session.short_streak = (session.short_streak + 1) if is_raw_short else 0
    # Cycle log / friction: show side-aware buffer (0 when strong with-trend waived).
    if decision.action.value == "LONG":
        blend_buffer = float(long_buf)
    elif decision.action.value == "SHORT":
        blend_buffer = float(short_buf)
    else:
        blend_buffer = float(max(long_buf, short_buf))
    logger.info(
        "CYCLE %s regime=%s action=%s vwap=%.1f%% twap=%.1f%% blend=%.1f%% "
        "px=%.2f vwap_px=%.2f twap_px=%.2f adx=%.1f atr=%.2f atr_pct=%.2f "
        "tp=$%.0f (~%st) sl=$%.0f (~%st) open_pnl=%.2f long_streak=%s short_streak=%s "
        "macro_bias=%s temperance_buf=%.1f velocity=%.1f reason=%s exposure=%s",
        session.cycle,
        decision.regime.value,
        decision.action.value,
        decision.vwap_score,
        decision.twap_score,
        decision.blended_score,
        price,
        decision.vwap,
        decision.twap,
        decision.adx,
        decision.atr,
        decision.atr_pct,
        float(VIRTUE_POSITION_TP_DOLLARS),
        tp_ticks,
        float(VIRTUE_POSITION_STOP_DOLLARS),
        sl_ticks,
        open_pnl,
        session.long_streak,
        session.short_streak,
        session.macro_bias,
        float(blend_buffer),
        float(vel_penalty),
        decision.reason,
        session.broker.net_exposure(),
    )

    # Legacy broker exposure without sleeve tags → attribute to tactical satellite.
    net_sync_dir, net_sync_sz = session.broker.net_exposure()
    claimed = (
        (int(session.core_size) if session.core_active else 0)
        + (int(session.tactical_size) if session.tactical_active else 0)
    )
    if net_sync_sz > 0 and claimed == 0:
        entry = float(session.broker._avg_entry(net_sync_dir) or price)
        _mark_tactical_open(
            session,
            side=str(net_sync_dir),
            price=entry,
            size=int(net_sync_sz),
            cycle=int(session.cycle),
        )

    # Dual-sleeve: update structural regime + core open/invalidate (before tactical escapes).
    await _manage_core_sleeve(
        session,
        price=price,
        stop_ticks=stop_ticks,
        blend=float(decision.blended_score),
        adx=float(decision.adx),
        ignore_hours=ignore_hours,
    )

    # ============================================================
    # PHASE 1 — FINANCIAL PROTECTION (Profit Guard / Virtue Balance)
    # ============================================================
    if check_virtue_pnl_lock(session):
        net_dir_vl, net_size_vl = session.broker.net_exposure()
        if net_size_vl > 0 or absolute_contract_footprint(session) > 0:
            try:
                # Sleeve-sequential close — never blind flatten_all across books.
                ok, pnl = await close_all_sleeves_sequential(
                    session,
                    price=price,
                    stop_ticks=stop_ticks,
                    reason="virtue_pnl_lock",
                    close_tactical=_close_tactical_sleeve,
                    close_core=_close_core_sleeve,
                    credit_residual=_credit_realized_pnl,
                )
            except Exception as exc:
                logger.exception("virtue_pnl_lock_flatten_failed err=%s", exc)
                session.last_action = "FLAT"
                return
            if ok:
                logger.warning(
                    "CYCLE %s VIRTUE_PNL_LOCK_FLAT sleeves_closed prior=%s x%s "
                    "pnl≈%.2f peak=%.2f realized=%.2f",
                    session.cycle,
                    net_dir_vl,
                    net_size_vl,
                    pnl,
                    session.peak_realized_pnl_today,
                    session.realized_pnl_today,
                )
            else:
                logger.error(
                    "CYCLE %s VIRTUE_PNL_LOCK_FLAT_FAILED — exposure may remain",
                    session.cycle,
                )
        session.last_action = "FLAT"
        session.last_regime = "CHOP_NO_TRADE"
        return

    # ============================================================
    # PHASE 2 — TACTICAL IN-FLIGHT ESCAPES (core uses structural invalidation)
    # ============================================================
    if bool(session.tactical_active) and int(session.tactical_size) > 0:
        tac_side = str(session.tactical_side or "FLAT").upper()
        cc_hit, cc_reason = check_course_correct(
            tac_side, float(decision.blended_score)
        )
        if cc_hit:
            logger.warning(
                "CYCLE %s COURSE_CORRECT_TRIGGERED holding=%s blend=%.1f — %s",
                session.cycle,
                tac_side,
                float(decision.blended_score),
                cc_reason,
            )
            notify_course_correct(
                exposure=str(tac_side),
                blend=float(decision.blended_score),
                reason=str(cc_reason),
            )
            ok, pnl = await _close_tactical_sleeve(
                session, price=price, stop_ticks=stop_ticks, reason=cc_reason
            )
            if ok:
                logger.info(
                    "CYCLE %s COURSE_CORRECT_FLAT tactical pnl≈%.2f resume_cycle=%s "
                    "core_remains=%s",
                    session.cycle,
                    pnl,
                    session.pipeline_resume_cycle,
                    bool(session.core_active),
                )
            else:
                logger.error(
                    "CYCLE %s COURSE_CORRECT_FLAT_FAILED — tactical may remain",
                    session.cycle,
                )
            session.last_action = "FLAT"
            return

    tactical_pnl = _tactical_open_pnl(session, price)

    # Dollar stop — tactical sleeve only (Temperance). Core ignores $ stop.
    if bool(session.tactical_active) and tactical_pnl <= -float(
        VIRTUE_POSITION_STOP_DOLLARS
    ):
        ok, pnl = await _close_tactical_sleeve(
            session,
            price=price,
            stop_ticks=stop_ticks,
            reason=f"stop_${float(VIRTUE_POSITION_STOP_DOLLARS):.0f}",
        )
        if ok:
            logger.info(
                "CYCLE %s STOP_EXIT tactical pnl≈%.2f stop=$%.0f resume_cycle=%s",
                session.cycle,
                pnl,
                float(VIRTUE_POSITION_STOP_DOLLARS),
                session.pipeline_resume_cycle,
            )
        else:
            logger.error("CYCLE %s STOP_EXIT_FAILED — tactical may remain", session.cycle)
        session.last_action = "FLAT"
        return

    # Take-profit — tactical sleeve only.
    if bool(session.tactical_active) and tactical_pnl >= float(
        VIRTUE_POSITION_TP_DOLLARS
    ):
        tac_dir = str(session.tactical_side or "FLAT").upper()
        ok, pnl = await _close_tactical_sleeve(
            session,
            price=price,
            stop_ticks=stop_ticks,
            reason=f"take_profit_${float(VIRTUE_POSITION_TP_DOLLARS):.0f}",
        )
        if ok:
            if session.require_tp_pullback or int(session.consecutive_tp_streak) >= int(
                VIRTUE_MULTI_TP_COOLDOWN_STREAK
            ):
                session.long_streak = 0
                session.short_streak = 0
            elif tac_dir == "LONG":
                session.long_streak = 1
                session.short_streak = 0
            elif tac_dir == "SHORT":
                session.short_streak = 1
                session.long_streak = 0
            if tac_dir == "LONG":
                session.long_tps_today = int(session.long_tps_today) + 1
            elif tac_dir == "SHORT":
                session.short_tps_today = int(session.short_tps_today) + 1
            update_macro_bias(session, banked_side=tac_dir)
            multi_lock = int(session.consecutive_tp_streak) >= int(
                VIRTUE_MULTI_TP_COOLDOWN_STREAK
            )
            _, pipe_rem = pipeline_lock_remaining(session)
            logger.info(
                "CYCLE %s TAKE_PROFIT_FULL tactical %s pnl≈%.2f target=$%.0f "
                "resume_cycle=%s pipe_rem=%s tp_streak=%s core_remains=%s",
                session.cycle,
                tac_dir,
                pnl,
                float(VIRTUE_POSITION_TP_DOLLARS),
                session.pipeline_resume_cycle,
                pipe_rem,
                session.consecutive_tp_streak,
                bool(session.core_active),
            )
        else:
            logger.error("CYCLE %s TAKE_PROFIT_FULL_FAILED — tactical may remain", session.cycle)
        session.last_action = "FLAT"
        return

    # Time-decay — tactical sleeve only (core is structural, not time-decayed).
    if bool(session.tactical_active) and int(session.tactical_size) > 0:
        td_hit, td_reason = check_time_decay_exit(
            session,
            session.cycle,
            open_pnl=float(tactical_pnl),
            exposure=str(session.tactical_side),
        )
        if td_hit:
            ok, pnl = await _close_tactical_sleeve(
                session, price=price, stop_ticks=stop_ticks, reason=td_reason
            )
            if ok:
                logger.info(
                    "CYCLE %s TIME_DECAY_FLAT tactical pnl≈%.2f resume_cycle=%s "
                    "core_remains=%s",
                    session.cycle,
                    pnl,
                    session.pipeline_resume_cycle,
                    bool(session.core_active),
                )
            else:
                logger.error(
                    "CYCLE %s TIME_DECAY_FLAT_FAILED — tactical may remain",
                    session.cycle,
                )
            session.last_action = "FLAT"
            return

    # Opposite-band exit — tactical only. Core waits for structural invalidation.
    # Do NOT reverse same cycle (structure); reverse needs a fresh flat streak later.
    if decision.action in {SignalAction.LONG, SignalAction.SHORT}:
        want = decision.action.value
        if (
            bool(session.tactical_active)
            and int(session.tactical_size) > 0
            and str(session.tactical_side).upper() != want
        ):
            tac_dir = str(session.tactical_side).upper()
            logger.warning(
                "CYCLE %s STRUCTURED_EXIT tactical=%s → signal=%s "
                "(close tactical; core uses structural invalidation)",
                session.cycle,
                tac_dir,
                want,
            )
            ok, pnl = await _close_tactical_sleeve(
                session,
                price=price,
                stop_ticks=stop_ticks,
                reason=f"structured_exit:{tac_dir}_vs_{want}",
            )
            if ok:
                logger.info(
                    "CYCLE %s STRUCTURED_EXIT_FLAT tactical pnl≈%.2f resume_cycle=%s "
                    "core_remains=%s",
                    session.cycle,
                    pnl,
                    session.pipeline_resume_cycle,
                    bool(session.core_active),
                )
            else:
                logger.error(
                    "CYCLE %s STRUCTURED_EXIT_FAILED — tactical may remain", session.cycle
                )
            session.last_action = "FLAT"
            return
        if (
            bool(session.core_active)
            and int(session.core_size) > 0
            and str(session.core_side).upper() != want
            and not bool(session.tactical_active)
        ):
            logger.info(
                "CYCLE %s STAND_ASIDE core=%s signal=%s — wait structural invalidation "
                "(no opposite tactical scale)",
                session.cycle,
                session.core_side,
                want,
            )
            session.last_action = "FLAT"
            return

    # Wisdom stand-aside / thesis-invalid → close tactical only; core is structural.
    if decision.action == SignalAction.FLAT:
        if bool(session.tactical_active) and int(session.tactical_size) > 0:
            ok, pnl = await _close_tactical_sleeve(
                session,
                price=price,
                stop_ticks=stop_ticks,
                reason=f"wisdom_flat:{decision.reason}",
            )
            if ok:
                logger.info(
                    "CYCLE %s THESIS_INVALID_FLAT tactical pnl≈%.2f resume_cycle=%s "
                    "core_remains=%s reason=%s",
                    session.cycle,
                    pnl,
                    session.pipeline_resume_cycle,
                    bool(session.core_active),
                    decision.reason,
                )
            else:
                logger.error(
                    "CYCLE %s THESIS_INVALID_FLAT_FAILED — tactical may remain",
                    session.cycle,
                )
        session.last_action = "FLAT"
        return

    # Tactical already in desired direction — hold satellite (core may already be aligned).
    if (
        bool(session.tactical_active)
        and int(session.tactical_size) > 0
        and str(session.tactical_side).upper() == decision.action.value
    ):
        logger.info(
            "CYCLE %s already_tactical_%s x%s — hold satellite (no add)",
            session.cycle,
            session.tactical_side,
            session.tactical_size,
        )
        session.last_action = decision.action.value
        return

    # ============================================================
    # PHASE 3 — ENTRY PIPELINE (Layer-1 cycle lock → velocity → temperance → emit)
    # ============================================================

    # Layer 1 — absolute cycle cooldown from update_outcome_state (no wall clock).
    if not is_entry_pipeline_clear(session, session.cycle):
        session.long_streak = 0
        session.short_streak = 0
        session.last_action = "FLAT"
        return

    # Layer 1b — multi-TP wall-clock lock (after absolute cycle clear).
    clear_to_trade, remaining_s = verify_cooldown_validity(session)
    if not clear_to_trade:
        session.layer1_streak_clear = False
        session.long_streak = 0
        session.short_streak = 0
        logger.info(
            "CYCLE %s LAYER1_multi_tp_cooldown Active streak=%s remaining=%ss — "
            "bypass entry calculations",
            session.cycle,
            session.consecutive_tp_streak,
            remaining_s,
        )
        session.last_action = "FLAT"
        return

    # Temperance: hard daily tactical round-trip cap (counted on close).
    if int(session.trades_today) >= int(VIRTUE_MAX_TACTICAL_TRADES_PER_DAY):
        logger.info(
            "CYCLE %s tactical_day_cap trades_today=%s >= %s — stand aside "
            "(Temperance; no more satellite entries today)",
            session.cycle,
            session.trades_today,
            int(VIRTUE_MAX_TACTICAL_TRADES_PER_DAY),
        )
        session.last_action = "FLAT"
        return

    # Wisdom: freeze tactical satellite in weak ADX (core uses its own structural gate).
    if float(decision.adx) < float(VIRTUE_TACTICAL_ADX_MIN):
        logger.info(
            "CYCLE %s tactical_adx_freeze adx=%.1f < %.1f — no satellite entry "
            "(stand aside micro; core may still manage structurally)",
            session.cycle,
            float(decision.adx),
            float(VIRTUE_TACTICAL_ADX_MIN),
        )
        session.last_action = "FLAT"
        return

    # Temperance: Virtue Balance trailing lock — preserve green-day floor.
    if check_virtue_pnl_lock(session):
        floor = float(session.peak_realized_pnl_today) * float(VIRTUE_PNL_LOCK_FLOOR_FRAC)
        logger.info(
            "CYCLE %s virtue_pnl_lock Active peak=%.2f floor=%.2f realized=%.2f — "
            "no new entries (day shut down)",
            session.cycle,
            session.peak_realized_pnl_today,
            floor,
            session.realized_pnl_today,
        )
        session.last_action = "FLAT"
        return

    # Temperance: hard daily profit lock — day is done; bank the win, no new entries.
    if session.realized_pnl_today >= float(GRADE_DAILY_PROFIT_LOCK):
        logger.info(
            "CYCLE %s daily_profit_lock pnl=%.2f >= %.2f — work done for the day (stand aside)",
            session.cycle,
            session.realized_pnl_today,
            GRADE_DAILY_PROFIT_LOCK,
        )
        session.last_action = "FLAT"
        return

    # Justice: refuse new risk while sleeve books disagree with broker.
    if not bool(session.sleeve_reconcile_ok):
        logger.warning(
            "CYCLE %s entry_blocked %s — stand aside new entries",
            session.cycle,
            session.sleeve_reconcile_detail,
        )
        session.last_action = "FLAT"
        return

    # Temperance windows + Courage extreme override (session still open).
    entries_ok = bool(ignore_hours) or virtue_entries_allowed(
        adx=float(decision.adx),
        blend=float(decision.blended_score),
    )
    if not entries_ok:
        logger.info(
            "CYCLE %s no_new_entry_window allow_new_entries=%s adx=%.1f blend=%.1f — "
            "manage/exit only (entries=09:45-11:30&13:45-15:55ET+extreme)",
            session.cycle,
            bool(session.allow_new_entries),
            float(decision.adx),
            float(decision.blended_score),
        )
        session.last_action = "FLAT"
        return
    if not bool(session.allow_new_entries) and not bool(ignore_hours):
        logger.info(
            "CYCLE %s extreme_trend_time_override adx=%.1f blend=%.1f — allow entry",
            session.cycle,
            float(decision.adx),
            float(decision.blended_score),
        )

    # Wisdom: structure (extreme ADX may waive ATR/spread noise).
    if not bool(session.last_structure_ok):
        logger.info(
            "CYCLE %s entry_structure_blocked %s — stand aside",
            session.cycle,
            session.last_structure_reason,
        )
        session.last_action = "FLAT"
        return

    # After anchor rebase, scores sit near 50 — wait before new entries (Temperance).
    if cooldown_blocks_entry:
        logger.info(
            "CYCLE %s entry_cooldown — stand aside new entries (remaining=%s)",
            session.cycle,
            session.entry_cooldown_cycles,
        )
        session.last_action = "FLAT"
        return

    # Temperance sizing / blend friction from last_trade_outcome (side-aware waiver).
    base_contracts, blend_buffer_raw = calculate_temperance_parameters(session=session)
    side_buf = effective_temperance_blend_buffer(
        blend_buffer_raw,
        side=decision.action.value,
        adx=float(decision.adx),
        macro_bias=str(session.macro_bias or "NEUTRAL"),
    )
    long_enter_thr = float(VIRTUE_SCORE_LONG_ENTER) + effective_temperance_blend_buffer(
        blend_buffer_raw,
        side="LONG",
        adx=float(decision.adx),
        macro_bias=str(session.macro_bias or "NEUTRAL"),
    )
    short_enter_thr = float(VIRTUE_SCORE_SHORT_ENTER) - effective_temperance_blend_buffer(
        blend_buffer_raw,
        side="SHORT",
        adx=float(decision.adx),
        macro_bias=str(session.macro_bias or "NEUTRAL"),
    )
    blend_buffer = float(side_buf)
    if float(blend_buffer_raw) > 0:
        logger.info(
            "CYCLE %s TEMPERANCE_FRICTION losses=%s last_reason=%s "
            "raw_buf=+%.1f effective_buf=+%.1f long_enter>=%.1f short_enter<=%.1f "
            "adx=%.1f bias=%s waived=%s base_contracts=%s",
            session.cycle,
            session.consecutive_losses,
            session.last_reason,
            float(blend_buffer_raw),
            float(blend_buffer),
            long_enter_thr,
            short_enter_thr,
            float(decision.adx),
            session.macro_bias,
            bool(float(blend_buffer) < float(blend_buffer_raw)),
            base_contracts,
        )

    # Require N consecutive raw entry-band cycles before firing (Temperance).
    # Extreme ADX: streak = 1 (Courage — do not miss a finished trend).
    from engine.config import VIRTUE_EXTREME_ADX_OVERRIDE

    need_streak = max(1, int(VIRTUE_REQUIRED_STREAK))
    if int(session.consecutive_losses) >= 1:
        need_streak += max(0, int(VIRTUE_POST_LOSS_EXTRA_STREAK))
    if float(decision.adx) >= float(VIRTUE_EXTREME_ADX_OVERRIDE):
        need_streak = 1
    side = decision.action.value
    if side == "LONG":
        if session.long_streak < need_streak:
            logger.info(
                "CYCLE %s entry_streak LONG %s/%s — wait for confirmation "
                "(loss_friction=%s temperance_buf=%.1f)",
                session.cycle,
                session.long_streak,
                need_streak,
                session.consecutive_losses,
                float(blend_buffer),
            )
            session.last_action = "FLAT"
            return
    elif side == "SHORT":
        if session.short_streak < need_streak:
            logger.info(
                "CYCLE %s entry_streak SHORT %s/%s — wait for confirmation "
                "(loss_friction=%s temperance_buf=%.1f)",
                session.cycle,
                session.short_streak,
                need_streak,
                session.consecutive_losses,
                float(blend_buffer),
            )
            session.last_action = "FLAT"
            return
    else:
        session.last_action = "FLAT"
        return

    # Wisdom: do not chase a move that already extended (late entry → stop / RTH flatten).
    blend = float(decision.blended_score)

    # Pipeline validation — Temperance + ADX velocity gate + bull-day short asymmetry.
    pipe_ok, pipe_reason = verify_pipeline_entry(
        side,
        pipeline_system_state(
            session, blend=blend, adx=float(decision.adx)
        ),
    )
    if not pipe_ok:
        logger.info(
            "CYCLE %s LAYER2_pipeline_entry_denied %s — stand aside",
            session.cycle,
            pipe_reason,
        )
        session.last_action = "FLAT"
        return

    if side == "LONG" and blend >= float(VIRTUE_SCORE_LONG_CHASE_MAX):
        logger.info(
            "CYCLE %s chase_filter LONG blend=%.1f >= %.1f — stand aside (move already extended)",
            session.cycle,
            blend,
            VIRTUE_SCORE_LONG_CHASE_MAX,
        )
        session.last_action = "FLAT"
        return
    if side == "SHORT" and blend <= float(VIRTUE_SCORE_SHORT_CHASE_MIN):
        logger.info(
            "CYCLE %s chase_filter SHORT blend=%.1f <= %.1f — stand aside (move already extended)",
            session.cycle,
            blend,
            VIRTUE_SCORE_SHORT_CHASE_MIN,
        )
        session.last_action = "FLAT"
        return

    # Temperance: after multi-TP streak, require blend pullback before re-entering.
    if session.require_tp_pullback:
        if side == "LONG" and blend > float(VIRTUE_POST_TP_PULLBACK_BLEND_LONG):
            logger.info(
                "CYCLE %s post_tp_pullback LONG blend=%.1f > %.1f — wait for cool-off "
                "(tp_streak=%s)",
                session.cycle,
                blend,
                float(VIRTUE_POST_TP_PULLBACK_BLEND_LONG),
                session.consecutive_tp_streak,
            )
            session.last_action = "FLAT"
            return
        if side == "SHORT" and blend < float(VIRTUE_POST_TP_PULLBACK_BLEND_SHORT):
            logger.info(
                "CYCLE %s post_tp_pullback SHORT blend=%.1f < %.1f — wait for cool-off "
                "(tp_streak=%s)",
                session.cycle,
                blend,
                float(VIRTUE_POST_TP_PULLBACK_BLEND_SHORT),
                session.consecutive_tp_streak,
            )
            session.last_action = "FLAT"
            return
        # Pullback satisfied — clear latch. Keep tp_streak for multi-TP wall-clock lock.
        session.require_tp_pullback = False
        logger.info(
            "CYCLE %s post_tp_pullback_cleared side=%s blend=%.1f tp_streak=%s — "
            "pullback ok (multi-TP lock still applies if streak>=%s)",
            session.cycle,
            side,
            blend,
            session.consecutive_tp_streak,
            int(VIRTUE_MULTI_TP_COOLDOWN_STREAK),
        )

    # Wisdom: shorts need stronger ADX than longs (fewer counter-trend traps).
    if side == "SHORT" and float(decision.adx) < float(VIRTUE_ADX_SHORT_ENTER_MIN):
        logger.info(
            "CYCLE %s short_adx_too_weak adx=%.1f < %.1f — stand aside",
            session.cycle,
            float(decision.adx),
            float(VIRTUE_ADX_SHORT_ENTER_MIN),
        )
        session.last_action = "FLAT"
        return

    # Layer 2b — hard bull-day short extremes (ADX + blend<=35) after pipeline blend gate.
    update_macro_bias(
        session,
        regime=decision.regime.value,
        action=decision.action.value,
        blend=blend,
        adx=float(decision.adx),
    )
    gate_ok, gate_reason = evaluate_directional_gate(
        side,
        macro_bias=session.macro_bias,
        blend=blend,
        adx=float(decision.adx),
    )
    if not gate_ok:
        logger.info(
            "CYCLE %s LAYER2_directional_gate_denied %s — stand aside",
            session.cycle,
            gate_reason,
        )
        session.last_action = "FLAT"
        return

    # Net Exposure Rule — tactical may fire only when aligned with core (or core flat).
    tac_ok, tac_reason = tactical_entry_allowed(
        core_active=bool(session.core_active),
        core_side=str(session.core_side),
        core_size=int(session.core_size or 0),
        tactical_side=side,
        tactical_size=1,
    )
    if not tac_ok:
        logger.info(
            "CYCLE %s DUAL_SLEEVE_ENTRY_BLOCKED %s — stand aside",
            session.cycle,
            tac_reason,
        )
        session.last_action = "FLAT"
        return

    # Exclusivity: never flatten core for an opposite entry (blocked above).
    # If broker net is opposite with no core book, flatten residual before satellite entry.
    net_excl_dir, net_excl_sz = session.broker.net_exposure()
    if (
        net_excl_sz > 0
        and net_excl_dir != side
        and not bool(session.core_active)
    ):
        try:
            exclusive_ok, excl_pnl = await session.broker.flatten_opposite_if_needed(
                desired_direction=side,
                price=price,
                stop_ticks=stop_ticks,
            )
        except Exception as exc:
            logger.exception("exclusivity_check_failed err=%s", exc)
            await _survive_outage(session, "exclusivity_exception")
            session.last_action = "FLAT"
            return
        if not exclusive_ok:
            logger.error(
                "CYCLE %s exclusivity_blocked — opposite flatten not confirmed",
                session.cycle,
            )
            session.last_action = "FLAT"
            return
        if abs(float(excl_pnl or 0.0)) >= 1e-12:
            _credit_tactical_pnl(session, excl_pnl)
            update_outcome_state(
                session,
                excl_pnl,
                "structured_exit:exclusivity",
                current_engine_cycle=session.cycle,
            )
            _mark_tactical_flat(session)

    # Temperance: reject wide spreads before live entry.
    from engine.config import MAX_ALLOWED_SPREAD_TICKS, forward_test_force_paper

    spread_ticks = session.broker.quote_spread_ticks()
    if spread_ticks is not None and spread_ticks > float(MAX_ALLOWED_SPREAD_TICKS):
        logger.warning(
            "CYCLE %s spread_too_wide ticks=%.1f max=%.1f — stand aside",
            session.cycle,
            spread_ticks,
            float(MAX_ALLOWED_SPREAD_TICKS),
        )
        session.last_action = "FLAT"
        return

    # Drag-aware sizing (single risk source of truth with Manus).
    drag_mult = float(session.risk.capital_drag_multiplier)
    size_pct = float(fixed_fractional_risk_pct()) * drag_mult
    sized = session.broker.size_for_direction(
        direction=decision.action.value,
        stop_ticks=stop_ticks,
        risk_limit_pct=size_pct,
    )
    # Paper only: under drag, budget may reject 1 MES — retry undragged for irreducible unit.
    # Live cash: never waive drag (Temperance).
    if (
        forward_test_force_paper()
        and (sized.rejected or sized.contracts < 1)
        and drag_mult < 1.0 - 1e-12
    ):
        sized = session.broker.size_for_direction(
            direction=decision.action.value,
            stop_ticks=stop_ticks,
            risk_limit_pct=float(fixed_fractional_risk_pct()),
        )
        if not sized.rejected and sized.contracts >= 1:
            unit_risk = float(1 * stop_ticks * TICK_VALUE)
            sized = SizeResult(
                contracts=1,
                risk_dollars=unit_risk,
                risk_pct=unit_risk / max(float(session.broker.equity), 1e-9),
                max_allowed_risk=sized.max_allowed_risk,
                rejected=False,
                reason="irreducible_unit_under_drag",
            )
    if sized.rejected or sized.contracts < 1:
        logger.warning("CYCLE %s size_rejected reason=%s", session.cycle, sized.reason)
        session.last_action = "FLAT"
        return

    contracts = min(int(sized.contracts), int(base_contracts))
    from engine.config import MAX_ACCOUNT_CONTRACT_CEILING

    core_occ = int(session.core_size) if bool(session.core_active) else 0
    room = max(0, int(MAX_ACCOUNT_CONTRACT_CEILING) - core_occ)
    contracts = min(contracts, room)
    if contracts < 1:
        logger.warning(
            "CYCLE %s temperance_or_ceiling_cap contracts=%s base=%s room=%s "
            "core=%s ceiling=%s — stand aside",
            session.cycle,
            sized.contracts,
            base_contracts,
            room,
            core_occ,
            int(MAX_ACCOUNT_CONTRACT_CEILING),
        )
        session.last_action = "FLAT"
        return
    # Live exit is dollar stop on the tactical sleeve — Manus risk matches that (Temperance).
    proposed_risk = float(VIRTUE_POSITION_STOP_DOLLARS)
    open_risk = _open_risk_notional(session, stop_ticks)

    # Sync Manus NAV from broker truth (including equity=0 → no fake STARTING_NAV)
    session.risk.update_nav(session.broker.equity)

    try:
        # Forward-test paper: waive floor when FF floor > irreducible 1 MES stop.
        verdict, reason = session.risk.evaluate(
            realized_pnl_today=session.realized_pnl_today,
            open_risk_notional=open_risk,
            proposed_trade_risk=proposed_risk,
            sandbox_fallback=forward_test_force_paper(),
        )
    except Exception as exc:
        logger.exception("manus_evaluate_failed err=%s", exc)
        session.last_action = "FLAT"
        return

    session.last_risk_verdict = verdict.value
    session.last_risk_reason = reason

    if verdict == RiskVerdict.HALT:
        # Hard daily / concurrent: session halt. Sizing band mismatch: cycle stand-aside only.
        soft_halt = (
            "fixed_fractional_floor" in reason
            or "exceeds_fixed_fractional" in reason
        )
        if soft_halt:
            logger.warning(
                "CYCLE %s MANUS_CYCLE_SKIP reason=%s — stand aside this cycle (not session halt)",
                session.cycle,
                reason,
            )
            session.last_action = "FLAT"
            return
        session.halted = True
        logger.error("CYCLE %s MANUS_HALT reason=%s — session halted", session.cycle, reason)
        session.last_action = "FLAT"
        return

    if verdict == RiskVerdict.REDUCE_SIZE:
        if contracts <= 1:
            logger.warning(
                "CYCLE %s MANUS_REDUCE_MIN_LOT reason=%s — keep 1 MES (cannot shrink further)",
                session.cycle,
                reason,
            )
        else:
            contracts = max(1, contracts // 2)
            proposed_risk = float(VIRTUE_POSITION_STOP_DOLLARS)
            logger.warning(
                "CYCLE %s MANUS_REDUCE_SIZE reason=%s contracts=%s risk=%.2f",
                session.cycle,
                reason,
                contracts,
                proposed_risk,
            )

    def _on_tactical_entry(
        sess: VirtueSession,
        *,
        pnl: float,
        reason: str,
        side: str,
        result: Any = None,
        **_: Any,
    ) -> None:
        # trades_today counted only on close (one round-trip = one trade).
        fill_side = str(
            getattr(result, "direction", None) or side or decision.action.value
        )
        notify_position_opened(
            side=fill_side,
            client_order_id=str(getattr(result, "order_id", "") or ""),
            cycle=int(sess.cycle),
            size=int(getattr(result, "contracts", None) or contracts),
        )

    try:
        ok, _, detail = await execute_tactical_action(
            session,
            target_side=decision.action.value,
            target_size=contracts,
            current_cycle=int(session.cycle),
            price=price,
            stop_ticks=stop_ticks,
            reason=f"tactical_entry:{reason}",
            mark_open=_mark_tactical_open,
            on_filled=_on_tactical_entry,
        )
    except Exception as exc:
        logger.exception("fire_order_failed err=%s", exc)
        await _survive_outage(session, "fire_order_exception")
        session.last_action = "FLAT"
        return

    if not ok:
        logger.info(
            "CYCLE %s TACTICAL_ROUTER_ENTRY_BLOCKED detail=%s", session.cycle, detail
        )
        session.last_action = "FLAT"
        return

    session.last_action = decision.action.value
    logger.info(
        "CYCLE %s FILLED/SUBMITTED tactical %s x%s @ %s manus=%s "
        "entry_cycle_marker=%s core_active=%s net_exposure=%s footprint=%s",
        session.cycle,
        session.tactical_side,
        session.tactical_size,
        session.tactical_entry_price,
        reason,
        session.entry_cycle_marker,
        bool(session.core_active),
        calculate_net_account_exposure(session),
        absolute_contract_footprint(session),
    )


async def run_loop(
    *,
    cycles: int | None,
    interval_s: float,
    once: bool,
    ignore_hours: bool,
) -> None:
    session = VirtueSession()
    # Compounded book equity + open paper positions persist across restarts / weekends.
    ledger = load_paper_book(STARTING_NAV)
    book = load_persisted_book_equity(STARTING_NAV)
    session.broker.update_equity(book)
    session.risk.update_nav(book)
    peak = max(float(ledger.get("peak_equity") or book), book)
    session.risk.peak_nav = max(float(session.risk.peak_nav), peak)
    # Seal durable ledger when we have a real restored/migrated book — not a wiped $15k default.
    if bool(ledger.get("restored")) or abs(float(book) - float(STARTING_NAV)) > 0.009:
        save_paper_book(book, peak_equity=peak, source="boot_restore")
    logger.info(
        "BOOT restored_paper_book equity=%.2f peak=%.2f source=%s",
        book,
        peak,
        ledger.get("source") if ledger.get("restored") else "system_state_or_default",
    )
    from engine.config import forward_test_force_paper, live_cash_arming_status

    armed, arm_reason = live_cash_arming_status()
    logger.info(
        "BOOT routing_mode=%s arming=%s detail=%s",
        "PAPER" if forward_test_force_paper() else "LIVE_CANDIDATE",
        "ARMED" if armed else "NOT_ARMED",
        arm_reason,
    )

    # Paper only: restore local ghost positions across restarts.
    # Live cash: never invent size from system_state — Webull reconcile is absolute truth.
    if forward_test_force_paper():
        restored = load_persisted_open_positions()
        if restored:
            session.broker.open_positions = list(restored)
            logger.info(
                "BOOT restored_paper_positions n=%s exposure=%s",
                len(restored),
                session.broker.net_exposure(),
            )
    else:
        session.broker.open_positions = []
        logger.info("BOOT live_mode — cleared local positions pending Webull reconcile")

    # Justice: restore dual-sleeve book tags before day-bucket / sync attribution.
    if restore_dual_sleeve_books(session):
        logger.info(
            "BOOT restored_dual_sleeve core=%s/%s tac=%s/%s regime=%s",
            session.core_side if session.core_active else "FLAT",
            session.core_size if session.core_active else 0,
            session.tactical_side if session.tactical_active else "FLAT",
            session.tactical_size if session.tactical_active else 0,
            session.macro_structural_regime,
        )

    # Justice: same ET day → restore Closed-today PnL / trade count (deploy must not wipe).
    day_bucket = load_persisted_day_bucket(et_session_date())
    if day_bucket.get("restored"):
        session.session_date_et = str(day_bucket["session_date_et"])
        session.realized_pnl_today = float(day_bucket["realized_pnl_today"])
        session.peak_realized_pnl_today = float(
            day_bucket.get("peak_realized_pnl_today")
            or day_bucket["realized_pnl_today"]
            or 0.0
        )
        session.virtue_pnl_lock_active = bool(
            day_bucket.get("virtue_pnl_lock_active") or False
        )
        session.trades_today = int(day_bucket["trades_today"])
        session.consecutive_tp_streak = int(day_bucket.get("consecutive_tp_streak") or 0)
        session.last_tp_timestamp = float(day_bucket.get("last_tp_timestamp") or 0.0)
        session.require_tp_pullback = bool(day_bucket.get("require_tp_pullback") or False)
        session.macro_bias = str(day_bucket.get("macro_bias") or "NEUTRAL")
        session.long_tps_today = int(day_bucket.get("long_tps_today") or 0)
        session.short_tps_today = int(day_bucket.get("short_tps_today") or 0)
        session.last_result = str(day_bucket.get("last_result") or "FLAT")
        session.last_reason = str(day_bucket.get("last_reason") or "none")
        session.consecutive_wins = int(day_bucket.get("consecutive_wins") or 0)
        session.consecutive_losses = int(day_bucket.get("consecutive_losses") or 0)
        session.last_trade_pnl = float(day_bucket.get("last_trade_pnl") or 0.0)
        logger.info(
            "BOOT restored_day_bucket et_date=%s realized_today=%.2f trades_today=%s "
            "tp_streak=%s last_tp=%.0f pullback=%s macro_bias=%s long_tps=%s short_tps=%s "
            "outcome=%s wins=%s losses=%s",
            session.session_date_et,
            session.realized_pnl_today,
            session.trades_today,
            session.consecutive_tp_streak,
            session.last_tp_timestamp,
            session.require_tp_pullback,
            session.macro_bias,
            session.long_tps_today,
            session.short_tps_today,
            session.last_result,
            session.consecutive_wins,
            session.consecutive_losses,
        )
    logger.info(
        "VIRTUE LOOP start equity=%.2f symbol=%s session_mode=%s data_source=%s "
        "position_tp=$%.0f position_sl=$%.0f day_lock=$%.0f "
        "tp_cool=%s cc_cool=%s stop_cool=%s "
        "long_enter=%.1f short_enter=%.1f long_exit=%.1f short_exit=%.1f "
        "streak=%s anchor_div_atr=%.1f network_timeout=%.1fs | %s",
        session.broker.equity,
        EXECUTION_SYMBOL,
        virtue_session_mode().upper(),
        primary_data_source(),
        float(VIRTUE_POSITION_TP_DOLLARS),
        float(VIRTUE_POSITION_STOP_DOLLARS),
        float(GRADE_DAILY_PROFIT_LOCK),
        int(VIRTUE_BASE_TP_COOLDOWN_CYCLES),
        int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES),
        int(VIRTUE_HARD_STOP_COOLDOWN_CYCLES),
        float(VIRTUE_SCORE_LONG_ENTER),
        float(VIRTUE_SCORE_SHORT_ENTER),
        float(VIRTUE_SCORE_LONG_EXIT),
        float(VIRTUE_SCORE_SHORT_EXIT),
        int(VIRTUE_REQUIRED_STREAK),
        float(VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT),
        NETWORK_TIMEOUT_S,
        virtue_session_label(),
    )

    # Boot reconcile (Webull absolute truth — including equity=0)
    try:
        truth = await session.broker.reconcile_with_broker()
        if truth.ok:
            # Paper reconcile preserves book equity; live uses Webull mark.
            session.risk.update_nav(session.broker.equity)
            session.risk.peak_nav = max(float(session.risk.peak_nav), float(session.broker.equity))
            # Day bucket starts at 0 for this ET date — never import cumulative broker PnL.
            roll_daily_counters_if_needed(session)
            if session.broker.equity <= 0:
                logger.error(
                    "BOOT zero_futures_equity — Virtue will stand aside on size until account is funded "
                    "(or enable FORWARD_TEST_MODE paper NAV / Webull sandbox keys)"
                )
            elif truth.detail == "forward_test_paper_nav":
                logger.info(
                    "BOOT forward_test_paper_nav equity=%.2f — local paper fills (not Webull app sandbox)",
                    session.broker.equity,
                )
        else:
            logger.error("BOOT reconcile_failed detail=%s", truth.detail)
            if not forward_test_force_paper():
                # Live cash: halt rather than trade on an unknown book (Temperance / Justice).
                session.halted = True
                logger.error(
                    "BOOT LIVE HALTED — reconcile failed; refusing entries/flatten-from-ghosts until Webull truth returns"
                )
    except Exception as exc:
        logger.exception("boot_reconcile_failed err=%s", exc)
        if not forward_test_force_paper():
            session.halted = True
            logger.error("BOOT LIVE HALTED — reconcile exception")

    # Seed Wisdom from Databento MES (preferred) or Alpaca SPY proxy history
    try:
        await seed_wisdom_from_market(session)
    except Exception as exc:
        logger.exception("boot_seed_failed err=%s", exc)

    session.is_running = True
    await asyncio.to_thread(_publish_ui, session)
    tick_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    poll_s = max(0.5, float(interval_s if interval_s is not None else VIRTUE_TICK_POLL_S))
    logger.info(
        "EVENT_ENGINE start listener+processor+state_writer poll_s=%.1fs "
        "heartbeat_every=%s rebase_cooldown=%s state=data/system_state.json persist_every=%.1fs",
        poll_s,
        int(VIRTUE_HEARTBEAT_EVERY_N_CYCLES),
        int(VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES),
        float(VIRTUE_STATE_PERSIST_INTERVAL_S),
    )

    listener = asyncio.create_task(
        market_tick_listener(session, tick_queue, poll_s=poll_s),
        name="virtue_market_listener",
    )
    processor = asyncio.create_task(
        engine_event_loop(
            session,
            tick_queue,
            cycles=cycles,
            once=once,
            ignore_hours=ignore_hours,
        ),
        name="virtue_engine_processor",
    )
    saver = asyncio.create_task(
        save_state_throttled(session),
        name="virtue_state_writer",
    )

    n = 0
    try:
        # Verified run.py architecture: market events ∥ state writer (Justice off hot path).
        results = await asyncio.gather(listener, processor, saver, return_exceptions=True)
        for label, result in zip(("listener", "processor", "saver"), results):
            if isinstance(result, Exception):
                logger.error("EVENT_ENGINE %s failed: %s", label, result)
        if isinstance(results[1], int):
            n = int(results[1])
        elif not isinstance(results[1], Exception):
            n = int(session.cycle)
    finally:
        session.is_running = False
        for task in (listener, processor, saver):
            if not task.done():
                task.cancel()
        await asyncio.gather(listener, processor, saver, return_exceptions=True)
        try:
            await asyncio.to_thread(_publish_ui, session)
        except Exception as exc:
            logger.error("Justice Layer final write failure (system_state.json): %s", exc)

    logger.info("VIRTUE LOOP done cycles=%s last_action=%s", n, session.last_action)


def main() -> None:
    parser = argparse.ArgumentParser(description="FutureMathics virtue main loop (native Wisdom brain)")
    parser.add_argument("--cycles", type=int, default=None, help="Stop after N cycles")
    parser.add_argument(
        "--interval",
        type=float,
        default=float(VIRTUE_TICK_POLL_S),
        help=f"Market tick poll cadence in seconds (default {VIRTUE_TICK_POLL_S}; event-driven engine)",
    )
    parser.add_argument("--once", action="store_true", help="Single cycle then exit")
    parser.add_argument(
        "--ignore-hours",
        action="store_true",
        help="Run outside CME MES hours (paper/testing only — forbidden when live)",
    )
    args = parser.parse_args()
    from engine.config import forward_test_force_paper, live_cash_arming_status

    if args.ignore_hours and not forward_test_force_paper():
        raise SystemExit(
            "REFUSE: --ignore-hours is forbidden in live cash mode. "
            "Keep FM_FORWARD_TEST_MODE paper or unset --ignore-hours."
        )
    armed, arm_reason = live_cash_arming_status()
    if not forward_test_force_paper() and not armed:
        raise SystemExit(
            f"REFUSE: live candidate but NOT_ARMED ({arm_reason}). "
            "Fix preflight gates or set FM_FORWARD_TEST_MODE=1 for paper."
        )
    asyncio.run(
        run_loop(
            cycles=args.cycles,
            interval_s=args.interval,
            once=args.once,
            ignore_hours=args.ignore_hours,
        )
    )


if __name__ == "__main__":
    main()
