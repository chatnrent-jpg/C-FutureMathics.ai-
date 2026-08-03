"""
Databento CME Globex MES L1 feed (primary overnight-capable market data).

Streams GLBX.MDP3 mbp-1 for MES.FUT (parent) into a thread-safe quote cache,
but ONLY the front-month outright — never calendar spreads or back months.

Never fabricates prices — unavailable/stale returns None (Justice).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATASET = "GLBX.MDP3"
SCHEMA = "mbp-1"
SYMBOLS = "MES.FUT"
STYPE_IN = "parent"
# Continuous front for historical warmup (single series, no spread mix).
CONTINUOUS_SYMBOL = "MES.c.0"
CONTINUOUS_STYPE = "continuous"
DEFAULT_MAX_QUOTE_AGE_S = 5.0

# Justice: MES outrights trade in the thousands; spreads print dozens/hundreds.
MES_MIN_SANE_PRICE = 1000.0
MES_MAX_SANE_PRICE = 20000.0

# CME month codes → month number
_MONTH_CODE = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}
_OUTRIGHT_RE = re.compile(r"^MES([FGHJKMNQUVXZ])(\d)$", re.IGNORECASE)


def is_mes_outright(symbol: str) -> bool:
    """True for MESU6 / MESZ6 style outrights; False for spreads (MESU6-MESZ6) or junk."""
    return bool(_OUTRIGHT_RE.match((symbol or "").strip().upper()))


def mes_expiry_rank(symbol: str) -> tuple[int, int] | None:
    """
    Sort key for MES outrights: (year, month). Smaller = nearer / front.
    Year digit: 6 → 2026, 7 → 2027 (CME single-digit year in root+month+year).
    """
    m = _OUTRIGHT_RE.match((symbol or "").strip().upper())
    if not m:
        return None
    month = _MONTH_CODE.get(m.group(1).upper())
    if month is None:
        return None
    year = 2020 + int(m.group(2))
    return year, month


def is_sane_mes_price(px: float) -> bool:
    try:
        v = float(px)
    except (TypeError, ValueError):
        return False
    return MES_MIN_SANE_PRICE <= v <= MES_MAX_SANE_PRICE


@dataclass
class DatabentoMESFeed:
    """Live MES top-of-book via Databento Live API (front-month outright only)."""

    api_key: str = field(default="")
    max_quote_age_s: float = DEFAULT_MAX_QUOTE_AGE_S
    dataset: str = DATASET
    schema: str = SCHEMA
    symbols: str = SYMBOLS
    stype_in: str = STYPE_IN

    _bid: float = 0.0
    _ask: float = 0.0
    _mid: float = 0.0
    _size: int = 0
    _ts_epoch: float = 0.0
    _raw_symbol: str = "MES"
    _sequence: int = 0
    _connected: bool = False
    _last_error: str = ""
    _started: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    # instrument_id → raw symbol (from SymbolMappingMsg)
    _id_to_symbol: dict[int, str] = field(default_factory=dict, repr=False)
    # Locked front-month outright (e.g. MESU6); None until first mapping resolves
    _front_symbol: str | None = None
    _front_instrument_id: int | None = None
    _rejected_non_front: int = 0

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("DATABENTO_API_KEY", "").strip()
        if self.is_configured():
            logger.info(
                "Databento MES feed configured dataset=%s schema=%s symbols=%s "
                "(front-month outright filter ON)",
                self.dataset,
                self.schema,
                self.symbols,
            )
        else:
            logger.warning("DATABENTO_API_KEY not set — CME MES feed unavailable")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def ensure_started(self) -> None:
        """Start background live stream once (idempotent)."""
        if not self.is_configured() or self._started:
            return
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_live_loop,
                name="databento-mes-live",
                daemon=True,
            )
            self._started = True
            self._thread.start()
            logger.info("Databento MES live thread started")

    def stop(self) -> None:
        self._stop.set()
        self._connected = False

    def _run_live_loop(self) -> None:
        """Reconnect loop — never raises out of the thread."""
        backoff = 2.0
        while not self._stop.is_set():
            try:
                self._stream_once()
                backoff = 2.0
            except Exception as exc:
                self._connected = False
                self._last_error = str(exc)[:160]
                logger.exception("databento_live_stream_failed err=%s", exc)
            if self._stop.is_set():
                break
            time.sleep(min(backoff, 60.0))
            backoff = min(backoff * 1.5, 60.0)

    def _stream_once(self) -> None:
        import databento as db

        client = db.Live(key=self.api_key)
        client.subscribe(
            dataset=self.dataset,
            schema=self.schema,
            symbols=self.symbols,
            stype_in=self.stype_in,
        )
        self._connected = True
        self._last_error = ""
        logger.info(
            "Databento subscribed dataset=%s schema=%s symbols=%s front_filter=outright",
            self.dataset,
            self.schema,
            self.symbols,
        )
        for record in client:
            if self._stop.is_set():
                try:
                    client.stop()
                except Exception:
                    pass
                break
            try:
                self._handle_record(record)
            except Exception as exc:
                logger.warning("databento_record_handle_failed err=%s", exc)

    def _maybe_set_front(self, symbol: str, instrument_id: int | None) -> None:
        """Choose nearest-expiry outright as the only quote source."""
        if not is_mes_outright(symbol):
            return
        rank = mes_expiry_rank(symbol)
        if rank is None:
            return
        with self._lock:
            current = self._front_symbol
            if current is None:
                self._front_symbol = symbol.upper()
                self._front_instrument_id = instrument_id
                self._raw_symbol = self._front_symbol
                logger.info(
                    "Databento front-month locked symbol=%s instrument_id=%s",
                    self._front_symbol,
                    self._front_instrument_id,
                )
                return
            cur_rank = mes_expiry_rank(current)
            if cur_rank is not None and rank < cur_rank:
                # A nearer outright appeared (or first mapping order was back-month).
                self._front_symbol = symbol.upper()
                self._front_instrument_id = instrument_id
                self._raw_symbol = self._front_symbol
                logger.info(
                    "Databento front-month upgraded symbol=%s instrument_id=%s",
                    self._front_symbol,
                    self._front_instrument_id,
                )

    def _handle_record(self, record: Any) -> None:
        import databento as db

        if isinstance(record, db.SymbolMappingMsg):
            sym = str(
                getattr(record, "stype_out_symbol", "")
                or getattr(record, "stype_in_symbol", "")
                or ""
            ).strip()
            iid = getattr(record, "instrument_id", None)
            try:
                iid_i = int(iid) if iid is not None else None
            except (TypeError, ValueError):
                iid_i = None
            if sym and iid_i is not None:
                with self._lock:
                    self._id_to_symbol[iid_i] = sym.upper()
            if sym:
                self._maybe_set_front(sym, iid_i)
            return

        if not isinstance(record, db.MBP1Msg):
            return

        iid = getattr(record, "instrument_id", None)
        try:
            iid_i = int(iid) if iid is not None else None
        except (TypeError, ValueError):
            iid_i = None

        with self._lock:
            front_sym = self._front_symbol
            front_id = self._front_instrument_id
            id_map = dict(self._id_to_symbol)

        # Resolve symbol for this tick
        sym = id_map.get(iid_i or -1, "")
        if front_id is not None and iid_i is not None and iid_i != front_id:
            with self._lock:
                self._rejected_non_front += 1
                n = self._rejected_non_front
            if n in (1, 10, 100) or n % 500 == 0:
                logger.info(
                    "databento_skip_non_front n=%s got_id=%s front_id=%s sym=%s",
                    n,
                    iid_i,
                    front_id,
                    sym or "?",
                )
            return
        if front_sym and sym and sym.upper() != front_sym.upper():
            # Front locked by symbol but id not yet known — still reject mismatches.
            if is_mes_outright(sym) or ("-" in sym):
                with self._lock:
                    self._rejected_non_front += 1
                return
        if front_sym is None:
            # No front yet: only accept a sane outright; lock it from the first good tick.
            if not sym or not is_mes_outright(sym):
                return
            self._maybe_set_front(sym, iid_i)
            with self._lock:
                front_sym = self._front_symbol

        levels = getattr(record, "levels", None) or ()
        if not levels:
            return
        level0 = levels[0]
        bid = float(getattr(level0, "pretty_bid_px", 0.0) or 0.0)
        ask = float(getattr(level0, "pretty_ask_px", 0.0) or 0.0)
        if bid <= 0 and ask <= 0:
            return
        if bid <= 0:
            bid = ask
        if ask <= 0:
            ask = bid
        mid = round((bid + ask) / 2.0, 2)
        # Justice: reject spreads / garbage that slip through mapping gaps.
        if not is_sane_mes_price(mid) or not is_sane_mes_price(bid) or not is_sane_mes_price(ask):
            logger.warning(
                "databento_reject_insane_price mid=%.4f bid=%.4f ask=%.4f sym=%s — Justice",
                mid,
                bid,
                ask,
                sym or front_sym or "?",
            )
            return
        # Reject crossed / absurd width (spread quotes sometimes survive)
        if ask < bid or (ask - bid) > 50.0:
            return

        size = int(getattr(level0, "ask_sz", 0) or getattr(level0, "bid_sz", 0) or 0)
        ts_ns = int(getattr(record, "ts_event", 0) or getattr(record, "ts_recv", 0) or 0)
        ts_epoch = (ts_ns / 1e9) if ts_ns > 0 else time.time()

        with self._lock:
            self._bid = bid
            self._ask = ask
            self._mid = mid
            self._size = size
            self._ts_epoch = ts_epoch
            self._sequence += 1
            self._connected = True
            if front_sym:
                self._raw_symbol = front_sym
            if self._front_instrument_id is None and iid_i is not None:
                self._front_instrument_id = iid_i

    def quote_age_seconds(self) -> float | None:
        with self._lock:
            if self._ts_epoch <= 0:
                return None
            return max(0.0, time.time() - self._ts_epoch)

    def get_cached_quote(self, *, max_age_s: float | None = None) -> dict[str, Any] | None:
        """
        Return fresh L1 quote or None if missing/stale (Justice).
        Does not invent prices from old cache beyond max_age_s.
        Does not start the live thread — call ensure_started()/fetch_quote() for that.
        """
        ceiling = float(self.max_quote_age_s if max_age_s is None else max_age_s)
        with self._lock:
            mid = float(self._mid)
            bid = float(self._bid)
            ask = float(self._ask)
            ts = float(self._ts_epoch)
            seq = int(self._sequence)
            sym = str(self._raw_symbol or "MES")
            size = int(self._size)
        if mid <= 0 or bid <= 0 or ask <= 0 or ts <= 0:
            return None
        if not is_sane_mes_price(mid):
            return None
        age = max(0.0, time.time() - ts)
        if age > ceiling:
            logger.error(
                "databento_mes_quote_stale age_s=%.2f max=%.2f — Justice reject",
                age,
                ceiling,
            )
            return None
        return {
            "symbol": sym,
            "price": mid,
            "last": mid,
            "bid": bid,
            "ask": ask,
            "size": size,
            "latency_ms": round(age * 1000.0, 1),
            "sequence_id": seq,
            "source": "databento_mes",
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "quote_age_s": age,
        }

    async def fetch_quote(self, *, max_age_s: float | None = None) -> dict[str, Any] | None:
        """Async wrapper — waits briefly for first tick after start."""
        if not self.is_configured():
            return None
        self.ensure_started()
        quote = self.get_cached_quote(max_age_s=max_age_s)
        if quote:
            return quote
        # Allow cold-start: wait up to ~3s for first MBP-1
        for _ in range(30):
            await asyncio.sleep(0.1)
            quote = self.get_cached_quote(max_age_s=max_age_s)
            if quote:
                return quote
        return None

    async def health_check(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "databento_not_configured"
        try:
            self.ensure_started()
            quote = await self.fetch_quote(max_age_s=max(self.max_quote_age_s, 15.0))
            if quote and float(quote.get("price") or 0) > 0:
                return True, (
                    f"databento_ok mes={quote['price']:.2f} src={quote.get('symbol')} "
                    f"front={self._front_symbol or '?'}"
                )
            err = self._last_error or "no_fresh_quote"
            return False, f"databento_waiting:{err}"
        except Exception as exc:
            logger.exception("databento_health_check_failed err=%s", exc)
            return False, f"databento_error:{str(exc)[:80]}"

    async def fetch_ohlcv_bars(
        self,
        *,
        timeframe: str = "1m",
        limit: int = 120,
        lookback_hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Historical OHLCV for Wisdom warmup — continuous front (no spread mix)."""
        if not self.is_configured():
            return []
        try:
            import databento as db

            schema = "ohlcv-1m" if timeframe in {"1m", "1Min", "1min"} else "ohlcv-1m"
            end = datetime.now(timezone.utc) - timedelta(minutes=20)
            start = end - timedelta(hours=max(1, int(lookback_hours)))
            client = db.Historical(key=self.api_key)

            async def _pull(symbols: str, stype_in: str) -> list[dict[str, Any]]:
                data = await asyncio.to_thread(
                    client.timeseries.get_range,
                    dataset=self.dataset,
                    schema=schema,
                    symbols=symbols,
                    stype_in=stype_in,
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
                rows: list[dict[str, Any]] = []
                for rec in data:
                    try:
                        close = float(getattr(rec, "pretty_close", 0) or 0)
                        high = float(getattr(rec, "pretty_high", 0) or 0)
                        low = float(getattr(rec, "pretty_low", 0) or 0)
                        open_ = float(getattr(rec, "pretty_open", 0) or 0)
                        if close <= 0:
                            scale = 1e-9
                            close = float(getattr(rec, "close", 0) or 0) * scale
                            high = float(getattr(rec, "high", 0) or 0) * scale
                            low = float(getattr(rec, "low", 0) or 0) * scale
                            open_ = float(getattr(rec, "open", 0) or 0) * scale
                        if not is_sane_mes_price(close):
                            continue
                        rows.append(
                            {
                                "open": open_,
                                "high": high if high > 0 else close,
                                "low": low if low > 0 else close,
                                "close": close,
                                "timestamp": getattr(rec, "ts_event", None),
                            }
                        )
                    except Exception:
                        continue
                return rows

            # Prefer continuous front-month series (single clean path).
            rows = await _pull(CONTINUOUS_SYMBOL, CONTINUOUS_STYPE)
            if not rows:
                logger.warning(
                    "databento_ohlcv continuous empty — falling back to parent %s",
                    self.symbols,
                )
                rows = await _pull(self.symbols, self.stype_in)
                # Parent may still mix — keep only sane MES price levels.
                rows = [r for r in rows if is_sane_mes_price(float(r.get("close") or 0))]
            if limit and len(rows) > limit:
                rows = rows[-int(limit) :]
            return rows
        except Exception as exc:
            logger.exception("databento_ohlcv_failed err=%s", exc)
            return []

    def bars_as_strategy_ohlc(self, bars: list[dict[str, Any]]) -> list[dict[str, float]]:
        out: list[dict[str, float]] = []
        for row in bars:
            try:
                close = float(row["close"])
                if not is_sane_mes_price(close):
                    continue
                out.append(
                    {
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": close,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    @property
    def last_price(self) -> float:
        with self._lock:
            return float(self._mid)

    @property
    def front_symbol(self) -> str | None:
        with self._lock:
            return self._front_symbol


async def smoke_databento_mes(*, seconds: float = 8.0) -> None:
    """Manual smoke: print a few live MES quotes."""
    from engine.env_loader import load_project_env

    load_project_env(Path(__file__).resolve().parent.parent)
    feed = DatabentoMESFeed()
    print(f"configured={feed.is_configured()}")
    if not feed.is_configured():
        print("Set DATABENTO_API_KEY in .env.local")
        return
    ok, msg = await feed.health_check()
    print(f"health={'OK' if ok else 'FAIL'} {msg}")
    deadline = time.time() + max(1.0, float(seconds))
    while time.time() < deadline:
        q = await feed.fetch_quote(max_age_s=30.0)
        if q:
            print(
                f"MES {q.get('symbol')} mid={q['price']:.2f} "
                f"bid={q['bid']:.2f} ask={q['ask']:.2f} age={q.get('quote_age_s'):.2f}s "
                f"front={feed.front_symbol}"
            )
        else:
            print("waiting for quote...")
        await asyncio.sleep(1.0)
    feed.stop()


if __name__ == "__main__":
    asyncio.run(smoke_databento_mes())
