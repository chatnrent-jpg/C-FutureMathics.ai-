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
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from broker import NETWORK_TIMEOUT_S, Order, SizeResult, VirtueBroker
from engine.config import (
    DEFAULT_STOP_TICKS,
    EXECUTION_SYMBOL,
    FORWARD_TEST_TIMEZONE,
    GRADE_DAILY_PROFIT_LOCK,
    STARTING_NAV,
    TICK_SIZE,
    TICK_VALUE,
    VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT,
    VIRTUE_FORCE_EVENT_MAX_S,
    VIRTUE_HEARTBEAT_EVERY_N_CYCLES,
    VIRTUE_MIN_PRICE_MOVE_TICKS,
    VIRTUE_POSITION_STOP_DOLLARS,
    VIRTUE_POSITION_TP_DOLLARS,
    VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES,
    VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES,
    VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES,
    VIRTUE_REQUIRED_STREAK,
    VIRTUE_RTH_FLATTEN_MAX_ATTEMPTS,
    VIRTUE_RTH_FLATTEN_RETRY_S,
    VIRTUE_SCORE_LONG_CHASE_MAX,
    VIRTUE_SCORE_LONG_ENTER,
    VIRTUE_SCORE_LONG_EXIT,
    VIRTUE_SCORE_PRICE_PCT,
    VIRTUE_SCORE_SHORT_CHASE_MIN,
    VIRTUE_SCORE_SHORT_ENTER,
    VIRTUE_SCORE_SHORT_EXIT,
    VIRTUE_STATE_PERSIST_INTERVAL_S,
    VIRTUE_TICK_POLL_S,
    fixed_fractional_risk_pct,
    forward_test_force_paper,
)

_ET = ZoneInfo(FORWARD_TEST_TIMEZONE)
from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict
from manus.heartbeat import BrokerHeartbeatAgent, HeartbeatState
from engine.ui_state_bridge import (
    load_persisted_book_equity,
    load_persisted_day_bucket,
    load_persisted_open_positions,
    persist_virtue_system_state,
)
from scripts.run_daily_session import virtue_entries_allowed, virtue_session_open
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
    session_date_et: str = ""  # YYYY-MM-DD America/New_York — Temperance day bucket
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
    entry_cooldown_cycles: int = 0  # skip new entries after anchor rebase
    long_streak: int = 0
    short_streak: int = 0
    is_running: bool = True  # False stops background state writer (run.py pattern)
    cycles_since_heartbeat: int = 0
    last_heartbeat_ok: bool = True
    last_heartbeat_state: str = "GREEN"
    strategy: WisdomStrategy = field(
        default_factory=lambda: WisdomStrategy(
            long_enter=float(VIRTUE_SCORE_LONG_ENTER),
            short_enter=float(VIRTUE_SCORE_SHORT_ENTER),
            long_exit=float(VIRTUE_SCORE_LONG_EXIT),
            short_exit=float(VIRTUE_SCORE_SHORT_EXIT),
            score_price_pct=float(VIRTUE_SCORE_PRICE_PCT),
        )
    )
    broker: VirtueBroker = field(default_factory=VirtueBroker)
    risk: CapitalProtectionMatrix = field(
        default_factory=lambda: CapitalProtectionMatrix(
            starting_nav=STARTING_NAV,
            account_nav=STARTING_NAV,
            peak_nav=STARTING_NAV,
        )
    )


def target_ticks_from_atr(atr: float) -> int:
    """
    Legacy ATR take-profit helper (compat/tests).
    Virtue live exits use VIRTUE_POSITION_TP_DOLLARS via take_profit_dollars_hit.
    """
    from engine.config import VIRTUE_TP_ATR_MULT, VIRTUE_TP_MIN_TICKS

    atr_pts = max(0.0, float(atr or 0.0))
    atr_ticks = atr_pts / float(TICK_SIZE) if TICK_SIZE > 0 else 0.0
    raw = int(round(atr_ticks * float(VIRTUE_TP_ATR_MULT)))
    return max(int(VIRTUE_TP_MIN_TICKS), raw)


def position_tp_ticks(contracts: int) -> int:
    """Ticks of favorable move so position PnL ≈ VIRTUE_POSITION_TP_DOLLARS."""
    n = max(1, int(contracts))
    tv = float(TICK_VALUE)
    if tv <= 0:
        return 1
    return max(1, int(math.ceil(float(VIRTUE_POSITION_TP_DOLLARS) / (tv * n))))


def position_stop_ticks(contracts: int) -> int:
    """Ticks of adverse move so position PnL ≈ -VIRTUE_POSITION_STOP_DOLLARS."""
    n = max(1, int(contracts))
    tv = float(TICK_VALUE)
    if tv <= 0:
        return 1
    return max(1, int(math.ceil(float(VIRTUE_POSITION_STOP_DOLLARS) / (tv * n))))


def et_session_date(now: datetime | None = None) -> str:
    """Calendar trading day in ET for daily profit-lock / trade counters."""
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    else:
        dt = dt.astimezone(_ET)
    return dt.strftime("%Y-%m-%d")


