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
    virtue_entries_allowed,
    virtue_session_open,
)


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


def test_entry_windows() -> None:
    """allow_new_entries True in 09:45–11:30 and 13:45–15:55 ET."""
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


if __name__ == "__main__":
    test_rth_hours()
    test_entry_windows()
