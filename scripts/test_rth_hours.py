#!/usr/bin/env python3
"""Test cash RTH gate used by virtue loop."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


if __name__ == "__main__":
    test_rth_hours()
