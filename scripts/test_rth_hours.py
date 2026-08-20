#!/usr/bin/env python3
"""Test cash RTH gate + exact entry windows used by virtue loop."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force Alpaca/RTH mode so these assertions stay stable regardless of local Databento keys.
os.environ.pop("DATABENTO_API_KEY", None)
os.environ.pop("VIRTUE_SESSION_MODE", None)
os.environ["FM_DATA_SOURCE"] = "alpaca"
os.environ["VIRTUE_SESSION_MODE"] = "rth"

from scripts.run_daily_session import (
    allow_new_entries,
    extreme_trend_entry_ok,
    in_rth_hours,
    rth_cash_close_flatten_due,
    virtue_entries_allowed,
    virtue_session_open,
)
from engine.rth_hours import filter_intraday_bars_to_rth, timestamp_in_rth


def test_rth_hours() -> None:
    tz = ZoneInfo("America/New_York")
    cases = [
        ("Saturday", datetime(2026, 7, 25, 12, 0, tzinfo=tz), False),
        ("Sunday evening CME open", datetime(2026, 7, 26, 20, 0, tzinfo=tz), False),
        ("Monday before open", datetime(2026, 7, 27, 9, 29, tzinfo=tz), False),
        ("Monday at open", datetime(2026, 7, 27, 9, 30, tzinfo=tz), True),
        ("Monday midday", datetime(2026, 7, 27, 12, 0, tzinfo=tz), True),
        ("Monday before close", datetime(2026, 7, 27, 15, 59, tzinfo=tz), True),
        ("Monday at close", datetime(2026, 7, 27, 16, 0, tzinfo=tz), False),
        ("Monday evening", datetime(2026, 7, 27, 20, 0, tzinfo=tz), False),
        ("Friday RTH", datetime(2026, 7, 24, 10, 0, tzinfo=tz), True),
        ("Friday after close", datetime(2026, 7, 24, 16, 5, tzinfo=tz), False),
    ]
    for desc, dt, expected in cases:
        got = in_rth_hours(dt)
        assert got == expected, f"{desc}: expected {expected} got {got} @ {dt}"
        assert virtue_session_open(dt) == expected, f"virtue_session_open mismatch {desc}"
    print("ALL RTH HOURS TESTS PASSED")


def test_rth_cash_close_flatten() -> None:
    tz = ZoneInfo("America/New_York")
    cases = [
        ("Monday 15:59:54", datetime(2026, 7, 27, 15, 59, 54, tzinfo=tz), False),
        ("Monday 15:59:55", datetime(2026, 7, 27, 15, 59, 55, tzinfo=tz), True),
        ("Monday 16:00:00", datetime(2026, 7, 27, 16, 0, 0, tzinfo=tz), True),
        ("Monday 09:30", datetime(2026, 7, 27, 9, 30, tzinfo=tz), False),
        ("Saturday 16:00", datetime(2026, 7, 25, 16, 0, tzinfo=tz), False),
    ]
    for desc, dt, expected in cases:
        got = rth_cash_close_flatten_due(dt)
        assert got == expected, f"flatten_due {desc}: expected {expected} got {got}"
    knife = datetime(2026, 7, 27, 15, 59, 55, tzinfo=tz)
    assert in_rth_hours(knife) is True  # session still open for a mark price
    assert virtue_entries_allowed(knife) is False
    assert virtue_entries_allowed(knife, adx=55.0, blend=29.0) is False
    print("ALL RTH FLATTEN TESTS PASSED")


def test_intraday_bars_between_time() -> None:
    """Force-restrict stream: 09:30–16:00 ET inclusive; drop pre/post."""
    bars = [
        {"timestamp": "2026-07-27T13:29:00Z", "close": 1.0},  # 09:29 ET
        {"timestamp": "2026-07-27T13:30:00Z", "close": 2.0},  # 09:30 ET
        {"timestamp": "2026-07-27T16:00:00Z", "close": 3.0},  # 12:00 ET
        {"timestamp": "2026-07-27T20:00:00Z", "close": 4.0},  # 16:00 ET
        {"timestamp": "2026-07-27T20:01:00Z", "close": 5.0},  # 16:01 ET
    ]
    kept = filter_intraday_bars_to_rth(bars, timeframe="5Min")
    closes = [row["close"] for row in kept]
    assert closes == [2.0, 3.0, 4.0]
    assert timestamp_in_rth("2026-07-27T13:29:00Z") is False
    assert timestamp_in_rth("2026-07-27T20:00:00Z") is True
    daily = filter_intraday_bars_to_rth(
        [{"timestamp": "2026-07-27T04:00:00Z", "close": 10.0}],
        timeframe="1Day",
    )
    assert len(daily) == 1
    print("ALL RTH BAR FILTER TESTS PASSED")


def test_entry_windows() -> None:
    """allow_new_entries True in 09:45–11:30 and 13:45–15:55 ET."""
    os.environ["FM_VIRTUE_SIMPLE_STACK"] = "0"
    tz = ZoneInfo("America/New_York")
    cases = [
        ("Monday 09:30 open auction", datetime(2026, 7, 27, 9, 30, tzinfo=tz), False),
        ("Monday 09:44", datetime(2026, 7, 27, 9, 44, tzinfo=tz), False),
        ("Monday 09:45 window open", datetime(2026, 7, 27, 9, 45, tzinfo=tz), True),
        ("Monday 10:30", datetime(2026, 7, 27, 10, 30, tzinfo=tz), True),
        ("Monday 11:29", datetime(2026, 7, 27, 11, 29, tzinfo=tz), True),
        ("Monday 11:30 window closed", datetime(2026, 7, 27, 11, 30, tzinfo=tz), False),
        ("Monday lunch 12:00", datetime(2026, 7, 27, 12, 0, tzinfo=tz), False),
        ("Monday 13:44", datetime(2026, 7, 27, 13, 44, tzinfo=tz), False),
        ("Monday 13:45 PM window", datetime(2026, 7, 27, 13, 45, tzinfo=tz), True),
        ("Monday 15:00", datetime(2026, 7, 27, 15, 0, tzinfo=tz), True),
        ("Monday 15:54", datetime(2026, 7, 27, 15, 54, tzinfo=tz), True),
        ("Monday 15:55 window closed", datetime(2026, 7, 27, 15, 55, tzinfo=tz), False),
        ("Monday 15:59", datetime(2026, 7, 27, 15, 59, tzinfo=tz), False),
        ("Saturday 10:00", datetime(2026, 7, 25, 10, 0, tzinfo=tz), False),
        ("UTC→ET morning window", datetime(2026, 7, 27, 14, 0, tzinfo=ZoneInfo("UTC")), True),
    ]
    for desc, dt, expected in cases:
        got = allow_new_entries(dt)
        assert got == expected, f"allow_new_entries {desc}: expected {expected} got {got} @ {dt}"
        full = virtue_entries_allowed(dt)
        if expected and in_rth_hours(dt):
            assert full is True, f"virtue_entries_allowed {desc}"
        elif not expected:
            assert full is False, f"virtue_entries_allowed should block {desc}"
    # Extreme override: late short after 15:55 while session open
    late = datetime(2026, 7, 27, 15, 59, tzinfo=tz)
    assert allow_new_entries(late) is False
    assert virtue_entries_allowed(late, adx=55.0, blend=29.0) is True
    assert extreme_trend_entry_ok(adx=55.0, blend=29.0) is True
    assert extreme_trend_entry_ok(adx=30.0, blend=29.0) is False
    print("ALL ENTRY WINDOW TESTS PASSED")


def test_simple_stack_skips_open_auction() -> None:
    """Simple stack: no new entries 09:30–09:45; rest of RTH until flatten is open."""
    os.environ.pop("FM_VIRTUE_SIMPLE_STACK", None)
    tz = ZoneInfo("America/New_York")
    cases = [
        ("Monday 09:30 auction", datetime(2026, 7, 27, 9, 30, tzinfo=tz), False),
        ("Monday 09:44 auction", datetime(2026, 7, 27, 9, 44, tzinfo=tz), False),
        ("Monday 09:45 VWAP live", datetime(2026, 7, 27, 9, 45, tzinfo=tz), True),
        ("Monday lunch 12:00", datetime(2026, 7, 27, 12, 0, tzinfo=tz), True),
        ("Monday 15:54", datetime(2026, 7, 27, 15, 54, tzinfo=tz), True),
        ("Monday 15:59:54", datetime(2026, 7, 27, 15, 59, 54, tzinfo=tz), True),
        ("Monday 15:59:55 flatten", datetime(2026, 7, 27, 15, 59, 55, tzinfo=tz), False),
    ]
    for desc, dt, expected in cases:
        got = virtue_entries_allowed(dt)
        assert got == expected, f"simple_stack entries {desc}: expected {expected} got {got}"
    print("ALL SIMPLE STACK AUCTION SKIP TESTS PASSED")


if __name__ == "__main__":
    test_rth_hours()
    test_rth_cash_close_flatten()
    test_intraday_bars_between_time()
    test_entry_windows()
    test_simple_stack_skips_open_auction()
