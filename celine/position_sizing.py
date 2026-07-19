"""
Celine — MES contract sizing from fixed-fractional risk budget.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.config import DEFAULT_STOP_TICKS, TICK_VALUE, paper_max_mes_contracts, ticks_to_dollars


@dataclass(frozen=True, slots=True)
class FuturesSizeResult:
    symbol: str
    direction: str
    contracts: int
    stop_ticks: int
    target_ticks: int
    max_loss_per_contract: float
    total_trade_risk: float
    per_trade_risk_budget: float


def size_mes_position(
    *,
    direction: str,
    risk_budget: float,
    stop_ticks: int = DEFAULT_STOP_TICKS,
    target_ticks: int | None = None,
    symbol: str = "MES",
) -> FuturesSizeResult:
    """Contracts = floor(risk_budget / max_loss_per_contract)."""
    from engine.config import DEFAULT_TARGET_TICKS

    tgt = target_ticks if target_ticks is not None else DEFAULT_TARGET_TICKS
    max_loss_one = ticks_to_dollars(stop_ticks, 1)
    if max_loss_one <= 0:
        contracts = 0
    else:
        contracts = max(int(risk_budget // max_loss_one), 0)
        if contracts < 1 and risk_budget >= max_loss_one * 0.85:
            contracts = 1

    cap = paper_max_mes_contracts()
    if contracts > cap:
        contracts = cap

    total_risk = ticks_to_dollars(stop_ticks, contracts)
    return FuturesSizeResult(
        symbol=symbol,
        direction=direction,
        contracts=contracts,
        stop_ticks=stop_ticks,
        target_ticks=tgt,
        max_loss_per_contract=max_loss_one,
        total_trade_risk=total_risk,
        per_trade_risk_budget=round(risk_budget, 2),
    )
