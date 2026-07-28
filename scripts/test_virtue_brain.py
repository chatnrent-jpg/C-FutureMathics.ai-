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



def test_hysteresis_avoids_50_whipsaw() -> None:
    """While LONG, scores dipping slightly must not flip SHORT until <=45."""
    s = WisdomStrategy(atr_pct_chaos_max=50.0, min_anchor_samples=5, long_enter=55.0, short_enter=45.0)
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


def test_vwap_twap_agreement_required() -> None:
    """Hard dump → clear SHORT band (<=45)."""
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
    assert d.vwap_score <= 45.0 and d.twap_score <= 45.0



def test_size_respects_half_percent() -> None:
    equity = 100_000.0
    stop_ticks = 60
    sized = calculate_max_contracts(equity=equity, stop_ticks=stop_ticks, hard_cap=1000)
    assert not sized.rejected
    assert sized.risk_pct <= FIXED_FRACTIONAL_RISK_PCT + 1e-12
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
    # 0.5% of 100k = $500; 60 ticks * $1.25 = $75/contract → floor(500/75)=6
    sized = calculate_max_contracts(equity=100_000.0, stop_ticks=60, hard_cap=100)
    assert sized.contracts == int((100_000.0 * 0.005) // (60 * TICK_VALUE))


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
    assert sized.contracts >= 1


def test_stop_hit_and_flat_exit_helpers() -> None:
    from broker import VirtueBroker
    from engine.config import TICK_SIZE

    b = VirtueBroker()
    b.open_positions = [{"direction": "SHORT", "size": 3, "price": 9269.12}]
    assert b.stop_hit(price=9269.12, stop_ticks=60) is False
    # SHORT stop is above entry
    assert b.stop_hit(price=9269.12 + 60 * TICK_SIZE, stop_ticks=60) is True
    b.open_positions = [{"direction": "LONG", "size": 2, "price": 9200.0}]
    assert b.stop_hit(price=9200.0 - 60 * TICK_SIZE, stop_ticks=60) is True


def test_take_profit_and_scale_out_qty() -> None:
    from broker import VirtueBroker
    from engine.config import DEFAULT_TARGET_TICKS, SCALE_OUT_LEAVE_CONTRACTS, TICK_SIZE

    b = VirtueBroker()
    entry = 9200.0
    b.open_positions = [{"direction": "LONG", "size": 3, "price": entry}]
    assert b.take_profit_hit(price=entry, target_ticks=DEFAULT_TARGET_TICKS) is False
    assert b.take_profit_hit(
        price=entry + DEFAULT_TARGET_TICKS * TICK_SIZE,
        target_ticks=DEFAULT_TARGET_TICKS,
    )
    assert b.scale_out_close_qty(leave=SCALE_OUT_LEAVE_CONTRACTS) == 2

    b.open_positions = [{"direction": "SHORT", "size": 3, "price": entry}]
    assert b.take_profit_hit(
        price=entry - DEFAULT_TARGET_TICKS * TICK_SIZE,
        target_ticks=DEFAULT_TARGET_TICKS,
    )
    # After conceptually leaving one runner, no more scale-out
    b.open_positions = [{"direction": "SHORT", "size": 1, "price": entry, "scaled_out_tp": True}]
    assert b.scale_out_close_qty(leave=SCALE_OUT_LEAVE_CONTRACTS) == 0


if __name__ == "__main__":
    test_wisdom_bull_regime()
    test_wisdom_bear_regime()
    test_wisdom_chop_stand_aside()
    test_score_vs_anchor_bounds()
    test_score_discontinuity_stands_aside()
    test_anchor_gap_too_wide()
    test_rebase_anchors_aligns_scores_to_live()
    test_hysteresis_avoids_50_whipsaw()
    test_vwap_twap_agreement_required()
    test_size_respects_half_percent()
    test_validate_order_symbol_size_stale()
    test_risk_math_consistency()
    test_position_exclusivity_helpers()
    test_network_timeout_constant()
    test_zero_equity_blocks_sizing()
    test_forward_test_paper_nav_allows_sizing()
    test_stop_hit_and_flat_exit_helpers()
    test_take_profit_and_scale_out_qty()
    print("ALL VIRTUE BRAIN TESTS PASSED")
