#!/usr/bin/env python3
"""Test CME MES futures market hours logic."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_daily_session import in_market_hours


def test_market_hours() -> None:
    """Test various times to verify market hours logic."""
    tz = ZoneInfo("America/New_York")
    
    test_cases = [
        # (description, datetime, expected_open)
        ("Saturday morning", datetime(2026, 7, 19, 10, 0, tzinfo=tz), False),
        ("Saturday afternoon", datetime(2026, 7, 19, 14, 0, tzinfo=tz), False),
        ("Saturday evening", datetime(2026, 7, 19, 20, 0, tzinfo=tz), False),
        
        ("Sunday before open", datetime(2026, 7, 20, 17, 59, tzinfo=tz), False),
        ("Sunday at open", datetime(2026, 7, 20, 18, 0, tzinfo=tz), True),
        ("Sunday after open", datetime(2026, 7, 20, 20, 0, tzinfo=tz), True),
        
        ("Monday morning", datetime(2026, 7, 21, 9, 30, tzinfo=tz), True),
        ("Monday before maintenance", datetime(2026, 7, 21, 16, 59, tzinfo=tz), True),
        ("Monday maintenance start", datetime(2026, 7, 21, 17, 0, tzinfo=tz), False),
        ("Monday maintenance", datetime(2026, 7, 21, 17, 30, tzinfo=tz), False),
        ("Monday after maintenance", datetime(2026, 7, 21, 18, 0, tzinfo=tz), True),
        ("Monday late night", datetime(2026, 7, 21, 23, 0, tzinfo=tz), True),
        
        ("Tuesday early morning", datetime(2026, 7, 22, 2, 0, tzinfo=tz), True),
        ("Tuesday maintenance", datetime(2026, 7, 22, 17, 15, tzinfo=tz), False),
        
        ("Friday morning", datetime(2026, 7, 25, 10, 0, tzinfo=tz), True),
        ("Friday before close", datetime(2026, 7, 25, 16, 59, tzinfo=tz), True),
        ("Friday at close", datetime(2026, 7, 25, 17, 0, tzinfo=tz), False),
        ("Friday after close", datetime(2026, 7, 25, 18, 0, tzinfo=tz), False),
    ]
    
    print("=" * 80)
    print("CME MES Futures Market Hours Test")
    print("=" * 80)
    print("\nMarket Schedule:")
    print("  Trading:     Sunday 6:00 PM ET → Friday 5:00 PM ET")
    print("  Maintenance: Monday-Thursday 5:00 PM - 6:00 PM ET")
    print("  Closed:      Friday 5:00 PM ET → Sunday 6:00 PM ET")
    print("\n" + "=" * 80)
    
    passed = 0
    failed = 0
    
    for description, test_dt, expected in test_cases:
        result = in_market_hours(test_dt)
        status = "✓ PASS" if result == expected else "✗ FAIL"
        
        if result == expected:
            passed += 1
        else:
            failed += 1
        
        day_name = test_dt.strftime("%A")
        time_str = test_dt.strftime("%I:%M %p")
        expected_str = "OPEN" if expected else "CLOSED"
        actual_str = "OPEN" if result else "CLOSED"
        
        print(f"{status} | {day_name:9} {time_str:8} | Expected: {expected_str:6} | Got: {actual_str:6} | {description}")
    
    print("=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    test_market_hours()
