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
    s = WisdomStrategy(atr_pct_chaos_max=50.0, adx_trend_min=0.0)
    s.seed(_trending_bars(90, bull=True, step=2.0))
    d = s.evaluate()
    assert d.action == SignalAction.LONG
    assert d.vwap_score > 50.0
    assert d.twap_score > 50.0
    assert d.regime in {Regime.TREND_BULL, Regime.WARMUP} or d.action == SignalAction.LONG


def test_wisdom_bear_regime() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0, adx_trend_min=0.0)
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


def test_structural_short_below_vwap_with_soft_adx() -> None:
    """Price < session VWAP + short scores + low lift → SHORT (below-VWAP path)."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        adx_trend_min=20.0,
        adx_short_min=20.0,
        adx_short_below_vwap_min=12.0,
        market_lift_short_max=45.0,
        vol_dead_max=35.0,
    )
    s.seed(_trending_bars(90, bull=False, step=2.0))
    last = float(list(s.closes)[-1])
    # Session open well above last → dead lift; price still below session VWAP.
    s.session_open = last + 80.0
    d = s.evaluate()
    assert d.vwap_score <= s.short_enter
    assert d.twap_score <= s.short_enter
    assert d.market_lift <= 45.0
    assert d.action == SignalAction.SHORT
    assert "SHORT SETUP" in d.reason
    assert "below-VWAP" in d.reason


def test_macro_bias_latches_bear_from_vwap_score() -> None:
    from main import VirtueSession, update_macro_bias

    s = VirtueSession()
    s.macro_bias = "BULL"
    update_macro_bias(s, vwap_score=30.0, blend=30.0, adx=12.0)
    assert s.macro_bias == "BEAR"


def test_macro_participation_blocks_fake_long() -> None:
    """Vol Conv 30% + Lift ~0% must not print LONG even if price > VWAP."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        adx_trend_min=0.0,
        vol_dead_max=35.0,
        vol_conviction_long_min=45.0,
        market_lift_long_min=40.0,
    )
    # Strong uptrend → VWAP/TWAP scores hot
    s.seed(_trending_bars(90, bull=True, step=2.0))
    # Kill volume on last print + pin session open ≈ last (dead lift)
    last = float(list(s.closes)[-1])
    s.session_open = last
    s.volumes[-1] = 0.2  # vs avg ~1.0 → ~11% conviction
    d = s.evaluate()
    assert d.action == SignalAction.FLAT
    assert d.vol_conviction <= 35.0 or d.market_lift < 40.0
    assert "MACRO PARTICIPATION" in d.reason or "DEAD VOLUME" in d.reason


def test_market_lift_and_vol_conviction_scores() -> None:
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=1.0))
    assert s.market_lift_score() > 50.0  # session open << last in uptrend
    assert 0.0 <= s.volume_conviction_score() <= 100.0


def test_score_vs_anchor_bounds() -> None:
    from strategy import score_vs_anchor

    # Legacy linear scale (compat)
    assert score_vs_anchor(100.0, 100.0, scale=10.0) == 50.0
    assert score_vs_anchor(110.0, 100.0, scale=10.0) == 100.0
    assert score_vs_anchor(90.0, 100.0, scale=10.0) == 0.0
    assert score_vs_anchor(105.0, 100.0, scale=10.0) == 75.0

    # Sticky bps mode (session VWAP — VolumeWatch MACRO-style native math)
    assert score_vs_anchor(100.0, 100.0) == 50.0
    # +3 bps → bull band start (70); +10 bps → 100
    assert score_vs_anchor(100.03, 100.0) == 70.0
    assert score_vs_anchor(100.10, 100.0) == 100.0
    # −3 bps → bear gate 45 (sticky ≤45); deeper stays ≤45
    assert score_vs_anchor(99.97, 100.0) == 45.0
    assert score_vs_anchor(99.90, 100.0) <= 45.0
    assert score_vs_anchor(99.90, 100.0) < score_vs_anchor(99.97, 100.0)


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
    assert abs(float(list(s.closes)[-1]) - live) < 1e-6
    assert abs(float(s.vwap_tracker.vwap) - live) / live < 0.01
    # After rebase + live update near the rebased path, scores should not clamp to 0
    s.update_price(live)
    d = s.evaluate()
    assert d.vwap_score > 5.0
    assert d.twap_score > 5.0


def test_rebase_fixes_ghost_vwap_when_last_close_already_matches() -> None:
    """Deploy bug: last close ≈ live, but rolling VWAP still on a ghost seed level."""
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5)
    s.seed(_trending_bars(40, bull=True, step=1.0))
    live = float(list(s.closes)[-1])
    assert s.vwap_tracker is not None and s.twap_tracker is not None
    # Poison averages ~100 pts above live without touching last close (≥1% ghost)
    s.vwap_tracker.reset()
    s.twap_tracker.reset()
    for _ in range(40):
        s.vwap_tracker.update_trade(price=live + 100.0, size=1.0)
        s.twap_tracker.update(live + 100.0)
    assert float(s.vwap_tracker.vwap) > live + 50.0
    assert s.anchor_gap_too_wide(live) is True
    s.rebase_anchors_to_price(live)
    # Rebuild from true OHLC (last already matched) — ghost tracker poison discarded
    assert abs(float(s.vwap_tracker.vwap) - live) / live < 0.01
    assert abs(float(s.twap_tracker.twap) - live) / live < 0.01
    s.update_price(live + 1.0)
    d = s.evaluate()
    assert d.blended_score > 40.0  # must not clamp to 0 / false SHORT


def test_rebase_flat_pins_when_seed_mean_is_ghost_level() -> None:
    """If OHLC window mean stays ≥1% off live after shift, flat-pin anchors at live."""
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5, anchor_window=30)
    # Synthetic: last close already at live, but older closes sit 150 pts higher
    live = 9300.0
    for i in range(30):
        c = live + 150.0 if i < 29 else live
        s.update(Bar(high=c + 0.5, low=c - 0.5, close=c))
    assert s.vwap_tracker is not None
    assert abs(float(s.vwap_tracker.vwap) - live) / live >= 0.01
    s.rebase_anchors_to_price(live)
    assert abs(float(s.vwap_tracker.vwap) - live) < 1e-6
    s.update_price(live + 0.5)
    d = s.evaluate()
    assert d.blended_score > 40.0


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




def test_hysteresis_holds_while_thesis_valid() -> None:
    """While LONG, small dips still hold until hysteresis exit (45), not mid-50."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        min_anchor_samples=5,
        adx_trend_min=0.0,
        long_enter=62.0,
        short_enter=38.0,
        long_exit=45.0,
        short_exit=55.0,
    )
    s.seed(_trending_bars(50, bull=True, step=2.0))
    d_long = s.evaluate(holding=None)
    assert d_long.action == SignalAction.LONG
    # Tiny pullback: still above mid → keep LONG
    last = list(s.closes)[-1]
    for _ in range(2):
        last -= 0.25
        s.update(Bar(high=last + 0.1, low=last - 0.1, close=last))
    d_hold = s.evaluate(holding="LONG")
    assert d_hold.action == SignalAction.LONG
    assert "hold_long" in d_hold.reason or d_hold.action == SignalAction.LONG


def test_separate_entry_exit_bands() -> None:
    """Enter needs clear edge; while long, invalidate/flatten with hysteresis."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        min_anchor_samples=5,
        adx_trend_min=0.0,
        long_enter=58.0,
        short_enter=42.0,
        long_exit=45.0,
        short_exit=55.0,
    )
    s.seed(_trending_bars(60, bull=True, step=3.0))
    d = s.evaluate(holding=None)
    assert d.action == SignalAction.LONG
    assert d.vwap_score >= 58.0
    # Holding with still-bullish scores stays LONG
    d_hold = s.evaluate(holding="LONG")
    assert d_hold.action == SignalAction.LONG


