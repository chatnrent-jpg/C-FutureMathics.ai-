"""
Daily 20-SMA regime filter (Wisdom).

Latched once per ET session from the last *completed* daily close.
The 5-second stack may only execute with-trend.

  Daily_Close > SMA(20) → BULLISH → longs only
  Daily_Close < SMA(20) → BEARISH → shorts only
  equal / insufficient / stale → UNKNOWN → no new entries (Justice)
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGIME_BULLISH = "BULLISH"
REGIME_BEARISH = "BEARISH"
REGIME_UNKNOWN = "UNKNOWN"


def sma(closes: Sequence[float], period: int = 20) -> float | None:
    """Simple moving average of the last ``period`` closes. None if too few."""
    n = int(period)
    if n <= 0:
        return None
    vals = [float(c) for c in closes if float(c) > 0]
    if len(vals) < n:
        return None
    window = vals[-n:]
    return sum(window) / float(n)


def classify_daily_sma_regime(
    daily_close: float,
    sma_mean: float | None,
    *,
    epsilon: float = 0.0,
) -> str:
    """
    Map completed daily close vs the latched SMA (default 20-day).

    Equal (within epsilon) is UNKNOWN — never invent a trend (Wisdom / Justice).
    """
    close = float(daily_close or 0.0)
    if close <= 0 or sma_mean is None:
        return REGIME_UNKNOWN
    mean = float(sma_mean)
    if mean <= 0:
        return REGIME_UNKNOWN
    if close > mean + float(epsilon):
        return REGIME_BULLISH
    if close < mean - float(epsilon):
        return REGIME_BEARISH
    return REGIME_UNKNOWN


def regime_allows_side(regime: str, side: str) -> bool:
    """Hard with-trend gate. UNKNOWN blocks both. Open trades are not this function."""
    reg = (regime or REGIME_UNKNOWN).strip().upper()
    want = (side or "").strip().upper()
    if want not in {"LONG", "SHORT"}:
        return False
    if reg == REGIME_BULLISH:
        return want == "LONG"
    if reg == REGIME_BEARISH:
        return want == "SHORT"
    return False


def parse_bar_et_date(timestamp: object) -> str:
    """Alpaca/ISO bar timestamp → YYYY-MM-DD America/New_York. Empty on failure."""
    if timestamp is None:
        return ""
    raw = str(timestamp).strip()
    if not raw:
        return ""
    try:
        text = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt.astimezone(ET).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return raw[:10] if len(raw) >= 10 and raw[4] == "-" else ""


def completed_daily_closes(
    bars: Sequence[dict],
    today_et: str,
) -> list[tuple[str, float]]:
    """
    Completed daily (date, close) pairs, oldest → newest.

    Drops today's in-progress RTH bar so the latch uses yesterday's close.
    """
    today = str(today_et or "").strip()
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for row in bars:
        if not isinstance(row, dict):
            continue
        try:
            close = float(row.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        day = parse_bar_et_date(row.get("timestamp") or row.get("t") or row.get("date"))
        if not day or (today and day >= today):
            continue
        if day in seen:
            # Last write wins (Alpaca can emit adjusted duplicates).
            out = [(d, c) for d, c in out if d != day]
        seen.add(day)
        out.append((day, close))
    out.sort(key=lambda x: x[0])
    return out


def close_date_is_fresh(
    close_date: str,
    today_et: str,
    *,
    max_age_days: int = 5,
) -> bool:
    """True when the completed daily close is this session's prior day (allow weekend/holiday)."""
    from datetime import date

    try:
        close_d = date.fromisoformat(str(close_date)[:10])
        today_d = date.fromisoformat(str(today_et)[:10])
    except ValueError:
        return False
    age = (today_d - close_d).days
    return 0 <= age <= int(max_age_days)


def latch_from_daily_bars(
    bars: Sequence[dict],
    today_et: str,
    *,
    period: int = 20,
) -> dict[str, object]:
    """
    Compute the once-per-day latch from raw daily bars.

    Returns regime, close, sma, sample count, close date.
    """
    series = completed_daily_closes(bars, today_et)
    closes = [c for _, c in series]
    mean = sma(closes, period)
    if not series or mean is None:
        return {
            "regime": REGIME_UNKNOWN,
            "daily_close": 0.0,
            "sma": None,
            "samples": len(closes),
            "close_date": "",
        }
    close_date, daily_close = series[-1]
    if not close_date_is_fresh(close_date, today_et):
        return {
            "regime": REGIME_UNKNOWN,
            "daily_close": float(daily_close),
            "sma": float(mean) if mean is not None else None,
            "samples": len(closes),
            "close_date": close_date,
        }
    regime = classify_daily_sma_regime(daily_close, mean)
    return {
        "regime": regime,
        "daily_close": float(daily_close),
        "sma": float(mean),
        "samples": len(closes),
        "close_date": close_date,
    }
