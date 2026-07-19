#!/usr/bin/env python3
"""Create and verify Webull OpenAPI access token (2FA via Webull app)."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_env_file, load_project_env
from engine.webull_openapi import signed_request, webull_api_host, webull_configured

load_project_env()
load_env_file(Path(r"C:\Volumewatch\.env.local"))

ENV_PATH = ROOT / ".env.local"


def _upsert_env(key: str, value: str) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    ENV_PATH.write_text(text, encoding="utf-8")


def create_token() -> tuple[str | None, str | None]:
    status, payload, err = signed_request("POST", "/openapi/auth/token/create")
    if err:
        return None, err
    if not isinstance(payload, dict):
        return None, f"Unexpected create response: {payload!r}"
    token = str(payload.get("token") or "").strip()
    if not token:
        return None, f"No token in response: {payload}"
    return token, None


def check_token(token: str) -> tuple[str | None, str | None]:
    status, payload, err = signed_request(
        "POST",
        "/openapi/auth/token/check",
        body={"token": token},
    )
    if err:
        return None, err
    if not isinstance(payload, dict):
        return None, f"Unexpected check response: {payload!r}"
    return str(payload.get("status") or "").upper() or None, None


def main() -> None:
    wait = "--wait" in sys.argv
    print("=== Webull Access Token Setup (Step 1) ===")
    if not webull_configured():
        print("FAIL: WEBULL_APP_KEY / WEBULL_APP_SECRET missing.")
        print("Run: python scripts/sync_webull_from_volumewatch.py")
        sys.exit(1)

    print(f"Host: {webull_api_host()}")
    print("Creating access token (Webull will SMS your phone)...")

    token, err = create_token()
    if err:
        print(f"FAIL create token: {err}")
        sys.exit(1)

    print(f"Token created (status=PENDING). First 8 chars: {token[:8]}...")
    pending_path = ROOT / ".webull_token_pending"
    pending_path.write_text(token, encoding="utf-8")
    print(f"(Token saved locally for completion — file: .webull_token_pending)")
    print("")
    print("NOW IN YOUR WEBULL APP (you said you're already logged in):")
    print("  1. Menu -> Messages -> OpenAPI Notifications")
    print("  2. Tap the latest verification message -> Check Now")
    print("  3. Enter the SMS code Webull sent -> Confirm")
    print("")
    if not wait:
        print("When done verifying in the app, tell me and I will run the handshake test.")
        print("Or re-run: python scripts/webull_setup_token.py --wait")
        return

    print("Waiting up to 5 minutes for you to verify (polling every 10s)...")

    deadline = time.time() + 300
    while time.time() < deadline:
        status, err = check_token(token)
        if err:
            print(f"  check error: {err}")
        elif status == "NORMAL":
            _upsert_env("WEBULL_ACCESS_TOKEN", token)
            print("")
            print("SUCCESS — token verified (NORMAL) and saved to .env.local")
            print("Run next: python scripts/test_webull_futures.py")
            return
        elif status in {"EXPIRED", "INVALID"}:
            print(f"FAIL — token status={status}. Re-run this script to create a new token.")
            sys.exit(1)
        else:
            print(f"  status={status or 'unknown'} — still waiting...")
        time.sleep(10)

    print("")
    print("TIMEOUT — verification not completed in 5 minutes.")
    print(f"Add manually to .env.local when verified: WEBULL_ACCESS_TOKEN={token}")
    print("Then run: python scripts/test_webull_futures.py")


if __name__ == "__main__":
    main()
