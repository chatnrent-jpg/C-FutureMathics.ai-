#!/usr/bin/env python3
"""FutureMathics — forward paper session for MES futures (Sun 6PM - Fri 5PM ET)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import (
    FORWARD_TEST_CYCLE_INTERVAL_S,
    FORWARD_TEST_MARKET_CLOSE_HOUR,
    FORWARD_TEST_MARKET_CLOSE_MINUTE,
    FORWARD_TEST_MARKET_OPEN_HOUR,
    FORWARD_TEST_MARKET_OPEN_MINUTE,
    FORWARD_TEST_TIMEZONE,
    HANDSHAKE_EQUITY_BASE,
    VIRTUE_CME_NO_NEW_ENTRY_HOUR,
    VIRTUE_CME_NO_NEW_ENTRY_MINUTE,
    VIRTUE_NO_NEW_ENTRY_HOUR,
    VIRTUE_NO_NEW_ENTRY_MINUTE,
    VIRTUE_RTH_CLOSE_HOUR,
    VIRTUE_RTH_CLOSE_MINUTE,
    VIRTUE_RTH_OPEN_HOUR,
    VIRTUE_RTH_OPEN_MINUTE,
    forward_test_force_paper,
    virtue_rth_only,
)
from engine.futures_broker_adapter import FuturesBrokerAdapter
from engine.futures_orchestrator import FuturesOrchestrator, OrchestratorConfig
from engine.ui_state_bridge import ensure_boot_system_state

logger = logging.getLogger(__name__)
TZ = ZoneInfo(FORWARD_TEST_TIMEZONE)


def in_market_hours(now: datetime | None = None) -> bool:
    """
    Check if CME MES futures market is open.
    
    Trading hours: Sunday 6:00 PM ET through Friday 5:00 PM ET
    Daily maintenance: 5:00 PM - 6:00 PM ET (Mon-Thu)
    Weekend closure: Friday 5:00 PM ET - Sunday 6:00 PM ET
    """
    dt = (now or datetime.now(TZ)).astimezone(TZ)
    weekday = dt.weekday()  # 0=Mon, 1=Tue, ..., 6=Sun
    current_time = dt.time()
    
    # Saturday: Market closed all day
    if weekday == 5:
        return False
    
    # Sunday: Market opens at 6:00 PM ET
    if weekday == 6:
        return current_time >= time(18, 0)
    
    # Friday: Market closes at 5:00 PM ET
    if weekday == 4:
        return current_time < time(17, 0)
    
    # Monday-Thursday: Check for daily maintenance (5:00 PM - 6:00 PM ET)
    if time(17, 0) <= current_time < time(18, 0):
        return False
    
    # Otherwise open (Mon-Thu outside maintenance window)
    return True


def in_rth_hours(now: datetime | None = None) -> bool:
    """
    US cash equity Regular Trading Hours (ET): Mon–Fri 9:30 AM – 4:00 PM.
    Used by virtue loop so Alpaca SPY decisions stay on live tape (no eve/weekend gaps).
    """
    dt = (now or datetime.now(TZ)).astimezone(TZ)
    if dt.weekday() >= 5:  # Sat/Sun
        return False
    open_t = time(VIRTUE_RTH_OPEN_HOUR, VIRTUE_RTH_OPEN_MINUTE)
    close_t = time(VIRTUE_RTH_CLOSE_HOUR, VIRTUE_RTH_CLOSE_MINUTE)
    return open_t <= dt.time() < close_t


def virtue_session_open(now: datetime | None = None) -> bool:
    """Session gate for virtue main: RTH-only (Alpaca) or full CME hours (Databento)."""
    if virtue_rth_only():
        return in_rth_hours(now)
    return in_market_hours(now)


def virtue_entries_allowed(now: datetime | None = None) -> bool:
    """
    True when new LONG/SHORT entries are allowed.

    - RTH mode (Alpaca): after VIRTUE_NO_NEW_ENTRY_* (default 15:45 ET) manage/exit only.
    - CME mode (Databento): overnight OK; block only last 15 minutes before 5:00 PM ET
      daily maintenance (Mon–Fri 16:45–17:00). After the 17:00–18:00 break, entries resume.
    """
    if not virtue_session_open(now):
        return False
    dt = (now or datetime.now(TZ)).astimezone(TZ)
    if virtue_rth_only():
        cutoff = time(VIRTUE_NO_NEW_ENTRY_HOUR, VIRTUE_NO_NEW_ENTRY_MINUTE)
        return dt.time() < cutoff
    # CME: Mon–Fri block ONLY 16:45–17:00 (pre-maintenance). Do NOT blank the overnight session.
    if dt.weekday() < 5:
        cme_cutoff = time(VIRTUE_CME_NO_NEW_ENTRY_HOUR, VIRTUE_CME_NO_NEW_ENTRY_MINUTE)
        maint_start = time(FORWARD_TEST_MARKET_CLOSE_HOUR, FORWARD_TEST_MARKET_CLOSE_MINUTE)
        if cme_cutoff <= dt.time() < maint_start:
            return False
    return True

async def run_session(*, cycles: int | None = None, ignore_hours: bool = False) -> None:
    ensure_boot_system_state()
    broker = FuturesBrokerAdapter()
    orch = FuturesOrchestrator(
        config=OrchestratorConfig(loop_interval_s=FORWARD_TEST_CYCLE_INTERVAL_S, print_state=True),
        broker=broker,
    )
    orch.risk.update_nav(HANDSHAKE_EQUITY_BASE)
    orch.risk.starting_nav = HANDSHAKE_EQUITY_BASE

    if forward_test_force_paper():
        print("  MANUS: FORWARD_TEST_MODE — MES paper routing LOCKED", flush=True)

    print("  Starting MES orchestrator cycles...", flush=True)
    n = 0
    while True:
        if not ignore_hours and not in_market_hours():
            await asyncio.sleep(30)
            continue
        await orch.run_cycle()
        n += 1
        if cycles is not None and n >= cycles:
            break
        await asyncio.sleep(FORWARD_TEST_CYCLE_INTERVAL_S)


def main() -> None:
    parser = argparse.ArgumentParser(description="FutureMathics MES paper session")
    parser.add_argument("--cycles", type=int, default=None, help="Exit after N cycles")
    parser.add_argument("--ignore-hours", action="store_true", help="Run outside RTH")
    args = parser.parse_args()
    ignore_hours = args.ignore_hours or os.getenv("FM_IGNORE_MARKET_HOURS", "").strip() in {"1", "true", "yes"}
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_session(cycles=args.cycles, ignore_hours=ignore_hours))


if __name__ == "__main__":
    main()
