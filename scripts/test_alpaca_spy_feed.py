"""
Test script for Alpaca SPY feed integration.

Usage:
    python scripts/test_alpaca_spy_feed.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load environment variables from .env.local
from engine.env_loader import load_project_env
load_project_env(ROOT)

from engine.alpaca_spy_feed import AlpacaSPYFeed


async def main() -> None:
    print("=" * 60)
    print("FutureMathics — Alpaca SPY Feed Test")
    print("=" * 60)
    
    feed = AlpacaSPYFeed()
    
    print("\n1️⃣  Configuration Check")
    print("-" * 60)
    if feed.is_configured():
        print("✅ Alpaca credentials found")
        print(f"   API Key: {feed.api_key[:10]}...")
        print(f"   Data URL: {feed.data_url}")
    else:
        print("❌ Alpaca credentials NOT configured")
        print("\nTo configure:")
        print("  1. Get API keys from https://alpaca.markets/")
        print("  2. Add to .env.local:")
        print("     ALPACA_API_KEY=your_key_here")
        print("     ALPACA_API_SECRET=your_secret_here")
        return
    
    print("\n2️⃣  Health Check")
    print("-" * 60)
    ok, msg = await feed.health_check()
    print(f"{'✅' if ok else '❌'} {msg}")
    
    if not ok:
        print("\n❌ Health check failed - cannot proceed")
        return
    
    print("\n3️⃣  Fetch SPY Quote")
    print("-" * 60)
    spy_quote = await feed.fetch_spy_quote()
    
    if not spy_quote:
        print("❌ Failed to fetch SPY quote")
        return
    
    print(f"SPY Price:  ${spy_quote['spy_last']:.2f}")
    print(f"SPY Bid:    ${spy_quote['spy_bid']:.2f}")
    print(f"SPY Ask:    ${spy_quote['spy_ask']:.2f}")
    print(f"Timestamp:  {spy_quote.get('timestamp', 'N/A')}")
    
    print("\n4️⃣  Convert to MES Proxy")
    print("-" * 60)
    mes_tick = feed.get_mes_proxy_tick(spy_quote)
    
    print(f"MES Price:  ${mes_tick['price']:.2f}")
    print(f"MES Bid:    ${mes_tick['bid']:.2f}")
    print(f"MES Ask:    ${mes_tick['ask']:.2f}")
    print(f"Source:     {mes_tick['source']}")
    print(f"Sequence:   {mes_tick['sequence_id']}")
    
    print("\n5️⃣  Continuous Feed Test (10 cycles)")
    print("-" * 60)
    
    for i in range(10):
        await asyncio.sleep(2)
        
        spy_quote = await feed.fetch_spy_quote()
        if spy_quote:
            mes_tick = feed.get_mes_proxy_tick(spy_quote)
            print(f"[{i+1:2d}] SPY: ${spy_quote['spy_last']:7.2f} → MES: ${mes_tick['price']:7.2f}")
        else:
            print(f"[{i+1:2d}] ❌ Quote fetch failed")
    
    print("\n" + "=" * 60)
    print("✅ Alpaca SPY feed test complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. System will auto-use Alpaca if ALPACA_API_KEY is set")
    print("  2. Run: python scripts/run_daily_session.py")
    print("  3. Check dashboard for 'alpaca_spy_proxy' in data source")


if __name__ == "__main__":
    asyncio.run(main())
