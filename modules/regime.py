"""
Institutional gamma regime.

Spot at or above the gamma flip is positive gamma (fade extremes).
Spot below the flip is negative gamma (momentum only).
A missing, stale, or non-numeric flip stands aside. The flip level is never invented.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger("futuremathics.regime")


def get_market_regime(spot_price: float, gamma_flip: float) -> str:
    """
    Determines whether the market is in a Positive Gamma (mean-reverting)
    or Negative Gamma (momentum/breakout) environment.
    """
    if spot_price >= gamma_flip:
        logger.info(f"Regime: POSITIVE GAMMA (Spot {spot_price} >= Flip {gamma_flip}). Fading extremes.")
        return "POSITIVE_GAMMA"
    else:
        logger.info(f"Regime: NEGATIVE GAMMA (Spot {spot_price} < Flip {gamma_flip}). Momentum only.")
        return "NEGATIVE_GAMMA"


class GammaRegime(str, Enum):
    POSITIVE = "POSITIVE_GAMMA"
    NEGATIVE = "NEGATIVE_GAMMA"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class GammaSnapshot:
    regime: GammaRegime
    net_gex: float | None
    gamma_flip: float | None
    age_seconds: float | None
    reason: str


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0.0:
        return None
    return number


def snapshot_from_flip(
    spot_price: float | None,
    gamma_flip: float | None,
    *,
    net_gex: float | None = None,
    age_seconds: float | None = None,
) -> GammaSnapshot:
    """Call get_market_regime only when both prices are real and positive."""
    spot = _finite_positive(spot_price)
    flip = _finite_positive(gamma_flip)
    if spot is None or flip is None:
        return GammaSnapshot(GammaRegime.UNKNOWN, net_gex, gamma_flip, age_seconds, "gamma_flip_invalid")
    label = get_market_regime(spot, flip)
    regime = GammaRegime.POSITIVE if label == "POSITIVE_GAMMA" else GammaRegime.NEGATIVE
    return GammaSnapshot(regime, net_gex, flip, age_seconds, label)


def read_gamma_file(
    path: Path,
    *,
    now: datetime | None = None,
    stale_seconds: float = 900.0,
    spot_price: float | None = None,
) -> GammaSnapshot:
    """Load data/gamma_state.json. Any read, shape, or age failure stands aside."""
    if not path.exists():
        return GammaSnapshot(GammaRegime.UNKNOWN, None, None, None, "gamma_file_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return GammaSnapshot(GammaRegime.UNKNOWN, None, None, None, "gamma_file_unreadable")
    if not isinstance(raw, dict):
        return GammaSnapshot(GammaRegime.UNKNOWN, None, None, None, "gamma_file_not_object")

    ts_raw = str(raw.get("timestamp") or "")
    age: float | None = None
    stale = True
    if ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            clock = now or datetime.now(timezone.utc)
            if clock.tzinfo is None:
                clock = clock.replace(tzinfo=timezone.utc)
            age = max(0.0, (clock - ts).total_seconds())
            stale = age > float(stale_seconds)
        except Exception:
            stale = True
    if stale:
        return GammaSnapshot(GammaRegime.UNKNOWN, None, raw.get("gamma_flip"), age, "gex_missing_or_stale")

    spot = spot_price if spot_price is not None else raw.get("price")
    gex_raw = raw.get("net_gex")
    try:
        gex = None if gex_raw is None else float(gex_raw)
    except (TypeError, ValueError):
        gex = None
    return snapshot_from_flip(spot, raw.get("gamma_flip"), net_gex=gex, age_seconds=age)
