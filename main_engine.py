"""
Fixed 1-MES bracket loop.

10-point stop ($50) and 20-point target ($100). No trail.
No new risk without a fresh gamma flip and yesterday's value area.
An open bracket is watched on every mark. This paper broker has no native OCO.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.config import POINT_VALUE, bracket_engine_enabled
from modules.regime import GammaRegime, classify_volatility_regime, get_market_regime, read_gamma_file
from modules.strategy import (
    BracketSignal,
    ValueArea,
    bracket_from_entry,
    entry_bars_frame,
    evaluate_entry_signal,
    previous_session_bars,
    scale_ohlc_bars,
    scale_value_area,
    session_bars,
    drop_forming_bar,
    value_area_from_bars,
)
from strategy import SignalAction

logger = logging.getLogger("futuremathics.engine")

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
GAMMA_PATH = ROOT / "data" / "gamma_state.json"
SCHEMA_PATH = ROOT / "config" / "state_schema.json"


def load_state_schema() -> dict[str, Any]:
    """Read config/state_schema.json. A bad file is a hard failure, not a scalp fallback."""
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("state_schema_not_object")
    risk = raw.get("risk_params")
    if not isinstance(risk, dict):
        raise ValueError("state_schema_missing_risk_params")
    stop_pts = float(risk["stop_points"])
    target_pts = float(risk["target_points"])
    contracts = int(raw["contract_size"])
    loss = float(risk["max_daily_loss_realized"])
    gain = float(risk["max_daily_gain_realized"])
    streak = int(risk["max_consecutive_losses"])
    if stop_pts <= 0 or target_pts <= stop_pts:
        raise ValueError("state_schema_risk_not_2_to_1")
    if contracts != 1:
        raise ValueError("state_schema_contract_size_must_be_1")
    if loss >= 0 or gain <= 0 or streak <= 0:
        raise ValueError("state_schema_day_limits_invalid")
    if str(raw.get("execution_mode") or "").lower() != "paper":
        raise ValueError("state_schema_execution_mode_must_be_paper")
    return raw


def bracket_risk_dollars() -> tuple[float, float, int]:
    """(stop dollars, target dollars, contracts) from the v2 schema."""
    raw = load_state_schema()
    risk = raw["risk_params"]
    stop = round(float(risk["stop_points"]) * float(POINT_VALUE), 2)
    target = round(float(risk["target_points"]) * float(POINT_VALUE), 2)
    return stop, target, int(raw["contract_size"])


def schema_day_limits() -> tuple[float, float, int]:
    """(max daily loss, max daily gain, max consecutive losses). Loss is negative."""
    risk = load_state_schema()["risk_params"]
    return (
        float(risk["max_daily_loss_realized"]),
        float(risk["max_daily_gain_realized"]),
        int(risk["max_consecutive_losses"]),
    )


_SCHEMA = load_state_schema()
STOP_POINTS = float(_SCHEMA["risk_params"]["stop_points"])
TARGET_POINTS = float(_SCHEMA["risk_params"]["target_points"])
CONTRACTS = int(_SCHEMA["contract_size"])


def must_flatten(*, session_monitored: bool, price_fresh: bool, has_position: bool) -> bool:
    """True when an open book would sit without a live mark."""
    return bool(has_position) and (not session_monitored or not price_fresh)


async def resolve_bracket_signal(session: Any, price: float) -> BracketSignal:
    """Value area plus a real flip when one exists. Otherwise 15-minute ATR."""
    area, today_bars, history = await _entry_context(session, price)
    gamma = read_gamma_file(
        GAMMA_PATH,
        now=datetime.now(timezone.utc),
        spot_price=price,
    )
    if gamma.regime == GammaRegime.UNKNOWN:
        gamma = classify_volatility_regime(history)
    session.bracket_gamma = gamma.regime.value
    session.bracket_gamma_reason = gamma.reason
    if gamma.regime == GammaRegime.UNKNOWN:
        return BracketSignal("FLAT", gamma.reason)
    if area is None:
        return BracketSignal("FLAT", "value_area_unavailable")
    session.bracket_val = area.val
    session.bracket_poc = area.poc
    session.bracket_vah = area.vah
    session.bracket_value_date = area.session_date
    signal = bracket_from_entry(evaluate_entry_signal(entry_bars_frame(today_bars, area), gamma.regime.value))
    session.bracket_order_type = signal.order_type
    session.bracket_limit = signal.limit_price
    return signal


async def overlay_bracket_decision(session: Any, decision: Any, price: float) -> Any:
    """Replace the VWAP/SMA action. An open bracket is held until stop or target."""
    if not bracket_engine_enabled():
        return decision
    holding = ""
    if bool(getattr(session, "tactical_active", False)) and int(getattr(session, "tactical_size", 0) or 0) > 0:
        holding = str(getattr(session, "tactical_side", "") or "").upper()
    if holding in {"LONG", "SHORT"}:
        session.bracket_signal = holding
        return replace(
            decision,
            action=SignalAction[holding],
            reason="bracket_hold_10pt_stop_20pt_target",
        )
    signal = await resolve_bracket_signal(session, price)
    session.bracket_signal = signal.side
    action = {"LONG": SignalAction.LONG, "SHORT": SignalAction.SHORT}.get(
        signal.side, SignalAction.FLAT
    )
    return replace(decision, action=action, reason=signal.reason)


async def _entry_context(session: Any, mes_price: float) -> tuple[ValueArea | None, list[dict], list[dict]]:
    """Yesterday's value area, today's 15-minute bars, and the raw history used for ATR."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    feed = getattr(getattr(session, "broker", None), "data", None)
    if feed is None or not hasattr(feed, "fetch_spy_bars"):
        return None, [], []
    try:
        bars = await feed.fetch_spy_bars(timeframe="15Min", limit=500, lookback_days=5, session_rth=False)
    except Exception:
        return None, [], []
    rows = drop_forming_bar(list(bars or []))
    if not rows:
        return None, [], []
    try:
        spy_ref = float(rows[-1].get("close") or 0)
    except (TypeError, ValueError):
        return None, [], rows
    day, prev_rows = previous_session_bars(rows, today)
    if not prev_rows:
        return None, [], rows
    spy_area = value_area_from_bars(prev_rows, session_date=day, bin_size=0.25)
    if spy_area is None:
        return None, [], rows
    scaled = scale_value_area(spy_area, spy_reference=spy_ref, mes_price=float(mes_price))
    if scaled is None:
        return None, [], rows
    session._bracket_area = scaled
    today_rows = scale_ohlc_bars(session_bars(rows, today), spy_reference=spy_ref, mes_price=float(mes_price))
    return scaled, today_rows, rows


