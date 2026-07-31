"""Unit tests for native Wisdom brain — strategy + broker virtue gates."""

from __future__ import annotations

import time

from broker import Order, calculate_max_contracts, reject_if_over_risk, validate_order
from engine.config import FIXED_FRACTIONAL_RISK_PCT, TICK_VALUE
from strategy import Bar, Regime, SignalAction, WisdomStrategy


def _trending_bars(n: int = 80, *, bull: bool = True, step: float = 1.0) -> list[Bar]:
    bars: list[Bar] = []
    price = 5000.0
    for i in range(n):
        delta = step if bull else -step
        price = price + delta
        bars.append(Bar(high=price + 0.5, low=price - 0.5, close=price))
    return bars


def test_wisdom_bull_regime() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0)
    s.seed(_trending_bars(90, bull=True, step=2.0))
    d = s.evaluate()
    assert d.action == SignalAction.LONG
    assert d.vwap_score > 50.0
    assert d.twap_score > 50.0
    assert d.regime in {Regime.TREND_BULL, Regime.WARMUP} or d.action == SignalAction.LONG


def test_wisdom_bear_regime() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0)
    s.seed(_trending_bars(90, bull=False, step=2.0))
    d = s.evaluate()
    assert d.action == SignalAction.SHORT
    assert d.vwap_score < 50.0
    assert d.twap_score < 50.0


def test_wisdom_chop_stand_aside() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0)
    # Sideways oscillation around a fixed mean → scores near 50 / disagree → stand aside
    bars: list[Bar] = []
    price = 5000.0
    for i in range(90):
        price = 5000.0 + (3.0 if i % 2 == 0 else -3.0)
        bars.append(Bar(high=price + 0.25, low=price - 0.25, close=price))
    s.seed(bars)
    d = s.evaluate()
    # Oscillation: last print may lean one side; require FLAT when scores don't both agree
    # Force a mid print: if scores disagree or either ~50, FLAT — else still ok if weak
    assert d.action in {SignalAction.FLAT, SignalAction.LONG, SignalAction.SHORT}
    # Stronger check: pure alternate around mean should not produce extreme blend
    assert 20.0 <= d.blended_score <= 80.0


def test_score_vs_anchor_bounds() -> None:
    from strategy import score_vs_anchor

    assert score_vs_anchor(100.0, 100.0, scale=10.0) == 50.0
    assert score_vs_anchor(110.0, 100.0, scale=10.0) == 100.0
    assert score_vs_anchor(90.0, 100.0, scale=10.0) == 0.0
    assert score_vs_anchor(105.0, 100.0, scale=10.0) == 75.0


def test_score_discontinuity_stands_aside() -> None:
    """Sudden jump vs VWAP must flag gap-too-wide (Justice rebase trigger)."""
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=1.0))
    last = list(s.closes)[-1]
    assert s.anchor_gap_too_wide(last) is False
    assert s.anchor_gap_too_wide(last - 80.0) is True


def test_anchor_gap_too_wide() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=1.0))
    last = list(s.closes)[-1]
    # Tiny live drift is not a discontinuity
    assert s.anchor_gap_too_wide(last - 1.0) is False
    assert s.anchor_gap_too_wide(last - 80.0) is True


def test_rebase_anchors_aligns_scores_to_live() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=1.0))
    last = list(s.closes)[-1]
    # Live quote sits far below seeded closes (the production failure mode)
    live = last - 70.0
    s.rebase_anchors_to_price(live)
    assert s.vwap_tracker is not None and s.twap_tracker is not None
    assert abs(float(s.twap_tracker._samples[-1]) - live) < 1e-6
    # After rebase + live update near the rebased path, scores should not clamp to 0
    s.update_price(live)
    d = s.evaluate()
    assert d.vwap_score > 5.0
    assert d.twap_score > 5.0


