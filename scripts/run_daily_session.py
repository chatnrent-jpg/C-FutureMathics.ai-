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
    VIRTUE_ENTRY_WINDOW_1_END,
    VIRTUE_ENTRY_WINDOW_1_START,
    VIRTUE_ENTRY_WINDOW_2_END,
    VIRTUE_ENTRY_WINDOW_2_START,
    VIRTUE_RTH_CLOSE_HOUR,
    VIRTUE_RTH_CLOSE_MINUTE,
    VIRTUE_RTH_OPEN_HOUR,
    VIRTUE_RTH_OPEN_MINUTE,
    forward_test_force_paper,
    live_allow_overnight,
    primary_data_source,
    virtue_session_mode,
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


def _live_cash_day_window(now: datetime | None = None) -> bool:
    """
    Live cash Temperance window (no overnight): Mon–Fri 09:30–16:45 ET.
    Outside → flatten / stand aside even if Globex is open.
    """
    dt = (now or datetime.now(TZ)).astimezone(TZ)
    if dt.weekday() >= 5:
        return False
    open_t = time(VIRTUE_RTH_OPEN_HOUR, VIRTUE_RTH_OPEN_MINUTE)
    cut = time(VIRTUE_CME_NO_NEW_ENTRY_HOUR, VIRTUE_CME_NO_NEW_ENTRY_MINUTE)
    return open_t <= dt.time() < cut


def virtue_session_open(now: datetime | None = None) -> bool:
    """
    Session gate for virtue main.

    CME (Databento): Sun 18:00 ET → Fri 17:00 ET, daily maint 17:00–18:00 ET.
    RTH (Alpaca fallback): Mon–Fri 09:30–16:00 ET only.
    Live cash default: daytime-only window (no overnight) unless FM_LIVE_ALLOW_OVERNIGHT=1.
    """
    if virtue_session_mode() == "cme":
        if not in_market_hours(now):
            return False
        if not live_allow_overnight():
            return _live_cash_day_window(now)
        return True
    return in_rth_hours(now)


def _et_now(now: datetime | None = None) -> datetime:
    """Normalize any aware/naive stamp into America/New_York (US/Eastern market stream)."""
    dt = now or datetime.now(TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def allow_new_entries(now: datetime | None = None) -> bool:
    """
    Primary RTH entry windows (America/New_York):
      09:45–11:30 ET and 13:45–15:55 ET (start inclusive, end exclusive).

    Session may still be open outside these windows for manage/exit only.
    Extreme-trend override lives in virtue_entries_allowed(..., adx=, blend=).
    """
    dt = _et_now(now)
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    windows = (
        (
            time(*VIRTUE_ENTRY_WINDOW_1_START),
            time(*VIRTUE_ENTRY_WINDOW_1_END),
        ),
        (
            time(*VIRTUE_ENTRY_WINDOW_2_START),
            time(*VIRTUE_ENTRY_WINDOW_2_END),
        ),
    )
    return any(start <= t < end for start, end in windows)


def extreme_trend_entry_ok(*, adx: float, blend: float) -> bool:
    """Courage override: finished trend may enter outside primary windows."""
    from engine.config import (
        VIRTUE_EXTREME_ADX_OVERRIDE,
        VIRTUE_EXTREME_BLEND_LONG,
        VIRTUE_EXTREME_BLEND_SHORT,
    )

    try:
        a = float(adx)
        b = float(blend)
    except (TypeError, ValueError):
        return False
    if a < float(VIRTUE_EXTREME_ADX_OVERRIDE):
        return False
    return b <= float(VIRTUE_EXTREME_BLEND_SHORT) or b >= float(VIRTUE_EXTREME_BLEND_LONG)


def below_vwap_short_entry_ok(*, blend: float, vwap_score: float | None = None) -> bool:
    """
    Lunch / off-window Courage: clear session-VWAP bear (blend + VWAP score).
    Aligns with short enter band (≤42), not the stricter extreme≤35 cut.
    """
    from engine.config import (
        VIRTUE_MACRO_BEAR_VWAP_SCORE_MAX,
        VIRTUE_SCORE_SHORT_ENTER,
    )

    try:
        b = float(blend)
    except (TypeError, ValueError):
        return False
    if b > float(VIRTUE_SCORE_SHORT_ENTER):
        return False
    if vwap_score is None:
        return True
    try:
        return float(vwap_score) <= float(VIRTUE_MACRO_BEAR_VWAP_SCORE_MAX)
    except (TypeError, ValueError):
        return False


def virtue_entries_allowed(
    now: datetime | None = None,
    *,
    adx: float | None = None,
    blend: float | None = None,
    vwap_score: float | None = None,
) -> bool:
    """
    True when new LONG/SHORT entries are allowed.

    Requires session open AND (primary RTH windows OR extreme-trend override
    OR clear below-VWAP short structure).
    CME overnight mode additionally blocks 16:45–17:00 ET pre-maintenance.
    """
    if not virtue_session_open(now):
        return False
    in_window = allow_new_entries(now)
    if not in_window:
        if adx is None or blend is None:
            return False
        extreme = extreme_trend_entry_ok(adx=float(adx), blend=float(blend))
        structural_short = below_vwap_short_entry_ok(
            blend=float(blend), vwap_score=vwap_score
        )
        if not (extreme or structural_short):
            return False
    dt = _et_now(now)
    if virtue_session_mode() == "cme" and live_allow_overnight():
        if dt.weekday() < 5:
            t = dt.time()
            cut = time(VIRTUE_CME_NO_NEW_ENTRY_HOUR, VIRTUE_CME_NO_NEW_ENTRY_MINUTE)
            maint_start = time(17, 0)
            if cut <= t < maint_start:
                return False
    return True


def virtue_session_label() -> str:
    """Human-readable timetable for boot logs / dashboard."""
    mode = virtue_session_mode()
    src = primary_data_source()
    windows = "entries=09:45-11:30&13:45-15:55ET+extreme"
    if mode == "cme":
        if live_allow_overnight():
            return (
                f"CME_GLOBEX data={src} hours=Sun18:00-Fri17:00ET "
                f"maint=17:00-18:00 {windows} overnight=ON"
            )
        return (
            f"CME_CASH_DAY data={src} hours=Mon-Fri 09:30-16:45ET "
            f"{windows} overnight=OFF"
        )
    return (
        f"RTH_CASH data={src} hours=Mon-Fri 09:30-16:00ET {windows}"
    )

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
