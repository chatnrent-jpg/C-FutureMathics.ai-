"""
Swing trading signal engine - Trend following, breakouts, pullbacks.

Generates 2-5 high-quality swing trade signals per day.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from celine.technical_indicators import TechnicalIndicators
from engine.config import TICK_SIZE


@dataclass(frozen=True)
class SwingTradeSignal:
    """Swing trade entry signal."""
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_ticks: int
    target_ticks: int
    signal_type: Literal["BREAKOUT", "PULLBACK", "MOMENTUM"]
    trend_strength: float  # 0.0 to 1.0
    confidence: float      # 0.0 to 1.0
    reason: str
    timestamp: float


class SwingSignalEngine:
    """
    Professional swing trading signal generator.
    
    Strategy:
    - Trend following (not mean reversion)
    - Breakout and pullback entries
    - 4-hour to 1-day holds
    - 2-5 signals per day maximum
    
    Entry Criteria:
    - Strong trend (60%+ strength)
    - Breakout above resistance OR pullback to support
    - Between 9:45 AM - 2:00 PM ET (avoid open/close)
    - Minimum 4 hours between signals
    """
    
    def __init__(
        self,
        tick_size: float = TICK_SIZE,
        stop_ticks: int = 60,
        target_ticks: int = 120,
        min_trend_strength: float = 0.60,
        min_confidence: float = 0.65,
        min_hours_between_trades: float = 4.0,
        max_trades_per_day: int = 5,
    ):
        self.tick_size = tick_size
        self.stop_ticks = stop_ticks
        self.target_ticks = target_ticks
        self.min_trend_strength = min_trend_strength
        self.min_confidence = min_confidence
        self.min_hours_between_trades = min_hours_between_trades
        self.max_trades_per_day = max_trades_per_day
        
        # Technical indicators
        self.indicators = TechnicalIndicators(tick_size=tick_size)
        
        # Trade tracking
        self.last_signal_time: float = 0.0
        self.signals_today: int = 0
        self.last_signal_date: str = ""
    
    def update(self, price: float, high: float | None = None, low: float | None = None) -> None:
        """
        Update indicators with new price data.
        
        Call this every 15 minutes (or when you want to check for signals).
        
        Args:
            price: Current price
            high: Bar high (optional)
            low: Bar low (optional)
        """
        self.indicators.update(price, high, low)
        
        # Reset daily counter at midnight
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        if today != self.last_signal_date:
            self.signals_today = 0
            self.last_signal_date = today
    
    def generate_signal(self) -> SwingTradeSignal | None:
        """
        Generate swing trade signal if conditions met.
        
        Returns:
            SwingTradeSignal if valid entry found, None otherwise
        """
        # Pre-flight checks
        if not self._preflight_checks():
            return None
        
        # Get current price
        if len(self.indicators.prices) == 0:
            return None
        current_price = self.indicators.prices[-1]
        
        # Analyze trend
        trend = self.indicators.analyze_trend()
        
        # Check minimum trend strength
        if trend.strength < self.min_trend_strength:
            return None
        
        # Check for entry signals based on trend direction
        if trend.direction == "BULL":
            signal = self._check_long_entry(current_price, trend)
        elif trend.direction == "BEAR":
            signal = self._check_short_entry(current_price, trend)
        else:
            return None
        
        # Update tracking if signal generated
        if signal:
            self.last_signal_time = time.time()
            self.signals_today += 1
        
        return signal
    
    def _preflight_checks(self) -> bool:
        """Check if we should even look for signals."""
        
        # 1. Check if enough data for indicators
        if not self.indicators.ready:
            return False
        
        # 2. Check time window (9:45 AM - 2:00 PM ET)
        now_et = datetime.now(ZoneInfo("America/New_York"))
        hour = now_et.hour
        minute = now_et.minute
        
        # Before 9:45 AM
        if hour < 9 or (hour == 9 and minute < 45):
            return False
        
        # After 2:00 PM
        if hour >= 14:
            return False
        
        # 3. Check cooldown (minimum time between signals)
        hours_since_last = (time.time() - self.last_signal_time) / 3600
        if hours_since_last < self.min_hours_between_trades:
            return False
        
        # 4. Check daily trade limit
        if self.signals_today >= self.max_trades_per_day:
            return False
        
        return True
    
    def _check_long_entry(self, price: float, trend: any) -> SwingTradeSignal | None:
        """
        Check for LONG entry opportunities in bullish trend.
        
        Entry types:
        1. Breakout above resistance
        2. Pullback to EMA20/EMA50 support
        3. Strong momentum continuation
        """
        # Type 1: Breakout Entry
        breakout = self.indicators.detect_breakout(lookback=20)
        if breakout.is_breakout and breakout.direction == "BULL":
            confidence = self._calculate_confidence(
                trend_strength=trend.strength,
                signal_strength=breakout.strength,
                higher_highs=trend.higher_highs,
                higher_lows=trend.higher_lows,
            )
            
            if confidence >= self.min_confidence:
                return SwingTradeSignal(
                    direction="LONG",
                    entry_price=price,
                    stop_ticks=self.stop_ticks,
                    target_ticks=self.target_ticks,
                    signal_type="BREAKOUT",
                    trend_strength=trend.strength,
                    confidence=confidence,
                    reason=f"Bullish breakout above {breakout.breakout_price:.2f}",
                    timestamp=time.time(),
                )
        
        # Type 2: Pullback Entry (preferred - better R:R)
        if self.indicators.is_pullback_to_ema(ema_period=20, tolerance_ticks=5):
            # Check if bouncing (momentum turning positive)
            if trend.momentum > -5:  # Not falling too fast
                confidence = self._calculate_confidence(
                    trend_strength=trend.strength,
                    signal_strength=0.8,  # Pullbacks are high quality
                    higher_highs=trend.higher_highs,
                    higher_lows=trend.higher_lows,
                )
                
                if confidence >= self.min_confidence:
                    return SwingTradeSignal(
                        direction="LONG",
                        entry_price=price,
                        stop_ticks=self.stop_ticks,
                        target_ticks=self.target_ticks,
                        signal_type="PULLBACK",
                        trend_strength=trend.strength,
                        confidence=confidence,
                        reason=f"Pullback to EMA20 support at {trend.ema20:.2f}",
                        timestamp=time.time(),
                    )
        
        # Type 3: Strong Momentum (continuation)
        if trend.momentum > 15:  # Strong upward momentum (15+ ticks/period)
            if trend.higher_highs and trend.higher_lows:
                confidence = self._calculate_confidence(
                    trend_strength=trend.strength,
                    signal_strength=0.75,
                    higher_highs=trend.higher_highs,
                    higher_lows=trend.higher_lows,
                )
                
                if confidence >= self.min_confidence:
                    return SwingTradeSignal(
                        direction="LONG",
                        entry_price=price,
                        stop_ticks=self.stop_ticks,
                        target_ticks=self.target_ticks,
                        signal_type="MOMENTUM",
                        trend_strength=trend.strength,
                        confidence=confidence,
                        reason=f"Strong bullish momentum ({trend.momentum:.1f} ticks/period)",
                        timestamp=time.time(),
                    )
        
        return None
    
    def _check_short_entry(self, price: float, trend: any) -> SwingTradeSignal | None:
        """
        Check for SHORT entry opportunities in bearish trend.
        
        Entry types:
        1. Breakdown below support
        2. Rally to EMA20/EMA50 resistance
        3. Strong downward momentum
        """
        # Type 1: Breakdown Entry
        breakout = self.indicators.detect_breakout(lookback=20)
        if breakout.is_breakout and breakout.direction == "BEAR":
            confidence = self._calculate_confidence(
                trend_strength=trend.strength,
                signal_strength=breakout.strength,
                higher_highs=not trend.higher_highs,  # Lower highs for shorts
                higher_lows=not trend.higher_lows,    # Lower lows for shorts
            )
            
            if confidence >= self.min_confidence:
                return SwingTradeSignal(
                    direction="SHORT",
                    entry_price=price,
                    stop_ticks=self.stop_ticks,
                    target_ticks=self.target_ticks,
                    signal_type="BREAKOUT",
                    trend_strength=trend.strength,
                    confidence=confidence,
                    reason=f"Bearish breakdown below {breakout.breakout_price:.2f}",
                    timestamp=time.time(),
                )
        
        # Type 2: Pullback Entry (rally to resistance)
        if self.indicators.is_pullback_to_ema(ema_period=20, tolerance_ticks=5):
            # Check if rejecting (momentum turning negative)
            if trend.momentum < 5:  # Not rising too fast
                confidence = self._calculate_confidence(
                    trend_strength=trend.strength,
                    signal_strength=0.8,
                    higher_highs=False,  # Want lower highs
                    higher_lows=False,   # Want lower lows
                )
                
                if confidence >= self.min_confidence:
                    return SwingTradeSignal(
                        direction="SHORT",
                        entry_price=price,
                        stop_ticks=self.stop_ticks,
                        target_ticks=self.target_ticks,
                        signal_type="PULLBACK",
                        trend_strength=trend.strength,
                        confidence=confidence,
                        reason=f"Rally to EMA20 resistance at {trend.ema20:.2f}",
                        timestamp=time.time(),
                    )
        
        # Type 3: Strong Downward Momentum
        if trend.momentum < -15:  # Strong downward momentum
            confidence = self._calculate_confidence(
                trend_strength=trend.strength,
                signal_strength=0.75,
                higher_highs=False,
                higher_lows=False,
            )
            
            if confidence >= self.min_confidence:
                return SwingTradeSignal(
                    direction="SHORT",
                    entry_price=price,
                    stop_ticks=self.stop_ticks,
                    target_ticks=self.target_ticks,
                    signal_type="MOMENTUM",
                    trend_strength=trend.strength,
                    confidence=confidence,
                    reason=f"Strong bearish momentum ({trend.momentum:.1f} ticks/period)",
                    timestamp=time.time(),
                )
        
        return None
    
    def _calculate_confidence(
        self,
        trend_strength: float,
        signal_strength: float,
        higher_highs: bool,
        higher_lows: bool,
    ) -> float:
        """
        Calculate overall signal confidence (0.0 to 1.0).
        
        Components:
        - Trend strength (40%)
        - Signal strength (30%)
        - Price structure (30%)
        """
        # Base confidence from trend
        confidence = trend_strength * 0.4
        
        # Add signal strength
        confidence += signal_strength * 0.3
        
        # Add price structure bonus
        if higher_highs and higher_lows:
            confidence += 0.3  # Perfect bull structure
        elif higher_highs or higher_lows:
            confidence += 0.15  # Partial structure
        
        return min(1.0, round(confidence, 3))
    
    @property
    def can_trade_now(self) -> bool:
        """Check if current time is within trading window."""
        now_et = datetime.now(ZoneInfo("America/New_York"))
        hour = now_et.hour
        minute = now_et.minute
        
        # Trading window: 9:45 AM - 2:00 PM ET
        if hour < 9 or (hour == 9 and minute < 45):
            return False
        if hour >= 14:
            return False
        
        return True
    
    @property
    def signals_today_count(self) -> int:
        """Get number of signals generated today."""
        return self.signals_today
