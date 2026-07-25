# FutureMathics Debug Checklist - System Not Trading
**Date**: July 21, 2026, 9:00 PM ET  
**Issue**: System traded yesterday ($237 profit) but ZERO trades today on major bullish day  
**Priority**: 🔴 CRITICAL - Going live next week

---

## 🚨 CRITICAL ISSUE

**Symptom**: System is deployed and running, but not trading when it should  
**Impact**: Missing profitable trades, system not functioning as intended  
**Timeline**: Need fix immediately - going live with real money next week

---

## 🔍 POSSIBLE ROOT CAUSES

### 1. **Alpaca SPY Data Issue** (MOST LIKELY)
**Problem**: Alpaca SPY feed only works during stock market hours (9:30 AM - 4 PM ET)

**Check**:
```bash
# On AWS server, check logs:
sudo journalctl -u futuremathics -n 100 | grep -i alpaca

# Look for:
# ✅ "Alpaca SPY feed enabled"
# ❌ "Alpaca SPY quote failed"
# ❌ "alpaca_fallback"
```

**If Alpaca fails outside SPY hours**: System should fall back to sim, but might not be generating signals

---

### 2. **Filters Too Aggressive**
**Problem**: Phase 1 improvements might be blocking ALL signals

**Check**:
```bash
# On AWS, check skip reasons:
sudo journalctl -u futuremathics --since today | grep -i "skip"

# Look for common skips:
# - "neutral_regime" (price not far enough from VWAP)
# - "low_confidence" (confidence < 60%)
# - "trade_cooldown_active" (60s cooldown)
# - "spread_too_wide" (bid-ask spread > 2 ticks)
# - "max_open_positions" (already have position)
```

**Likely culprit**: With 8-tick VWAP threshold + 60% confidence + trend filter, very few signals pass

---

### 3. **System Halted**
**Problem**: Daily loss limit hit, system shut itself down

**Check**:
```bash
# Check for HALT messages:
sudo journalctl -u futuremathics --since today | grep -i halt

# Check daily P&L:
sudo journalctl -u futuremathics --since today | grep "daily_pnl"
```

**If halted**: System hit 2% daily loss ($2,000) and stopped trading for the day

---

### 4. **Market Hours Mismatch**
**Problem**: MES futures hours vs SPY stock hours mismatch

**Current logic**:
- MES futures: Sunday 6 PM - Friday 5 PM ET (almost 24/7)
- SPY stocks: Monday-Friday 9:30 AM - 4 PM ET only
- System checks MES hours (OK) but Alpaca data only works during SPY hours

**Result**: 
- Outside SPY hours → No Alpaca data → Falls back to sim
- Sim might not generate VWAP signals properly

---

### 5. **VWAP Threshold Too High**
**Problem**: 8-tick threshold means price must be $2.00 away from VWAP to trigger

**On a choppy day**: Price might oscillate but never get 8 ticks away from VWAP

**Yesterday ($237 profit)**: Might have had stronger trends, hit 8-tick threshold
**Today (no trades)**: Might be ranging market, never hit threshold

---

### 6. **Confidence Filter Blocking Everything**
**Problem**: 60% minimum confidence + volatility adjustment

**Check formula** (from signals.py):
```python
# Base confidence: 0.65 to 0.89 for 8-16 tick distance
# Adjusted by volatility: confidence × (1 / (1 + vol/10))
# Must be ≥ 0.60 to trade

# On high volatility day:
# base = 0.75, vol = 15 ticks
# adjusted = 0.75 × (1 / (1 + 15/10)) = 0.75 × 0.40 = 0.30
# Result: BLOCKED (< 0.60)
```

**If today was volatile**: All signals might be getting blocked by volatility adjustment

---

### 7. **Trend Filter Blocking Trades**
**Problem**: Linear regression trend filter preventing entries

**Logic** (from signals.py):
```python
# Calculate 30-period momentum
# Don't LONG if momentum < -12 ticks (strong downtrend)
# Don't SHORT if momentum > +12 ticks (strong uptrend)
```

**On strong bullish day**:
- Momentum > +12 → SHORT signals blocked ✅ (correct)
- But LONG signals should work... unless price never gets 8 ticks BELOW VWAP
- In strong uptrend, price stays ABOVE VWAP (BULL regime)
- System wants to LONG, but price is above VWAP, not below
- Result: No signals!

**THIS MIGHT BE THE BUG!** 🚨

---

## 🎯 MOST LIKELY CAUSE

### **Signal Logic Issue on Trending Days**

**The Problem**:
```python
# From signals.py:
if dist_ticks >= VWAP_ENTRY_THRESHOLD_TICKS:  # Price > VWAP + 8 ticks
    regime = TrendRegime.BULL
    direction = SignalDirection.LONG
    
elif dist_ticks <= -VWAP_ENTRY_THRESHOLD_TICKS:  # Price < VWAP - 8 ticks
    regime = TrendRegime.BEAR
    direction = SignalDirection.SHORT
```

