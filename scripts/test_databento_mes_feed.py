#!/usr/bin/env python3
"""Unit tests for Databento MES feed cache + source selection (no live key required)."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_primary_data_source_auto_and_forced(monkeypatch) -> None:
    from engine import config as cfg

    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    monkeypatch.setenv("FM_DATA_SOURCE", "auto")
    assert cfg.primary_data_source() == "alpaca"
    assert cfg.virtue_rth_only() is True

    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    monkeypatch.setenv("FM_DATA_SOURCE", "auto")
    assert cfg.primary_data_source() == "databento"
    assert cfg.virtue_rth_only() is False

    monkeypatch.setenv("FM_DATA_SOURCE", "alpaca")
    assert cfg.primary_data_source() == "alpaca"
    assert cfg.virtue_rth_only() is True

    monkeypatch.setenv("FM_DATA_SOURCE", "databento")
    assert cfg.primary_data_source() == "databento"
    assert cfg.virtue_rth_only() is False


def test_cached_quote_rejects_stale() -> None:
    from engine.databento_mes_feed import DatabentoMESFeed

    feed = DatabentoMESFeed(api_key="db-test-key", max_quote_age_s=1.0)
    # Inject a fresh quote without starting live thread
    with feed._lock:
        feed._bid = 5000.0
        feed._ask = 5000.25
        feed._mid = 5000.125
        feed._ts_epoch = time.time()
        feed._sequence = 1
        feed._raw_symbol = "MESU6"
    q = feed.get_cached_quote(max_age_s=5.0)
    assert q is not None
    assert q["source"] == "databento_mes"
    assert q["bid"] == 5000.0

    with feed._lock:
        feed._ts_epoch = time.time() - 30.0
    stale = feed.get_cached_quote(max_age_s=5.0)
    assert stale is None


def test_cme_entry_cutoff_when_databento(monkeypatch) -> None:
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test-key")
    monkeypatch.setenv("FM_DATA_SOURCE", "databento")
    # Reload helpers that close over config at import time — call functions fresh
    from scripts.run_daily_session import in_market_hours, virtue_entries_allowed, virtue_session_open

    tz = ZoneInfo("America/New_York")
    # Sunday evening CME open — session open, entries allowed
    sun = datetime(2026, 7, 26, 20, 0, tzinfo=tz)
    assert in_market_hours(sun) is True
    assert virtue_session_open(sun) is True
    assert virtue_entries_allowed(sun) is True

    # Monday 16:30 — session open, entries still allowed
    mon_ok = datetime(2026, 7, 27, 16, 30, tzinfo=tz)
    assert virtue_session_open(mon_ok) is True
    assert virtue_entries_allowed(mon_ok) is True

    # Monday 16:45 — pre-maintenance cutoff
    mon_cut = datetime(2026, 7, 27, 16, 45, tzinfo=tz)
    assert virtue_session_open(mon_cut) is True
    assert virtue_entries_allowed(mon_cut) is False

    # Monday 17:30 maintenance — session closed
    mon_maint = datetime(2026, 7, 27, 17, 30, tzinfo=tz)
    assert virtue_session_open(mon_maint) is False
    assert virtue_entries_allowed(mon_maint) is False


def test_rth_mode_still_blocks_overnight(monkeypatch) -> None:
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    monkeypatch.setenv("FM_DATA_SOURCE", "alpaca")
    from scripts.run_daily_session import virtue_entries_allowed, virtue_session_open

    tz = ZoneInfo("America/New_York")
    sun = datetime(2026, 7, 26, 20, 0, tzinfo=tz)
    assert virtue_session_open(sun) is False
    assert virtue_entries_allowed(sun) is False
    mon_open = datetime(2026, 7, 27, 9, 30, tzinfo=tz)
    assert virtue_session_open(mon_open) is True
    assert virtue_entries_allowed(mon_open) is True
    mon_1500 = datetime(2026, 7, 27, 15, 0, tzinfo=tz)
    assert virtue_entries_allowed(mon_1500) is False


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
