"""
MacroMathics production core — phase contract for the virtue execution loop.

PHASE 1 — Financial protection (Profit Guard / Virtue Balance)
PHASE 2 — In-flight escapes (time-decay, course-correct, stop, TP)
PHASE 3 — Entry pipeline (Layer-1 cycle lock → velocity gate → temperance → emit)

Live async broker I/O stays in main.run_cycle; this module is the clean
decision surface + config re-exports for tests and documentation.
"""

from __future__ import annotations

from typing import Any

from engine.config import (
    MAX_STAGNATION_CYCLES,
    PROFIT_GUARD_RETAIN_PCT,
    PROFIT_GUARD_THRESHOLD,
    VIRTUE_BASE_TP_COOLDOWN_CYCLES,
    VIRTUE_HARD_STOP_COOLDOWN_CYCLES,
    VIRTUE_PIPELINE_BULL_SHORT_PENALTY,
    VIRTUE_PIPELINE_LONG_BLEND_BASE,
    VIRTUE_PIPELINE_SHORT_BLEND_BASE,
    VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES,
    VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES,
    VIRTUE_TIME_DECAY_COOLDOWN_CYCLES,
    VIRTUE_TIME_DECAY_MIN_OPEN_PNL,
    VIRTUE_VELOCITY_ADX_FLOOR,
    VIRTUE_VELOCITY_PENALTY_PER_ADX,
)

# --- ENGINE PERFORMANCE CONFIGURATIONS (production template) ---
__all__ = [
    "VIRTUE_BASE_TP_COOLDOWN_CYCLES",
    "VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES",
    "VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES",
    "VIRTUE_HARD_STOP_COOLDOWN_CYCLES",
    "VIRTUE_TIME_DECAY_COOLDOWN_CYCLES",
    "MAX_STAGNATION_CYCLES",
    "PROFIT_GUARD_THRESHOLD",
    "PROFIT_GUARD_RETAIN_PCT",
    "phase1_profit_guard_triggered",
    "phase2_time_decay_triggered",
    "phase3_velocity_gates",
    "phase3_layer1_clear",
    "cooldown_cycles_for_reason",
]


def phase1_profit_guard_triggered(session_state: dict[str, Any]) -> tuple[bool, float]:
    """
    PHASE 1 — trailing daily profit floor.

    Returns (triggered, floor_lock). Updates peak_realized_pnl_today in-place.
    """
    realized = float(session_state.get("realized_pnl_today", 0.0) or 0.0)
    peak = float(session_state.get("peak_realized_pnl_today", 0.0) or 0.0)
    if realized > peak:
        session_state["peak_realized_pnl_today"] = realized
        peak = realized
    if bool(session_state.get("virtue_pnl_lock_active")):
        floor = peak * float(PROFIT_GUARD_RETAIN_PCT) if peak > 0 else 0.0
        return True, floor
    if peak >= float(PROFIT_GUARD_THRESHOLD):
        floor_lock = peak * float(PROFIT_GUARD_RETAIN_PCT)
        if realized <= floor_lock + 1e-9:
            session_state["virtue_pnl_lock_active"] = True
            session_state["Regime"] = "CHOP_NO_TRADE"
            session_state["regime"] = "CHOP_NO_TRADE"
            return True, floor_lock
        return False, floor_lock
    return False, 0.0


def phase2_time_decay_triggered(
    session_state: dict[str, Any],
    current_cycle: int,
) -> bool:
    """
    PHASE 2 — stagnation failsafe.

    Marks entry_cycle_marker on first exposure. Triggers when elapsed >=
    MAX_STAGNATION_CYCLES and open_pnl <= VIRTUE_TIME_DECAY_MIN_OPEN_PNL.
    """
    exposure = str(session_state.get("engine_exposure", "FLAT") or "FLAT").upper()
    if exposure == "FLAT":
        session_state["entry_cycle_marker"] = None
        return False
    if session_state.get("entry_cycle_marker") is None:
        session_state["entry_cycle_marker"] = int(current_cycle)
        return False
    elapsed = int(current_cycle) - int(session_state["entry_cycle_marker"])
    open_pnl = float(session_state.get("open_pnl", 0.0) or 0.0)
    return (
        elapsed >= int(MAX_STAGNATION_CYCLES)
        and open_pnl <= float(VIRTUE_TIME_DECAY_MIN_OPEN_PNL)
    )


def phase3_velocity_gates(
    session_state: dict[str, Any] | None = None,
    *,
    adx: float | None = None,
) -> tuple[float, float]:
    """
    PHASE 3 — Breakout Velocity Gate.

    Widens long/short blend bases when ADX < floor.
    """
    state = session_state if isinstance(session_state, dict) else {}
    try:
        adx_val = float(
            adx if adx is not None else state.get("ADX", state.get("adx", 14.0)) or 14.0
        )
    except (TypeError, ValueError):
        adx_val = 14.0
    if adx_val <= 0:
        adx_val = 14.0
    long_gate = float(VIRTUE_PIPELINE_LONG_BLEND_BASE)
    short_gate = float(VIRTUE_PIPELINE_SHORT_BLEND_BASE)
    floor = float(VIRTUE_VELOCITY_ADX_FLOOR)
    if adx_val < floor:
        penalty = (floor - adx_val) * float(VIRTUE_VELOCITY_PENALTY_PER_ADX)
        long_gate += penalty
        short_gate -= penalty
    return long_gate, short_gate


def phase3_layer1_clear(
    session_state: dict[str, Any],
    current_cycle: int,
) -> bool:
    """PHASE 3 Layer-1 — absolute pipeline_resume_cycle lock."""
    resume = int(session_state.get("pipeline_resume_cycle", 0) or 0)
    pipeline = session_state.setdefault("entry_pipeline", {})
    if resume > 0 and int(current_cycle) < resume:
        remaining = resume - int(current_cycle)
        pipeline["layer1_streak_clear"] = False
        session_state["multi_tp_cooldown_active"] = True
        session_state["multi_tp_cooldown_remaining_s"] = remaining
        return False
    pipeline["layer1_streak_clear"] = True
    session_state["multi_tp_cooldown_active"] = False
    session_state["multi_tp_cooldown_remaining_s"] = 0
    return True


def phase3_short_gate_with_bias(
    short_gate: float,
    macro_bias: str,
) -> float:
    """Asymmetric bull-day short penalty on top of velocity gate."""
    bias = (macro_bias or "NEUTRAL").upper()
    if bias == "BULL":
        return float(short_gate) - float(VIRTUE_PIPELINE_BULL_SHORT_PENALTY)
    return float(short_gate)


def cooldown_cycles_for_reason(reason: str, *, tp_streak: int = 0) -> int:
    """Map exit reason → absolute-cycle cooldown length (production template)."""
    r = (reason or "").strip().lower()
    if "take_profit" in r:
        return int(VIRTUE_BASE_TP_COOLDOWN_CYCLES) + (
            max(0, int(tp_streak)) * int(VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES)
        )
    if "course_correct" in r:
        return int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES)
    if r.startswith("stop") or r == "stop":
        return int(VIRTUE_HARD_STOP_COOLDOWN_CYCLES)
    if "time_decay" in r:
        return int(VIRTUE_TIME_DECAY_COOLDOWN_CYCLES)
    if "profit_guard" in r or "virtue_pnl_lock" in r:
        return int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES)
    return 3
