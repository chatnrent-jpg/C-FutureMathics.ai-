"""
Completed daily-bar entries.

The signal is yesterday's daily bar against the prior day's low, midpoint, and high.
Today's unfinished session does not open a trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("futuremathics.strategy")

ET = ZoneInfo("America/New_York")
VALUE_AREA_FRACTION = 0.70


@dataclass(frozen=True)
class ValueArea:
    session_date: str
    poc: float
    val: float
    vah: float

    def valid(self) -> bool:
        return self.val < self.poc < self.vah and self.val > 0


@dataclass(frozen=True)
class BracketSignal:
    side: str  # LONG, SHORT, or FLAT
    reason: str
    order_type: str = ""
    limit_price: float | None = None


def _bar_et_date(timestamp: object) -> str:
    raw = str(timestamp or "").strip()
    if not raw:
        return ""
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    return ts.astimezone(ET).strftime("%Y-%m-%d")


def previous_session_bars(bars: Sequence[dict], today_et: str) -> tuple[str, list[dict]]:
    """Last completed session strictly before today_et. Empty if none."""
    grouped: dict[str, list[dict]] = {}
    for row in bars:
        if not isinstance(row, dict):
            continue
        day = _bar_et_date(row.get("timestamp") or row.get("t"))
        if not day or day >= str(today_et):
            continue
        grouped.setdefault(day, []).append(row)
    if not grouped:
        return "", []
    day = max(grouped)
    return day, grouped[day]


def value_area_from_bars(
    bars: Sequence[dict],
    *,
    session_date: str,
    bin_size: float = 0.25,
) -> ValueArea | None:
    """70% value area around the volume POC. None if the profile is unusable."""
    if bin_size <= 0:
        return None
    buckets: dict[float, float] = {}
    for row in bars:
        try:
            high = float(row.get("high") or 0)
            low = float(row.get("low") or 0)
            close = float(row.get("close") or 0)
            volume = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0 or close <= 0 or high < low or volume <= 0:
            continue
        typical = (high + low + close) / 3.0
        price = round(typical / bin_size) * bin_size
        buckets[price] = buckets.get(price, 0.0) + volume
    if len(buckets) < 3:
        return None
    total = sum(buckets.values())
    if total <= 0:
        return None
    prices = sorted(buckets)
    poc = max(prices, key=lambda p: buckets[p])
    target = total * VALUE_AREA_FRACTION
    lo = hi = prices.index(poc)
    covered = buckets[poc]
    while covered < target and (lo > 0 or hi < len(prices) - 1):
        left = buckets[prices[lo - 1]] if lo > 0 else -1.0
        right = buckets[prices[hi + 1]] if hi < len(prices) - 1 else -1.0
        if right >= left and hi < len(prices) - 1:
            hi += 1
            covered += buckets[prices[hi]]
        elif lo > 0:
            lo -= 1
            covered += buckets[prices[lo]]
        else:
            break
    area = ValueArea(session_date, float(poc), float(prices[lo]), float(prices[hi]))
    return area if area.valid() else None


def scale_value_area(area: ValueArea, *, spy_reference: float, mes_price: float) -> ValueArea | None:
    """Map a SPY value area onto the MES price scale used by the engine."""
    try:
        spy = float(spy_reference)
        mes = float(mes_price)
    except (TypeError, ValueError):
        return None
    if spy <= 0 or mes <= 0:
        return None
    ratio = mes / spy
    scaled = ValueArea(
        area.session_date,
        round(area.poc * ratio, 2),
        round(area.val * ratio, 2),
        round(area.vah * ratio, 2),
    )
    return scaled if scaled.valid() else None


def drop_forming_bar(
    bars: Sequence[dict],
    *,
    minutes: int = 15,
    now: datetime | None = None,
) -> list[dict]:
    """Drop the current unfinished bar so a live wick cannot look like a closed rejection."""
    rows = [row for row in bars if isinstance(row, dict)]
    if not rows:
        return []
    raw = str(rows[-1].get("timestamp") or rows[-1].get("t") or "").strip()
    if not raw:
        return rows
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return rows
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    clock = now or datetime.now(ET)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=ET)
    if ts.astimezone(ET) + timedelta(minutes=int(minutes)) > clock.astimezone(ET):
        return rows[:-1]
    return rows


def bar_et_date(timestamp: object) -> str:
    """ET calendar date for a bar timestamp."""
    return _bar_et_date(timestamp)


def completed_daily_bars(bars: Sequence[dict], today_et: str) -> list[dict]:
    """Completed daily bars only. Today's unfinished session is excluded."""
    out: list[dict] = []
    for row in bars:
        if not isinstance(row, dict):
            continue
        day = _bar_et_date(row.get("timestamp") or row.get("t"))
        if day and day < str(today_et):
            out.append(row)
    return out


