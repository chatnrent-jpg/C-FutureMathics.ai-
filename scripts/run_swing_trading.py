#!/usr/bin/env python3
"""
FutureMathics Swing Trading System - Professional 4-hour+ holds.

Target: 2-5 trades/day, $500+/day profit, 60-70% win rate
Strategy: Trend following, breakouts, pullbacks
Hold time: 4 hours to 1 day
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import (
    DEFAULT_STOP_TICKS,
    DEFAULT_TARGET_TICKS,
    FORWARD_TEST_CYCLE_INTERVAL_S,
    HANDSHAKE_EQUITY_BASE,
    MIN_CONFIDENCE_THRESHOLD,
    MIN_SECONDS_BETWEEN_TRADES,
    SWING_CONTRACTS,
    SWING_MAX_TRADES_PER_DAY,
    SWING_MIN_TREND_STRENGTH,
    forward_test_force_paper,
)
from engine.futures_broker_adapter import FuturesBrokerAdapter
from engine.futures_orchestrator import FuturesOrchestrator, OrchestratorConfig, SessionState
from engine.ui_state_bridge import ensure_boot_system_state
from celine.swing_signals import SwingSignalEngine
from scripts.run_daily_session import in_market_hours

logger = logging.getLogger(__name__)


async def run_swing_trading(*, cycles: int | None = None, ignore_hours: bool = False) -> None:
    """
    Run swing trading system.
    
    Checks for signals every 15 minutes during market hours (9:30 AM - 4 PM ET).
    Generates 2-5 high-quality signals per day.
    Holds positions 4 hours to 1 day.
    """
    ensure_boot_system_state()
    
    # Initialize components
    broker = FuturesBrokerAdapter()
    swing_engine = SwingSignalEngine(
        stop_ticks=DEFAULT_STOP_TICKS,
        target_ticks=DEFAULT_TARGET_TICKS,
        min_trend_strength=SWING_MIN_TREND_STRENGTH,
        min_confidence=MIN_CONFIDENCE_THRESHOLD,
        min_hours_between_trades=MIN_SECONDS_BETWEEN_TRADES / 3600,
        max_trades_per_day=SWING_MAX_TRADES_PER_DAY,
    )
    
    # Use existing orchestrator but with swing config
    orch = FuturesOrchestrator(
        config=OrchestratorConfig(
            loop_interval_s=FORWARD_TEST_CYCLE_INTERVAL_S,  # 15 minutes
            print_state=True
        ),
        broker=broker,
    )
    orch.risk.update_nav(HANDSHAKE_EQUITY_BASE)
    orch.risk.starting_nav = HANDSHAKE_EQUITY_BASE
    
    if forward_test_force_paper():
        print("🎯 SWING TRADING MODE — MES paper routing", flush=True)
        print(f"   Stop: {DEFAULT_STOP_TICKS} ticks ($75 risk)", flush=True)
        print(f"   Target: {DEFAULT_TARGET_TICKS} ticks ($150 profit)", flush=True)
        print(f"   Frequency: {MIN_SECONDS_BETWEEN_TRADES/3600:.1f} hours between trades", flush=True)
        print(f"   Max trades/day: {SWING_MAX_TRADES_PER_DAY}", flush=True)
        print(f"   Contracts: {SWING_CONTRACTS}", flush=True)
        print(f"   Target profit: $500+/day", flush=True)
        print("", flush=True)
    
    print("🚀 Starting swing orchestrator (15-minute cycles)...", flush=True)
    print(f"   Signal window: 9:45 AM - 2:00 PM ET", flush=True)
    print(f"   Position holds: 4 hours - 1 day", flush=True)
    print("", flush=True)
    
    n = 0
    bar_count = 0
    
    while True:
        # Check market hours
        if not ignore_hours and not in_market_hours():
            print(f"[{bar_count:05d}] Market closed, sleeping 30s...", flush=True)
            await asyncio.sleep(30)
            continue
        
        # Get market data
        ctx = await broker.resolve_market_context()
        tick = ctx.get("tick") or {}
        price = float(tick.get("price") or tick.get("last") or 0)
        high = float(tick.get("high") or price)
        low = float(tick.get("low") or price)
        
        if price <= 0:
            print(f"[{bar_count:05d}] No price data, skipping...", flush=True)
            await asyncio.sleep(60)
            continue
        
        # Update swing engine with new bar
        swing_engine.update(price, high, low)
        bar_count += 1
        
        # Monitor existing positions
        monitor_result = orch.position_manager.monitor_tick(price)
        if monitor_result.exit_requests:
            for exit_req in monitor_result.exit_requests:
                print(f"💰 Position closed: {exit_req.reason} | P&L: ${exit_req.realized_pnl:.2f}", flush=True)
                orch.session.realized_pnl_today += exit_req.realized_pnl
        
        # Check if we can generate new signal
        if len(orch.position_manager.positions) > 0:
            print(f"[{bar_count:05d}] Position open, monitoring... | P&L today: ${orch.session.realized_pnl_today:.2f}", flush=True)
        else:
            # Try to generate signal
            signal = swing_engine.generate_signal()
            
            if signal:
                print(f"", flush=True)
                print(f"🎯 SWING SIGNAL GENERATED!", flush=True)
                print(f"   Direction: {signal.direction}", flush=True)
                print(f"   Type: {signal.signal_type}", flush=True)
                print(f"   Entry: ${signal.entry_price:.2f}", flush=True)
                print(f"   Stop: {signal.stop_ticks} ticks ($75 risk)", flush=True)
                print(f"   Target: {signal.target_ticks} ticks ($150 profit)", flush=True)
                print(f"   Trend strength: {signal.trend_strength:.2f}", flush=True)
                print(f"   Confidence: {signal.confidence:.2f}", flush=True)
                print(f"   Reason: {signal.reason}", flush=True)
                print(f"   Signals today: {swing_engine.signals_today}/{SWING_MAX_TRADES_PER_DAY}", flush=True)
                print(f"", flush=True)
                
                # Open position
                import uuid
                trade_id = f"SWING-{uuid.uuid4().hex[:10].upper()}"
                orch.position_manager.open_position(
                    symbol="MES",
                    direction=signal.direction,
                    contracts=SWING_CONTRACTS,
                    entry_price=signal.entry_price,
                    stop_ticks=signal.stop_ticks,
                    target_ticks=signal.target_ticks,
                    max_loss=DEFAULT_STOP_TICKS * 1.25 * SWING_CONTRACTS,  # $75
                    trade_id=trade_id,
                    nav_at_entry=orch.risk.account_nav,
                    daily_pnl_at_entry=orch.session.realized_pnl_today,
                    cycle_at_entry=bar_count,
                )
                orch.session.trades_today += 1
                print(f"✅ Position opened | Trade ID: {trade_id}", flush=True)
            else:
                status = "Waiting for signal" if swing_engine.can_trade_now else "Outside trading window"
                print(f"[{bar_count:05d}] {status} | Price: ${price:.2f} | P&L today: ${orch.session.realized_pnl_today:.2f} | Signals: {swing_engine.signals_today}/{SWING_MAX_TRADES_PER_DAY}", flush=True)
        
        # Persist state
        await orch._persist_state()
        
        n += 1
        if cycles is not None and n >= cycles:
            break
        
        # Sleep until next 15-minute check
        await asyncio.sleep(FORWARD_TEST_CYCLE_INTERVAL_S)
    
    print(f"", flush=True)
    print(f"🏁 Session complete!", flush=True)
    print(f"   Total signals: {swing_engine.signals_today}", flush=True)
    print(f"   Daily P&L: ${orch.session.realized_pnl_today:.2f}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="FutureMathics Swing Trading System")
    parser.add_argument("--cycles", type=int, default=None, help="Exit after N cycles")
    parser.add_argument("--ignore-hours", action="store_true", help="Run outside market hours (testing)")
    args = parser.parse_args()
    
    ignore_hours = args.ignore_hours or os.getenv("FM_IGNORE_MARKET_HOURS", "").strip() in {"1", "true", "yes"}
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    asyncio.run(run_swing_trading(cycles=args.cycles, ignore_hours=ignore_hours))


if __name__ == "__main__":
    main()
