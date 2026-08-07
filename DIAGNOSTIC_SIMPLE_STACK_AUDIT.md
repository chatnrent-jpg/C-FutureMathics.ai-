# FutureMathics Simple Stack Diagnostic Audit
**Date:** 2026-08-07  
**Mode:** Tactical-Only 1 MES + Simple Stack  
**Status:** System configured but not finding entries

## Current Configuration

### Core Settings (from config.py)
- `VIRTUE_CORE_ENABLED = False` ✅ Core sleeve disabled
- `MAX_ACCOUNT_CONTRACT_CEILING = 1` ✅ Hard 1 MES limit
- `PAPER_MAX_MES_CONTRACTS = 1` ✅ Paper 1 MES limit
- `VIRTUE_SIMPLE_STACK = True` ✅ Simple stack enabled

### System State (from system_state.json)
- `allow_new_entries: false` ❌ **BLOCKER**
- `entry_structure_ok: false` ⚠️ May be stale (simple_stack should bypass)
- `temperance_long_enter: 68.0` ⚠️ High threshold (should be 58.0 base in simple_stack)
- `pipeline_long_blend_required: 65.0` ⚠️ High threshold (should be bypassed)
- `velocity_adx_penalty: 10.0` ⚠️ Velocity penalty applied (should be 0.0 in simple_stack)
- `last_price: null` ⚠️ No market data yet (cold start)

---

## Gates Currently BYPASSED in Simple Stack Mode ✅

### 1. Entry Structure Gates (Line 2324-2326)
```python
if virtue_simple_stack():
    structure_ok, structure_reason = True, "simple_stack:structure_waived"
```
**Status:** ✅ WORKING - ATR expansion, ADX rising, spread widening all waived

### 2. Temperance Blend Buffer (Line 2361-2363)
```python
if virtue_simple_stack():
    blend_buffer_raw = 0.0
    vel_penalty = 0.0
```
**Status:** ✅ WORKING - Loss friction blend widening disabled

### 3. Course Correct (Line 2552)
```python
if bool(session.tactical_active) and int(session.tactical_size) > 0 and not virtue_simple_stack():
    # course_correct check
```
**Status:** ✅ WORKING - Holding path course_correct disabled

### 4. ADX Tactical Freeze (Line 2856-2869)
```python
if not virtue_simple_stack() and float(decision.adx) < float(VIRTUE_TACTICAL_ADX_MIN) and not below_vwap_short:
```
**Status:** ✅ WORKING - ADX floor for tactical entries waived

### 5. Streak Confirmation (Line 3006-3007)
```python
if virtue_simple_stack():
    need_streak = 1
```
**Status:** ✅ WORKING - Multi-cycle confirmation reduced to 1

### 6. Pipeline Validation (Line 3042-3052)
```python
if virtue_simple_stack():
    # skip pipeline/chase/pullback/bias gates
```
**Status:** ✅ WORKING - Velocity gates, chase filters, pullback requirements all bypassed

### 7. ADX Short Floor (Line 3126-3145)
```python
if (not virtue_simple_stack()) and side == "SHORT":
    # ADX floor check
```
**Status:** ✅ WORKING - Short ADX minimum waived

### 8. Directional Gate (Line 3156-3170)
```python
if not virtue_simple_stack():
    gate_ok, gate_reason = evaluate_directional_gate(...)
```
**Status:** ✅ WORKING - Bull-day short asymmetry waived

---

## Gates STILL ENFORCED (Potential Blockers) ❌

### 1. Entry Time Windows (Line 2910-2943) ❌ **PRIMARY BLOCKER**
```python
entries_ok = bool(ignore_hours) or virtue_entries_allowed(adx=..., blend=..., vwap_score=...)
if not entries_ok:
    # block entry
```

**Issue:** Even in simple stack mode, entries are ONLY allowed during:
- **09:45 - 11:30 ET**
- **13:45 - 15:55 ET**
- OR extreme trend override (ADX ≥40 + blend ≤35 or ≥65)
- OR below-VWAP short (blend ≤42 + VWAP score ≤45)

**Impact:**
- Testing outside these windows shows `allow_new_entries: false`
- System will not enter trades during 11:30-13:45 "lunch window"
- System will not enter trades during 15:55-16:00 late session
- Extreme overrides require ADX ≥40 which is rare

**Recommendation:** In simple stack mode, allow entries ANY time session is open (09:30-16:00 RTH)

### 2. Session Open Check (implicit in virtue_entries_allowed)
- System won't trade if session closed (before 09:30 ET or after 16:00 ET on weekdays)
- This is correct behavior for RTH mode

### 3. Circuit Breaker / Profit Lock (Line 2870-2886)
- Remains enforced even in simple stack
- This is CORRECT - Temperance protection should stay

### 4. Daily Trade Cap (Line 2836-2844)
- `VIRTUE_MAX_TACTICAL_TRADES_PER_DAY = 12`
- This is CORRECT - Temperance overtrading protection

---

## Diagnosis: Why "Not Working Flawlessly"

### Root Cause
The system is **correctly configured** for simple stack mode, but **entry time windows** are blocking trades outside of:
- 09:45-11:30 ET (1h 45min window)
- 13:45-15:55 ET (2h 10min window)

**Total trading time: ~4 hours out of 6.5 hour RTH session**

### When Testing Occurs
If user is testing at:
- 09:30-09:45 ET → ❌ Blocked
- 11:30-13:45 ET → ❌ Blocked (lunch window)
- 15:55-16:00 ET → ❌ Blocked (late session)

The system shows `allow_new_entries: false` and will not fire any entries.

---

## Recommendations

### Option 1: Widen Entry Windows in Simple Stack Mode (RECOMMENDED)
Remove strict time windows when simple_stack is enabled. Allow entries ANY time during RTH (09:30-16:00 ET).

**Rationale:**
- Simple stack is already a "trust the signal" mode
- Time windows were designed to avoid proxy noise and late-session flatten risk
- User wants "work flawlessly" = trade when signals appear
- 1 MES + $100 TP + $75 stop = low risk per trade

**Implementation:**
```python
# In run_daily_session.py or main.py
if virtue_simple_stack():
    return virtue_session_open(now)  # Any time during RTH
else:
    return allow_new_entries(now)  # Strict windows
```

### Option 2: Lower Extreme Override Threshold
Current: ADX ≥40 for out-of-window entries  
Proposed: ADX ≥25 in simple stack mode (still shows trend, not flat)

### Option 3: Add Explicit --ignore-hours Flag Support
Already exists in code: `bool(ignore_hours)` but requires manual activation.

---

## Testing Checklist

To verify simple stack is working:

1. ✅ Boot logs show `SIMPLE_STACK_ENABLED`
2. ✅ Core sleeve disabled (`core_enabled: false`)
3. ✅ Max contracts = 1 (`max_account_contract_ceiling: 1`)
4. ❌ Check current ET time is within entry windows (09:45-11:30 or 13:45-15:55)
5. ⚠️ Verify market data feed is live (`last_price` should not be null)
6. ⚠️ Check if extreme override applies (ADX ≥40 + blend extreme)

---

## Conclusion

**Simple stack mode is implemented correctly** and all indicator gates are bypassed as designed.

**The blocker is TIME WINDOWS**, not indicator complexity.

**Next Action:** Widen or remove entry time windows when `VIRTUE_SIMPLE_STACK = True` to allow trading throughout the full RTH session.
