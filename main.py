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
    DEFAULT_TARGET_TICKS,
    EXECUTION_SYMBOL,
    GRADE_DAILY_PROFIT_LOCK,
    SCALE_OUT_LEAVE_CONTRACTS,
    STARTING_NAV,
    TICK_SIZE,
    TICK_VALUE,
    forward_test_force_paper,
)
from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict
from manus.heartbeat import BrokerHeartbeatAgent, HeartbeatState
from engine.ui_state_bridge import persist_virtue_system_state
from scripts.run_daily_session import virtue_session_open
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
    trades_today: int = 0
    last_risk_verdict: str = ""
    last_risk_reason: str = ""
    last_regime: str = ""
    last_signal_reason: str = ""
    last_adx: float = 0.0
    last_atr_pct: float = 0.0
    last_vwap_score: float = 50.0
    last_twap_score: float = 50.0
    last_blended_score: float = 50.0
    last_data_source: str = "alpaca_spy_mes_proxy"
    anchors_aligned: bool = False
    strategy: WisdomStrategy = field(default_factory=WisdomStrategy)
    broker: VirtueBroker = field(default_factory=VirtueBroker)
    risk: CapitalProtectionMatrix = field(
        default_factory=lambda: CapitalProtectionMatrix(
            starting_nav=STARTING_NAV,
            account_nav=STARTING_NAV,
            peak_nav=STARTING_NAV,
        )
    )