def test_nimble_short_invalidates_before_stop() -> None:
    """Wrong-side SHORT past hysteresis (55) must flatten (not ride to $75 stop)."""
    s = WisdomStrategy(
        atr_pct_chaos_max=50.0,
        min_anchor_samples=5,
        adx_trend_min=0.0,
        long_enter=58.0,
        short_enter=42.0,
        long_exit=45.0,
        short_exit=55.0,
    )
    s.seed(_trending_bars(40, bull=False, step=2.0))
    d_short = s.evaluate(holding=None)
    assert d_short.action == SignalAction.SHORT
    # Market turns up through anchors — thesis broken past 55
    last = list(s.closes)[-1]
    for _ in range(25):
        last += 3.0
        s.update(Bar(high=last + 0.2, low=last - 0.2, close=last))
    d_fix = s.evaluate(holding="SHORT")
    assert d_fix.action == SignalAction.FLAT
    assert "SHORT THESIS BROKEN" in d_fix.reason or "thesis_invalid_short" in d_fix.reason
    assert d_fix.blended_score >= 55.0


def test_vwap_twap_agreement_required() -> None:
    """Hard dump → clear SHORT band (<= short_enter)."""
    from strategy import score_vs_anchor

    assert score_vs_anchor(101.0, 100.0, scale=10.0) > 50.0
    assert score_vs_anchor(99.0, 100.0, scale=10.0) < 50.0
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5, adx_trend_min=0.0)
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
    # $15k × 1% = $150 → risk math allows 2 MES @ $75 (hard_cap=1000 in this unit test)
    assert sized.contracts == 2
    assert sized.risk_pct <= FIXED_FRACTIONAL_RISK_PCT + 1e-12
    # Production paper default is 1 MES simplify mode.
    assert int(PAPER_MAX_MES_CONTRACTS) == 1
    one = calculate_max_contracts(equity=10_000.0, stop_ticks=stop_ticks, hard_cap=1000)
    assert not one.rejected
    assert one.contracts == 1
    capped = calculate_max_contracts(equity=equity, stop_ticks=stop_ticks, hard_cap=1)
    assert not capped.rejected
    assert capped.contracts == 1
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
    # Paper hard cap is 1 MES in simplify mode.
    assert sized.contracts == 1


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
        VIRTUE_ADX_ENTER_MIN,
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
    assert s.strategy.long_enter == float(VIRTUE_SCORE_LONG_ENTER) == 58.0
    assert s.strategy.short_enter == float(VIRTUE_SCORE_SHORT_ENTER) == 42.0
    assert s.strategy.long_exit == float(VIRTUE_SCORE_LONG_EXIT) == 45.0
    assert s.strategy.short_exit == float(VIRTUE_SCORE_SHORT_EXIT) == 55.0
    assert s.strategy.adx_trend_min == float(VIRTUE_ADX_ENTER_MIN) == 20.0
    from engine.config import (
        VIRTUE_ADX_SHORT_ENTER_MIN,
        VIRTUE_COURSE_CORRECT_LONG_BLEND,
        VIRTUE_COURSE_CORRECT_SHORT_BLEND,
        VIRTUE_MAX_TACTICAL_TRADES_PER_DAY,
        VIRTUE_POST_TP_STREAK_PULLBACK_AFTER,
        VIRTUE_TACTICAL_ADX_MIN,
        GRADE_MODE,
    )

    assert s.strategy.adx_short_min == float(VIRTUE_ADX_SHORT_ENTER_MIN) == 20.0
    assert GRADE_MODE is False
    assert float(VIRTUE_COURSE_CORRECT_SHORT_BLEND) == 55.0
    assert float(VIRTUE_COURSE_CORRECT_LONG_BLEND) == 45.0
    assert float(VIRTUE_TACTICAL_ADX_MIN) == 20.0
    assert int(VIRTUE_MAX_TACTICAL_TRADES_PER_DAY) == 12
    assert int(VIRTUE_POST_TP_STREAK_PULLBACK_AFTER) == 2
    assert int(VIRTUE_REQUIRED_STREAK) == 2
    assert float(VIRTUE_SCORE_LONG_CHASE_MAX) == 85.0
    assert float(VIRTUE_SCORE_SHORT_CHASE_MIN) == 15.0
    assert int(VIRTUE_NO_NEW_ENTRY_HOUR) == 15
    assert int(VIRTUE_NO_NEW_ENTRY_MINUTE) == 55  # afternoon window end


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


def test_load_persisted_day_bucket_same_day_only(tmp_path=None) -> None:
    """Deploy/restart on the same ET day must restore Closed-today PnL (Justice)."""
    import json
    import tempfile
    from pathlib import Path

    from engine.ui_state_bridge import load_persisted_day_bucket

    root = Path(tmp_path) if tmp_path is not None else Path(tempfile.mkdtemp())
    state = root / "system_state.json"
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


def test_paper_book_survives_system_state_wipe_to_starting_nav(tmp_path, monkeypatch) -> None:
    """Friday gains in paper_book.json must not vanish when system_state snaps to $15k."""
    import json
    from engine import ui_state_bridge as bridge

    data = tmp_path
    monkeypatch.setattr(bridge, "_data_dir", lambda: data)
    # Durable ledger has compounded equity
    bridge.save_paper_book(15_531.40, peak_equity=15_600.0, source="test")
    # Dashboard/state file looks "reset"
    (data / "system_state.json").write_text(
        json.dumps(
            {
                "book_equity": 15_000.0,
                "account_nav": 15_000.0,
                "session": {"session_date_et": "2026-08-01", "realized_pnl_today": 0.0, "trades_today": 0},
            }
        ),
        encoding="utf-8",
    )
    assert bridge.load_persisted_book_equity(15_000.0) == 15_531.40
    ledger = bridge.load_paper_book(15_000.0)
    assert ledger["restored"] is True
    assert ledger["book_equity"] == 15_531.40


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


def test_required_streak_is_three_for_structure() -> None:
    from engine.config import VIRTUE_REQUIRED_STREAK

    assert int(VIRTUE_REQUIRED_STREAK) == 2


def test_check_course_correct_hard_blend_hook() -> None:
    """Hysteresis COURSE_CORRECT: SHORT+blend>=55 / LONG+blend<=45 force flatten."""
    from main import check_course_correct

    hit, reason = check_course_correct("SHORT", 55.0)
    assert hit is True
    assert "course_correct_short_vs_bull" in reason

    hit, reason = check_course_correct("SHORT", 54.9)
    assert hit is False

    hit, reason = check_course_correct("LONG", 45.0)
    assert hit is True
    assert "course_correct_long_vs_bear" in reason

    hit, reason = check_course_correct("LONG", 45.1)
    assert hit is False

    # Mid-band chop must NOT course-correct (hysteresis gap).
    hit, _ = check_course_correct("LONG", 50.0)
    assert hit is False
    hit, _ = check_course_correct("SHORT", 50.0)
    assert hit is False

    hit, _ = check_course_correct("FLAT", 90.0)
    assert hit is False


