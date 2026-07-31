"""
Databento CME Globex MES L1 feed (primary overnight-capable market data).

Streams GLBX.MDP3 mbp-1 for MES.FUT (parent) into a thread-safe quote cache.
Never fabricates prices — unavailable/stale returns None (Justice).
"""

from __future__ import annotations

import asyncio
import logging
import os
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
DEFAULT_MAX_QUOTE_AGE_S = 5.0


@dataclass
class DatabentoMESFeed:
    """Live MES top-of-book via Databento Live API."""

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

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("DATABENTO_API_KEY", "").strip()
        if self.is_configured():
            logger.info(
                "Databento MES feed configured dataset=%s schema=%s symbols=%s",
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
            "Databento subscribed dataset=%s schema=%s symbols=%s",
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

    def _handle_record(self, record: Any) -> None:
        import databento as db

        if isinstance(record, db.SymbolMappingMsg):
            sym = str(getattr(record, "stype_out_symbol", "") or getattr(record, "stype_in_symbol", "") or "")
            if sym:
                with self._lock:
                    self._raw_symbol = sym
            return

        if not isinstance(record, db.MBP1Msg):
            return

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
        size = int(getattr(level0, "ask_sz", 0) or getattr(level0, "bid_sz", 0) or 0)
        # Prefer exchange event time; fall back to recv / wall clock.
        ts_ns = int(getattr(record, "ts_event", 0) or getattr(record, "ts_recv", 0) or 0)
        if ts_ns > 0:
            ts_epoch = ts_ns / 1e9
        else:
            ts_epoch = time.time()

        with self._lock:
            self._bid = bid
            self._ask = ask
            self._mid = mid
            self._size = size
            self._ts_epoch = ts_epoch
            self._sequence += 1
            self._connected = True

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
                return True, f"databento_ok mes={quote['price']:.2f} src={quote.get('symbol')}"
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
        """Historical OHLCV for Wisdom warmup (Justice: empty on failure)."""
        if not self.is_configured():
            return []
        try:
            import databento as db

            schema = "ohlcv-1m" if timeframe in {"1m", "1Min", "1min"} else "ohlcv-1m"
            # Historical availability lags wall clock — keep end inside published range.
            end = datetime.now(timezone.utc) - timedelta(minutes=20)
            start = end - timedelta(hours=max(1, int(lookback_hours)))
            client = db.Historical(key=self.api_key)
            data = await asyncio.to_thread(
                client.timeseries.get_range,
                dataset=self.dataset,
                schema=schema,
                symbols=self.symbols,
                stype_in=self.stype_in,
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
                        # Fallback fixed-point scale if pretty_* missing
                        scale = 1e-9
                        close = float(getattr(rec, "close", 0) or 0) * scale
                        high = float(getattr(rec, "high", 0) or 0) * scale
                        low = float(getattr(rec, "low", 0) or 0) * scale
                        open_ = float(getattr(rec, "open", 0) or 0) * scale
                    if close <= 0:
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
                out.append(
                    {
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    @property
    def last_price(self) -> float:
        with self._lock:
            return float(self._mid)


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
                f"bid={q['bid']:.2f} ask={q['ask']:.2f} age={q.get('quote_age_s'):.2f}s"
            )
        else:
            print("waiting for quote...")
        await asyncio.sleep(1.0)
    feed.stop()


if __name__ == "__main__":
    asyncio.run(smoke_databento_mes())
