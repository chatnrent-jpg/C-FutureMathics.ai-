"""
Celine — fixed-fractional compounding math (Phase 10).

Per-trade risk = 0.5% of rolling NAV, with income projection to annual targets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from engine.config import FIXED_FRACTIONAL_RISK_PCT, STARTING_NAV

# Annual income target for projection timeline
ANNUAL_INCOME_GOAL_USD = 1_000_000.0
TRADING_DAYS_PER_YEAR = 252

# 100-day hyper-speed simulation reference (Phase 9 baseline)
SIM_100D_START_NAV = STARTING_NAV
SIM_100D_END_NAV = 160_242.72
SIM_100D_TRADING_DAYS = 100


def fixed_fractional_risk(nav: float, *, drag_multiplier: float = 1.0) -> float:
    """
    Fixed-Fractional Sizing Rule: risk_budget = NAV × 0.5% × drag_multiplier.

    Examples:
      $100,000 NAV → $500/trade
      $160,000 NAV → $800/trade
    """
    nav = max(nav, 0.0)
    multiplier = max(0.0, min(1.0, drag_multiplier))
    return round(nav * FIXED_FRACTIONAL_RISK_PCT * multiplier, 2)


def contracts_from_risk(
    *,
    risk_budget: float,
    max_loss_per_contract: float,
) -> int:
    """Integer contract count from dynamic risk budget and spread max loss."""
    if max_loss_per_contract <= 0 or risk_budget <= 0:
        return 0
    return max(0, int(math.floor(risk_budget / max_loss_per_contract)))


@dataclass(frozen=True, slots=True)
class CompoundingProjection:
    """Timeline model toward $1M annualized income using compounding."""

    starting_nav: float
    current_nav: float
    sim_daily_return_rate: float
    sim_annualized_return_pct: float
    avg_daily_pnl_observed: float
    annual_income_goal: float
    daily_income_goal: float
    projected_trading_days_to_goal: int
    projected_calendar_months: float
    nav_required_for_goal_at_current_edge: float
    projected_trading_days_to_nav_required: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "starting_nav": self.starting_nav,
            "current_nav": self.current_nav,
            "sim_daily_return_rate": round(self.sim_daily_return_rate, 6),
            "sim_annualized_return_pct": round(self.sim_annualized_return_pct, 2),
            "avg_daily_pnl_observed": round(self.avg_daily_pnl_observed, 2),
            "annual_income_goal": self.annual_income_goal,
            "daily_income_goal": round(self.daily_income_goal, 2),
            "projected_trading_days_to_goal": self.projected_trading_days_to_goal,
            "projected_calendar_months": round(self.projected_calendar_months, 1),
            "nav_required_for_goal_at_current_edge": round(
                self.nav_required_for_goal_at_current_edge, 2
            ),
            "projected_trading_days_to_nav_required": self.projected_trading_days_to_nav_required,
        }


def _safe_log(x: float) -> float:
    return math.log(max(x, 1e-9))


def project_income_timeline(
    *,
    starting_nav: float = SIM_100D_START_NAV,
    ending_nav: float = SIM_100D_END_NAV,
    trading_days: int = SIM_100D_TRADING_DAYS,
    annual_income_goal: float = ANNUAL_INCOME_GOAL_USD,
    current_nav: float | None = None,
) -> CompoundingProjection:
    """
    Project timeline to $1M annualized income using 100-day simulation edge.

    Uses geometric daily return from the simulation, then estimates when
    expected daily PnL (NAV × daily_edge) reaches the income goal / 252.
    """
    start = max(starting_nav, 1.0)
    end = max(ending_nav, start)
    days = max(trading_days, 1)
    nav_now = current_nav if current_nav is not None else end

    daily_return = (end / start) ** (1.0 / days) - 1.0
    annualized = ((1.0 + daily_return) ** TRADING_DAYS_PER_YEAR - 1.0) * 100.0
    avg_daily_pnl = (end - start) / days
    daily_income_goal = annual_income_goal / TRADING_DAYS_PER_YEAR

    # Expected daily profit scales with NAV at observed edge
    edge = daily_return
    if edge > 0:
        nav_required = daily_income_goal / edge
        days_to_nav = int(math.ceil(_safe_log(nav_required / max(nav_now, 1.0)) / _safe_log(1.0 + edge)))
        days_to_goal = days_to_nav
    else:
        nav_required = float("inf")
        days_to_goal = 9999

    calendar_months = (days_to_goal / TRADING_DAYS_PER_YEAR) * 12.0

    return CompoundingProjection(
        starting_nav=start,
        current_nav=nav_now,
        sim_daily_return_rate=daily_return,
        sim_annualized_return_pct=annualized,
        avg_daily_pnl_observed=avg_daily_pnl,
        annual_income_goal=annual_income_goal,
        daily_income_goal=daily_income_goal,
        projected_trading_days_to_goal=days_to_goal,
        projected_calendar_months=calendar_months,
        nav_required_for_goal_at_current_edge=nav_required if nav_required != float("inf") else 0.0,
        projected_trading_days_to_nav_required=days_to_goal,
    )
