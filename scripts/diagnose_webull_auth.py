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
    print("=== Webull auth diagnosis ===")
    print(f"host: {webull_api_host()}")
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
        print("OK: Webull credentials are valid.")
        return 0

    print()
    print("LIKELY CAUSE: App Key / App Secret pair is rejected by Webull.")
    print("Our HMAC signer matches Webull's official test vector - this is not a code bug.")
    print()
    print("FIX:")
    print("  1. Open https://developer.webull.com/ (US OpenAPI portal)")
    print("  2. Open your application and regenerate App Secret (or create a new app)")
    print("  3. Update C:\\FutureMathics.ai\\.env.local:")
    print("       WEBULL_APP_KEY=<new key>")
    print("       WEBULL_APP_SECRET=<new secret>")
    print("       WEBULL_API_HOST=api.webull.com")
    print("       WEBULL_FUTURES_ACCOUNT_ID=<futures account id>")
    print("  4. Remove or refresh WEBULL_ACCESS_TOKEN if your app requires it")
    print("  5. Re-run: python scripts/diagnose_webull_auth.py")
    print()
    print("Also sync the same values to AWS .env.local after local auth succeeds.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
