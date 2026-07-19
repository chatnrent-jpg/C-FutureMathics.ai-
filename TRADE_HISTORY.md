# FutureMathics Trade History System

Complete trade history tracking and performance analytics system.

## 🎯 What's New

**Trade History Database:**
- SQLite database storing every completed trade
- Full entry/exit context with P&L, R-multiple, timing
- Daily performance summaries with win rate, profit factor, expectancy
- Historical analysis and trend tracking

**Automatic Logging:**
- All trades are now logged to `data/trade_history.db`
- Position entries capture NAV, daily P&L, cycle number
- Position exits log complete trade record with R-multiple

**Performance Analytics:**
- Overall statistics (total P&L, win rate, avg trade, largest win/loss)
- Daily summaries (P&L, trade count, win rate, profit factor)
- Recent trade list with entry/exit/reason
- R-multiple analysis

## 📊 Quick Start

### View Current Performance

```powershell
# Overall stats + last 30 days + recent 20 trades
python scripts/analyze_performance.py

# Show last 60 days
python scripts/analyze_performance.py --days 60

# Show last 50 trades
python scripts/analyze_performance.py --recent 50
```

### Import Historical Data from AWS

```powershell
# Fetch last 30 days of logs and import
.\scripts\fetch_aws_history.ps1 ubuntu@your-aws-ip

# Fetch last 90 days
.\scripts\fetch_aws_history.ps1 ubuntu@your-aws-ip -DaysBack 90
```

### Manual Log Import

```powershell
# On AWS server:
sudo journalctl -u futuremathics.service --no-pager > futuremathics_logs.txt

# On local machine (after copying file):
python scripts/parse_aws_logs.py futuremathics_logs.txt
python scripts/analyze_performance.py --compute-summaries
```

## 📈 Performance Metrics Explained

### Overall Stats
- **Total Trades**: Count of all completed trades
- **Win Rate**: Percentage of winning trades (P&L > 0)
- **Avg P&L per Trade**: Mean profit/loss across all trades
- **Avg R-Multiple**: Average return relative to risk (2.0R = hit full target)

### Daily Summary
- **P&L**: Total realized profit/loss for the day
- **%**: P&L as percentage of starting NAV
- **Trades**: Number of trades executed
- **W/L**: Winners / Losers count
- **WR%**: Win rate for that day
- **PF**: Profit Factor (gross wins / gross losses)

### R-Multiple
- **1.0R**: Broke even (exited at entry)
- **-1.0R**: Hit stop loss (full risk)
- **+2.0R**: Hit target (full reward with 2:1 R:R)
- **Values between**: Partial wins/losses

## 🗄️ Database Schema

### `trades` Table
```sql
- trade_id (unique identifier)
- position_id (position tracking ID)
- symbol (MES)
- direction (LONG/SHORT)
- contracts (position size)
- entry_price, exit_price
- stop_price, target_price
- entry_time, exit_time, duration_seconds
- realized_pnl (actual P&L)
- risk_amount (max loss at stop)
- reward_amount (max gain at target)
- r_multiple (actual return / risk)
- exit_reason (STOP_LOSS_TICKS, TAKE_PROFIT_TICKS, END_OF_DAY)
- nav_at_entry, nav_at_exit
- daily_pnl_before (cumulative P&L before this trade)
- session_date (YYYY-MM-DD)
- cycle_number (orchestrator cycle count)
```

### `daily_summary` Table
```sql
- session_date (YYYY-MM-DD)
- starting_nav, ending_nav
- realized_pnl, pnl_pct
- total_trades, winning_trades, losing_trades
- win_rate
- avg_win, avg_loss
- largest_win, largest_loss
- profit_factor (gross wins / gross losses)
- expectancy ((win_rate × avg_win) - ((1-win_rate) × avg_loss))
```

## 🔧 Direct Database Access

```powershell
# SQLite command line
sqlite3 data/trade_history.db

# Example queries:
SELECT COUNT(*) FROM trades;
SELECT session_date, realized_pnl FROM daily_summary ORDER BY session_date DESC;
SELECT * FROM trades WHERE realized_pnl > 50 ORDER BY realized_pnl DESC LIMIT 10;
```

## 🎯 Performance Targets

**Good Performance Indicators:**
- Win Rate > 50% with 2:1 R:R
- Profit Factor > 1.5
- Positive Expectancy
- Avg R-Multiple > 0.3

**Warning Signs:**
- Win Rate < 35% (below break-even with 2:1 R:R)
- Profit Factor < 1.0
- Negative Expectancy
- Increasing daily losses

## 📁 Files Added

- `engine/trade_history.py` - Database and analytics core
- `scripts/analyze_performance.py` - Performance reporting
- `scripts/parse_aws_logs.py` - Log import tool
- `scripts/fetch_aws_history.ps1` - AWS import automation
- `data/trade_history.db` - SQLite database (created on first run)

## 🔄 Integration

The orchestrator now automatically:
1. Logs trade entry context (NAV, daily P&L, cycle)
2. Logs trade exit with full record (P&L, R-multiple, reason)
3. Persists to SQLite database
4. Available for immediate analysis

No manual intervention required - just run the system and analyze anytime!
