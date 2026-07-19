"""Celine — continuous session VWAP from live SPY trade stream."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LiveVwapTracker:
    """Rolling VWAP = sum(price × volume) / sum(volume) for the session."""

    cumulative_pv: float = 0.0
    cumulative_volume: float = 0.0
    last_price: float = 0.0
    tick_count: int = 0

    @property
    def vwap(self) -> float:
        if self.cumulative_volume <= 0:
            return self.last_price
        return round(self.cumulative_pv / self.cumulative_volume, 4)

    def update_trade(self, *, price: float, size: float) -> float:
        if price <= 0 or size <= 0:
            return self.vwap
        self.last_price = price
        self.cumulative_pv += price * size
        self.cumulative_volume += size
        self.tick_count += 1
        return self.vwap

    def reset(self) -> None:
        self.cumulative_pv = 0.0
        self.cumulative_volume = 0.0
        self.last_price = 0.0
        self.tick_count = 0
