"""
FutureMathics Institutional Adaptive Position Sizing.

Size dynamically based on:
1. Regime confidence (lower confidence = smaller size or zero)
2. Entry grade (only A+ and A setups get sized)
3. Recent performance (after 2+ losses, reset to zero until clean setup)
4. Daily PnL (capital preservation mode below -$200)

Base size = 1 MES
Scale UP = not yet (1 MES max for now)
Scale DOWN = to zero in unfavorable conditions
"""

from __future__ import annotations

from engine.regime_engine import MarketRegime


def calculate_institutional_size(
    *,
    regime: MarketRegime,
    regime_confidence: float,
    entry_grade: str,
    consecutive_losses: int,
    consecutive_wins: int,
    daily_pnl: float,
    nav: float,
) -> tuple[int, str]:
    """
    Institutional adaptive sizing with multiple safety filters.

    Returns: (contracts, reason)
    - contracts: 0 or 1 (base size, never scales up beyond 1 for now)
    - reason: Human-readable explanation of sizing decision

    Sizing gates (in order):
    1. CHAOS regime → 0
    2. 2+ consecutive losses → 0 (reset mode)
    3. Entry grade < A → 0 (institutional standard)
    4. Regime confidence < 60% → 0 (low conviction)
    5. Daily PnL ≤ -$200 → 0 (capital preservation)
    6. All clear → 1 (full size)
    """
    base_size = 1

    # ============================================================
    # Gate 1: CHAOS regime (always zero)
    # ============================================================
    if regime == MarketRegime.CHAOS_STAND_ASIDE:
        return 0, f"CHAOS regime — stand aside (size 0)"

    # ============================================================
    # Gate 2: Reset mode after 2+ consecutive losses
    # ============================================================
    if int(consecutive_losses) >= 2:
        return 0, f"RESET mode — {consecutive_losses} consecutive losses, stand aside until clean A+ setup"

    # ============================================================
    # Gate 3: Entry grade filter (institutional standard A+ or A only)
    # ============================================================
    grade = str(entry_grade or "F").upper()
    if grade not in {"A+", "A"}:
        return 0, f"Setup grade {grade} — institutional standard requires A+ or A (size 0)"

    # ============================================================
    # Gate 4: Regime confidence filter
    # ============================================================
    confidence = float(regime_confidence or 0.0)
    if confidence < 60.0:
        return 0, f"Regime confidence {confidence:.0f}% < 60% — low conviction (size 0)"

    # ============================================================
    # Gate 5: Daily PnL capital preservation
    # ============================================================
    pnl = float(daily_pnl or 0.0)
    if pnl <= -200.0:
        return 0, f"Daily PnL ${pnl:.0f} ≤ -$200 — capital preservation mode (size 0)"

    # ============================================================
    # All gates passed: FULL SIZE
    # ============================================================
    regime_name = regime.value if hasattr(regime, 'value') else str(regime)
    return base_size, (
        f"FULL SIZE — {regime_name} grade {grade} "
        f"confidence {confidence:.0f}% (1 MES)"
    )


def should_cool_off_after_loss(
    *,
    consecutive_losses: int,
    last_reason: str,
) -> tuple[bool, int]:
    """
    Determine if system should cool off after loss (skip 1-2 cycles).

    Returns: (should_cool_off, cycles_to_skip)
    - After 1 loss: cool off 1-2 cycles depending on reason
    - After 2+ losses: handled by calculate_institutional_size (zero size)

    Cool-off prevents revenge trading and lets market settle.
    """
    losses = int(consecutive_losses or 0)

    if losses == 0:
        return False, 0

    if losses == 1:
        # After 1 loss: brief cool-off (1-2 cycles = 5-10 minutes)
        reason = str(last_reason or "").lower()
        if "stop" in reason or "thesis" in reason:
            # Hard stop or thesis break: cool off 2 cycles
            return True, 2
        else:
            # Other loss (time decay, etc.): cool off 1 cycle
            return True, 1

    # 2+ losses handled by zero sizing in calculate_institutional_size
    return False, 0


def post_win_sizing_boost() -> tuple[int, str]:
    """
    After a win, return to full size immediately (Courage).

    Institutional algos don't hesitate after wins — capitalize on momentum.
    """
    return 1, "Post-win — full size immediately (Courage)"


def is_reset_mode(consecutive_losses: int) -> bool:
    """True if in reset mode (2+ consecutive losses, zero size until clean setup)."""
    return int(consecutive_losses or 0) >= 2


__all__ = [
    "calculate_institutional_size",
    "should_cool_off_after_loss",
    "post_win_sizing_boost",
    "is_reset_mode",
]
