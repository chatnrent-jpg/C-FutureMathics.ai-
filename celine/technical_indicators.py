"""
Technical indicators for swing trading - EMAs, trend detection, breakouts.

Professional-grade indicators optimized for MES futures swing trading.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal


@dataclass
class TrendAnalysis:
    """Complete trend analysis result."""
    direction: Literal["BULL", "BEAR", "NEUTRAL"]
    strength: float  # 0.0 to 1.0
    ema20: float
    ema50: float
    ema200: float
    higher_highs: bool  # True if making higher highs (bullish)
    higher_lows: bool   # True if making higher lows (bullish)
    momentum: float     # Ticks per period (positive = up, negative = down)


@dataclass
class BreakoutSignal:
    """Breakout detection result."""
    is_breakout: bool
    direction: Literal["BULL", "BEAR", "NONE"]
    breakout_price: float
    strength: float  # 0.0 to 1.0


class TechnicalIndicators:
    """
    Technical analysis indicators for swing trading.
    
    Tracks price history and calculates:
    - Exponential Moving Averages (EMA 20, 50, 200)
    - Trend direction and strength
    - Higher highs / lower lows
    - Breakout detection
    - Momentum
    """
    
    def __init__(self, tick_size: float = 0.25):
        self.tick_size = tick_size
        
        # Price history (last 200 bars for EMA200)
        self.prices: deque[float] = deque(maxlen=200)
        self.highs: deque[float] = deque(maxlen=50)
        self.lows: deque[float] = deque(maxlen=50)
        
        # Cached EMAs (updated each bar)
        self._ema20: float = 0.0
        self._ema50: float = 0.0
        self._ema200: float = 0.0
    
    def update(self, price: float, high: float | None = None, low: float | None = None) -> None:
        """
        Update indicators with new price bar.
        
        Args:
            price: Current/close price
            high: Bar high (or None to use price)
            low: Bar low (or None to use price)
        """
        self.prices.append(price)
        self.highs.append(high if high is not None else price)
        self.lows.append(low if low is not None else price)
        
        # Recalculate EMAs
        self._ema20 = self.calculate_ema(20)
        self._ema50 = self.calculate_ema(50)
        self._ema200 = self.calculate_ema(200)
    
    def calculate_ema(self, period: int) -> float:
        """
        Calculate Exponential Moving Average.
        
        Formula: EMA = Price(t) × k + EMA(y) × (1 - k)
        where k = 2 / (period + 1)
        
        Args:
            period: EMA period (e.g., 20, 50, 200)
            
        Returns:
            EMA value, or 0.0 if not enough data
        """
        if len(self.prices) < 2:
            return 0.0
        
        if len(self.prices) < period:
            # Use SMA until we have enough data
            return sum(list(self.prices)) / len(self.prices)
        
        # Calculate EMA
        k = 2.0 / (period + 1)
        prices_list = list(self.prices)
        
        # Start with SMA of first 'period' prices
        ema = sum(prices_list[:period]) / period
        
        # Apply EMA formula to remaining prices
        for price in prices_list[period:]:
            ema = price * k + ema * (1 - k)
        
        return round(ema, 2)
    
    def analyze_trend(self) -> TrendAnalysis:
        """
        Comprehensive trend analysis.
        
        Returns:
            TrendAnalysis with direction, strength, EMAs, and momentum
        """
        if len(self.prices) < 20:
            return TrendAnalysis(
                direction="NEUTRAL",
                strength=0.0,
                ema20=0.0,
                ema50=0.0,
                ema200=0.0,
                higher_highs=False,
                higher_lows=False,
                momentum=0.0,
            )
        
        current_price = self.prices[-1]
        
        # Determine trend direction based on EMA alignment
        if self._ema20 > 0 and self._ema50 > 0:
            if current_price > self._ema20 > self._ema50:
                if self._ema200 > 0 and self._ema50 > self._ema200:
                    direction = "BULL"
                else:
                    direction = "BULL"  # Still bullish even without EMA200 alignment
            elif current_price < self._ema20 < self._ema50:
                if self._ema200 > 0 and self._ema50 < self._ema200:
                    direction = "BEAR"
                else:
                    direction = "BEAR"  # Still bearish even without EMA200 alignment
            else:
                direction = "NEUTRAL"
        else:
            direction = "NEUTRAL"
        
        # Calculate trend strength (0.0 to 1.0)
        strength = self._calculate_trend_strength(current_price)
        
        # Check for higher highs / lower lows (last 20 bars)
        higher_highs, higher_lows = self._check_swing_points()
        
        # Calculate momentum (ticks per bar over last 10 bars)
        momentum = self._calculate_momentum()
        
        return TrendAnalysis(
            direction=direction,
            strength=round(strength, 3),
            ema20=self._ema20,
            ema50=self._ema50,
            ema200=self._ema200,
            higher_highs=higher_highs,
            higher_lows=higher_lows,
            momentum=round(momentum, 2),
        )
    
    def _calculate_trend_strength(self, current_price: float) -> float:
        """
        Calculate trend strength score (0.0 to 1.0).
        
        Components:
        - EMA alignment (0-0.4)
        - EMA separation (0-0.3)
        - Price momentum (0-0.3)
        """
        if self._ema20 == 0 or self._ema50 == 0:
            return 0.0
        
        # 1. EMA alignment (0-0.4 points)
        if current_price > self._ema20 > self._ema50:
            alignment_score = 0.4  # Perfect bullish alignment
        elif current_price < self._ema20 < self._ema50:
            alignment_score = 0.4  # Perfect bearish alignment
        elif current_price > self._ema20 or current_price < self._ema20:
            alignment_score = 0.2  # Partial alignment
        else:
            alignment_score = 0.0
        
        # 2. EMA separation (0-0.3 points)
        # Wider separation = stronger trend
        separation = abs(self._ema20 - self._ema50) / self._ema50
        separation_score = min(0.3, separation * 20)
        
        # 3. Price momentum (0-0.3 points)
        # Further from EMA20 = stronger momentum
        distance_from_ema20 = abs(current_price - self._ema20) / self._ema20
        momentum_score = min(0.3, distance_from_ema20 * 30)
        
        total = alignment_score + separation_score + momentum_score
        return min(1.0, total)
    
    def _check_swing_points(self) -> tuple[bool, bool]:
        """
        Check if price is making higher highs and higher lows (bullish)
        or lower highs and lower lows (bearish).
        
        Returns:
            (higher_highs, higher_lows)
        """
        if len(self.highs) < 20 or len(self.lows) < 20:
            return False, False
        
        # Compare recent highs/lows to previous highs/lows
        recent_highs = list(self.highs)[-10:]
        previous_highs = list(self.highs)[-20:-10]
        recent_lows = list(self.lows)[-10:]
        previous_lows = list(self.lows)[-20:-10]
        
        # Higher highs: Recent max > Previous max
        higher_highs = max(recent_highs) > max(previous_highs)
        
        # Higher lows: Recent min > Previous min
        higher_lows = min(recent_lows) > min(previous_lows)
        
        return higher_highs, higher_lows
    
    def _calculate_momentum(self) -> float:
        """
        Calculate price momentum (ticks per bar over last 10 bars).
        
        Returns:
            Momentum in ticks (positive = up, negative = down)
        """
        if len(self.prices) < 10:
            return 0.0
        
        prices_list = list(self.prices)
        old_price = prices_list[-10]
        new_price = prices_list[-1]
        
        price_change = new_price - old_price
        momentum_ticks = price_change / self.tick_size / 10  # Ticks per bar
        
        return momentum_ticks
    
    def detect_breakout(self, lookback: int = 20) -> BreakoutSignal:
        """
        Detect breakout above recent high or below recent low.
        
        Args:
            lookback: Number of bars to look back for high/low
            
        Returns:
            BreakoutSignal with breakout status and direction
        """
        if len(self.prices) < lookback:
            return BreakoutSignal(
                is_breakout=False,
                direction="NONE",
                breakout_price=0.0,
                strength=0.0,
            )
        
        current_price = self.prices[-1]
        
        # Get recent highs and lows (excluding current bar)
        recent_highs = list(self.highs)[-lookback-1:-1]
        recent_lows = list(self.lows)[-lookback-1:-1]
        
        if not recent_highs or not recent_lows:
            return BreakoutSignal(
                is_breakout=False,
                direction="NONE",
                breakout_price=0.0,
                strength=0.0,
            )
        
        high_20 = max(recent_highs)
        low_20 = min(recent_lows)
        
        # Breakout threshold (must exceed by at least 3 ticks)
        breakout_threshold = 3 * self.tick_size
        
        # Check for bullish breakout
        if current_price >= high_20 + breakout_threshold:
            distance_ticks = (current_price - high_20) / self.tick_size
            strength = min(1.0, distance_ticks / 10)  # 10 ticks = 1.0 strength
            
            return BreakoutSignal(
                is_breakout=True,
                direction="BULL",
                breakout_price=high_20,
                strength=round(strength, 3),
            )
        
        # Check for bearish breakout
        if current_price <= low_20 - breakout_threshold:
            distance_ticks = (low_20 - current_price) / self.tick_size
            strength = min(1.0, distance_ticks / 10)
            
            return BreakoutSignal(
                is_breakout=True,
                direction="BEAR",
                breakout_price=low_20,
                strength=round(strength, 3),
            )
        
        return BreakoutSignal(
            is_breakout=False,
            direction="NONE",
            breakout_price=0.0,
            strength=0.0,
        )
    
    def is_pullback_to_ema(self, ema_period: int = 20, tolerance_ticks: int = 5) -> bool:
        """
        Check if price has pulled back to EMA (pullback entry opportunity).
        
        Args:
            ema_period: Which EMA to check (20 or 50)
            tolerance_ticks: How close to EMA counts as pullback
            
        Returns:
            True if price is near EMA (within tolerance)
        """
        if len(self.prices) < ema_period:
            return False
        
        current_price = self.prices[-1]
        
        if ema_period == 20:
            ema = self._ema20
        elif ema_period == 50:
            ema = self._ema50
        else:
            return False
        
        if ema == 0:
            return False
        
        # Check if price is within tolerance of EMA
        distance = abs(current_price - ema)
        distance_ticks = distance / self.tick_size
        
        return distance_ticks <= tolerance_ticks
    
    @property
    def ready(self) -> bool:
        """Check if enough data for reliable indicators."""
        return len(self.prices) >= 50  # Need at least 50 bars for EMA50
    
    @property
    def ema20(self) -> float:
        """Get current EMA20."""
        return self._ema20
    
    @property
    def ema50(self) -> float:
        """Get current EMA50."""
        return self._ema50
    
    @property
    def ema200(self) -> float:
        """Get current EMA200."""
        return self._ema200
