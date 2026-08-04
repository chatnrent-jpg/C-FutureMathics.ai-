"""
FutureMathics native Wisdom brain — market regime classification.

Pillar 1 (Wisdom / Phronesis):
  VWAP + TWAP scored 0–100% (50 = at average).
  - Both scores > 50 → LONG
  - Both scores < 50 → SHORT
  - Disagree / neutral → STAND ASIDE
  ATR% chaos → STAND ASIDE (never force trades in unstable vol)

No VolumeWatch dependency. Objective conditions only.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from celine.live_twap import LiveTwapTracker
from celine.live_vwap import LiveVwapTracker


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
    volume: float = 1.0


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
    vwap: float = 0.0
    twap: float = 0.0
    vwap_score: float = 50.0
    twap_score: float = 50.0
    blended_score: float = 50.0


def score_vs_anchor(price: float, anchor: float, *, scale: float) -> float:
    """
    Map price vs anchor to 0–100.
    50 = price at anchor; >50 price above; <50 price below.
    ±scale maps to the full 0–100 range (clamped).
    """
    if price <= 0 or anchor <= 0 or scale <= 0:
        return 50.0
    raw = 50.0 + 50.0 * ((float(price) - float(anchor)) / float(scale))
    return round(max(0.0, min(100.0, raw)), 2)


@dataclass
class WisdomStrategy:
    """
    Dynamic regime detection for MES (or any continuous price series).

    Direction: VWAP score + TWAP score (both must agree vs 50%).
    Volatility structure: ATR% for chaos / unstable vol stand-aside.
    Entries: ADX + EMA alignment gates (fewer wrong-side starts).
    Holds: invalidate at mid-band and flatten (nimble course-correct).
    """

    ema_fast_period: int = 20
    ema_slow_period: int = 50
    adx_period: int = 14
    atr_period: int = 14
    adx_trend_min: float = 18.0  # flat long entries require ADX >= this (Wisdom)
    adx_short_min: float = 22.0  # shorts need stronger ADX (Temperance)
    atr_pct_chaos_max: float = 2.5  # ATR as % of price; above → stand aside
    score_atr_mult: float = 2.0  # ATR component of score scale
    score_price_pct: float = 0.004  # ±0.4% of price spans 0–100 (trend extensions register)
    max_anchor_gap_pct: float = 0.004  # >40bps price vs VWAP → rebase (Justice)
    long_enter: float = 58.0  # ENTER long from flat (both scores >=)
    short_enter: float = 42.0  # ENTER short from flat (both scores <=)
    long_exit: float = 50.0  # while LONG: flatten when both scores <= mid
    short_exit: float = 50.0  # while SHORT: flatten when both scores >= mid
    anchor_window: int = 60  # rolling VWAP/TWAP lookback (seed + live)
    min_anchor_samples: int = 20
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    highs: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    lows: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    vwap_tracker: LiveVwapTracker | None = None
    twap_tracker: LiveTwapTracker | None = None

    def __post_init__(self) -> None:
        if self.vwap_tracker is None:
            self.vwap_tracker = LiveVwapTracker(window=self.anchor_window)
        if self.twap_tracker is None:
            self.twap_tracker = LiveTwapTracker(window=self.anchor_window)

    def update(self, bar: Bar) -> None:
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        # Equal-weight close for both anchors (SPY proxy has no reliable tape size)
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        self.vwap_tracker.update_trade(price=close, size=1.0)
        self.twap_tracker.update(close)

    def update_price(self, price: float, *, high: float | None = None, low: float | None = None) -> None:
        p = float(price)
        self.update(
            Bar(
                high=high if high is not None else p,
                low=low if low is not None else p,
                close=p,
                volume=1.0,
            )
        )

    def seed(self, bars: Sequence[Bar]) -> None:
        for bar in bars:
            self.update(bar)

    def rebase_anchors_to_price(self, live_price: float) -> None:
        """
        Pin rolling VWAP/TWAP (and OHLC path) onto the live quote (Justice).

        1) Shift OHLC so last close == live (preserve relative path / ATR).
        2) Rebuild VWAP/TWAP from that aligned window.
        3) If the average is still ≥1% off live (ghost seed), flat-pin anchors
           at the live print so scores cannot clamp to 0/100 and fake a SHORT/LONG.
        """
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        px = float(live_price)
        if px <= 0:
            return
        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)
        if closes:
            shift = px - float(closes[-1])
            if abs(shift) > 1e-12:
                self.closes = deque((float(c) + shift for c in closes), maxlen=self.closes.maxlen)
                self.highs = deque((float(h) + shift for h in highs), maxlen=self.highs.maxlen)
                self.lows = deque((float(lo) + shift for lo in lows), maxlen=self.lows.maxlen)
            window = list(self.closes)[-self.anchor_window :]
        else:
            window = [px]
        self.vwap_tracker.reset()
        self.twap_tracker.reset()
        for c in window:
            p = float(c)
            self.vwap_tracker.update_trade(price=p, size=1.0)
            self.twap_tracker.update(p)
        vwap = float(self.vwap_tracker.vwap or 0.0)
        if vwap > 0 and abs(px - vwap) / px >= 0.01:
            # Ghost mean survived the close-align — pin anchors and collapse OHLC
            # so ATR/ADX are not haunted by a cliff in the seed window.
            n_ohlc = len(self.closes) if self.closes else max(int(self.min_anchor_samples), 20)
            self.closes = deque([px] * n_ohlc, maxlen=self.closes.maxlen)
            self.highs = deque([px] * n_ohlc, maxlen=self.highs.maxlen)
            self.lows = deque([px] * n_ohlc, maxlen=self.lows.maxlen)
            self.vwap_tracker.reset()
            self.twap_tracker.reset()
            n = max(int(self.min_anchor_samples), min(int(self.anchor_window), n_ohlc))
            for _ in range(n):
                self.vwap_tracker.update_trade(price=px, size=1.0)
                self.twap_tracker.update(px)

    def anchor_gap_too_wide(self, price: float) -> bool:
        """
        True on seed/live disconnect from rolling VWAP (Justice).

        - Sudden jump: gap wide AND last close jumped (classic seed cutover).
        - Ghost VWAP: average ≥1% off live even when last close already matches
          (the deploy failure that left blend=0 / false SHORT in a bull).
        """
        assert self.vwap_tracker is not None
        px = float(price)
        vwap = float(self.vwap_tracker.vwap or 0.0)
        closes = list(self.closes)
        if px <= 0 or vwap <= 0 or not closes:
            return False
        gap_pct = abs(px - vwap) / px
        atr = self._atr()
        jump = abs(px - float(closes[-1]))
        jump_gate = max(2.0 * atr if atr > 0 else 0.0, px * 0.002, 5.0)
        gap_gate = max(self.max_anchor_gap_pct, (3.0 * atr / px) if atr > 0 else self.max_anchor_gap_pct)
        sudden = gap_pct > gap_gate and jump > jump_gate
        ghost = gap_pct >= 0.01  # ≥100 bps — never trade on a ghost average
        return sudden or ghost

    def anchors_diverged(self, *, atr_mult: float = 3.0) -> bool:
        """
        True when |VWAP − TWAP| exceeds atr_mult × ATR (anchor disagreement).
        Wisdom: rebase + cooldown rather than trade on conflicting averages.
        """
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        vwap = float(self.vwap_tracker.vwap or 0.0)
        twap = float(self.twap_tracker.twap or 0.0)
        atr = self._atr()
        if vwap <= 0 or twap <= 0 or atr <= 0:
            return False
        return abs(vwap - twap) > (atr * float(atr_mult))

    def score_scale(self, *, price: float, atr: float) -> float:
        return max(atr * self.score_atr_mult, price * self.score_price_pct, 5.0)


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
        """Wilder-style ADX approximation on stored OHLC (telemetry only)."""
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
        adx_s = wilder_smooth(dx_list, n)
        return float(adx_s[-1] / n)

    def _empty(
        self,
        *,
        regime: Regime,
        action: SignalAction,
        reason: str,
        ema_fast: float = 0.0,
        ema_slow: float = 0.0,
        adx: float = 0.0,
        atr: float = 0.0,
        atr_pct: float = 0.0,
        vwap: float = 0.0,
        twap: float = 0.0,
        vwap_score: float = 50.0,
        twap_score: float = 50.0,
    ) -> RegimeDecision:
        blended = round((vwap_score + twap_score) / 2.0, 2)
        return RegimeDecision(
            regime=regime,
            action=action,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            adx=adx,
            atr=atr,
            atr_pct=atr_pct,
            reason=reason,
            vwap=vwap,
            twap=twap,
            vwap_score=vwap_score,
            twap_score=twap_score,
            blended_score=blended,
        )

    def evaluate(self, *, holding: str | None = None) -> RegimeDecision:
        need = max(self.atr_period + 2, self.min_anchor_samples)
        if len(self.closes) < need or self.twap_tracker.samples < self.min_anchor_samples:
            return self._empty(
                regime=Regime.WARMUP,
                action=SignalAction.FLAT,
                reason="insufficient_bars_stand_aside",
            )

        closes = list(self.closes)
        price = closes[-1]
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        adx = self._adx()
        atr = self._atr()
        atr_pct = (atr / price * 100.0) if price > 0 else 0.0
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        vwap = float(self.vwap_tracker.vwap or price)
        twap = float(self.twap_tracker.twap or price)
        # Wide scale so normal MES noise doesn't slam scores to 0/100
        scale = self.score_scale(price=price, atr=atr)
        vwap_score = score_vs_anchor(price, vwap, scale=scale)
        twap_score = score_vs_anchor(price, twap, scale=scale)
        blended = round((vwap_score + twap_score) / 2.0, 2)
        hold = (holding or "").upper()

        # Chaos / unstable volatility → stand aside (Wisdom)
        if atr_pct >= self.atr_pct_chaos_max:
            return self._empty(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                reason=(
                    f"STAND ASIDE | CHAOS VOLATILITY — "
                    f"ATR% {atr_pct:.2f} is at/above chaos ceiling {self.atr_pct_chaos_max:.2f}"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        # Entry bands (from flat) vs invalidate bands (while holding) — nimble hysteresis
        enter_long = vwap_score >= self.long_enter and twap_score >= self.long_enter
        enter_short = vwap_score <= self.short_enter and twap_score <= self.short_enter
        # Thesis broken at mid: do not ride wrong-side into the dollar stop.
        exit_long = vwap_score <= self.long_exit and twap_score <= self.long_exit
        exit_short = vwap_score >= self.short_exit and twap_score >= self.short_exit

        # While in a trade: hold only while thesis remains valid; else flatten (course-correct).
        # Return FLAT (not reverse) — reverse needs a fresh entry streak from cash (Courage+Temperance).
        if hold == "LONG":
            if exit_long:
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"FLATTEN SIGNAL | LONG THESIS BROKEN — "
                        f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                        f"dropped to exit band ≤{self.long_exit:.0f}"
                    ),
                    ema_fast=ema_fast,
                    ema_slow=ema_slow,
                    adx=adx,
                    atr=atr,
                    atr_pct=atr_pct,
                    vwap=vwap,
                    twap=twap,
                    vwap_score=vwap_score,
                    twap_score=twap_score,
                )
            return self._empty(
                regime=Regime.TREND_BULL,
                action=SignalAction.LONG,
                reason=(
                    f"HOLDING LONG | THESIS STILL VALID — "
                    f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                    f"(exit if both ≤{self.long_exit:.0f})"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        if hold == "SHORT":
            if exit_short:
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"FLATTEN SIGNAL | SHORT THESIS BROKEN — "
                        f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                        f"rose to exit band ≥{self.short_exit:.0f}"
                    ),
                    ema_fast=ema_fast,
                    ema_slow=ema_slow,
                    adx=adx,
                    atr=atr,
                    atr_pct=atr_pct,
                    vwap=vwap,
                    twap=twap,
                    vwap_score=vwap_score,
                    twap_score=twap_score,
                )
            return self._empty(
                regime=Regime.TREND_BEAR,
                action=SignalAction.SHORT,
                reason=(
                    f"HOLDING SHORT | THESIS STILL VALID — "
                    f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                    f"(exit if both ≥{self.short_exit:.0f})"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        # Flat: clear ENTRY band + ADX strength + EMA alignment (fewer wrong starts)
        if adx < float(self.adx_trend_min):
            return self._empty(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                reason=(
                    f"STAND ASIDE | ADX TOO WEAK — "
                    f"trend strength ADX {adx:.1f} is below floor {self.adx_trend_min:.1f} "
                    f"(need stronger trend to enter) · blend {blended:.1f}"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        if enter_long and ema_fast >= ema_slow:
            return self._empty(
                regime=Regime.TREND_BULL,
                action=SignalAction.LONG,
                reason=(
                    f"LONG SETUP | ENTRY BAND CLEARED — "
                    f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                    f"≥ enter {self.long_enter:.0f} · ADX {adx:.1f}"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        if enter_short and ema_fast <= ema_slow:
            if adx < float(self.adx_short_min):
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"STAND ASIDE | SHORT ADX TOO WEAK — "
                        f"ADX {adx:.1f} is below short floor {self.adx_short_min:.1f} "
                        f"(shorts need stronger trend) · blend {blended:.1f}"
                    ),
                    ema_fast=ema_fast,
                    ema_slow=ema_slow,
                    adx=adx,
                    atr=atr,
                    atr_pct=atr_pct,
                    vwap=vwap,
                    twap=twap,
                    vwap_score=vwap_score,
                    twap_score=twap_score,
                )
            return self._empty(
                regime=Regime.TREND_BEAR,
                action=SignalAction.SHORT,
                reason=(
                    f"SHORT SETUP | ENTRY BAND CLEARED — "
                    f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                    f"≤ enter {self.short_enter:.0f} · ADX {adx:.1f}"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        if enter_long and ema_fast < ema_slow:
            return self._empty(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                reason=(
                    f"STAND ASIDE | EMA DISAGREES WITH LONG — "
                    f"blend {blended:.1f} but fast EMA {ema_fast:.2f} < slow EMA {ema_slow:.2f}"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        if enter_short and ema_fast > ema_slow:
            return self._empty(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                reason=(
                    f"STAND ASIDE | EMA DISAGREES WITH SHORT — "
                    f"blend {blended:.1f} but fast EMA {ema_fast:.2f} > slow EMA {ema_slow:.2f}"
                ),
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                adx=adx,
                atr=atr,
                atr_pct=atr_pct,
                vwap=vwap,
                twap=twap,
                vwap_score=vwap_score,
                twap_score=twap_score,
            )

        return self._empty(
            regime=Regime.CHOP_NO_TRADE,
            action=SignalAction.FLAT,
            reason=(
                f"STAND ASIDE | NEUTRAL BAND — "
                f"blend {blended:.1f} is between long enter ≥{self.long_enter:.0f} "
                f"and short enter ≤{self.short_enter:.0f} "
                f"(VWAP {vwap_score:.1f} / TWAP {twap_score:.1f}) — no clear edge"
            ),
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            adx=adx,
            atr=atr,
            atr_pct=atr_pct,
            vwap=vwap,
            twap=twap,
            vwap_score=vwap_score,
            twap_score=twap_score,
        )


__all__ = [
    "Bar",
    "Regime",
    "RegimeDecision",
    "SignalAction",
    "WisdomStrategy",
    "score_vs_anchor",
]
