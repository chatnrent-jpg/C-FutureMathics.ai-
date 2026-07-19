#!/usr/bin/env python3
"""Step 5A — verify Webull US_FUTURES market data subscription + MES snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_project_env
from engine.webull_futures import fetch_futures_quote, front_month_contract, resolve_trading_symbol
from engine.webull_openapi import webull_configured

load_project_env()


def main() -> None:
    print("=== Step 5A: Webull MES Market Data ===\n")
    if not webull_configured():
        print("FAIL: WEBULL_APP_KEY / WEBULL_APP_SECRET missing.")
        sys.exit(1)

    sym, err = resolve_trading_symbol("MES")
    if err or not sym:
        print(f"FAIL: could not resolve MES contract — {err}")
        sys.exit(1)
    print(f"Tradable MES contract: {sym} (front_month={front_month_contract()})")

    quote = fetch_futures_quote(sym)
    if quote and quote.get("needs_subscription"):
        print("\nSTATUS: SUBSCRIPTION REQUIRED")
        print(f"  Webull says: {quote.get('error')}")
        print("\nYour action (one-time):")
        print("  1. Log in at https://www.webull.com")
        print("  2. Avatar (top-right) -> Advanced Quotes")
        print("  3. Subscribe to **OpenAPI Advanced Quotes** -> **US_FUTURES**")
        print("     (App/QT data does NOT count — must be OpenAPI-specific.)")
        print("\nAfter subscribing, re-run:")
        print("  python scripts/test_webull_market_data.py")
        sys.exit(2)

    if quote and quote.get("price") is not None:
        print("\nSTATUS: PASS — live MES quotes enabled")
        print(f"  last={quote.get('price')} bid={quote.get('bid')} ask={quote.get('ask')}")
        print(f"  volume={quote.get('volume')} source={quote.get('source')}")
        sys.exit(0)

    print("\nSTATUS: FAIL — no quote returned (unexpected)")
    print(f"  payload: {quote}")
    sys.exit(1)


if __name__ == "__main__":
    main()
