"""Rolling VWAP — volume-weighted average over a fixed lookback window."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class LiveVwapTracker:
    """Rolling VWAP = sum(price × volume) / sum(volume) over `window` samples."""

    window: int = 60
    last_price: float = 0.0
    _samples: deque[tuple[float, float]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.window = max(2, int(self.window))
        self._samples = deque(maxlen=self.window)

    @property
    def tick_count(self) -> int:
        return len(self._samples)

    @property
    def vwap(self) -> float:
        if not self._samples:
            return self.last_price
        pv = sum(p * v for p, v in self._samples)
        vol = sum(v for _, v in self._samples)
        if vol <= 0:
            return self.last_price
        return round(pv / vol, 4)

    def update_trade(self, *, price: float, size: float) -> float:
        if price <= 0 or size <= 0:
            return self.vwap
        self.last_price = float(price)
        self._samples.append((float(price), float(size)))
        return self.vwap

    def reset(self) -> None:
        self._samples.clear()
        self.last_price = 0.0
