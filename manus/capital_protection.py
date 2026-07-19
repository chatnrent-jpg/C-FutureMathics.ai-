"""
Manus capital protection matrix — dynamic NAV compounding & drawdown brake (Phase 10).

Fixed-fractional per-trade risk (0.5% NAV) with Capital Drag Brake on 10% drawdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from engine.config import (
    CAPITAL_DRAG_MULTIPLIER,
    DRAWDOWN_BRAKE_PCT,
    FIXED_FRACTIONAL_RISK_PCT,
    HARD_DAILY_STOP,
    MAX_CONCURRENT_RISK_PCT,
    MAX_DAILY_LOSS_PCT,
    PER_TRADE_RISK_MAX,
    PER_TRADE_RISK_MIN,
    RISK_BUDGET_TOLERANCE_PCT,
    STARTING_NAV,
    concurrent_risk_cap,
    effective_risk_nav,
    fixed_fractional_risk_pct,
    max_daily_loss_cap,
    risk_budget_tolerance_pct,
)

# Re-export config constants for backward-compatible imports
__all__ = (
    "CAPITAL_DRAG_MULTIPLIER",
    "CapitalProtectionMatrix",
    "DRAWDOWN_BRAKE_PCT",
    "FIXED_FRACTIONAL_RISK_PCT",
    "HARD_DAILY_STOP",
    "MAX_CONCURRENT_RISK_PCT",
    "MAX_DAILY_LOSS_PCT",
    "PER_TRADE_RISK_MAX",
    "PER_TRADE_RISK_MIN",
    "RISK_BUDGET_TOLERANCE_PCT",
    "RiskVerdict",
    "STARTING_NAV",
)


class RiskVerdict(str, Enum):
    APPROVED = "APPROVED"
    REDUCE_SIZE = "REDUCE_SIZE"
    HALT = "HALT"


@dataclass
class CapitalProtectionMatrix:
    """
    Rolling NAV capital constraints for MarketMathics.

    - Per-trade risk: exactly 0.5% of current NAV (fixed-fractional)
    - Capital Drag Brake: 10% drawdown → 50% risk until new equity high
    - Hard daily stop: 2% of current NAV
    - Max concurrent exposure: paper 50% NAV; live capped at LIVE_RISK_NAV_CAP
    """

    starting_nav: float = STARTING_NAV
    account_nav: float = STARTING_NAV
    peak_nav: float = STARTING_NAV
    daily_profit_target: float = 4_000.0
    size_reduction_factor: float = 0.5
    fixed_fractional_pct: float = FIXED_FRACTIONAL_RISK_PCT
    drawdown_brake_pct: float = DRAWDOWN_BRAKE_PCT
    sandbox_preflight_ok: bool = False

    @property
    def max_daily_loss_hard(self) -> float:
        return max_daily_loss_cap(self.account_nav)

    @property
    def max_daily_loss_soft(self) -> float:
        return round(self.max_daily_loss_hard * 0.6, 2)

    @property
    def max_concurrent_risk(self) -> float:
        return concurrent_risk_cap(self.account_nav)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_nav <= 0:
            return 0.0
        return max(0.0, (self.peak_nav - self.account_nav) / self.peak_nav)

    @property
    def capital_drag_active(self) -> bool:
        """True when 10% peak-to-trough drawdown is in effect."""
        return self.drawdown_pct >= self.drawdown_brake_pct

    @property
    def capital_drag_multiplier(self) -> float:
        return CAPITAL_DRAG_MULTIPLIER if self.capital_drag_active else 1.0

    @staticmethod
    def sandbox_vol_override_active() -> bool:
        from celine.signals import SANDBOX_VOL_OVERRIDE

        return bool(SANDBOX_VOL_OVERRIDE)

    def approve_sandbox_live_route(self, *, vrp_regime: str) -> bool:
        """
        When SANDBOX_VOL_OVERRIDE injects synthetic POSITIVE VRP, allow LIVE_ROUTE
        to the Alpaca paper endpoint for end-to-end execution testing.
        """
        if not self.sandbox_vol_override_active():
            return False
        return str(vrp_regime).upper() == "POSITIVE"

    def evaluate_sandbox_session(
        self,
        *,
        vrp_regime: str,
        heartbeat_green: bool,
        preflight_green: bool | None = None,
    ) -> tuple[RiskVerdict, str]:
        """
        Explicit sandbox session verdict — clears sticky session_halted when
        pre-flight, heartbeat, and POSITIVE VRP are all green.
        """
        if not self.sandbox_vol_override_active():
            return RiskVerdict.HALT, "session_halted"

        preflight = self.sandbox_preflight_ok if preflight_green is None else bool(preflight_green)
        if not preflight:
            return RiskVerdict.HALT, "session_halted"
        if not heartbeat_green:
            return RiskVerdict.HALT, "session_halted"
        if str(vrp_regime).upper() != "POSITIVE":
            return RiskVerdict.HALT, "session_halted"

        return RiskVerdict.APPROVED, "sandbox_session_approved"

    def evaluate_sandbox_feed_gate(
        self,
        *,
        session_halted: bool,
        vrp_regime: str,
        heartbeat_green: bool,
        preflight_green: bool | None = None,
        chain_valid: bool = True,
        warmup_fallback: bool = False,
        offline_simulation: bool = False,
    ) -> tuple[RiskVerdict, str, bool]:
        """
        Sandbox feed gate — resume from session_halted when green; otherwise APPROVED.
        Returns (verdict, reason, force_paper).
        """
        if session_halted:
            verdict, reason = self.evaluate_sandbox_session(
                vrp_regime=vrp_regime,
                heartbeat_green=heartbeat_green,
                preflight_green=preflight_green,
            )
            if verdict == RiskVerdict.HALT:
                return verdict, reason, True

        if (warmup_fallback or offline_simulation) and self.sandbox_vol_override_active():
            reason = "sandbox_offline_simulation" if offline_simulation else "sandbox_warmup_fallback"
            return RiskVerdict.APPROVED, reason, True

        if not chain_valid and self.sandbox_vol_override_active():
            if str(vrp_regime).upper() == "POSITIVE" and heartbeat_green:
                return RiskVerdict.APPROVED, "sandbox_chain_degraded_paper", True

        return RiskVerdict.APPROVED, "sandbox_feed_gate_ok", False

    def update_nav(self, nav: float) -> None:
        """Refresh account NAV and peak tracker."""
        self.account_nav = round(max(nav, 0.0), 2)
        self.peak_nav = max(self.peak_nav, self.account_nav)

    def per_trade_risk_budget(self) -> float:
        """
        Fixed-fractional sizing: pct × effective risk NAV, halved under Capital Drag Brake.
        """
        base = effective_risk_nav(self.account_nav) * fixed_fractional_risk_pct()
        if self.capital_drag_active:
            base *= self.capital_drag_multiplier
        return round(base, 2)

    @property
    def per_trade_risk_min(self) -> float:
        return round(self.per_trade_risk_budget() * (1.0 - RISK_BUDGET_TOLERANCE_PCT), 2)

    @property
    def per_trade_risk_max(self) -> float:
        return round(self.per_trade_risk_budget() * (1.0 + RISK_BUDGET_TOLERANCE_PCT), 2)

    def per_trade_risk_floor(self, *, sandbox_fallback: bool = False) -> float:
        tol = risk_budget_tolerance_pct(sandbox_fallback=sandbox_fallback)
        return round(self.per_trade_risk_budget() * (1.0 - tol), 2)

    def per_trade_risk_ceiling(self, *, sandbox_fallback: bool = False) -> float:
        tol = risk_budget_tolerance_pct(sandbox_fallback=sandbox_fallback)
        return round(self.per_trade_risk_budget() * (1.0 + tol), 2)

    def evaluate(
        self,
        *,
        realized_pnl_today: float,
        open_risk_notional: float,
        proposed_trade_risk: float,
        vrp_regime: str = "",
        sandbox_fallback: bool = False,
    ) -> tuple[RiskVerdict, str]:
        budget = self.per_trade_risk_budget()
        floor = self.per_trade_risk_floor(sandbox_fallback=sandbox_fallback)
        ceiling = self.per_trade_risk_ceiling(sandbox_fallback=sandbox_fallback)

        if realized_pnl_today <= -self.max_daily_loss_hard:
            return RiskVerdict.HALT, "hard_daily_loss_breach_2pct_nav"

        if open_risk_notional + proposed_trade_risk > self.max_concurrent_risk:
            return RiskVerdict.HALT, "max_concurrent_risk_exceeded"

        if proposed_trade_risk > ceiling:
            return RiskVerdict.HALT, "per_trade_risk_exceeds_fixed_fractional"

        if proposed_trade_risk > 0 and proposed_trade_risk < floor:
            if sandbox_fallback:
                return RiskVerdict.APPROVED, "sandbox_fallback_floor_waived"
            return RiskVerdict.HALT, "per_trade_risk_below_fixed_fractional_floor"

        if self.capital_drag_active and proposed_trade_risk > budget * 1.01:
            return RiskVerdict.REDUCE_SIZE, "capital_drag_brake_active"

        if realized_pnl_today <= -self.max_daily_loss_soft:
            return RiskVerdict.REDUCE_SIZE, "soft_daily_loss_reduce_size"

        if self.capital_drag_active:
            return RiskVerdict.APPROVED, "capital_drag_brake_reduced_risk"

        if self.approve_sandbox_live_route(vrp_regime=vrp_regime):
            return RiskVerdict.APPROVED, "sandbox_vol_override_live_route"

        return RiskVerdict.APPROVED, "within_fixed_fractional_budget"
