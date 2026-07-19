# Win Rate Improvement Strategies for FutureMathics

## Current State Analysis

**Metrics:**
- Win Rate: ~35% (estimated)
- Trade Frequency: 2,687 trades/day
- R:R Ratio: 2:1 (8 tick stop / 16 tick target)
- Entry Threshold: 4 ticks from VWAP
- **Break-even requirement:** 33.3% win rate

**Verdict:** Barely profitable, likely overtrading

---

## 🎯 Improvement Strategy Ranking

### Priority 1: Reduce Trade Frequency (Biggest Impact)

**Current Problem:**
- 2,687 trades/day = one trade every 32 seconds
- Taking every signal without quality filter
- Low confidence trades dilute performance

**Solution Options:**

#### Option A: Increase VWAP Threshold (Easy - High Impact)
```python
# In engine/config.py
VWAP_ENTRY_THRESHOLD_TICKS = 8  # Was 4 (double the threshold)
```

**Expected Impact:**
- Reduces trade count by 50-70%
- Filters out noise and weak signals
- Takes only stronger VWAP deviations
- Win rate should increase to 45-55%

#### Option B: Add Confidence Minimum (Easy - Medium Impact)
```python
# In engine/futures_orchestrator.py, in run_cycle():
# After signal is generated, add:

if signal.confidence < 0.65:  # Only take high-confidence signals
    await self._persist_state()
    return {"action": "SKIP", "reason": "low_confidence"}
```

**Expected Impact:**
- Reduces trades by 30-50%
- Filters weak VWAP signals
- Win rate increase: 40-50%

#### Option C: Minimum Time Between Trades (Easy - High Impact)
```python
# Add to SessionState in futures_orchestrator.py:
last_trade_time: float = 0.0

# In run_cycle(), before opening position:
import time
now = time.time()
if now - self.session.last_trade_time < 60:  # Min 60s between trades
    return {"action": "SKIP", "reason": "cooldown_period"}

self.session.last_trade_time = now
```

**Expected Impact:**
- Caps frequency at 1 trade/minute
- Prevents overtrading same moves
- Expected trades: ~300/day (vs 2,687)
- Win rate increase: 40-55%

---

### Priority 2: Improve Exit Management

#### Option A: Tighten Target (Easier Wins)
```python
# In engine/config.py
DEFAULT_TARGET_TICKS = 12  # Was 16 (1.5:1 R:R instead of 2:1)
```

**Impact:**
- Easier to hit target (closer price)
- Win rate increase: +10-15%
- Trade-off: Lower profit per winner
- Break-even win rate: 40% (vs 33%)

**Analysis:**
- With 45% win rate and 1.5:1 R:R:
  - (0.45 × $45) - (0.55 × $30) = $3.75 per trade
- With 35% win rate and 2:1 R:R:
  - (0.35 × $60) - (0.65 × $30) = $1.50 per trade
- **Better expectancy even though R:R is lower!**

#### Option B: Trailing Stops (Lock Profits)
```python
# Add to position_management.py:
def evaluate_exit_with_trailing(
    *,
    last_price: float,
    levels: ExitLevels,
    peak_price: float,  # Track highest price for LONG
    trail_ticks: int = 4,
) -> str | None:
    """Exit with trailing stop once in profit."""
    d = levels.direction.upper()
    
    if d == "LONG":
        # Original stop loss
        if last_price <= levels.stop_price:
            return ExitReason.STOP_LOSS
        
        # Trailing stop if we're past breakeven
        if peak_price > levels.entry_price:
            trail_price = peak_price - (trail_ticks * TICK_SIZE)
            if last_price <= trail_price:
                return "TRAILING_STOP"
        
        # Target
        if last_price >= levels.target_price:
            return ExitReason.TAKE_PROFIT
    
    # Similar for SHORT...
    return None
```

**Expected Impact:**
- Protects profits when trade moves favorably
- Win rate increase: +5-10%
- Reduces "gave back profit" trades

---

### Priority 3: Add Quality Filters

#### Option A: Trend Filter (Don't Fade Strong Trends)
```python
# Add to FuturesSignalEngine in signals.py:
from collections import deque

@dataclass
class FuturesSignalEngine:
    # ... existing fields ...
    recent_prices: deque = field(default_factory=lambda: deque(maxlen=20))
    
    def _calculate_trend_strength(self) -> float:
        """Calculate price momentum over last 20 ticks."""
        if len(self.recent_prices) < 20:
            return 0.0
        first = self.recent_prices[0]
        last = self.recent_prices[-1]
        return (last - first) / TICK_SIZE
    
    def ingest_tick(self, *, price: float, ...):
        self.recent_prices.append(price)
        # ... rest of existing code ...
    
    def build_trade_signal(self, snapshot: FuturesSnapshot):
        # ... existing code ...
        
        # NEW: Check if we're fading a strong trend (bad idea!)
        trend_strength = self._calculate_trend_strength()
        
        # Don't short into strong uptrend
        if direction == SignalDirection.SHORT and trend_strength > 15:
            return None
        
        # Don't long into strong downtrend  
        if direction == SignalDirection.LONG and trend_strength < -15:
            return None
        
        # ... rest of existing code ...
```

