"""
FutureMathics trade history database — SQLite persistence for all trades.

Tracks every position entry/exit with full context for performance analysis.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """Complete trade record from entry to exit."""
    
    trade_id: str
    position_id: str
    symbol: str
    direction: str
    contracts: int
    
    # Prices
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    
    # Timing
    entry_time: str
    exit_time: str
    duration_seconds: float
    
    # P&L
    realized_pnl: float
    risk_amount: float
    reward_amount: float
    r_multiple: float  # Actual return / risk (e.g., 2.0 = hit target with 2R)
    
    # Context
    exit_reason: str
    nav_at_entry: float
    nav_at_exit: float
    daily_pnl_before: float
    
    # Metadata
    session_date: str  # YYYY-MM-DD
    cycle_number: int


class TradeHistoryDB:
    """SQLite database for trade history with performance analytics."""
    
    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = Path(__file__).parent.parent / "data" / "trade_history.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
    
    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT UNIQUE NOT NULL,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    contracts INTEGER NOT NULL,
                    
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    
                    realized_pnl REAL NOT NULL,
                    risk_amount REAL NOT NULL,
                    reward_amount REAL NOT NULL,
                    r_multiple REAL NOT NULL,
                    
                    exit_reason TEXT NOT NULL,
                    nav_at_entry REAL NOT NULL,
                    nav_at_exit REAL NOT NULL,
                    daily_pnl_before REAL NOT NULL,
                    
                    session_date TEXT NOT NULL,
                    cycle_number INTEGER NOT NULL,
                    
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_session_date 
                ON trades(session_date)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_exit_time 
                ON trades(exit_time)
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT UNIQUE NOT NULL,
                    starting_nav REAL NOT NULL,
                    ending_nav REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    winning_trades INTEGER NOT NULL,
                    losing_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    avg_win REAL NOT NULL,
                    avg_loss REAL NOT NULL,
                    largest_win REAL NOT NULL,
                    largest_loss REAL NOT NULL,
                    profit_factor REAL NOT NULL,
                    expectancy REAL NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
    
    def insert_trade(self, trade: TradeRecord) -> None:
        """Insert a completed trade record."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, position_id, symbol, direction, contracts,
                    entry_price, exit_price, stop_price, target_price,
                    entry_time, exit_time, duration_seconds,
                    realized_pnl, risk_amount, reward_amount, r_multiple,
                    exit_reason, nav_at_entry, nav_at_exit, daily_pnl_before,
                    session_date, cycle_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id, trade.position_id, trade.symbol, trade.direction, trade.contracts,
                trade.entry_price, trade.exit_price, trade.stop_price, trade.target_price,
                trade.entry_time, trade.exit_time, trade.duration_seconds,
                trade.realized_pnl, trade.risk_amount, trade.reward_amount, trade.r_multiple,
                trade.exit_reason, trade.nav_at_entry, trade.nav_at_exit, trade.daily_pnl_before,
                trade.session_date, trade.cycle_number
            ))
            conn.commit()
    
    def get_trades_by_date(self, session_date: str) -> list[dict[str, Any]]:
        """Get all trades for a specific session date (YYYY-MM-DD)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM trades 
                WHERE session_date = ? 
                ORDER BY exit_time
            """, (session_date,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_date_range_trades(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Get trades between two dates (inclusive)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM trades 
                WHERE session_date BETWEEN ? AND ? 
                ORDER BY exit_time
            """, (start_date, end_date))
            return [dict(row) for row in cursor.fetchall()]
    
    def compute_daily_summary(self, session_date: str) -> dict[str, Any] | None:
        """Compute performance metrics for a session date."""
        trades = self.get_trades_by_date(session_date)
        if not trades:
            return None
        
        winners = [t for t in trades if t["realized_pnl"] > 0]
        losers = [t for t in trades if t["realized_pnl"] < 0]
        
        total_pnl = sum(t["realized_pnl"] for t in trades)
        gross_wins = sum(t["realized_pnl"] for t in winners)
        gross_losses = abs(sum(t["realized_pnl"] for t in losers))
        
        win_rate = len(winners) / len(trades) if trades else 0.0
        avg_win = gross_wins / len(winners) if winners else 0.0
        avg_loss = gross_losses / len(losers) if losers else 0.0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        starting_nav = trades[0]["nav_at_entry"]
        ending_nav = trades[-1]["nav_at_exit"]
        pnl_pct = (total_pnl / starting_nav * 100) if starting_nav > 0 else 0.0
        
        return {
            "session_date": session_date,
            "starting_nav": starting_nav,
            "ending_nav": ending_nav,
            "realized_pnl": total_pnl,
            "pnl_pct": pnl_pct,
            "total_trades": len(trades),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "largest_win": max((t["realized_pnl"] for t in winners), default=0.0),
            "largest_loss": min((t["realized_pnl"] for t in losers), default=0.0),
            "profit_factor": profit_factor,
            "expectancy": expectancy,
        }
    
    def save_daily_summary(self, summary: dict[str, Any]) -> None:
        """Save or update daily summary."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summary (
                    session_date, starting_nav, ending_nav, realized_pnl, pnl_pct,
                    total_trades, winning_trades, losing_trades, win_rate,
                    avg_win, avg_loss, largest_win, largest_loss,
                    profit_factor, expectancy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                summary["session_date"], summary["starting_nav"], summary["ending_nav"],
                summary["realized_pnl"], summary["pnl_pct"], summary["total_trades"],
                summary["winning_trades"], summary["losing_trades"], summary["win_rate"],
                summary["avg_win"], summary["avg_loss"], summary["largest_win"],
                summary["largest_loss"], summary["profit_factor"], summary["expectancy"]
            ))
            conn.commit()
    
    def get_all_daily_summaries(self) -> list[dict[str, Any]]:
        """Get all daily summaries ordered by date."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM daily_summary 
                ORDER BY session_date DESC
            """)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_total_stats(self) -> dict[str, Any]:
        """Get overall statistics across all trades."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losers,
                    SUM(realized_pnl) as total_pnl,
                    AVG(realized_pnl) as avg_pnl,
                    MAX(realized_pnl) as max_win,
                    MIN(realized_pnl) as max_loss,
                    AVG(r_multiple) as avg_r_multiple
                FROM trades
            """)
            row = cursor.fetchone()
            if not row or row[0] == 0:
                return {}
            
            total, winners, losers, total_pnl, avg_pnl, max_win, max_loss, avg_r = row
            return {
                "total_trades": total,
                "winning_trades": winners,
                "losing_trades": losers,
                "win_rate": winners / total if total > 0 else 0.0,
                "total_pnl": total_pnl or 0.0,
                "avg_pnl_per_trade": avg_pnl or 0.0,
                "largest_win": max_win or 0.0,
                "largest_loss": max_loss or 0.0,
                "avg_r_multiple": avg_r or 0.0,
            }
