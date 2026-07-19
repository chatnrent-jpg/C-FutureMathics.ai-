"""Webull futures — accounts, contract symbols, orders, quotes."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from engine.config import EXECUTION_SYMBOL, TICK_SIZE
from engine.webull_openapi import signed_request, webull_configured, webull_is_sandbox

# CME month codes (Jan=F … Dec=Z)
_CME_MONTH = "FGHJKMNQUVXZ"

_ACCOUNT_ID_CACHE: str | None = None
_TRADING_SYMBOL_CACHE: dict[str, str] = {}


def futures_live_orders_allowed() -> bool:
    if webull_is_sandbox():
        return True
    return os.getenv("FM_ALLOW_LIVE_ORDERS", os.getenv("VW_ALLOW_LIVE_ORDERS", "")).strip() == "1"


def _account_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("accounts") or payload.get("account_list") or []
        if isinstance(rows, dict):
            rows = [rows]
    else:
        return []
    return [row for row in rows if isinstance(row, dict)]


def _pick_futures_account_id(payload: Any) -> str | None:
    for row in _account_rows(payload):
        acct_class = str(row.get("account_class") or row.get("accountClass") or "").upper()
        label = str(row.get("account_label") or row.get("accountLabel") or "").upper()
        if acct_class == "FUTURES" or label == "FUTURES":
            acct_id = str(row.get("account_id") or row.get("accountId") or "").strip()
            if acct_id:
                return acct_id
    return None


def _pick_account_id(payload: Any) -> str | None:
    futures = _pick_futures_account_id(payload)
    if futures:
        return futures
    for row in _account_rows(payload):
        acct_id = str(row.get("account_id") or row.get("accountId") or "").strip()
        if acct_id:
            return acct_id
    return None


def _looks_like_openapi_account_id(value: str) -> bool:
    """OpenAPI account ids are long opaque strings; UI account numbers are short."""
    return len(value.strip()) >= 20


def resolve_account_id() -> tuple[str | None, str | None]:
    global _ACCOUNT_ID_CACHE
    forced = os.getenv("WEBULL_FUTURES_ACCOUNT_ID")
    if forced:
        return str(forced).strip(), None
    legacy = os.getenv("WEBULL_ACCOUNT_ID")
    if legacy and _looks_like_openapi_account_id(legacy):
        return str(legacy).strip(), None
    if _ACCOUNT_ID_CACHE:
        return _ACCOUNT_ID_CACHE, None
    status, payload, err = signed_request("GET", "/openapi/account/list")
    if err:
        return None, err
    acct = _pick_account_id(payload)
    if not acct:
        return None, "Webull account list returned no account_id — set WEBULL_FUTURES_ACCOUNT_ID."
    _ACCOUNT_ID_CACHE = acct
    return acct, None


def list_accounts() -> tuple[list[dict[str, Any]], str | None]:
    status, payload, err = signed_request("GET", "/openapi/account/list")
    if err:
        return [], err
    if not isinstance(payload, (list, dict)):
        return [], "Unexpected account list payload"
    out: list[dict[str, Any]] = []
    for row in _account_rows(payload):
        out.append(
            {
                "account_id": row.get("account_id") or row.get("accountId"),
                "account_number": row.get("account_number") or row.get("accountNumber"),
                "account_type": row.get("account_type") or row.get("accountType") or row.get("type"),
                "account_label": row.get("account_label") or row.get("accountLabel"),
                "account_class": row.get("account_class") or row.get("accountClass"),
                "status": row.get("status"),
            }
        )
    return out, None


def resolve_trading_symbol(product_code: str = EXECUTION_SYMBOL) -> tuple[str | None, str | None]:
    """Front tradable contract from Webull instrument list (e.g. MES -> MESU6)."""
    code = product_code.upper()
    if code in _TRADING_SYMBOL_CACHE:
        return _TRADING_SYMBOL_CACHE[code], None
    forced = os.getenv("WEBULL_FUTURES_SYMBOL") or os.getenv("FM_EXECUTION_CONTRACT")
    if forced:
        sym = str(forced).strip().upper()
        _TRADING_SYMBOL_CACHE[code] = sym
        return sym, None

    status, payload, err = signed_request(
        "GET",
        "/openapi/instrument/futures/list",
        query={"category": "US_FUTURES", "code": code, "status": "OC"},
    )
    if err:
        return None, err
    if not isinstance(payload, list) or not payload:
        return None, f"No tradable {code} contracts returned from Webull."
    row = payload[0] if isinstance(payload[0], dict) else None
    if not row:
        return None, f"Unexpected instrument list payload for {code}."
    sym = str(row.get("symbol") or "").strip().upper()
    if not sym:
        return None, f"Instrument list missing symbol for {code}."
    _TRADING_SYMBOL_CACHE[code] = sym
    return sym, None


def front_month_contract(root: str = EXECUTION_SYMBOL, when: datetime | None = None) -> str:
    """Webull tradable symbol, falling back to CME month code if API unavailable."""
    sym, _ = resolve_trading_symbol(root)
    if sym:
        return sym
    dt = when or datetime.now(timezone.utc)
    month_code = _CME_MONTH[dt.month - 1]
    return f"{root.upper()}{month_code}{dt.year % 10}"


def market_data_subscription_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    msg = str(payload.get("message") or payload.get("error_msg") or "")
    code = str(payload.get("error_code") or payload.get("code") or "")
    if "subscribe" in msg.lower() or "US_FUTURES" in msg:
        return msg or code
    if code.upper() in {"UNAUTHORIZED", "FORBIDDEN"} and msg:
        return msg
    return None


def fetch_futures_quote(contract: str | None = None) -> dict[str, Any] | None:
    """Real-time snapshot via OpenAPI market-data (requires US_FUTURES quote subscription)."""
    sym = contract or front_month_contract()
    status, payload, err = signed_request(
        "GET",
        "/openapi/market-data/futures/snapshot",
        query={"symbols": sym, "category": "US_FUTURES"},
    )
    if err:
        sub = market_data_subscription_error(payload if isinstance(payload, dict) else None)
        if sub:
            return {"symbol": sym, "error": sub, "needs_subscription": True, "source": "webull"}
        return None
    if status != 200:
        return None

    row: dict[str, Any] | None = None
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        row = payload[0]
    elif isinstance(payload, dict):
        inner = payload.get("data")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            row = inner[0]
        else:
            row = payload

    if not row:
        return None

    last = row.get("price") or row.get("last") or row.get("close")
    bid = row.get("bid") or row.get("bidPrice")
    ask = row.get("ask") or row.get("askPrice")
    if last is None:
        return None

    return {
        "symbol": str(row.get("symbol") or sym),
        "price": float(last),
        "bid": float(bid) if bid is not None else float(last) - TICK_SIZE,
        "ask": float(ask) if ask is not None else float(last) + TICK_SIZE,
        "volume": row.get("volume"),
        "open_interest": row.get("open_interest"),
        "quote_time": row.get("quote_time"),
        "source": "webull",
    }


def get_account_balance() -> dict[str, Any]:
    if not webull_configured():
        return {"ok": False, "error": "Webull not configured"}
    acct_id, err = resolve_account_id()
    if err or not acct_id:
        return {"ok": False, "error": err or "no account"}
    status, payload, err = signed_request("GET", "/openapi/assets/balance", query={"account_id": acct_id})
    if err:
        return {"ok": False, "error": err, "account_id": acct_id}
    equity = None
    buying_power = None
    if isinstance(payload, dict):
        equity = payload.get("total_net_liquidation_value") or payload.get("total_asset")
        assets = payload.get("account_currency_assets") or payload.get("currency_assets") or []
        if isinstance(assets, list) and assets and isinstance(assets[0], dict):
            buying_power = assets[0].get("buying_power")
        buying_power = buying_power or payload.get("buying_power")
    return {
        "ok": status == 200,
        "account_id": acct_id,
        "equity": equity,
        "buying_power": buying_power,
        "paper_host": webull_is_sandbox(),
    }


def get_futures_positions() -> list[dict[str, Any]]:
    acct_id, err = resolve_account_id()
    if err or not acct_id:
        return []
    status, payload, err = signed_request("GET", "/openapi/assets/positions", query={"account_id": acct_id})
    if err or status != 200:
        return []
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("positions") or []
    else:
        return []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or row.get("ticker") or "")
        inst = str(row.get("instrument_type") or row.get("instrumentType") or "").upper()
        if inst and inst != "FUTURES" and not sym.upper().startswith(EXECUTION_SYMBOL):
            continue
        if sym.upper().startswith(EXECUTION_SYMBOL) or inst == "FUTURES":
            out.append(row)
    return out


def submit_futures_market_order(
    *,
    direction: str,
    contracts: int,
    contract: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not webull_configured():
        return None, "Webull not configured"
    if not futures_live_orders_allowed() and not webull_is_sandbox():
        return None, "Live Webull blocked — set FM_ALLOW_LIVE_ORDERS=1"

    side = direction.upper()
    if side not in {"BUY", "SELL"}:
        return None, "direction must be LONG/SHORT or BUY/SELL"
    if side == "LONG":
        side = "BUY"
    if side == "SHORT":
        side = "SELL"

    try:
        qty_i = int(contracts)
    except (TypeError, ValueError):
        return None, "invalid contracts"
    if qty_i < 1:
        return None, "contracts must be >= 1"

    acct_id, err = resolve_account_id()
    if err or not acct_id:
        return None, err or "no account"

    sym = contract or front_month_contract()
    client_order_id = f"fm{uuid.uuid4().hex}"[:32]
    body = {
        "account_id": acct_id,
        "new_orders": [
            {
                "client_order_id": client_order_id,
                "combo_type": "NORMAL",
                "symbol": sym,
                "instrument_type": "FUTURES",
                "market": "US",
                "order_type": "MARKET",
                "quantity": str(qty_i),
                "side": side,
                "time_in_force": "DAY",
                "entrust_type": "QTY",
            }
        ],
    }
    status, payload, err = signed_request("POST", "/openapi/trade/order/place", body=body)
    if err:
        return None, err
    if status != 200:
        return None, f"Webull futures order failed ({status}): {payload}"

    order_id = client_order_id
    order_status = "submitted"
    if isinstance(payload, dict):
        order_id = str(payload.get("order_id") or payload.get("client_order_id") or client_order_id)
        order_status = str(payload.get("status") or payload.get("order_status") or "submitted")
        inner = payload.get("data")
        if isinstance(inner, dict):
            order_id = str(inner.get("order_id") or inner.get("client_order_id") or order_id)
            order_status = str(inner.get("status") or order_status)

    return {
        "order_id": order_id,
        "status": order_status,
        "symbol": sym,
        "side": side,
        "contracts": qty_i,
        "instrument_type": "FUTURES",
    }, None
