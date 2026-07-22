# FutureMathics Swing Trading System - Professional Design
**Date**: July 22, 2026  
**Goal**: 2-5 trades/day, $500/day profit, 4-hour to 1-day holds  
**Strategy**: Trend following, breakouts, momentum

---

## 🎯 TARGET PERFORMANCE

| Metric | Target |
|--------|--------|
| **Trades per day** | 2-5 |
| **Daily profit** | $500+ |
| **Profit per trade** | $100-250 |
| **Win rate** | 60-70% |
| **Hold time** | 4 hours - 1 day |
| **R:R ratio** | 2:1 minimum |
| **Max positions** | 1-2 concurrent |

---

## 📊 SWING TRADING LOGIC

### **1. Trend Detection**

Use multiple timeframe analysis:

```python
# 20-period EMA (fast trend)
# 50-period EMA (medium trend)  
# 200-period EMA (long-term trend)

# BULLISH TREND:
# - Price > EMA20 > EMA50 > EMA200
# - Recent higher highs and higher lows
# - Momentum positive

# BEARISH TREND:
# - Price < EMA20 < EMA50 < EMA200
# - Recent lower highs and lower lows
# - Momentum negative
```

### **2. Entry Signals**

**Type A: Breakout Entry**
- Price breaks above recent high (20-period) with volume
- Trend already established (EMAs aligned)
- Enter on breakout + 2-3 ticks

**Type B: Pullback Entry** (Preferred)
- Strong trend established
- Price pulls back to EMA20 or EMA50
- Enter when price bounces off EMA with momentum

**Type C: Momentum Entry**
- Strong directional move (30+ ticks in one direction)
- Momentum accelerating
- Enter continuation with trend

### **3. Position Sizing**

```python
# For $100-250 profit target with 60-80 tick stop:
# Risk: $75-100 per trade
# Reward: $150-250 per trade (2:1 minimum R:R)

contracts = 1  # Start with 1 contract
stop_ticks = 60  # 60 ticks = 15 points = $75 risk
target_ticks = 120  # 120 ticks = 30 points = $150 profit (2:1 R:R)

# For $250 profit:
target_ticks = 200  # 200 ticks = 50 points = $250
stop_ticks = 80     # 80 ticks = 20 points = $100 risk (2.5:1 R:R)
```

### **4. Exit Strategy**

**Multi-tier exit system:**

```python
# Tier 1: Fixed profit target (primary)
if profit >= target_ticks:
    EXIT("profit_target")

# Tier 2: Trailing stop (after 50% to target)
if profit >= target_ticks * 0.5:
    # Activate trailing stop at 30 ticks behind peak
    trailing_stop = peak_profit - 30

# Tier 3: Time-based minimum hold
if time_in_position < 4 hours:
    # Don't exit early unless stop hit or major reversal

# Tier 4: End-of-day exit
if time_now > 3:30 PM ET and not hit_target:
    EXIT("end_of_day")

# Tier 5: Hard stop loss
if loss >= stop_ticks:
    EXIT("stop_loss")
```

### **5. Trade Frequency Control**

```python
# Minimum time between entries
MIN_HOURS_BETWEEN_TRADES = 4

# Maximum trades per day
MAX_TRADES_PER_DAY = 5

# Only check for signals every 15 minutes (not every 2 seconds)
SIGNAL_CHECK_INTERVAL = 900  # 15 minutes

# No new positions after 2 PM ET (avoid late-day risk)
NO_NEW_ENTRIES_AFTER = 14  # 2 PM ET
```

---

## 🏗️ ARCHITECTURE CHANGES

### **New Components:**

1. **`celine/swing_signals.py`** - Swing trading signal engine
   - Trend detection (EMAs, higher highs/lows)
   - Breakout detection
   - Pullback detection
   - Momentum analysis

2. **`celine/swing_position_manager.py`** - Advanced position management
   - Multi-hour position tracking
   - Trailing stop logic
   - Time-based hold enforcement
   - End-of-day exit logic

3. **`celine/technical_indicators.py`** - TA-Lib style indicators
   - EMA calculations
   - Higher highs / lower lows detection
   - Momentum indicators
   - Volume analysis (if available)

4. **`engine/swing_orchestrator.py`** - Swing-optimized orchestrator
   - 15-minute cycle intervals (not 2 seconds)
   - Position holding logic
   - Daily trade count tracking
   - Time window enforcement

---

## 📐 MATHEMATICAL FOUNDATIONS

### **1. Exponential Moving Average (EMA)**

```python
def calculate_ema(prices: deque, period: int) -> float:
    """
    EMA = Price(t) × k + EMA(y) × (1 - k)
    where k = 2 / (period + 1)
    """
    if len(prices) < period:
        return sum(prices) / len(prices)  # SMA fallback
    
    k = 2.0 / (period + 1)
    ema = prices[0]  # Start with first price
    
    for price in list(prices)[1:]:
        ema = price * k + ema * (1 - k)
    
    return ema
```

### **2. Trend Strength Score**

