"""Rolling TWAP — equal-weight time average over a fixed lookback window."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class LiveTwapTracker:
    """TWAP = mean(price) over `window` samples."""

    window: int = 60
    last_price: float = 0.0
    _samples: deque[float] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.window = max(2, int(self.window))
        self._samples = deque(maxlen=self.window)

    @property
    def samples(self) -> int:
        return len(self._samples)

    @property
    def twap(self) -> float:
        if not self._samples:
            return self.last_price
        return round(sum(self._samples) / len(self._samples), 4)

    def update(self, price: float) -> float:
        if price <= 0:
            return self.twap
        self.last_price = float(price)
        self._samples.append(float(price))
        return self.twap

    def reset(self) -> None:
        self._samples.clear()
        self.last_price = 0.0
