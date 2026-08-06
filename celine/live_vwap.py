"""Session / rolling VWAP — volume-weighted average price."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class LiveVwapTracker:
    """
    VWAP = sum(price × volume) / sum(volume).

    window > 0  → rolling lookback (maxlen samples)
    window <= 0 → session cumulative (never drops samples; reset on day roll)
    """

    window: int = 0
    last_price: float = 0.0
    _cum_pv: float = 0.0
    _cum_vol: float = 0.0
    _samples: deque[tuple[float, float]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.window = int(self.window)
        if self.window > 0:
            self.window = max(2, self.window)
            self._samples = deque(maxlen=self.window)
        else:
            self._samples = deque()  # unbounded session path (rebuild on reset)

    @property
    def tick_count(self) -> int:
        return len(self._samples)

    @property
    def vwap(self) -> float:
        if self._cum_vol > 0:
            return round(self._cum_pv / self._cum_vol, 4)
        return self.last_price

    def update_trade(self, *, price: float, size: float) -> float:
        if price <= 0 or size <= 0:
            return self.vwap
        p = float(price)
        v = float(size)
        self.last_price = p
        if self.window > 0:
            # Rolling: recompute from samples so drops are correct.
            self._samples.append((p, v))
            self._cum_pv = sum(px * vx for px, vx in self._samples)
            self._cum_vol = sum(vx for _, vx in self._samples)
        else:
            self._samples.append((p, v))
            self._cum_pv += p * v
            self._cum_vol += v
        return self.vwap

    def reset(self) -> None:
        self._samples.clear()
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self.last_price = 0.0
