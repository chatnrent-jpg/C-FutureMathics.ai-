"""
Celine — MES futures signal engine (VWAP trend + session regime).

Replaces options VRP/GEX with directional bias from price vs session VWAP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from collections import deque
import math

from celine.live_vwap import LiveVwapTracker
from engine.config import EXECUTION_SYMBOL, TICK_SIZE, VWAP_ENTRY_THRESHOLD_TICKS, MIN_CONFIDENCE_THRESHOLD

# MarketMathics options uses True to inject synthetic VRP; futures uses real VWAP only.
SANDBOX_VOL_OVERRIDE = False


class TrendRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(frozen=True, slots=True)
class FuturesSnapshot:
    symbol: str
    last_price: float
    bid: float
    ask: float
    vwap: float
    trend_regime: TrendRegime
    feed_latency_ms: float
    sequence_id: int
    data_valid: bool
    spread_ticks: float
    timestamp_utc: str = ""


@dataclass(frozen=True, slots=True)
class FuturesTradeSignal:
    direction: SignalDirection
    symbol: str
    entry_price: float
    trend_regime: str
    vwap_distance_ticks: float
    confidence: float
    sequence_id: int
    feed_latency_ms: float


@dataclass
class FuturesSignalEngine:
    symbol: str = EXECUTION_SYMBOL
    vwap_tracker: LiveVwapTracker = field(default_factory=LiveVwapTracker)
    _sequence: int = 0
    recent_prices: deque = field(default_factory=lambda: deque(maxlen=30))  # Track last 30 prices
    
    def _calculate_trend_momentum(self) -> float:
        """
        Calculate price momentum using linear regression slope.
        Returns: ticks per tick (positive = uptrend, negative = downtrend)
        """
        if len(self.recent_prices) < 10:
            return 0.0
        
        # Simple linear regression: y = mx + b
        # We only need slope (m) for trend direction
        n = len(self.recent_prices)
        prices = list(self.recent_prices)
        
        # Calculate means
        x_mean = (n - 1) / 2  # Time indices: 0, 1, 2, ... n-1
        y_mean = sum(prices) / n
        
        # Calculate slope using least squares
        numerator = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return 0.0
        
        slope = numerator / denominator
        
        # Convert to ticks per period
        return slope / TICK_SIZE
    
    def _calculate_volatility(self) -> float:
        """
        Calculate recent price volatility (standard deviation in ticks).
        Higher volatility = less reliable signals.
        """
        if len(self.recent_prices) < 10:
            return 1.0
        
        prices = list(self.recent_prices)
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = math.sqrt(variance)
        
        return std_dev / TICK_SIZE
    
    def _calculate_vwap_quality(self, dist_ticks: float) -> float:
        """
        Calculate signal quality based on VWAP distance and context.
        Returns: 0.0 to 1.0 confidence score
        """
        # Base confidence from distance
        # Further from VWAP = stronger signal (up to a point)
        abs_dist = abs(dist_ticks)
        
        if abs_dist < VWAP_ENTRY_THRESHOLD_TICKS:
            return 0.0  # Too close to VWAP
        
        # Optimal distance: 8-16 ticks from VWAP
        if 8 <= abs_dist <= 16:
            base_confidence = 0.65 + (abs_dist - 8) * 0.03  # 0.65 to 0.89
        elif abs_dist > 16:
            # Too far = might be extended, reduce confidence
            base_confidence = 0.89 - (abs_dist - 16) * 0.02
            base_confidence = max(0.50, base_confidence)
        else:
            base_confidence = 0.50
        
        # Adjust for volatility (higher vol = lower confidence)
        volatility = self._calculate_volatility()
        vol_factor = 1.0 / (1.0 + volatility / 10.0)  # Dampen high volatility
        
        return min(0.95, base_confidence * vol_factor)

    def ingest_tick(
        self,
        *,
        price: float,
        size: float = 1.0,
        bid: float | None = None,
        ask: float | None = None,
        latency_ms: float = 0.0,
    ) -> FuturesSnapshot:
        self._sequence += 1
        self.recent_prices.append(price)  # Track for trend/volatility
        
        bid_px = bid if bid is not None else price - TICK_SIZE
        ask_px = ask if ask is not None else price + TICK_SIZE
        self.vwap_tracker.update_trade(price=price, size=size)
        vwap = self.vwap_tracker.vwap or price
        spread_ticks = max((ask_px - bid_px) / TICK_SIZE, 0.0)
        dist_ticks = (price - vwap) / TICK_SIZE

        if dist_ticks >= VWAP_ENTRY_THRESHOLD_TICKS:
            regime = TrendRegime.BULL
        elif dist_ticks <= -VWAP_ENTRY_THRESHOLD_TICKS:
            regime = TrendRegime.BEAR
        else:
            regime = TrendRegime.NEUTRAL

        return FuturesSnapshot(
            symbol=self.symbol,
            last_price=round(price, 2),
            bid=round(bid_px, 2),
            ask=round(ask_px, 2),
            vwap=round(vwap, 2),
            trend_regime=regime,
            feed_latency_ms=latency_ms,
            sequence_id=self._sequence,
            data_valid=price > 0,
            spread_ticks=round(spread_ticks, 2),
            timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def build_trade_signal(self, snapshot: FuturesSnapshot) -> FuturesTradeSignal | None:
        if not snapshot.data_valid:
            return None
        
        dist = (snapshot.last_price - snapshot.vwap) / TICK_SIZE
        
        # No signal in neutral regime
        if snapshot.trend_regime == TrendRegime.NEUTRAL:
            return None
        
        # Calculate trend momentum to avoid counter-trend trades
        momentum = self._calculate_trend_momentum()
        
        # Determine direction
        if snapshot.trend_regime == TrendRegime.BULL:
            direction = SignalDirection.LONG
            # Don't LONG into strong downtrend (momentum < -12 ticks)
            if momentum < -12:
                return None
        elif snapshot.trend_regime == TrendRegime.BEAR:
            direction = SignalDirection.SHORT
            # Don't SHORT into strong uptrend (momentum > +12 ticks)
            if momentum > 12:
                return None
        else:
            return None
        
        # Calculate sophisticated confidence score
        confidence = self._calculate_vwap_quality(dist)
        
        # Apply minimum confidence threshold
        if confidence < MIN_CONFIDENCE_THRESHOLD:
            return None

        return FuturesTradeSignal(
            direction=direction,
            symbol=snapshot.symbol,
            entry_price=snapshot.last_price,
            trend_regime=snapshot.trend_regime.value,
            vwap_distance_ticks=round(dist, 2),
            confidence=round(confidence, 3),
            sequence_id=snapshot.sequence_id,
            feed_latency_ms=snapshot.feed_latency_ms,
        )

    async def refresh(self, market_context: dict[str, Any] | None) -> tuple[FuturesSnapshot | None, FuturesTradeSignal | None]:
        ctx = market_context or {}
        tick = ctx.get("tick") or {}
        price = float(tick.get("price") or tick.get("last") or 0)
        if price <= 0:
            return None, None
        snap = self.ingest_tick(
            price=price,
            size=float(tick.get("size") or 1),
            bid=float(tick["bid"]) if tick.get("bid") is not None else None,
            ask=float(tick["ask"]) if tick.get("ask") is not None else None,
            latency_ms=float(tick.get("latency_ms") or 0),
        )
        return snap, self.build_trade_signal(snap)