def daily_reference_area(bar: dict, session_date: str) -> ValueArea | None:
    """Prior completed day: low, midpoint, high. No intraday profile."""
    try:
        high = float(bar["high"])
        low = float(bar["low"])
    except (KeyError, TypeError, ValueError):
        return None
    if high <= low or low <= 0:
        return None
    area = ValueArea(str(session_date), round((high + low) / 2.0, 2), round(low, 2), round(high, 2))
    return area if area.valid() else None


def session_bars(bars: Sequence[dict], session_date: str) -> list[dict]:
    """Bars whose ET date equals session_date."""
    want = str(session_date)
    out: list[dict] = []
    for row in bars:
        if isinstance(row, dict) and _bar_et_date(row.get("timestamp") or row.get("t")) == want:
            out.append(row)
    return out


def scale_ohlc_bars(bars: Sequence[dict], *, spy_reference: float, mes_price: float) -> list[dict]:
    """Put SPY OHLC on the same MES scale as the value area."""
    try:
        spy = float(spy_reference)
        mes = float(mes_price)
    except (TypeError, ValueError):
        return []
    if spy <= 0 or mes <= 0:
        return []
    ratio = mes / spy
    out: list[dict] = []
    for row in bars:
        try:
            out.append(
                {
                    "high": float(row["high"]) * ratio,
                    "low": float(row["low"]) * ratio,
                    "close": float(row["close"]) * ratio,
                    "timestamp": row.get("timestamp") or row.get("t"),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def entry_bars_frame(bars: Sequence[dict], area: ValueArea) -> pd.DataFrame:
    """Completed daily rows with the prior day's low, midpoint, and high."""
    rows: list[dict[str, float]] = []
    for row in bars:
        try:
            rows.append(
                {
                    "close": float(row["close"]),
                    "low": float(row["low"]),
                    "high": float(row["high"]),
                    "val": float(area.val),
                    "vah": float(area.vah),
                    "poc": float(area.poc),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return pd.DataFrame(rows)


def evaluate_entry_signal(df: pd.DataFrame, gamma_regime: str) -> dict:
    """
    Evaluates the last completed daily bar against the prior day's range.
    """
    if df.empty or len(df) < 2:
        return {"action": "HOLD", "reason": "Insufficient data"}

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    close = latest["close"]
    low = latest["low"]
    high = latest["high"]
    val = latest["val"]  # Value Area Low
    vah = latest["vah"]  # Value Area High
    poc = latest["poc"]  # Point of Control

    # Rule 1: Positive Gamma -> Mean reversion at Value Area extremes
    if gamma_regime in {"POSITIVE_GAMMA", "MEAN_REVERT"}:
        # Long entry: Price dipped below VAL and rejected back inside
        if low <= val and close > val:
            logger.info(f"Signal Generated: Long bounce off VAL ({val})")
            return {"action": "BUY", "order_type": "LIMIT", "price": val, "reason": "VAL_REJECTION"}

        # Short entry: Price poked above VAH and rejected back inside
        elif high >= vah and close < vah:
            logger.info(f"Signal Generated: Short rejection at VAH ({vah})")
            return {"action": "SELL", "order_type": "LIMIT", "price": vah, "reason": "VAH_REJECTION"}

    # Rule 2: Negative Gamma -> Breakout / POC continuation
    elif gamma_regime in {"NEGATIVE_GAMMA", "MOMENTUM"}:
        if prev["close"] < poc and close > poc:
            logger.info(f"Signal Generated: POC Momentum Breakout Long ({poc})")
            return {"action": "BUY", "order_type": "MARKET", "reason": "POC_MOMENTUM"}

    return {"action": "HOLD", "reason": "No structural setup"}


def bracket_from_entry(signal: dict) -> BracketSignal:
    """Map BUY/SELL/HOLD onto the engine's LONG/SHORT/FLAT book."""
    action = str(signal.get("action") or "HOLD").upper()
    reason = str(signal.get("reason") or "No structural setup")
    order_type = str(signal.get("order_type") or "")
    raw_px = signal.get("price")
    try:
        limit = float(raw_px) if raw_px is not None else None
    except (TypeError, ValueError):
        limit = None
    if action == "BUY":
        return BracketSignal("LONG", reason, order_type, limit)
    if action == "SELL":
        return BracketSignal("SHORT", reason, order_type, limit)
    return BracketSignal("FLAT", reason, order_type, limit)
