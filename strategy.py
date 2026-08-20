"""
FutureMathics native Wisdom brain — market regime classification.

Pillar 1 (Wisdom / Phronesis):
  Session VWAP + TWAP scored 0–100% from price distance in bps
  (institutional fair-value: price above / below the average — sticky).
  - Both scores bullish → LONG
  - Both scores bearish (≤45) → SHORT
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
    vol_conviction: float = 50.0
    market_lift: float = 50.0


def score_vs_anchor(
    price: float,
    anchor: float,
    *,
    scale: float | None = None,
    bull_bps: float = 3.0,
    bear_bps: float = 3.0,
) -> float:
    """
    Map price vs session VWAP/TWAP to 0–100 (sticky distance score).

    Primary (VolumeWatch MACRO-style, native math — no VW dependency):
      bps = (price - anchor) / anchor × 10_000
      ≥ +3 bps → bull band (70→100)
      ≤ −3 bps → bear band (≤45, sticky for hours in trends)
      near zero → mid 45–70

    Legacy: if ``scale`` is provided, keep the old linear ATR/% map for tests.
    """
    if price <= 0 or anchor <= 0:
        return 50.0
    if scale is not None and float(scale) > 0:
        raw = 50.0 + 50.0 * ((float(price) - float(anchor)) / float(scale))
        return round(max(0.0, min(100.0, raw)), 2)

    bps = (float(price) - float(anchor)) / float(anchor) * 10_000.0
    bull = max(0.1, float(bull_bps))
    bear = max(0.1, float(bear_bps))
    if bps >= bull:
        # +3 bps → 70, +10 bps → 100
        t = min(1.0, (bps - bull) / 7.0)
        return round(70.0 + 30.0 * t, 2)
    if bps <= -bear:
        # −3 bps → 45 (bear gate), deeper → down toward 20
        t = min(1.0, (-bps - bear) / 27.0)
        return round(max(0.0, 45.0 - 25.0 * t), 2)
    if bps >= 0:
        return round(50.0 + (bps / bull) * 20.0, 2)  # 50 → 70
    return round(50.0 + (bps / bear) * 5.0, 2)  # 50 → 45


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
    adx_short_min: float = 22.0  # default short ADX when not below session VWAP
    adx_short_below_vwap_min: float = 8.0  # price < VWAP + short scores (proxy ADX)
    atr_pct_chaos_max: float = 0.15  # ATR% of price; MES/proxy chaos stand-aside
    score_atr_mult: float = 2.0  # legacy linear score scale (unused when bps scoring)
    score_price_pct: float = 0.004  # legacy linear score scale
    # Ghost/seed discontinuity only — real session distance is the signal (do not wipe).
    max_anchor_gap_pct: float = 0.01  # ≥100 bps + jump → rebase (Justice ghost)
    long_enter: float = 58.0  # ENTER long from flat (session VWAP score >=)
    short_enter: float = 42.0  # ENTER short from flat (session VWAP score <=)
    long_exit: float = 45.0  # while LONG: flatten when VWAP score <= hysteresis
    short_exit: float = 55.0  # while SHORT: flatten when VWAP score >= hysteresis
    # 0 = session cumulative VWAP/TWAP (RTH day); >0 = rolling lookback (tests)
    anchor_window: int = 0
    min_anchor_samples: int = 20
    score_bull_bps: float = 3.0  # price ≥ +3 bps vs VWAP → bull band
    score_bear_bps: float = 3.0  # price ≤ −3 bps vs VWAP → bear band (≤45)
    # Native MACRO participation (Wisdom) — block fake bulls when lift/vol are dead.
    vol_conviction_long_min: float = 45.0
    market_lift_long_min: float = 40.0
    vol_conviction_short_min: float = 40.0
    market_lift_short_max: float = 45.0
    vol_dead_max: float = 35.0
    session_open: float = 0.0  # first print of RTH session (market lift anchor)
    closes: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    highs: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    lows: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    volumes: deque[float] = field(default_factory=lambda: deque(maxlen=300))
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
        vol = max(1.0, float(bar.volume or 1.0))
        # Proxy quotes often have bid≈ask≈mid — preserve close-to-close range for ATR.
        if self.closes:
            prev = float(self.closes[-1])
            high = max(high, close, prev)
            low = min(low, close, prev)
        if high < low:
            high, low = low, high
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.volumes.append(vol)
        if self.session_open <= 0:
            self.session_open = close
        # VWAP uses trade/quote size; TWAP stays equal-weight time average.
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        self.vwap_tracker.update_trade(price=close, size=vol)
        self.twap_tracker.update(close)

    def update_price(
        self,
        price: float,
        *,
        high: float | None = None,
        low: float | None = None,
        volume: float | None = None,
    ) -> None:
        p = float(price)
        self.update(
            Bar(
                high=high if high is not None else p,
                low=low if low is not None else p,
                close=p,
                volume=max(1.0, float(volume or 1.0)),
            )
        )

    def seed(self, bars: Sequence[Bar]) -> None:
        for bar in bars:
            self.update(bar)

    def reset_session_anchors(self) -> None:
        """Clear session VWAP/TWAP on ET day roll (keep OHLC/ATR memory)."""
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        self.vwap_tracker.reset()
        self.twap_tracker.reset()
        self.session_open = 0.0

    def volume_conviction_score(self) -> float:
        """
        Native Vol Conviction 0–100: latest volume vs recent average.
        Dead tape (~0.5× avg) → ~30; average → ~55; 2× → 100.
        """
        vols = [float(v) for v in self.volumes if float(v) > 0]
        if len(vols) < 8:
            return 50.0
        cur = vols[-1]
        base = vols[-21:-1] if len(vols) > 21 else vols[:-1]
        if not base:
            return 50.0
        avg = sum(base) / len(base)
        if avg <= 0:
            return 50.0
        ratio = cur / avg
        if ratio >= 2.0:
            return 100.0
        if ratio >= 1.0:
            return round(55.0 + (ratio - 1.0) * 45.0, 2)
        return round(max(0.0, min(55.0, ratio * 55.0)), 2)

    def market_lift_score(self) -> float:
        """
        Native Market Lift 0–100 from session open → now (bps).
        Flat session → ~50; +30 bps → 100; −30 bps → 0 (matches dead-lift bears).
        """
        if not self.closes:
            return 50.0
        px = float(self.closes[-1])
        op = float(self.session_open) if self.session_open > 0 else float(self.closes[0])
        if px <= 0 or op <= 0:
            return 50.0
        bps = (px - op) / op * 10_000.0
        if bps >= 30.0:
            return 100.0
        if bps <= -30.0:
            return 0.0
        return round(50.0 + (bps / 30.0) * 50.0, 2)

    def rebase_anchors_to_price(self, live_price: float) -> None:
        """
        Pin VWAP/TWAP (and OHLC path) onto the live quote on ghost/seed faults only.

        Justice: never trade on a wrong-scale average. Do NOT call this just because
        price is a few bps away from session VWAP — that distance is the signal.
        """
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        px = float(live_price)
        if px <= 0:
            return
        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)
        vols = list(self.volumes)
        lookback = int(self.anchor_window) if int(self.anchor_window) > 0 else len(closes)
        if closes:
            shift = px - float(closes[-1])
            if abs(shift) > 1e-12:
                self.closes = deque((float(c) + shift for c in closes), maxlen=self.closes.maxlen)
                self.highs = deque((float(h) + shift for h in highs), maxlen=self.highs.maxlen)
                self.lows = deque((float(lo) + shift for lo in lows), maxlen=self.lows.maxlen)
                if self.session_open > 0:
                    self.session_open = float(self.session_open) + shift
            window = list(self.closes)[-lookback:] if lookback > 0 else list(self.closes)
            vols = list(self.volumes)[-lookback:] if lookback > 0 else list(self.volumes)
        else:
            window = [px]
            vols = [1.0]
        while len(vols) < len(window):
            vols.insert(0, 1.0)
        self.vwap_tracker.reset()
        self.twap_tracker.reset()
        for c, v in zip(window, vols[-len(window) :]):
            p = float(c)
            self.vwap_tracker.update_trade(price=p, size=max(1.0, float(v)))
            self.twap_tracker.update(p)
        vwap = float(self.vwap_tracker.vwap or 0.0)
        if vwap > 0 and abs(px - vwap) / px >= 0.01:
            # Ghost mean survived close-align — pin VWAP/TWAP at live only.
            # Keep shifted OHLC path so ATR/ADX memory is not zeroed (Wisdom).
            self.vwap_tracker.reset()
            self.twap_tracker.reset()
            n_ohlc = len(self.closes) if self.closes else max(int(self.min_anchor_samples), 20)
            aw = int(self.anchor_window)
            n = max(
                int(self.min_anchor_samples),
                min(aw, n_ohlc) if aw > 0 else min(n_ohlc, max(int(self.min_anchor_samples), 20)),
            )
            for _ in range(n):
                self.vwap_tracker.update_trade(price=px, size=1.0)
                self.twap_tracker.update(px)

    def anchor_gap_too_wide(self, price: float) -> bool:
        """
        True only on seed/live ghost disconnect (Justice) — not normal VWAP distance.

        Session price sitting 3–40 bps under VWAP is a BEAR signal, not a rebase trigger.
        Ghost if VWAP *or* TWAP sits ≥1% off live (seed can skew one without the other).
        """
        assert self.vwap_tracker is not None and self.twap_tracker is not None
        px = float(price)
        vwap = float(self.vwap_tracker.vwap or 0.0)
        twap = float(self.twap_tracker.twap or 0.0)
        closes = list(self.closes)
        if px <= 0 or not closes:
            return False
        atr = self._atr()
        jump = abs(px - float(closes[-1]))
        jump_gate = max(2.0 * atr if atr > 0 else 0.0, px * 0.002, 5.0)
        gap_vwap = (abs(px - vwap) / px) if vwap > 0 else 0.0
        gap_twap = (abs(px - twap) / px) if twap > 0 else 0.0
        gap_pct = max(gap_vwap, gap_twap)
        sudden = gap_pct >= 0.01 and jump > jump_gate
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
        vol_conviction: float | None = None,
        market_lift: float | None = None,
    ) -> RegimeDecision:
        blended = round((vwap_score + twap_score) / 2.0, 2)
        if vol_conviction is not None:
            vc = float(vol_conviction)
        else:
            vc = float(getattr(self, "_eval_vol_conviction", 50.0))
        if market_lift is not None:
            ml = float(market_lift)
        else:
            # Do not use `or 50` — lift 0.0 is a valid dead-lift reading.
            ml = float(getattr(self, "_eval_market_lift", 50.0))
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
            vol_conviction=vc,
            market_lift=ml,
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
        # Sticky bps distance vs session anchors (price above/below — not ATR chatter).
        vwap_score = score_vs_anchor(
            price, vwap, bull_bps=self.score_bull_bps, bear_bps=self.score_bear_bps
        )
        twap_score = score_vs_anchor(
            price, twap, bull_bps=self.score_bull_bps, bear_bps=self.score_bear_bps
        )
        blended = round((vwap_score + twap_score) / 2.0, 2)
        vol_c = self.volume_conviction_score()
        lift = self.market_lift_score()
        self._eval_vol_conviction = vol_c
        self._eval_market_lift = lift
        hold = (holding or "").upper()

        # Session VWAP only (Wisdom). TWAP is logged, never a veto.
        enter_long = vwap_score >= self.long_enter
        enter_short = vwap_score <= self.short_enter
        # Thesis broken past hysteresis band (default 45/55) — not every mid-50 dip.
        exit_long = vwap_score <= self.long_exit
        exit_short = vwap_score >= self.short_exit

        # Chaos ATR blocks NEW entries only. Never knife an open trade on proxy ATR spikes —
        # open books exit via hysteresis bands / dollar stop / TP / time-decay (Temperance).
        if atr_pct >= self.atr_pct_chaos_max and hold not in {"LONG", "SHORT"}:
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

        # While in a trade: hold only while thesis remains valid; else flatten (course-correct).
        # Return FLAT (not reverse) — reverse needs a fresh entry streak from cash (Courage+Temperance).
        if hold == "LONG":
            if exit_long:
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"FLATTEN SIGNAL | LONG THESIS BROKEN — "
                        f"VWAP {vwap_score:.1f} (TWAP {twap_score:.1f} obs) "
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
                        f"VWAP {vwap_score:.1f} (TWAP {twap_score:.1f} obs) "
                        f"(exit if VWAP ≤{self.long_exit:.0f})"
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
                        f"VWAP {vwap_score:.1f} (TWAP {twap_score:.1f} obs) "
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
                        f"VWAP {vwap_score:.1f} (TWAP {twap_score:.1f} obs) "
                        f"(exit if VWAP ≥{self.short_exit:.0f})"
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

        # Simple stack: session VWAP bands only (chaos already handled).
        # TWAP / EMA / ADX / lift / vol are observational — they cannot veto a clean VWAP.
        try:
            from engine.config import virtue_simple_stack as _virtue_simple_stack

            _simple = bool(_virtue_simple_stack())
        except Exception:
            _simple = False
        if _simple:
            if enter_long:
                return self._empty(
                    regime=Regime.TREND_BULL,
                    action=SignalAction.LONG,
                    reason=(
                        f"LONG SETUP | SIMPLE STACK — "
                        f"VWAP {vwap_score:.1f} ≥ enter {self.long_enter:.0f} "
                        f"(TWAP {twap_score:.1f} obs; ADX/EMA/macro waived)"
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
            if enter_short:
                return self._empty(
                    regime=Regime.TREND_BEAR,
                    action=SignalAction.SHORT,
                    reason=(
                        f"SHORT SETUP | SIMPLE STACK — "
                        f"VWAP {vwap_score:.1f} ≤ enter {self.short_enter:.0f} "
                        f"(TWAP {twap_score:.1f} obs; ADX/EMA/macro waived)"
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
                        f"STAND ASIDE | SIMPLE STACK — "
                        f"need VWAP ≥{self.long_enter:.0f} or ≤{self.short_enter:.0f} "
                        f"· VWAP {vwap_score:.1f} (TWAP {twap_score:.1f} obs)"
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

        # Structural short: price below session VWAP + short scores + dead/low lift.
        # Proxy ADX often stays <20 while day VWAP is clearly bear (VolumeWatch-aligned).
        price_below_vwap = price < vwap
        structural_short = bool(
            enter_short
            and price_below_vwap
            and lift <= float(self.market_lift_short_max)
        )
        short_adx_floor = (
            float(self.adx_short_below_vwap_min)
            if structural_short
            else float(self.adx_short_min)
        )

        # Dead participation — never force LONG; structural shorts may trade thinner vol.
        if vol_c <= float(self.vol_dead_max) and not structural_short:
            return self._empty(
                regime=Regime.CHOP_NO_TRADE,
                action=SignalAction.FLAT,
                reason=(
                    f"STAND ASIDE | DEAD VOLUME CONVICTION — "
                    f"vol_conv {vol_c:.1f}% ≤ {float(self.vol_dead_max):.0f}% "
                    f"(lift {lift:.1f}%) · blend {blended:.1f}"
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
            if lift < float(self.market_lift_long_min) or vol_c < float(
                self.vol_conviction_long_min
            ):
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"STAND ASIDE | LONG BLOCKED BY MACRO PARTICIPATION — "
                        f"lift {lift:.1f}% (need ≥{float(self.market_lift_long_min):.0f}) "
                        f"vol_conv {vol_c:.1f}% (need ≥{float(self.vol_conviction_long_min):.0f}) "
                        f"· VWAP/TWAP blend {blended:.1f} alone is not enough"
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
                    f"LONG SETUP | ENTRY BAND CLEARED — "
                    f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                    f"≥ enter {self.long_enter:.0f} · ADX {adx:.1f} "
                    f"· lift {lift:.1f}% vol_conv {vol_c:.1f}%"
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

        # Below-VWAP structural shorts may waive EMA lag (proxy EMA often slow).
        short_ema_ok = ema_fast <= ema_slow or structural_short
        if enter_short and short_ema_ok:
            if adx < short_adx_floor:
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"STAND ASIDE | SHORT ADX TOO WEAK — "
                        f"ADX {adx:.1f} is below short floor {short_adx_floor:.1f}"
                        f"{' (below-VWAP path)' if structural_short else ''} "
                        f"· blend {blended:.1f}"
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
            if lift > float(self.market_lift_short_max):
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"STAND ASIDE | SHORT BLOCKED BY MACRO PARTICIPATION — "
                        f"lift {lift:.1f}% (need ≤{float(self.market_lift_short_max):.0f}) "
                        f"· blend {blended:.1f}"
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
            # Standard shorts still need volume; structural below-VWAP uses lift+VWAP.
            if (
                not structural_short
                and vol_c < float(self.vol_conviction_short_min)
            ):
                return self._empty(
                    regime=Regime.CHOP_NO_TRADE,
                    action=SignalAction.FLAT,
                    reason=(
                        f"STAND ASIDE | SHORT BLOCKED BY MACRO PARTICIPATION — "
                        f"vol_conv {vol_c:.1f}% (need ≥{float(self.vol_conviction_short_min):.0f}) "
                        f"· blend {blended:.1f}"
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
            path = "below-VWAP" if structural_short else "standard"
            return self._empty(
                regime=Regime.TREND_BEAR,
                action=SignalAction.SHORT,
                reason=(
                    f"SHORT SETUP | ENTRY BAND CLEARED — "
                    f"VWAP {vwap_score:.1f} / TWAP {twap_score:.1f} / blend {blended:.1f} "
                    f"≤ enter {self.short_enter:.0f} · ADX {adx:.1f} ({path}) "
                    f"· lift {lift:.1f}% vol_conv {vol_c:.1f}%"
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

        # Neither side cleared — if ADX is soft and no structural short, say so.
        if adx < float(self.adx_trend_min) and not structural_short:
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
