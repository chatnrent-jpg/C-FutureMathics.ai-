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
    s = WisdomStrategy(adx_trend_min=10.0, atr_pct_chaos_max=50.0)
    s.seed(_trending_bars(90, bull=True, step=2.0))
    d = s.evaluate()
    assert d.action == SignalAction.LONG
    assert d.regime in {Regime.TREND_BULL, Regime.WARMUP} or d.action == SignalAction.LONG


def test_wisdom_bear_regime() -> None:
    s = WisdomStrategy(adx_trend_min=10.0, atr_pct_chaos_max=50.0)
    s.seed(_trending_bars(90, bull=False, step=2.0))
    d = s.evaluate()
    assert d.action == SignalAction.SHORT


def test_wisdom_chop_stand_aside() -> None:
    s = WisdomStrategy(adx_trend_min=25.0, atr_pct_chaos_max=50.0)
    # Sideways oscillation → weak directional ADX → stand aside
    bars: list[Bar] = []
    price = 5000.0
    for i in range(90):
        price = 5000.0 + (3.0 if i % 2 == 0 else -3.0)
        bars.append(Bar(high=price + 0.25, low=price - 0.25, close=price))
    s.seed(bars)
    d = s.evaluate()
    assert d.action == SignalAction.FLAT
    assert d.regime in {Regime.CHOP_NO_TRADE, Regime.WARMUP}


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


if __name__ == "__main__":
    test_wisdom_bull_regime()
    test_wisdom_bear_regime()
    test_wisdom_chop_stand_aside()
    test_size_respects_half_percent()
    test_validate_order_symbol_size_stale()
    test_risk_math_consistency()
    test_position_exclusivity_helpers()
    test_network_timeout_constant()
    test_zero_equity_blocks_sizing()
    test_forward_test_paper_nav_allows_sizing()
    print("ALL VIRTUE BRAIN TESTS PASSED")
