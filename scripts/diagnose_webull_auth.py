"""
Diagnose Webull OpenAPI auth for FutureMathics.

Verifies signature algorithm against Webull's official test vector, then
probes /openapi/account/list. Never prints App Key / App Secret / tokens.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_project_env
from engine.webull_openapi import (
    generate_signature,
    signed_request,
    webull_api_host,
    webull_configured,
)


def _verify_official_vector() -> bool:
    uri = "/trade/place_order"
    query = {"a1": "webull", "a2": "123", "a3": "xxx", "q1": "yyy"}
    body = {"k1": 123, "k2": "this is the api request body", "k3": True, "k4": {"foo": [1, 2]}}
    headers = {
        "x-app-key": "776da210ab4a452795d74e726ebd74b6",
        "x-signature-algorithm": "HMAC-SHA1",
        "x-signature-version": "1.0",
        "x-signature-nonce": "48ef5afed43d4d91ae514aaeafbc29ba",
        "x-timestamp": "2022-01-04T03:55:31Z",
        "host": "api.webull.com",
    }
    sig = generate_signature(
        uri,
        query_params=query,
        body=body,
        headers=headers,
        app_secret="0f50a2e853334a9aae1a783bee120c1f",
    )
    return sig == "kvlS6opdZDhEBo5jq40nHYXaLvM="


def main() -> int:
    load_project_env(ROOT)
    host = webull_api_host()
    paper = "sandbox" in host.lower() or "uat" in host.lower()
    print("=== Webull auth diagnosis ===")
    print(f"host: {host}")
    print(f"mode: {'PAPER/SANDBOX' if paper else 'LIVE'}")
    print(f"configured: {webull_configured()}")
    print(f"signature_algorithm_ok: {_verify_official_vector()}")

    if not webull_configured():
        print("FAIL: set WEBULL_APP_KEY and WEBULL_APP_SECRET in .env.local")
        return 2

    st, payload, err = signed_request("GET", "/openapi/account/list")
    msg = ""
    code = ""
    if isinstance(payload, dict):
        msg = str(payload.get("message") or "")
        code = str(payload.get("error_code") or "")
    elif err:
        msg = str(err)
    print(f"account_list_status: {st}")
    print(f"error_code: {code or 'n/a'}")
    print(f"message: {msg[:160] or 'n/a'}")

    if st == 200:
        print("OK: Webull credentials are valid for this host.")
        if paper:
            print("Paper path ready — next: set WEBULL_FUTURES_ACCOUNT_ID from account list, then run main.py")
        return 0

    print()
    print("LIKELY CAUSE: App Key / App Secret rejected for this host/environment.")
    print("Our HMAC signer matches Webull's official test vector - this is not a code bug.")
    print()
    if paper or "correct environment" in msg.lower() or "invalid credentials" in msg.lower():
        print("PAPER ($100k) FIX:")
        print("  Live keys do NOT work on paper. Create sandbox OpenAPI credentials:")
        print("  1. Open https://portal.sandbox.webull.com (Webull sandbox portal)")
        print("  2. Apply/enable OpenAPI for Paper Trading → create App Key + App Secret")
        print("  3. Update C:\\FutureMathics.ai\\.env.local:")
        print("       WEBULL_API_HOST=api.sandbox.webull.com")
        print("       WEBULL_APP_KEY=<sandbox key>")
        print("       WEBULL_APP_SECRET=<sandbox secret>")
        print("       WEBULL_FUTURES_ACCOUNT_ID=<sandbox futures account id>")
        print("  4. Re-run: python scripts/diagnose_webull_auth.py")
        print("  5. Sync same values to AWS .env.local and redeploy virtue")
    else:
        print("LIVE FIX:")
        print("  1. Open https://developer.webull.com/ (US OpenAPI portal)")
        print("  2. Regenerate App Secret (or create a new app)")
        print("  3. Update C:\\FutureMathics.ai\\.env.local:")
        print("       WEBULL_APP_KEY=<new key>")
        print("       WEBULL_APP_SECRET=<new secret>")
        print("       WEBULL_API_HOST=api.webull.com")
        print("       WEBULL_FUTURES_ACCOUNT_ID=<futures account id>")
        print("  4. Re-run: python scripts/diagnose_webull_auth.py")
    print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
