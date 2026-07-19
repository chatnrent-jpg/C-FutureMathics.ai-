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
    submit_futures_market_order,
)
from engine.webull_openapi import webull_configured, webull_is_sandbox, webull_api_host
from engine.alpaca_spy_feed import AlpacaSPYFeed

logger = logging.getLogger(__name__)


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
        # Priority: Alpaca (data) + Webull (execution)
        if self._using_alpaca:
            ok, msg = await self._alpaca_feed.health_check()
            if ok:
                return True, f"alpaca_spy_feed_ok | {msg}"
        
        if self._using_webull:
            bal = await asyncio.to_thread(get_account_balance)
            if bal.get("ok"):
                return True, f"webull_ok equity={bal.get('equity')}"
            return False, str(bal.get("error") or "webull_balance_failed")
        
        return True, "mes_local_sim_ok"

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
        # Priority 1: Alpaca SPY as signal proxy (if configured)
        if self._using_alpaca:
            spy_quote = await self._alpaca_feed.fetch_spy_quote()
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
            else:
                logger.warning("Alpaca SPY quote failed - falling back to Webull/sim")
        
        # Priority 2: Webull MES futures quote
        if self._using_webull:
            quote = await asyncio.to_thread(fetch_futures_quote, self._contract)
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

        # Priority 3: Local sim fallback
        tick = self._sim_tick()
        if self._using_webull:
            tick["webull_quote_missing"] = True
        if self._using_alpaca:
            tick["alpaca_fallback"] = True
        return {"tick": tick, "live_stream": False, "sim": True}

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

        if self._using_webull and live_route:
            result, err = await asyncio.to_thread(
                submit_futures_market_order,
                direction=d,
                contracts=contracts,
                contract=self._contract,
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

    @property
    def last_tick(self) -> dict[str, Any]:
        return {
            "price": self._last_price,
            "last": self._last_price,
            "bid": round(self._last_price - TICK_SIZE, 2),
            "ask": round(self._last_price + TICK_SIZE, 2),
        }