def roll_daily_counters_if_needed(session: VirtueSession, *, now: datetime | None = None) -> bool:
    """
    Reset Temperance day counters when the ET calendar date changes.

    Justice: yesterday's realized PnL must never lock today's entries.
    Temperance: book equity / account NAV must NOT reset — only the day bucket clears.
    """
    today = et_session_date(now)
    if session.session_date_et == today:
        return False
    prior_date = session.session_date_et or "(boot)"
    prior_pnl = float(session.realized_pnl_today)
    prior_trades = int(session.trades_today)
    session.session_date_et = today
    session.realized_pnl_today = 0.0
    session.trades_today = 0
    logger.info(
        "SESSION_DAY_ROLL et_date=%s prior_date=%s prior_realized=%.2f prior_trades=%s "
        "book_equity=%.2f — day counters reset; NAV compounds (not reset)",
        today,
        prior_date,
        prior_pnl,
        prior_trades,
        float(session.broker.equity),
    )
    return True


def _local_paper_book() -> bool:
    """True when local paper fills own the book (Webull equity is not truth)."""
    from engine.webull_openapi import webull_is_sandbox

    return bool(forward_test_force_paper() and not webull_is_sandbox())


def _credit_realized_pnl(session: VirtueSession, pnl: float) -> None:
    """
    Bank realized PnL into the day bucket.

    Justice: update book equity only on local paper. Live/sandbox reconcile already
    set broker.equity — adding approx_pnl again would double-count NAV.
    """
    delta = float(pnl or 0.0)
    session.realized_pnl_today = round(session.realized_pnl_today + delta, 2)
    if abs(delta) >= 1e-12 and _local_paper_book():
        session.broker.update_equity(round(float(session.broker.equity) + delta, 2))
    session.risk.update_nav(session.broker.equity)



def _publish_ui(session: VirtueSession, *, last_price: float | None = None) -> None:
    """Persist to primary live data/system_state.json for Streamlit / cloud dashboard."""
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
        heartbeat_state=session.last_heartbeat_state,
    )


async def save_state_throttled(
    session: VirtueSession,
    *,
    interval_s: float | None = None,
) -> None:
    """
    Background Justice writer — concurrent with the market event engine.
    Always writes the primary live path: data/system_state.json (via _publish_ui).
    Disk I/O stays off the hot path via asyncio.to_thread.
    """
    sleep_s = float(interval_s if interval_s is not None else VIRTUE_STATE_PERSIST_INTERVAL_S)
    sleep_s = max(0.5, sleep_s)
    while session.is_running:
        try:
            await asyncio.to_thread(_publish_ui, session)
        except Exception as exc:
            logger.error("Justice Layer write failure (system_state.json): %s", exc)
        await asyncio.sleep(sleep_s)


async def market_tick_listener(
    session: VirtueSession,
    tick_queue: asyncio.Queue,
    *,
    poll_s: float,
) -> None:
    """
    Produce market tick events onto the queue (event-driven source).
    Calm efficiency: poll at poll_s; skip enqueue when price is quiet unless
    a force-event timer fires (so open risk still gets stop/TP checks).
    """
    cadence = max(0.5, float(poll_s))
    min_move = max(1, int(VIRTUE_MIN_PRICE_MOVE_TICKS)) * float(TICK_SIZE)
    force_every = max(cadence, float(VIRTUE_FORCE_EVENT_MAX_S))
    last_emitted_px = 0.0
    last_emit_mono = 0.0
    logger.info(
        "MARKET_LISTENER start poll_s=%.1f force_event_max=%.1fs min_move=%.2f → event queue",
        cadence,
        force_every,
        min_move,
    )
    while session.is_running:
        try:
            ctx = await session.broker.resolve_market_context_timed()
            tick = (ctx or {}).get("tick") or {}
            px = float(tick.get("price") or tick.get("last") or 0.0)
            now_mono = time.monotonic()
            holding = session.broker.net_exposure()[1] > 0
            moved = last_emitted_px <= 0 or abs(px - last_emitted_px) >= min_move
            due = (now_mono - last_emit_mono) >= force_every
            # Always emit when holding (Temperance stops), on meaningful move, or force timer.
            if px > 0 and (holding or moved or due or last_emitted_px <= 0):
                while not tick_queue.empty():
                    try:
                        tick_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await tick_queue.put({"ok": True, "ctx": ctx})
                last_emitted_px = px
                last_emit_mono = now_mono
        except Exception as exc:
            logger.exception("market_tick_listener_failed err=%s", exc)
            try:
                await tick_queue.put({"ok": False, "error": str(exc)})
            except Exception:
                logger.exception("market_tick_enqueue_failed")
        await asyncio.sleep(cadence)
    try:
        await tick_queue.put(None)
    except Exception:
        pass


