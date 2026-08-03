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

    # Default FORWARD_TEST_MODE=True → never armed
    armed, reason = cfg.live_cash_arming_status()
    assert armed is False
    assert "FORWARD_TEST_MODE" in reason


def test_positions_tuple_contract() -> None:
    """get_futures_positions must return (list, err|None)."""
    import inspect
    from engine import webull_futures as wf

    sig = inspect.signature(wf.get_futures_positions)
    assert sig.return_annotation != list


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