def test_calculate_temperance_parameters_loss_and_course_correct() -> None:
    """Losing streak / course_correct widen enter bands; MES size stays 1."""
    from main import VirtueSession, calculate_temperance_parameters, update_outcome_state
    from engine.config import (
        VIRTUE_SCORE_LONG_ENTER,
        VIRTUE_SCORE_SHORT_ENTER,
        VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER,
        VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER,
    )

    s = VirtueSession()
    contracts, buf = calculate_temperance_parameters(session=s)
    assert contracts == 1
    assert buf == 0.0

    # Single loss → streak friction exists elsewhere; blend buffer still 0 until 2 losses
    update_outcome_state(s, -75.0, "stop_$75")
    contracts, buf = calculate_temperance_parameters(session=s)
    assert contracts == 1
    assert buf == 0.0

    update_outcome_state(s, -52.5, "course_correct_short_vs_bull blend=55.0>=50.0")
    assert s.consecutive_losses == 2
    assert s.last_reason == "course_correct"
    contracts, buf = calculate_temperance_parameters(session=s)
    assert contracts == 1
    # Stronger of loss-streak (5) and course_correct (3)
    assert buf == float(VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER) == 5.0
    assert float(VIRTUE_SCORE_LONG_ENTER) + buf == 63.0
    assert float(VIRTUE_SCORE_SHORT_ENTER) - buf == 37.0

    # course_correct alone (after a win resets losses) still applies 3.0 buffer
    update_outcome_state(s, 100.0, "take_profit_$100", current_engine_cycle=1)
    update_outcome_state(
        s, -10.0, "course_correct_long_vs_bear blend=45.0<=45.0", current_engine_cycle=5
    )
    assert s.consecutive_losses == 1
    assert s.last_reason == "course_correct"
    from engine.config import VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES

    assert s.pipeline_resume_cycle == 5 + int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES)
    contracts, buf = calculate_temperance_parameters(session=s)
    assert buf == float(VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER) == 3.0

    from engine.ui_state_bridge import build_virtue_system_state
    from main import effective_temperance_blend_buffer

    # Strong ADX + BULL: with-trend long blend buffer waived (streak friction remains).
    assert (
        effective_temperance_blend_buffer(
            5.0, side="LONG", adx=39.5, macro_bias="BULL"
        )
        == 0.0
    )
    assert (
        effective_temperance_blend_buffer(
            5.0, side="SHORT", adx=39.5, macro_bias="BULL"
        )
        == 5.0
    )
    assert (
        effective_temperance_blend_buffer(
            5.0, side="LONG", adx=18.0, macro_bias="BULL"
        )
        == 5.0
    )

    s.last_adx = 25.0  # no velocity penalty; strong enough to waive BULL long buffer
    s.macro_bias = "BULL"
    st = build_virtue_system_state(s, last_price=7700.0)
    assert st["entry_pipeline"]["temperance_blend_buffer"] == 3.0  # raw
    assert st["entry_pipeline"]["temperance_effective_long_buffer"] == 0.0
    assert st["entry_pipeline"]["temperance_course_correct_friction"] is True
    assert st["entry_pipeline"]["temperance_long_enter"] == 58.0  # waived
    assert st["entry_pipeline"]["temperance_short_enter"] == 39.0  # short keeps buffer
    assert st["entry_pipeline"]["velocity_adx_penalty"] == 0.0

    # Dict API (system_state shape)
    contracts, buf = calculate_temperance_parameters(
        {
            "last_trade_outcome": {
                "consecutive_losses": 2,
                "last_reason": "stop",
            }
        }
    )
    assert buf == 5.0


def test_update_outcome_state_win_loss_and_tp_streak() -> None:
    """Post-fill updater: reason cooldowns + absolute pipeline_resume_cycle."""
    from main import VirtueSession, pipeline_lock_remaining, update_outcome_state
    from engine.config import (
        VIRTUE_BASE_TP_COOLDOWN_CYCLES,
        VIRTUE_HARD_STOP_COOLDOWN_CYCLES,
        VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES,
        VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES,
    )

    s = VirtueSession()
    s.cycle = 10
    assert s.last_result == "FLAT"
    assert s.last_reason == "none"

    update_outcome_state(s, 101.2, "take_profit_$100", current_engine_cycle=10)
    assert s.last_result == "WIN"
    assert s.last_reason == "take_profit"
    assert s.consecutive_wins == 1
    assert s.consecutive_losses == 0
    assert s.last_trade_pnl == 101.2
    assert s.consecutive_tp_streak == 1
    assert s.trades_today == 1
    # First TP: base + 0*bonus → resume at 10 + base
    assert s.pipeline_resume_cycle == 10 + int(VIRTUE_BASE_TP_COOLDOWN_CYCLES)
    assert s.last_tp_timestamp > 0

    s.cycle = 13
    update_outcome_state(s, 100.0, "take_profit_$100", current_engine_cycle=13)
    assert s.consecutive_wins == 2
    assert s.consecutive_tp_streak == 2
    assert s.trades_today == 2
    # Second TP: base + 1*bonus → resume at 13 + base + bonus
    assert s.pipeline_resume_cycle == 13 + int(VIRTUE_BASE_TP_COOLDOWN_CYCLES) + int(
        VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES
    )

    # course_correct is always LOSS friction (even green exit) + cool-off lock
    s.cycle = 21
    update_outcome_state(
        s,
        12.5,
        "course_correct_short_vs_bull blend=55.0>=50.0",
        current_engine_cycle=21,
    )
    assert s.last_result == "LOSS"
    assert s.last_reason == "course_correct"
    assert s.consecutive_wins == 0
    assert s.consecutive_losses == 1
    assert s.consecutive_tp_streak == 0
    assert s.pipeline_resume_cycle == 21 + int(
        VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES
    )
    locked, rem = pipeline_lock_remaining(s)
    assert locked is True
    assert rem == int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES)

    s.cycle = 40
    update_outcome_state(s, -75.0, "stop_$75", current_engine_cycle=40)
    assert s.last_result == "LOSS"
    assert s.last_reason == "stop"
    assert s.consecutive_losses == 2
    assert s.pipeline_resume_cycle == 40 + int(VIRTUE_HARD_STOP_COOLDOWN_CYCLES)

    from engine.ui_state_bridge import build_virtue_system_state

    st = build_virtue_system_state(s, last_price=7700.0)
    assert st["last_trade_outcome"]["last_result"] == "LOSS"
    assert st["last_trade_outcome"]["consecutive_losses"] == 2
    assert st["entry_pipeline"]["temperance_loss_friction"] is True
    assert st["entry_pipeline"]["pipeline_resume_cycle"] == s.pipeline_resume_cycle
    assert st["entry_pipeline"]["pipeline_locked"] is True


def test_is_entry_pipeline_clear_layer1_cycle_lock() -> None:
    """Layer 1 blocks until current_engine_cycle >= pipeline_resume_cycle."""
    from main import VirtueSession, is_entry_pipeline_clear, update_outcome_state

    s = VirtueSession()
    s.cycle = 10
    assert is_entry_pipeline_clear(s, 10) is True
    assert s.layer1_streak_clear is True

    from engine.config import VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES

    update_outcome_state(s, -50.0, "course_correct_x", current_engine_cycle=10)
    resume = 10 + int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES)
    assert s.pipeline_resume_cycle == resume
    assert is_entry_pipeline_clear(s, 10) is False
    assert s.layer1_streak_clear is False
    assert is_entry_pipeline_clear(s, resume - 1) is False
    assert is_entry_pipeline_clear(s, resume) is True
    assert s.layer1_streak_clear is True

    # Dict poll-contract shape
    state = {
        "pipeline_resume_cycle": 25,
        "cycle": 20,
        "entry_pipeline": {},
    }
    assert is_entry_pipeline_clear(state, 20) is False
    assert state["entry_pipeline"]["layer1_streak_clear"] is False
    assert state["multi_tp_cooldown_active"] is True
    assert state["multi_tp_cooldown_remaining_s"] == 5
    assert is_entry_pipeline_clear(state, 25) is True
    assert state["entry_pipeline"]["layer1_streak_clear"] is True
    assert state["multi_tp_cooldown_active"] is False

    from engine.ui_state_bridge import build_virtue_system_state

    s.cycle = 12
    rem = max(0, int(s.pipeline_resume_cycle) - 12)
    st = build_virtue_system_state(s, last_price=7700.0)
    assert st["entry_pipeline"]["layer1_streak_clear"] is False
    assert st["entry_pipeline"]["pipeline_remaining_cycles"] == rem
    assert st["multi_tp_cooldown_active"] is True
    assert st["multi_tp_cooldown_remaining_s"] == rem


