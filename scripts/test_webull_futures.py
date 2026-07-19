#!/usr/bin/env python3
"""Probe Webull futures connectivity for FutureMathics (no orders placed)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_env_file, load_project_env
from engine.webull_futures import (
    front_month_contract,
    fetch_futures_quote,
    get_account_balance,
    get_futures_positions,
    list_accounts,
    resolve_trading_symbol,
)
from engine.webull_openapi import webull_configured, webull_api_host, webull_is_sandbox

# Also pull VolumeWatch keys if FutureMathics .env.local empty
load_project_env()
load_env_file(Path(r"C:\Volumewatch\.env.local"))


def main() -> None:
    print("=== FutureMathics Webull Futures Handshake ===")
    if not webull_configured():
        print("FAIL: WEBULL_APP_KEY / WEBULL_APP_SECRET not set.")
        print("Run: python scripts/sync_webull_from_volumewatch.py")
        sys.exit(1)

    print(f"Host: {webull_api_host()} (sandbox={webull_is_sandbox()})")
    sym, sym_err = resolve_trading_symbol()
    contract = sym or front_month_contract()
    print(f"Tradable MES contract: {contract}" + (f" ({sym_err})" if sym_err else ""))

    accounts, err = list_accounts()
    if err:
        print(f"Account list ERROR: {err}")
    else:
        print(f"Accounts ({len(accounts)}):")
        for row in accounts:
            label = row.get("account_label") or row.get("account_type")
            num = row.get("account_number") or "?"
            cls = row.get("account_class") or "?"
            print(f"  - {label} (#{num}) class={cls}")
            print(f"      api_id={row.get('account_id')}")

    bal = get_account_balance()
    if bal.get("ok"):
        print(f"Balance OK — account={bal.get('account_id')} equity={bal.get('equity')} buying_power={bal.get('buying_power')}")
    else:
        print(f"Balance FAIL: {bal.get('error')}")

    quote = fetch_futures_quote(contract)
    if quote and quote.get("needs_subscription"):
        print(f"Quote: subscription required — {quote.get('error')}")
        print("  -> Run Step 5A: python scripts/test_webull_market_data.py")
    elif quote and quote.get("price") is not None:
        print(f"Quote OK — {contract} last={quote.get('price')} bid={quote.get('bid')} ask={quote.get('ask')}")
    else:
        print("Quote: unavailable — engine uses local price sim until quotes work")

    positions = get_futures_positions()
    print(f"Open futures positions: {len(positions)}")
    for p in positions[:5]:
        print(f"  - {p.get('symbol')} qty={p.get('quantity') or p.get('qty')}")

    print("")
    print("Step 2 OK when Balance shows your Futures account (WEBULL_FUTURES_ACCOUNT_ID in .env.local)")
    print("Next step: python scripts/run_daily_session.py --ignore-hours --cycles 5")


if __name__ == "__main__":
    main()
