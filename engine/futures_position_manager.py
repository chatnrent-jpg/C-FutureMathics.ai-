"""
FutureMathics position manager — single MES position with tick stop/target.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from celine.position_management import ExitLevels, build_exit_levels, evaluate_exit, realized_pnl

logger = logging.getLogger(__name__)


@dataclass
class ManagedPosition:
    position_id: str
    trade_id: str
    symbol: str
    direction: str
    contracts: int
    entry_price: float
    stop_ticks: int
    target_ticks: int
    max_loss: float
    entry_time: str
    levels: ExitLevels
    nav_at_entry: float = 0.0
    daily_pnl_at_entry: float = 0.0
    cycle_at_entry: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "contracts": self.contracts,
            "entry_price": self.entry_price,
            "stop_ticks": self.stop_ticks,
            "target_ticks": self.target_ticks,
            "max_loss": self.max_loss,
            "entry_time": self.entry_time,
            "nav_at_entry": self.nav_at_entry,
        }


@dataclass(frozen=True, slots=True)
class ExitRequest:
    position_id: str
    trade_id: str
    reason: str
    contracts: int
    direction: str
    entry_price: float
    exit_price: float
    max_loss: float
    realized_pnl: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "trade_id": self.trade_id,
            "reason": self.reason,
            "contracts": self.contracts,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "max_loss": self.max_loss,
            "realized_pnl": self.realized_pnl,
        }


@dataclass
class MonitorResult:
    exit_requests: list[ExitRequest] = field(default_factory=list)
    open_positions: int = 0


@dataclass
class FuturesPositionManager:
    realized_pnl_today: float = 0.0
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    emergency_halt: bool = False

    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        contracts: int,
        entry_price: float,
        stop_ticks: int,
        target_ticks: int,
        max_loss: float,
        trade_id: str,
        nav_at_entry: float = 0.0,
        daily_pnl_at_entry: float = 0.0,
        cycle_at_entry: int = 0,
    ) -> ManagedPosition:
        pos_id = f"POS-{uuid.uuid4().hex[:8].upper()}"
        levels = build_exit_levels(
            entry_price=entry_price,
            direction=direction,
            stop_ticks=stop_ticks,
            target_ticks=target_ticks,
        )
        pos = ManagedPosition(
            position_id=pos_id,
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            contracts=contracts,
            entry_price=entry_price,
            stop_ticks=stop_ticks,
            target_ticks=target_ticks,
            max_loss=max_loss,
            entry_time=datetime.now(timezone.utc).isoformat(),
            levels=levels,
            nav_at_entry=nav_at_entry,
            daily_pnl_at_entry=daily_pnl_at_entry,
            cycle_at_entry=cycle_at_entry,
        )
        self.positions[pos_id] = pos
        return pos

    def monitor_tick(self, last_price: float) -> MonitorResult:
        exits: list[ExitRequest] = []
        for pid in list(self.positions.keys()):
            pos = self.positions[pid]
            reason = evaluate_exit(last_price=last_price, levels=pos.levels)
            if reason is None:
                continue
            pnl = realized_pnl(
                entry_price=pos.entry_price,
                exit_price=last_price,
                direction=pos.direction,
                contracts=pos.contracts,
            )
            self.realized_pnl_today = round(self.realized_pnl_today + pnl, 2)
            exits.append(
                ExitRequest(
                    position_id=pos.position_id,
                    trade_id=pos.trade_id,
                    reason=reason,
                    contracts=pos.contracts,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    exit_price=last_price,
                    max_loss=pos.max_loss,
                    realized_pnl=pnl,
                )
            )
            del self.positions[pid]
            logger.info("position_exit id=%s reason=%s pnl=%.2f", pid, reason, pnl)
        return MonitorResult(exit_requests=exits, open_positions=len(self.positions))

    def total_open_risk(self) -> float:
        return round(sum(p.max_loss for p in self.positions.values()), 2)

    def open_positions_list(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.positions.values()]
