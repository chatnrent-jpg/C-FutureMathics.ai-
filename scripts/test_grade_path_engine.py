"""Unit tests for VolumeWatch grade path engine (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from celine.grade_path_engine import (
    Exposure,
    GradeAction,
    GradePathConfig,
    GradePathEngine,
    PathBias,
)


def _eng() -> GradePathEngine:
    return GradePathEngine(config=GradePathConfig())


def test_rising_cash_then_long() -> None:
    e = _eng()
    # Bootstrap rising
    d = e.update(40.0)
    assert d.path == PathBias.FALLING or d.action == GradeAction.STAY_FLAT
    d = e.update(49.0)  # rising but still below 50
    assert e.path == PathBias.RISING
    assert d.action == GradeAction.STAY_FLAT
    assert "cash" in d.reason or "0_to_50" in d.reason
    d = e.update(51.0)
    assert d.action == GradeAction.ENTER_LONG
    assert e.exposure == Exposure.LONG


def test_long_exit_at_85() -> None:
    e = _eng()
    e.update(40.0)
    e.update(45.0)
    e.update(51.0)
    assert e.exposure == Exposure.LONG
    d = e.update(86.0)
    assert d.action == GradeAction.EXIT_LONG
    assert e.exposure == Exposure.FLAT


def test_short_at_90_after_long_exit() -> None:
    e = _eng()
    e.update(40.0)
    e.update(51.0)
    e.update(86.0)  # exit long
    d = e.update(87.0)  # cash band
    assert d.action == GradeAction.STAY_FLAT
    d = e.update(91.0)
    assert d.action == GradeAction.ENTER_SHORT
    assert e.exposure == Exposure.SHORT


def test_short_exit_at_50_falling() -> None:
    e = _eng()
    e.update(91.0)
    # force short exposure
    if e.exposure != Exposure.SHORT:
        e.update(92.0)
    assert e.exposure == Exposure.SHORT
    # Fall toward 50
    e.update(80.0)
    e.update(60.0)
    d = e.update(49.0)
    assert d.action == GradeAction.EXIT_SHORT
    assert e.exposure == Exposure.FLAT


def test_falling_no_trade_below_50() -> None:
    e = _eng()
    e.update(95.0)  # enter short on extreme
    assert e.exposure == Exposure.SHORT
    e.update(70.0)  # falling, still short
    assert e.path == PathBias.FALLING
    e.update(49.0)  # exit short
    assert e.exposure == Exposure.FLAT
    d = e.update(40.0)
    assert d.action == GradeAction.STAY_FLAT
    assert "no_trade" in d.reason or "below" in d.reason


def test_failed_long_recovery() -> None:
    e = _eng()
    e.update(40.0)
    e.update(51.0)
    assert e.exposure == Exposure.LONG
    e.update(70.0)
    d = e.update(49.0)  # falling below 50
    assert d.action == GradeAction.EXIT_LONG


def test_persist_roundtrip(tmp_path: Path | None = None) -> None:
    from pathlib import Path as P

    path = P(ROOT) / "data" / "_test_grade_path_state.json"
    e = _eng()
    e.update(40.0)
    e.update(66.0)
    e.save(path)
    e2 = GradePathEngine.load(path)
    assert e2.exposure == Exposure.LONG
    assert e2.last_score == 66.0
    path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_rising_cash_then_long()
    test_long_exit_at_85()
    test_short_at_90_after_long_exit()
    test_short_exit_at_50_falling()
    test_falling_no_trade_below_50()
    test_failed_long_recovery()
    test_persist_roundtrip()
    print("ALL GRADE PATH TESTS PASSED")
