#!/usr/bin/env python3
"""Quick check if market is currently open."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_daily_session import in_market_hours

now = datetime.now(ZoneInfo("America/New_York"))
is_open = in_market_hours(now)

print(f"Current Time: {now.strftime('%A, %B %d, %Y at %I:%M:%S %p %Z')}")
print(f"Market Status: {'🟢 OPEN' if is_open else '🔴 CLOSED'}")
print()
print("CME MES Futures Hours:")
print("  Trading:     Sunday 6:00 PM ET → Friday 5:00 PM ET")
print("  Maintenance: Monday-Thursday 5:00 PM - 6:00 PM ET (daily)")
print("  Weekend:     Friday 5:00 PM ET → Sunday 6:00 PM ET (CLOSED)")
