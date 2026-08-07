"""
FutureMathics Institutional Regime Engine — Market condition classification.

Detects 3 primary regimes:
1. TREND (ADX ≥22, EMA aligned, blend extreme) - Trend-following mode
2. RANGE (ADX 12-22, normal ATR) - Mean-reversion mode
3. CHAOS (ATR% ≥20 OR ADX <12) - Stand aside

Institutional algos adapt strategy to regime, not trade the same way always.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MarketRegime(str, Enum):
    """Primary market regimes for strategy adaptation."""
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    RANGE_MEAN_REVERT = "RANGE_MEAN_REVERT"
    CHAOS_STAND_ASIDE = "CHAOS_STAND_ASIDE"


@dataclass(frozen=True, slots=True)
class RegimeState:
    """Complete regime classification with confidence."""
    regime: MarketRegime
    confidence: float  # 0-100
    reason: str
    adx: float = 0.0
    atr_pct: float = 0.0
    blend: float = 50.0


def detect_regime(
    *,
    adx: float,
    atr_pct: float,
    blend: float,
    ema_fast: float,
    ema_slow: float,
) -> RegimeState:
    """
    Institutional-grade regime detection with confidence scoring.

    Order of precedence:
    1. CHAOS filters (high volatility, dead ADX) - immediate stand aside
    2. TREND detection (strong ADX + EMA alignment + blend extreme)
    3. RANGE default (ADX 12-22, normal conditions)

    Returns RegimeState with regime, confidence (0-100), and reasoning.
    """
    from engine.config import (
        VIRTUE_SIMPLE_STACK,
    )

    # Import institutional thresholds (will be added to config)
    try:
        from engine.config import (
            INSTITUTIONAL_ADX_TREND_MIN,
            INSTITUTIONAL_ADX_RANGE_MIN,
            INSTITUTIONAL_ATR_CHAOS_MAX,
        )
    except ImportError:
        # Fallback defaults if not yet in config
        INSTITUTIONAL_ADX_TREND_MIN = 22.0
        INSTITUTIONAL_ADX_RANGE_MIN = 12.0
        INSTITUTIONAL_ATR_CHAOS_MAX = 0.20

    adx_val = float(adx or 0.0)
    atr_val = float(atr_pct or 0.0)
    blend_val = float(blend or 50.0)
    ema_f = float(ema_fast or 0.0)
    ema_s = float(ema_slow or 0.0)

    # ============================================================
    # CHAOS DETECTION (Priority 1)
    # ============================================================

    # High volatility = unreliable signals, stand aside
    if atr_val >= INSTITUTIONAL_ATR_CHAOS_MAX:
        return RegimeState(
            regime=MarketRegime.CHAOS_STAND_ASIDE,
            confidence=100.0,
            reason=f"CHAOS | High volatility ATR% {atr_val:.2f}% ≥ {INSTITUTIONAL_ATR_CHAOS_MAX:.2f}%",
            adx=adx_val,
            atr_pct=atr_val,
            blend=blend_val,
        )

    # Dead drift (ADX < 12) = no edge, pure noise
    if adx_val < INSTITUTIONAL_ADX_RANGE_MIN:
        return RegimeState(
            regime=MarketRegime.CHAOS_STAND_ASIDE,
            confidence=100.0,
            reason=f"CHAOS | Dead drift ADX {adx_val:.1f} < {INSTITUTIONAL_ADX_RANGE_MIN:.0f}",
            adx=adx_val,
            atr_pct=atr_val,
            blend=blend_val,
        )

    # ============================================================
    # TREND DETECTION (Priority 2)
    # ============================================================

    # Strong trend requires: ADX ≥22 + EMA alignment + blend extreme
    if adx_val >= INSTITUTIONAL_ADX_TREND_MIN:
        ema_bull = ema_f > ema_s
        ema_bear = ema_f < ema_s
        blend_bull = blend_val >= 65.0  # Strong bull (price well above VWAP/TWAP)
        blend_bear = blend_val <= 35.0  # Strong bear (price well below VWAP/TWAP)

        # TREND_BULL: ADX strong + EMA bullish + blend bullish
        if ema_bull and blend_bull:
            # Confidence scales with ADX strength: 22→60%, 30→76%, 40→96%
            confidence = min(100.0, 50.0 + (adx_val - 22.0) * 2.0)
            return RegimeState(
                regime=MarketRegime.TREND_BULL,
                confidence=confidence,
                reason=f"TREND_BULL | ADX {adx_val:.1f} EMA_bull blend {blend_val:.1f}",
                adx=adx_val,
                atr_pct=atr_val,
                blend=blend_val,
            )

        # TREND_BEAR: ADX strong + EMA bearish + blend bearish
        if ema_bear and blend_bear:
            confidence = min(100.0, 50.0 + (adx_val - 22.0) * 2.0)
            return RegimeState(
                regime=MarketRegime.TREND_BEAR,
                confidence=confidence,
                reason=f"TREND_BEAR | ADX {adx_val:.1f} EMA_bear blend {blend_val:.1f}",
                adx=adx_val,
                atr_pct=atr_val,
                blend=blend_val,
            )

    # ============================================================
    # RANGE / MEAN-REVERSION (Default)
    # ============================================================

    # ADX 12-22 with normal volatility = range-bound, mean-reversion edge
    return RegimeState(
        regime=MarketRegime.RANGE_MEAN_REVERT,
        confidence=60.0,
        reason=f"RANGE | ADX {adx_val:.1f} (12-22 range) mean-revert mode",
        adx=adx_val,
        atr_pct=atr_val,
        blend=blend_val,
    )


def regime_allows_entry(regime: MarketRegime) -> bool:
    """True if regime permits new entries (TREND or RANGE), False if CHAOS."""
    return regime in {
        MarketRegime.TREND_BULL,
        MarketRegime.TREND_BEAR,
        MarketRegime.RANGE_MEAN_REVERT,
    }


def regime_is_trending(regime: MarketRegime) -> bool:
    """True if regime is TREND_BULL or TREND_BEAR."""
    return regime in {MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR}


def regime_is_range(regime: MarketRegime) -> bool:
    """True if regime is RANGE_MEAN_REVERT."""
    return regime == MarketRegime.RANGE_MEAN_REVERT


def regime_is_chaos(regime: MarketRegime) -> bool:
    """True if regime is CHAOS_STAND_ASIDE."""
    return regime == MarketRegime.CHAOS_STAND_ASIDE


__all__ = [
    "MarketRegime",
    "RegimeState",
    "detect_regime",
    "regime_allows_entry",
    "regime_is_trending",
    "regime_is_range",
    "regime_is_chaos",
]
