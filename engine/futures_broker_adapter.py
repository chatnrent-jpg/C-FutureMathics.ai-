"""
FutureMathics broker adapter — Webull futures (primary) or local MES sim (fallback).
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from engine.config import EXECUTION_SYMBOL, TICK_SIZE, WARMUP_MES_PRICE, forward_test_force_paper
from engine.webull_futures import (
    fetch_futures_quote,
    front_month_contract,
    get_account_balance,
    get_futures_positions,
    submit_futures_market_order,
)
from engine.webull_openapi import webull_configured, webull_is_sandbox, webull_api_host
from engine.alpaca_spy_feed import AlpacaSPYFeed

logger = logging.getLogger(__name__)

# Hard socket timeout for all broker network calls (Justice)
NETWORK_TIMEOUT_S = 5.0


class RoutingMode(str, Enum):
    PAPER_ROUTE = "PAPER_ROUTE"
    LIVE_ROUTE = "LIVE_ROUTE"


@dataclass(frozen=True, slots=True)
class OrderExecutionResult:
    routing_mode: RoutingMode
    status: str
    order_id: str
    fill_price: float
    contracts: int
    direction: str
    detail: str = ""
    contract: str = ""


@dataclass
class BrokerConfig:
    symbol: str = EXECUTION_SYMBOL
    paper: bool = True
    submit_live_orders: bool = False


@dataclass
class FuturesBrokerAdapter:
    config: BrokerConfig = field(default_factory=BrokerConfig)
    _last_price: float = WARMUP_MES_PRICE
    _sequence: int = 0
    _drift: float = 0.0
    _contract: str = field(default_factory=front_month_contract)
    _using_webull: bool = field(default=False, init=False)
    _alpaca_feed: AlpacaSPYFeed = field(default_factory=AlpacaSPYFeed, init=False)
    _using_alpaca: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._using_webull = webull_configured()
        if self._using_webull:
            logger.info("Webull futures broker enabled (host=%s)", webull_api_host())
        
        # Initialize Alpaca SPY feed
        self._using_alpaca = self._alpaca_feed.is_configured()
        if self._using_alpaca:
            logger.info("Alpaca SPY feed enabled - using as MES signal proxy")

    async def health_check(self) -> tuple[bool, str]:
        # Priority: Alpaca (data) + Webull (execution) — always timeout-bounded
        try:
            if self._using_alpaca:
                ok, msg = await asyncio.wait_for(
                    self._alpaca_feed.health_check(),
                    timeout=NETWORK_TIMEOUT_S,
                )
                if ok:
                    return True, f"alpaca_spy_feed_ok | {msg}"

            if self._using_webull:
                bal = await asyncio.wait_for(
                    asyncio.to_thread(get_account_balance),
                    timeout=NETWORK_TIMEOUT_S,
                )
                if bal.get("ok"):
                    return True, f"webull_ok equity={bal.get('equity')}"
                return False, str(bal.get("error") or "webull_balance_failed")

            return True, "mes_local_sim_ok"
        except Exception as exc:
            logger.exception("health_check_failed err=%s", exc)
            return False, str(exc)

    def _sim_tick(self) -> dict[str, Any]:
        self._sequence += 1
        shock = random.gauss(0, 0.5) + self._drift * 0.3
        self._drift = self._drift * 0.95 + shock * 0.05
        delta_ticks = round(shock)
        self._last_price = round(self._last_price + delta_ticks * TICK_SIZE, 2)
        bid = round(self._last_price - TICK_SIZE, 2)
        ask = round(self._last_price + TICK_SIZE, 2)
        return {
            "symbol": self._contract,
            "price": self._last_price,
            "last": self._last_price,
            "bid": bid,
            "ask": ask,
            "size": random.randint(1, 50),
            "latency_ms": random.uniform(20, 80),
            "sequence_id": self._sequence,
            "source": "sim",
        }

    async def resolve_market_context(self) -> dict[str, Any]:
        try:
            # Priority 1: Alpaca SPY as signal proxy (if configured)
            if self._using_alpaca:
                spy_quote = await asyncio.wait_for(
                    self._alpaca_feed.fetch_spy_quote(),
                    timeout=NETWORK_TIMEOUT_S,
                )
                if spy_quote:
                    mes_tick = self._alpaca_feed.get_mes_proxy_tick(spy_quote)
                    self._last_price = mes_tick["price"]
                    self._sequence += 1
                    return {
                        "tick": mes_tick,
                        "live_stream": True,
                        "alpaca": True,
                        "spy_quote": spy_quote,
                    }
                logger.warning("Alpaca SPY quote failed - falling back to Webull/sim")

            # Priority 2: Webull MES futures quote
            if self._using_webull:
                quote = await asyncio.wait_for(
                    asyncio.to_thread(fetch_futures_quote, self._contract),
                    timeout=NETWORK_TIMEOUT_S,
                )
                if quote and quote.get("price") is not None and not quote.get("needs_subscription"):
                    self._last_price = float(quote["price"])
                    tick = {
                        **quote,
                        "last": quote["price"],
                        "size": 1,
                        "latency_ms": 45.0,
                        "sequence_id": self._sequence + 1,
                    }
                    self._sequence += 1
                    return {"tick": tick, "live_stream": True, "webull": True}

            # Priority 3: Local sim — paper/dev only. Never invent tape for live cash.
            from engine.config import forward_test_force_paper

            if not forward_test_force_paper():
                logger.error("market_data_stand_aside — refusing sim ticks in live mode")
                return {
                    "tick": {"price": 0.0, "last": 0.0, "source": "unavailable"},
                    "live_stream": False,
                    "stand_aside": True,
                    "detail": "sim_forbidden_live",
                }
            tick = self._sim_tick()
            if self._using_webull:
                tick["webull_quote_missing"] = True
            if self._using_alpaca:
                tick["alpaca_fallback"] = True
            return {"tick": tick, "live_stream": False, "sim": True}
        except Exception as exc:
            logger.exception("resolve_market_context_failed err=%s", exc)
            raise

    async def fetch_broker_truth(self) -> dict[str, Any]:
        """
        Live broker snapshot for reconcile_with_broker().
        Bypasses local memory — queries Webull positions + balance when configured;
        sim mode returns empty positions with last known price equity placeholder.
        """
        try:
            if self._using_webull:
                bal = await asyncio.wait_for(
                    asyncio.to_thread(get_account_balance),
                    timeout=NETWORK_TIMEOUT_S,
                )
                positions, pos_err = await asyncio.wait_for(
                    asyncio.to_thread(get_futures_positions),
                    timeout=NETWORK_TIMEOUT_S,
                )
                if not bal.get("ok"):
                    return {
                        "ok": False,
                        "equity": 0.0,
                        "realized_pnl": 0.0,
                        "positions": [],
                        "detail": str(bal.get("error") or "balance_failed"),
                    }
                if pos_err:
                    return {
                        "ok": False,
                        "equity": float(bal.get("equity") or 0.0),
                        "realized_pnl": 0.0,
                        "positions": [],
                        "detail": f"positions_fetch_failed:{pos_err}",
                    }
                equity = float(bal.get("equity") or 0.0)
                realized = float(
                    bal.get("realized_pnl")
                    or bal.get("realizedPnl")
                    or bal.get("day_pnl")
                    or 0.0
                )
                normalized: list[dict[str, Any]] = []
                for row in positions or []:
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
                    if size < 0:
                        direction = "SHORT"
                        size = abs(size)
                    elif side.upper() in {"SHORT", "SELL"}:
                        direction = "SHORT"
                        size = abs(size)
                    else:
                        direction = "LONG"
                        size = abs(size)
                    normalized.append(
                        {
                            "symbol": str(row.get("symbol") or row.get("ticker") or self._contract),
                            "direction": direction,
                            "size": size,
                            "price": float(row.get("average_price") or row.get("avgPrice") or row.get("cost_price") or 0.0),
                            "raw": row,
                        }
                    )
                return {
                    "ok": True,
                    "equity": equity,
                    "realized_pnl": realized,
                    "positions": normalized,
                    "detail": "webull_remote",
                }

            # Sim / paper without Webull: truthful empty remote book (no fabricated positions)
            return {
                "ok": True,
                "equity": 0.0,  # caller keeps prior equity if 0
                "realized_pnl": 0.0,
                "positions": [],
                "detail": "sim_no_remote_positions",
            }
        except Exception as exc:
            logger.exception("fetch_broker_truth_failed err=%s", exc)
            return {
                "ok": False,
                "equity": 0.0,
                "realized_pnl": 0.0,
                "positions": [],
                "detail": str(exc),
            }

    async def submit_order(
        self,
        *,
        direction: str,
        contracts: int,
        limit_price: float | None = None,
        order_details: dict[str, Any] | None = None,
    ) -> OrderExecutionResult:
        d = direction.upper()
        if d == "LONG":
            d = "BUY"
        if d == "SHORT":
            d = "SELL"

        live_route = (
            self._using_webull
            and not webull_is_sandbox()
            and not forward_test_force_paper()
        )
        mode = RoutingMode.LIVE_ROUTE if live_route else RoutingMode.PAPER_ROUTE

        try:
            if self._using_webull and live_route:
                result, err = await asyncio.wait_for(
                    asyncio.to_thread(
                        submit_futures_market_order,
                        direction=d,
                        contracts=contracts,
                        contract=self._contract,
                    ),
                    timeout=NETWORK_TIMEOUT_S,
                )
                if err:
                    logger.error("Webull order failed: %s", err)
                    return OrderExecutionResult(
                        routing_mode=mode,
                        status="REJECTED",
                        order_id="",
                        fill_price=limit_price or self._last_price,
                        contracts=contracts,
                        direction=direction.upper(),
                        detail=err,
                        contract=self._contract,
                    )
                fill = limit_price if limit_price is not None else self._last_price
                return OrderExecutionResult(
                    routing_mode=mode,
                    status=str(result.get("status") or "SUBMITTED").upper(),
                    order_id=str(result.get("order_id") or ""),
                    fill_price=round(float(fill), 2),
                    contracts=contracts,
                    direction=direction.upper(),
                    detail="webull_futures",
                    contract=str(result.get("symbol") or self._contract),
                )

            await asyncio.sleep(0.05)
            fill = limit_price if limit_price is not None else self._last_price
            return OrderExecutionResult(
                routing_mode=RoutingMode.PAPER_ROUTE,
                status="FILLED",
                order_id=f"FM-{uuid.uuid4().hex[:10].upper()}",
                fill_price=round(fill, 2),
                contracts=contracts,
                direction=direction.upper(),
                detail="local_sim_fill",
                contract=self._contract,
            )
        except Exception as exc:
            logger.exception("submit_order_failed err=%s", exc)
            return OrderExecutionResult(
                routing_mode=mode,
                status="REJECTED",
                order_id="",
                fill_price=limit_price or self._last_price,
                contracts=contracts,
                direction=direction.upper(),
                detail=str(exc),
                contract=self._contract,
            )

    @property
    def last_tick(self) -> dict[str, Any]:
        return {
            "price": self._last_price,
            "last": self._last_price,
            "bid": round(self._last_price - TICK_SIZE, 2),
            "ask": round(self._last_price + TICK_SIZE, 2),
        }
