# FutureMathics Simple Stack - Full RTH Deployment

**Commit:** `f6072e9`  
**Branch:** `main`  
**Date:** 2026-08-07  
**Change:** Widen entry windows to full RTH session in simple stack mode

---

## What Changed

### Core Fix
- **Simple stack now allows entries ANY time during RTH session (09:30-16:00 ET)**
- Removes strict window restrictions (09:45-11:30 & 13:45-15:55) when `FM_VIRTUE_SIMPLE_STACK=1`
- Allows trading throughout:
  - Morning session (09:30-09:45) - previously blocked
  - Lunch window (11:30-13:45) - previously blocked
  - Late session (15:55-16:00) - previously blocked

### Files Modified
1. `scripts/run_daily_session.py`
   - Modified `virtue_entries_allowed()` to check `virtue_simple_stack()`
   - If True: allow entries full session (not just restricted windows)
   - If False: keep strict windows + extreme overrides
   - Updated `virtue_session_label()` to show "FULL_SESSION (simple_stack)"

2. `DIAGNOSTIC_SIMPLE_STACK_AUDIT.md` (NEW)
   - Comprehensive audit showing all bypassed gates
   - Identified entry windows as primary blocker
   - Full testing checklist

---

## Deployment Steps (AWS)

### Option 1: Deploy from PowerShell (Windows)

```powershell
cd C:\FutureMathics.ai
git fetch origin
git checkout main
git pull origin main
.\scripts\deploy_virtue_remote.ps1
```

### Option 2: Direct SSH to AWS

```bash
# SSH into your AWS instance
ssh -i ~/.ssh/your-key.pem ubuntu@your-ec2-instance

# Navigate to repo
cd /path/to/FutureMathics.ai

# Pull latest changes
git fetch origin
git pull origin main

# Restart the service
sudo systemctl restart futuremathics
# OR if using PM2:
pm2 restart futuremathics

# Verify it's running
sudo systemctl status futuremathics
# OR
pm2 logs futuremathics --lines 50
```

### Option 3: Redeploy via GitHub Actions (if configured)
- Push to `main` triggers auto-deployment
- Check Actions tab in GitHub for status

---

## Verification Checklist

After deployment, verify the system is working:

### 1. Check Boot Logs
```bash
# Look for these lines in logs:
```

Expected output:
```
BOOT SIMPLE_STACK_ENABLED bands+stop+tp+peak_lock+time_decay
BOOT CORE_DISABLED tactical_only simplify_mode contracts_ceiling=1
session_label=...entries=FULL_SESSION (simple_stack)
```

### 2. Check System State
```bash
curl http://your-server/api/system_state | jq .
```

Expected JSON (when market open):
```json
{
  "dual_sleeve": {
    "core_enabled": false,
    "simplify_mode": true,
    "max_account_contract_ceiling": 1
  },
  "entry_pipeline": {
    "allow_new_entries": true,  // <-- Should be TRUE during 09:30-16:00 ET
    "entry_windows_et": "FULL_SESSION (simple_stack)"
  }
}
```

### 3. Check Environment Variables

Ensure these are set correctly in `.env.local` on the AWS instance:

```bash
FM_VIRTUE_SIMPLE_STACK=1
FM_VIRTUE_CORE_ENABLED=0
FM_MAX_ACCOUNT_CONTRACT_CEILING=1
FM_PAPER_MAX_MES_CONTRACTS=1
```

Verify with:
```bash
cat .env.local | grep -E "FM_VIRTUE_SIMPLE_STACK|FM_VIRTUE_CORE_ENABLED|FM_MAX_ACCOUNT_CONTRACT_CEILING"
```

### 4. Monitor First Hour of Trading

Watch for entries to occur during previously blocked windows:
- **09:30-09:45** (morning warm-up)
- **11:30-13:45** (lunch window)
- **15:55-16:00** (late session)

Check logs for:
```
SIMPLE_STACK_ENTRY side=LONG blend=62.3 streak=1 — bands only
```

**NOT** this (old behavior):
```
no_new_entry_window allow_new_entries=False — manage/exit only
```

---

## Rollback Plan

If the system behaves unexpectedly, rollback to previous commit:

```bash
cd /path/to/FutureMathics.ai
git checkout abcc14e  # Previous commit before this change
sudo systemctl restart futuremathics
# OR
pm2 restart futuremathics
```

Alternatively, disable simple stack mode without code rollback:

```bash
# Edit .env.local on AWS
FM_VIRTUE_SIMPLE_STACK=0
```

This reverts to standard mode with strict entry windows.

---

## Expected Behavior After Deployment

### ✅ What Should Happen
1. System shows `allow_new_entries=true` ANY time during 09:30-16:00 ET (RTH)
2. Entries can occur throughout the full 6.5-hour trading day
3. No "no_new_entry_window" blocks during RTH
4. Session label shows "entries=FULL_SESSION (simple_stack)"

### ❌ What Should NOT Happen
1. Entries before 09:30 ET or after 16:00 ET (session still enforced)
2. Entries on weekends (session closed)
3. Any "entry_structure_blocked" messages (already bypassed)
4. Any "pipeline_block" messages (already bypassed)

### Protection Still Enforced (Correct)
- Circuit breaker / profit lock ($100 → $25 trailing floor)
- Daily trade cap (12 trades max)
- Daily loss halt ($300)
- Session hours (09:30-16:00 ET RTH mode)

---

## Performance Expectations

### Increased Trading Opportunities
- **Before:** ~4 hours of entry windows per day (09:45-11:30 + 13:45-15:55)
- **After:** ~6.5 hours of entry windows per day (full 09:30-16:00 RTH)
- **Expected increase:** ~60% more potential entry opportunities

### Risk Stays Controlled
- 1 MES max position size (unchanged)
- $100 TP / $75 stop per trade (unchanged)
- Max 12 trades/day (unchanged)
- Max daily loss $300 (unchanged)

---

## Next Steps

1. **Deploy to AWS** using steps above
2. **Monitor closely** during first day of trading
3. **Check trades occur** in previously blocked windows (09:30-09:45, 11:30-13:45, 15:55-16:00)
4. **Review end-of-day performance** vs. previous days
5. **Report any issues** to this agent for further tuning

---

## Support

If issues arise:
1. Check logs first: `pm2 logs futuremathics` or `journalctl -u futuremathics -f`
2. Verify `.env.local` settings
3. Check system_state.json: `cat data/system_state.json | jq .`
4. Review DIAGNOSTIC_SIMPLE_STACK_AUDIT.md for troubleshooting checklist
5. Rollback if necessary (see Rollback Plan above)

---

## Summary

This deployment removes the final blocker preventing simple stack from "working flawlessly" during the full trading day. All indicator-based friction was already removed; this change removes time-based friction, allowing the system to trust its signals throughout the entire RTH session.

**Deploy with confidence.** Tests pass. All changes are backwards compatible (standard mode unchanged).
