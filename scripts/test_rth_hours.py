#!/usr/bin/env python3
"""Test cash RTH gate used by virtue loop (Alpaca / RTH-only mode)."""

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
os.environ["FM_DATA_SOURCE"] = "alpaca"

from scripts.run_daily_session import in_rth_hours, virtue_session_open


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


def test_entry_cutoff() -> None:
    from scripts.run_daily_session import virtue_entries_allowed

    tz = ZoneInfo("America/New_York")
    cases = [
        ("Monday open", datetime(2026, 7, 27, 9, 30, tzinfo=tz), True),
        ("Monday 14:59", datetime(2026, 7, 27, 14, 59, tzinfo=tz), True),
        ("Monday 15:00 trend ok", datetime(2026, 7, 27, 15, 0, tzinfo=tz), True),
        ("Monday 15:44 still ok", datetime(2026, 7, 27, 15, 44, tzinfo=tz), True),
        ("Monday 15:45 cutoff", datetime(2026, 7, 27, 15, 45, tzinfo=tz), False),
        ("Monday 15:59", datetime(2026, 7, 27, 15, 59, tzinfo=tz), False),
        ("Monday 16:00 closed", datetime(2026, 7, 27, 16, 0, tzinfo=tz), False),
        ("Saturday", datetime(2026, 7, 25, 12, 0, tzinfo=tz), False),
    ]
    for desc, dt, expected in cases:
        got = virtue_entries_allowed(dt)
        assert got == expected, f"{desc}: expected {expected} got {got} @ {dt}"
    print("ALL ENTRY CUTOFF TESTS PASSED")


if __name__ == "__main__":
    test_rth_hours()
    test_entry_cutoff()
