"""
New York Regular Trading Hours clock (Wisdom / Justice).

Cash session: Mon–Fri 09:30–16:00 America/New_York.
Intraday SPY bars/quotes outside that window are rejected.
Residual risk is flattened at 15:59:55 ET (RTH_MARKET_CLOSE) so nothing
rides the cash close or extended-hours print.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from engine.config import (
    VIRTUE_RTH_CLOSE_HOUR,
    VIRTUE_RTH_CLOSE_MINUTE,
    VIRTUE_RTH_FLATTEN_HOUR,
    VIRTUE_RTH_FLATTEN_MINUTE,
    VIRTUE_RTH_FLATTEN_SECOND,
    VIRTUE_RTH_OPEN_HOUR,
    VIRTUE_RTH_OPEN_MINUTE,
)

ET = ZoneInfo("America/New_York")


def _rth_open() -> time:
    return time(int(VIRTUE_RTH_OPEN_HOUR), int(VIRTUE_RTH_OPEN_MINUTE), 0)


def _rth_close() -> time:
    return time(int(VIRTUE_RTH_CLOSE_HOUR), int(VIRTUE_RTH_CLOSE_MINUTE), 0)


def _rth_flatten() -> time:
    return time(
        int(VIRTUE_RTH_FLATTEN_HOUR),
        int(VIRTUE_RTH_FLATTEN_MINUTE),
        int(VIRTUE_RTH_FLATTEN_SECOND),
    )


def et_now(now: datetime | None = None) -> datetime:
    """Normalize any stamp into America/New_York."""
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def parse_et_datetime(raw: object) -> datetime | None:
    """ISO / Alpaca timestamp → aware ET datetime. None if unparseable (Justice)."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return et_now(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return et_now(ts)


def in_rth_hours(now: datetime | None = None) -> bool:
    """
    US cash Regular Trading Hours: Mon–Fri 09:30 ≤ t < 16:00 ET.

    Session may still be open at 15:59:55 so a mark price exists for flatten.
    """
    dt = et_now(now)
    if dt.weekday() >= 5:
        return False
    return _rth_open() <= dt.time() < _rth_close()


def rth_cash_close_flatten_due(now: datetime | None = None) -> bool:
    """
    True from 15:59:55 ET through the rest of that weekday (until midnight).

    Hard-kill window before the cash close. CME Globex callers must not use this
    unless they are on the RTH/Alpaca timetable.
    """
    dt = et_now(now)
    if dt.weekday() >= 5:
        return False
    return dt.time() >= _rth_flatten()


def timestamp_in_rth(raw: object, *, include_close: bool = True) -> bool:
    """
    pandas-equivalent of ``df.between_time('09:30', '16:00')`` for one stamp.

    include_close=True keeps the 16:00:00 cash print; 16:00:00.001+ is dropped.
    Weekends and unparseable stamps are rejected (Justice).
    """
    dt = parse_et_datetime(raw)
    if dt is None:
        return False
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    if t < _rth_open():
        return False
    if include_close:
        return t <= _rth_close()
    return t < _rth_close()


def is_daily_timeframe(timeframe: str) -> bool:
    tf = str(timeframe or "").strip().lower()
    return tf in {"1day", "1d", "day", "d"}


def filter_intraday_bars_to_rth(
    bars: Sequence[dict[str, Any]],
    *,
    timeframe: str = "5Min",
) -> list[dict[str, Any]]:
    """
    Drop pre/post-market intraday bars. Daily bars are left intact
    (their timestamps are typically midnight / 04:00 ET, not the cash print).
    """
    if is_daily_timeframe(timeframe):
        return [dict(row) for row in bars if isinstance(row, dict)]
    out: list[dict[str, Any]] = []
    for row in bars:
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp") or row.get("t") or row.get("time")
        if timestamp_in_rth(ts, include_close=True):
            out.append(dict(row))
    return out
