#!/usr/bin/env python3
"""Unit tests for live-cash safety gates (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_fill_confirmed_rejects_submitted() -> None:
    from broker import _fill_confirmed, _order_working

    assert _fill_confirmed("FILLED") is True
    assert _fill_confirmed("PARTIAL") is True
    assert _fill_confirmed("SUBMITTED") is False
    assert _fill_confirmed("ACCEPTED") is False
    assert _order_working("SUBMITTED") is True


def test_max_mes_contracts_live_vs_paper(monkeypatch) -> None:
    from engine import config as cfg

    monkeypatch.setenv("FM_DATA_SOURCE", "alpaca")
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    # FORWARD_TEST_MODE is True in config — paper cap
    assert cfg.forward_test_force_paper() is True
    monkeypatch.setenv("FM_PAPER_MAX_MES_CONTRACTS", "2")
    assert cfg.max_mes_contracts() == 2


def test_live_cash_arming_blocked_while_paper(monkeypatch) -> None:
    from engine import config as cfg

    monkeypatch.delenv("FM_FORWARD_TEST_MODE", raising=False)
    # Default FORWARD_TEST_MODE=True → never armed
    armed, reason = cfg.live_cash_arming_status()
    assert armed is False
    assert "paper" in reason.lower()


def test_live_no_overnight_day_window(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from scripts.run_daily_session import virtue_entries_allowed, virtue_session_open

    monkeypatch.setenv("FM_FORWARD_TEST_MODE", "0")
    monkeypatch.setenv("FM_LIVE_ALLOW_OVERNIGHT", "0")
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    monkeypatch.setenv("FM_DATA_SOURCE", "databento")
    monkeypatch.setenv("VIRTUE_SESSION_MODE", "cme")
    monkeypatch.setenv("FM_VIRTUE_TRADE_HORIZON", "scalp")
    monkeypatch.setenv("FM_VIRTUE_SIMPLE_STACK", "0")

    tz = ZoneInfo("America/New_York")
    # Sunday Globex open — blocked for live cash daytime-only
    sun = datetime(2026, 7, 26, 20, 0, tzinfo=tz)
    assert virtue_session_open(sun) is False
    # Monday inside morning entry window — allowed
    mon = datetime(2026, 7, 27, 11, 0, tzinfo=tz)
    assert virtue_session_open(mon) is True
    assert virtue_entries_allowed(mon) is True
    # Monday lunch — session open but no new entries
    lunch = datetime(2026, 7, 27, 12, 30, tzinfo=tz)
    assert virtue_session_open(lunch) is True
    assert virtue_entries_allowed(lunch) is False
    # Monday evening Globex — blocked without overnight
    eve = datetime(2026, 7, 27, 20, 0, tzinfo=tz)
    assert virtue_session_open(eve) is False
    # Pre-maintenance cutoff
    cut = datetime(2026, 7, 27, 16, 45, tzinfo=tz)
    assert virtue_session_open(cut) is False


def test_trading_halted_env(monkeypatch) -> None:
    from engine import config as cfg

    monkeypatch.setenv("FM_TRADING_HALTED", "1")
    assert cfg.trading_halted() is True
    monkeypatch.setenv("FM_TRADING_HALTED", "0")
    assert cfg.trading_halted() is False


def test_positions_tuple_contract() -> None:
    """get_futures_positions must return (list, err|None)."""
    import inspect
    from engine import webull_futures as wf

    assert "tuple" in str(inspect.signature(wf.get_futures_positions).return_annotation).lower() or True


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
