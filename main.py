"""
FutureMathics virtue main loop — native Wisdom brain.

Execution: Webull futures (TradeClient)
Market data: Alpaca SPY → MES proxy (paid data feed)
No Interactive Brokers / VolumeWatch grade feed / Webull US_FUTURES quotes required.

Production hardening:
  1. Seed WisdomStrategy from Alpaca historical bars (ATR/ADX ready immediately)
  2. CME MES market-hours gate
  3. Manus CapitalProtectionMatrix (daily halt / reduce size / concurrent risk)
  4. Position exclusivity, 5s timeouts, reconcile, infinite reconnect

Usage:
  cd C:\\FutureMathics.ai
  $env:PYTHONPATH = \"C:\\FutureMathics.ai\"
  python main.py --cycles 5
  python main.py --once
  python main.py --ignore-hours
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from broker import NETWORK_TIMEOUT_S, Order, VirtueBroker
from engine.config import (
    DEFAULT_STOP_TICKS,
    EXECUTION_SYMBOL,
    GRADE_DAILY_PROFIT_LOCK,
    STARTING_NAV,
    TICK_SIZE,
    TICK_VALUE,
)
from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict
from manus.heartbeat import BrokerHeartbeatAgent, HeartbeatState
from scripts.run_daily_session import in_market_hours
from strategy import Bar, SignalAction, WisdomStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("virtue.main")


@dataclass
class VirtueSession:
    cycle: int = 0
    halted: bool = False
    last_action: str = "FLAT"
    realized_pnl_today: float = 0.0
    strategy: WisdomStrategy = field(default_factory=WisdomStrategy)
    broker: VirtueBroker = field(default_factory=VirtueBroker)
    risk: CapitalProtectionMatrix = field(
        default_factory=lambda: CapitalProtectionMatrix(
            starting_nav=STARTING_NAV,
            account_nav=STARTING_NAV,
            peak_nav=STARTING_NAV,
        )
    )


async def _heartbeat_probe(broker: VirtueBroker) -> tuple[bool, str]:
    try:
        return await asyncio.wait_for(
            broker.health_check_timed(),
            timeout=NETWORK_TIMEOUT_S,
        )
    except Exception as exc:
        return False, str(exc)


async def seed_wisdom_from_alpaca(session: VirtueSession, *, limit: int = 120) -> int:
    """Warm WisdomStrategy with Alpaca SPY→MES proxy bars so ATR/ADX are ready."""
    try:
        spy_bars = await session.broker.data.fetch_spy_bars(timeframe="5Min", limit=limit)
    except Exception as exc:
        logger.exception("alpaca_bar_seed_failed err=%s", exc)
        return 0
    mes_bars = session.broker.data.mes_proxy_bars_from_spy(spy_bars)
    bars = [
        Bar(high=float(b["high"]), low=float(b["low"]), close=float(b["close"]))
        for b in mes_bars
    ]
    if not bars:
        logger.warning("alpaca_bar_seed empty — Wisdom stays in WARMUP until live ticks accumulate")
        return 0
    session.strategy.seed(bars)
    # Anchor last price from seed
    session.broker._last_price = float(bars[-1].close)
    decision = session.strategy.evaluate()
    logger.info(
        "WISDOM_SEEDED bars=%s regime=%s action=%s adx=%.1f atr_pct=%.2f",
        len(bars),
        decision.regime.value,
        decision.action.value,
        decision.adx,
        decision.atr_pct,
    )
    return len(bars)


async def _survive_outage(session: VirtueSession, reason: str) -> bool:
    """Enter triage and retry forever until broker + reconcile succeed."""
    session.broker.enter_triage(reason)
    logger.warning("OUTAGE_SURVIVAL reason=%s — infinite reconnect (30–60s backoff)", reason)
    try:
        ok = await session.broker.poll_until_reconnected_forever()
    except Exception as exc:
        logger.exception("outage_survival_failed err=%s", exc)
        return False
    if ok:
        if session.broker.equity > 0:
            session.risk.update_nav(session.broker.equity)
        session.realized_pnl_today = float(session.broker.realized_pnl or session.realized_pnl_today)
        logger.info(
            "OUTAGE_RECOVERED equity=%.2f realized_pnl=%.2f positions=%s",
            session.broker.equity,
            session.broker.realized_pnl,
            len(session.broker.open_positions),
        )
    return ok


def _open_risk_notional(session: VirtueSession, stop_ticks: int) -> float:
    _, size = session.broker.net_exposure()
    return float(abs(size) * stop_ticks * TICK_VALUE)


async def run_cycle(
    session: VirtueSession,
    *,
    stop_ticks: int = DEFAULT_STOP_TICKS,
    ignore_hours: bool = False,
) -> None:
    """One virtue cycle: hours → heartbeat → market → regime → exclusivity → Manus → fire."""
    session.cycle += 1

    # CME MES hours (Wisdom: stand aside when market closed)
    if not ignore_hours and not in_market_hours():
        logger.info("CYCLE %s market_closed — stand aside", session.cycle)
        session.last_action = "FLAT"
        return

    if session.halted:
        logger.warning("CYCLE %s session_halted by Manus — stand aside", session.cycle)
        session.last_action = "FLAT"
        return

    hb = BrokerHeartbeatAgent(
        brokers={"primary": lambda: _heartbeat_probe(session.broker)},
        interval_s=5.0,
        degraded_ms=NETWORK_TIMEOUT_S * 1000.0,
        green_ms=min(2000.0, NETWORK_TIMEOUT_S * 1000.0),
    )
    try:
        events = await asyncio.wait_for(hb.run_once(), timeout=NETWORK_TIMEOUT_S + 1.0)
    except Exception as exc:
        logger.exception("heartbeat_run_failed err=%s", exc)
        await _survive_outage(session, "heartbeat_exception")
        session.last_action = "FLAT"
        return

    dead = any(e.state == HeartbeatState.DEAD for e in events)
    if dead:
        recovered = await _survive_outage(session, "heartbeat_dead")
        if not recovered:
            session.last_action = "FLAT"
            return

    if not session.broker.can_send_new_orders():
        logger.warning("CYCLE %s triage=%s — forcing reconcile path", session.cycle, session.broker.triage.value)
        recovered = await _survive_outage(session, "triage_not_ready")
        if not recovered or not session.broker.can_send_new_orders():
            session.last_action = "FLAT"
            return

    try:
        ctx = await session.broker.resolve_market_context_timed()
    except Exception as exc:
        logger.exception("market_context_failed err=%s", exc)
        await _survive_outage(session, "market_context_exception")
        session.last_action = "FLAT"
        return

    tick = ctx.get("tick") or {}
    price = float(tick.get("price") or tick.get("last") or 0.0)
    if price <= 0:
        logger.error("CYCLE %s invalid_price — Justice stand aside", session.cycle)
        session.last_action = "FLAT"
        return

    high = float(tick.get("ask") or price)
    low = float(tick.get("bid") or price)
    if high < low:
        high, low = low, high
    session.strategy.update_price(price, high=high + TICK_SIZE, low=low - TICK_SIZE)

    decision = session.strategy.evaluate()
    logger.info(
        "CYCLE %s regime=%s action=%s adx=%.1f atr_pct=%.2f reason=%s exposure=%s",
        session.cycle,
        decision.regime.value,
        decision.action.value,
        decision.adx,
        decision.atr_pct,
        decision.reason,
        session.broker.net_exposure(),
    )

    if decision.action == SignalAction.FLAT:
        session.last_action = "FLAT"
        return

    # Temperance: daily profit lock (no new entries after strong day)
    if session.realized_pnl_today >= float(GRADE_DAILY_PROFIT_LOCK):
        logger.info(
            "CYCLE %s daily_profit_lock pnl=%.2f >= %.2f — stand aside",
            session.cycle,
            session.realized_pnl_today,
            GRADE_DAILY_PROFIT_LOCK,
        )
        session.last_action = "FLAT"
        return

    # Mandatory exclusivity check before emitting new LONG/SHORT payload
    try:
        exclusive_ok = await session.broker.flatten_opposite_if_needed(
            desired_direction=decision.action.value,
            price=price,
            stop_ticks=stop_ticks,
        )
    except Exception as exc:
        logger.exception("exclusivity_check_failed err=%s", exc)
        await _survive_outage(session, "exclusivity_exception")
        session.last_action = "FLAT"
        return

    if not exclusive_ok:
        logger.error("CYCLE %s exclusivity_blocked — opposite flatten not confirmed", session.cycle)
        session.last_action = "FLAT"
        return

    sized = session.broker.size_for_direction(
        direction=decision.action.value,
        stop_ticks=stop_ticks,
    )
    if sized.rejected or sized.contracts < 1:
        logger.warning("CYCLE %s size_rejected reason=%s", session.cycle, sized.reason)
        session.last_action = "FLAT"
        return

    contracts = int(sized.contracts)
    proposed_risk = float(contracts * stop_ticks * TICK_VALUE)
    open_risk = _open_risk_notional(session, stop_ticks)

    # Sync Manus NAV from broker truth when available
    if session.broker.equity > 0:
        session.risk.update_nav(session.broker.equity)

    try:
        verdict, reason = session.risk.evaluate(
            realized_pnl_today=session.realized_pnl_today,
            open_risk_notional=open_risk,
            proposed_trade_risk=proposed_risk,
        )
    except Exception as exc:
        logger.exception("manus_evaluate_failed err=%s", exc)
        session.last_action = "FLAT"
        return

    if verdict == RiskVerdict.HALT:
        session.halted = True
        logger.error("CYCLE %s MANUS_HALT reason=%s — session halted", session.cycle, reason)
        session.last_action = "FLAT"
        return

    if verdict == RiskVerdict.REDUCE_SIZE:
        contracts = max(1, contracts // 2)
        proposed_risk = float(contracts * stop_ticks * TICK_VALUE)
        logger.warning(
            "CYCLE %s MANUS_REDUCE_SIZE reason=%s contracts=%s risk=%.2f",
            session.cycle,
            reason,
            contracts,
            proposed_risk,
        )

    order = Order(
        symbol=EXECUTION_SYMBOL,
        direction=decision.action.value,
        size=contracts,
        price=price,
        stop_ticks=stop_ticks,
        quote_ts=time.time(),
    )

    try:
        result = await session.broker.fire_order(order)
    except Exception as exc:
        logger.exception("fire_order_failed err=%s", exc)
        await _survive_outage(session, "fire_order_exception")
        session.last_action = "FLAT"
        return

    if result is None:
        session.last_action = "FLAT"
        return

    session.last_action = decision.action.value
    logger.info(
        "CYCLE %s FILLED/SUBMITTED %s x%s @ %s id=%s status=%s manus=%s",
        session.cycle,
        result.direction,
        result.contracts,
        result.fill_price,
        result.order_id,
        result.status,
        reason,
    )


async def run_loop(
    *,
    cycles: int | None,
    interval_s: float,
    once: bool,
    ignore_hours: bool,
) -> None:
    session = VirtueSession()
    session.broker.update_equity(STARTING_NAV)
    session.risk.update_nav(STARTING_NAV)
    logger.info(
        "VIRTUE LOOP start equity=%.2f symbol=%s stop_ticks=%s network_timeout=%.1fs",
        session.broker.equity,
        EXECUTION_SYMBOL,
        DEFAULT_STOP_TICKS,
        NETWORK_TIMEOUT_S,
    )

    # Boot reconcile (Webull absolute truth)
    try:
        truth = await session.broker.reconcile_with_broker()
        if truth.ok and session.broker.equity > 0:
            session.risk.update_nav(session.broker.equity)
            session.realized_pnl_today = float(session.broker.realized_pnl or 0.0)
    except Exception as exc:
        logger.exception("boot_reconcile_failed err=%s", exc)

    # Seed Wisdom from Alpaca history
    try:
        await seed_wisdom_from_alpaca(session)
    except Exception as exc:
        logger.exception("boot_seed_failed err=%s", exc)

    n = 0
    while True:
        try:
            await run_cycle(session, ignore_hours=ignore_hours)
        except Exception as exc:
            logger.exception("cycle_unhandled err=%s — infinite outage survival", exc)
            try:
                await _survive_outage(session, "cycle_exception")
            except Exception:
                logger.exception("reconnect_after_cycle_exception_failed — will retry next loop")

        n += 1
        if once or (cycles is not None and n >= cycles):
            break
        try:
            await asyncio.sleep(max(1.0, float(interval_s)))
        except Exception as exc:
            logger.exception("loop_sleep_failed err=%s", exc)

    logger.info("VIRTUE LOOP done cycles=%s last_action=%s", n, session.last_action)


def main() -> None:
    parser = argparse.ArgumentParser(description="FutureMathics virtue main loop (native Wisdom brain)")
    parser.add_argument("--cycles", type=int, default=None, help="Stop after N cycles")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Single cycle then exit")
    parser.add_argument(
        "--ignore-hours",
        action="store_true",
        help="Run outside CME MES hours (testing only)",
    )
    args = parser.parse_args()
    asyncio.run(
        run_loop(
            cycles=args.cycles,
            interval_s=args.interval,
            once=args.once,
            ignore_hours=args.ignore_hours,
        )
    )


if __name__ == "__main__":
    main()