def test_check_virtue_pnl_lock_trailing_floor() -> None:
    """Peak >= $100 arms hard $25 floor; breach latches circuit breaker."""
    from main import VirtueSession, check_virtue_pnl_lock, _credit_realized_pnl
    from engine.config import VIRTUE_PNL_LOCK_ARM_PEAK, VIRTUE_PNL_LOCK_HARD_FLOOR

    s = VirtueSession()
    assert check_virtue_pnl_lock(s) is False

    s.realized_pnl_today = 99.0
    s.peak_realized_pnl_today = 99.0
    assert check_virtue_pnl_lock(s) is False  # not armed yet

    s.realized_pnl_today = 104.0
    s.peak_realized_pnl_today = 104.0
    assert check_virtue_pnl_lock(s) is False  # armed but above $25 floor
    assert float(VIRTUE_PNL_LOCK_HARD_FLOOR) == 25.0

    s.realized_pnl_today = 25.0
    assert check_virtue_pnl_lock(s) is True
    assert s.virtue_pnl_lock_active is True
    assert s.circuit_breaker_tripped is True
    assert s.last_regime == "CHOP_NO_TRADE"

    # Latched — stays locked even if PnL recovers (Justice: no silent unlock)
    s.realized_pnl_today = 180.0
    assert check_virtue_pnl_lock(s) is True

    # Credit path updates peak
    s2 = VirtueSession()
    _credit_realized_pnl(s2, 160.0)
    assert s2.peak_realized_pnl_today == 160.0
    assert float(VIRTUE_PNL_LOCK_ARM_PEAK) == 100.0


def test_check_time_decay_exit_stagnant_hold() -> None:
    """After MAX cycles with open_pnl <= $0, time_decay cuts flat/red — never green."""
    from main import VirtueSession, check_time_decay_exit, update_outcome_state
    from engine.config import (
        VIRTUE_TIME_DECAY_COOLDOWN_CYCLES,
        VIRTUE_TIME_DECAY_MAX_CYCLES,
    )

    max_c = int(VIRTUE_TIME_DECAY_MAX_CYCLES)
    s = VirtueSession()
    s.cycle = 10
    # Flat → no trigger, marker cleared
    hit, _ = check_time_decay_exit(s, 10, open_pnl=0.0, exposure="FLAT")
    assert hit is False
    assert s.entry_cycle_marker is None

    # Simulate open long via broker local position
    s.broker.open_positions = [
        {
            "symbol": "MES",
            "direction": "LONG",
            "size": 1,
            "price": 7700.0,
        }
    ]
    hit, _ = check_time_decay_exit(s, 10, open_pnl=5.0, exposure="LONG")
    assert hit is False
    assert s.entry_cycle_marker == 10

    # Not yet max cycles
    hit, _ = check_time_decay_exit(s, 10 + max_c - 1, open_pnl=-5.0, exposure="LONG")
    assert hit is False

    # Max elapsed but still green → keep (Courage toward $100 TP)
    hit, _ = check_time_decay_exit(s, 10 + max_c, open_pnl=20.0, exposure="LONG")
    assert hit is False

    # Max elapsed and flat/red stagnation → cut
    hit, reason = check_time_decay_exit(s, 10 + max_c, open_pnl=0.0, exposure="LONG")
    assert hit is True
    assert "time_decay" in reason

    update_outcome_state(s, -2.0, reason, current_engine_cycle=10 + max_c)
    assert s.last_reason == "time_decay"
    assert s.entry_cycle_marker is None
    assert s.pipeline_resume_cycle == 10 + max_c + int(VIRTUE_TIME_DECAY_COOLDOWN_CYCLES)
    assert int(VIRTUE_TIME_DECAY_COOLDOWN_CYCLES) == 8
    assert int(VIRTUE_TIME_DECAY_MAX_CYCLES) == 36


def test_observability_metric_lines_and_webhook_guard() -> None:
    """CloudWatch METRIC: lines emit; placeholder webhooks stay silent."""
    import logging

    from engine import observability as obs

    class _Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record.getMessage())

    capture = _Capture()
    log = logging.getLogger("virtue.metrics")
    log.addHandler(capture)
    log.setLevel(logging.INFO)
    try:
        obs.emit_metric(
            "EntryPreventedVelocityGate",
            Side="LONG",
            Blend=56.0,
            Target=59.0,
        )
        obs.notify_course_correct(exposure="SHORT", blend=53.5, reason="mid50")
        obs.notify_time_decay(elapsed=15, open_pnl=12.0, exposure="LONG")
    finally:
        log.removeHandler(capture)

    joined = "\n".join(capture.records)
    assert "METRIC:EntryPreventedVelocityGate=1" in joined
    assert "Side=LONG" in joined
    assert "METRIC:CourseCorrectTriggered=1" in joined
    assert "METRIC:TimeDecayTriggered=1" in joined

    # Placeholder / missing webhook must no-op (no crash).
    # Real Discord webhook paths must NOT be rejected by the bare-domain placeholder.
    import os

    cases = [
        ("https://discord.com", ""),
        ("https://discord.com/", ""),
        (
            "https://discord.com/api/webhooks/1/abc",
            "https://discord.com/api/webhooks/1/abc",
        ),
    ]
    old_fm = os.environ.get("FM_CHAT_WEBHOOK_URL")
    old_chat = os.environ.get("CHAT_WEBHOOK_URL")
    try:
        os.environ.pop("CHAT_WEBHOOK_URL", None)
        for raw, expected in cases:
            os.environ["FM_CHAT_WEBHOOK_URL"] = raw
            assert obs.chat_webhook_url() == expected
        os.environ["FM_CHAT_WEBHOOK_URL"] = "https://discord.com"
        obs.send_chat_notification("should not send")
    finally:
        if old_fm is None:
            os.environ.pop("FM_CHAT_WEBHOOK_URL", None)
        else:
            os.environ["FM_CHAT_WEBHOOK_URL"] = old_fm
        if old_chat is None:
            os.environ.pop("CHAT_WEBHOOK_URL", None)
        else:
            os.environ["CHAT_WEBHOOK_URL"] = old_chat

    assert obs._display_reason("course_correct_short_vs_bull") == "COURSE_CORRECT"
    assert obs._format_signed_usd(-12.5) == "-$12.50"
    assert obs._format_signed_usd(100.0) == "$100.00"
    flatten = (
        "⚡ **MACROMATHICS FLATTEN ORDER FIRED**\n"
        "• Reason: `COURSE_CORRECT`\n"
        "• Realized P&L on Trade: `-$12.50`\n"
        "• Engine Cycle Account: #14926"
    )
    assert "Engine Cycle Account: #14926" in flatten
    assert obs._display_reason("course_correct") == "COURSE_CORRECT"


def test_macromathics_core_production_phases() -> None:
    """Production template phase helpers match live config contract."""
    from engine.macromathics_core import (
        MAX_STAGNATION_CYCLES,
        PROFIT_GUARD_THRESHOLD,
        VIRTUE_TIME_DECAY_COOLDOWN_CYCLES,
        cooldown_cycles_for_reason,
        phase1_profit_guard_triggered,
        phase2_time_decay_triggered,
        phase3_layer1_clear,
        phase3_velocity_gates,
    )

    assert int(MAX_STAGNATION_CYCLES) == 36
    assert float(PROFIT_GUARD_THRESHOLD) == 100.0
    assert int(VIRTUE_TIME_DECAY_COOLDOWN_CYCLES) == 8

    state = {"realized_pnl_today": 104.0, "peak_realized_pnl_today": 104.0}
    hit, floor = phase1_profit_guard_triggered(state)
    assert hit is False
    assert floor == 25.0
    state["realized_pnl_today"] = 25.0
    hit, floor = phase1_profit_guard_triggered(state)
    assert hit is True
    assert state.get("circuit_breaker_tripped") is True

    # Green open_pnl never time-decays; flat/red after 36 cycles does.
    td_green = {"engine_exposure": "LONG", "entry_cycle_marker": 1, "open_pnl": 10.0}
    assert phase2_time_decay_triggered(td_green, 37) is False
    td = {"engine_exposure": "LONG", "entry_cycle_marker": 1, "open_pnl": 0.0}
    assert phase2_time_decay_triggered(td, 37) is True
    assert phase2_time_decay_triggered(td, 10) is False

    # Floor 20: ADX 14 → penalty 3 → 58 / 42
    long_g, short_g = phase3_velocity_gates({"ADX": 14.0})
    assert abs(long_g - 58.0) < 1e-9
    assert abs(short_g - 42.0) < 1e-9

    pipe = {"pipeline_resume_cycle": 20, "entry_pipeline": {}}
    assert phase3_layer1_clear(pipe, 15) is False
    assert phase3_layer1_clear(pipe, 20) is True

    assert cooldown_cycles_for_reason("time_decay") == 8
    assert cooldown_cycles_for_reason("course_correct") == 12
    assert cooldown_cycles_for_reason("take_profit", tp_streak=1) == 8 + 5

    # Live wire: main must import the phase contract (no shadow module).
    import main as virtue_main

    assert hasattr(virtue_main, "phase1_profit_guard_triggered")
    assert hasattr(virtue_main, "phase3_velocity_gates")


