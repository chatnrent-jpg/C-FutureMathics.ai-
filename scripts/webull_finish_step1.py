#!/usr/bin/env python3
"""After app verification: create token, detect NORMAL status, save to .env.local."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_env_file, load_project_env
from engine.webull_openapi import signed_request, webull_configured

load_project_env()
load_env_file(Path(r"C:\Volumewatch\.env.local"))

ENV_PATH = ROOT / ".env.local"


def upsert_env(key: str, value: str) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    text = pattern.sub(line, text) if pattern.search(text) else (text + ("\n" if text and not text.endswith("\n") else "") + line + "\n")
    ENV_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    import os

    if not webull_configured():
        print("FAIL: WEBULL_APP_KEY / WEBULL_APP_SECRET missing.")
        sys.exit(1)

    pending_path = ROOT / ".webull_token_pending"

    # Try existing env token
    existing = (os.getenv("WEBULL_ACCESS_TOKEN") or os.getenv("WEBULL_TOKEN") or "").strip()
    if existing:
        status, payload, err = signed_request("POST", "/openapi/auth/token/check", body={"token": existing})
        if not err and isinstance(payload, dict) and str(payload.get("status", "")).upper() == "NORMAL":
            upsert_env("WEBULL_ACCESS_TOKEN", existing)
            print("OK: existing WEBULL_ACCESS_TOKEN is NORMAL and saved.")
            sys.exit(0)

    # Try pending file from prior create (user may have verified in app)
    if pending_path.exists():
        token = pending_path.read_text(encoding="utf-8").strip()
        if token:
            _, payload, err = signed_request("POST", "/openapi/auth/token/check", body={"token": token})
            if not err and isinstance(payload, dict):
                tok_status = str(payload.get("status") or "").upper()
                if tok_status == "NORMAL":
                    upsert_env("WEBULL_ACCESS_TOKEN", token)
                    pending_path.unlink(missing_ok=True)
                    print("OK: verified token saved to .env.local")
                    sys.exit(0)
                print(f"Pending file token status={tok_status}")

    status, payload, err = signed_request("POST", "/openapi/auth/token/create")
    if err:
        print(f"FAIL create: {err}")
        sys.exit(1)
    if not isinstance(payload, dict):
        print(f"FAIL unexpected payload: {payload!r}")
        sys.exit(1)

    token = str(payload.get("token") or "").strip()
    tok_status = str(payload.get("status") or "").upper()
    if not token:
        print(f"FAIL no token in response: {payload}")
        sys.exit(1)

    if tok_status != "NORMAL":
        chk_status, chk_payload, chk_err = signed_request(
            "POST", "/openapi/auth/token/check", body={"token": token}
        )
        if not chk_err and isinstance(chk_payload, dict):
            tok_status = str(chk_payload.get("status") or tok_status).upper()

    if tok_status == "NORMAL":
        upsert_env("WEBULL_ACCESS_TOKEN", token)
        pending_path.unlink(missing_ok=True)
        print("OK: token NORMAL — saved WEBULL_ACCESS_TOKEN to .env.local")
        sys.exit(0)

    pending_path.write_text(token, encoding="utf-8")
    print(f"Created new token (first 8 chars: {token[:8]}...). Status={tok_status}.")
    print("Verify in Webull app: Menu -> Messages -> OpenAPI Notifications -> enter SMS code.")
    print("Then reply SUCCESSFUL and I will save it.")
    sys.exit(1)


if __name__ == "__main__":
    main()
