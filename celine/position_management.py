"""
Celine — MES exit math (stop / target in ticks).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.config import TICK_SIZE, ticks_to_dollars


class ExitReason(str):
    TAKE_PROFIT = "TAKE_PROFIT_TICKS"
    STOP_LOSS = "STOP_LOSS_TICKS"
    EOD = "END_OF_DAY"


@dataclass(frozen=True, slots=True)
class ExitLevels:
    entry_price: float
    stop_price: float
    target_price: float
    direction: str


def build_exit_levels(
    *,
    entry_price: float,
    direction: str,
    stop_ticks: int,
    target_ticks: int,
) -> ExitLevels:
    stop_pts = stop_ticks * TICK_SIZE
    tgt_pts = target_ticks * TICK_SIZE
    d = direction.upper()
    if d == "LONG":
        return ExitLevels(
            entry_price=entry_price,
            stop_price=round(entry_price - stop_pts, 2),
            target_price=round(entry_price + tgt_pts, 2),
            direction=d,
        )
    return ExitLevels(
        entry_price=entry_price,
        stop_price=round(entry_price + stop_pts, 2),
        target_price=round(entry_price - tgt_pts, 2),
        direction=d,
    )


def evaluate_exit(
    *,
    last_price: float,
    levels: ExitLevels,
) -> str | None:
    d = levels.direction.upper()
    if d == "LONG":
        if last_price <= levels.stop_price:
            return ExitReason.STOP_LOSS
        if last_price >= levels.target_price:
            return ExitReason.TAKE_PROFIT
    else:
        if last_price >= levels.stop_price:
            return ExitReason.STOP_LOSS
        if last_price <= levels.target_price:
            return ExitReason.TAKE_PROFIT
    return None


def realized_pnl(
    *,
    entry_price: float,
    exit_price: float,
    direction: str,
    contracts: int,
) -> float:
    d = direction.upper()
    points = (exit_price - entry_price) if d == "LONG" else (entry_price - exit_price)
    return ticks_to_dollars(points / TICK_SIZE, contracts)
