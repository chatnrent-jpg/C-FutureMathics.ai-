"""
FutureMathics native Wisdom brain — market regime classification.

Pillar 1 (Wisdom / Phronesis):
  EMA trend filter + ADX/ATR volatility filter.
  - Bullish trend + stable volatility → LONG
  - Bearish trend → SHORT
  - Choppy / chaotic ADX → STAND ASIDE (NO_TRADE)

No VolumeWatch dependency. Objective conditions only.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


class Regime(str, Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    CHOP_NO_TRADE = "CHOP_NO_TRADE"
    WARMUP = "WARMUP"


class SignalAction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"  # stand aside — virtue: never force a trade


@dataclass(frozen=True, slots=True)
class Bar:
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class RegimeDecision:
    regime: Regime
    action: SignalAction
    ema_fast: float
    ema_slow: float
    adx: float
    atr: float
    atr_pct: float
    reason: str


@dataclass
class WisdomStrategy:
    """
    Dynamic regime detection for MES (or any continuous price series).

    Trend: EMA(fast) vs EMA(slow).
    Volatility structure: ADX for trend strength; ATR% for chaos / unstable vol.
    """

    ema_fast_period: int = 20
    ema_slow_period: int = 50
    adx_period: int = 14
    atr_period: int = 14
    adx_trend_min: float = 25.0  # below → chop / no trade
    atr_pct_chaos_max: float = 2.5  # ATR as % of price; above → stand aside
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    highs: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    lows: deque[float] = field(default_factory=lambda: deque(maxlen=300))

    def update(self, bar: Bar) -> None:
        self.highs.append(float(bar.high))
        self.lows.append(float(bar.low))
        self.closes.append(float(bar.close))

    def update_price(self, price: float, *, high: float | None = None, low: float | None = None) -> None:
        p = float(price)
        self.update(Bar(high=high if high is not None else p, low=low if low is not None else p, close=p))

    def seed(self, bars: Sequence[Bar]) -> None:
        for bar in bars:
            self.update(bar)

    @staticmethod
    def _ema(values: Sequence[float], period: int) -> float:
        if not values:
            return 0.0
        if len(values) < period:
            return sum(values) / len(values)
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for price in values[period:]:
            ema = price * k + ema * (1.0 - k)
        return float(ema)

    def _atr(self) -> float:
        n = self.atr_period
        if len(self.closes) < n + 1:
            return 0.0
        trs: list[float] = []
        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        window = trs[-n:]
        return sum(window) / len(window) if window else 0.0

    def _adx(self) -> float:
        """Wilder-style ADX approximation on stored OHLC."""
        n = self.adx_period
        if len(self.closes) < n + 2:
            return 0.0
        highs = list(self.highs)
        lows = list(self.lows)
        closes = list(self.closes)

        plus_dm: list[float] = []
        minus_dm: list[float] = []
        trs: list[float] = []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
            trs.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

        if len(trs) < n:
            return 0.0

        def wilder_smooth(vals: list[float], period: int) -> list[float]:
            out: list[float] = []
            s = sum(vals[:period])
            out.append(s)
            for v in vals[period:]:
                s = s - (s / period) + v
                out.append(s)
            return out

        atr_s = wilder_smooth(trs, n)
        plus_s = wilder_smooth(plus_dm, n)
        minus_s = wilder_smooth(minus_dm, n)
        dx_list: list[float] = []
        for a, p, m in zip(atr_s, plus_s, minus_s):
            if a <= 0:
                dx_list.append(0.0)
                continue
            plus_di = 100.0 * (p / a)
            minus_di = 100.0 * (m / a)
            denom = plus_di + minus_di
            dx = 0.0 if denom <= 0 else 100.0 * abs(plus_di - minus_di) / denom
            dx_list.append(dx)
        if len(dx_list) < n:
            return sum(dx_list) / len(dx_list) if dx_list else 0.0
        # ADX = Wilder smooth of DX
        adx_s = wilder_smooth(dx_list, n)
        return float(adx_s[-1] / n)

    def evaluate(self) -> RegimeDecision:
        need = max(self.ema_slow_period, self.adx_period + 2, self.atr_period + 2)
        if len(self.closes) < need:
            return RegimeDecision(
                regime=Regime.WARMUP,
                action=SignalAction.FLAT,
                ema_fast=0.0,
                ema_slow=0.0,
                adx=0.0,
                atr=0.0,
                atr_pct=0.0,
                reason="insufficient_bars_stand_aside",
            )

        closes = list(self.closes)
        price = closes[-1]
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        adx = self._adx()
        atr = self._atr()
        atr_pct = (atr / price * 100.0) if price > 0 else 0.0

        # Chaos / unstable volatility → stand aside (Wisdom)
        if atr_pct >= self.atr_pct_chaos_max:
            return RegimeDecision(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                reason=f"atr_chaos atr_pct={atr_pct:.2f}>={self.atr_pct_chaos_max}",
            )

        # Choppy range (weak ADX) → No Trade zone
        if adx < self.adx_trend_min:
            return RegimeDecision(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                reason=f"adx_chop adx={adx:.1f}<{self.adx_trend_min}",
            )

        if ema_fast > ema_slow:
            return RegimeDecision(
                regime=Regime.TREND_BULL,
                action=SignalAction.LONG,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                reason="ema_bull_adx_ok_vol_stable",
            )

        if ema_fast < ema_slow:
            return RegimeDecision(
                regime=Regime.TREND_BEAR,
                action=SignalAction.SHORT,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                reason="ema_bear_adx_ok_vol_stable",
            )

        return RegimeDecision(
            regime=Regime.CHOP_NO_TRADE,
            action=SignalAction.FLAT,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            adx=adx,
            atr=atr,
            atr_pct=atr_pct,
            reason="ema_flat_stand_aside",
        )


__all__ = [
    "Bar",
    "Regime",
    "RegimeDecision",
    "SignalAction",
    "WisdomStrategy",
]