async def engine_event_loop(
    session: VirtueSession,
    tick_queue: asyncio.Queue,
    *,
    cycles: int | None,
    once: bool,
    ignore_hours: bool,
    stop_ticks: int = DEFAULT_STOP_TICKS,
) -> int:
    """
    Consume market tick events and run virtue cycles (Courage — act when signal arrives).
    Replaces the old sleep-then-poll main loop.
    """
    n = 0
    while session.is_running:
        try:
            item = await tick_queue.get()
        except Exception as exc:
            logger.exception("engine_event_queue_get_failed err=%s", exc)
            await asyncio.sleep(1.0)
            continue

        # Coalesce to latest event while running.
        while True:
            try:
                nxt = tick_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            item = nxt

        if item is None:
            break

        market_ctx = None
        if isinstance(item, dict) and item.get("ok") and isinstance(item.get("ctx"), dict):
            market_ctx = item["ctx"]
        elif isinstance(item, dict) and not item.get("ok"):
            logger.error(
                "CYCLE pending market_listener_error err=%s — outage survival",
                item.get("error"),
            )
            try:
                await _survive_outage(session, "market_listener_error")
            except Exception:
                logger.exception("reconnect_after_listener_error_failed")
            continue

        try:
            await run_cycle(
                session,
                stop_ticks=stop_ticks,
                ignore_hours=ignore_hours,
                market_ctx=market_ctx,
            )
        except Exception as exc:
            logger.exception("cycle_unhandled err=%s — infinite outage survival", exc)
            try:
                await _survive_outage(session, "cycle_exception")
            except Exception:
                logger.exception("reconnect_after_cycle_exception_failed — will retry next event")

        n += 1
        if once or (cycles is not None and n >= cycles):
            session.is_running = False
            break

    return n


def _should_run_heartbeat(session: VirtueSession) -> bool:
    """Calm efficiency: probe every N cycles; always on first / after prior failure / triage."""
    every = max(1, int(VIRTUE_HEARTBEAT_EVERY_N_CYCLES))
    session.cycles_since_heartbeat += 1
    if session.cycle <= 1:
        return True
    if not session.last_heartbeat_ok:
        return True
    if not session.broker.can_send_new_orders():
        return True
    return session.cycles_since_heartbeat >= every



async def _heartbeat_probe(broker: VirtueBroker) -> tuple[bool, str]:
    try:
        return await asyncio.wait_for(
            broker.health_check_timed(),
            timeout=NETWORK_TIMEOUT_S,
        )
    except Exception as exc:
        return False, str(exc)


async def seed_wisdom_from_market(session: VirtueSession, *, limit: int = 120) -> int:
    """Warm WisdomStrategy from Databento MES bars (preferred) or Alpaca SPY→MES proxy."""
    bars: list[Bar] = []
    seed_src = "none"
    mes_data = getattr(session.broker, "mes_data", None)
    if mes_data is not None and mes_data.is_configured():
        try:
            raw = await mes_data.fetch_ohlcv_bars(timeframe="1m", limit=limit, lookback_hours=36)
            ohlc = mes_data.bars_as_strategy_ohlc(raw)
            bars = [
                Bar(high=float(b["high"]), low=float(b["low"]), close=float(b["close"]))
                for b in ohlc
            ]
            if bars:
                seed_src = "databento_mes"
                session.last_data_source = "databento_mes"
        except Exception as exc:
            logger.exception("databento_bar_seed_failed err=%s", exc)

    if not bars:
        try:
            spy_bars = await session.broker.data.fetch_spy_bars(timeframe="5Min", limit=limit)
            mes_bars = session.broker.data.mes_proxy_bars_from_spy(spy_bars)
            bars = [
                Bar(high=float(b["high"]), low=float(b["low"]), close=float(b["close"]))
                for b in mes_bars
            ]
            if bars:
                seed_src = "alpaca_spy_mes_proxy"
                session.last_data_source = "alpaca_spy_mes_proxy"
        except Exception as exc:
            logger.exception("alpaca_bar_seed_failed err=%s", exc)
            return 0

    if not bars:
        logger.warning("bar_seed empty — Wisdom stays in WARMUP until live ticks accumulate")
        return 0
    session.strategy.seed(bars)
    session.broker._last_price = float(bars[-1].close)
    decision = session.strategy.evaluate()
    logger.info(
        "WISDOM_SEEDED src=%s bars=%s regime=%s action=%s vwap=%.1f twap=%.1f blend=%.1f adx=%.1f",
        seed_src,
        len(bars),
        decision.regime.value,
        decision.action.value,
        decision.vwap_score,
        decision.twap_score,
        decision.blended_score,
        decision.adx,
    )
    return len(bars)


# Backward-compatible alias
async def seed_wisdom_from_alpaca(session: VirtueSession, *, limit: int = 120) -> int:
    return await seed_wisdom_from_market(session, limit=limit)


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
        # Do NOT copy broker.realized_pnl into realized_pnl_today — it can be
        # process-lifetime paper PnL and would falsely trip daily_profit_lock.
        roll_daily_counters_if_needed(session)
        logger.info(
            "OUTAGE_RECOVERED equity=%.2f realized_pnl_today=%.2f positions=%s",
            session.broker.equity,
            session.realized_pnl_today,
            len(session.broker.open_positions),
        )
    return ok


def _open_risk_notional(session: VirtueSession, stop_ticks: int) -> float:
    """Open risk at the dollar stop (Temperance — matches live exit, not legacy tick stop)."""
    del stop_ticks  # legacy signature; dollar stop owns live risk
    _, size = session.broker.net_exposure()
    if size <= 0:
        return 0.0
    return float(VIRTUE_POSITION_STOP_DOLLARS)


