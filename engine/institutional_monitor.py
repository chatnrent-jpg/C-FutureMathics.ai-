"""
FutureMathics Institutional Strategy Health Monitor & Circuit Breakers.

Self-monitoring: Detect when strategy is underperforming and stand aside.

4 circuit breakers (any triggers → halt new entries):
1. Win rate < 30% over last 10 trades (strategy broken)
2. Avg PnL < -$10 over last 10 trades (bleeding capital)
3. 3+ consecutive losses (reset mode, wait for clean A+ setup)
4. Daily PnL ≤ -$250 (near daily loss limit, preserve remaining capital)

Institutional algos know when to stop trading, not just when to trade.
"""

from __future__ import annotations


def evaluate_strategy_health(
    *,
    trades_today: int,
    consecutive_losses: int,
    daily_pnl: float,
    recent_trades: list[dict] | None = None,
) -> tuple[bool, str, str]:
    """
    Evaluate strategy health and circuit breakers.

    Returns: (healthy, reason, breaker_name)
    - healthy: True if all circuit breakers clear, False if any triggered
    - reason: Human-readable explanation
    - breaker_name: Which breaker triggered (CB1-CB4) or "HEALTHY"

    Circuit breakers (checked in order):
    CB1: Win rate collapsed (< 30% over last 10 trades)
    CB2: Bleeding capital (avg PnL < -$10 over last 10 trades)
    CB3: Consecutive loss streak (≥3 losses in a row)
    CB4: Daily PnL approaching limit (≤ -$250)
    """
    from engine.config import MAX_DAILY_LOSS

    try:
        from engine.config import INSTITUTIONAL_CB_DAILY_PNL_FLOOR
    except ImportError:
        INSTITUTIONAL_CB_DAILY_PNL_FLOOR = -250.0

    trades = int(trades_today or 0)
    losses = int(consecutive_losses or 0)
    pnl = float(daily_pnl or 0.0)
    recent = recent_trades or []

    # ============================================================
    # Circuit Breaker 3: Consecutive loss streak (check first, no min trades needed)
    # ============================================================
    if losses >= 3:
        return False, (
            f"CB3_CONSECUTIVE_LOSSES | {losses} consecutive losses — "
            f"reset mode (wait for clean A+ setup)"
        ), "CB3"

    # ============================================================
    # Circuit Breaker 4: Daily PnL floor (capital preservation)
    # ============================================================
    pnl_floor = float(INSTITUTIONAL_CB_DAILY_PNL_FLOOR)
    if pnl <= pnl_floor:
        max_loss = float(MAX_DAILY_LOSS)
        return False, (
            f"CB4_DAILY_PNL_FLOOR | Daily PnL ${pnl:.0f} ≤ ${pnl_floor:.0f} — "
            f"preserve capital (hard stop at ${max_loss:.0f})"
        ), "CB4"

    # ============================================================
    # Circuit Breakers 1 & 2: Require at least 10 trades for stats
    # ============================================================
    if trades < 10:
        # Not enough trades yet to evaluate win rate / avg PnL
        return True, f"HEALTHY — {trades} trades today (need 10+ for win rate check)", "HEALTHY"

    # Calculate recent performance stats
    if recent and len(recent) >= 10:
        last_10 = recent[-10:]
        wins = sum(1 for t in last_10 if t.get("outcome") == "WIN")
        win_rate = wins / 10.0
        avg_pnl = sum(t.get("pnl", 0.0) for t in last_10) / 10.0
    else:
        # No recent trade data available, skip stats checks
        return True, "HEALTHY — recent trade data not available", "HEALTHY"

    # ============================================================
    # Circuit Breaker 1: Win rate collapsed
    # ============================================================
    if win_rate < 0.30:
        return False, (
            f"CB1_WIN_RATE_COLLAPSED | Win rate {win_rate*100:.0f}% < 30% over last 10 trades — "
            f"strategy edge lost (stand aside)"
        ), "CB1"

    # ============================================================
    # Circuit Breaker 2: Bleeding capital
    # ============================================================
    if avg_pnl < -10.0:
        return False, (
            f"CB2_BLEEDING_CAPITAL | Avg PnL ${avg_pnl:.0f} < -$10 over last 10 trades — "
            f"net negative (stand aside)"
        ), "CB2"

    # ============================================================
    # All circuit breakers clear: HEALTHY
    # ============================================================
    return True, (
        f"HEALTHY — trades={trades} win_rate={win_rate*100:.0f}% "
        f"avg_pnl=${avg_pnl:.0f} losses={losses} daily_pnl=${pnl:.0f}"
    ), "HEALTHY"


def is_circuit_breaker_tripped(
    *,
    consecutive_losses: int,
    daily_pnl: float,
) -> tuple[bool, str]:
    """
    Quick check if any circuit breaker is tripped (simplified for fast path).

    Returns: (tripped, reason)
    """
    try:
        from engine.config import INSTITUTIONAL_CB_DAILY_PNL_FLOOR
    except ImportError:
        INSTITUTIONAL_CB_DAILY_PNL_FLOOR = -250.0

    losses = int(consecutive_losses or 0)
    pnl = float(daily_pnl or 0.0)

    if losses >= 3:
        return True, f"CB3 tripped — {losses} consecutive losses"

    pnl_floor = float(INSTITUTIONAL_CB_DAILY_PNL_FLOOR)
    if pnl <= pnl_floor:
        return True, f"CB4 tripped — daily PnL ${pnl:.0f} ≤ ${pnl_floor:.0f}"

    return False, "No circuit breakers tripped"


def calculate_recent_win_rate(recent_trades: list[dict], lookback: int = 10) -> float:
    """
    Calculate win rate over last N trades.

    Returns: win_rate (0.0 - 1.0)
    """
    if not recent_trades or len(recent_trades) < lookback:
        return 0.5  # Default neutral if not enough data

    last_n = recent_trades[-lookback:]
    wins = sum(1 for t in last_n if t.get("outcome") == "WIN")
    return wins / lookback


def calculate_recent_avg_pnl(recent_trades: list[dict], lookback: int = 10) -> float:
    """
    Calculate average PnL over last N trades.

    Returns: avg_pnl (dollars)
    """
    if not recent_trades or len(recent_trades) < lookback:
        return 0.0  # Default neutral if not enough data

    last_n = recent_trades[-lookback:]
    total_pnl = sum(t.get("pnl", 0.0) for t in last_n)
    return total_pnl / lookback


__all__ = [
    "evaluate_strategy_health",
    "is_circuit_breaker_tripped",
    "calculate_recent_win_rate",
    "calculate_recent_avg_pnl",
]
