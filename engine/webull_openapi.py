"""Signed HTTP client for Webull OpenAPI (adapted from VolumeWatch desk)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from engine.env_loader import load_project_env

load_project_env()


def _env_first(*names: str) -> str | None:
    for name in names:
        val = os.getenv(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def webull_configured() -> bool:
    return bool(_env_first("WEBULL_APP_KEY", "WEBULL_API_KEY") and _env_first("WEBULL_APP_SECRET", "WEBULL_API_SECRET"))


def webull_api_host() -> str:
    return _env_first("WEBULL_API_HOST") or "api.webull.com"


def webull_is_sandbox() -> bool:
    host = webull_api_host().lower()
    return "uat" in host or "sandbox" in host


def _body_json(body: dict[str, Any] | None) -> str:
    if not body:
        return ""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def generate_signature(
    uri: str,
    *,
    query_params: dict[str, str] | None,
    body: dict[str, Any] | None,
    headers: dict[str, str],
    app_secret: str,
) -> str:
    params_dict: dict[str, str] = dict(query_params or {})
    params_dict.update(
        {
            "x-app-key": headers["x-app-key"],
            "x-signature-algorithm": headers["x-signature-algorithm"],
            "x-signature-version": headers["x-signature-version"],
            "x-signature-nonce": headers["x-signature-nonce"],
            "x-timestamp": headers["x-timestamp"],
            "host": headers["host"],
        }
    )
    param_string = "&".join(f"{k}={v}" for k, v in sorted(params_dict.items()))
    body_md5 = ""
    if body:
        body_md5 = hashlib.md5(_body_json(body).encode()).hexdigest().upper()
    sign_string = f"{uri}&{param_string}{'&' + body_md5 if body_md5 else ''}"
    encoded_sign_string = quote(sign_string, safe="")
    secret = f"{app_secret}&"
    digest = hmac.new(secret.encode(), encoded_sign_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


# Default request timeout for signed Webull calls (virtue path uses 5s)
DEFAULT_WEBULL_TIMEOUT_S = 5.0


def signed_request(
    method: str,
    uri: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_WEBULL_TIMEOUT_S,
) -> tuple[int, Any, str | None]:
    """
    Sign and send a Webull OpenAPI request.

    Signature algorithm matches Webull's official US recipe / test vector.
    Returns ``(status_code, json_or_text, error)``.
    """
    app_key = _env_first("WEBULL_APP_KEY", "WEBULL_API_KEY")
    app_secret = _env_first("WEBULL_APP_SECRET", "WEBULL_API_SECRET")
    if not app_key or not app_secret:
        return 0, None, "Webull App Key / App Secret missing."

    host = webull_api_host().strip()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = uuid.uuid4().hex
    body_string = _body_json(body) if body else None

    # Headers used for signature computation (must include host)
    sign_headers: dict[str, str] = {
        "x-app-key": app_key,
        "x-signature-algorithm": "HMAC-SHA1",
        "x-signature-version": "1.0",
        "x-signature-nonce": nonce,
        "x-timestamp": timestamp,
        "host": host,
    }
    signature = generate_signature(
        uri,
        query_params=query,
        body=body,
        headers=sign_headers,
        app_secret=app_secret,
    )

    # Outbound request headers — match official recipe (host is signed, not sent as custom header)
    headers: dict[str, str] = {
        "x-app-key": app_key,
        "x-timestamp": timestamp,
        "x-signature": signature,
        "x-signature-algorithm": "HMAC-SHA1",
        "x-signature-version": "1.0",
        "x-signature-nonce": nonce,
        "x-version": "v2",
    }
    token = _env_first("WEBULL_ACCESS_TOKEN", "WEBULL_TOKEN")
    if token:
        headers["x-access-token"] = token
    if body_string is not None:
        headers["Content-Type"] = "application/json"

    url = f"https://{host}{uri}"
    try:
        resp = requests.request(
            method.upper(),
            url,
            params=query or None,
            data=body_string,
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:
        return 0, None, str(exc)

    try:
        payload: Any = resp.json()
    except Exception:
        payload = resp.text
    if resp.status_code >= 400:
        err = payload if isinstance(payload, str) else json.dumps(payload)
        return resp.status_code, payload, err
    return resp.status_code, payload, None