async def _resolve_flatten_price(session: VirtueSession) -> float:
    price = float(getattr(session.broker, "_last_price", 0.0) or 0.0)
    if price > 0:
        return price
    try:
        ctx = await session.broker.resolve_market_context_timed()
        tick = (ctx or {}).get("tick") or {}
        return float(tick.get("price") or tick.get("last") or 0.0)
    except Exception as exc:
        logger.exception("rth_flatten_price_failed err=%s", exc)
        return 0.0


async def _flatten_until_flat(
    session: VirtueSession,
    *,
    stop_ticks: int,
    reason: str = "rth_close_flatten",
) -> bool:
    """
    Temperance: retry flatten until flat so residual size cannot ride overnight/weekend gaps.
    Returns True when exposure is flat.
    """
    attempts = max(1, int(VIRTUE_RTH_FLATTEN_MAX_ATTEMPTS))
    retry_s = max(0.5, float(VIRTUE_RTH_FLATTEN_RETRY_S))
    for attempt in range(1, attempts + 1):
        net_dir, net_size = session.broker.net_exposure()
        if net_size <= 0:
            return True
        price = await _resolve_flatten_price(session)
        if price <= 0:
            logger.error(
                "CYCLE %s %s attempt=%s/%s no mark price — cannot flatten %s x%s",
                session.cycle,
                reason,
                attempt,
                attempts,
                net_dir,
                net_size,
            )
            await asyncio.sleep(retry_s)
            continue
        try:
            ok, pnl = await session.broker.flatten_all(
                price=price,
                stop_ticks=stop_ticks,
                reason=reason,
            )
        except Exception as exc:
            logger.exception(
                "CYCLE %s %s attempt=%s/%s exception err=%s",
                session.cycle,
                reason,
                attempt,
                attempts,
                exc,
            )
            await asyncio.sleep(retry_s)
            continue
        if ok:
            _credit_realized_pnl(session, pnl)
            session.trades_today += 1
            logger.info(
                "CYCLE %s %s attempt=%s closed %s x%s pnl≈%.2f",
                session.cycle,
                reason,
                attempt,
                net_dir,
                net_size,
                pnl,
            )
        else:
            logger.error(
                "CYCLE %s %s attempt=%s/%s FAILED — retrying",
                session.cycle,
                reason,
                attempt,
                attempts,
            )
        if session.broker.net_exposure()[1] <= 0:
            return True
        await asyncio.sleep(retry_s)
    _, left = session.broker.net_exposure()
    if left > 0:
        logger.error(
            "CYCLE %s %s EXHAUSTED — exposure may remain overnight size=%s",
            session.cycle,
            reason,
            left,
        )
        return False
    return True


async def _rth_gate_or_flatten(
    session: VirtueSession,
    *,
    stop_ticks: int,
    ignore_hours: bool,
) -> bool:
    """
    Return True if trading cycle may continue.
    Outside session (RTH or CME): flatten residual risk and return False (Temperance).
    """
    if ignore_hours or virtue_session_open():
        return True
    session.last_regime = "OUTSIDE_SESSION"
    session.last_signal_reason = "outside_session_stand_aside"
    _, net_size = session.broker.net_exposure()
    if net_size > 0:
        await _flatten_until_flat(session, stop_ticks=stop_ticks, reason="session_close_flatten")
    else:
        logger.info(
            "CYCLE %s outside_session — stand aside (CME closed / maintenance or RTH-only mode)",
            session.cycle,
        )
    session.last_action = "FLAT"
    return False