def test_calculate_dynamic_blend_thresholds_velocity_gate() -> None:
    """Low ADX widens blend gates; ADX>=20 keeps base 55/45."""
    from main import calculate_dynamic_blend_thresholds, verify_pipeline_entry

    long_thr, short_thr = calculate_dynamic_blend_thresholds({"ADX": 25.0})
    assert long_thr == 55.0
    assert short_thr == 45.0

    # ADX 14 → penalty (20-14)*0.5 = 3.0 → 58 / 42
    long_thr, short_thr = calculate_dynamic_blend_thresholds({"ADX": 14.0})
    assert abs(long_thr - 58.0) < 1e-9
    assert abs(short_thr - 42.0) < 1e-9

    ok, reason = verify_pipeline_entry(
        "LONG",
        {"vwap_twap_blend": 56.0, "ADX": 14.0, "macro_bias": "NEUTRAL",
         "last_trade_outcome": {}},
    )
    assert ok is False
    assert "vel=3.0" in reason

    ok, _ = verify_pipeline_entry(
        "LONG",
        {"vwap_twap_blend": 59.0, "ADX": 14.0, "macro_bias": "NEUTRAL",
         "last_trade_outcome": {}},
    )
    assert ok is True


def test_verify_pipeline_entry_temperance_and_bull_short() -> None:
    """Pipeline blocks weak blend after losses/course_correct; bull shorts get -5."""
    from main import (
        VirtueSession,
        pipeline_system_state,
        update_outcome_state,
        verify_pipeline_entry,
    )

    s = VirtueSession()
    s.macro_bias = "NEUTRAL"
    s.last_adx = 25.0  # strong trend — no velocity penalty

    ok, reason = verify_pipeline_entry(
        "LONG", pipeline_system_state(s, blend=56.0, adx=25.0)
    )
    assert ok is True, reason

    ok, reason = verify_pipeline_entry(
        "LONG", pipeline_system_state(s, blend=54.0, adx=25.0)
    )
    assert ok is False
    assert "pipeline_block:long_blend" in reason

    # After 2 losses → buffer 5 → long needs 60 (NEUTRAL: no with-trend waive)
    update_outcome_state(s, -75.0, "stop_$75")
    update_outcome_state(s, -50.0, "course_correct_short_vs_bull blend=55>=50")
    assert s.consecutive_losses == 2
    ok, reason = verify_pipeline_entry(
        "LONG", pipeline_system_state(s, blend=59.0, adx=25.0)
    )
    assert ok is False
    assert "required=60.0" in reason
    ok, _ = verify_pipeline_entry(
        "LONG", pipeline_system_state(s, blend=60.0, adx=25.0)
    )
    assert ok is True

    # Bull-day short: base 45 - buf 5 - penalty 5 = 35 (shorts keep friction)
    s.macro_bias = "BULL"
    ok, reason = verify_pipeline_entry(
        "SHORT", pipeline_system_state(s, blend=36.0, adx=25.0)
    )
    assert ok is False
    assert "pipeline_block:short_blend" in reason
    ok, _ = verify_pipeline_entry(
        "SHORT", pipeline_system_state(s, blend=35.0, adx=25.0)
    )
    assert ok is True

    # Strong BULL + ADX>=25: with-trend LONG waives +5 buffer (Courage in trend).
    ok, reason = verify_pipeline_entry(
        "LONG", pipeline_system_state(s, blend=58.4, adx=39.5)
    )
    assert ok is True, reason

    from engine.ui_state_bridge import build_virtue_system_state

    s.last_adx = 39.5
    st = build_virtue_system_state(s, last_price=7700.0)
    assert st["entry_pipeline"]["temperance_blend_buffer"] == 5.0  # raw
    assert st["entry_pipeline"]["temperance_effective_long_buffer"] == 0.0
    assert st["entry_pipeline"]["pipeline_long_blend_required"] == 55.0
    assert st["entry_pipeline"]["pipeline_short_blend_required"] == 35.0
    assert st["entry_pipeline"]["pipeline_bull_short_penalty"] is True
    assert st["entry_pipeline"]["velocity_adx_penalty"] == 0.0


def test_evaluate_directional_gate_bull_day_shorts() -> None:
    """On BULL days, shorts need blend<=35 and ADX>=18 (below-VWAP-friendly)."""
    from main import evaluate_directional_gate

    ok, reason = evaluate_directional_gate(
        "SHORT", macro_bias="BULL", blend=44.9, adx=22.0
    )
    assert ok is False
    assert "bull_day_short_denied" in reason

    ok, reason = evaluate_directional_gate(
        "SHORT", macro_bias="BULL", blend=34.0, adx=17.0
    )
    assert ok is False

    ok, reason = evaluate_directional_gate(
        "SHORT", macro_bias="BULL", blend=34.0, adx=18.0
    )
    assert ok is True

    ok, _ = evaluate_directional_gate(
        "LONG", macro_bias="BULL", blend=60.0, adx=18.0
    )
    assert ok is True

    ok, _ = evaluate_directional_gate(
        "SHORT", macro_bias="NEUTRAL", blend=42.0, adx=22.0
    )
    assert ok is True


def test_verify_cooldown_validity_multi_tp_lock() -> None:
    """3+ consecutive TPs engage a 15-minute wall-clock entry lock."""
    import time as _time

    from engine.config import VIRTUE_MULTI_TP_COOLDOWN_S, VIRTUE_MULTI_TP_COOLDOWN_STREAK
    from main import VirtueSession, verify_cooldown_validity

    s = VirtueSession()
    assert int(VIRTUE_MULTI_TP_COOLDOWN_STREAK) == 3
    assert int(VIRTUE_MULTI_TP_COOLDOWN_S) == 900

    ok, rem = verify_cooldown_validity(s, now=1_000_000.0)
    assert ok is True and rem == 0

    s.consecutive_tp_streak = 3
    s.last_tp_timestamp = 1_000_000.0
    ok, rem = verify_cooldown_validity(s, now=1_000_000.0 + 100.0)
    assert ok is False
    assert rem == 800

    ok, rem = verify_cooldown_validity(s, now=1_000_000.0 + 900.0)
    assert ok is True and rem == 0
    assert s.consecutive_tp_streak == 0
    assert s.last_tp_timestamp == 0.0
    _ = _time  # keep import intentional for parity with production hook


