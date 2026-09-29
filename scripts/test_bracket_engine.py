"""Bracket engine: gamma regime, prior-day value area, fixed 10/20 risk."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from main_engine import STOP_POINTS, TARGET_POINTS, bracket_risk_dollars, must_flatten
from modules.regime import GammaRegime, get_market_regime, read_gamma_file, snapshot_from_flip
from modules.strategy import evaluate_entry_signal, value_area_from_bars


def test_schema_is_v2() -> None:
    from main_engine import SCHEMA_PATH, load_state_schema

    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert raw["bot_name"] == "futuremathics_virtue_v2"
    assert raw["contract_size"] == 1
    assert raw["execution_mode"] == "paper"
    assert raw["state"] == "IDLE"
    assert "stop_dollars" not in raw
    loaded = load_state_schema()
    assert loaded["risk_params"]["max_daily_loss_realized"] == -300.0
    assert loaded["risk_params"]["max_daily_gain_realized"] == 500.0
    assert loaded["risk_params"]["max_consecutive_losses"] == 3
    stop, target, contracts = bracket_risk_dollars()
    assert contracts == 1
    assert stop == STOP_POINTS * 5.0
    assert target == TARGET_POINTS * 5.0
    assert stop == 50.0
    assert target == 100.0


def test_no_unmonitored_hold() -> None:
    assert must_flatten(session_monitored=False, price_fresh=True, has_position=True) is True
    assert must_flatten(session_monitored=True, price_fresh=False, has_position=True) is True
    assert must_flatten(session_monitored=True, price_fresh=True, has_position=True) is False
    assert must_flatten(session_monitored=False, price_fresh=False, has_position=False) is False


def test_gamma_flip_regime() -> None:
    assert get_market_regime(100.0, 90.0) == "POSITIVE_GAMMA"
    assert get_market_regime(90.0, 90.0) == "POSITIVE_GAMMA"
    assert get_market_regime(80.0, 90.0) == "NEGATIVE_GAMMA"
    above = snapshot_from_flip(100.0, 90.0)
    below = snapshot_from_flip(80.0, 90.0)
    missing = snapshot_from_flip(None, 90.0)
    assert above.regime == GammaRegime.POSITIVE
    assert below.regime == GammaRegime.NEGATIVE
    assert missing.regime == GammaRegime.UNKNOWN


def test_stale_gamma_file() -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder) / "gamma_state.json"
        tmp.write_text(json.dumps({"net_gex": 10, "timestamp": old}), encoding="utf-8")
        snap = read_gamma_file(tmp, stale_seconds=900)
    assert snap.regime == GammaRegime.UNKNOWN
    assert snap.reason == "gex_missing_or_stale"


def _levels() -> dict[str, float]:
    return {"val": 100.0, "vah": 110.0, "poc": 105.0}


def test_val_vah_rejection_and_poc_breakout() -> None:
    bars = []
    for i, vol in enumerate([10, 20, 40, 20, 10]):
        px = 100 + i
        bars.append(
            {"high": px + 0.2, "low": px - 0.2, "close": px, "volume": vol, "timestamp": "2026-09-28T14:00:00+00:00"}
        )
    area = value_area_from_bars(bars, session_date="2026-09-28", bin_size=1.0)
    assert area is not None
    assert area.val < area.poc < area.vah

    levels = _levels()
    long_df = pd.DataFrame(
        [
            {"close": 101.0, "low": 100.5, "high": 102.0, **levels},
            {"close": 100.5, "low": 99.0, "high": 101.0, **levels},
        ]
    )
    long_sig = evaluate_entry_signal(long_df, "POSITIVE_GAMMA")
    assert long_sig["action"] == "BUY"
    assert long_sig["order_type"] == "LIMIT"
    assert long_sig["price"] == 100.0
    assert long_sig["reason"] == "VAL_REJECTION"

    short_df = pd.DataFrame(
        [
            {"close": 109.0, "low": 108.0, "high": 109.5, **levels},
            {"close": 109.5, "low": 109.0, "high": 111.0, **levels},
        ]
    )
    short_sig = evaluate_entry_signal(short_df, "POSITIVE_GAMMA")
    assert short_sig["action"] == "SELL"
    assert short_sig["price"] == 110.0
    assert short_sig["reason"] == "VAH_REJECTION"

    poc_df = pd.DataFrame(
        [
            {"close": 104.0, "low": 103.0, "high": 104.5, **levels},
            {"close": 106.0, "low": 104.0, "high": 106.5, **levels},
        ]
    )
    poc_sig = evaluate_entry_signal(poc_df, "NEGATIVE_GAMMA")
    assert poc_sig["action"] == "BUY"
    assert poc_sig["order_type"] == "MARKET"
    assert poc_sig["reason"] == "POC_MOMENTUM"

    fade_down = pd.DataFrame(
        [
            {"close": 106.0, "low": 105.5, "high": 106.5, **levels},
            {"close": 104.0, "low": 103.5, "high": 106.0, **levels},
        ]
    )
    assert evaluate_entry_signal(fade_down, "NEGATIVE_GAMMA")["action"] == "HOLD"
    assert evaluate_entry_signal(pd.DataFrame(), "POSITIVE_GAMMA")["action"] == "HOLD"
    assert evaluate_entry_signal(long_df.iloc[:1], "POSITIVE_GAMMA")["reason"] == "Insufficient data"


class _PaperBroker:
    def __init__(self, price: float) -> None:
        self.price = price
        self.orders: list[dict] = []
        self.closes: list[tuple[str, float]] = []

    def get_current_price(self) -> float:
        return self.price

    def submit_bracket_order(self, **kwargs) -> None:
        self.orders.append(kwargs)

    def close_bracket(self, reason: str, price: float) -> None:
        self.closes.append((reason, price))


def test_virtue_engine_v2_bracket() -> None:
    from main_engine import VirtueEngineV2, load_state_schema

    cfg = load_state_schema()
    levels = _levels()
    long_df = pd.DataFrame(
        [
            {"close": 101.0, "low": 100.5, "high": 102.0, **levels},
            {"close": 100.5, "low": 99.0, "high": 101.0, **levels},
        ]
    )
    short_df = pd.DataFrame(
        [
            {"close": 109.0, "low": 108.0, "high": 109.5, **levels},
            {"close": 109.5, "low": 109.0, "high": 111.0, **levels},
        ]
    )
    broker = _PaperBroker(5000.0)
    engine = VirtueEngineV2(broker, cfg)
    opened = engine.execute_tick(long_df, 5000.0, 4900.0)
    assert opened["action"] == "BUY"
    assert broker.orders[0]["qty"] == 1
    assert broker.orders[0]["stop_loss"] == 4990.0
    assert broker.orders[0]["take_profit"] == 5020.0
    engine.execute_tick(long_df, 5005.0, 4900.0)
    assert len(broker.orders) == 1
    engine.execute_tick(long_df, 4990.0, 4900.0)
    assert engine.position is None
    assert broker.closes[-1][0] == "STOP"

    short_broker = _PaperBroker(5000.0)
    short_engine = VirtueEngineV2(short_broker, cfg)
    short_engine.execute_tick(short_df, 5000.0, 4900.0)
    assert short_engine.position["stop"] == 5010.0
    assert short_engine.position["target"] == 4980.0
    short_engine.execute_tick(short_df, 4980.0, 4900.0)
    assert short_engine.position is None
    assert short_broker.closes[-1][0] == "TARGET"

    idle = VirtueEngineV2(_PaperBroker(5000.0), cfg)
    held = idle.execute_tick(long_df, None, 4900.0)
    assert held["action"] == "HOLD"
    assert idle.position is None
    live = dict(cfg)
    live["execution_mode"] = "live"
    try:
        VirtueEngineV2(_PaperBroker(5000.0), live)
        raise AssertionError("live mode must be refused")
    except ValueError:
        pass


def main() -> None:
    test_schema_is_v2()
    test_no_unmonitored_hold()
    test_gamma_flip_regime()
    test_stale_gamma_file()
    test_val_vah_rejection_and_poc_breakout()
    test_virtue_engine_v2_bracket()
    print("bracket engine tests passed")


if __name__ == "__main__":
    main()
