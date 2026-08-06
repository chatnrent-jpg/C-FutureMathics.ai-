"""
Alpaca SPY real-time feed as MES signal proxy.

Uses your existing Alpaca market data subscription to feed SPY prices,
which are then scaled to approximate MES futures movements.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Auto-load .env files if running standalone
if __name__ == "__main__":
    from engine.env_loader import load_project_env
    load_project_env(Path(__file__).resolve().parent.parent)

# SPY tracks S&P 500 at ~1/10th the index value
# MES tracks S&P 500 at 5x the index value  
# Approximate scaling: MES ≈ SPY × 12.5 (this gets refined in production)
SPY_TO_MES_SCALE = 12.5


@dataclass
class AlpacaSPYFeed:
    """Real-time SPY feed via Alpaca WebSocket or REST API."""
    
    api_key: str = field(default="")
    api_secret: str = field(default="")
    base_url: str = field(default="https://paper-api.alpaca.markets")
    data_url: str = field(default="https://data.alpaca.markets")
    
    _last_spy_price: float = 500.0  # Typical SPY price
    _last_mes_proxy: float = 6250.0  # SPY × 12.5
    _connected: bool = False
    _sequence: int = 0
    
    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("ALPACA_API_KEY", "").strip()
        if not self.api_secret:
            self.api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        
        # Check if using live or paper
        if os.getenv("ALPACA_LIVE", "").strip().lower() in ("1", "true"):
            self.base_url = "https://api.alpaca.markets"
        
        if self.api_key and self.api_secret:
            logger.info("Alpaca SPY feed initialized (data_url=%s)", self.data_url)
        else:
            logger.warning("Alpaca credentials not set - SPY feed unavailable")
    
    def is_configured(self) -> bool:
        """Check if Alpaca credentials are available."""
        return bool(self.api_key and self.api_secret)
    
    async def health_check(self) -> tuple[bool, str]:
        """Verify Alpaca API connectivity."""
        if not self.is_configured():
            return False, "alpaca_not_configured"
        
        try:
            # Simple REST API health check
            import aiohttp
            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            }
            
            async with aiohttp.ClientSession() as session:
                # Get latest SPY quote
                url = f"{self.data_url}/v2/stocks/SPY/quotes/latest"
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "quote" in data:
                            self._connected = True
                            return True, f"alpaca_ok spy={data['quote'].get('ap', 'N/A')}"
                    return False, f"alpaca_status_{resp.status}"
        except Exception as e:
            logger.error("Alpaca health check failed: %s", e)
            return False, f"alpaca_error: {str(e)[:50]}"
    
    async def fetch_spy_quote(self) -> dict[str, Any] | None:
        """Fetch latest SPY quote via REST API."""
        if not self.is_configured():
            return None
        
        try:
            import aiohttp
            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            }
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.data_url}/v2/stocks/SPY/quotes/latest"
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status != 200:
                        logger.warning("Alpaca SPY quote status %s", resp.status)
                        return None
                    
                    data = await resp.json()
                    quote = data.get("quote", {})
                    
                    if not quote:
                        return None
                    
                    # Extract bid/ask/last + quote sizes for VWAP weight
                    bid_price = float(quote.get("bp", 0) or 0)
                    ask_price = float(quote.get("ap", 0) or 0)
                    bid_size = float(quote.get("bs", 0) or 0)
                    ask_size = float(quote.get("as", 0) or 0)

                    # Use mid-price as "last" if no trade price available
                    last_price = (bid_price + ask_price) / 2 if bid_price and ask_price else 0

                    if last_price <= 0:
                        return None

                    # Prefer latest trade size when quote sizes are empty.
                    trade_size = 0.0
                    try:
                        turl = f"{self.data_url}/v2/stocks/SPY/trades/latest"
                        async with session.get(
                            turl, headers=headers, timeout=aiohttp.ClientTimeout(total=2)
                        ) as tresp:
                            if tresp.status == 200:
                                tdata = await tresp.json()
                                trade = tdata.get("trade") or {}
                                trade_size = float(trade.get("s", 0) or 0)
                                tp = float(trade.get("p", 0) or 0)
                                if tp > 0:
                                    last_price = tp
                    except Exception:
                        trade_size = 0.0

                    spy_size = max(trade_size, bid_size + ask_size, 1.0)

                    self._last_spy_price = last_price
                    self._last_mes_proxy = round(last_price * SPY_TO_MES_SCALE, 2)
                    self._sequence += 1

                    return {
                        "spy_bid": bid_price,
                        "spy_ask": ask_price,
                        "spy_last": last_price,
                        "spy_size": spy_size,
                        "timestamp": quote.get("t", datetime.now().isoformat()),
                    }
        except Exception as e:
            logger.error("Failed to fetch SPY quote: %s", e)
            return None

    @staticmethod
    def quote_age_seconds(spy_quote: dict[str, Any] | None) -> float | None:
        """Seconds since quote timestamp; None if unparseable."""
        if not spy_quote:
            return None
        raw = spy_quote.get("timestamp")
        if not raw:
            return None
        try:
            from datetime import timezone

            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return None

    def scale_spy_to_mes(self, spy_price: float) -> float:
        """Convert SPY price to approximate MES futures price."""
        return round(spy_price * SPY_TO_MES_SCALE, 2)
    
    def get_mes_proxy_tick(self, spy_quote: dict[str, Any] | None) -> dict[str, Any]:
        """Convert SPY quote to MES-compatible tick format."""
        if not spy_quote:
            # Return last known value
            return {
                "symbol": "MES_PROXY",
                "price": self._last_mes_proxy,
                "last": self._last_mes_proxy,
                "bid": round(self._last_mes_proxy - 0.25, 2),
                "ask": round(self._last_mes_proxy + 0.25, 2),
                "size": 1,
                "latency_ms": 0,
                "sequence_id": self._sequence,
                "source": "alpaca_spy_proxy",
                "spy_price": self._last_spy_price,
            }
        
        # Scale SPY to MES
        mes_price = self.scale_spy_to_mes(spy_quote["spy_last"])
        mes_bid = self.scale_spy_to_mes(spy_quote["spy_bid"])
        mes_ask = self.scale_spy_to_mes(spy_quote["spy_ask"])
        
        self._last_spy_price = spy_quote["spy_last"]
        self._last_mes_proxy = mes_price
        
        return {
            "symbol": "MES_PROXY",
            "price": mes_price,
            "last": mes_price,
            "bid": mes_bid,
            "ask": mes_ask,
            "size": spy_quote.get("spy_size", 0),
            "latency_ms": 50,  # REST API latency estimate
            "sequence_id": self._sequence,
            "source": "alpaca_spy_proxy",
            "spy_price": spy_quote["spy_last"],
            "spy_timestamp": spy_quote.get("timestamp", ""),
        }
    
    async def fetch_spy_bars(
        self,
        *,
        timeframe: str = "5Min",
        limit: int = 120,
        lookback_days: int = 10,
        session_rth: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent SPY OHLCV bars from Alpaca (for Wisdom warmup).
        Alpaca requires start/end — bare limit-only requests return bars=null.
        Returns list of dicts: open, high, low, close, timestamp.

        session_rth=True → bars from today's 09:30 America/New_York (true day VWAP seed).
        """
        if not self.is_configured():
            return []
        try:
            import aiohttp
            from datetime import datetime, timedelta, timezone
            from zoneinfo import ZoneInfo

            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            }
            end = datetime.now(timezone.utc)
            if session_rth:
                et = ZoneInfo("America/New_York")
                now_et = end.astimezone(et)
                start = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                if now_et < start:
                    # Pre-open: use prior RTH day open so seed is not empty.
                    start = (start - timedelta(days=1)).replace(
                        hour=9, minute=30, second=0, microsecond=0
                    )
                start = start.astimezone(timezone.utc)
            else:
                start = end - timedelta(days=max(1, int(lookback_days)))
            params = {
                "timeframe": timeframe,
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": str(max(20, min(int(limit), 1000))),
                "adjustment": "raw",
                "feed": "iex",
                "sort": "asc",
            }
            url = f"{self.data_url}/v2/stocks/SPY/bars"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning("Alpaca bars status=%s body=%s", resp.status, text[:160])
                        return []
                    data = await resp.json()
                    bars = data.get("bars") or []
                    out: list[dict[str, Any]] = []
                    for row in bars:
                        if not isinstance(row, dict):
                            continue
                        try:
                            out.append(
                                {
                                    "open": float(row["o"]),
                                    "high": float(row["h"]),
                                    "low": float(row["l"]),
                                    "close": float(row["c"]),
                                    "volume": float(row.get("v") or 1.0),
                                    "timestamp": row.get("t"),
                                }
                            )
                        except (KeyError, TypeError, ValueError):
                            continue
                    if not out:
                        logger.warning(
                            "Alpaca bars empty timeframe=%s start=%s end=%s",
                            timeframe,
                            params["start"],
                            params["end"],
                        )
                    return out
        except Exception as exc:
            logger.exception("fetch_spy_bars_failed err=%s", exc)
            return []

    def mes_proxy_bars_from_spy(self, spy_bars: list[dict[str, Any]]) -> list[dict[str, float]]:
        """Scale SPY OHLC bars to MES proxy OHLC."""
        out: list[dict[str, float]] = []
        for row in spy_bars:
            try:
                out.append(
                    {
                        "high": self.scale_spy_to_mes(float(row["high"])),
                        "low": self.scale_spy_to_mes(float(row["low"])),
                        "close": self.scale_spy_to_mes(float(row["close"])),
                        "volume": max(1.0, float(row.get("volume") or 1.0)),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    @property
    def last_mes_proxy_price(self) -> float:
        """Get last known MES proxy price."""
        return self._last_mes_proxy
    
    @property
    def last_spy_price(self) -> float:
        """Get last known SPY price."""
        return self._last_spy_price


async def test_alpaca_feed() -> None:
    """Test the Alpaca SPY feed integration."""
    feed = AlpacaSPYFeed()
    
    print(f"Configured: {feed.is_configured()}")
    
    if not feed.is_configured():
        print("❌ Set ALPACA_API_KEY and ALPACA_API_SECRET environment variables")
        return
    
    print("\n🔍 Health check...")
    ok, msg = await feed.health_check()
    print(f"   {'✅' if ok else '❌'} {msg}")
    
    if not ok:
        return
    
    print("\n📊 Fetching SPY quote...")
    spy_quote = await feed.fetch_spy_quote()
    
    if spy_quote:
        print(f"   SPY: ${spy_quote['spy_last']:.2f}")
        print(f"   Bid: ${spy_quote['spy_bid']:.2f} | Ask: ${spy_quote['spy_ask']:.2f}")
        
        mes_tick = feed.get_mes_proxy_tick(spy_quote)
        print(f"\n📈 MES Proxy: ${mes_tick['price']:.2f}")
        print(f"   Bid: ${mes_tick['bid']:.2f} | Ask: ${mes_tick['ask']:.2f}")
        print(f"   Scaling: SPY × {SPY_TO_MES_SCALE} = MES")
    else:
        print("   ❌ Failed to fetch SPY quote")


if __name__ == "__main__":
    asyncio.run(test_alpaca_feed())
