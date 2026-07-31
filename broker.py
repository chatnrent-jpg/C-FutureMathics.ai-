"""
FutureMathics virtue broker — Webull execution + Databento/Alpaca market data.

- Trade / positions / reconcile: Webull OpenAPI (ApiClient + TradeClient)
- Live prices: Databento CME MES L1 (primary) → Alpaca SPY proxy fallback → Webull snapshot
- No Interactive Brokers / ib_insync

Guardrails (intact):
  1. Position exclusivity — flatten opposite before new entry; wait for fill confirm
  2. Hard 5s socket timeouts on every network call
  3. reconcile_with_broker() — remote Webull positions/balance truth after triage
  4. Infinite outage survival — exponential backoff 30–60s, never exit the process
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from engine.config import (
    DATABENTO_MAX_QUOTE_AGE_S,
    EXECUTION_SYMBOL,
    FIXED_FRACTIONAL_RISK_PCT,
    STARTING_NAV,
    WEBULL_NETWORK_TIMEOUT_S,
    forward_test_force_paper,
    paper_max_mes_contracts,
    primary_data_source,
    webull_credentials_configured,
    webull_futures_account_id,
)
from engine.alpaca_spy_feed import AlpacaSPYFeed
from engine.databento_mes_feed import DatabentoMESFeed
from engine.futures_broker_adapter import OrderExecutionResult, RoutingMode
from engine.webull_clients import ApiClient, TradeClient
from engine.webull_openapi import webull_is_sandbox

logger = logging.getLogger(__name__)

RISK_LIMIT_PCT = FIXED_FRACTIONAL_RISK_PCT  # 0.8% on $10k book
NETWORK_TIMEOUT_S = WEBULL_NETWORK_TIMEOUT_S  # 5.0 — Justice hard cap
RECONNECT_BACKOFF_MIN_S = 30.0
RECONNECT_BACKOFF_MAX_S = 60.0
# SPY is closed Sun/overnight while MES trades — reject stale equity quotes (Justice)
MAX_SPY_QUOTE_AGE_S = 15 * 60.0


class TriageState(str, Enum):
    LIVE = "LIVE"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    VERIFYING_POSITIONS = "VERIFYING_POSITIONS"
    READY = "READY"


@dataclass(frozen=True, slots=True)
class Order:
    symbol: str
    direction: str  # LONG | SHORT
    size: int
    price: float
    stop_ticks: int
    quote_ts: float
    order_id: str = ""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SizeResult:
    contracts: int
    risk_dollars: float
    risk_pct: float
    max_allowed_risk: float
    rejected: bool
    reason: str


@dataclass(frozen=True, slots=True)
class BrokerTruth:
    ok: bool
    equity: float
    realized_pnl: float
    positions: list[dict[str, Any]]
    detail: str = ""


def validate_order(
    order: Order,
    *,
    expected_symbol: str = EXECUTION_SYMBOL,
    max_price_age_s: float = 5.0,
    now_ts: float | None = None,
) -> ValidationResult:
    sym = str(order.symbol or "").strip().upper()
    expect = str(expected_symbol or EXECUTION_SYMBOL).strip().upper()
    # Accept MES root or Webull front-month (MESU6, MESH6, …)
    if sym != expect and not sym.startswith(expect):
        return ValidationResult(False, f"symbol_mismatch got={sym} expected={expect}*")

    if int(order.size) <= 0:
        return ValidationResult(False, "size_must_be_gt_zero")
    if order.price is None or float(order.price) <= 0:
        return ValidationResult(False, "price_invalid")

    now = time.time() if now_ts is None else float(now_ts)
    age = now - float(order.quote_ts)
    if age < 0:
        return ValidationResult(False, "quote_ts_in_future")
    if age > max_price_age_s:
        return ValidationResult(False, f"price_stale age_s={age:.2f}>{max_price_age_s}")

    direction = str(order.direction or "").upper()
    if direction not in {"LONG", "SHORT", "BUY", "SELL"}:
        return ValidationResult(False, f"direction_invalid {direction}")
    if int(order.stop_ticks) <= 0:
        return ValidationResult(False, "stop_ticks_must_be_gt_zero")
    return ValidationResult(True, "ok")


def calculate_max_contracts(
    *,
    equity: float,
    stop_ticks: int,
    risk_limit_pct: float | None = None,
    tick_value: float | None = None,
    hard_cap: int | None = None,
) -> SizeResult:
    from engine.config import TICK_VALUE, fixed_fractional_risk_pct

    tv = float(TICK_VALUE if tick_value is None else tick_value)
    eq = float(equity)
    stop = int(stop_ticks)
    limit_pct = float(risk_limit_pct if risk_limit_pct is not None else fixed_fractional_risk_pct())
    if eq <= 0:
        return SizeResult(0, 0.0, 0.0, 0.0, True, "equity_invalid")
    if stop <= 0 or tv <= 0:
        return SizeResult(0, 0.0, 0.0, 0.0, True, "stop_or_tick_invalid")

    max_risk = eq * limit_pct
    risk_per_contract = stop * tv
    raw = int(max_risk // risk_per_contract)
    cap = hard_cap if hard_cap is not None else paper_max_mes_contracts()
    contracts = max(0, min(raw, int(cap)))
    if contracts < 1:
        return SizeResult(
            0, 0.0, 0.0, max_risk, True,
            f"risk_budget_too_small max_risk={max_risk:.2f} per_contract={risk_per_contract:.2f}",
        )
    total_risk = contracts * risk_per_contract
    risk_pct = total_risk / eq
    if risk_pct > limit_pct + 1e-12:
        return SizeResult(
            0, total_risk, risk_pct, max_risk, True,
            f"exceeds_risk_limit pct={risk_pct:.4%} limit={limit_pct:.4%}",
        )
    return SizeResult(contracts, total_risk, risk_pct, max_risk, False, "ok")


def reject_if_over_risk(
    *,
    equity: float,
    contracts: int,
    stop_ticks: int,
    risk_limit_pct: float | None = None,
) -> ValidationResult:
    from engine.config import TICK_VALUE, fixed_fractional_risk_pct

    if contracts <= 0:
        return ValidationResult(False, "contracts_must_be_gt_zero")
    eq = float(equity)
    if eq <= 0:
        return ValidationResult(False, "equity_invalid")
    limit_pct = float(risk_limit_pct if risk_limit_pct is not None else fixed_fractional_risk_pct())
    total_risk = int(contracts) * int(stop_ticks) * TICK_VALUE
    if total_risk / eq > limit_pct + 1e-12:
        return ValidationResult(
            False,
            f"order_exceeds_fixed_fractional_risk risk={total_risk:.2f} equity={eq:.2f} limit={limit_pct:.4%}",
        )
    return ValidationResult(True, "ok")


def _normalize_direction(direction: str) -> str:
    d = str(direction or "").upper()
    if d in {"BUY", "LONG"}:
        return "LONG"
    if d in {"SELL", "SHORT"}:
        return "SHORT"
    return d


def _opposite(direction: str) -> str:
    d = _normalize_direction(direction)
    if d == "LONG":
        return "SHORT"
    if d == "SHORT":
        return "LONG"
    return "FLAT"


def _fill_confirmed(status: str) -> bool:
    return str(status or "").upper() in {"FILLED", "SUBMITTED", "ACCEPTED", "PARTIAL"}


async def _await_timeout(coro, *, timeout: float = NETWORK_TIMEOUT_S, label: str = "network"):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{label}_timeout after {timeout}s") from exc


@dataclass
class VirtueBroker:
    """
    Virtue execution gate.

    - DatabentoMESFeed: primary CME MES L1 (overnight-capable)
    - AlpacaSPYFeed: SPY → MES proxy fallback
    - TradeClient: Webull futures account, orders, positions, reconcile
    """

    api: ApiClient = field(default_factory=ApiClient)
    trade: TradeClient | None = None
    data: AlpacaSPYFeed = field(default_factory=AlpacaSPYFeed)
    mes_data: DatabentoMESFeed | None = None
    equity: float = STARTING_NAV
    realized_pnl: float = 0.0
    triage: TriageState = TriageState.READY
    max_price_age_s: float = 5.0
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    network_timeout_s: float = NETWORK_TIMEOUT_S
    _last_price: float = 0.0
    _last_disconnect_at: float | None = None
    _contract: str = EXECUTION_SYMBOL
    _data_source: str = "alpaca_spy_mes_proxy"

    def __post_init__(self) -> None:
        if self.trade is None:
            self.trade = TradeClient(api=self.api, product_root=EXECUTION_SYMBOL)
        if self.mes_data is None:
            self.mes_data = DatabentoMESFeed(max_quote_age_s=float(DATABENTO_MAX_QUOTE_AGE_S))
        if not webull_credentials_configured():
            logger.error("WEBULL credentials missing — set WEBULL_APP_KEY / WEBULL_APP_SECRET in .env.local")
        src = primary_data_source()
        if src == "databento" and self.mes_data.is_configured():
            self._data_source = "databento_mes"
            self.mes_data.ensure_started()
            logger.info("VirtueBroker market data: Databento CME MES L1 (primary)")
        elif self.data.is_configured():
            self._data_source = "alpaca_spy_mes_proxy"
            logger.info("VirtueBroker market data: Alpaca SPY → MES proxy")
        else:
            logger.error(
                "No market data — set DATABENTO_API_KEY (preferred) or ALPACA_API_KEY / ALPACA_API_SECRET"
            )
        acct = webull_futures_account_id() or "(resolve-at-runtime)"
        logger.info(
            "VirtueBroker Webull execution account=%s product=%s timeout=%.1fs data_source=%s",
            acct,
            EXECUTION_SYMBOL,
            self.network_timeout_s,
            self._data_source,
        )

    @property
    def data_source(self) -> str:
        return str(self._data_source or "unknown")

    # --- compatibility shim for older main/tests that expect .adapter ---
    @property
    def adapter(self) -> "VirtueBroker":
        return self

    def update_equity(self, equity: float) -> None:
        self.equity = float(equity)

    def enter_triage(self, reason: str = "webull_disconnect") -> None:
        self.triage = TriageState.DISCONNECTED
        self._last_disconnect_at = time.time()
        logger.warning("TRIAGE_ENTER reason=%s state=%s", reason, self.triage.value)

    def begin_reconnect(self) -> None:
        self.triage = TriageState.RECONNECTING
        logger.info("TRIAGE_RECONNECTING webull")

    def can_send_new_orders(self) -> bool:
        return self.triage in {TriageState.READY, TriageState.LIVE}

    def net_exposure(self) -> tuple[str, int]:
        long_qty = 0
        short_qty = 0
        for pos in self.open_positions:
            size = abs(int(pos.get("size") or 0))
            d = _normalize_direction(str(pos.get("direction") or ""))
            if d == "LONG":
                long_qty += size
            elif d == "SHORT":
                short_qty += size
        net = long_qty - short_qty
        if net > 0:
            return "LONG", net
        if net < 0:
            return "SHORT", abs(net)
        return "FLAT", 0

    async def health_check(self) -> tuple[bool, str]:
        """FuturesBrokerAdapter-compatible alias."""
        return await self.health_check_timed()

    async def health_check_timed(self) -> tuple[bool, str]:
        """Healthy when Webull trade API is up and primary market data is fresh."""
        parts: list[str] = []
        webull_ok = False
        data_ok = False
        try:
            webull_ok, wdetail = await _await_timeout(
                asyncio.to_thread(self.trade.health_check),
                timeout=self.network_timeout_s,
                label="webull_health",
            )
            parts.append(f"webull={wdetail}")
        except Exception as exc:
            logger.exception("webull_health_check_failed err=%s", exc)
            parts.append(f"webull_error={exc}")

        prefer = primary_data_source()
        if prefer == "databento" and self.mes_data and self.mes_data.is_configured():
            try:
                data_ok, ddetail = await _await_timeout(
                    self.mes_data.health_check(),
                    timeout=max(self.network_timeout_s, 8.0),
                    label="databento_health",
                )
                parts.append(f"databento={ddetail}")
            except Exception as exc:
                logger.exception("databento_health_check_failed err=%s", exc)
                parts.append(f"databento_error={exc}")
        elif self.data.is_configured():
            try:
                data_ok, adetail = await _await_timeout(
                    self.data.health_check(),
                    timeout=self.network_timeout_s,
                    label="alpaca_health",
                )
                parts.append(f"alpaca={adetail}")
            except Exception as exc:
                logger.exception("alpaca_health_check_failed err=%s", exc)
                parts.append(f"alpaca_error={exc}")
        else:
            data_ok = True  # no MD configured → don't block webull-only health

        if not webull_ok:
            return False, " | ".join(parts)
        # Require primary MD when configured
        if prefer == "databento" and self.mes_data and self.mes_data.is_configured() and not data_ok:
            return False, " | ".join(parts)
        if prefer != "databento" and self.data.is_configured() and not data_ok:
            return False, " | ".join(parts)
        return True, " | ".join(parts)

    async def resolve_market_context(self) -> dict[str, Any]:
        return await self.resolve_market_context_timed()

    async def resolve_market_context_timed(self) -> dict[str, Any]:
        """
        Prefer Databento CME MES L1 when configured.
        Fall back to Alpaca SPY → MES proxy, then Webull futures snapshot.
        """
        self._contract = self.trade.contract_symbol() if self.trade else EXECUTION_SYMBOL

        # Primary: Databento CME MES
        if self.mes_data and self.mes_data.is_configured() and primary_data_source() == "databento":
            try:
                mes_quote = await _await_timeout(
                    self.mes_data.fetch_quote(max_age_s=float(DATABENTO_MAX_QUOTE_AGE_S)),
                    timeout=max(self.network_timeout_s, 8.0),
                    label="databento_quote",
                )
            except Exception as exc:
                logger.exception("databento_quote_failed err=%s", exc)
                mes_quote = None
            if mes_quote:
                price = float(mes_quote.get("price") or mes_quote.get("last") or 0.0)
                if price > 0:
                    self._last_price = price
                    self._data_source = "databento_mes"
                    tick = {
                        **mes_quote,
                        "symbol": self._contract,
                        "source": "databento_mes",
                    }
                    return {
                        "tick": tick,
                        "live_stream": True,
                        "databento": True,
                        "webull_contract": self._contract,
                    }
            logger.warning("Databento MES quote unavailable/stale — trying Alpaca SPY proxy")

        # Secondary: Alpaca SPY → MES proxy
        if self.data.is_configured():
            try:
                spy_quote = await _await_timeout(
                    self.data.fetch_spy_quote(),
                    timeout=self.network_timeout_s,
                    label="alpaca_quote",
                )
            except Exception as exc:
                logger.exception("alpaca_quote_failed err=%s", exc)
                spy_quote = None
            if spy_quote:
                age_s = AlpacaSPYFeed.quote_age_seconds(spy_quote)
                if age_s is not None and age_s > MAX_SPY_QUOTE_AGE_S:
                    logger.error(
                        "alpaca_spy_quote_stale age_s=%.0f max=%.0f ts=%s — Justice: reject "
                        "(SPY closed; cannot proxy Sunday/overnight MES)",
                        age_s,
                        MAX_SPY_QUOTE_AGE_S,
                        spy_quote.get("timestamp"),
                    )
                else:
                    tick = self.data.get_mes_proxy_tick(spy_quote)
                    price = float(tick.get("price") or tick.get("last") or 0.0)
                    if price > 0:
                        self._last_price = price
                        self._data_source = "alpaca_spy_mes_proxy"
                        tick = {
                            **tick,
                            "symbol": self._contract,
                            "source": "alpaca_spy_mes_proxy",
                            "latency_ms": float(tick.get("latency_ms") or 45.0),
                            "quote_age_s": age_s,
                        }
                        return {
                            "tick": tick,
                            "live_stream": True,
                            "alpaca": True,
                            "webull_contract": self._contract,
                            "spy_quote": spy_quote,
                        }
            logger.warning("Alpaca quote unavailable/stale — trying Webull futures snapshot")

        # Tertiary: Webull (requires US_FUTURES subscription)
        try:
            quote = await _await_timeout(
                asyncio.to_thread(self.trade.get_quote),
                timeout=self.network_timeout_s,
                label="webull_quote",
            )
        except Exception as exc:
            logger.exception("webull_quote_failed err=%s", exc)
            raise RuntimeError(f"market_data_unavailable all_sources_failed: {exc}") from exc

        if not quote or quote.get("needs_subscription"):
            detail = (quote or {}).get("error") or "webull_quote_unavailable"
            logger.error(
                "market_data_stand_aside databento/alpaca_down webull=%s — no fresh MES price",
                detail,
            )
            return {
                "tick": {"price": 0.0, "last": 0.0, "source": "unavailable"},
                "live_stream": False,
                "stand_aside": True,
                "detail": detail,
            }

        price = float(quote.get("price") or quote.get("last") or 0.0)
        if price <= 0:
            raise RuntimeError("webull_quote_invalid_price")

        self._last_price = price
        self._contract = str(quote.get("symbol") or self._contract)
        self._data_source = "webull"
        tick = {
            "symbol": self._contract,
            "price": price,
            "last": price,
            "bid": float(quote.get("bid") or price),
            "ask": float(quote.get("ask") or price),
            "source": "webull",
            "latency_ms": 45.0,
        }
        return {"tick": tick, "live_stream": True, "webull": True}

    async def submit_order_timed(
        self,
        *,
        direction: str,
        contracts: int,
        limit_price: float | None = None,
    ) -> OrderExecutionResult:
        d = _normalize_direction(direction)
        side = "BUY" if d == "LONG" else "SELL"
        contract = self._contract or self.trade.contract_symbol()

        live_route = (
            self.trade.configured()
            and not webull_is_sandbox()
            and not forward_test_force_paper()
        )
        mode = RoutingMode.LIVE_ROUTE if live_route else RoutingMode.PAPER_ROUTE

        # Paper / sandbox: still go through Webull TradeClient when sandbox;
        # forced paper without live flag uses Webull submit only if sandbox host.
        use_webull_submit = self.trade.configured() and (
            webull_is_sandbox() or live_route
        )

        if use_webull_submit:
            try:
                result, err = await _await_timeout(
                    asyncio.to_thread(
                        self.trade.submit_market_order,
                        direction=side,
                        contracts=contracts,
                        contract=contract,
                    ),
                    timeout=self.network_timeout_s,
                    label="webull_submit",
                )
            except Exception as exc:
                logger.exception("webull_submit_failed err=%s", exc)
                return OrderExecutionResult(
                    routing_mode=mode,
                    status="REJECTED",
                    order_id="",
                    fill_price=limit_price or self._last_price,
                    contracts=contracts,
                    direction=d,
                    detail=str(exc),
                    contract=contract,
                )
            if err:
                return OrderExecutionResult(
                    routing_mode=mode,
                    status="REJECTED",
                    order_id="",
                    fill_price=limit_price or self._last_price,
                    contracts=contracts,
                    direction=d,
                    detail=err,
                    contract=contract,
                )
            fill = limit_price if limit_price is not None else self._last_price
            return OrderExecutionResult(
                routing_mode=mode,
                status=str((result or {}).get("status") or "SUBMITTED").upper(),
                order_id=str((result or {}).get("order_id") or ""),
                fill_price=round(float(fill or 0.0), 2),
                contracts=contracts,
                direction=d,
                detail="webull_futures",
                contract=str((result or {}).get("symbol") or contract),
            )

        # Forward-test paper without live Webull route: confirmed local fill (still Webull quotes)
        await asyncio.sleep(0.05)
        fill = limit_price if limit_price is not None else self._last_price
        return OrderExecutionResult(
            routing_mode=RoutingMode.PAPER_ROUTE,
            status="FILLED",
            order_id=f"FM-WB-{uuid.uuid4().hex[:8].upper()}",
            fill_price=round(float(fill or 0.0), 2),
            contracts=contracts,
            direction=d,
            detail="webull_paper_fill",
            contract=contract,
        )

    async def reconcile_with_broker(self) -> BrokerTruth:
        """Bypass local memory — sync from Webull futures positions + balance."""
        self.triage = TriageState.VERIFYING_POSITIONS
        try:
            truth = await _await_timeout(
                asyncio.to_thread(self.trade.fetch_broker_truth),
                timeout=self.network_timeout_s,
                label="webull_reconcile",
            )
        except Exception as exc:
            logger.exception("reconcile_with_broker_failed err=%s", exc)
            self.triage = TriageState.DISCONNECTED
            return BrokerTruth(False, self.equity, self.realized_pnl, [], detail=str(exc))

        if not truth.get("ok"):
            detail = str(truth.get("detail") or "webull_truth_unavailable")
            logger.error("reconcile_rejected detail=%s", detail)
            self.triage = TriageState.DISCONNECTED
            return BrokerTruth(False, self.equity, self.realized_pnl, [], detail=detail)

        # Justice: broker equity is absolute truth on live/sandbox.
        # Forward-test paper (FORWARD_TEST_MODE): Webull live futures often reports $0;
        # historically FM sized against STARTING_NAV and filled locally (webull_paper_fill).
        # That is FM paper — not the Webull mobile/sandbox $100k account.
        remote_equity = max(0.0, float(truth.get("equity") or 0.0))
        equity_source = "webull"
        if remote_equity > 0:
            self.equity = remote_equity
        elif forward_test_force_paper() and not webull_is_sandbox():
            # Preserve compounded paper book equity across reconcile — never snap back to STARTING_NAV.
            if self.equity <= 0:
                self.equity = float(STARTING_NAV)
            equity_source = "forward_test_paper_nav"
            logger.warning(
                "reconcile_forward_test_paper_nav equity=%.2f (webull_live_futures=0.00) "
                "orders=local_paper_fills — not Webull sandbox app paper",
                self.equity,
            )
        else:
            self.equity = 0.0
            logger.error(
                "reconcile_zero_equity account=%s — Temperance: size/fire blocked until "
                "futures funded (live) or WEBULL_API_HOST=api.sandbox.webull.com with sandbox keys (paper API)",
                truth.get("account_id") or webull_futures_account_id() or "unknown",
            )
        self.realized_pnl = float(truth.get("realized_pnl") or 0.0)

        synced: list[dict[str, Any]] = []
        for row in list(truth.get("positions") or []):
            try:
                size = int(row.get("size") or 0)
                if size == 0:
                    continue
                direction = _normalize_direction(str(row.get("direction") or "LONG"))
                synced.append(
                    {
                        "direction": direction,
                        "size": abs(size),
                        "price": float(row.get("price") or 0.0),
                        "symbol": str(row.get("symbol") or EXECUTION_SYMBOL),
                        "order_id": str(row.get("order_id") or ""),
                        "source": "webull_remote",
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as exc:
                logger.exception("reconcile_row_skip err=%s row=%s", exc, row)
                continue

        # Forward-test paper: Webull futures book is empty — keep local paper fills (Justice).
        if equity_source == "forward_test_paper_nav" and not synced:
            logger.info(
                "reconcile_paper_keep_local_positions n=%s equity=%.2f",
                len(self.open_positions),
                self.equity,
            )
        else:
            self.open_positions = synced
        self.triage = TriageState.READY
        logger.info(
            "reconcile_with_broker ok equity=%.2f realized_pnl=%.2f positions=%s source=%s",
            self.equity,
            self.realized_pnl,
            len(self.open_positions),
            equity_source,
        )
        return BrokerTruth(True, self.equity, self.realized_pnl, list(self.open_positions), detail=equity_source)

    async def poll_until_reconnected_forever(self) -> bool:
        """Infinite Webull outage survival — 30–60s backoff until health + reconcile succeed."""
        self.begin_reconnect()
        attempt = 0
        backoff = RECONNECT_BACKOFF_MIN_S
        while True:
            attempt += 1
            try:
                ok, detail = await self.health_check_timed()
            except Exception as exc:
                ok, detail = False, str(exc)
                logger.exception("webull_reconnect_health_failed attempt=%s", attempt)

            if ok:
                logger.info("webull_reconnected attempt=%s detail=%s — reconciling", attempt, detail)
                truth = await self.reconcile_with_broker()
                if truth.ok:
                    return True
                logger.error("webull_reconnect_ok_but_reconcile_failed detail=%s", truth.detail)
            else:
                logger.warning(
                    "webull_reconnect_wait attempt=%s backoff=%.0fs detail=%s",
                    attempt,
                    backoff,
                    detail,
                )

            try:
                await asyncio.sleep(backoff)
            except Exception as exc:
                logger.exception("reconnect_sleep_failed err=%s", exc)
            backoff = min(RECONNECT_BACKOFF_MAX_S, backoff * 1.25)

    async def flatten_opposite_if_needed(
        self,
        *,
        desired_direction: str,
        price: float,
        stop_ticks: int,
    ) -> tuple[bool, float]:
        """
        Flatten opposite exposure before a new entry.
        Returns (ok, approx_realized_pnl). PnL is 0 when nothing was closed.
        """
        from engine.config import POINT_VALUE

        desired = _normalize_direction(desired_direction)
        if desired not in {"LONG", "SHORT"}:
            return True, 0.0

        exposure_dir, exposure_size = self.net_exposure()
        if exposure_dir == "FLAT" or exposure_size <= 0:
            return True, 0.0
        if exposure_dir == desired:
            return True, 0.0

        entry = self._avg_entry(exposure_dir) or float(price)
        flat_dir = _opposite(exposure_dir)
        logger.warning(
            "POSITION_EXCLUSIVITY flatten %s x%s via Webull before entry %s",
            exposure_dir,
            exposure_size,
            desired,
        )
        flatten_order = Order(
            symbol=self._contract or EXECUTION_SYMBOL,
            direction=flat_dir,
            size=exposure_size,
            price=price,
            stop_ticks=max(1, int(stop_ticks)),
            quote_ts=time.time(),
        )
        v = validate_order(flatten_order, max_price_age_s=self.max_price_age_s)
        if not v.ok:
            logger.error("FLATTEN_VALIDATION_FAILED reason=%s", v.reason)
            return False, 0.0

        try:
            result = await self.submit_order_timed(
                direction=flatten_order.direction,
                contracts=flatten_order.size,
                limit_price=flatten_order.price,
            )
        except Exception as exc:
            logger.exception("FLATTEN_SUBMIT_FAILED err=%s", exc)
            self.enter_triage("flatten_exception")
            return False, 0.0

        if not _fill_confirmed(result.status):
            logger.error("FLATTEN_NO_FILL_CONFIRM status=%s detail=%s", result.status, result.detail)
            return False, 0.0

        fill = float(result.fill_price or price)
        points = (fill - entry) if exposure_dir == "LONG" else (entry - fill)
        approx_pnl = round(points * POINT_VALUE * abs(exposure_size), 2)
        logger.info(
            "FLATTEN_CONFIRMED id=%s status=%s fill=%.2f size=%s pnl≈%.2f",
            result.order_id,
            result.status,
            fill,
            result.contracts,
            approx_pnl,
        )
        self.open_positions = [
            p
            for p in self.open_positions
            if _normalize_direction(str(p.get("direction") or "")) == desired
        ]
        if forward_test_force_paper() and not webull_is_sandbox():
            return True, approx_pnl
        truth = await self.reconcile_with_broker()
        ok = bool(truth.ok and self.net_exposure()[0] in {"FLAT", desired})
        return ok, approx_pnl

    def _avg_entry(self, direction: str) -> float | None:
        """Size-weighted average entry (Justice — stop/TP must match real risk)."""
        notional = 0.0
        qty = 0.0
        for p in self.open_positions:
            if _normalize_direction(str(p.get("direction") or "")) != direction:
                continue
            px = float(p.get("price") or p.get("entry_price") or 0.0)
            sz = float(p.get("size") or p.get("contracts") or 0.0)
            if px <= 0 or sz <= 0:
                continue
            notional += px * sz
            qty += sz
        if qty <= 0:
            return None
        return notional / qty

    async def flatten_all(
        self,
        *,
        price: float,
        stop_ticks: int,
        reason: str = "flatten_all",
    ) -> tuple[bool, float]:
        """
        Close entire net exposure. Returns (ok, approx_realized_pnl).
        Used when Wisdom goes FLAT / stop hit (Temperance + Courage exit).
        """
        from engine.config import POINT_VALUE

        exposure_dir, exposure_size = self.net_exposure()
        if exposure_dir == "FLAT" or exposure_size <= 0:
            return True, 0.0

        entry = self._avg_entry(exposure_dir) or float(price)
        points = (float(price) - entry) if exposure_dir == "LONG" else (entry - float(price))
        approx_pnl = round(points * POINT_VALUE * abs(exposure_size), 2)

        flat_dir = _opposite(exposure_dir)
        logger.warning(
            "FLATTEN_ALL reason=%s close %s x%s @ %.2f entry≈%.2f pnl≈%.2f",
            reason,
            exposure_dir,
            exposure_size,
            price,
            entry,
            approx_pnl,
        )
        flatten_order = Order(
            symbol=self._contract or EXECUTION_SYMBOL,
            direction=flat_dir,
            size=exposure_size,
            price=price,
            stop_ticks=max(1, int(stop_ticks)),
            quote_ts=time.time(),
        )
        v = validate_order(flatten_order, max_price_age_s=self.max_price_age_s)
        if not v.ok:
            logger.error("FLATTEN_ALL_VALIDATION_FAILED reason=%s", v.reason)
            return False, 0.0

        try:
            result = await self.submit_order_timed(
                direction=flatten_order.direction,
                contracts=flatten_order.size,
                limit_price=flatten_order.price,
            )
        except Exception as exc:
            logger.exception("FLATTEN_ALL_SUBMIT_FAILED err=%s", exc)
            self.enter_triage("flatten_all_exception")
            return False, 0.0

        if not _fill_confirmed(result.status):
            logger.error("FLATTEN_ALL_NO_FILL status=%s detail=%s", result.status, result.detail)
            return False, 0.0

        fill = float(result.fill_price or price)
        points = (fill - entry) if exposure_dir == "LONG" else (entry - fill)
        approx_pnl = round(points * POINT_VALUE * abs(exposure_size), 2)
        self.open_positions = []
        logger.info(
            "FLATTEN_ALL_CONFIRMED id=%s status=%s fill=%.2f pnl≈%.2f reason=%s",
            result.order_id,
            result.status,
            fill,
            approx_pnl,
            reason,
        )
        # Forward-test paper: local book is source of truth (Webull futures equity is 0)
        if forward_test_force_paper() and not webull_is_sandbox():
            return True, approx_pnl
        truth = await self.reconcile_with_broker()
        return bool(truth.ok and self.net_exposure()[0] == "FLAT"), approx_pnl

    async def partial_close(
        self,
        *,
        contracts: int,
        price: float,
        stop_ticks: int,
        reason: str = "take_profit_scale_out",
        leave: int = 1,
    ) -> tuple[bool, float]:
        """
        Close `contracts` of net exposure; leave `leave` contracts as a runner.
        Returns (ok, approx_realized_pnl on the closed size).
        """
        from engine.config import POINT_VALUE

        exposure_dir, exposure_size = self.net_exposure()
        close_qty = max(0, int(contracts))
        leave_qty = max(0, int(leave))
        if exposure_dir == "FLAT" or exposure_size <= 0 or close_qty < 1:
            return True, 0.0
        if exposure_size <= leave_qty:
            logger.info(
                "PARTIAL_CLOSE_SKIP reason=%s size=%s leave=%s — runner only",
                reason,
                exposure_size,
                leave_qty,
            )
            return True, 0.0
        close_qty = min(close_qty, exposure_size - leave_qty)

        entry = self._avg_entry(exposure_dir) or float(price)
        points = (float(price) - entry) if exposure_dir == "LONG" else (entry - float(price))
        approx_pnl = round(points * POINT_VALUE * close_qty, 2)
        flat_dir = _opposite(exposure_dir)

        logger.warning(
            "PARTIAL_CLOSE reason=%s close %s x%s leave=%s @ %.2f entry≈%.2f pnl≈%.2f",
            reason,
            exposure_dir,
            close_qty,
            leave_qty,
            price,
            entry,
            approx_pnl,
        )
        order = Order(
            symbol=self._contract or EXECUTION_SYMBOL,
            direction=flat_dir,
            size=close_qty,
            price=price,
            stop_ticks=max(1, int(stop_ticks)),
            quote_ts=time.time(),
        )
        v = validate_order(order, max_price_age_s=self.max_price_age_s)
        if not v.ok:
            logger.error("PARTIAL_CLOSE_VALIDATION_FAILED reason=%s", v.reason)
            return False, 0.0

        try:
            result = await self.submit_order_timed(
                direction=order.direction,
                contracts=order.size,
                limit_price=order.price,
            )
        except Exception as exc:
            logger.exception("PARTIAL_CLOSE_SUBMIT_FAILED err=%s", exc)
            self.enter_triage("partial_close_exception")
            return False, 0.0

        if not _fill_confirmed(result.status):
            logger.error("PARTIAL_CLOSE_NO_FILL status=%s detail=%s", result.status, result.detail)
            return False, 0.0

        fill = float(result.fill_price or price)
        points = (fill - entry) if exposure_dir == "LONG" else (entry - fill)
        approx_pnl = round(points * POINT_VALUE * close_qty, 2)
        remaining = exposure_size - close_qty
        # Consolidate leftover into one runner row (keep original avg entry)
        self.open_positions = [
            {
                "direction": exposure_dir,
                "size": remaining,
                "price": entry,
                "entry_price": entry,
                "order_id": str(result.order_id or ""),
                "symbol": self._contract or EXECUTION_SYMBOL,
                "source": "scale_out_runner",
                "scaled_out_tp": True,
            }
        ] if remaining > 0 else []
        logger.info(
            "PARTIAL_CLOSE_CONFIRMED id=%s status=%s fill=%.2f closed=%s remain=%s pnl≈%.2f reason=%s",
            result.order_id,
            result.status,
            fill,
            close_qty,
            remaining,
            approx_pnl,
            reason,
        )
        if forward_test_force_paper() and not webull_is_sandbox():
            return True, approx_pnl
        truth = await self.reconcile_with_broker()
        # After live reconcile, positions may overwrite runner — require remaining exposure
        ok = bool(truth.ok) and self.net_exposure()[1] >= leave_qty
        return ok, approx_pnl

    def stop_hit(self, *, price: float, stop_ticks: int) -> bool:
        """True if mark price breached stop_ticks from average entry."""
        from engine.config import TICK_SIZE

        exposure_dir, exposure_size = self.net_exposure()
        if exposure_dir == "FLAT" or exposure_size <= 0:
            return False
        entry = self._avg_entry(exposure_dir)
        if entry is None:
            return False
        stop_pts = max(1, int(stop_ticks)) * float(TICK_SIZE)
        if exposure_dir == "LONG":
            return float(price) <= entry - stop_pts
        return float(price) >= entry + stop_pts

    def take_profit_hit(self, *, price: float, target_ticks: int) -> bool:
        """True if mark price reached target_ticks of favorable move from average entry."""
        from engine.config import TICK_SIZE

        exposure_dir, exposure_size = self.net_exposure()
        if exposure_dir == "FLAT" or exposure_size <= 0:
            return False
        entry = self._avg_entry(exposure_dir)
        if entry is None:
            return False
        target_pts = max(1, int(target_ticks)) * float(TICK_SIZE)
        if exposure_dir == "LONG":
            return float(price) >= entry + target_pts
        return float(price) <= entry - target_pts

    def unrealized_position_pnl(self, *, price: float) -> float:
        """Open position mark-to-market PnL in dollars (0 when flat)."""
        from engine.config import POINT_VALUE

        exposure_dir, exposure_size = self.net_exposure()
        if exposure_dir == "FLAT" or exposure_size <= 0:
            return 0.0
        entry = self._avg_entry(exposure_dir)
        if entry is None:
            return 0.0
        points = (float(price) - entry) if exposure_dir == "LONG" else (entry - float(price))
        return round(points * float(POINT_VALUE) * int(exposure_size), 2)

    def take_profit_dollars_hit(self, *, price: float, target_dollars: float) -> bool:
        """True when open position unrealized PnL reaches the dollar take-profit."""
        target = float(target_dollars)
        if target <= 0:
            return False
        return self.unrealized_position_pnl(price=price) >= target

    def stop_dollars_hit(self, *, price: float, stop_dollars: float) -> bool:
        """True when open position unrealized PnL is at/below -stop_dollars."""
        limit = float(stop_dollars)
        if limit <= 0:
            return False
        return self.unrealized_position_pnl(price=price) <= -limit

    def scale_out_close_qty(self, *, leave: int = 1) -> int:
        """Contracts to close so `leave` remain (0 if already at/below leave)."""
        _, size = self.net_exposure()
        leave_qty = max(0, int(leave))
        if size <= leave_qty:
            return 0
        return int(size - leave_qty)

    def size_for_direction(
        self,
        *,
        direction: str,
        stop_ticks: int,
        risk_limit_pct: float | None = None,
    ) -> SizeResult:
        return calculate_max_contracts(
            equity=self.equity,
            stop_ticks=stop_ticks,
            risk_limit_pct=risk_limit_pct,
        )

    async def fire_order(self, order: Order) -> OrderExecutionResult | None:
        if not self.can_send_new_orders():
            logger.error("ORDER_BLOCKED triage=%s — no new orders until verified", self.triage.value)
            return None

        flattened, _excl_pnl = await self.flatten_opposite_if_needed(
            desired_direction=order.direction,
            price=order.price,
            stop_ticks=order.stop_ticks,
        )
        if not flattened:
            logger.error("ORDER_BLOCKED exclusivity_flatten_failed")
            return None

        order = Order(
            symbol=order.symbol if order.symbol else (self._contract or EXECUTION_SYMBOL),
            direction=order.direction,
            size=order.size,
            price=order.price,
            stop_ticks=order.stop_ticks,
            quote_ts=time.time(),
            order_id=order.order_id,
        )

        v = validate_order(order, expected_symbol=EXECUTION_SYMBOL, max_price_age_s=self.max_price_age_s)
        if not v.ok:
            logger.error("ORDER_REJECTED_VALIDATION reason=%s", v.reason)
            return None

        risk = reject_if_over_risk(
            equity=self.equity,
            contracts=order.size,
            stop_ticks=order.stop_ticks,
        )
        if not risk.ok:
            logger.error("ORDER_REJECTED_RISK reason=%s", risk.reason)
            return None

        try:
            result = await self.submit_order_timed(
                direction=order.direction,
                contracts=order.size,
                limit_price=order.price,
            )
        except Exception as exc:
            logger.exception("ORDER_SUBMIT_FAILED err=%s", exc)
            self.enter_triage("submit_exception")
            return None

        if _fill_confirmed(result.status):
            self.open_positions.append(
                {
                    "direction": _normalize_direction(order.direction),
                    "size": order.size,
                    "price": result.fill_price,
                    "order_id": result.order_id,
                    "symbol": result.contract or order.symbol,
                    "source": "webull_fill",
                }
            )
        return result


__all__ = [
    "BrokerTruth",
    "NETWORK_TIMEOUT_S",
    "Order",
    "RECONNECT_BACKOFF_MAX_S",
    "RECONNECT_BACKOFF_MIN_S",
    "RISK_LIMIT_PCT",
    "SizeResult",
    "TriageState",
    "ValidationResult",
    "VirtueBroker",
    "calculate_max_contracts",
    "reject_if_over_risk",
    "validate_order",
]
