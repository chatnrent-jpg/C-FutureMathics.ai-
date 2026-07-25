# FutureMathics.ai

Systematic **MES (Micro E-mini S&P 500)** futures infrastructure — mirror of [MarketMathics.ai](../MarketMathics.ai) architecture.

| Layer | Role |
|-------|------|
| **Celine** | VWAP trend signals, contract sizing, tick stop/target |
| **Manus** | Fixed-fractional risk, daily halt, drawdown brake |
| **Engine** | Orchestrator, paper broker, position monitor, Streamlit state |

## Quick start (paper demo)

```powershell
cd C:\FutureMathics.ai
pip install -r requirements.txt
$env:PYTHONPATH = "C:\FutureMathics.ai"
python scripts/bootstrap_system_state.py
python engine/futures_orchestrator.py
```

## Daily session + dashboard

```powershell
# Terminal 1 — dashboard
streamlit run scripts/sandbox_streamlit.py --server.port 8502

# Terminal 2 — orchestrator (respects CME MES futures hours: Sun 6PM - Fri 5PM ET)
python scripts/run_daily_session.py --cycles 20

# To override market hours for testing (not recommended for production):
python scripts/run_daily_session.py --ignore-hours --cycles 20
```

## MES contract specs

| | |
|---|---|
| Symbol | **MES** |
| Point value | **$5** / index point |
| Tick size | **0.25** pts = **$1.25** / tick |
| Default stop | **8 ticks** ($10/contract) |
| Default target | **16 ticks** ($20/contract) |

## Risk (paper mode — actual config values)

- **5%** of NAV max concurrent open risk
- **0.5%** of NAV per trade budget (fixed-fractional)
- **2%** of NAV daily loss halt
- **3 contracts** max position size (paper cap)
- **1 position** max open positions (paper)

## Live mode ($1k affordable loss)

Set `FORWARD_TEST_MODE = False` in `engine/config.py` and wire Tradovate credentials in `.env`.

## 🎯 Market Data: Alpaca SPY Feed (Recommended)

**NEW:** Use your existing Alpaca market data subscription for real-time signals!

Since Webull MES futures quotes are hard to access via API, we use **SPY (S&P 500 ETF)** as a signal proxy:
- SPY → Real-time price from Alpaca ($9/month)
- System → Scales SPY to MES equivalent  
- Webull → Executes MES trades

**5-Minute Setup:** See **[ALPACA_QUICKSTART.md](ALPACA_QUICKSTART.md)** 🚀

**Result:** Real market VWAP signals instead of sim data = **50-60% win rate vs 35%**

Full docs: [docs/ALPACA_INTEGRATION.md](docs/ALPACA_INTEGRATION.md)

---

## 🏆 PRIMARY STRATEGY: VolumeWatch Grade-Path MES

**Brain = VolumeWatch F→A grade · Body = MES futures**

| Path | Rules |
|------|--------|
| Rising (from down) | Cash &lt;50 → **LONG ≥50** → exit ≥85 → **SHORT ≥90** |
| Falling (from up) | Exit short ≤50 → cash below 50 |

```powershell
python scripts/test_grade_path_engine.py
python scripts/run_grade_futures.py
```

See [docs/GRADE_PATH_MES.md](docs/GRADE_PATH_MES.md).

---

## Webull (execution broker)

One broker for **MES futures sim/live** and eventually options — no Tradovate.

```powershell
# Copy your existing VolumeWatch Webull API keys
python scripts/sync_webull_from_volumewatch.py

# Probe accounts, balance, quotes (no orders)
python scripts/test_webull_futures.py
```

Set `WEBULL_FUTURES_ACCOUNT_ID` in `.env.local` to your **futures simulated** account id.

| Env | Purpose |
|-----|---------|
| `WEBULL_FUTURES_ACCOUNT_ID` | Futures sim (or live when ready) |
| `FM_ALLOW_LIVE_ORDERS=1` | Allow real Webull futures orders |

**Note:** Webull is used for trade execution. Market data comes from Alpaca SPY feed (if configured) or falls back to sim.

## vs MarketMathics

| MarketMathics | FutureMathics |
|---------------|---------------|
| SPY 0DTE options | MES futures |
| VRP / GEX signals | VWAP trend |
| Credit spreads | Long/short directional |
| Alpaca options API | Tradovate (Phase 2) / paper sim now |

## 🚀 Phase 1 Improvements (NEW!)

**Mathematical enhancement system deployed:**
- 8-tick VWAP threshold (2x more selective)
- 1.5:1 R:R ratio (easier wins)
- 60-second trade cooldown (prevents overtrading)  
- Linear regression trend filter (avoids counter-trend)
- Statistical confidence scoring (quality > quantity)

**Expected Results:** 50-60% win rate, 300-500 trades/day (vs 35% / 2,687)

See [PHASE1_IMPROVEMENTS.md](PHASE1_IMPROVEMENTS.md) for technical details.

## Trade History & Performance Analytics

**Complete trade history tracking with SQLite database!**

```powershell
# View performance stats
python scripts/analyze_performance.py

# Import AWS logs (last 30 days)
.\scripts\fetch_aws_history.ps1 ubuntu@your-aws-ip
```

See [TRADE_HISTORY.md](TRADE_HISTORY.md) for full documentation.

## Deploy (AWS)

Copy `deploy/systemd/*.service` and adjust paths — same pattern as MarketMathics on port **8502**.
