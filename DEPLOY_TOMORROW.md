# Deploy Alpaca Integration to AWS - Morning Checklist

**Date**: Sunday, July 19, 2026  
**Time**: First thing in the morning  
**Priority**: HIGH - AWS system currently trading with old code (weekend bug + overtrading)

---

## 🎯 What Needs to Be Done

Deploy the Alpaca integration + Phase 1 improvements to your AWS server to:
1. ✅ Stop weekend trading bug (market hours fix)
2. ✅ Stop overtrading (2,702 → 300-500 trades/day)
3. ✅ Switch from sim to real Alpaca SPY data
4. ✅ Apply Phase 1 mathematical filters

---

## 📋 Step-by-Step Deployment

### Step 1: Copy Files to AWS (5 minutes)

Open PowerShell:

```powershell
cd C:\FutureMathics.ai

# Set connection details
$remote = "ubuntu@54.91.152.140"
$key = "C:\MarketMathics.ai\MarketMathics.pem"

# Copy new Alpaca files to /tmp
scp -i $key engine/alpaca_spy_feed.py "${remote}:/tmp/"
scp -i $key engine/futures_broker_adapter.py "${remote}:/tmp/"
scp -i $key engine/ui_state_bridge.py "${remote}:/tmp/"
scp -i $key scripts/test_alpaca_spy_feed.py "${remote}:/tmp/"

# Verify upload
# Should see: alpaca_spy_feed.py, futures_broker_adapter.py, etc.
```

---

### Step 2: SSH to Server (1 minute)

```powershell
ssh -i $key $remote
```

You should see: `ubuntu@ip-XXX-XX-XX-XXX:~$`

---

### Step 3: Backup Current Code (1 minute)

```bash
cd /home/ubuntu/FutureMathics

# Backup existing files
cp engine/futures_broker_adapter.py engine/futures_broker_adapter.py.backup
cp engine/ui_state_bridge.py engine/ui_state_bridge.py.backup

echo "✅ Backup complete"
```

---

### Step 4: Deploy New Files (2 minutes)

```bash
# Copy files from /tmp to production
sudo cp /tmp/alpaca_spy_feed.py engine/
sudo cp /tmp/futures_broker_adapter.py engine/
sudo cp /tmp/ui_state_bridge.py engine/
sudo cp /tmp/test_alpaca_spy_feed.py scripts/

# Set permissions
sudo chown ubuntu:ubuntu engine/alpaca_spy_feed.py
sudo chown ubuntu:ubuntu scripts/test_alpaca_spy_feed.py

# Verify files exist
ls -l engine/alpaca_spy_feed.py
ls -l scripts/test_alpaca_spy_feed.py

echo "✅ Files deployed"
```

---

### Step 5: Add Alpaca Credentials (2 minutes)

```bash
# Edit .env.local
nano .env.local

# Add these lines at the bottom:
# 
# # Alpaca Market Data
# ALPACA_API_KEY=<your-alpaca-key>
# ALPACA_API_SECRET=<your-alpaca-secret>
# ALPACA_LIVE=0

# Save: Ctrl+O, Enter, Ctrl+X
```

---

### Step 6: Test Alpaca Integration (2 minutes)

```bash
# Test connection
python3 scripts/test_alpaca_spy_feed.py

# Expected output:
# ✅ Alpaca credentials found
# ✅ alpaca_ok spy=742.45
# SPY Price:  $742.45
# MES Price:  $9280.69
```

**If test fails**: Double-check credentials in .env.local

---

### Step 7: Restart Service (1 minute)

```bash
# Restart the trading system
sudo systemctl restart futuremathics

# Wait 5 seconds
sleep 5

# Check status
sudo systemctl status futuremathics

# Should see: Active: active (running)
```

---

### Step 8: Verify Logs (2 minutes)