**On a STRONG BULL DAY**:
1. Price trends up strongly
2. Price stays consistently ABOVE VWAP (bullish)
3. System says: "BULL regime, want to LONG"
4. **BUT**: LONG signal requires price to be ABOVE VWAP + 8 ticks
5. This is a **reversion signal** (price too high, expect pullback)
6. On trending day, price might climb gradually without getting "too high"
7. **Result: NO SIGNALS GENERATED**

**This is a MEAN REVERSION strategy, not a TREND FOLLOWING strategy!**

---

## 🔧 IMMEDIATE FIXES TO TEST

### Option 1: Reduce VWAP Threshold (Quick Fix)
```python
# In engine/config.py on AWS:
VWAP_ENTRY_THRESHOLD_TICKS = 4  # Back to original (was 8)

# This will:
# - Generate more signals (less selective)
# - Catch trending moves earlier
# - Increase trade count
```

**Risk**: Might bring back overtrading

---

### Option 2: Reduce Confidence Threshold (Quick Fix)
```python
# In engine/config.py on AWS:
MIN_CONFIDENCE_THRESHOLD = 0.45  # Lower from 0.60

# This will:
# - Allow medium-confidence trades
# - Not block on volatile days
```

**Risk**: Might reduce win rate slightly

---

### Option 3: Add Trend Following Logic (Better Fix)
```python
# In celine/signals.py:
# Add ALTERNATIVE signal mode:

# MEAN REVERSION (current):
# - Price > VWAP + 8 → LONG (expecting pullback to VWAP)
# - Price < VWAP - 8 → SHORT (expecting bounce to VWAP)

# TREND FOLLOWING (new):
# - Strong uptrend + price crosses ABOVE VWAP → LONG (ride the trend)
# - Strong downtrend + price crosses BELOW VWAP → SHORT (ride the trend)
```

---

### Option 4: Disable Specific Filters Temporarily
```python
# In engine/futures_orchestrator.py:
# Comment out these lines to test:

# Line 226-229: Confidence check
# if signal.confidence < 0.60:
#     return {"action": "SKIP", "reason": "low_confidence"}

# Line 240-245: Trade cooldown
# if now - self.session.last_trade_time < MIN_SECONDS_BETWEEN_TRADES:
#     return {"action": "SKIP", "reason": "trade_cooldown_active"}
```

**Test one at a time to isolate the blocker**

---

## 📊 DIAGNOSTIC COMMANDS

### On AWS Server:

```bash
# 1. Check if system is running
sudo systemctl status futuremathics

# 2. Check today's logs
sudo journalctl -u futuremathics --since "2026-07-21 09:30:00" --until "2026-07-21 16:00:00"

# 3. Count skip reasons
sudo journalctl -u futuremathics --since today | grep "SKIP" | grep "reason" | cut -d'"' -f4 | sort | uniq -c

# 4. Check if ANY signals were generated
sudo journalctl -u futuremathics --since today | grep "CELINE SIGNAL"

# 5. Check VWAP distances
sudo journalctl -u futuremathics --since today | grep "vwap" | head -20

# 6. Check confidence scores
sudo journalctl -u futuremathics --since today | grep "confidence"

# 7. Check Alpaca feed status
sudo journalctl -u futuremathics --since today | grep -i alpaca | head -10

# 8. Check if halted
sudo journalctl -u futuremathics --since today | grep -i "halt"

# 9. Check daily P&L
sudo journalctl -u futuremathics --since today | grep "daily_pnl" | tail -5
```

---

## 🎯 STEP-BY-STEP DEBUG PROCESS

### Step 1: Connect to AWS
```powershell
# From local machine:
$key = "C:\MarketMathics.ai\MarketMathics.pem"
$remote = "ubuntu@54.91.152.140"
ssh -i $key $remote
```

### Step 2: Check System Status
```bash
cd /home/ubuntu/FutureMathics
sudo systemctl status futuremathics

# Look for:
# ✅ Active: active (running)
# ❌ Active: failed
```

### Step 3: Analyze Today's Logs
```bash
# Get all today's activity:
sudo journalctl -u futuremathics --since today > ~/today_logs.txt

# Count cycles:
grep "CYCLE START" ~/today_logs.txt | wc -l
# Should see hundreds if running all day

# Count signals:
grep "CELINE SIGNAL" ~/today_logs.txt | wc -l
# Should see many if price data working

# Count skips:
grep "SKIP" ~/today_logs.txt | wc -l
# High number = filters blocking trades

# Count trades:
grep "PAPER_ROUTE\|LIVE_ROUTE" ~/today_logs.txt | wc -l
# Should be > 0 if system working
```

### Step 4: Find Most Common Skip Reason
```bash
grep "SKIP" ~/today_logs.txt | grep "reason" | sed 's/.*reason.*: //' | sort | uniq -c | sort -rn

# Example output:
# 1847 neutral_regime         ← Price within 8 ticks of VWAP
#  523 low_confidence         ← Confidence < 60%
#   89 trade_cooldown_active  ← 60s cooldown
#   12 spread_too_wide        ← Spread > 2 ticks
```

