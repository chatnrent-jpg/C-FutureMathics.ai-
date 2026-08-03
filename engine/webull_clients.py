"""
Webull OpenAPI clients for FutureMathics virtue execution.

Established stack (no Interactive Brokers / ib_insync):
  - ApiClient  → signed HTTP via engine.webull_openapi.signed_request
  - TradeClient → futures account, quotes, positions, orders via engine.webull_futures

These names are the canonical SDK surface for broker.py / main.py.
"""

from __future__ import annotations

import logging
from typing import Any

from engine import config as cfg
from engine.webull_futures import (
    fetch_futures_quote,
    front_month_contract,
    get_account_balance,
    get_futures_positions,
    list_accounts,
    resolve_account_id,
    resolve_trading_symbol,
    submit_futures_market_order,
)
from engine.webull_openapi import (
    signed_request,
    webull_api_host,
    webull_configured,
    webull_is_sandbox,
)

logger = logging.getLogger(__name__)


class ApiClient:
    """Low-level signed Webull OpenAPI client (App Key / App Secret)."""

    def __init__(self, *, timeout_s: float | None = None) -> None:
        self.timeout_s = float(timeout_s if timeout_s is not None else cfg.WEBULL_NETWORK_TIMEOUT_S)
        self.app_key = cfg.webull_app_key()
        self.app_secret = cfg.webull_app_secret()
        self.host = cfg.webull_api_host_name()

    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret) or webull_configured()

    def request(
        self,
        method: str,
        uri: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any, str | None]:
        try:
            return signed_request(
                method,
                uri,
                query=query,
                body=body,
                timeout=self.timeout_s,
            )
        except Exception as exc:
            logger.exception("ApiClient.request_failed method=%s uri=%s err=%s", method, uri, exc)
            return 0, None, str(exc)

    def health(self) -> tuple[bool, str]:
        if not self.configured():
            return False, "webull_not_configured"
        try:
            status, payload, err = self.request("GET", "/openapi/account/list")
            if err:
                return False, err
            if status != 200:
                return False, f"http_{status}"
            return True, f"webull_api_ok host={self.host or webull_api_host()}"
        except Exception as exc:
            logger.exception("ApiClient.health_failed err=%s", exc)
            return False, str(exc)