```bash
# Watch live logs
sudo journalctl -u futuremathics -f

# Look for these key messages:
# ✅ "Alpaca SPY feed enabled - using as MES signal proxy"
# ✅ "alpaca_spy_feed_ok | alpaca_ok spy=742.45"
# ✅ "CYCLE START"

# Press Ctrl+C to exit log view
```

---

### Step 9: Check Dashboard (1 minute)

Open browser:
- **URL**: https://supreme-whats-interactions-objectives.trycloudflare.com/

**Verify**:
- ✅ Updated timestamp is current
- ✅ Cycles incrementing every 2 seconds
- ✅ System no longer trading (market closed until 6 PM)
- ✅ Daily stats reset to $0 (new trading day)

---

### Step 10: Exit SSH (1 second)

```bash
exit
```

Back to your local PowerShell.

---

## ✅ Success Criteria

After deployment, your AWS system should:

| Check | Expected |
|-------|----------|
| **Alpaca enabled** | ✅ "Alpaca SPY feed enabled" in logs |
| **Market hours** | ✅ Not trading until 6 PM Sunday |
| **Data source** | ✅ `alpaca_spy_proxy` (not `sim`) |
| **Trade count** | ✅ 0 trades (market closed) |
| **Dashboard** | ✅ Live updates every 2 seconds |

---

## 🚨 Troubleshooting

### "Alpaca credentials not configured"
- Check .env.local has ALPACA_API_KEY and ALPACA_API_SECRET
- Restart service: `sudo systemctl restart futuremathics`

### "ModuleNotFoundError: aiohttp"
- Install: `pip3 install aiohttp`
- Restart service

### "Permission denied"
- Use `sudo` for file operations
- Check file ownership: `ls -l engine/alpaca_spy_feed.py`

### Service won't start
- Check logs: `sudo journalctl -u futuremathics -n 50`
- Look for Python errors
- Verify Python syntax: `python3 -m py_compile engine/alpaca_spy_feed.py`

---

## 📊 Expected Behavior After Deployment

### Right Now (Morning, Markets Closed)
- System running but idle
- No trades (market hours enforced)
- Using last SPY price ($742.45)
- Dashboard updating every 2 seconds

### 6 PM Tonight (Futures Market Opens)
- System starts trading MES futures
- Uses Alpaca SPY for signals
- Phase 1 filters active:
  - 60-second trade cooldown
  - 8-tick VWAP threshold
  - 1.5:1 R:R ratio
  - Trend filter
  - Confidence scoring
- Expected: 300-500 trades/day (vs 2,702)

### Monday 9:30 AM (SPY Market Opens)
- Switches to live SPY streaming
- Real-time S&P 500 data
- Optimal signal quality
- Expected 50-60% win rate

---

## 🎯 Timeline Estimate

| Step | Time |
|------|------|
| Copy files | 5 min |
| SSH + backup | 2 min |
| Deploy files | 2 min |
| Add credentials | 2 min |
| Test Alpaca | 2 min |
| Restart service | 1 min |
| Verify logs | 2 min |
| Check dashboard | 1 min |
| **TOTAL** | **17 minutes** |

---

## 📝 Notes

- **Backup created**: Yes (`.backup` files)
- **Rollback if needed**: Restore from `.backup` files
- **Credentials safe**: In .env.local (gitignored)
- **Service auto-restarts**: On failure (systemd config)

---

## 🎉 After Deployment

You'll have:
- ✅ Real Alpaca SPY data feed ($99/mo subscription)
- ✅ Phase 1 mathematical improvements
- ✅ Market hours fix (no weekend trading)
- ✅ Trade cooldown (prevents overtrading)
- ✅ All fixes deployed to production

**Expected result**: Professional-grade automated MES futures trading system with 50-60% win rate! 🚀

---

## 💾 Save This File

This checklist is saved at:
- **Local**: `C:\FutureMathics.ai\DEPLOY_TOMORROW.md`
- **GitHub**: Already committed

**Tomorrow morning, just open this file and follow the steps!**

---

**Good night! Deploy this first thing in the morning before 6 PM futures open.** 💤
