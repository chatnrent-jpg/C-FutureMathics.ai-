"""
Daily swing policy (Wisdom / Temperance).

Direction from completed daily close vs 20-SMA.
Entry is a pullback to session VWAP after 10:00 ET — not a 5-second poke.
Stop is 1.5× daily ATR in MES dollars. Hold overnight; do not 15:59-flatten.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

SWING_ATR_PERIOD = 14
SWING_ATR_STOP_MULT = 1.5
SWING_STOP_DOLLARS_MIN = 100.0
SWING_STOP_DOLLARS_MAX = 500.0  # absolute clip; also min()'d with HARD_DAILY_STOP
# Pullback band: at/near value. Extended scores are chases; collapsed scores are breakdowns.
SWING_LONG_VWAP_MIN = 48.0
SWING_LONG_VWAP_MAX = 62.0
SWING_SHORT_VWAP_MIN = 38.0
SWING_SHORT_VWAP_MAX = 52.0


def wilder_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = SWING_ATR_PERIOD,
) -> float | None:
    """Wilder ATR from completed OHLC. None if too few bars (Justice)."""
    n = int(period)
    if n <= 0:
        return None
    h = [float(x) for x in highs]
    l = [float(x) for x in lows]
    c = [float(x) for x in closes]
    if not (len(h) == len(l) == len(c)) or len(c) < n + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(c)):
        prev = c[i - 1]
        tr = max(h[i] - l[i], abs(h[i] - prev), abs(l[i] - prev))
        if tr <= 0:
            return None
        trs.append(tr)
    if len(trs) < n:
        return None
    atr = sum(trs[:n]) / float(n)
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / float(n)
    return atr if atr > 0 else None


def atr_from_completed_daily_bars(
    bars: Sequence[dict],
    today_et: str,
    period: int = SWING_ATR_PERIOD,
) -> float | None:
    """ATR on last completed daily sessions only (drops today's in-progress bar)."""
    from engine.regime_filter import completed_daily_closes, parse_bar_et_date

    done = {d for d, _ in completed_daily_closes(bars, today_et)}
    rows: list[dict] = []
    last_for_day: dict[str, dict] = {}
    for row in bars:
        if not isinstance(row, dict):
            continue
        day = parse_bar_et_date(row.get("timestamp") or row.get("t") or row.get("date"))
        if day in done:
            last_for_day[day] = row
    for day in sorted(last_for_day):
        rows.append(last_for_day[day])
    return atr_from_daily_bars(rows, period=period)


def atr_from_daily_bars(bars: Sequence[dict], period: int = SWING_ATR_PERIOD) -> float | None:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in bars:
        if not isinstance(row, dict):
            continue
        try:
            hi = float(row.get("high") or 0.0)
            lo = float(row.get("low") or 0.0)
            cl = float(row.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        if hi <= 0 or lo <= 0 or cl <= 0 or hi < lo:
            continue
        highs.append(hi)
        lows.append(lo)
        closes.append(cl)
    return wilder_atr(highs, lows, closes, period=period)


def mes_atr_points(*, atr_spy: float, spy_close: float, mes_price: float) -> float | None:
    """Map SPY daily ATR into MES points via the live proxy ratio."""
    try:
        a = float(atr_spy)
        spy = float(spy_close)
        mes = float(mes_price)
    except (TypeError, ValueError):
        return None
    if a <= 0 or spy <= 0 or mes <= 0:
        return None
    return a * (mes / spy)


def swing_stop_dollars(
    *,
    atr_spy: float | None,
    spy_close: float,
    mes_price: float,
    point_value: float,
) -> float:
    """1.5× daily ATR in USD per MES, clipped (Temperance)."""
    pts = mes_atr_points(
        atr_spy=float(atr_spy or 0.0), spy_close=spy_close, mes_price=mes_price
    )
    if pts is None:
        return float(SWING_STOP_DOLLARS_MIN)
    raw = float(pts) * float(SWING_ATR_STOP_MULT) * float(point_value)
    try:
        from engine.config import HARD_DAILY_STOP

        hard = float(HARD_DAILY_STOP)
    except Exception:
        hard = float(SWING_STOP_DOLLARS_MAX)
    hi = float(SWING_STOP_DOLLARS_MAX)
    if hard > 0:
        hi = min(hi, hard)
    return max(float(SWING_STOP_DOLLARS_MIN), min(hi, round(raw, 2)))


def swing_pullback_ok(
    side: str,
    *,
    vwap_score: float,
    price: float,
    vwap: float,
) -> bool:
    """True when this is a value pullback, not a chase or a breakdown."""
    want = str(side or "").strip().upper()
    try:
        score = float(vwap_score)
        px = float(price)
        anchor = float(vwap)
    except (TypeError, ValueError):
        return False
    if px <= 0 or anchor <= 0:
        return False
    if want == "LONG":
        if px < anchor:
            return False
        return float(SWING_LONG_VWAP_MIN) <= score <= float(SWING_LONG_VWAP_MAX)
    if want == "SHORT":
        if px > anchor:
            return False
        return float(SWING_SHORT_VWAP_MIN) <= score <= float(SWING_SHORT_VWAP_MAX)
    return False


def swing_entry_window_open(now: datetime | None = None) -> bool:
    """New swing entries: Mon–Fri 10:00 ≤ t < 15:30 ET."""
    from engine.config import (
        VIRTUE_SWING_ENTRY_END_HOUR,
        VIRTUE_SWING_ENTRY_END_MINUTE,
        VIRTUE_SWING_ENTRY_HOUR,
        VIRTUE_SWING_ENTRY_MINUTE,
    )

    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    else:
        dt = dt.astimezone(ET)
    if dt.weekday() >= 5:
        return False
    start = time(int(VIRTUE_SWING_ENTRY_HOUR), int(VIRTUE_SWING_ENTRY_MINUTE), 0)
    end = time(int(VIRTUE_SWING_ENTRY_END_HOUR), int(VIRTUE_SWING_ENTRY_END_MINUTE), 0)
    return start <= dt.time() < end


def swing_entry_side(
    htf_regime: str,
    *,
    vwap_score: float,
    price: float,
    vwap: float,
) -> str:
    """LONG or SHORT when daily SMA and session-VWAP pullback agree; else ''."""
    htf = str(htf_regime or "").strip().upper()
    if htf == "BULLISH":
        want = "LONG"
    elif htf == "BEARISH":
        want = "SHORT"
    else:
        return ""
    if swing_pullback_ok(want, vwap_score=vwap_score, price=price, vwap=vwap):
        return want
    return ""


def swing_regime_flip_exit(side: str, htf_regime: str) -> bool:
    """True when a held swing is now against the completed daily SMA (Wisdom)."""
    have = str(side or "").strip().upper()
    htf = str(htf_regime or "").strip().upper()
    if have == "LONG" and htf == "BEARISH":
        return True
    if have == "SHORT" and htf == "BULLISH":
        return True
    return False
