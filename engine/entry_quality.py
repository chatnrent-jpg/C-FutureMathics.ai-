"""
FutureMathics Institutional Entry Quality Grading.

Grades setups A+ through F based on:
1. Edge (VWAP+TWAP both agree)
2. Structure (ATR expanding OR ADX rising)
3. Momentum (ADX above regime floor)

Institutional standard: Only trade A+ or A setups.
B/C/D/F = stand aside.
"""

from __future__ import annotations

from engine.regime_engine import MarketRegime


def evaluate_entry_quality(
    *,
    regime: MarketRegime,
    adx: float,
    prev_adx: float,
    atr: float,
    prev_atr: float,
    blend: float,
    vwap_score: float,
    twap_score: float,
    signal_side: str,  # "LONG" or "SHORT"
) -> tuple[bool, str, str]:
    """
    Grade entry quality from A+ (best) to F (worst).

    Returns: (allowed, reason, grade)
    - allowed: True if grade A+ or A, False otherwise
    - reason: Human-readable explanation
    - grade: "A+", "A", "B", "C", "D", or "F"

    Grading rubric:
    - A+: Edge + Structure + Momentum all aligned (3/3 factors)
    - A:  Edge + (Structure OR Momentum) (2/3 factors)
    - B:  Edge only, skip unless desperate (1/3 factors)
    - C:  Edge weak, some other factors present
    - D:  Edge weak, no other factors
    - F:  CHAOS regime or completely invalid
    """
    from engine.config import VIRTUE_SCORE_LONG_ENTER, VIRTUE_SCORE_SHORT_ENTER

    try:
        from engine.config import (
            INSTITUTIONAL_ADX_TREND_MIN,
            INSTITUTIONAL_ADX_RANGE_MIN,
        )
    except ImportError:
        INSTITUTIONAL_ADX_TREND_MIN = 22.0
        INSTITUTIONAL_ADX_RANGE_MIN = 12.0

    adx_val = float(adx or 0.0)
    prev_adx_val = float(prev_adx or 0.0)
    atr_val = float(atr or 0.0)
    prev_atr_val = float(prev_atr or 0.0)
    blend_val = float(blend or 50.0)
    vwap_val = float(vwap_score or 50.0)
    twap_val = float(twap_score or 50.0)
    side = str(signal_side or "").upper()

    # ============================================================
    # F Grade: CHAOS regime or invalid signal
    # ============================================================
    if regime == MarketRegime.CHAOS_STAND_ASIDE:
        return False, "CHAOS regime — no entries (grade F)", "F"

    if side not in {"LONG", "SHORT"}:
        return False, f"Invalid signal side '{side}' (grade F)", "F"

    # ============================================================
    # Check EDGE (VWAP+TWAP agreement)
    # ============================================================
    if side == "LONG":
        long_enter = float(VIRTUE_SCORE_LONG_ENTER)
        edge_strong = vwap_val >= long_enter and twap_val >= long_enter
        edge_partial = vwap_val >= long_enter or twap_val >= long_enter
    else:  # SHORT
        short_enter = float(VIRTUE_SCORE_SHORT_ENTER)
        edge_strong = vwap_val <= short_enter and twap_val <= short_enter
        edge_partial = vwap_val <= short_enter or twap_val <= short_enter

    if not edge_strong:
        if not edge_partial:
            # No edge at all
            return (
                False,
                f"Edge absent — VWAP {vwap_val:.1f} TWAP {twap_val:.1f} "
                f"(need both {'≥' if side == 'LONG' else '≤'} "
                f"{long_enter if side == 'LONG' else short_enter:.0f}) grade D",
                "D",
            )
        else:
            # Partial edge (one anchor agrees, other doesn't)
            return (
                False,
                f"Edge weak — VWAP {vwap_val:.1f} TWAP {twap_val:.1f} "
                f"(only one anchor agrees) grade C",
                "C",
            )

    # Edge is strong (both VWAP+TWAP agree) — now check structure + momentum

    # ============================================================
    # Check STRUCTURE (ATR expanding OR ADX rising)
    # ============================================================
    atr_expanding = atr_val > prev_atr_val
    adx_rising = adx_val > prev_adx_val
    structure_ok = atr_expanding or adx_rising

    # ============================================================
    # Check MOMENTUM (ADX above regime floor)
    # ============================================================
    if regime in {MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR}:
        momentum_floor = INSTITUTIONAL_ADX_TREND_MIN
    else:  # RANGE
        momentum_floor = INSTITUTIONAL_ADX_RANGE_MIN

    momentum_ok = adx_val >= momentum_floor

    # ============================================================
    # GRADE: Count factors (edge already confirmed strong)
    # ============================================================
    factors_present = sum([True, structure_ok, momentum_ok])  # Edge=True always here

    if factors_present == 3:
        # A+: All 3 factors aligned
        detail = []
        if atr_expanding:
            detail.append(f"ATR↑{atr_val:.4f}>{prev_atr_val:.4f}")
        if adx_rising:
            detail.append(f"ADX↑{adx_val:.1f}>{prev_adx_val:.1f}")
        detail.append(f"ADX{adx_val:.1f}≥{momentum_floor:.0f}")
        return (
            True,
            f"A+ setup — edge + structure + momentum ({', '.join(detail)})",
            "A+",
        )

    if factors_present == 2:
        # A: Edge + one other factor
        if structure_ok and momentum_ok:
            # Both structure + momentum, but not all sub-factors
            return (
                True,
                f"A setup — edge + momentum (ADX {adx_val:.1f}) + structure "
                f"({'ATR↑' if atr_expanding else 'ADX↑'})",
                "A",
            )
        elif structure_ok:
            return (
                True,
                f"A setup — edge + structure ({'ATR↑' if atr_expanding else 'ADX↑'}) "
                f"momentum adequate",
                "A",
            )
        else:  # momentum_ok only
            return (
                True,
                f"A setup — edge + momentum (ADX {adx_val:.1f}≥{momentum_floor:.0f}) "
                f"structure neutral",
                "A",
            )

    # Only 1 factor (edge alone) = B grade
    return (
        False,
        f"B setup — edge strong but no structure (ATR/ADX flat) or momentum "
        f"(ADX {adx_val:.1f}<{momentum_floor:.0f}) — marginal quality",
        "B",
    )


def is_institutional_grade(grade: str) -> bool:
    """True if grade meets institutional standard (A+ or A)."""
    return grade in {"A+", "A"}


__all__ = [
    "evaluate_entry_quality",
    "is_institutional_grade",
]