def _positive_price(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0.0:
        return None
    return number


def bracket_prices(side: str, entry_price: float, state_config: dict[str, Any]) -> dict[str, float | str]:
    """1 MES stop and target from the schema. BUY subtracts the stop; SELL adds it."""
    risk = state_config["risk_params"]
    stop_dist = float(risk["stop_points"])
    target_dist = float(risk["target_points"])
    entry = float(entry_price)
    if str(side).upper() == "BUY":
        stop_price = entry - stop_dist
        target_price = entry + target_dist
    else:
        stop_price = entry + stop_dist
        target_price = entry - target_dist
    return {"side": str(side).upper(), "entry": entry, "stop": stop_price, "target": target_price}


def bracket_exit(position: dict[str, Any] | None, spot_price: float) -> str | None:
    """STOP or TARGET when the live mark reaches the stored bracket. A bad mark waits."""
    if not isinstance(position, dict):
        return None
    spot = _positive_price(spot_price)
    stop = _positive_price(position.get("stop"))
    target = _positive_price(position.get("target"))
    if spot is None or stop is None or target is None:
        return None
    side = str(position.get("side") or "").upper()
    if side == "BUY":
        if spot <= stop:
            return "STOP"
        if spot >= target:
            return "TARGET"
    elif side == "SELL":
        if spot >= stop:
            return "STOP"
        if spot <= target:
            return "TARGET"
    return None


def remember_bracket(session: Any) -> dict[str, Any] | None:
    """Store the 10/20 prices on the open tactical sleeve."""
    side_name = str(getattr(session, "tactical_side", "") or "").upper()
    if side_name == "LONG":
        side = "BUY"
    elif side_name == "SHORT":
        side = "SELL"
    else:
        return None
    entry = _positive_price(getattr(session, "tactical_entry_price", 0))
    if entry is None:
        return None
    position = bracket_prices(side, entry, load_state_schema())
    session.bracket_position = position
    return position


def clear_bracket(session: Any) -> None:
    session.bracket_position = None


class VirtueEngineV2:
    def __init__(self, broker_client, state_config):
        self.broker = broker_client
        self.config = state_config
        self.position = None
        if str(state_config.get("execution_mode") or "").lower() != "paper":
            raise ValueError("execution_mode_must_be_paper")
        if int(state_config.get("contract_size") or 0) != 1:
            raise ValueError("contract_size_must_be_1")

    def execute_tick(self, market_data_df, spot_price, gamma_flip):
        spot = _positive_price(spot_price)
        flip = _positive_price(gamma_flip)
        if spot is None or flip is None:
            logger.info("Regime inputs invalid — stand aside")
            return {"action": "HOLD", "reason": "invalid_regime_inputs"}
        regime = get_market_regime(spot, flip)

        # Check active position management (Fixed Brackets)
        if self.position:
            self.manage_brackets(spot)
            return {"action": "HOLD", "reason": "bracket_open"}
        # Check for new entry
        signal = evaluate_entry_signal(market_data_df, regime)
        if signal["action"] in ["BUY", "SELL"]:
            self.open_position(signal)
        return signal

    def open_position(self, signal):
        side = signal["action"]
        if side not in {"BUY", "SELL"} or self.position is not None:
            return
        try:
            entry_price = float(self.broker.get_current_price())
        except Exception:
            logger.exception("bracket_price_unavailable")
            return
        if entry_price <= 0:
            logger.error("bracket_price_invalid entry=%s", entry_price)
            return
        planned = bracket_prices(side, entry_price, self.config)
        stop_price = planned["stop"]
        target_price = planned["target"]
        logger.info(f"Executing {side} 1 MES at {entry_price} | Stop: {stop_price} | Target: {target_price}")

        # Submit OCO bracket order to broker API (Paper Mode)
        try:
            self.broker.submit_bracket_order(
                qty=1,
                side=side,
                entry=entry_price,
                stop_loss=stop_price,
                take_profit=target_price,
            )
        except Exception:
            logger.exception("bracket_submit_failed")
            return
        self.position = planned

    def manage_brackets(self, spot_price):
        """Watch the 10-point stop and 20-point target. Paper mode has no resting OCO."""
        if not self.position:
            return None
        hit = bracket_exit(self.position, spot_price)
        if hit is None:
            return None
        logger.info(
            "Bracket %s side=%s entry=%s spot=%s stop=%s target=%s",
            hit,
            self.position.get("side"),
            self.position.get("entry"),
            spot_price,
            self.position.get("stop"),
            self.position.get("target"),
        )
        closer = getattr(self.broker, "close_bracket", None)
        if callable(closer):
            try:
                closer(reason=hit, price=float(spot_price))
            except Exception:
                logger.exception("bracket_close_failed")
                return hit
        self.position = None
        return hit