**Expected Impact:**
- Avoids counter-trend trades in strong moves
- Win rate increase: +5-10%
- Reduces brutal stop-outs

#### Option B: Time-of-Day Filter
```python
# In run_daily_session.py or orchestrator:
def is_high_quality_hours() -> bool:
    """Trade only during high-volume periods."""
    now = datetime.now(ZoneInfo("America/New_York"))
    hour = now.hour
    
    # Avoid:
    # - Late night (low volume): 11 PM - 7 AM ET
    # - Lunch lull: 11:30 AM - 1:30 PM ET
    if 23 <= hour or hour < 7:
        return False
    if 11 <= hour < 14:  # Lunch period
        return False
    
    return True

# In run_cycle(), before generating signals:
if not is_high_quality_hours():
    return {"action": "SKIP", "reason": "outside_prime_hours"}
```

**Expected Impact:**
- Trades only high-volume periods
- Better liquidity, tighter spreads
- Win rate increase: +3-5%

---

### Priority 4: Dynamic Stop/Target Sizing

#### Use ATR-Based Stops (Adaptive)
```python
# Add ATR calculation to signals.py:
def calculate_atr(prices: deque, period: int = 14) -> float:
    """Simple ATR approximation from price ranges."""
    if len(prices) < period:
        return 0.25 * 5  # Default to 5 ticks
    
    ranges = []
    for i in range(len(prices) - 1):
        ranges.append(abs(prices[i+1] - prices[i]))
    
    return sum(ranges[-period:]) / period

# In config.py, make stops adaptive:
def calculate_stop_ticks(atr: float) -> int:
    """Stop = 2.5 × ATR, minimum 6 ticks."""
    atr_ticks = atr / TICK_SIZE
    return max(6, int(atr_ticks * 2.5))

def calculate_target_ticks(stop_ticks: int) -> int:
    """Maintain 1.5:1 or 2:1 R:R."""
    return stop_ticks * 2  # or * 1.5 for easier wins
```

**Expected Impact:**
- Wider stops in volatile periods (fewer false stops)
- Tighter stops in calm periods (better risk)
- Win rate increase: +5-8%

---

## 🎯 Recommended Implementation Plan

### Phase 1: Quick Wins (This Weekend)

**1. Increase VWAP threshold to 8 ticks**
```python
# engine/config.py
VWAP_ENTRY_THRESHOLD_TICKS = 8  # Was 4
```

**2. Add minimum time between trades**
```python
# Add 60-second cooldown in orchestrator
```

**3. Reduce target to 12 ticks (1.5:1 R:R)**
```python
# engine/config.py
DEFAULT_TARGET_TICKS = 12  # Was 16
```

**Expected Results:**
- Trade count: 2,687 → ~300-400/day
- Win rate: 35% → 45-55%
- Better expectancy

### Phase 2: Medium-Term (Next Week)

**4. Add trend filter**
- Avoid counter-trend trades in strong moves

**5. Add time-of-day filter**
- Trade only prime hours

**Expected Results:**
- Win rate: 55% → 60-65%
- More consistent performance

### Phase 3: Advanced (Future)

**6. Implement trailing stops**
**7. ATR-based dynamic stops**
**8. Volume confirmation**

---

## 📊 Expected Performance After Improvements

### Before (Current):
- Trades: 2,687/day
- Win Rate: 35%
- Avg P&L: $1.61/trade
- Daily P&L: $4,338

### After Phase 1:
- Trades: 300-400/day
- Win Rate: 45-55%
- Avg P&L: $4-6/trade
- Daily P&L: $1,200-2,400 (more sustainable)

### After Phase 2:
- Trades: 150-250/day
- Win Rate: 55-65%
- Avg P&L: $6-10/trade
- Daily P&L: $900-2,500 (very consistent)

---

## ⚠️ Important Notes

**Don't Overfit:**
- Make one change at a time
- Test for at least 5-10 trading days
- Use trade history database to track impact
- Compare before/after metrics

**Quality > Quantity:**
- Fewer, higher-quality trades is better
- 100 trades at 60% win rate > 2,687 at 35%
- Lower stress, better risk management

**Monitor These Metrics:**
```python
# After each change, check:
- Win rate (should increase)
- Trade count (should decrease)
- Profit factor (should improve)
- Expectancy (should increase)
- Daily P&L consistency (should improve)
```

---

## 🔧 Testing Procedure

1. **Implement change in code**
2. **Backtest on AWS logs** (use parse_aws_logs.py)
3. **Deploy to paper trading**
4. **Run for 5-10 days**
5. **Analyze with trade history:**
   ```python
   python scripts/analyze_performance.py
   ```
6. **Compare metrics before/after**
7. **Keep if improved, revert if worse**

---

## 📈 Quick Implementation: Phase 1 Changes

I can implement the Phase 1 changes right now if you want immediate improvements:
1. VWAP threshold 4→8 ticks
2. 60-second trade cooldown  
3. Target 16→12 ticks (1.5:1 R:R)

These three changes alone should:
- Cut trades by 80%: 2,687 → ~400/day
- Increase win rate: 35% → 50%+
- Improve consistency and sustainability

Ready to implement?
