#!/usr/bin/env python3
"""
Preflight checklist before arming $15k live MES cash.

Does NOT place orders. Prints PASS/FAIL for each live-safety gate.

Usage:
  python scripts/preflight_live_cash.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_project_env


def main() -> int:
    load_project_env(ROOT)
    from engine.config import (
        live_allow_overnight,
        live_cash_arming_status,
        live_max_mes_contracts,
        max_mes_contracts,
        primary_data_source,
        trading_halted,
        virtue_session_mode,
        webull_credentials_configured,
        forward_test_force_paper,
    )
    from engine.webull_futures import (
        front_month_contract,
        futures_live_orders_allowed,
        get_account_balance,
        get_futures_positions,
        webull_is_sandbox,
    )
    from engine.databento_mes_feed import DatabentoMESFeed

    print("=== FutureMathics LIVE CASH PREFLIGHT ===\n")
    fails = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal fails
        mark = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))

    armed, reason = live_cash_arming_status()
    check(not forward_test_force_paper(), "Paper lock OFF (FM_FORWARD_TEST_MODE=0)", f"paper={forward_test_force_paper()}")
    check(not trading_halted(), "FM_TRADING_HALTED unset", "kill switch must be off to trade")
    check(not webull_is_sandbox(), "Webull host is live (not sandbox)")
    check(webull_credentials_configured(), "Webull credentials present")
    check(futures_live_orders_allowed(), "FM_ALLOW_LIVE_ORDERS=1")
    check(primary_data_source() == "databento", "FM_DATA_SOURCE=databento", primary_data_source())
    check(virtue_session_mode() == "cme", "VIRTUE_SESSION_MODE=cme", virtue_session_mode())
    forced = (os.getenv("WEBULL_FUTURES_SYMBOL") or os.getenv("FM_EXECUTION_CONTRACT") or "").strip()
    check(bool(forced), "WEBULL_FUTURES_SYMBOL set", forced or "MISSING")
    check(max_mes_contracts() <= 2, "MES contract hard cap <= 2", f"cap={max_mes_contracts()} live={live_max_mes_contracts()}")
    check(not live_allow_overnight(), "Overnight OFF for live cash (safer default)", "set FM_LIVE_ALLOW_OVERNIGHT=1 only if intentional")
    check(armed, "live_cash_arming_status", reason)

    bal = get_account_balance()
    check(bool(bal.get("ok")), "Webull balance API", str(bal.get("error") or f"equity={bal.get('equity')}"))
    eq = float(bal.get("equity") or 0.0)
    check(eq >= 14000.0, "Futures equity roughly funded (~$15k)", f"equity={eq:.2f}")

    positions, pos_err = get_futures_positions()
    check(pos_err is None, "Webull positions API", pos_err or f"n={len(positions)}")
    if pos_err is None:
        check(len(positions) == 0, "Account flat before first live boot", f"open_rows={len(positions)}")

    wb_sym = front_month_contract()
    print(f"\nWebull execution symbol: {wb_sym}")

    async def _db() -> None:
        feed = DatabentoMESFeed()
        ok, msg = await feed.health_check()
        check(ok, "Databento live MES quote", msg)
        q = await feed.fetch_quote(max_age_s=10.0)
        db_sym = str((q or {}).get("symbol") or feed.front_symbol or "")
        print(f"Databento front symbol: {db_sym}")
        if db_sym and wb_sym:
            check(
                db_sym.upper() == str(wb_sym).upper(),
                "Databento symbol == Webull symbol",
                f"{db_sym} vs {wb_sym}",
            )
        feed.stop()

    try:
        asyncio.run(_db())
    except Exception as exc:
        check(False, "Databento health", str(exc))

    print("\n=== RESULT ===")
    if fails:
        print(f"NOT READY — {fails} check(s) failed. Do NOT arm live cash.")
        print("Keep FM_FORWARD_TEST_MODE=1 (or unset) until every check PASSes.")
        return 2
    print("READY — all gates passed.")
    print("To arm: FM_FORWARD_TEST_MODE=0 FM_ALLOW_LIVE_ORDERS=1 WEBULL_FUTURES_SYMBOL=<front>")
    print("Kill switch anytime: FM_TRADING_HALTED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
