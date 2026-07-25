"""
VolumeWatch grade feed — reads shared macro state for FutureMathics MES trading.

Canonical file: C:/Volumewatch/shared_volumewatch_marketmathics_state.json
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("C:/Volumewatch/shared_volumewatch_marketmathics_state.json")
ScoreSource = Literal["overall", "1m"]


@dataclass(frozen=True, slots=True)
class VolumeWatchGradeSnapshot:
    score: float
    grade: str
    risk_mode: str
    vw_position: str
    trade_permission: str
    pillar_scores: dict[str, float]
    timeframe_scores: dict[str, float]
    timestamp: str
    age_seconds: float
    score_source: str
    fresh: bool


def _state_path() -> Path:
    raw = os.getenv("FM_VOLUMEWATCH_STATE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_STATE_PATH


def fetch_volumewatch_grade(
    *,
    score_source: ScoreSource = "overall",
    stale_seconds: float = 300.0,
    allow_stale: bool = False,
) -> VolumeWatchGradeSnapshot | None:
    """
    Read VolumeWatch macro grade from shared state.

    score_source:
      - overall: blended multi-TF score (what the hub shows as macro)
      - 1m: monthly timeframe score only
    """
    path = _state_path()
    if not path.exists():
        logger.warning("volumewatch_grade_missing path=%s", path)
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vw: dict[str, Any] | None = data.get("volumewatch")
        if not vw:
            return None

        ts_raw = str(vw.get("timestamp") or "")
        if not ts_raw:
            return None
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        fresh = age <= stale_seconds

        if not fresh and not allow_stale:
            logger.warning("volumewatch_grade_stale age=%.1fs threshold=%.0fs", age, stale_seconds)
            return None

        tf_scores = {str(k): float(v) for k, v in (vw.get("timeframe_scores") or {}).items()}
        if score_source == "1m" and "1m" in tf_scores:
            score = float(tf_scores["1m"])
        else:
            score = float(vw.get("overall_score") or 0.0)

        snap = VolumeWatchGradeSnapshot(
            score=round(score, 4),
            grade=str(vw.get("overall_grade") or ""),
            risk_mode=str(vw.get("risk_mode") or ""),
            vw_position=str(vw.get("position") or ""),
            trade_permission=str(vw.get("trade_permission") or ""),
            pillar_scores={str(k): float(v) for k, v in (vw.get("pillar_scores") or {}).items()},
            timeframe_scores=tf_scores,
            timestamp=ts_raw,
            age_seconds=round(age, 1),
            score_source=score_source if score_source != "1m" or "1m" in tf_scores else "overall",
            fresh=fresh,
        )
        logger.info(
            "volumewatch_grade score=%.2f grade=%s source=%s age=%.1fs",
            snap.score,
            snap.grade,
            snap.score_source,
            snap.age_seconds,
        )
        return snap
    except Exception as exc:
        logger.exception("volumewatch_grade_read_failed err=%s", exc)
        return None
