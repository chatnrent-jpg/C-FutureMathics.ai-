#!/usr/bin/env python3
"""
Parse AWS systemd journal logs and import historical trades into database.

Usage:
    # On AWS, export logs to file:
    sudo journalctl -u futuremathics.service --no-pager > futuremathics_logs.txt
    
    # Copy to local machine and parse:
    python scripts/parse_aws_logs.py futuremathics_logs.txt
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.trade_history import TradeHistoryDB, TradeRecord


def parse_log_line(line: str) -> dict | None:
    """
    Parse a position_exit log line.
    
    Example format:
    Jul 19 02:15:43 hostname python[12345]: INFO:engine.futures_position_manager:position_exit id=POS-ABC123 reason=TAKE_PROFIT_TICKS pnl=20.00
    """
    # Match position_exit log entries
    pattern = r'position_exit id=([A-Z0-9-]+) reason=([A-Z_]+) pnl=([-+]?\d+\.\d+)'
    match = re.search(pattern, line)
    if not match:
        return None
    
    position_id, reason, pnl = match.groups()
    
    # Extract timestamp (format: Jul 19 02:15:43)
    ts_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})'
    ts_match = re.search(ts_pattern, line)
    timestamp = ts_match.group(1) if ts_match else None
    
    return {
        "position_id": position_id,
        "exit_reason": reason,
        "realized_pnl": float(pnl),
        "timestamp": timestamp,
    }


def import_logs_to_db(log_file: Path, year: int = 2026) -> None:
    """Import parsed log entries into trade history database."""
    if not log_file.exists():
        print(f"Error: Log file not found: {log_file}")
        return
    
    db = TradeHistoryDB()
    trades_parsed = []
    
    print(f"Parsing log file: {log_file}")
    with log_file.open('r', encoding='utf-8', errors='ignore') as f:
        for line_num, line in enumerate(f, 1):
            parsed = parse_log_line(line)
            if parsed:
                trades_parsed.append(parsed)
    
    print(f"Found {len(trades_parsed)} position_exit entries")
    
    if not trades_parsed:
        print("No trades found in log file. Make sure the file contains position_exit log lines.")
        return
    
    # Convert to TradeRecord objects
    # Note: We have limited data from logs, so many fields will be estimates
    print("\nImporting trades to database...")
    imported = 0
    
    for idx, trade_data in enumerate(trades_parsed):
        try:
            # Parse timestamp (Jul 19 02:15:43) - need to add year
            ts_str = trade_data['timestamp']
            if ts_str:
                dt = datetime.strptime(f"{year} {ts_str}", "%Y %b %d %H:%M:%S")
                exit_time = dt.isoformat()
                session_date = dt.strftime("%Y-%m-%d")
            else:
                exit_time = datetime.now().isoformat()
                session_date = datetime.now().strftime("%Y-%m-%d")
            
            # Estimate trade details based on P&L
            pnl = trade_data['realized_pnl']
            
            # Assume standard 3 contracts, $10 stop, $20 target
            if pnl > 0:
                # Winner - likely hit target
                contracts = max(1, int(round(pnl / 20)))  # $20 per contract
                risk_amount = contracts * 10
                reward_amount = contracts * 20
            else:
                # Loser - hit stop
                contracts = max(1, int(round(abs(pnl) / 10)))  # $10 per contract
                risk_amount = contracts * 10
                reward_amount = contracts * 20
            
            r_multiple = pnl / risk_amount if risk_amount > 0 else 0.0
            
            # Generate synthetic trade_id
            trade_id = f"LOG-{trade_data['position_id'][-8:]}"
            
            # Create trade record with estimated values
            trade_record = TradeRecord(
                trade_id=trade_id,
                position_id=trade_data['position_id'],
                symbol="MES",
                direction="LONG",  # Unknown from logs
                contracts=contracts,
                entry_price=0.0,  # Unknown
                exit_price=0.0,  # Unknown
                stop_price=0.0,
                target_price=0.0,
                entry_time=exit_time,  # Unknown - use exit time
                exit_time=exit_time,
                duration_seconds=0.0,
                realized_pnl=pnl,
                risk_amount=risk_amount,
                reward_amount=reward_amount,
                r_multiple=r_multiple,
                exit_reason=trade_data['exit_reason'],
                nav_at_entry=0.0,  # Unknown
                nav_at_exit=0.0,  # Unknown
                daily_pnl_before=0.0,
                session_date=session_date,
                cycle_number=idx,
            )
            
            db.insert_trade(trade_record)
            imported += 1
            
            if imported % 100 == 0:
                print(f"  Imported {imported} trades...")
        
        except Exception as e:
            print(f"  Error importing trade {idx}: {e}")
            continue
    
    print(f"\n✓ Successfully imported {imported} / {len(trades_parsed)} trades")
    print(f"\nDatabase location: {db.db_path}")
    print("\nRun: python scripts/analyze_performance.py --compute-summaries")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Parse AWS logs and import trades")
    parser.add_argument("log_file", type=Path, help="Path to exported journalctl log file")
    parser.add_argument("--year", type=int, default=2026, help="Year for log timestamps")
    args = parser.parse_args()
    
    import_logs_to_db(args.log_file, year=args.year)


if __name__ == "__main__":
    main()