```python
def calculate_trend_strength(ema20, ema50, ema200, price) -> float:
    """
    Score: 0.0 (no trend) to 1.0 (very strong trend)
    """
    # EMA alignment (0-0.4 points)
    if price > ema20 > ema50 > ema200:
        alignment_score = 0.4  # Bullish alignment
    elif price < ema20 < ema50 < ema200:
        alignment_score = 0.4  # Bearish alignment
    else:
        alignment_score = 0.0  # No alignment
    
    # EMA separation (0-0.3 points)
    separation = abs(ema20 - ema50) / ema50
    separation_score = min(0.3, separation * 10)
    
    # Price momentum (0-0.3 points)
    distance_from_ema20 = abs(price - ema20) / ema20
    momentum_score = min(0.3, distance_from_ema20 * 20)
    
    return alignment_score + separation_score + momentum_score
```

### **3. Breakout Detection**

```python
def detect_breakout(price, high_20, low_20, trend_direction) -> bool:
    """
    Breakout = Price exceeds recent range with momentum
    """
    breakout_threshold = 5  # ticks beyond high/low
    
    if trend_direction == "BULL":
        if price >= high_20 + (breakout_threshold * TICK_SIZE):
            return True
    elif trend_direction == "BEAR":
        if price <= low_20 - (breakout_threshold * TICK_SIZE):
            return True
    
    return False
```

---

## 🎯 ENTRY CRITERIA (All Must Pass)

### **LONG Entry:**
1. ✅ Trend: Price > EMA20 > EMA50 (bullish alignment)
2. ✅ Momentum: Price rising (last 5 bars mostly green)
3. ✅ Signal: Breakout OR pullback to EMA20
4. ✅ Timing: Between 9:45 AM - 2:00 PM ET
5. ✅ Frequency: > 4 hours since last trade
6. ✅ Daily limit: < 5 trades today
7. ✅ Trend strength: > 0.60 score

### **SHORT Entry:**
1. ✅ Trend: Price < EMA20 < EMA50 (bearish alignment)
2. ✅ Momentum: Price falling (last 5 bars mostly red)
3. ✅ Signal: Breakdown OR rally to EMA20
4. ✅ Timing: Between 9:45 AM - 2:00 PM ET
5. ✅ Frequency: > 4 hours since last trade
6. ✅ Daily limit: < 5 trades today
7. ✅ Trend strength: > 0.60 score

---

## 🛡️ RISK MANAGEMENT

### **Position Sizing:**
```python
# Conservative: 1 contract
# Moderate: 2 contracts (after proven 70% win rate)
# Aggressive: 3 contracts (never exceed)

# For 1 contract:
STOP_TICKS = 60     # $75 risk
TARGET_TICKS = 120  # $150 profit (2:1 R:R)

# For 2 contracts (scale out):
# Exit 1st contract at +80 ticks ($100)
# Trail 2nd contract to +200 ticks ($250)
```

### **Daily Limits:**
```python
MAX_DAILY_LOSS = $200    # Stop trading if hit
MAX_DAILY_TRADES = 5     # Cap at 5 trades regardless of outcome
MAX_CONCURRENT_POSITIONS = 1  # One position at a time to start
```

### **Time-based Risk:**
```python
# No new trades after 2 PM (avoid late-day volatility)
# Force close all positions by 3:50 PM (before close)
# Minimum 4-hour hold (don't exit prematurely)
```

---

## 📊 EXPECTED PERFORMANCE

### **Conservative Scenario (60% win rate):**
```
Trades: 3/day × 20 trading days = 60 trades/month
Wins: 36 × $150 = $5,400
Losses: 24 × $75 = $1,800
Net profit: $3,600/month
```

### **Target Scenario (70% win rate):**
```
Trades: 4/day × 20 trading days = 80 trades/month
Wins: 56 × $175 = $9,800
Losses: 24 × $75 = $1,800
Net profit: $8,000/month
```

### **Optimistic Scenario (70% win rate + scaling):**
```
Trades: 5/day × 20 trading days = 100 trades/month
Wins: 70 trades
  - 50% scaled at $150 = 35 × $150 = $5,250
  - 50% runners at $250 = 35 × $250 = $8,750
Losses: 30 × $75 = $2,250
Net profit: $11,750/month
```

---

## 🔧 IMPLEMENTATION STEPS

### **Phase 1: Core Components** (1-2 hours)
1. Create `technical_indicators.py` (EMA, trend, breakout)
2. Create `swing_signals.py` (entry logic)
3. Create `swing_position_manager.py` (holding logic)
4. Update `config.py` with swing parameters

### **Phase 2: Orchestration** (30 min)
1. Modify `futures_orchestrator.py` for 15-min cycles
2. Add daily trade tracking
3. Add time window enforcement
4. Add trailing stop logic

### **Phase 3: Testing** (30 min)
1. Test locally with ignore-hours mode
2. Verify signals generate 2-5x per day
3. Verify 4-hour minimum holds
4. Verify profit targets hit correctly

### **Phase 4: Deployment** (15 min)
1. Deploy to AWS
2. Monitor first day
3. Adjust parameters if needed

---

## 🎯 SUCCESS METRICS (Week 1)

| Metric | Target | Status |
|--------|--------|--------|
| Trades per day | 2-5 | [ ] |
| Win rate | 60%+ | [ ] |
| Avg profit/trade | $100+ | [ ] |
| Daily profit | $300-700 | [ ] |
| Hold time | 4+ hours | [ ] |
| Max drawdown | < $500 | [ ] |

---

## 🚀 READY TO BUILD

**Estimated time**: 2-3 hours total
**Complexity**: Moderate (new strategy but clean architecture)
**Risk**: Low (paper mode first, validated before live)

**Let's build it!** 💪
