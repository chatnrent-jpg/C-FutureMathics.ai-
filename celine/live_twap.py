"""Session / rolling TWAP — equal-weight time average."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class LiveTwapTracker:
    """
    TWAP = mean(price).

    window > 0  → rolling lookback
    window <= 0 → session cumulative (reset on day roll)
    """

    window: int = 0
    last_price: float = 0.0
    _cum_price: float = 0.0
    _n: int = 0
    _samples: deque[float] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.window = int(self.window)
        if self.window > 0:
            self.window = max(2, self.window)
            self._samples = deque(maxlen=self.window)
        else:
            self._samples = deque()

    @property
    def samples(self) -> int:
        return self._n if self.window <= 0 else len(self._samples)

    @property
    def twap(self) -> float:
        if self.window > 0:
            if not self._samples:
                return self.last_price
            return round(sum(self._samples) / len(self._samples), 4)
        if self._n <= 0:
            return self.last_price
        return round(self._cum_price / self._n, 4)

    def update(self, price: float) -> float:
        if price <= 0:
            return self.twap
        p = float(price)
        self.last_price = p
        if self.window > 0:
            self._samples.append(p)
            self._n = len(self._samples)
            self._cum_price = sum(self._samples)
        else:
            self._samples.append(p)
            self._cum_price += p
            self._n += 1
        return self.twap

    def reset(self) -> None:
        self._samples.clear()
        self._cum_price = 0.0
        self._n = 0
        self.last_price = 0.0