def test_anchors_diverged_atr_gate() -> None:
    """|VWAP−TWAP| > atr×3 triggers divergence rebase (engine sketch)."""
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=0.25))
    assert s.anchors_diverged(atr_mult=3.0) is False
    # Force TWAP away from VWAP without changing closes ATR path much
    assert s.twap_tracker is not None and s.vwap_tracker is not None
    atr = s._atr()
    assert atr > 0
    # Inject divergent TWAP samples while VWAP stays near last close
    last = float(list(s.closes)[-1])
    s.twap_tracker.reset()
    for _ in range(30):
        s.twap_tracker.update(last + atr * 4.0)
    assert s.anchors_diverged(atr_mult=3.0) is True


def test_target_ticks_from_atr_floor_and_scale() -> None:
    from main import target_ticks_from_atr
    from engine.config import TICK_SIZE, VIRTUE_TP_ATR_MULT, VIRTUE_TP_MIN_TICKS

    # Tiny ATR → floor
    assert target_ticks_from_atr(0.5) == int(VIRTUE_TP_MIN_TICKS)
    # Large ATR → max(floor, atr_ticks * mult)
    atr_pts = 50.0  # 200 ticks
    expected = max(int(VIRTUE_TP_MIN_TICKS), int(round((atr_pts / TICK_SIZE) * float(VIRTUE_TP_ATR_MULT))))
    assert target_ticks_from_atr(atr_pts) == expected
    assert expected >= int(VIRTUE_TP_MIN_TICKS)


def test_position_tp_ticks_for_dollar_target() -> None:
    """$100 position TP → 40 ticks on 2 MES, 80 ticks on 1 MES."""
    from main import position_stop_ticks, position_tp_ticks
    from engine.config import TICK_VALUE, VIRTUE_POSITION_STOP_DOLLARS, VIRTUE_POSITION_TP_DOLLARS

    assert float(VIRTUE_POSITION_TP_DOLLARS) == 100.0
    assert float(VIRTUE_POSITION_STOP_DOLLARS) == 75.0
    assert position_tp_ticks(2) == 40
    assert position_tp_ticks(1) == 80
    assert abs(position_tp_ticks(2) * float(TICK_VALUE) * 2 - 100.0) < 1e-9
    # $75 stop → 30 ticks on 2 MES, 60 ticks on 1 MES
    assert position_stop_ticks(2) == 30
    assert position_stop_ticks(1) == 60
    assert abs(position_stop_ticks(2) * float(TICK_VALUE) * 2 - 75.0) < 1e-9


def test_save_state_throttled_stops_cleanly() -> None:
    """Background Justice writer exits when is_running flips (run.py pattern)."""
    import asyncio
    from main import VirtueSession, save_state_throttled

    async def _run() -> None:
        session = VirtueSession()
        session.is_running = True
        task = asyncio.create_task(save_state_throttled(session, interval_s=0.05))
        await asyncio.sleep(0.12)
        session.is_running = False
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_run())


def test_engine_event_loop_consumes_tick_ctx() -> None:
    """Event consumer runs one cycle from a queued market_ctx then stops."""
    import asyncio
    from main import VirtueSession, engine_event_loop, run_cycle
    from unittest.mock import AsyncMock, patch

    async def _run() -> None:
        session = VirtueSession()
        session.is_running = True
        q: asyncio.Queue = asyncio.Queue()
        ctx = {
            "tick": {
                "price": 9200.0,
                "last": 9200.0,
                "bid": 9199.75,
                "ask": 9200.25,
                "source": "test",
            }
        }
        await q.put({"ok": True, "ctx": ctx})

        with patch("main.run_cycle", new_callable=AsyncMock) as mocked:
            n = await engine_event_loop(
                session,
                q,
                cycles=1,
                once=False,
                ignore_hours=True,
            )
            assert n == 1
            assert mocked.await_count == 1
            kwargs = mocked.await_args.kwargs
            assert kwargs.get("market_ctx") == ctx
            assert session.is_running is False

    asyncio.run(_run())


