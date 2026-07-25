"""
VolumeWatch path-dependent grade engine for MES futures.

Rules (crystal clear):
  RISING (coming from down / weakness):
    - <50: CASH (no trading)
    - ≥50 and <85: LONG
    - ≥85: EXIT LONG → cash until short zone
    - ≥90: SHORT

  FALLING (coming from up / strength):
    - Hold SHORT until ≤50 → EXIT SHORT
    - <50: CASH (no trading)
    - No new LONG while falling through below 50

Protective futures hard-stops are applied by the position manager, not this engine.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PathBias(str, Enum):
    RISING = "RISING"      # coming from down
    FALLING = "FALLING"    # coming from up
    UNKNOWN = "UNKNOWN"


class Exposure(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


class GradeAction(str, Enum):
    HOLD = "HOLD"
    ENTER_LONG = "ENTER_LONG"
    EXIT_LONG = "EXIT_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT_SHORT = "EXIT_SHORT"
    STAY_FLAT = "STAY_FLAT"


@dataclass
class GradePathConfig:
    long_entry: float = 50.0
    long_exit: float = 85.0
    short_entry: float = 90.0
    short_exit: float = 50.0
    path_epsilon: float = 0.35  # min score delta to flip path bias


@dataclass
class GradeDecision:
    action: GradeAction
    exposure: Exposure
    path: PathBias
    score: float
    reason: str


@dataclass
class GradePathEngine:
    """
    Stateful path machine. Persist via save()/load() across restarts.
    """

    config: GradePathConfig = field(default_factory=GradePathConfig)
    last_score: float | None = None
    path: PathBias = PathBias.UNKNOWN
    exposure: Exposure = Exposure.FLAT
    peak_score: float | None = None
    trough_score: float | None = None

    def update(self, score: float) -> GradeDecision:
        score = float(score)
        self._update_path(score)
        decision = self._decide(score)
        self.last_score = score
        if self.peak_score is None or score > self.peak_score:
            self.peak_score = score
        if self.trough_score is None or score < self.trough_score:
            self.trough_score = score
        logger.info(
            "grade_path score=%.2f path=%s exposure=%s → %s (%s)",
            score,
            self.path.value,
            self.exposure.value,
            decision.action.value,
            decision.reason,
        )
        return decision

    def _update_path(self, score: float) -> None:
        if self.last_score is None:
            # Bootstrap: treat mid/high as rising unless already weak
            if score >= self.config.long_entry:
                self.path = PathBias.RISING
            else:
                self.path = PathBias.FALLING
            return

        delta = score - self.last_score
        eps = self.config.path_epsilon
        if delta >= eps:
            self.path = PathBias.RISING
        elif delta <= -eps:
            self.path = PathBias.FALLING
        # else keep prior path (hysteresis)

    def _decide(self, score: float) -> GradeDecision:
        cfg = self.config
        path = self.path
        exp = self.exposure

        # --- Exits (always first) ---
        if exp == Exposure.LONG and score >= cfg.long_exit:
            self.exposure = Exposure.FLAT
            return GradeDecision(
                action=GradeAction.EXIT_LONG,
                exposure=Exposure.FLAT,
                path=path,
                score=score,
                reason=f"long_exit_score>={cfg.long_exit}",
            )

        if exp == Exposure.SHORT and score <= cfg.short_exit:
            self.exposure = Exposure.FLAT
            return GradeDecision(
                action=GradeAction.EXIT_SHORT,
                exposure=Exposure.FLAT,
                path=path,
                score=score,
                reason=f"short_exit_score<={cfg.short_exit}",
            )

        # Failed long recovery: rising long that rolls over below entry
        if exp == Exposure.LONG and path == PathBias.FALLING and score < cfg.long_entry:
            self.exposure = Exposure.FLAT
            return GradeDecision(
                action=GradeAction.EXIT_LONG,
                exposure=Exposure.FLAT,
                path=path,
                score=score,
                reason=f"long_failed_recovery_score<{cfg.long_entry}",
            )

        # --- Flat: entries / cash zones ---
        if exp == Exposure.FLAT:
            if path == PathBias.RISING:
                # Coming from down: no trading 0–65
                if score < cfg.long_entry:
                    return GradeDecision(
                        action=GradeAction.STAY_FLAT,
                        exposure=Exposure.FLAT,
                        path=path,
                        score=score,
                        reason=f"rising_cash_zone_0_to_{cfg.long_entry}",
                    )
                # Long band [65, 85)
                if cfg.long_entry <= score < cfg.long_exit:
                    self.exposure = Exposure.LONG
                    return GradeDecision(
                        action=GradeAction.ENTER_LONG,
                        exposure=Exposure.LONG,
                        path=path,
                        score=score,
                        reason=f"rising_long_entry>={cfg.long_entry}",
                    )
                # [85, 90): cash after long exit / before short
                if cfg.long_exit <= score < cfg.short_entry:
                    return GradeDecision(
                        action=GradeAction.STAY_FLAT,
                        exposure=Exposure.FLAT,
                        path=path,
                        score=score,
                        reason=f"rising_cash_between_{cfg.long_exit}_and_{cfg.short_entry}",
                    )
                # ≥90 short
                if score >= cfg.short_entry:
                    self.exposure = Exposure.SHORT
                    return GradeDecision(
                        action=GradeAction.ENTER_SHORT,
                        exposure=Exposure.SHORT,
                        path=path,
                        score=score,
                        reason=f"rising_short_entry>={cfg.short_entry}",
                    )

            if path == PathBias.FALLING:
                # Coming from up: no trading below 50
                if score < cfg.short_exit:
                    return GradeDecision(
                        action=GradeAction.STAY_FLAT,
                        exposure=Exposure.FLAT,
                        path=path,
                        score=score,
                        reason=f"falling_no_trade_below_{cfg.short_exit}",
                    )
                # Still extreme: allow short fade
                if score >= cfg.short_entry:
                    self.exposure = Exposure.SHORT
                    return GradeDecision(
                        action=GradeAction.ENTER_SHORT,
                        exposure=Exposure.SHORT,
                        path=path,
                        score=score,
                        reason=f"falling_short_fade>={cfg.short_entry}",
                    )
                # 50–90 falling, flat: cash (includes 50–65)
                return GradeDecision(
                    action=GradeAction.STAY_FLAT,
                    exposure=Exposure.FLAT,
                    path=path,
                    score=score,
                    reason="falling_cash_mid_band",
                )

            return GradeDecision(
                action=GradeAction.STAY_FLAT,
                exposure=Exposure.FLAT,
                path=path,
                score=score,
                reason="path_unknown_cash",
            )

        # --- Holding ---
        if exp == Exposure.LONG:
            return GradeDecision(
                action=GradeAction.HOLD,
                exposure=Exposure.LONG,
                path=path,
                score=score,
                reason="hold_long",
            )
        return GradeDecision(
            action=GradeAction.HOLD,
            exposure=Exposure.SHORT,
            path=path,
            score=score,
            reason="hold_short",
        )

    def sync_exposure_from_broker(self, has_long: bool, has_short: bool) -> None:
        """Reconcile engine exposure with live positions after restart."""
        if has_long:
            self.exposure = Exposure.LONG
        elif has_short:
            self.exposure = Exposure.SHORT
        else:
            self.exposure = Exposure.FLAT

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_score": self.last_score,
            "path": self.path.value,
            "exposure": self.exposure.value,
            "peak_score": self.peak_score,
            "trough_score": self.trough_score,
            "config": {
                "long_entry": self.config.long_entry,
                "long_exit": self.config.long_exit,
                "short_entry": self.config.short_entry,
                "short_exit": self.config.short_exit,
                "path_epsilon": self.config.path_epsilon,
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, config: GradePathConfig | None = None) -> GradePathEngine:
        cfg = config or GradePathConfig()
        if not path.exists():
            return cls(config=cfg)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            eng = cls(config=cfg)
            eng.last_score = raw.get("last_score")
            eng.path = PathBias(raw.get("path") or PathBias.UNKNOWN.value)
            eng.exposure = Exposure(raw.get("exposure") or Exposure.FLAT.value)
            eng.peak_score = raw.get("peak_score")
            eng.trough_score = raw.get("trough_score")
            return eng
        except Exception as exc:
            logger.warning("grade_path_load_failed err=%s — starting fresh", exc)
            return cls(config=cfg)