### Step 5: Check VWAP Distance
```bash
# See how far price was from VWAP:
grep "vwap" ~/today_logs.txt | grep "regime" | head -50

# Look for lines like:
# vwap: 5642.25, price: 5645.50, regime: NEUTRAL
#                        ^^^^ Only 3.25 points = 13 ticks
# If always < 8 ticks away → No signals generated
```

### Step 6: Check Confidence Scores
```bash
# If signals were generated, check confidence:
grep "confidence" ~/today_logs.txt | head -20

# Look for:
# confidence: 0.42 ← BLOCKED (< 0.60)
# confidence: 0.68 ← ALLOWED
```

### Step 7: Check Alpaca Data
```bash
# Verify real data flowing:
grep "alpaca" ~/today_logs.txt | head -10

# Should see:
# ✅ "alpaca_spy_feed_ok | alpaca_ok spy=598.25"
# ❌ "Alpaca SPY quote failed"
```

---

## 🚀 IMMEDIATE ACTION PLAN

### If "neutral_regime" is dominant skip reason:

**Root cause**: 8-tick VWAP threshold too strict for today's market

**Fix**:
```bash
cd /home/ubuntu/FutureMathics

# Edit config
nano engine/config.py

# Change line:
VWAP_ENTRY_THRESHOLD_TICKS = 4  # Was 8

# Save and restart
sudo systemctl restart futuremathics

# Monitor
sudo journalctl -u futuremathics -f
```

---

### If "low_confidence" is dominant skip reason:

**Root cause**: Confidence threshold or volatility adjustment too strict

**Fix**:
```bash
# Option A: Lower confidence threshold
nano engine/config.py
# Change: MIN_CONFIDENCE_THRESHOLD = 0.50  # Was 0.60

# Option B: Reduce volatility penalty
nano celine/signals.py
# Find: vol_factor = 1.0 / (1.0 + volatility / 10.0)
# Change to: vol_factor = 1.0 / (1.0 + volatility / 20.0)
# (Less volatility penalty)

sudo systemctl restart futuremathics
```

---

### If "trade_cooldown_active" is dominant:

**Root cause**: 60-second cooldown blocking rapid signals

**Fix**:
```bash
nano engine/config.py
# Change: MIN_SECONDS_BETWEEN_TRADES = 30  # Was 60

sudo systemctl restart futuremathics
```

---

### If Alpaca data failing:

**Root cause**: API issues or credentials

**Fix**:
```bash
# Test Alpaca manually:
python3 scripts/test_alpaca_spy_feed.py

# If fails, check credentials:
cat .env.local | grep ALPACA

# If working but system not using it:
sudo systemctl restart futuremathics
```

---

## 📈 VALIDATION

After fix, monitor for 30 minutes:

```bash
sudo journalctl -u futuremathics -f

# Should see:
# ✅ CELINE SIGNAL (signals being generated)
# ✅ MANUS RISK: verdict=APPROVE (risk checks passing)
# ✅ PAPER_ROUTE or LIVE_ROUTE (trades executing)
# ✅ confidence: 0.XX (with various values)
```

---

## 🎯 QUICK EMERGENCY FIX

**If you need trades RIGHT NOW** and can't wait for diagnosis:

```bash
# Temporarily disable strict filters:
cd /home/ubuntu/FutureMathics

# Create emergency config override:
nano .env.local

# Add:
FM_EMERGENCY_MODE=1

# Then edit run script to check this flag
# Or manually change configs to less strict values:

# engine/config.py:
VWAP_ENTRY_THRESHOLD_TICKS = 4  # Back to original
MIN_CONFIDENCE_THRESHOLD = 0.40  # Very permissive
MIN_SECONDS_BETWEEN_TRADES = 15  # Allow more frequent trades

sudo systemctl restart futuremathics
```

**Warning**: This will increase trade frequency. Monitor closely.

---

## 📊 ROOT CAUSE HYPOTHESIS

Based on symptoms (yesterday worked, today didn't on bull day):

**Most likely**: VWAP threshold (8 ticks) works for volatile/choppy days but fails on smooth trending days

- **Yesterday**: Choppy volatility → Price swings 8+ ticks from VWAP → Signals generated → $237 profit
- **Today**: Smooth bullish trend → Price rises steadily → Never gets 8 ticks from VWAP → No signals

**Solution**: Dynamic threshold based on market conditions, OR use smaller threshold (4-6 ticks)

---

## ⏰ TIMELINE

1. **Immediate** (next 30 min): Run diagnostic commands, identify skip reasons
2. **Today** (tonight): Apply targeted fix based on diagnosis
3. **Tomorrow** (Jul 22): Monitor all day, verify trades happening
4. **This week**: Validate performance, ensure consistent trading
5. **Next week**: Go live with real money (if validated)

---

**Run the diagnostic commands and send me the output. I'll tell you exactly what's wrong and how to fix it!** 🔍
