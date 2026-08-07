"""
FutureMathics Institutional Layered Exit Strategy.

6 exit layers (checked in order, first trigger wins):
1. Hard stop ($75 max loss)
2. Time decay (stagnant holds, regime-dependent)
3. Thesis break (VWAP/TWAP bands reverse)
4. Regime change (trend → chaos, exit immediately)
5. Target profit (regime-dependent: $80 range, $125 trend)
6. Trailing stop (trend mode only, after $50 profit)

Institutional algos exit with precision, not hope.
"""

from __future__ import annotations

from engine.regime_engine import MarketRegime, regime_is_trending, regime_is_range


def evaluate_institutional_exits(
    *,
    regime: MarketRegime,
    holding: str,
    entry_price: float,
    current_price: float,
    open_pnl: float,
    cycles_held: int,
    adx: float,
    blend: float,
    peak_pnl: float | None = None,
) -> tuple[bool, str, str]:
    """
    Evaluate all 6 exit layers and return first trigger.

    Returns: (should_exit, reason, layer)
    - should_exit: True if any layer triggers
    - reason: Human-readable exit reason
    - layer: Which layer triggered (L1-L6) for analytics

    Layers checked in priority order (safety → profit):
    L1: Hard stop (always enforced)
    L2: Time decay (regime-dependent max hold cycles)
    L3: Thesis break (bands reverse direction)
    L4: Regime change (entered trend, now chaos → exit)
    L5: Target profit (regime-dependent TP)
    L6: Trailing stop (trend mode only, 50% retrace from peak)
    """
    from engine.config import (
        VIRTUE_POSITION_STOP_DOLLARS,
        VIRTUE_SCORE_LONG_EXIT,
        VIRTUE_SCORE_SHORT_EXIT,
    )

    try:
        from engine.config import (
            INSTITUTIONAL_RANGE_TP_QUICK,
            INSTITUTIONAL_TREND_TP_TRAIL,
            INSTITUTIONAL_TREND_HOLD_CYCLES,
        )
    except ImportError:
        INSTITUTIONAL_RANGE_TP_QUICK = 80.0
        INSTITUTIONAL_TREND_TP_TRAIL = True
        INSTITUTIONAL_TREND_HOLD_CYCLES = 24

    pnl = float(open_pnl or 0.0)
    cycles = int(cycles_held or 0)
    blend_val = float(blend or 50.0)
    side = str(holding or "").upper()

    if side not in {"LONG", "SHORT"}:
        return False, "No position to exit", "NONE"

    # ============================================================
    # Layer 1: HARD STOP (highest priority)
    # ============================================================
    stop_dollars = float(VIRTUE_POSITION_STOP_DOLLARS)
    if pnl <= -stop_dollars:
        return True, f"L1_HARD_STOP | PnL ${pnl:.0f} ≤ -${stop_dollars:.0f}", "L1"

    # ============================================================
    # Layer 2: TIME DECAY (regime-dependent max hold)
    # ============================================================
    if regime_is_range(regime):
        # Range mode: quick in/out (18 cycles ≈ 15 minutes)
        max_cycles_range = 18
        if cycles >= max_cycles_range and pnl <= 10.0:
            return True, (
                f"L2_TIME_DECAY | Range mode held {cycles} ≥ {max_cycles_range} cycles "
                f"with PnL ${pnl:.0f} ≤ $10 (cut stagnant)"
            ), "L2"

    if regime_is_trending(regime):
        # Trend mode: hold longer for runners (48 cycles ≈ 40 minutes)
        max_cycles_trend = int(INSTITUTIONAL_TREND_HOLD_CYCLES)
        if cycles >= max_cycles_trend and pnl <= 0.0:
            return True, (
                f"L2_TIME_DECAY | Trend mode held {cycles} ≥ {max_cycles_trend} cycles "
                f"with PnL ${pnl:.0f} ≤ 0 (cut dead trend)"
            ), "L2"

    # ============================================================
    # Layer 3: THESIS BREAK (bands reverse)
    # ============================================================
    long_exit_band = float(VIRTUE_SCORE_LONG_EXIT)
    short_exit_band = float(VIRTUE_SCORE_SHORT_EXIT)

    if side == "LONG" and blend_val <= long_exit_band:
        return True, (
            f"L3_THESIS_BREAK | LONG thesis broken — "
            f"blend {blend_val:.1f} ≤ {long_exit_band:.0f} (bands reversed)"
        ), "L3"

    if side == "SHORT" and blend_val >= short_exit_band:
        return True, (
            f"L3_THESIS_BREAK | SHORT thesis broken — "
            f"blend {blend_val:.1f} ≥ {short_exit_band:.0f} (bands reversed)"
        ), "L3"

    # ============================================================
    # Layer 4: REGIME CHANGE (entered in TREND/RANGE, now CHAOS)
    # ============================================================
    if regime == MarketRegime.CHAOS_STAND_ASIDE:
        return True, (
            f"L4_REGIME_CHANGE | Market entered CHAOS — "
            f"flatten and stand aside (volatility/drift too high)"
        ), "L4"

    # ============================================================
    # Layer 5: TARGET PROFIT (regime-dependent)
    # ============================================================
    if regime_is_range(regime):
        # Range mode: quick TP ($80) — scalp the mean-reversion
        tp_range = float(INSTITUTIONAL_RANGE_TP_QUICK)
        if pnl >= tp_range:
            return True, (
                f"L5_TARGET_PROFIT | Range mode TP ${pnl:.0f} ≥ ${tp_range:.0f} "
                f"(quick scalp)"
            ), "L5"

    if regime_is_trending(regime):
        # Trend mode: larger TP ($125) — let winners run
        tp_trend = 125.0
        if pnl >= tp_trend:
            return True, (
                f"L5_TARGET_PROFIT | Trend mode TP ${pnl:.0f} ≥ ${tp_trend:.0f} "
                f"(runner captured)"
            ), "L5"

    # ============================================================
    # Layer 6: TRAILING STOP (trend mode only, after $50 profit)
    # ============================================================
    if regime_is_trending(regime) and bool(INSTITUTIONAL_TREND_TP_TRAIL):
        if pnl >= 50.0:
            # Trail by 50% of peak profit (protect 50% of gains)
            peak = float(peak_pnl or pnl)
            trail_threshold = peak * 0.5

            if pnl <= trail_threshold:
                return True, (
                    f"L6_TRAILING_STOP | Trailed from peak ${peak:.0f} to ${pnl:.0f} "
                    f"(≤50% threshold ${trail_threshold:.0f}, protect gains)"
                ), "L6"

    # ============================================================
    # No exit triggered: HOLD
    # ============================================================
    return False, f"HOLD — all exit layers cleared (PnL ${pnl:.0f}, cycles {cycles})", "HOLD"


def update_peak_pnl(current_pnl: float, previous_peak: float | None = None) -> float:
    """
    Update peak PnL for trailing stop calculation.

    Returns: max(current_pnl, previous_peak)
    """
    current = float(current_pnl or 0.0)
    peak = float(previous_peak or 0.0)
    return max(current, peak)


def calculate_regime_specific_tp(regime: MarketRegime) -> float:
    """
    Return regime-specific take-profit target.

    Range mode: $80 (quick scalp)
    Trend mode: $125 (let runners breathe)
    Chaos: N/A (should not be in position)
    """
    try:
        from engine.config import INSTITUTIONAL_RANGE_TP_QUICK
    except ImportError:
        INSTITUTIONAL_RANGE_TP_QUICK = 80.0

    if regime_is_range(regime):
        return float(INSTITUTIONAL_RANGE_TP_QUICK)
    elif regime_is_trending(regime):
        return 125.0
    else:
        return 100.0  # Fallback (should not reach here in CHAOS)


__all__ = [
    "evaluate_institutional_exits",
    "update_peak_pnl",
    "calculate_regime_specific_tp",
]