async def run_cycle(
    session: VirtueSession,
    *,
    stop_ticks: int = DEFAULT_STOP_TICKS,
    ignore_hours: bool = False,
    market_ctx: dict | None = None,
) -> None:
    """One virtue cycle: hours → heartbeat → market → regime → exclusivity → Manus → fire."""
    session.cycle += 1
    # Temperance: new ET calendar day → clear yesterday's PnL/trade counters (Justice).
    roll_daily_counters_if_needed(session)
    # Tick post-rebase entry cooldown every cycle (even while holding).
    cooldown_blocks_entry = session.entry_cooldown_cycles > 0
    if session.entry_cooldown_cycles > 0:
        session.entry_cooldown_cycles -= 1

    # Session gate: RTH-only when Alpaca primary; full CME hours when Databento primary.
    # Outside session → flatten residual risk (Temperance).
    if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
        return

    if session.halted:
        logger.warning("CYCLE %s session_halted by Manus — stand aside", session.cycle)
        session.last_action = "FLAT"
        return

    # Calm efficiency: full heartbeat every N cycles (old-format load), not every event.
    if _should_run_heartbeat(session):
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
            session.last_heartbeat_ok = False
            session.cycles_since_heartbeat = 0
            await _survive_outage(session, "heartbeat_exception")
            session.last_action = "FLAT"
            return

        dead = any(e.state == HeartbeatState.DEAD for e in events)
        degraded = any(e.state == HeartbeatState.DEGRADED for e in events)
        session.cycles_since_heartbeat = 0
        if dead:
            session.last_heartbeat_ok = False
            session.last_heartbeat_state = "DEAD"
            recovered = await _survive_outage(session, "heartbeat_dead")
            if not recovered:
                session.last_action = "FLAT"
                return
            # Outage may have spanned the 16:00 boundary — re-gate before continuing.
            if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
                return
        else:
            session.last_heartbeat_ok = True
            session.last_heartbeat_state = "DEGRADED" if degraded else "GREEN"
    # else: skipped heartbeat this cycle — cycles_since_heartbeat already advanced

    if not session.broker.can_send_new_orders():
        logger.warning("CYCLE %s triage=%s — forcing reconcile path", session.cycle, session.broker.triage.value)
        recovered = await _survive_outage(session, "triage_not_ready")
        if not recovered or not session.broker.can_send_new_orders():
            session.last_action = "FLAT"
            return
        if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
            return

    # Event-driven path supplies market_ctx from the listener; fallback fetch for --once tools.
    if market_ctx is not None:
        ctx = market_ctx
    else:
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

    # Re-check RTH after market context (cycle may have started in RTH then crossed close).
    if not await _rth_gate_or_flatten(session, stop_ticks=stop_ticks, ignore_hours=ignore_hours):
        return
    high = float(tick.get("ask") or price)
    low = float(tick.get("bid") or price)
    if high < low:
        high, low = low, high

    # Align rolling VWAP/TWAP to live print (seed bars can sit far from quote).
    # Also rebase when VWAP↔TWAP diverge beyond ATR×N (sketch: anchor disagreement).
    try:
        need_rebase = (
            (not session.anchors_aligned)
            or session.strategy.anchor_gap_too_wide(price)
            or session.strategy.anchors_diverged(atr_mult=float(VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT))
        )
        if need_rebase:
            session.strategy.rebase_anchors_to_price(price)
            session.anchors_aligned = True
            session.entry_cooldown_cycles = max(
                int(session.entry_cooldown_cycles),
                max(0, int(VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES)),
            )
            # Same-cycle: do not fire new entries right after rebase (scores near 50).
            cooldown_blocks_entry = session.entry_cooldown_cycles > 0
            logger.info(
                "CYCLE %s anchors_rebased_to_live px=%.2f vwap=%.2f twap=%.2f entry_cooldown=%s",
                session.cycle,
                price,
                session.strategy.vwap_tracker.vwap if session.strategy.vwap_tracker else 0.0,
                session.strategy.twap_tracker.twap if session.strategy.twap_tracker else 0.0,
                session.entry_cooldown_cycles,
            )
    except Exception as exc:
        logger.exception("anchor_rebase_failed err=%s", exc)
        session.last_action = "FLAT"
        return

    # Use true bid/ask (or mid) — do not pad ±1 tick (that inflated ATR on proxy quotes).
    session.strategy.update_price(price, high=high, low=low)

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
    _, size_for_tp = session.broker.net_exposure()
    size_ref = size_for_tp if size_for_tp > 0 else 2
    tp_ticks = position_tp_ticks(size_ref)
    sl_ticks = position_stop_ticks(size_ref)
    open_pnl = session.broker.unrealized_position_pnl(price=price)

    # Dual independent streaks from raw entry bands (build while flat OR holding).
    # Enables same-cycle flip when opposite streak is already ready (Courage).
    v_score = float(decision.vwap_score)
    t_score = float(decision.twap_score)
    is_raw_long = v_score >= float(VIRTUE_SCORE_LONG_ENTER) and t_score >= float(VIRTUE_SCORE_LONG_ENTER)
    is_raw_short = v_score <= float(VIRTUE_SCORE_SHORT_ENTER) and t_score <= float(VIRTUE_SCORE_SHORT_ENTER)
    session.long_streak = (session.long_streak + 1) if is_raw_long else 0
    session.short_streak = (session.short_streak + 1) if is_raw_short else 0
    logger.info(
        "CYCLE %s regime=%s action=%s vwap=%.1f%% twap=%.1f%% blend=%.1f%% "
        "px=%.2f vwap_px=%.2f twap_px=%.2f adx=%.1f atr=%.2f atr_pct=%.2f "
        "tp=$%.0f (~%st) sl=$%.0f (~%st) open_pnl=%.2f long_streak=%s short_streak=%s "
        "reason=%s exposure=%s",
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
        decision.atr,
        decision.atr_pct,
        float(VIRTUE_POSITION_TP_DOLLARS),
        tp_ticks,
        float(VIRTUE_POSITION_STOP_DOLLARS),
        sl_ticks,
        open_pnl,
        session.long_streak,
        session.short_streak,
        decision.reason,
        session.broker.net_exposure(),
    )

    # Exit priority (Temperance + Wisdom structure):
    # 1) dollar stop  2) take-profit  3) opposite-band flatten-to-flat (no same-cycle reverse)

    # Dollar stop (Temperance) — cut ~$75 on the whole position, then cool down.
    if session.broker.stop_dollars_hit(
        price=price, stop_dollars=float(VIRTUE_POSITION_STOP_DOLLARS)
    ):
        net_dir, net_size = session.broker.net_exposure()
        try:
            ok, pnl = await session.broker.flatten_all(
                price=price,
                stop_ticks=stop_ticks,
                reason=f"stop_${float(VIRTUE_POSITION_STOP_DOLLARS):.0f}",
            )
        except Exception as exc:
            logger.exception("stop_flatten_failed err=%s", exc)
            session.last_action = "FLAT"
            return
        if ok:
            _credit_realized_pnl(session, pnl)
            session.trades_today += 1
            session.entry_cooldown_cycles = max(
                int(session.entry_cooldown_cycles),
                int(VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES),
            )
            session.long_streak = 0
            session.short_streak = 0
            logger.info(
                "CYCLE %s STOP_EXIT closed %s x%s pnl≈%.2f stop=$%.0f "
                "cooldown=%s realized_today=%.2f",
                session.cycle,
                net_dir,
                net_size,
                pnl,
                float(VIRTUE_POSITION_STOP_DOLLARS),
                session.entry_cooldown_cycles,
                session.realized_pnl_today,
            )
        else:
            logger.error("CYCLE %s STOP_EXIT_FAILED — exposure may remain", session.cycle)
        session.last_action = "FLAT"
        return

    # Take-profit (Temperance): bank ~$100 on the whole position, full flatten, then cooldown.
    # Not gated on signal side — position geometry / open PnL only.
    if session.broker.take_profit_dollars_hit(
        price=price, target_dollars=float(VIRTUE_POSITION_TP_DOLLARS)
    ):
        net_dir, net_size = session.broker.net_exposure()
        try:
            ok, pnl = await session.broker.flatten_all(
                price=price,
                stop_ticks=stop_ticks,
                reason=f"take_profit_${float(VIRTUE_POSITION_TP_DOLLARS):.0f}",
            )
        except Exception as exc:
            logger.exception("take_profit_full_failed err=%s", exc)
            session.last_action = net_dir if net_dir != "FLAT" else "FLAT"
            return
        if ok:
            _credit_realized_pnl(session, pnl)
            session.trades_today += 1
            session.entry_cooldown_cycles = max(
                int(session.entry_cooldown_cycles),
                int(VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES),
            )
            session.long_streak = 0
            session.short_streak = 0
            logger.info(
                "CYCLE %s TAKE_PROFIT_FULL closed %s x%s pnl≈%.2f target=$%.0f "
                "cooldown=%s realized_today=%.2f",
                session.cycle,
                net_dir,
                net_size,
                pnl,
                float(VIRTUE_POSITION_TP_DOLLARS),
                session.entry_cooldown_cycles,
                session.realized_pnl_today,
            )
        else:
            logger.error("CYCLE %s TAKE_PROFIT_FULL_FAILED — exposure may remain", session.cycle)
        session.last_action = "FLAT"
        return

    # Opposite-band exit: flatten to FLAT only. Do NOT reverse same cycle (structure).
    # Reverse needs a fresh streak from flat on a later cycle (Courage with confirmation).
    net_dir, net_size = session.broker.net_exposure()
    wrong_side = (
        net_size > 0
        and decision.action in {SignalAction.LONG, SignalAction.SHORT}
        and net_dir != decision.action.value
    )
    if wrong_side:
        logger.warning(
            "CYCLE %s STRUCTURED_EXIT holding=%s scores vwap=%.1f twap=%.1f → signal=%s "
            "(flatten to flat; no same-cycle reverse)",
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
                reason=f"structured_exit:{net_dir}_vs_{decision.action.value}",
            )
        except Exception as exc:
            logger.exception("structured_exit_failed err=%s", exc)
            session.last_action = "FLAT"
            return
        if ok:
            _credit_realized_pnl(session, pnl)
            session.trades_today += 1
            # Cool off briefly so scores must re-confirm before opposite entry.
            session.entry_cooldown_cycles = max(
                int(session.entry_cooldown_cycles),
                int(VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES),
            )
            session.long_streak = 0
            session.short_streak = 0
            logger.info(
                "CYCLE %s STRUCTURED_EXIT_FLAT pnl≈%.2f cooldown=%s — wait for re-confirm",
                session.cycle,
                pnl,
                session.entry_cooldown_cycles,
            )
        else:
            logger.error("CYCLE %s STRUCTURED_EXIT_FAILED — standing aside", session.cycle)
        session.last_action = "FLAT"
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
                _credit_realized_pnl(session, pnl)
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

    # Temperance: daily profit lock — day is done; bank the win, no new entries.
    if session.realized_pnl_today >= float(GRADE_DAILY_PROFIT_LOCK):
        logger.info(
            "CYCLE %s daily_profit_lock pnl=%.2f >= %.2f — work done for the day (stand aside)",
            session.cycle,
            session.realized_pnl_today,
            GRADE_DAILY_PROFIT_LOCK,
        )
        session.last_action = "FLAT" if session.broker.net_exposure()[1] <= 0 else decision.action.value
        return

    # Temperance: last 15 min of RTH — manage/exit only, no new overnight risk.
    if not ignore_hours and not virtue_entries_allowed():
        logger.info(
            "CYCLE %s no_new_entry_cutoff — manage/exit only (pre-close / pre-maintenance)",
            session.cycle,
        )
        session.last_action = "FLAT" if session.broker.net_exposure()[1] <= 0 else decision.action.value
        return

    # After anchor rebase, scores sit near 50 — wait before new entries (Temperance).
    if cooldown_blocks_entry:
        logger.info(
            "CYCLE %s entry_cooldown — stand aside new entries (remaining=%s)",
            session.cycle,
            session.entry_cooldown_cycles,
        )
        session.last_action = "FLAT" if session.broker.net_exposure()[1] <= 0 else decision.action.value
        return

    # Require N consecutive raw entry-band cycles before firing (Temperance).
    need_streak = max(1, int(VIRTUE_REQUIRED_STREAK))
    side = decision.action.value
    if side == "LONG":
        if session.long_streak < need_streak:
            logger.info(
                "CYCLE %s entry_streak LONG %s/%s — wait for confirmation",
                session.cycle,
                session.long_streak,
                need_streak,
            )
            session.last_action = "FLAT"
            return
    elif side == "SHORT":
        if session.short_streak < need_streak:
            logger.info(
                "CYCLE %s entry_streak SHORT %s/%s — wait for confirmation",
                session.cycle,
                session.short_streak,
                need_streak,
            )
            session.last_action = "FLAT"
            return
    else:
        session.last_action = "FLAT"
        return

    # Wisdom: do not chase a move that already extended (late entry → stop / RTH flatten).
    blend = float(decision.blended_score)
    if side == "LONG" and blend >= float(VIRTUE_SCORE_LONG_CHASE_MAX):
        logger.info(
            "CYCLE %s chase_filter LONG blend=%.1f >= %.1f — stand aside (move already extended)",
            session.cycle,
            blend,
            VIRTUE_SCORE_LONG_CHASE_MAX,
        )
        session.last_action = "FLAT"
        return
    if side == "SHORT" and blend <= float(VIRTUE_SCORE_SHORT_CHASE_MIN):
        logger.info(
            "CYCLE %s chase_filter SHORT blend=%.1f <= %.1f — stand aside (move already extended)",
            session.cycle,
            blend,
            VIRTUE_SCORE_SHORT_CHASE_MIN,
        )
        session.last_action = "FLAT"
        return

    # Mandatory exclusivity check before emitting new LONG/SHORT payload
    try:
        exclusive_ok, excl_pnl = await session.broker.flatten_opposite_if_needed(
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
    if abs(float(excl_pnl or 0.0)) >= 1e-12:
        _credit_realized_pnl(session, excl_pnl)
        session.trades_today += 1

    # Drag-aware sizing (single risk source of truth with Manus).
    drag_mult = float(session.risk.capital_drag_multiplier)
    size_pct = float(fixed_fractional_risk_pct()) * drag_mult
    sized = session.broker.size_for_direction(
        direction=decision.action.value,
        stop_ticks=stop_ticks,
        risk_limit_pct=size_pct,
    )
    # Under drag, budget may reject 1 MES — retry undragged for irreducible unit.
    if (sized.rejected or sized.contracts < 1) and drag_mult < 1.0 - 1e-12:
        sized = session.broker.size_for_direction(
            direction=decision.action.value,
            stop_ticks=stop_ticks,
            risk_limit_pct=float(fixed_fractional_risk_pct()),
        )
        if not sized.rejected and sized.contracts >= 1:
            unit_risk = float(1 * stop_ticks * TICK_VALUE)
            sized = SizeResult(
                contracts=1,
                risk_dollars=unit_risk,
                risk_pct=unit_risk / max(float(session.broker.equity), 1e-9),
                max_allowed_risk=sized.max_allowed_risk,
                rejected=False,
                reason="irreducible_unit_under_drag",
            )
    if sized.rejected or sized.contracts < 1:
        logger.warning("CYCLE %s size_rejected reason=%s", session.cycle, sized.reason)
        session.last_action = "FLAT"
        return

    contracts = int(sized.contracts)
    # Live exit is dollar stop on the whole book — Manus risk matches that (Temperance).
    proposed_risk = float(VIRTUE_POSITION_STOP_DOLLARS)
    open_risk = _open_risk_notional(session, stop_ticks)

    # Sync Manus NAV from broker truth (including equity=0 → no fake STARTING_NAV)
    session.risk.update_nav(session.broker.equity)

    try:
        # Forward-test paper: waive floor when FF floor > irreducible 1 MES stop.
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
        # Hard daily / concurrent: session halt. Sizing band mismatch: cycle stand-aside only.
        soft_halt = (
            "fixed_fractional_floor" in reason
            or "exceeds_fixed_fractional" in reason
        )
        if soft_halt:
            logger.warning(
                "CYCLE %s MANUS_CYCLE_SKIP reason=%s — stand aside this cycle (not session halt)",
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
        if contracts <= 1:
            logger.warning(
                "CYCLE %s MANUS_REDUCE_MIN_LOT reason=%s — keep 1 MES (cannot shrink further)",
                session.cycle,
                reason,
            )
        else:
            contracts = max(1, contracts // 2)
            proposed_risk = float(VIRTUE_POSITION_STOP_DOLLARS)
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
    # Compounded book equity + open paper positions persist across restarts.
    book = load_persisted_book_equity(STARTING_NAV)
    session.broker.update_equity(book)
    session.risk.update_nav(book)
    session.risk.peak_nav = max(float(session.risk.peak_nav), book)
    restored = load_persisted_open_positions()
    if restored:
        session.broker.open_positions = list(restored)
        logger.info(
            "BOOT restored_paper_positions n=%s exposure=%s",
            len(restored),
            session.broker.net_exposure(),
        )
    # Justice: same ET day → restore Closed-today PnL / trade count (deploy must not wipe).
    day_bucket = load_persisted_day_bucket(et_session_date())
    if day_bucket.get("restored"):
        session.session_date_et = str(day_bucket["session_date_et"])
        session.realized_pnl_today = float(day_bucket["realized_pnl_today"])
        session.trades_today = int(day_bucket["trades_today"])
        logger.info(
            "BOOT restored_day_bucket et_date=%s realized_today=%.2f trades_today=%s",
            session.session_date_et,
            session.realized_pnl_today,
            session.trades_today,
        )
    logger.info(
        "VIRTUE LOOP start equity=%.2f symbol=%s position_tp=$%.0f position_sl=$%.0f "
        "day_lock=$%.0f exit_cooldown=%s long_enter=%.1f short_enter=%.1f "
        "long_exit=%.1f short_exit=%.1f streak=%s anchor_div_atr=%.1f network_timeout=%.1fs",
        session.broker.equity,
        EXECUTION_SYMBOL,
        float(VIRTUE_POSITION_TP_DOLLARS),
        float(VIRTUE_POSITION_STOP_DOLLARS),
        float(GRADE_DAILY_PROFIT_LOCK),
        int(VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES),
        float(VIRTUE_SCORE_LONG_ENTER),
        float(VIRTUE_SCORE_SHORT_ENTER),
        float(VIRTUE_SCORE_LONG_EXIT),
        float(VIRTUE_SCORE_SHORT_EXIT),
        int(VIRTUE_REQUIRED_STREAK),
        float(VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT),
        NETWORK_TIMEOUT_S,
    )

    # Boot reconcile (Webull absolute truth — including equity=0)
    try:
        truth = await session.broker.reconcile_with_broker()
        if truth.ok:
            # Paper reconcile preserves book equity; live uses Webull mark.
            session.risk.update_nav(session.broker.equity)
            session.risk.peak_nav = max(float(session.risk.peak_nav), float(session.broker.equity))
            # Day bucket starts at 0 for this ET date — never import cumulative broker PnL.
            roll_daily_counters_if_needed(session)
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

    # Seed Wisdom from Databento MES (preferred) or Alpaca SPY proxy history
    try:
        await seed_wisdom_from_market(session)
    except Exception as exc:
        logger.exception("boot_seed_failed err=%s", exc)

    session.is_running = True
    await asyncio.to_thread(_publish_ui, session)
    tick_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    poll_s = max(0.5, float(interval_s if interval_s is not None else VIRTUE_TICK_POLL_S))
    logger.info(
        "EVENT_ENGINE start listener+processor+state_writer poll_s=%.1fs "
        "heartbeat_every=%s rebase_cooldown=%s state=data/system_state.json persist_every=%.1fs",
        poll_s,
        int(VIRTUE_HEARTBEAT_EVERY_N_CYCLES),
        int(VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES),
        float(VIRTUE_STATE_PERSIST_INTERVAL_S),
    )

    listener = asyncio.create_task(
        market_tick_listener(session, tick_queue, poll_s=poll_s),
        name="virtue_market_listener",
    )
    processor = asyncio.create_task(
        engine_event_loop(
            session,
            tick_queue,
            cycles=cycles,
            once=once,
            ignore_hours=ignore_hours,
        ),
        name="virtue_engine_processor",
    )
    saver = asyncio.create_task(
        save_state_throttled(session),
        name="virtue_state_writer",
    )

    n = 0
    try:
        # Verified run.py architecture: market events ∥ state writer (Justice off hot path).
        results = await asyncio.gather(listener, processor, saver, return_exceptions=True)
        for label, result in zip(("listener", "processor", "saver"), results):
            if isinstance(result, Exception):
                logger.error("EVENT_ENGINE %s failed: %s", label, result)
        if isinstance(results[1], int):
            n = int(results[1])
        elif not isinstance(results[1], Exception):
            n = int(session.cycle)
    finally:
        session.is_running = False
        for task in (listener, processor, saver):
            if not task.done():
                task.cancel()
        await asyncio.gather(listener, processor, saver, return_exceptions=True)
        try:
            await asyncio.to_thread(_publish_ui, session)
        except Exception as exc:
            logger.error("Justice Layer final write failure (system_state.json): %s", exc)

    logger.info("VIRTUE LOOP done cycles=%s last_action=%s", n, session.last_action)


def main() -> None:
    parser = argparse.ArgumentParser(description="FutureMathics virtue main loop (native Wisdom brain)")
    parser.add_argument("--cycles", type=int, default=None, help="Stop after N cycles")
    parser.add_argument(
        "--interval",
        type=float,
        default=float(VIRTUE_TICK_POLL_S),
        help=f"Market tick poll cadence in seconds (default {VIRTUE_TICK_POLL_S}; event-driven engine)",
    )
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
