"""Manus state recovery guard — Phase 6 crash recovery matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence


class RecoveryVerdict(str, Enum):
    CLEAN = "CLEAN"
    RECOVERED = "RECOVERED"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    CORRUPT_STATE = "CORRUPT_STATE"
    NO_STATE = "NO_STATE"


class MismatchType(str, Enum):
    MISSING_IN_PORTFOLIO = "missing_in_portfolio"
    EXTRA_IN_PORTFOLIO = "extra_in_portfolio"
    CONTRACT_COUNT = "contract_count_mismatch"
    STRIKE_MISMATCH = "strike_mismatch"
    STRATEGY_MISMATCH = "strategy_mismatch"


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Normalized open position for cross-reference."""

    position_key: str
    symbol: str
    strategy_type: str
    target_strike: float
    contracts: int
    entry_credit: float = 0.0
    max_loss: float = 0.0
    trade_id: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PositionSnapshot:
        strike = float(raw.get("target_strike") or raw.get("strike") or 0)
        symbol = str(raw.get("symbol") or "SPY")
        strategy = str(raw.get("strategy_type") or "CREDIT_SPREAD")
        contracts = int(raw.get("contracts") or 0)
        trade_id = str(raw.get("trade_id") or "")
        key = trade_id or f"{symbol}:{strategy}:{strike:.1f}"
        return cls(
            position_key=key,
            symbol=symbol,
            strategy_type=strategy,
            target_strike=strike,
            contracts=contracts,
            entry_credit=float(raw.get("entry_credit") or 0),
            max_loss=float(raw.get("max_loss") or 0),
            trade_id=trade_id,
        )


@dataclass
class PositionMismatch:
    mismatch_type: MismatchType
    position_key: str
    state_contracts: int = 0
    portfolio_contracts: int = 0
    detail: str = ""


@dataclass
class RecoveryResult:
    verdict: RecoveryVerdict
    block_new_trades: bool
    mismatches: list[PositionMismatch] = field(default_factory=list)
    message: str = ""
    state_version: int = 0
    recovered_session: dict[str, Any] = field(default_factory=dict)


PortfolioProvider = Callable[[], Sequence[PositionSnapshot]]


@dataclass
class StateRecoveryGuard:
    """
    Critical recovery matrix — blocks new execution on position mismatch.

    After mid-day reboot, state file open positions must match the live or
    simulated portfolio exactly before trading resumes.
    """

    require_exact_match: bool = True

    def compare_positions(
        self,
        state_positions: Sequence[PositionSnapshot],
        portfolio_positions: Sequence[PositionSnapshot],
    ) -> list[PositionMismatch]:
        mismatches: list[PositionMismatch] = []
        state_map = {p.position_key: p for p in state_positions}
        port_map = {p.position_key: p for p in portfolio_positions}

        for key, state_pos in state_map.items():
            port_pos = port_map.get(key)
            if port_pos is None:
                mismatches.append(
                    PositionMismatch(
                        mismatch_type=MismatchType.MISSING_IN_PORTFOLIO,
                        position_key=key,
                        state_contracts=state_pos.contracts,
                        portfolio_contracts=0,
                        detail="state_position_not_in_portfolio",
                    )
                )
                continue
            if state_pos.contracts != port_pos.contracts:
                mismatches.append(
                    PositionMismatch(
                        mismatch_type=MismatchType.CONTRACT_COUNT,
                        position_key=key,
                        state_contracts=state_pos.contracts,
                        portfolio_contracts=port_pos.contracts,
                        detail="contract_count_mismatch",
                    )
                )
            if abs(state_pos.target_strike - port_pos.target_strike) > 0.01:
                mismatches.append(
                    PositionMismatch(
                        mismatch_type=MismatchType.STRIKE_MISMATCH,
                        position_key=key,
                        detail=f"state={state_pos.target_strike}_portfolio={port_pos.target_strike}",
                    )
                )
            if state_pos.strategy_type != port_pos.strategy_type:
                mismatches.append(
                    PositionMismatch(
                        mismatch_type=MismatchType.STRATEGY_MISMATCH,
                        position_key=key,
                        detail=f"state={state_pos.strategy_type}_portfolio={port_pos.strategy_type}",
                    )
                )

        for key, port_pos in port_map.items():
            if key not in state_map:
                mismatches.append(
                    PositionMismatch(
                        mismatch_type=MismatchType.EXTRA_IN_PORTFOLIO,
                        position_key=key,
                        state_contracts=0,
                        portfolio_contracts=port_pos.contracts,
                        detail="portfolio_position_not_in_state",
                    )
                )

        return mismatches

    def evaluate_recovery(
        self,
        *,
        state_payload: dict[str, Any] | None,
        portfolio_provider: PortfolioProvider,
    ) -> RecoveryResult:
        if not state_payload:
            return RecoveryResult(
                verdict=RecoveryVerdict.NO_STATE,
                block_new_trades=False,
                message="no_state_file_clean_start",
            )

        version = int(state_payload.get("version") or 0)
        session = dict(state_payload.get("session") or {})
        raw_positions = state_payload.get("open_positions") or []
        state_positions = [PositionSnapshot.from_dict(p) for p in raw_positions if isinstance(p, dict)]

        try:
            portfolio_positions = list(portfolio_provider())
        except Exception as exc:
            return RecoveryResult(
                verdict=RecoveryVerdict.CORRUPT_STATE,
                block_new_trades=True,
                message=f"portfolio_provider_failed:{exc}",
                state_version=version,
            )

        mismatches = self.compare_positions(state_positions, portfolio_positions)
        if mismatches and self.require_exact_match:
            return RecoveryResult(
                verdict=RecoveryVerdict.POSITION_MISMATCH,
                block_new_trades=True,
                mismatches=mismatches,
                message="position_mismatch_block_new_trades",
                state_version=version,
                recovered_session=session,
            )

        verdict = RecoveryVerdict.RECOVERED if state_payload else RecoveryVerdict.CLEAN
        return RecoveryResult(
            verdict=verdict,
            block_new_trades=False,
            mismatches=mismatches,
            message="state_recovered_ok",
            state_version=version,
            recovered_session=session,
        )
