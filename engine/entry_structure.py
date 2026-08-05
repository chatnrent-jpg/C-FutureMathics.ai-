"""
Entry structure gates — ATR expanding, ADX rising through floor, VWAP/TWAP spread widening.

Timezone-agnostic pure math; callers pass America/New_York wall times via session gates.
"""

from __future__ import annotations

from typing import Any

from engine.config import VIRTUE_ENTRY_ADX_MIN, VIRTUE_STRUCTURE_EXTREME_ADX


def anchor_spread_pct(*, vwap: float, twap: float, price: float) -> float:
    """
    Structure width as percent of price.

    Uses max(|VWAP−TWAP|, |price − mid_anchor|) so equal-weight twin anchors
    (Alpaca proxy) still register displacement from the mean as widening.
    """
    try:
        px = float(price)
        if px <= 0:
            return 0.0
        v = float(vwap)
        t = float(twap)
        twin = abs(v - t) / px * 100.0
        mid = (v + t) / 2.0 if (v > 0 and t > 0) else (v or t)
        if mid <= 0:
            return twin
        displace = abs(px - mid) / px * 100.0
        return max(twin, displace)
    except (TypeError, ValueError):
        return 0.0


def evaluate_entry_structure_gates(
    session: Any,
    *,
    atr: float,
    adx: float,
    vwap: float,
    twap: float,
    price: float,
    adx_min: float | None = None,
) -> tuple[bool, str]:
    """
    Trades initiate when:
      1) ADX > floor and rising vs prior cycle
      2) ATR expanding AND spread widening — OR extreme ADX waiver

    Extreme ADX (>= VIRTUE_STRUCTURE_EXTREME_ADX) + rising: waive ATR/spread
    so a finished trend day is not starved by proxy microstructure noise.
    Justice: missing prior samples → stand aside (no trade on warm-up).
    """
    floor = float(adx_min if adx_min is not None else VIRTUE_ENTRY_ADX_MIN)
    extreme = float(VIRTUE_STRUCTURE_EXTREME_ADX)
    try:
        atr_now = float(atr or 0.0)
        adx_now = float(adx or 0.0)
    except (TypeError, ValueError):
        return False, "entry_structure:invalid_atr_adx"

    prev_atr = float(getattr(session, "prev_atr", 0.0) or 0.0)
    prev_adx = float(getattr(session, "prev_adx", 0.0) or 0.0)
    prev_spread = float(getattr(session, "prev_anchor_spread_pct", 0.0) or 0.0)
    spread_now = anchor_spread_pct(vwap=vwap, twap=twap, price=price)

    if prev_atr <= 0.0 or prev_adx <= 0.0:
        return False, "entry_structure:warmup_no_prior"

    if adx_now <= floor:
        return False, f"entry_structure:adx_weak adx={adx_now:.1f}<={floor:.1f}"
    if adx_now <= prev_adx:
        return (
            False,
            f"entry_structure:adx_not_rising adx={adx_now:.1f}<=prev={prev_adx:.1f}",
        )

    atr_ok = atr_now > prev_atr
    spread_ok = spread_now > prev_spread

    if adx_now >= extreme:
        # Rising ADX already proven above; extreme trend is the structure.
        return (
            True,
            (
                f"entry_structure:ok_extreme_adx adx={adx_now:.1f}>={extreme:.0f} "
                f">prev={prev_adx:.1f} atr_exp={atr_ok} spread_widen={spread_ok}"
            ),
        )

    if not atr_ok:
        return (
            False,
            f"entry_structure:atr_not_expanding atr={atr_now:.4f}<=prev={prev_atr:.4f}",
        )
    if not spread_ok:
        return (
            False,
            f"entry_structure:spread_not_widening "
            f"spread={spread_now:.4f}%<=prev={prev_spread:.4f}%",
        )
    return (
        True,
        (
            f"entry_structure:ok atr={atr_now:.4f}>{prev_atr:.4f} "
            f"adx={adx_now:.1f}>{prev_adx:.1f}>{floor:.1f} "
            f"spread={spread_now:.4f}%>{prev_spread:.4f}%"
        ),
    )


def commit_entry_structure_memory(
    session: Any,
    *,
    atr: float,
    adx: float,
    vwap: float,
    twap: float,
    price: float,
) -> None:
    """Persist this cycle's structure samples for the next compare (unidirectional)."""
    try:
        session.prev_atr = float(atr or 0.0)
        session.prev_adx = float(adx or 0.0)
        session.prev_anchor_spread_pct = anchor_spread_pct(
            vwap=vwap, twap=twap, price=price
        )
    except Exception:
        session.prev_atr = 0.0
        session.prev_adx = 0.0
        session.prev_anchor_spread_pct = 0.0
