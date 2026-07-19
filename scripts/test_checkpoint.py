#!/usr/bin/env python3
"""FutureMathics checkpoint — run before advancing to the next setup step."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_project_env
from engine.config import forward_test_force_paper
from engine.webull_futures import list_accounts, get_account_balance, resolve_account_id
from engine.webull_openapi import webull_configured, webull_api_host

load_project_env()


def ok(label: str, passed: bool, detail: str = "") -> bool:
    mark = "PASS" if passed else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return passed


def main() -> int:
    print("=== FutureMathics Checkpoint ===\n")
    results: list[bool] = []

    results.append(ok("Webull keys configured", webull_configured()))
    if webull_configured():
        accounts, err = list_accounts()
        results.append(ok("Webull account list", not err and len(accounts) >= 1, err or f"{len(accounts)} accounts"))
        acct, acct_err = resolve_account_id()
        results.append(ok("Futures account id resolved", bool(acct) and not acct_err, acct or acct_err or ""))
        bal = get_account_balance()
        results.append(ok("Webull balance API", bool(bal.get("ok")), str(bal.get("error") or f"equity={bal.get('equity')}")))

    try:
        from celine.signals import SANDBOX_VOL_OVERRIDE  # noqa: F401
        results.append(ok("Manus import (SANDBOX_VOL_OVERRIDE)", True))
    except ImportError as exc:
        results.append(ok("Manus import (SANDBOX_VOL_OVERRIDE)", False, str(exc)))

    results.append(ok("Paper mode locked", forward_test_force_paper(), "FORWARD_TEST_MODE=True"))

    state_path = ROOT / "data" / "system_state.json"
    if state_path.exists():
        import json
        data = json.loads(state_path.read_text(encoding="utf-8"))
        cycles = (data.get("session") or {}).get("cycle_count", 0)
        results.append(ok("Local state file readable", True, f"cycles={cycles}"))
    else:
        results.append(ok("Local state file readable", False, "missing — run a session first"))

    print("\nCloud (phone): verify manually — tunnel URL on EC2, cycles/trades updating.")
    print("Desktop dashboard: deferred — use phone cloud URL for now.\n")

    passed = sum(results)
    total = len(results)
    print(f"Score: {passed}/{total} automated checks passed")
    if all(results):
        print("Ready to discuss Step 5 (market data or live path).")
        return 0
    print("Fix FAIL items before Step 5.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