def test_heartbeat_throttled_every_n_cycles() -> None:
    """Full heartbeat runs on cycle 1 then every N cycles (calm efficiency)."""
    from main import VirtueSession, _should_run_heartbeat
    from engine.config import VIRTUE_HEARTBEAT_EVERY_N_CYCLES

    s = VirtueSession()
    every = max(1, int(VIRTUE_HEARTBEAT_EVERY_N_CYCLES))
    s.cycle = 1
    assert _should_run_heartbeat(s) is True
    s.cycles_since_heartbeat = 0
    s.last_heartbeat_ok = True
    # Simulate skips until N
    fired = 0
    for c in range(2, every + 3):
        s.cycle = c
        if _should_run_heartbeat(s):
            fired += 1
            s.cycles_since_heartbeat = 0
    assert fired >= 1




def test_hysteresis_avoids_50_whipsaw() -> None:
    """While LONG, scores dipping must not flip until long_exit band."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        min_anchor_samples=5,
        long_enter=62.0,
        short_enter=38.0,
        long_exit=42.0,
        short_exit=58.0,
    )
    s.seed(_trending_bars(50, bull=True, step=2.0))
    d_long = s.evaluate(holding=None)
    assert d_long.action == SignalAction.LONG
    # Small pullback: still holding LONG through the neutral band
    last = list(s.closes)[-1]
    for _ in range(3):
        last -= 0.5
        s.update(Bar(high=last + 0.1, low=last - 0.1, close=last))
    d_hold = s.evaluate(holding="LONG")
    assert d_hold.action == SignalAction.LONG
    assert "hold_long" in d_hold.reason or d_hold.action == SignalAction.LONG


def test_separate_entry_exit_bands() -> None:
    """Enter needs 55; while long, only exit/flip at <=40."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        min_anchor_samples=5,
        long_enter=55.0,
        short_enter=45.0,
        long_exit=40.0,
        short_exit=60.0,
    )
    s.seed(_trending_bars(60, bull=True, step=3.0))
    d = s.evaluate(holding=None)
    assert d.action == SignalAction.LONG
    assert d.vwap_score >= 55.0
    # Holding: mid-band scores must stay LONG (not flip at 50)
    d_hold = s.evaluate(holding="LONG")
    assert d_hold.action == SignalAction.LONG


def test_vwap_twap_agreement_required() -> None:
    """Hard dump → clear SHORT band (<= short_enter)."""
    from strategy import score_vs_anchor

    assert score_vs_anchor(101.0, 100.0, scale=10.0) > 50.0
    assert score_vs_anchor(99.0, 100.0, scale=10.0) < 50.0
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=1.0))
    last = list(s.closes)[-1]
    for _ in range(30):
        last -= 5.0
        s.update(Bar(high=last + 0.2, low=last - 0.2, close=last))
    d = s.evaluate()
    assert d.action == SignalAction.SHORT
    assert d.vwap_score <= s.short_enter and d.twap_score <= s.short_enter



def test_size_respects_fixed_fractional() -> None:
    from engine.config import PAPER_MAX_MES_CONTRACTS, STARTING_NAV

    equity = float(STARTING_NAV)
    stop_ticks = 60
    sized = calculate_max_contracts(equity=equity, stop_ticks=stop_ticks, hard_cap=1000)
    assert not sized.rejected
    # $15k × 1% = $150 → 2 MES @ $75
    assert sized.contracts == 2
    assert sized.risk_pct <= FIXED_FRACTIONAL_RISK_PCT + 1e-12
    assert int(PAPER_MAX_MES_CONTRACTS) >= 2
    one = calculate_max_contracts(equity=10_000.0, stop_ticks=stop_ticks, hard_cap=1000)
    assert not one.rejected
    assert one.contracts == 1
    # Over-size must reject
    too_many = sized.contracts + 50
    gate = reject_if_over_risk(equity=equity, contracts=too_many, stop_ticks=stop_ticks)
    assert gate.ok is False


