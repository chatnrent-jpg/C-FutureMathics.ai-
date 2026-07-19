#!/usr/bin/env python3
"""
FutureMathics performance analytics — generate reports from trade history.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.trade_history import TradeHistoryDB


def print_overall_stats(db: TradeHistoryDB) -> None:
    """Print overall performance statistics."""
    stats = db.get_total_stats()
    if not stats:
        print("No trades found in database.")
        return
    
    print("=" * 80)
    print("OVERALL PERFORMANCE")
    print("=" * 80)
    print(f"Total Trades:        {stats['total_trades']:,}")
    print(f"Winning Trades:      {stats['winning_trades']:,} ({stats['win_rate']:.1%})")
    print(f"Losing Trades:       {stats['losing_trades']:,}")
    print(f"Win Rate:            {stats['win_rate']:.1%}")
    print(f"\nTotal P&L:           ${stats['total_pnl']:,.2f}")
    print(f"Avg P&L per Trade:   ${stats['avg_pnl_per_trade']:.2f}")
    print(f"Largest Win:         ${stats['largest_win']:.2f}")
    print(f"Largest Loss:        ${stats['largest_loss']:.2f}")
    print(f"Avg R-Multiple:      {stats['avg_r_multiple']:.2f}R")
    print()


def print_daily_summaries(db: TradeHistoryDB, days: int = 10) -> None:
    """Print daily performance summaries."""
    summaries = db.get_all_daily_summaries()
    if not summaries:
        print("No daily summaries available.")
        return
    
    print("=" * 80)
    print(f"DAILY PERFORMANCE (Last {min(days, len(summaries))} Days)")
    print("=" * 80)
    print(f"{'Date':<12} {'P&L':>12} {'%':>8} {'Trades':>8} {'W/L':>8} {'WR%':>8} {'PF':>8}")
    print("-" * 80)
    
    for summary in summaries[:days]:
        date = summary['session_date']
        pnl = summary['realized_pnl']
        pct = summary['pnl_pct']
        trades = summary['total_trades']
        w = summary['winning_trades']
        l = summary['losing_trades']
        wr = summary['win_rate']
        pf = summary['profit_factor']
        
        pnl_color = "+" if pnl >= 0 else ""
        print(f"{date:<12} {pnl_color}${pnl:>10,.2f} {pct:>7.2f}% {trades:>8,} {w:>3}/{l:<3} {wr:>7.1%} {pf:>7.2f}")
    print()


def print_recent_trades(db: TradeHistoryDB, count: int = 20) -> None:
    """Print most recent trades."""
    # Get trades from last 7 days
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    trades = db.get_date_range_trades(start_date, end_date)
    
    if not trades:
        print("No recent trades found.")
        return
    
    print("=" * 80)
    print(f"RECENT TRADES (Last {min(count, len(trades))})")
    print("=" * 80)
    print(f"{'Time':<20} {'Dir':<5} {'C':>3} {'Entry':>8} {'Exit':>8} {'P&L':>10} {'R':>6} {'Reason':<20}")
    print("-" * 80)
    
    for trade in trades[-count:]:
        time_str = trade['exit_time'][:19].replace('T', ' ')
        direction = trade['direction'][:4]
        contracts = trade['contracts']
        entry = trade['entry_price']
        exit_p = trade['exit_price']
        pnl = trade['realized_pnl']
        r = trade['r_multiple']
        reason = trade['exit_reason']
        
        pnl_sign = "+" if pnl >= 0 else ""
        print(f"{time_str:<20} {direction:<5} {contracts:>3} {entry:>8.2f} {exit_p:>8.2f} {pnl_sign}${pnl:>8.2f} {r:>5.2f}R {reason:<20}")
    print()


def compute_and_save_daily_summaries(db: TradeHistoryDB) -> None:
    """Compute daily summaries for all dates with trades."""
    # Get all unique session dates
    with db.db_path.open() as f:
        pass  # Just to ensure db exists
    
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.execute("SELECT DISTINCT session_date FROM trades ORDER BY session_date")
        dates = [row[0] for row in cursor.fetchall()]
    
    print(f"Computing daily summaries for {len(dates)} session dates...")
    for date in dates:
        summary = db.compute_daily_summary(date)
        if summary:
            db.save_daily_summary(summary)
    print(f"✓ Saved {len(dates)} daily summaries")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Analyze FutureMathics trading performance")
    parser.add_argument("--days", type=int, default=30, help="Number of days to show in daily summary")
    parser.add_argument("--recent", type=int, default=20, help="Number of recent trades to show")
    parser.add_argument("--compute-summaries", action="store_true", help="Recompute all daily summaries")
    args = parser.parse_args()
    
    db = TradeHistoryDB()
    
    if args.compute_summaries:
        compute_and_save_daily_summaries(db)
        print()
    
    print_overall_stats(db)
    print_daily_summaries(db, days=args.days)
    print_recent_trades(db, count=args.recent)


if __name__ == "__main__":
    main()