def _publish_ui(session: VirtueSession, *, last_price: float | None = None) -> None:
    """Keep Streamlit/cloud dashboard fresh from virtue loop (not grade path)."""
    price = last_price if last_price is not None else float(getattr(session.broker, "_last_price", 0.0) or 0.0)
    persist_virtue_system_state(
        session,
        last_price=price if price > 0 else None,
        regime=session.last_regime,
        action=session.last_action,
        reason=session.last_signal_reason,
        adx=session.last_adx,
        atr_pct=session.last_atr_pct,
        vwap_score=session.last_vwap_score,
        twap_score=session.last_twap_score,
        blended_score=session.last_blended_score,
        data_source=session.last_data_source,
        last_risk_verdict=session.last_risk_verdict,
        last_risk_reason=session.last_risk_reason,
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
        "WISDOM_SEEDED bars=%s regime=%s action=%s vwap=%.1f twap=%.1f blend=%.1f adx=%.1f",
        len(bars),
        decision.regime.value,
        decision.action.value,
        decision.vwap_score,
        decision.twap_score,
        decision.blended_score,
        decision.adx,
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

    # Cash RTH only (Alpaca SPY live): Mon–Fri 9:30–16:00 ET.
    # Outside RTH → flatten any residual risk (Temperance: no overnight/weekend gaps).
    if not ignore_hours and not virtue_session_open():
        session.last_regime = "OUTSIDE_RTH"
        session.last_signal_reason = "rth_only_stand_aside"
        net_dir, net_size = session.broker.net_exposure()
        if net_size > 0:
            price = float(getattr(session.broker, "_last_price", 0.0) or 0.0)
            if price <= 0:
                try:
                    ctx = await session.broker.resolve_market_context_timed()
                    tick = (ctx or {}).get("tick") or {}
                    price = float(tick.get("price") or tick.get("last") or 0.0)
                except Exception as exc:
                    logger.exception("rth_flatten_price_failed err=%s", exc)
            if price > 0:
                try:
                    ok, pnl = await session.broker.flatten_all(
                        price=price,
                        stop_ticks=stop_ticks,
                        reason="rth_close_flatten",
                    )
                except Exception as exc:
                    logger.exception("rth_flatten_failed err=%s", exc)
                    session.last_action = "FLAT"
                    return
                if ok:
                    session.realized_pnl_today = round(session.realized_pnl_today + pnl, 2)
                    session.trades_today += 1
                    logger.info(
                        "CYCLE %s RTH_CLOSE_FLATTEN closed %s x%s pnl≈%.2f — no overnight gap",
                        session.cycle,
                        net_dir,
                        net_size,
                        pnl,
                    )
                else:
                    logger.error(
                        "CYCLE %s RTH_CLOSE_FLATTEN_FAILED — exposure may remain overnight",
                        session.cycle,
                    )
            else:
                logger.error(
                    "CYCLE %s outside_RTH with %s x%s but no mark price — cannot flatten",
                    session.cycle,
                    net_dir,
                    net_size,
                )
        else:
            logger.info("CYCLE %s outside_RTH — stand aside (Alpaca SPY / gap avoidance)", session.cycle)
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

    if ctx.get("stand_aside"):
        logger.error(
            "CYCLE %s market_data_stand_aside detail=%s — Justice flat (need live MES, not stale SPY)",
            session.cycle,
            ctx.get("detail"),
        )
        session.last_action = "FLAT"
        session.last_regime = "NO_FRESH_DATA"
        session.last_signal_reason = str(ctx.get("detail") or "stand_aside")
        return

    tick = ctx.get("tick") or {}
    session.last_data_source = str(tick.get("source") or session.last_data_source)
    price = float(tick.get("price") or tick.get("last") or 0.0)
    if price <= 0:
        logger.error("CYCLE %s invalid_price — Justice stand aside", session.cycle)
        session.last_action = "FLAT"
        return

    high = float(tick.get("ask") or price)
    low = float(tick.get("bid") or price)
    if high < low:
        high, low = low, high

    # Align rolling VWAP/TWAP to live print (seed bars can sit far from quote).
    # Re-rebase whenever gap is too wide so we never sit SHORT in a bull on false 0% scores.
    try:
        need_rebase = (not session.anchors_aligned) or session.strategy.anchor_gap_too_wide(price)
        if need_rebase:
            session.strategy.rebase_anchors_to_price(price)
            session.anchors_aligned = True
            logger.info(
                "CYCLE %s anchors_rebased_to_live px=%.2f vwap=%.2f twap=%.2f",
                session.cycle,
                price,
                session.strategy.vwap_tracker.vwap if session.strategy.vwap_tracker else 0.0,
                session.strategy.twap_tracker.twap if session.strategy.twap_tracker else 0.0,
            )
    except Exception as exc:
        logger.exception("anchor_rebase_failed err=%s", exc)
        session.last_action = "FLAT"
        return

    session.strategy.update_price(price, high=high + TICK_SIZE, low=low - TICK_SIZE)

    net_dir_pre, net_size_pre = session.broker.net_exposure()
    holding = net_dir_pre if net_size_pre > 0 else None
    decision = session.strategy.evaluate(holding=holding)
    session.last_regime = decision.regime.value
    session.last_signal_reason = decision.reason
    session.last_adx = float(decision.adx)
    session.last_atr_pct = float(decision.atr_pct)
    session.last_vwap_score = float(decision.vwap_score)
    session.last_twap_score = float(decision.twap_score)
    session.last_blended_score = float(decision.blended_score)
    logger.info(
        "CYCLE %s regime=%s action=%s vwap=%.1f%% twap=%.1f%% blend=%.1f%% "
        "px=%.2f vwap_px=%.2f twap_px=%.2f adx=%.1f atr_pct=%.2f reason=%s exposure=%s",
        session.cycle,
        decision.regime.value,
        decision.action.value,
        decision.vwap_score,
        decision.twap_score,
        decision.blended_score,
        price,
        decision.vwap,
        decision.twap,
        decision.adx,
        decision.atr_pct,
        decision.reason,
        session.broker.net_exposure(),
    )

    # Courage: flip only when hysteresis says the opposite side is clear
    net_dir, net_size = session.broker.net_exposure()
    wrong_side = (
        net_size > 0
        and decision.action in {SignalAction.LONG, SignalAction.SHORT}
        and net_dir != decision.action.value
    )
    if wrong_side:
        logger.warning(
            "CYCLE %s WRONG_SIDE_FLIP holding=%s scores vwap=%.1f twap=%.1f → %s",
            session.cycle,
            net_dir,
            decision.vwap_score,
            decision.twap_score,
            decision.action.value,
        )
        try:
            ok, pnl = await session.broker.flatten_all(
                price=price,
                stop_ticks=stop_ticks,
                reason=f"wrong_side_flip:{net_dir}_to_{decision.action.value}",
            )
        except Exception as exc:
            logger.exception("wrong_side_flatten_failed err=%s", exc)
            session.last_action = "FLAT"
            return
        if ok:
            session.realized_pnl_today = round(session.realized_pnl_today + pnl, 2)
            session.trades_today += 1
            logger.info(
                "CYCLE %s WRONG_SIDE_EXIT pnl≈%.2f — will follow %s same cycle",
                session.cycle,
                pnl,
                decision.action.value,
            )
        else:
            logger.error("CYCLE %s WRONG_SIDE_EXIT_FAILED — standing aside", session.cycle)
            session.last_action = "FLAT"
            return
        # Fall through to entry path for the correct side (Courage)

    # Hard stop from entry (Temperance) — exit even if regime still trending
    if session.broker.stop_hit(price=price, stop_ticks=stop_ticks):
        try:
            ok, pnl = await session.broker.flatten_all(
                price=price,
                stop_ticks=stop_ticks,
                reason="stop_hit",
            )
        except Exception as exc:
            logger.exception("stop_flatten_failed err=%s", exc)
            session.last_action = "FLAT"
            return
        if ok:
            session.realized_pnl_today = round(session.realized_pnl_today + pnl, 2)
            session.trades_today += 1
            logger.info("CYCLE %s STOP_EXIT pnl≈%.2f realized_today=%.2f", session.cycle, pnl, session.realized_pnl_today)
        else:
            logger.error("CYCLE %s STOP_EXIT_FAILED — exposure may remain", session.cycle)
        session.last_action = "FLAT"
        return

    # Scale-out take-profit (Temperance): bank most size, leave a runner
    # e.g. 3 contracts → close 2 at +120 ticks ($150/ct), leave 1 until stop/flat/flip
    # Only while Wisdom still wants the trade — FLAT path below closes everything.
    close_qty = session.broker.scale_out_close_qty(leave=SCALE_OUT_LEAVE_CONTRACTS)
    if (
        decision.action in {SignalAction.LONG, SignalAction.SHORT}
        and close_qty > 0
        and session.broker.take_profit_hit(price=price, target_ticks=DEFAULT_TARGET_TICKS)
    ):
        net_dir, net_size = session.broker.net_exposure()
        try:
            ok, pnl = await session.broker.partial_close(
                contracts=close_qty,
                price=price,
                stop_ticks=stop_ticks,
                reason=f"take_profit_{DEFAULT_TARGET_TICKS}t",
                leave=SCALE_OUT_LEAVE_CONTRACTS,
            )
        except Exception as exc:
            logger.exception("take_profit_partial_failed err=%s", exc)
            session.last_action = decision.action.value
            return
        if ok:
            session.realized_pnl_today = round(session.realized_pnl_today + pnl, 2)
            session.trades_today += 1
            remain = session.broker.net_exposure()[1]
            logger.info(
                "CYCLE %s TAKE_PROFIT_SCALE_OUT closed %s x%s leave=%s pnl≈%.2f "
                "target=%st realized_today=%.2f",
                session.cycle,
                net_dir,
                close_qty,
                remain,
                pnl,
                DEFAULT_TARGET_TICKS,
                session.realized_pnl_today,
            )
        else:
            logger.error(
                "CYCLE %s TAKE_PROFIT_SCALE_OUT_FAILED — size may still be %s",
                session.cycle,
                net_size,
            )
        session.last_action = decision.action.value
        return

    # Wisdom stand-aside / chop → close open risk (do not orphan positions)
    if decision.action == SignalAction.FLAT:
        net_dir, net_size = session.broker.net_exposure()
        if net_size > 0:
            try:
                ok, pnl = await session.broker.flatten_all(
                    price=price,
                    stop_ticks=stop_ticks,
                    reason=f"wisdom_flat:{decision.reason}",
                )
            except Exception as exc:
                logger.exception("flat_flatten_failed err=%s", exc)
                session.last_action = "FLAT"
                return
            if ok:
                session.realized_pnl_today = round(session.realized_pnl_today + pnl, 2)
                session.trades_today += 1
                logger.info(
                    "CYCLE %s WISDOM_FLAT_EXIT closed %s x%s pnl≈%.2f reason=%s",
                    session.cycle,
                    net_dir,
                    net_size,
                    pnl,
                    decision.reason,
                )
            else:
                logger.error("CYCLE %s WISDOM_FLAT_EXIT_FAILED — exposure may remain", session.cycle)
        session.last_action = "FLAT"
        return

    # Already in desired direction — hold (no pyramid every cycle)
    net_dir, net_size = session.broker.net_exposure()
    if net_size > 0 and net_dir == decision.action.value:
        logger.info(
            "CYCLE %s already_%s x%s — hold (no add)",
            session.cycle,
            net_dir,
            net_size,
        )
        session.last_action = decision.action.value
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

    # Sync Manus NAV from broker truth (including equity=0 → no fake STARTING_NAV)
    session.risk.update_nav(session.broker.equity)

    try:
        # Forward-test paper caps contracts (e.g. 3 MES = $225) below Manus 0.5% floor
        # (~$425 on $100k) — waive floor in FORWARD_TEST_MODE only.
        verdict, reason = session.risk.evaluate(
            realized_pnl_today=session.realized_pnl_today,
            open_risk_notional=open_risk,
            proposed_trade_risk=proposed_risk,
            sandbox_fallback=forward_test_force_paper(),
        )
    except Exception as exc:
        logger.exception("manus_evaluate_failed err=%s", exc)
        session.last_action = "FLAT"
        return

    session.last_risk_verdict = verdict.value
    session.last_risk_reason = reason

    if verdict == RiskVerdict.HALT:
        # Hard daily loss / concurrent risk: halt. Floor mismatch in paper: skip cycle only.
        if forward_test_force_paper() and "fixed_fractional_floor" in reason:
            logger.warning(
                "CYCLE %s MANUS_PAPER_SKIP reason=%s — stand aside this cycle (not session halt)",
                session.cycle,
                reason,
            )
            session.last_action = "FLAT"
            return
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
    session.trades_today += 1
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
        "VIRTUE LOOP start equity=%.2f symbol=%s stop_ticks=%s target_ticks=%s "
        "scale_out_leave=%s network_timeout=%.1fs",
        session.broker.equity,
        EXECUTION_SYMBOL,
        DEFAULT_STOP_TICKS,
        DEFAULT_TARGET_TICKS,
        SCALE_OUT_LEAVE_CONTRACTS,
        NETWORK_TIMEOUT_S,
    )

    # Boot reconcile (Webull absolute truth — including equity=0)
    try:
        truth = await session.broker.reconcile_with_broker()
        if truth.ok:
            session.risk.update_nav(session.broker.equity)
            session.realized_pnl_today = float(session.broker.realized_pnl or 0.0)
            if session.broker.equity <= 0:
                logger.error(
                    "BOOT zero_futures_equity — Virtue will stand aside on size until account is funded "
                    "(or enable FORWARD_TEST_MODE paper NAV / Webull sandbox keys)"
                )
            elif truth.detail == "forward_test_paper_nav":
                logger.info(
                    "BOOT forward_test_paper_nav equity=%.2f — local paper fills (not Webull app sandbox)",
                    session.broker.equity,
                )
    except Exception as exc:
        logger.exception("boot_reconcile_failed err=%s", exc)

    # Seed Wisdom from Alpaca history
    try:
        await seed_wisdom_from_alpaca(session)
    except Exception as exc:
        logger.exception("boot_seed_failed err=%s", exc)

    n = 0
    _publish_ui(session)
    while True:
        try:
            await run_cycle(session, ignore_hours=ignore_hours)
        except Exception as exc:
            logger.exception("cycle_unhandled err=%s — infinite outage survival", exc)
            try:
                await _survive_outage(session, "cycle_exception")
            except Exception:
                logger.exception("reconnect_after_cycle_exception_failed — will retry next loop")
        finally:
            _publish_ui(session)

        n += 1
        if once or (cycles is not None and n >= cycles):
            break
        try:
            await asyncio.sleep(max(1.0, float(interval_s)))
        except Exception as exc:
            logger.exception("loop_sleep_failed err=%s", exc)

    logger.info("VIRTUE LOOP done cycles=%s last_action=%s", n, session.last_action)
    _publish_ui(session)


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
