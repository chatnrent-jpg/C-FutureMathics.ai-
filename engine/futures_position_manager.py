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
    # Swing trading additions
    peak_profit_ticks: float = 0.0  # Track best profit for trailing stop
    trailing_stop_active: bool = False
    trailing_stop_ticks: int = 30  # Trail by 30 ticks once activated
    manage_mode: str = "ticks"  # "ticks" | "grade" — grade uses hard stop only; exits via grade engine

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
            "peak_profit_ticks": self.peak_profit_ticks,
            "trailing_stop_active": self.trailing_stop_active,
            "manage_mode": self.manage_mode,
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
        manage_mode: str = "ticks",
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
            manage_mode=manage_mode,
        )
        self.positions[pos_id] = pos
        return pos

    def force_close(
        self,
        *,
        position_id: str,
        exit_price: float,
        reason: str,
    ) -> ExitRequest | None:
        """Close a position by id (grade-engine exits)."""
        pos = self.positions.get(position_id)
        if pos is None:
            return None
        pnl = realized_pnl(
            entry_price=pos.entry_price,
            exit_price=exit_price,
            direction=pos.direction,
            contracts=pos.contracts,
        )
        self.realized_pnl_today = round(self.realized_pnl_today + pnl, 2)
        req = ExitRequest(
            position_id=pos.position_id,
            trade_id=pos.trade_id,
            reason=reason,
            contracts=pos.contracts,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            max_loss=pos.max_loss,
            realized_pnl=pnl,
        )
        del self.positions[position_id]
        logger.info("position_force_close id=%s reason=%s pnl=%.2f", position_id, reason, pnl)
        return req

    def monitor_tick(self, last_price: float) -> MonitorResult:
        exits: list[ExitRequest] = []
        for pid in list(self.positions.keys()):
            pos = self.positions[pid]
            
            # Calculate current profit in ticks
            from engine.config import TICK_SIZE
            if pos.direction.upper() == "LONG":
                profit_ticks = (last_price - pos.entry_price) / TICK_SIZE
            else:
                profit_ticks = (pos.entry_price - last_price) / TICK_SIZE
            
            # Update peak profit
            if profit_ticks > pos.peak_profit_ticks:
                pos.peak_profit_ticks = profit_ticks
            
            reason = None

            # Grade-managed: protective hard stop only (grade engine owns TP/path exits).
            # Always use live GRADE_HARD_STOP_TICKS so widening the stop applies to open trades too.
            if pos.manage_mode == "grade":
                from engine.config import GRADE_HARD_STOP_TICKS, TICK_SIZE as _TS, ticks_to_dollars

                stop_ticks = int(GRADE_HARD_STOP_TICKS)
                if pos.stop_ticks != stop_ticks:
                    pos.stop_ticks = stop_ticks
                    pos.max_loss = ticks_to_dollars(stop_ticks, pos.contracts)
                    pos.levels = build_exit_levels(
                        entry_price=pos.entry_price,
                        direction=pos.direction,
                        stop_ticks=stop_ticks,
                        target_ticks=pos.target_ticks,
                    )
                d = pos.direction.upper()
                if d == "LONG" and last_price <= pos.levels.stop_price:
                    reason = "STOP_LOSS_TICKS"
                elif d != "LONG" and last_price >= pos.levels.stop_price:
                    reason = "STOP_LOSS_TICKS"
                if reason is None:
                    continue
            else:
                # Activate trailing stop if reached 50% of target
                if not pos.trailing_stop_active and profit_ticks >= pos.target_ticks * 0.5:
                    pos.trailing_stop_active = True
                    logger.info("Trailing stop activated for %s at %.1f ticks profit", pid, profit_ticks)
                
                if pos.trailing_stop_active:
                    trail_distance = pos.peak_profit_ticks - pos.trailing_stop_ticks
                    if profit_ticks <= trail_distance:
                        reason = "trailing_stop"
                
                if reason is None:
                    reason = evaluate_exit(last_price=last_price, levels=pos.levels)
                
                if reason is None:
                    reason = self._check_time_based_exit(pos, last_price)
                
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
            logger.info("position_exit id=%s reason=%s pnl=%.2f peak_ticks=%.1f", 
                       pid, reason, pnl, pos.peak_profit_ticks)
        return MonitorResult(exit_requests=exits, open_positions=len(self.positions))
    
    def _check_time_based_exit(self, pos: ManagedPosition, current_price: float) -> str | None:
        """
        Check if position should exit based on time rules.
        
        Rules:
        1. End-of-day exit: Close all positions by 3:50 PM ET
        2. Don't exit before 4 hours unless stop/target hit
        """
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        
        # Parse entry time
        entry_dt = datetime.fromisoformat(pos.entry_time)
        now_utc = datetime.now(timezone.utc)
        now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
        
        # Calculate hours in position
        hours_in_position = (now_utc - entry_dt).total_seconds() / 3600
        
        # Rule 1: End-of-day exit (3:50 PM ET)
        if now_et.hour == 15 and now_et.minute >= 50:
            if hours_in_position >= 0.5:  # At least 30 minutes
                return "end_of_day"
        
        # Rule 2: Minimum 4-hour hold (unless stop/target already hit)
        # This is enforced by not returning any time-based exit before 4 hours
        # The standard stop/target checks will still work
        
        return None

    def total_open_risk(self) -> float:
        return round(sum(p.max_loss for p in self.positions.values()), 2)

    def open_positions_list(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.positions.values()]

    def restore_from_dicts(self, rows: list[dict[str, Any]]) -> int:
        """Reload open paper/live positions after process restart (keeps dashboard continuous)."""
        restored = 0
        for row in rows or []:
            try:
                direction = str(row.get("direction") or "LONG")
                entry_price = float(row.get("entry_price") or 0)
                stop_ticks = int(row.get("stop_ticks") or 80)
                target_ticks = int(row.get("target_ticks") or 10000)
                if entry_price <= 0:
                    continue
                levels = build_exit_levels(
                    entry_price=entry_price,
                    direction=direction,
                    stop_ticks=stop_ticks,
                    target_ticks=target_ticks,
                )
                pos_id = str(row.get("position_id") or f"POS-{uuid.uuid4().hex[:8].upper()}")
                pos = ManagedPosition(
                    position_id=pos_id,
                    trade_id=str(row.get("trade_id") or f"GW-{uuid.uuid4().hex[:10].upper()}"),
                    symbol=str(row.get("symbol") or "MES"),
                    direction=direction,
                    contracts=int(row.get("contracts") or 1),
                    entry_price=entry_price,
                    stop_ticks=stop_ticks,
                    target_ticks=target_ticks,
                    max_loss=float(row.get("max_loss") or 0),
                    entry_time=str(row.get("entry_time") or datetime.now(timezone.utc).isoformat()),
                    levels=levels,
                    nav_at_entry=float(row.get("nav_at_entry") or 0),
                    peak_profit_ticks=float(row.get("peak_profit_ticks") or 0),
                    trailing_stop_active=bool(row.get("trailing_stop_active") or False),
                    manage_mode=str(row.get("manage_mode") or "ticks"),
                )
                self.positions[pos_id] = pos
                restored += 1
            except Exception as exc:
                logger.warning("skip_restore_position err=%s row=%s", exc, row)
        return restored