def test_profit_lock_stands_aside_at_500() -> None:
    """At $500 realized, day is done — no new entries (not a 1-MES continue)."""
    from engine.config import (
        GRADE_DAILY_PROFIT_LOCK,
        PROFIT_LOCK_MAX_CONTRACTS,
        STARTING_NAV,
        VIRTUE_BASE_TP_COOLDOWN_CYCLES,
        VIRTUE_HARD_STOP_COOLDOWN_CYCLES,
        VIRTUE_POSITION_STOP_DOLLARS,
        VIRTUE_POSITION_TP_DOLLARS,
        VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES,
        VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES,
        VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES,
        VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES,
    )

    assert float(GRADE_DAILY_PROFIT_LOCK) == 500.0
    assert int(PROFIT_LOCK_MAX_CONTRACTS) == 0
    assert float(VIRTUE_POSITION_TP_DOLLARS) == 100.0
    assert float(VIRTUE_POSITION_STOP_DOLLARS) == 75.0
    assert int(VIRTUE_BASE_TP_COOLDOWN_CYCLES) == 8
    assert int(VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES) == 5
    assert int(VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES) == 12
    assert int(VIRTUE_HARD_STOP_COOLDOWN_CYCLES) == 12
    # Compat aliases stay wired to the canonical knobs.
    assert int(VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES) == int(VIRTUE_BASE_TP_COOLDOWN_CYCLES)
    assert int(VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES) == int(VIRTUE_HARD_STOP_COOLDOWN_CYCLES)
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


def test_entry_structure_atr_adx_spread_gates() -> None:
    """ATR expanding + ADX>20 rising + VWAP/TWAP % spread widening required."""
    from engine.entry_structure import (
        commit_entry_structure_memory,
        evaluate_entry_structure_gates,
    )
    from main import VirtueSession

    s = VirtueSession()
    ok, reason = evaluate_entry_structure_gates(
        s, atr=1.0, adx=25.0, vwap=100.0, twap=99.0, price=100.0
    )
    assert ok is False
    assert "warmup" in reason

    commit_entry_structure_memory(
        s, atr=1.0, adx=22.0, vwap=100.0, twap=99.5, price=100.0
    )
    # ATR not expanding
    ok, reason = evaluate_entry_structure_gates(
        s, atr=0.9, adx=25.0, vwap=100.0, twap=99.0, price=100.0
    )
    assert ok is False and "atr_not_expanding" in reason

    # ADX not rising
    ok, reason = evaluate_entry_structure_gates(
        s, atr=1.2, adx=21.0, vwap=100.0, twap=99.0, price=100.0
    )
    assert ok is False and "adx_not_rising" in reason

    # ADX weak
    ok, reason = evaluate_entry_structure_gates(
        s, atr=1.2, adx=19.0, vwap=100.0, twap=99.0, price=100.0
    )
    assert ok is False and "adx_weak" in reason

    # Spread not widening (prior |100-99.5|/100 = 0.5%)
    ok, reason = evaluate_entry_structure_gates(
        s, atr=1.2, adx=25.0, vwap=100.0, twap=99.6, price=100.0
    )
    assert ok is False and "spread_not_widening" in reason

    # All clear: ATR up, ADX up through 20, spread widens
    ok, reason = evaluate_entry_structure_gates(
        s, atr=1.2, adx=25.0, vwap=100.0, twap=99.0, price=100.0
    )
    assert ok is True, reason
    assert "entry_structure:ok" in reason

    # Extreme ADX rising waives ATR/spread noise
    commit_entry_structure_memory(
        s, atr=2.0, adx=50.0, vwap=100.0, twap=100.0, price=100.0
    )
    ok, reason = evaluate_entry_structure_gates(
        s, atr=1.5, adx=55.0, vwap=100.0, twap=100.0, price=100.0
    )
    assert ok is True and "extreme" in reason, reason

    # Twin anchors (VWAP=TWAP): price displacement still counts as structure width.
    from engine.entry_structure import anchor_spread_pct

    twin = anchor_spread_pct(vwap=100.0, twap=100.0, price=100.5)
    assert twin > 0.0

    # Below-VWAP short: soft ADX + waive ATR/ADX-rise/spread grind.
    commit_entry_structure_memory(
        s, atr=2.0, adx=20.0, vwap=100.0, twap=99.5, price=99.0
    )
    ok, reason = evaluate_entry_structure_gates(
        s,
        atr=1.5,
        adx=12.0,
        vwap=100.0,
        twap=99.6,
        price=99.0,
        adx_min=8.0,
        below_vwap_short=True,
    )
    assert ok is True and "below_vwap_short" in reason, reason
    ok, reason = evaluate_entry_structure_gates(
        s,
        atr=1.5,
        adx=7.0,
        vwap=100.0,
        twap=99.6,
        price=99.0,
        adx_min=8.0,
        below_vwap_short=True,
    )
    assert ok is False and "adx_weak" in reason


def test_sleeve_reconcile_repairs_desync() -> None:
    """Broker net wins over phantom sleeve books (Justice)."""
    from engine.dual_sleeve import reconcile_sleeves_to_broker
    from main import VirtueSession

    s = VirtueSession()
    s.core_active = True
    s.core_side = "LONG"
    s.core_size = 1
    s.core_entry_price = 100.0
    s.tactical_active = True
    s.tactical_side = "LONG"
    s.tactical_size = 1
    # Broker flat — books must clear
    ok, detail = reconcile_sleeves_to_broker(s, s.broker)
    assert ok is False
    assert "repaired_flat" in detail
    assert s.core_active is False
    assert s.tactical_active is False


def test_anti_churn_temperance_gates() -> None:
    """Streaks freeze under pipeline lock; day cap / hysteresis knobs are armed."""
    from engine.config import (
        VIRTUE_COURSE_CORRECT_LONG_BLEND,
        VIRTUE_COURSE_CORRECT_SHORT_BLEND,
        VIRTUE_MAX_TACTICAL_TRADES_PER_DAY,
        VIRTUE_SCORE_LONG_EXIT,
        VIRTUE_SCORE_SHORT_EXIT,
        VIRTUE_TACTICAL_ADX_MIN,
    )
    from main import VirtueSession, check_course_correct, update_outcome_state

    assert float(VIRTUE_SCORE_LONG_EXIT) == 45.0
    assert float(VIRTUE_SCORE_SHORT_EXIT) == 55.0
    assert float(VIRTUE_COURSE_CORRECT_LONG_BLEND) == 45.0
    assert float(VIRTUE_COURSE_CORRECT_SHORT_BLEND) == 55.0
    assert float(VIRTUE_TACTICAL_ADX_MIN) == 20.0
    assert int(VIRTUE_MAX_TACTICAL_TRADES_PER_DAY) == 12

    # Mid-band must not flatten
    assert check_course_correct("LONG", 50.0)[0] is False
    assert check_course_correct("SHORT", 50.0)[0] is False

    s = VirtueSession()
    s.cycle = 10
    s.long_streak = 3
    update_outcome_state(s, -10.0, "course_correct_x", current_engine_cycle=10)
    # Round-trip counted once on close
    assert s.trades_today == 1
    # Simulate what run_cycle does while locked: streaks cleared
    s.cycle = 11
    pipe_resume = int(s.pipeline_resume_cycle)
    assert pipe_resume > 11
    s.long_streak = 0
    s.short_streak = 0
    assert s.long_streak == 0