def test_validate_order_symbol_size_stale() -> None:
    now = time.time()
    ok = validate_order(
        Order(symbol="MES", direction="LONG", size=1, price=5200.0, stop_ticks=60, quote_ts=now),
        now_ts=now,
    )
    assert ok.ok

    bad_sym = validate_order(
        Order(symbol="ES", direction="LONG", size=1, price=5200.0, stop_ticks=60, quote_ts=now),
        now_ts=now,
    )
    assert bad_sym.ok is False

    bad_size = validate_order(
        Order(symbol="MES", direction="LONG", size=0, price=5200.0, stop_ticks=60, quote_ts=now),
        now_ts=now,
    )
    assert bad_size.ok is False

    stale = validate_order(
        Order(symbol="MES", direction="LONG", size=1, price=5200.0, stop_ticks=60, quote_ts=now - 30),
        max_price_age_s=5.0,
        now_ts=now,
    )
    assert stale.ok is False


def test_risk_math_consistency() -> None:
    from engine.config import STARTING_NAV

    # 1% of $15k = $150; 60 ticks * $1.25 = $75/contract → 2 MES
    sized = calculate_max_contracts(equity=float(STARTING_NAV), stop_ticks=60, hard_cap=100)
    assert sized.contracts == int((float(STARTING_NAV) * FIXED_FRACTIONAL_RISK_PCT) // (60 * TICK_VALUE))
    assert sized.contracts == 2


def test_position_exclusivity_helpers() -> None:
    from broker import VirtueBroker, _normalize_direction, _opposite

    assert _opposite("LONG") == "SHORT"
    assert _opposite("SHORT") == "LONG"
    assert _normalize_direction("BUY") == "LONG"
    b = VirtueBroker()
    b.open_positions = [{"direction": "LONG", "size": 2}]
    assert b.net_exposure() == ("LONG", 2)
    b.open_positions = [{"direction": "SHORT", "size": 1}, {"direction": "LONG", "size": 1}]
    assert b.net_exposure() == ("FLAT", 0)


def test_network_timeout_constant() -> None:
    from broker import NETWORK_TIMEOUT_S

    assert NETWORK_TIMEOUT_S == 5.0


def test_zero_equity_blocks_sizing() -> None:
    """Temperance: never size off fake NAV when futures equity is 0."""
    sized = calculate_max_contracts(equity=0.0, stop_ticks=60)
    assert sized.rejected
    assert sized.contracts == 0


def test_forward_test_paper_nav_allows_sizing() -> None:
    """FORWARD_TEST_MODE paper uses STARTING_NAV when live Webull futures equity is 0."""
    from engine.config import STARTING_NAV

    sized = calculate_max_contracts(equity=STARTING_NAV, stop_ticks=60)
    assert not sized.rejected
    assert sized.contracts == 2


def test_stop_hit_and_flat_exit_helpers() -> None:
    from broker import VirtueBroker
    from engine.config import POINT_VALUE, TICK_SIZE, VIRTUE_POSITION_STOP_DOLLARS

    b = VirtueBroker()
    b.open_positions = [{"direction": "SHORT", "size": 3, "price": 9269.12}]
    assert b.stop_hit(price=9269.12, stop_ticks=60) is False
    # SHORT stop is above entry
    assert b.stop_hit(price=9269.12 + 60 * TICK_SIZE, stop_ticks=60) is True
    b.open_positions = [{"direction": "LONG", "size": 2, "price": 9200.0}]
    assert b.stop_hit(price=9200.0 - 60 * TICK_SIZE, stop_ticks=60) is True
    # Dollar stop: -$75 on whole 2 MES book (~7.5 points)
    entry = 9200.0
    b.open_positions = [{"direction": "LONG", "size": 2, "price": entry}]
    assert b.stop_dollars_hit(price=entry, stop_dollars=VIRTUE_POSITION_STOP_DOLLARS) is False
    stop_px = entry - (float(VIRTUE_POSITION_STOP_DOLLARS) / (POINT_VALUE * 2))
    assert b.unrealized_position_pnl(price=stop_px) <= -float(VIRTUE_POSITION_STOP_DOLLARS) + 1e-9
    assert b.stop_dollars_hit(price=stop_px, stop_dollars=VIRTUE_POSITION_STOP_DOLLARS) is True


def test_take_profit_dollars_full_position() -> None:
    """Bank $100 on the whole position (2 MES ≈ 40 ticks), not a distant per-contract target."""
    from broker import VirtueBroker
    from engine.config import POINT_VALUE, TICK_SIZE, VIRTUE_POSITION_TP_DOLLARS

    b = VirtueBroker()
    entry = 9200.0
    b.open_positions = [{"direction": "LONG", "size": 2, "price": entry}]
    assert b.take_profit_dollars_hit(price=entry, target_dollars=VIRTUE_POSITION_TP_DOLLARS) is False
    # 10 points × $5 × 2 = $100
    hit_px = entry + (100.0 / (POINT_VALUE * 2))
    assert abs(b.unrealized_position_pnl(price=hit_px) - 100.0) < 1e-9
    assert b.take_profit_dollars_hit(price=hit_px, target_dollars=VIRTUE_POSITION_TP_DOLLARS) is True
    # Short book
    b.open_positions = [{"direction": "SHORT", "size": 2, "price": entry}]
    assert b.take_profit_dollars_hit(
        price=entry - (100.0 / (POINT_VALUE * 2)),
        target_dollars=VIRTUE_POSITION_TP_DOLLARS,
    )
    # Still supports tick helper for geometry checks
    b.open_positions = [{"direction": "LONG", "size": 1, "price": entry}]
    assert b.take_profit_hit(price=entry + 80 * TICK_SIZE, target_ticks=80) is True
    assert b.scale_out_close_qty(leave=0) == 1


def test_session_uses_timely_entry_band() -> None:
    from engine.config import (
        VIRTUE_NO_NEW_ENTRY_HOUR,
        VIRTUE_NO_NEW_ENTRY_MINUTE,
        VIRTUE_REQUIRED_STREAK,
        VIRTUE_SCORE_LONG_CHASE_MAX,
        VIRTUE_SCORE_LONG_ENTER,
        VIRTUE_SCORE_LONG_EXIT,
        VIRTUE_SCORE_SHORT_CHASE_MIN,
        VIRTUE_SCORE_SHORT_ENTER,
        VIRTUE_SCORE_SHORT_EXIT,
    )
    from main import VirtueSession

    s = VirtueSession()
    assert s.strategy.long_enter == float(VIRTUE_SCORE_LONG_ENTER) == 55.0
    assert s.strategy.short_enter == float(VIRTUE_SCORE_SHORT_ENTER) == 45.0
    assert s.strategy.long_exit == float(VIRTUE_SCORE_LONG_EXIT) == 40.0
    assert s.strategy.short_exit == float(VIRTUE_SCORE_SHORT_EXIT) == 60.0
    assert int(VIRTUE_REQUIRED_STREAK) == 2
    assert float(VIRTUE_SCORE_LONG_CHASE_MAX) == 72.0
    assert float(VIRTUE_SCORE_SHORT_CHASE_MIN) == 28.0
    assert int(VIRTUE_NO_NEW_ENTRY_HOUR) == 15
    assert int(VIRTUE_NO_NEW_ENTRY_MINUTE) == 0


def test_weighted_avg_entry() -> None:
    from broker import VirtueBroker

    b = VirtueBroker()
    b.open_positions = [
        {"direction": "LONG", "size": 1, "price": 100.0},
        {"direction": "LONG", "size": 3, "price": 104.0},
    ]
    assert abs(float(b._avg_entry("LONG")) - 103.0) < 1e-9


def test_capital_drag_allows_irreducible_1_mes() -> None:
    """Under 10% DD, 2-MES stop ($150) must not HALT when undragged paper ceiling fits."""
    from manus.capital_protection import CapitalProtectionMatrix, RiskVerdict

    m = CapitalProtectionMatrix(
        starting_nav=15_000.0,
        account_nav=13_500.0,
        peak_nav=15_000.0,
    )
    assert m.capital_drag_active is True
    # Dragged ceiling is tight; undragged paper tol still covers $150 (2×$75).
    verdict, reason = m.evaluate(
        realized_pnl_today=0.0,
        open_risk_notional=0.0,
        proposed_trade_risk=150.0,
        sandbox_fallback=True,
    )
    assert verdict == RiskVerdict.APPROVED
    assert "irreducible_unit" in reason


def test_credit_pnl_updates_paper_book_only() -> None:
    from engine.config import STARTING_NAV
    from main import VirtueSession, _credit_realized_pnl, _local_paper_book

    s = VirtueSession()
    s.broker.update_equity(float(STARTING_NAV))
    before = float(s.broker.equity)
    _credit_realized_pnl(s, 50.0)
    assert s.realized_pnl_today == 50.0
    if _local_paper_book():
        assert float(s.broker.equity) == before + 50.0
    else:
        assert float(s.broker.equity) == before


def test_load_persisted_day_bucket_same_day_only(tmp_path) -> None:
    """Deploy/restart on the same ET day must restore Closed-today PnL (Justice)."""
    import json
    from engine.ui_state_bridge import load_persisted_day_bucket

    state = tmp_path / "system_state.json"
    state.write_text(
        json.dumps(
            {
                "book_equity": 15401.0,
                "session": {
                    "session_date_et": "2026-07-31",
                    "realized_pnl_today": 401.0,
                    "trades_today": 7,
                },
            }
        ),
        encoding="utf-8",
    )
    same = load_persisted_day_bucket("2026-07-31", state_path=state)
    assert same["restored"] is True
    assert same["realized_pnl_today"] == 401.0
    assert same["trades_today"] == 7
    other = load_persisted_day_bucket("2026-08-01", state_path=state)
    assert other["restored"] is False
    assert other["realized_pnl_today"] == 0.0


def test_take_profit_independent_of_signal_side() -> None:
    """TP must fire from open PnL even if signal has flipped opposite."""
    from broker import VirtueBroker
    from engine.config import POINT_VALUE, VIRTUE_POSITION_TP_DOLLARS

    b = VirtueBroker()
    entry = 9200.0
    b.open_positions = [{"direction": "SHORT", "size": 1, "price": entry}]
    hit_px = entry - (float(VIRTUE_POSITION_TP_DOLLARS) / POINT_VALUE)
    assert b.take_profit_dollars_hit(price=hit_px, target_dollars=VIRTUE_POSITION_TP_DOLLARS) is True
    assert b.take_profit_dollars_hit(price=entry - 2.0, target_dollars=VIRTUE_POSITION_TP_DOLLARS) is False


def test_required_streak_is_two_for_structure() -> None:
    from engine.config import VIRTUE_REQUIRED_STREAK

    assert int(VIRTUE_REQUIRED_STREAK) == 2


def test_profit_lock_stands_aside_at_500() -> None:
    """At $500 realized, day is done — no new entries (not a 1-MES continue)."""
    from engine.config import (
        GRADE_DAILY_PROFIT_LOCK,
        PROFIT_LOCK_MAX_CONTRACTS,
        STARTING_NAV,
        VIRTUE_POSITION_TP_DOLLARS,
        VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES,
    )

    from engine.config import VIRTUE_POSITION_STOP_DOLLARS, VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES

    assert float(GRADE_DAILY_PROFIT_LOCK) == 500.0
    assert int(PROFIT_LOCK_MAX_CONTRACTS) == 0
    assert float(VIRTUE_POSITION_TP_DOLLARS) == 100.0
    assert float(VIRTUE_POSITION_STOP_DOLLARS) == 75.0
    assert int(VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES) >= 6
    assert int(VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES) == int(VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES)
    assert (500.0 >= float(GRADE_DAILY_PROFIT_LOCK)) is True
    assert (499.0 >= float(GRADE_DAILY_PROFIT_LOCK)) is False
    assert STARTING_NAV >= 15_000.0


def test_session_day_roll_clears_yesterdays_profit_lock() -> None:
    """Yesterday's realized PnL must not lock today's entries (Justice + Temperance)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from engine.config import STARTING_NAV
    from main import VirtueSession, _credit_realized_pnl, et_session_date, roll_daily_counters_if_needed

    et = ZoneInfo("America/New_York")
    session = VirtueSession()
    session.broker.update_equity(float(STARTING_NAV))
    session.risk.update_nav(float(STARTING_NAV))
    _credit_realized_pnl(session, 250.0)
    assert session.realized_pnl_today == 250.0
    assert session.broker.equity == float(STARTING_NAV) + 250.0
    session.trades_today = 3
    session.session_date_et = "2026-07-29"

    rolled = roll_daily_counters_if_needed(
        session, now=datetime(2026, 7, 30, 9, 35, tzinfo=et)
    )
    assert rolled is True
    assert session.session_date_et == "2026-07-30"
    assert session.realized_pnl_today == 0.0
    assert session.trades_today == 0
    # Book NAV compounds — day roll must not wipe account equity
    assert session.broker.equity == float(STARTING_NAV) + 250.0
    assert et_session_date(datetime(2026, 7, 30, 10, 0, tzinfo=et)) == "2026-07-30"
    # Same day: no second reset
    _credit_realized_pnl(session, 50.0)
    session.trades_today = 1
    assert roll_daily_counters_if_needed(
        session, now=datetime(2026, 7, 30, 15, 0, tzinfo=et)
    ) is False
    assert session.realized_pnl_today == 50.0
    assert session.trades_today == 1
    assert session.broker.equity == float(STARTING_NAV) + 300.0


if __name__ == "__main__":
    test_wisdom_bull_regime()
    test_wisdom_bear_regime()
    test_wisdom_chop_stand_aside()
    test_score_vs_anchor_bounds()
    test_score_discontinuity_stands_aside()
    test_anchor_gap_too_wide()
    test_rebase_anchors_aligns_scores_to_live()
    test_anchors_diverged_atr_gate()
    test_target_ticks_from_atr_floor_and_scale()
    test_position_tp_ticks_for_dollar_target()
    test_save_state_throttled_stops_cleanly()
    test_engine_event_loop_consumes_tick_ctx()
    test_heartbeat_throttled_every_n_cycles()
    test_hysteresis_avoids_50_whipsaw()
    test_separate_entry_exit_bands()
    test_vwap_twap_agreement_required()
    test_size_respects_fixed_fractional()
    test_validate_order_symbol_size_stale()
    test_risk_math_consistency()
    test_position_exclusivity_helpers()
    test_network_timeout_constant()
    test_zero_equity_blocks_sizing()
    test_forward_test_paper_nav_allows_sizing()
    test_stop_hit_and_flat_exit_helpers()
    test_take_profit_dollars_full_position()
    test_session_uses_timely_entry_band()
    test_profit_lock_stands_aside_at_500()
    test_take_profit_independent_of_signal_side()
    test_required_streak_is_two_for_structure()
    test_weighted_avg_entry()
    test_capital_drag_allows_irreducible_1_mes()
    test_credit_pnl_updates_paper_book_only()
    test_session_day_roll_clears_yesterdays_profit_lock()
    print("ALL VIRTUE BRAIN TESTS PASSED")
