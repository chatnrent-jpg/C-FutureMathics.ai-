"""
FutureMathics — shared configuration (MES micro E-mini S&P 500 futures).

Mirrors MarketMathics risk architecture; instrument constants are futures-specific.
"""

from __future__ import annotations

import os
from pathlib import Path

from engine.env_loader import load_project_env

load_project_env(Path(__file__).resolve().parent.parent)

# Instrument — CME Micro E-mini S&P 500 (Webull futures product root)
EXECUTION_SYMBOL = "MES"
POINT_VALUE = 5.0  # USD per index point per contract
TICK_SIZE = 0.25  # index points
TICK_VALUE = POINT_VALUE * TICK_SIZE  # $1.25 per tick per contract

# ---------------------------------------------------------------------------
# Webull OpenAPI — exclusive broker for FutureMathics virtue execution
# Credentials load from .env.local (WEBULL_APP_KEY / WEBULL_APP_SECRET / account ids)
# ---------------------------------------------------------------------------
WEBULL_NETWORK_TIMEOUT_S = 5.0  # hard socket timeout (Justice)
WEBULL_PRODUCT_ROOT = EXECUTION_SYMBOL  # maps to Webull US_FUTURES code (MES → MESU6 etc.)


def _env_str(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            return raw
    return default


def webull_app_key() -> str:
    return _env_str("WEBULL_APP_KEY", "WEBULL_API_KEY")


def webull_app_secret() -> str:
    return _env_str("WEBULL_APP_SECRET", "WEBULL_API_SECRET")


def webull_api_host_name() -> str:
    return _env_str("WEBULL_API_HOST", default="api.webull.com")


def webull_access_token() -> str:
    return _env_str("WEBULL_ACCESS_TOKEN", "WEBULL_TOKEN")


def webull_futures_account_id() -> str | None:
    """Prefer futures OpenAPI account id; fall back to long-form WEBULL_ACCOUNT_ID."""
    futures = _env_str("WEBULL_FUTURES_ACCOUNT_ID")
    if futures:
        return futures
    legacy = _env_str("WEBULL_ACCOUNT_ID")
    # OpenAPI ids are long opaque strings; short UI account numbers are not usable alone
    if legacy and len(legacy) >= 20:
        return legacy
    return None


def webull_futures_symbol() -> str | None:
    """Optional forced front-month symbol (e.g. MESU6); else resolve via Webull instrument list."""
    forced = _env_str("WEBULL_FUTURES_SYMBOL", "FM_EXECUTION_CONTRACT")
    return forced.upper() if forced else None


def webull_credentials_configured() -> bool:
    return bool(webull_app_key() and webull_app_secret())


# Capital baseline — $10k real-market paper test (Temperance: 1 MES)
STARTING_NAV = 10_000.0
HANDSHAKE_EQUITY_BASE = 10_000.0

MAX_DAILY_LOSS_PCT = 0.02
HARD_DAILY_STOP = 200.0  # 2% of $10k
# 0.8% of $10k = $80 → 1 MES @ 60-tick stop ($75) still fits after small adverse days
FIXED_FRACTIONAL_RISK_PCT = 0.008
MAX_CONCURRENT_RISK_PCT = 0.05
PER_TRADE_RISK_MIN = 60.0
PER_TRADE_RISK_MAX = 90.0

# Paper forward-test — $10k / 1 MES
FORWARD_TEST_MAX_CONCURRENT_RISK_PCT = 0.05
FORWARD_TEST_FIXED_FRACTIONAL_RISK_PCT = 0.008
FORWARD_TEST_MAX_DAILY_LOSS_PCT = 0.02
PAPER_MAX_MES_CONTRACTS = 1
PAPER_MAX_OPEN_MES_POSITIONS = 1

# Live — aligned to $10k test book when FORWARD_TEST_MODE=false
LIVE_RISK_NAV_CAP = 10_000.0
LIVE_MAX_DAILY_LOSS = 200.0
LIVE_MAX_CONCURRENT_RISK_PCT = 0.05
LIVE_FIXED_FRACTIONAL_RISK_PCT = 0.008

DRAWDOWN_BRAKE_PCT = 0.10
CAPITAL_DRAG_MULTIPLIER = 0.5
RISK_BUDGET_TOLERANCE_PCT = 0.15
SANDBOX_FALLBACK_RISK_BUDGET_TOLERANCE_PCT = 0.75

LATENCY_CEILING_MS = 200.0
SANDBOX_LATENCY_CEILING_MS = 600.0
MAX_ALLOWED_SPREAD_TICKS = 2  # max bid/ask spread in ticks for entry

# Strategy defaults
DEFAULT_STOP_TICKS = 60  # 60 ticks = 15 points = $75/contract risk (SWING)
DEFAULT_TARGET_TICKS = 120  # 120 ticks = 30 points = $150/contract profit (2:1 R:R SWING)
# Virtue scale-out: at target, bank most size and leave a runner (e.g. 3→close 2, leave 1).
# On $10k / 1 MES, leave=1 means no partial scale-out (full exit via stop/flat/flip/TP path).
SCALE_OUT_LEAVE_CONTRACTS = 1
VWAP_ENTRY_THRESHOLD_TICKS = 8  # min distance from VWAP to enter (legacy, not used in swing)
MIN_CONFIDENCE_THRESHOLD = 0.65  # Only take signals with 65%+ confidence (SWING quality)
MIN_SECONDS_BETWEEN_TRADES = 14400  # 4 hours between trades (SWING frequency)

# Swing trading parameters
SWING_MODE = True  # Toggle between swing (True) and scalp (False) strategies
SWING_MIN_TREND_STRENGTH = 0.60  # Minimum trend strength for entry (0-1.0)
SWING_MAX_TRADES_PER_DAY = 5  # Maximum 5 swing trades per day
SWING_TRAILING_STOP_TICKS = 30  # Trail by 30 ticks after 50% to target
SWING_CONTRACTS = 1  # $10k test: 1 contract only

# ---------------------------------------------------------------------------
# VolumeWatch grade-path MES strategy (PRIMARY for directional futures)
# Rising (from down): cash 0–65 → LONG ≥65 → EXIT ≥85 → SHORT ≥90
# Falling (from up): EXIT SHORT ≤50 → cash below 50
# ---------------------------------------------------------------------------
GRADE_MODE = True
GRADE_LONG_ENTRY = 50.0  # recovery gate (was 55; enter as soon as washout recovers above 50)
GRADE_LONG_EXIT = 85.0
GRADE_SHORT_ENTRY = 90.0
GRADE_SHORT_EXIT = 50.0
GRADE_PATH_EPSILON = 0.35
GRADE_SCORE_SOURCE = "overall"  # "overall" | "1m"
GRADE_STALE_SECONDS = 300.0
GRADE_ALLOW_STALE = False
GRADE_CONTRACTS = 1  # $10k test: 1 MES
# Protective only — grade owns the real exit (≥85). Wide so MES turbulence does not stop out rising-path longs.
# 200 ticks = 50 pts = $250/contract.
GRADE_HARD_STOP_TICKS = 200
GRADE_CYCLE_INTERVAL_S = 30.0  # poll VolumeWatch + MES frequently
GRADE_DAILY_PROFIT_LOCK = 250.0  # ~2.5% of $10k — halt new entries after strong day
GRADE_DAILY_LOSS_HALT = 200.0  # 2% of $10k

FORWARD_TEST_MODE = True
FORWARD_TEST_CYCLE_INTERVAL_S = 900.0  # 15 minutes for swing (was 2.0 for scalping)
FORWARD_TEST_RECONNECT_SLEEP_S = 60.0

# CME MES Futures Market Hours (America/Chicago native, converted to ET for consistency)
# Trading: Sunday 6:00 PM ET through Friday 5:00 PM ET
# Daily maintenance break: 5:00 PM - 6:00 PM ET (Mon-Thu)
# Weekend closure: Friday 5:00 PM ET - Sunday 6:00 PM ET
FORWARD_TEST_MARKET_OPEN_HOUR = 18  # 6:00 PM ET (Sunday open)
FORWARD_TEST_MARKET_OPEN_MINUTE = 0
FORWARD_TEST_MARKET_CLOSE_HOUR = 17  # 5:00 PM ET (Friday close)
FORWARD_TEST_MARKET_CLOSE_MINUTE = 0
FORWARD_TEST_MAINTENANCE_START_HOUR = 17  # 5:00 PM ET (daily break start)
FORWARD_TEST_MAINTENANCE_END_HOUR = 18     # 6:00 PM ET (daily break end)
FORWARD_TEST_TIMEZONE = "America/New_York"

# Virtue live / paper session: cash Regular Trading Hours only (Alpaca SPY live).
# Avoids evening / weekend MES gap risk when decisions are SPY-proxied.
VIRTUE_RTH_ONLY = True
VIRTUE_RTH_OPEN_HOUR = 9
VIRTUE_RTH_OPEN_MINUTE = 30
VIRTUE_RTH_CLOSE_HOUR = 16  # exclusive — flatten at/after 4:00 PM ET
VIRTUE_RTH_CLOSE_MINUTE = 0
# No new entries in the last 15 minutes of RTH (manage/exit only — gap avoidance).
VIRTUE_NO_NEW_ENTRY_HOUR = 15
VIRTUE_NO_NEW_ENTRY_MINUTE = 45
# Outside RTH: retry flatten until flat (or attempts exhausted).
VIRTUE_RTH_FLATTEN_MAX_ATTEMPTS = 10
VIRTUE_RTH_FLATTEN_RETRY_S = 3.0
# Hysteresis bands (Temperance — hard to enter, harder to whipsaw out)
# Enter only on strong agreement; while holding, exit/flip only at opposite exit band.
VIRTUE_SCORE_LONG_ENTER = 62.0   # both VWAP+TWAP >= this to ENTER long from flat
VIRTUE_SCORE_SHORT_ENTER = 38.0  # both VWAP+TWAP <= this to ENTER short from flat
VIRTUE_SCORE_LONG_EXIT = 42.0    # while LONG, flip/exit only when both <= this
VIRTUE_SCORE_SHORT_EXIT = 58.0   # while SHORT, flip/exit only when both >= this
VIRTUE_REQUIRED_STREAK = 2       # consecutive clear entry cycles before fire
# After anchor rebase, skip new entries for N cycles (scores are artificially near 50)
VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES = 2
# Dynamic take-profit: max(floor, atr_in_ticks * ATR_TP_MULT)
VIRTUE_TP_ATR_MULT = 1.5
VIRTUE_TP_MIN_TICKS = DEFAULT_TARGET_TICKS  # floor = 120 ticks
# Rebase when |VWAP − TWAP| exceeds this many ATRs (anchor disagreement)
VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT = 3.0
# Concurrent Justice layer: throttle writes to primary data/system_state.json
VIRTUE_STATE_PERSIST_INTERVAL_S = 2.0
# Event-driven market tick poll cadence (replaces old 30s blocking sleep loop)
VIRTUE_TICK_POLL_S = 5.0

# Friendly aliases (engine-sketch names)
LONG_ENTER = VIRTUE_SCORE_LONG_ENTER
SHORT_ENTER = VIRTUE_SCORE_SHORT_ENTER
LONG_EXIT = VIRTUE_SCORE_LONG_EXIT
SHORT_EXIT = VIRTUE_SCORE_SHORT_EXIT
REQUIRED_STREAK = VIRTUE_REQUIRED_STREAK
MAX_DAILY_LOSS = HARD_DAILY_STOP
PROFIT_LOCK = GRADE_DAILY_PROFIT_LOCK

# Simulated MES price anchor (updated from live feed when wired)
WARMUP_MES_PRICE = 6200.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def forward_test_force_paper() -> bool:
    return bool(FORWARD_TEST_MODE)


def risk_mode_paper() -> bool:
    return forward_test_force_paper()


def effective_risk_nav(account_nav: float) -> float:
    nav = max(float(account_nav or 0.0), 0.0)
    if risk_mode_paper():
        return nav
    return min(nav, LIVE_RISK_NAV_CAP)


def concurrent_risk_pct() -> float:
    if risk_mode_paper():
        return _env_float("FM_PAPER_MAX_CONCURRENT_RISK_PCT", FORWARD_TEST_MAX_CONCURRENT_RISK_PCT)
    return _env_float("FM_LIVE_MAX_CONCURRENT_RISK_PCT", LIVE_MAX_CONCURRENT_RISK_PCT)


def fixed_fractional_risk_pct() -> float:
    if risk_mode_paper():
        return _env_float("FM_PAPER_PER_TRADE_RISK_PCT", FORWARD_TEST_FIXED_FRACTIONAL_RISK_PCT)
    return _env_float("FM_LIVE_PER_TRADE_RISK_PCT", LIVE_FIXED_FRACTIONAL_RISK_PCT)


def concurrent_risk_cap(account_nav: float) -> float:
    return round(effective_risk_nav(account_nav) * concurrent_risk_pct(), 2)


def max_daily_loss_cap(account_nav: float) -> float:
    enav = effective_risk_nav(account_nav)
    if risk_mode_paper():
        pct = _env_float("FM_PAPER_MAX_DAILY_LOSS_PCT", FORWARD_TEST_MAX_DAILY_LOSS_PCT)
        return round(enav * pct, 2)
    return min(round(enav * MAX_DAILY_LOSS_PCT, 2), LIVE_MAX_DAILY_LOSS)


def latency_ceiling_ms(*, sandbox: bool = False) -> float:
    return SANDBOX_LATENCY_CEILING_MS if sandbox else LATENCY_CEILING_MS


def risk_budget_tolerance_pct(*, sandbox_fallback: bool = False) -> float:
    return SANDBOX_FALLBACK_RISK_BUDGET_TOLERANCE_PCT if sandbox_fallback else RISK_BUDGET_TOLERANCE_PCT


def paper_max_mes_contracts() -> int:
    raw = os.getenv("FM_PAPER_MAX_MES_CONTRACTS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return PAPER_MAX_MES_CONTRACTS


def paper_max_open_mes_positions() -> int:
    raw = os.getenv("FM_PAPER_MAX_OPEN_MES_POSITIONS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return PAPER_MAX_OPEN_MES_POSITIONS


def ticks_to_dollars(ticks: float, contracts: int = 1) -> float:
    return round(ticks * TICK_VALUE * contracts, 2)


def points_to_dollars(points: float, contracts: int = 1) -> float:
    return round(points * POINT_VALUE * contracts, 2)
