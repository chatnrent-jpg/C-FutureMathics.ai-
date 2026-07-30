"""Mock-tick demo for FutureMathicsEngine (sketch harness — not live trading)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import FutureMathicsEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def run_market_listener(engine: FutureMathicsEngine) -> None:
    # Simulates live websocket events incoming rapidly
    mock_ticks = [
        (450.50, 450.00, 450.10, 1.2),
        (452.10, 450.00, 450.15, 1.2),  # Long streak 1
        (453.00, 450.00, 450.20, 1.2),  # Long streak 2 -> Entry Triggers
    ]
    for price, vwap, twap, atr in mock_ticks:
        await engine.process_market_tick(price, vwap, twap, atr)
        await asyncio.sleep(1.0)
    engine.is_running = False


async def main() -> None:
    engine = FutureMathicsEngine()
    await engine.load_persisted_state()

    # Run data processing loop and file-writing loop concurrently
    await asyncio.gather(
        run_market_listener(engine),
        engine.save_state_throttled(),
    )
    logging.info(
        "Demo done position=%s entry=%.2f daily_pnl=%.2f long_streak=%s short_streak=%s",
        engine.state["position"],
        engine.state["entry_price"],
        engine.state["daily_pnl"],
        engine.long_streak,
        engine.short_streak,
    )


if __name__ == "__main__":
    asyncio.run(main())