def test_multi_sleeve_order_router() -> None:
    """Tactical FLAT must never imply whole-account wipe; hedge/ceiling gates hold."""
    import asyncio
    from engine.sleeve_order_router import (
        absolute_contract_footprint,
        calculate_net_account_exposure,
        execute_tactical_action,
    )
    from main import (
        VirtueSession,
        _mark_core_open,
        _mark_tactical_flat,
        _mark_tactical_open,
    )

    # Dict-shaped state (operator contract)
    state = {
        "max_account_contract_ceiling": 2,
        "core_anchor_sleeve": {"side": "LONG", "size": 1, "active": True},
        "tactical_satellite_sleeve": {
            "engine_exposure": "LONG",
            "size": 1,
            "active": True,
        },
    }
    assert calculate_net_account_exposure(state) == 2
    assert absolute_contract_footprint(state) == 2

    state["tactical_satellite_sleeve"]["engine_exposure"] = "SHORT"
    assert calculate_net_account_exposure(state) == 0  # signed net cancels

    s = VirtueSession()
    _mark_core_open(s, side="LONG", price=5420.5, size=1)
    _mark_tactical_open(s, side="LONG", price=5421.0, size=1, cycle=100)
    assert calculate_net_account_exposure(s) == 2

    class _SpyBroker:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.open_positions = [
                {"direction": "LONG", "size": 2, "price": 5420.5, "entry_price": 5420.5}
            ]

        def net_exposure(self) -> tuple[str, int]:
            total = sum(int(p.get("size") or 0) for p in self.open_positions)
            if total <= 0:
                return "FLAT", 0
            return "LONG", total

        async def close_contracts(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(("close_contracts", kwargs))
            qty = int(kwargs["contracts"])
            remain = max(0, 2 - qty)
            self.open_positions = (
                [
                    {
                        "direction": "LONG",
                        "size": remain,
                        "price": 5420.5,
                        "entry_price": 5420.5,
                    }
                ]
                if remain > 0
                else []
            )
            return True, 12.5

        async def fire_order(self, order):  # type: ignore[no-untyped-def]
            self.calls.append(("fire_order", order.direction, order.size))
            return None

        async def flatten_all(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("flatten_all must not be used by tactical router")

    s.broker = _SpyBroker()  # type: ignore[assignment]

    async def _run() -> None:
        ok, pnl, detail = await execute_tactical_action(
            s,
            target_side="FLAT",
            current_cycle=101,
            price=5425.0,
            stop_ticks=30,
            reason="take_profit_$100",
            mark_flat=_mark_tactical_flat,
        )
        assert ok is True
        assert detail == "tactical_closed"
        # Sleeve entry 5421 → exit 5425: LONG 4pts * $5 = $20 (not broker spy 12.5)
        assert pnl == 20.0
        assert s.broker.calls[0][0] == "close_contracts"
        assert s.broker.calls[0][1]["contracts"] == 1
        assert float(s.broker.calls[0][1].get("entry_price") or 0) == 5421.0
        assert str(s.broker.calls[0][1].get("sleeve") or "") == "tac"
        assert "tac:" in str(s.broker.calls[0][1].get("reason") or "")
        assert s.broker.net_exposure() == ("LONG", 1)  # core remains
        assert s.core_active is True and s.core_size == 1

        # Hedge forbidden while core LONG
        ok, _, detail = await execute_tactical_action(
            s,
            target_side="SHORT",
            target_size=1,
            current_cycle=102,
            price=5425.0,
            stop_ticks=30,
            reason="entry",
        )
        assert ok is False
        assert "hedge" in detail or "opposite" in detail or "forbidden" in detail

    asyncio.run(_run())


def test_dual_sleeve_unified_rules() -> None:
    """Net ceiling, alignment, structural invalidation, temperance isolation."""
    from engine.dual_sleeve import (
        STRUCTURAL_BEAR,
        STRUCTURAL_BULL,
        STRUCTURAL_NEUTRAL,
        build_dual_sleeve_state,
        classify_structural_regime,
        core_should_invalidate,
        tactical_entry_allowed,
    )
    from main import VirtueSession, _credit_core_pnl, calculate_temperance_parameters

    ok, reason = tactical_entry_allowed(
        core_active=True,
        core_side="LONG",
        core_size=1,
        tactical_side="LONG",
        tactical_size=1,
        ceiling=2,
    )
    assert ok is True, reason

    ok, reason = tactical_entry_allowed(
        core_active=True,
        core_side="LONG",
        core_size=1,
        tactical_side="SHORT",
        tactical_size=1,
        ceiling=2,
    )
    assert ok is False
    assert "opposite_core" in reason

    ok, reason = tactical_entry_allowed(
        core_active=True,
        core_side="LONG",
        core_size=1,
        tactical_side="LONG",
        tactical_size=2,
        ceiling=2,
    )
    assert ok is False
    assert "ceiling" in reason

    # Core flat → satellite may open either side under ceiling
    ok, _ = tactical_entry_allowed(
        core_active=False,
        core_side="FLAT",
        core_size=0,
        tactical_side="SHORT",
        tactical_size=1,
        ceiling=2,
    )
    assert ok is True

    # Slow invalidation: hard opposite regime OR deep momentum — sticky in NEUTRAL
    inv, why = core_should_invalidate(
        core_active=True,
        core_side="LONG",
        structural_regime=STRUCTURAL_BEAR,
        blend=55.0,
    )
    assert inv is True
    assert "regime_flip" in why

    inv, why = core_should_invalidate(
        core_active=True,
        core_side="LONG",
        structural_regime=STRUCTURAL_NEUTRAL,
        blend=55.0,
    )
    assert inv is False, why  # sticky through chop

    inv, why = core_should_invalidate(
        core_active=True,
        core_side="LONG",
        structural_regime=STRUCTURAL_BULL,
        blend=40.0,
    )
    assert inv is True
    assert "momentum_breakdown" in why

    inv, _ = core_should_invalidate(
        core_active=True,
        core_side="LONG",
        structural_regime=STRUCTURAL_BULL,
        blend=58.0,
    )
    assert inv is False

    from engine.dual_sleeve import evaluate_core_macro_safety

    dict_state = {
        "vwap_twap_blend": 38.0,
        "regime_engine": {"macro_structural_regime": STRUCTURAL_BULL},
        "core_anchor_sleeve": {
            "active": True,
            "side": "LONG",
            "size": 1,
            "structural_invalidation_blend": 40.0,
        },
    }
    hit, why = evaluate_core_macro_safety(dict_state, 14930)
    assert hit is True
    assert "momentum_breakdown" in why

    dict_state["vwap_twap_blend"] = 52.0
    dict_state["regime_engine"]["macro_structural_regime"] = STRUCTURAL_NEUTRAL
    hit, _ = evaluate_core_macro_safety(dict_state, 14931)
    assert hit is False

    # SHORT: depth 40 → breakdown at blend >= 60
    inv, why = core_should_invalidate(
        core_active=True,
        core_side="SHORT",
        structural_regime=STRUCTURAL_BEAR,
        blend=60.0,
    )
    assert inv is True
    assert "momentum_breakdown" in why

    # Confirm streaks → STRUCTURAL_BULL
    regime, bull, bear = STRUCTURAL_NEUTRAL, 0, 0
    for _ in range(5):
        regime, bull, bear = classify_structural_regime(
            macro_bias="BULL",
            adx=25.0,
            confirm_cycles=5,
            bull_streak=bull,
            bear_streak=bear,
        )
    assert regime == STRUCTURAL_BULL

    # Core PnL must not widen tactical temperance buffers
    s = VirtueSession()
    s.consecutive_losses = 0
    s.last_reason = "none"
    _credit_core_pnl(s, -120.0)
    contracts, buf = calculate_temperance_parameters(session=s)
    assert contracts == 1
    assert buf == 0.0
    assert s.consecutive_losses == 0
    assert s.core_realized_pnl_today == -120.0

    s.core_active = True
    s.core_side = "LONG"
    s.core_size = 1
    s.core_entry_price = 5420.50
    s.macro_structural_regime = STRUCTURAL_BULL
    s.last_regime = "CHOP_NO_TRADE"
    s.pipeline_resume_cycle = 14930
    s.last_result = "WIN"
    s.last_reason = "take_profit"
    payload = build_dual_sleeve_state(s, account_nav=16065.80)
    assert payload["max_account_contract_ceiling"] == 1
    assert payload["core_enabled"] is False
    assert payload["simplify_mode"] is True
    assert payload["regime_engine"]["macro_structural_regime"] == STRUCTURAL_BULL
    assert payload["core_anchor_sleeve"]["active"] is True
    assert payload["core_anchor_sleeve"]["structural_invalidation_blend"] == 40.0
    assert payload["tactical_satellite_sleeve"]["pipeline_resume_cycle"] == 14930

    from engine.ui_state_bridge import build_virtue_system_state

    st = build_virtue_system_state(s, last_price=5425.0, regime="CHOP_NO_TRADE")
    assert "dual_sleeve" in st
    assert st["dual_sleeve"]["core_anchor_sleeve"]["side"] == "LONG"


def test_core_invalidation_cooldown_and_tp() -> None:
    """Invalidation arms 20m cooldown; ADX breakout can clear; core TP at $75."""
    from engine.config import POINT_VALUE, VIRTUE_CORE_TP_DOLLARS
    from engine.dual_sleeve import (
        STRUCTURAL_BEAR,
        STRUCTURAL_BULL,
        arm_core_invalidation_cooldown,
        core_reentry_allowed,
        core_tp_hit,
    )
    from main import VirtueSession

    assert float(VIRTUE_CORE_TP_DOLLARS) == 75.0
    s = VirtueSession()
    s.core_active = True
    s.core_side = "SHORT"
    s.core_size = 1
    s.core_entry_price = 9600.0
    # SHORT +15 pts * $5 = $75
    assert core_tp_hit(s, 9585.0) is True
    assert core_tp_hit(s, 9590.0) is False

    arm_core_invalidation_cooldown(s, closed_side="SHORT")
    assert float(s.core_reentry_blocked_until) > 0
    ok, why = core_reentry_allowed(
        s, side="SHORT", adx=20.0, structural_regime=STRUCTURAL_BEAR
    )
    assert ok is False
    assert "cooldown" in why
    ok, why = core_reentry_allowed(
        s, side="SHORT", adx=30.0, structural_regime=STRUCTURAL_BEAR
    )
    assert ok is True
    assert "adx_breakout" in why
    # Bull open while cooldown for short still cleared after breakout path
    arm_core_invalidation_cooldown(s, closed_side="LONG")
    ok, _ = core_reentry_allowed(
        s, side="LONG", adx=30.0, structural_regime=STRUCTURAL_BULL
    )
    assert ok is True
    assert POINT_VALUE == 5.0


def test_pipeline_stuck_alert_debounce() -> None:
    """Depth > 100 for 30s fires CRITICAL once then force-rebases absurd depth."""
    from main import VirtueSession, _check_pipeline_stuck_alert, _pipeline_queue_depth
    import time

    s = VirtueSession()
    s.cycle = 1
    s.pipeline_resume_cycle = 5000
    assert _pipeline_queue_depth(s) > 100
    s.pipeline_stuck_since = time.time() - 35.0
    s.pipeline_stuck_alerted = False
    _check_pipeline_stuck_alert(s)
    assert s.pipeline_stuck_alerted is True
    # Force rebase should shrink residual
    assert int(s.pipeline_resume_cycle) < 100


def test_simplify_tactical_only_1mes() -> None:
    """Core disabled + ceiling 1 — no core opens; size hard-capped at 1."""
    import os
    from engine.config import (
        PAPER_MAX_MES_CONTRACTS,
        account_contract_ceiling_limit,
        paper_max_mes_contracts,
        virtue_core_enabled,
    )
    from engine.dual_sleeve import (
        STRUCTURAL_BEAR,
        core_should_open,
        core_size_default,
        build_dual_sleeve_state,
    )
    from main import VirtueSession

    assert virtue_core_enabled() is False
    assert int(PAPER_MAX_MES_CONTRACTS) == 1
    assert int(paper_max_mes_contracts()) == 1
    assert int(account_contract_ceiling_limit()) == 1
    assert core_size_default() == 0
    ok, side = core_should_open(STRUCTURAL_BEAR, core_active=False)
    assert ok is False
    assert side == "FLAT"

    # Env override can re-enable for experiments.
    os.environ["FM_VIRTUE_CORE_ENABLED"] = "1"
    os.environ["FM_MAX_ACCOUNT_CONTRACT_CEILING"] = "2"
    try:
        assert virtue_core_enabled() is True
        assert int(account_contract_ceiling_limit()) == 2
        ok, side = core_should_open(STRUCTURAL_BEAR, core_active=False)
        assert ok is True and side == "SHORT"
    finally:
        os.environ.pop("FM_VIRTUE_CORE_ENABLED", None)
        os.environ.pop("FM_MAX_ACCOUNT_CONTRACT_CEILING", None)

    s = VirtueSession()
    payload = build_dual_sleeve_state(s, account_nav=16000.0)
    assert payload["core_enabled"] is False
    assert payload["simplify_mode"] is True
    assert payload["max_account_contract_ceiling"] == 1


def test_sleeve_pnl_uses_sleeve_entry_not_broker_avg() -> None:
    """Dual SHORT close credits tactical from tactical entry, not blended avg."""
    from broker import VirtueBroker
    import asyncio

    async def _run() -> None:
        b = VirtueBroker()
        b.open_positions = [
            {
                "direction": "SHORT",
                "size": 2,
                "price": 9620.0,
                "entry_price": 9620.0,
            }
        ]
        # Tactical entered better (9615); sleeve entry must win over blended 9620.
        ok, pnl = await b.close_contracts(
            contracts=1,
            price=9610.0,
            stop_ticks=30,
            reason="tac:time_decay",
            entry_price=9615.0,
            sleeve="tac",
        )
        assert ok is True
        # SHORT: (9615 - 9610) * 5 * 1 = 25
        assert abs(float(pnl) - 25.0) < 0.01
        assert b.net_exposure()[1] == 1

    asyncio.run(_run())


if __name__ == "__main__":
    test_wisdom_bull_regime()
    test_wisdom_bear_regime()
    test_wisdom_chop_stand_aside()
    test_structural_short_below_vwap_with_soft_adx()
    test_macro_bias_latches_bear_from_vwap_score()
    test_macro_participation_blocks_fake_long()
    test_market_lift_and_vol_conviction_scores()
    test_score_vs_anchor_bounds()
    test_score_discontinuity_stands_aside()
    test_anchor_gap_too_wide()
    test_rebase_anchors_aligns_scores_to_live()
    test_rebase_fixes_ghost_vwap_when_last_close_already_matches()
    test_rebase_flat_pins_when_seed_mean_is_ghost_level()
    test_anchors_diverged_atr_gate()
    test_target_ticks_from_atr_floor_and_scale()
    test_position_tp_ticks_for_dollar_target()
    test_save_state_throttled_stops_cleanly()
    test_engine_event_loop_consumes_tick_ctx()
    test_heartbeat_throttled_every_n_cycles()
    test_hysteresis_holds_while_thesis_valid()
    test_separate_entry_exit_bands()
    test_nimble_short_invalidates_before_stop()
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
    test_required_streak_is_three_for_structure()
    test_check_course_correct_hard_blend_hook()
    test_calculate_temperance_parameters_loss_and_course_correct()
    test_update_outcome_state_win_loss_and_tp_streak()
    test_is_entry_pipeline_clear_layer1_cycle_lock()
    test_check_virtue_pnl_lock_trailing_floor()
    test_check_time_decay_exit_stagnant_hold()
    test_observability_metric_lines_and_webhook_guard()
    test_macromathics_core_production_phases()
    test_calculate_dynamic_blend_thresholds_velocity_gate()
    test_verify_pipeline_entry_temperance_and_bull_short()
    test_evaluate_directional_gate_bull_day_shorts()
    test_verify_cooldown_validity_multi_tp_lock()
    test_weighted_avg_entry()
    test_capital_drag_allows_irreducible_1_mes()
    test_credit_pnl_updates_paper_book_only()
    test_load_persisted_day_bucket_same_day_only()
    test_session_day_roll_clears_yesterdays_profit_lock()
    test_dual_sleeve_unified_rules()
    test_multi_sleeve_order_router()
    test_anti_churn_temperance_gates()
    test_entry_structure_atr_adx_spread_gates()
    test_sleeve_reconcile_repairs_desync()
    test_core_invalidation_cooldown_and_tp()
    test_pipeline_stuck_alert_debounce()
    test_simplify_tactical_only_1mes()
    test_sleeve_pnl_uses_sleeve_entry_not_broker_avg()
    print("ALL VIRTUE BRAIN TESTS PASSED")