class TradeClient:
    """
    Futures trade surface for MES (and root product codes).

    Maps virtue guardrails to Webull futures tracking endpoints:
      balance → /openapi/assets/balance
      positions → /openapi/assets/positions
      quote → /openapi/market-data/futures/snapshot
      order → futures market order submit
    """

    def __init__(self, *, api: ApiClient | None = None, product_root: str | None = None) -> None:
        self.api = api or ApiClient()
        self.product_root = (product_root or cfg.EXECUTION_SYMBOL).upper()
        self._contract: str | None = None

    @property
    def account_id(self) -> str | None:
        return cfg.webull_futures_account_id()

    @property
    def sandbox(self) -> bool:
        return webull_is_sandbox()

    def configured(self) -> bool:
        return self.api.configured()

    def resolve_account(self) -> tuple[str | None, str | None]:
        try:
            return resolve_account_id()
        except Exception as exc:
            logger.exception("TradeClient.resolve_account_failed err=%s", exc)
            return None, str(exc)

    def contract_symbol(self, root: str | None = None) -> str:
        code = (root or self.product_root).upper()
        forced = cfg.webull_futures_symbol()
        if forced and code == self.product_root:
            self._contract = forced
            return forced
        try:
            sym, err = resolve_trading_symbol(code)
            if sym:
                self._contract = sym
                return sym
            if err:
                logger.warning("TradeClient.resolve_trading_symbol_fallback err=%s", err)
        except Exception as exc:
            logger.exception("TradeClient.contract_symbol_failed err=%s", exc)
        self._contract = front_month_contract(code)
        return self._contract

    def health_check(self) -> tuple[bool, str]:
        if not self.configured():
            return False, "webull_not_configured"
        try:
            bal = get_account_balance()
            if bal.get("ok"):
                acct = bal.get("account_id") or self.account_id or "?"
                return True, f"webull_trade_ok account={acct} equity={bal.get('equity')}"
            # Fall back to API account list
            ok, detail = self.api.health()
            if ok:
                return True, detail
            return False, str(bal.get("error") or detail)
        except Exception as exc:
            logger.exception("TradeClient.health_check_failed err=%s", exc)
            return False, str(exc)

    def get_balance(self) -> dict[str, Any]:
        try:
            return get_account_balance()
        except Exception as exc:
            logger.exception("TradeClient.get_balance_failed err=%s", exc)
            return {"ok": False, "error": str(exc)}

    def get_positions(self) -> tuple[list[dict[str, Any]], str | None]:
        """Returns (rows, err). err set ⇒ fetch failed (do NOT treat as flat)."""
        try:
            rows, err = get_futures_positions()
            return list(rows or []), err
        except Exception as exc:
            logger.exception("TradeClient.get_positions_failed err=%s", exc)
            return [], str(exc)

    def get_quote(self, contract: str | None = None) -> dict[str, Any] | None:
        sym = contract or self.contract_symbol()
        try:
            return fetch_futures_quote(sym)
        except Exception as exc:
            logger.exception("TradeClient.get_quote_failed err=%s", exc)
            return None

    def submit_market_order(
        self,
        *,
        direction: str,
        contracts: int,
        contract: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        sym = contract or self.contract_symbol()
        try:
            return submit_futures_market_order(
                direction=direction,
                contracts=contracts,
                contract=sym,
            )
        except Exception as exc:
            logger.exception("TradeClient.submit_market_order_failed err=%s", exc)
            return None, str(exc)

    def list_accounts(self) -> tuple[list[dict[str, Any]], str | None]:
        try:
            return list_accounts()
        except Exception as exc:
            logger.exception("TradeClient.list_accounts_failed err=%s", exc)
            return [], str(exc)

    def fetch_broker_truth(self) -> dict[str, Any]:
        """Absolute Webull futures snapshot for reconcile_with_broker()."""
        try:
            if not self.configured():
                return {
                    "ok": False,
                    "equity": 0.0,
                    "realized_pnl": 0.0,
                    "positions": [],
                    "detail": "webull_not_configured",
                }
            bal = self.get_balance()
            if not bal.get("ok"):
                return {
                    "ok": False,
                    "equity": 0.0,
                    "realized_pnl": 0.0,
                    "positions": [],
                    "detail": str(bal.get("error") or "balance_failed"),
                }
            equity = float(bal.get("equity") or 0.0)
            realized = float(
                bal.get("realized_pnl")
                or bal.get("realizedPnl")
                or bal.get("day_pnl")
                or 0.0
            )
            pos_rows, pos_err = self.get_positions()
            if pos_err:
                # Justice: never report ok=True with an empty book when the fetch failed.
                return {
                    "ok": False,
                    "equity": equity,
                    "realized_pnl": realized,
                    "positions": [],
                    "detail": f"positions_fetch_failed:{pos_err}",
                    "account_id": bal.get("account_id") or self.account_id,
                }
            normalized: list[dict[str, Any]] = []
            for row in pos_rows:
                if not isinstance(row, dict):
                    continue
                qty = row.get("quantity") or row.get("qty") or row.get("position") or row.get("size") or 0
                try:
                    size = int(float(qty))
                except (TypeError, ValueError):
                    continue
                if size == 0:
                    continue
                side = str(row.get("side") or row.get("positionSide") or row.get("direction") or "")
                if size < 0 or side.upper() in {"SHORT", "SELL"}:
                    direction = "SHORT"
                    size = abs(size)
                else:
                    direction = "LONG"
                    size = abs(size)
                normalized.append(
                    {
                        "symbol": str(row.get("symbol") or row.get("ticker") or self.contract_symbol()),
                        "direction": direction,
                        "size": size,
                        "price": float(
                            row.get("average_price")
                            or row.get("avgPrice")
                            or row.get("cost_price")
                            or 0.0
                        ),
                        "raw": row,
                    }
                )
            return {
                "ok": True,
                "equity": equity,
                "realized_pnl": realized,
                "positions": normalized,
                "detail": "webull_trade_client",
                "account_id": bal.get("account_id") or self.account_id,
            }
        except Exception as exc:
            logger.exception("TradeClient.fetch_broker_truth_failed err=%s", exc)
            return {
                "ok": False,
                "equity": 0.0,
                "realized_pnl": 0.0,
                "positions": [],
                "detail": str(exc),
            }


__all__ = ["ApiClient", "TradeClient"]
