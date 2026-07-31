#!/usr/bin/env python3
"""One-shot Databento MES L1 smoke test (requires DATABENTO_API_KEY in .env.local)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.databento_mes_feed import smoke_databento_mes


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    asyncio.run(smoke_databento_mes(seconds=seconds))
