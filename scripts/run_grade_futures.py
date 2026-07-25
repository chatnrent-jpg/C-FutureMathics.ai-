#!/usr/bin/env python3
"""
FutureMathics — VolumeWatch grade-path MES futures session.

Brain: VolumeWatch F→A score (path-dependent)
Body: MES long/short via Webull/paper

Rules:
  Rising (from down): cash &lt;50 → LONG ≥50 → EXIT ≥85 → SHORT ≥90
  Falling (from up): EXIT SHORT ≤50 → cash below 50
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
    GRADE_CONTRACTS,
    GRADE_CYCLE_INTERVAL_S,
    GRADE_HARD_STOP_TICKS,
    GRADE_LONG_ENTRY,
    GRADE_LONG_EXIT,
    GRADE_SHORT_ENTRY,
    GRADE_SHORT_EXIT,
    HANDSHAKE_EQUITY_BASE,
    forward_test_force_paper,
)
from engine.env_loader import load_project_env
from engine.grade_orchestrator import GradeOrchestrator
from engine.ui_state_bridge import ensure_boot_system_state
from scripts.run_daily_session import in_market_hours

load_project_env(ROOT)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("run_grade_futures")


async def run_grade_session(*, cycles: int | None = None, ignore_hours: bool = False) -> None:
    ensure_boot_system_state()
    orch = GradeOrchestrator()
    orch.risk.update_nav(HANDSHAKE_EQUITY_BASE)
    orch.risk.starting_nav = HANDSHAKE_EQUITY_BASE

    print("=" * 60, flush=True)
    print("FutureMathics — VolumeWatch GRADE PATH → MES", flush=True)
    print("=" * 60, flush=True)
    if forward_test_force_paper():
        print("  MODE: PAPER (FORWARD_TEST_MODE)", flush=True)
    print(f"  LONG  entry>={GRADE_LONG_ENTRY}  exit>={GRADE_LONG_EXIT}", flush=True)
    print(f"  SHORT entry>={GRADE_SHORT_ENTRY}  exit<={GRADE_SHORT_EXIT}", flush=True)
    print(f"  Contracts={GRADE_CONTRACTS}  HardStop={GRADE_HARD_STOP_TICKS} ticks", flush=True)
    print(f"  Cycle={GRADE_CYCLE_INTERVAL_S}s", flush=True)
    print("=" * 60, flush=True)

    n = 0
    while True:
        if not ignore_hours and not in_market_hours():
            print("[market closed — sleeping 60s]", flush=True)
            await asyncio.sleep(60)
            continue

        result = await orch.run_cycle()
        n += 1
        logger.info("cycle_result=%s", result)

        if cycles is not None and n >= cycles:
            break
        await asyncio.sleep(GRADE_CYCLE_INTERVAL_S)


def main() -> None:
    parser = argparse.ArgumentParser(description="VolumeWatch grade-path MES futures")
    parser.add_argument("--cycles", type=int, default=None, help="Stop after N cycles")
    parser.add_argument(
        "--ignore-hours",
        action="store_true",
        help="Trade even outside CME hours (testing)",
    )
    args = parser.parse_args()
    ignore = args.ignore_hours or os.getenv("FM_IGNORE_MARKET_HOURS", "").strip() in ("1", "true")
    asyncio.run(run_grade_session(cycles=args.cycles, ignore_hours=ignore))


if __name__ == "__main__":
    main()
