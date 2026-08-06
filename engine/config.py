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


# Capital baseline — $15k paper book for 2 MES scale-out runner (Temperance)
STARTING_NAV = 15_000.0
HANDSHAKE_EQUITY_BASE = 15_000.0

MAX_DAILY_LOSS_PCT = 0.02
HARD_DAILY_STOP = 300.0  # 2% of $15k
# 1.0% of $15k = $150 → exactly 2 MES @ 60-tick stop ($75 each)
FIXED_FRACTIONAL_RISK_PCT = 0.01
MAX_CONCURRENT_RISK_PCT = 0.05
PER_TRADE_RISK_MIN = 60.0
PER_TRADE_RISK_MAX = 160.0  # headroom for 2×$75 stop

# Paper forward-test — 2 MES (enter 2, TP scale-out leave 1 runner)
FORWARD_TEST_MAX_CONCURRENT_RISK_PCT = 0.05
FORWARD_TEST_FIXED_FRACTIONAL_RISK_PCT = 0.01
FORWARD_TEST_MAX_DAILY_LOSS_PCT = 0.02
PAPER_MAX_MES_CONTRACTS = 2
PAPER_MAX_OPEN_MES_POSITIONS = 1
# Dual-sleeve Unified Rules — account-wide ceiling (core + tactical).
MAX_ACCOUNT_CONTRACT_CEILING = 2
VIRTUE_CORE_SIZE = 1  # structural anchor sleeve
VIRTUE_CORE_CONFIRM_CYCLES = 5  # HTF bias+ADX confirm before opening core
VIRTUE_CORE_STRUCTURAL_ADX_MIN = 22.0
# Slow Invalidation depth from the long edge (sticky through NEUTRAL).
# LONG closes at/below this; SHORT closes at/above (100 - depth).
VIRTUE_CORE_INVALIDATION_BLEND_LONG = 40.0
VIRTUE_CORE_INVALIDATION_BLEND_SHORT = 60.0  # == 100 - LONG depth (legacy alias)
# Core adverse failsafe (Temperance) — sticky does not mean suicidal.
VIRTUE_CORE_MAX_ADVERSE_DOLLARS = 150.0
# 0 = disabled. Core is the HOLD sleeve — exit on structure/adverse $, not a short timer.
VIRTUE_CORE_MAX_HOLD_CYCLES = 0
# After core_invalidation close: block re-entry for this many seconds (hysteresis).
VIRTUE_CORE_INVALIDATION_COOLDOWN_S = 1200  # 20 minutes
# Early clear of invalidation cooldown when ADX proves a structural breakout.
VIRTUE_CORE_REENTRY_ADX_MIN = 28.0
# Core take-profit — bank structural premium before flip/invalidation whipsaws.
# Slightly tighter than tactical $100 so 1 MES proxy noise can still reach target.
VIRTUE_CORE_TP_DOLLARS = 75.0

# Live — aligned to $15k / 2 MES profile
LIVE_RISK_NAV_CAP = 15_000.0
LIVE_MAX_DAILY_LOSS = 300.0
LIVE_MAX_CONCURRENT_RISK_PCT = 0.05
LIVE_FIXED_FRACTIONAL_RISK_PCT = 0.01
LIVE_MAX_MES_CONTRACTS = 2  # hard Temperance cap for cash MES

DRAWDOWN_BRAKE_PCT = 0.10
CAPITAL_DRAG_MULTIPLIER = 0.5
RISK_BUDGET_TOLERANCE_PCT = 0.15
SANDBOX_FALLBACK_RISK_BUDGET_TOLERANCE_PCT = 0.75

LATENCY_CEILING_MS = 200.0
SANDBOX_LATENCY_CEILING_MS = 600.0
MAX_ALLOWED_SPREAD_TICKS = 2  # max bid/ask spread in ticks for entry

# Strategy defaults
DEFAULT_STOP_TICKS = 60  # 60 ticks = 15 points = $75/contract risk (SWING)
# Legacy swing default (Virtue uses VIRTUE_POSITION_TP_DOLLARS instead).
DEFAULT_TARGET_TICKS = 40  # ~$100 on 2 MES (40t × $1.25 × 2) — kept for non-Virtue helpers
# Virtue profit-taking: full flatten at dollar TP (no runner). Scale-out leave unused on Virtue path.
SCALE_OUT_LEAVE_CONTRACTS = 0
VWAP_ENTRY_THRESHOLD_TICKS = 8  # min distance from VWAP to enter (legacy, not used in swing)
MIN_CONFIDENCE_THRESHOLD = 0.65  # Only take signals with 65%+ confidence (SWING quality)
MIN_SECONDS_BETWEEN_TRADES = 14400  # 4 hours between trades (SWING frequency)

# Swing trading parameters
SWING_MODE = True  # Toggle between swing (True) and scalp (False) strategies
SWING_MIN_TREND_STRENGTH = 0.60  # Minimum trend strength for entry (0-1.0)
SWING_MAX_TRADES_PER_DAY = 5  # Maximum 5 swing trades per day
SWING_TRAILING_STOP_TICKS = 30  # Trail by 30 ticks after 50% to target
SWING_CONTRACTS = 2  # $15k / 2 MES scale-out profile

# ---------------------------------------------------------------------------
# VolumeWatch grade-path MES strategy (PRIMARY for directional futures)
# Rising (from down): cash 0–65 → LONG ≥65 → EXIT ≥85 → SHORT ≥90
# Falling (from up): EXIT SHORT ≤50 → cash below 50
# ---------------------------------------------------------------------------
GRADE_MODE = False  # VolumeWatch grade path quarantined — virtue brain is primary
GRADE_LONG_ENTRY = 50.0  # recovery gate (was 55; enter as soon as washout recovers above 50)
GRADE_LONG_EXIT = 85.0
GRADE_SHORT_ENTRY = 90.0
GRADE_SHORT_EXIT = 50.0
GRADE_PATH_EPSILON = 0.35
GRADE_SCORE_SOURCE = "overall"  # "overall" | "1m"
GRADE_STALE_SECONDS = 300.0
GRADE_ALLOW_STALE = False
GRADE_CONTRACTS = 2  # align with paper max (scale-out runner profile)
# Protective only — grade owns the real exit (≥85). Wide so MES turbulence does not stop out rising-path longs.
# 200 ticks = 50 pts = $250/contract.
GRADE_HARD_STOP_TICKS = 200
GRADE_CYCLE_INTERVAL_S = 30.0  # poll VolumeWatch + MES frequently
GRADE_DAILY_PROFIT_LOCK = 500.0  # ~3.3% of $15k — day done; no new entries (Temperance)
# Virtue Balance / Profit Guard — hard trailing lock (Temperance).
# Arm when peak realized >= $100; breach if daily realized drops to/below $25.
VIRTUE_PNL_LOCK_ARM_PEAK = 100.0
VIRTUE_PNL_LOCK_HARD_FLOOR = 25.0
PROFIT_GUARD_THRESHOLD = VIRTUE_PNL_LOCK_ARM_PEAK  # alias for macromathics_core
# Legacy retain-pct kept for UI/compat; phase1 uses HARD_FLOOR when armed.
PROFIT_GUARD_RETAIN_PCT = 0.25  # unused by hard-floor path (was 0.60 of peak)
VIRTUE_PNL_LOCK_FLOOR_FRAC = PROFIT_GUARD_RETAIN_PCT  # legacy alias
# Pipeline stuck-on-reboot health check (cycle depth after rebase).
VIRTUE_PIPELINE_STUCK_DEPTH = 100
VIRTUE_PIPELINE_STUCK_ALERT_S = 30.0
# 0 = stand aside after lock (do not keep trading smaller)
PROFIT_LOCK_MAX_CONTRACTS = 0
GRADE_DAILY_LOSS_HALT = 300.0  # 2% of $15k

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

# Virtue session hours:
# - Databento CME MES primary → full Globex timetable (Sun 18:00 – Fri 17:00 ET)
# - Alpaca SPY proxy fallback → cash RTH only (no overnight proxy guesses)
# Override with VIRTUE_SESSION_MODE=cme|rth|auto (default auto → follow data source)
VIRTUE_RTH_ONLY = True  # used only when session mode resolves to RTH (Alpaca)
VIRTUE_RTH_OPEN_HOUR = 9
VIRTUE_RTH_OPEN_MINUTE = 30
VIRTUE_RTH_CLOSE_HOUR = 16  # exclusive — flatten at/after 4:00 PM ET (RTH mode)
VIRTUE_RTH_CLOSE_MINUTE = 0
# Legacy single cutoff (superseded by dual RTH entry windows).
VIRTUE_NO_NEW_ENTRY_HOUR = 15
VIRTUE_NO_NEW_ENTRY_MINUTE = 55
# RTH new-entry windows (America/New_York) — start inclusive / end exclusive.
# Widened after paper evidence: rigid 15:30 cut blocked a clean late short.
VIRTUE_ENTRY_WINDOW_1_START = (9, 45)
VIRTUE_ENTRY_WINDOW_1_END = (11, 30)
VIRTUE_ENTRY_WINDOW_2_START = (13, 45)
VIRTUE_ENTRY_WINDOW_2_END = (15, 55)
# Extreme trend may enter outside windows while session still open (Courage).
VIRTUE_EXTREME_ADX_OVERRIDE = 40.0
VIRTUE_EXTREME_BLEND_LONG = 65.0
VIRTUE_EXTREME_BLEND_SHORT = 35.0
# Structure: at this ADX, waive ATR-expand + spread-widen if ADX still rising.
VIRTUE_STRUCTURE_EXTREME_ADX = 40.0
# CME Globex: no new entries in last 15 minutes before 5:00 PM ET daily maintenance.
VIRTUE_CME_NO_NEW_ENTRY_HOUR = 16
VIRTUE_CME_NO_NEW_ENTRY_MINUTE = 45
# Outside session: retry flatten until flat (or attempts exhausted).
VIRTUE_RTH_FLATTEN_MAX_ATTEMPTS = 10
VIRTUE_RTH_FLATTEN_RETRY_S = 3.0
# Databento quote staleness ceiling (seconds)
DATABENTO_MAX_QUOTE_AGE_S = 5.0
# Canonical ADX ladder (one floor for flat tactical / structure / velocity).
VIRTUE_ENTRY_ADX_MIN = 20.0
VIRTUE_ADX_ENTER_MIN = 20.0      # flat long entries need trend strength (Wisdom)
VIRTUE_ADX_SHORT_ENTER_MIN = 20.0  # default short floor when not below session VWAP
# Proxy ADX under-reads: allow shorts when price < session VWAP + short scores.
VIRTUE_ADX_SHORT_BELOW_VWAP_MIN = 8.0
VIRTUE_TACTICAL_ADX_MIN = 20.0
VIRTUE_VELOCITY_ADX_FLOOR = 20.0
# Latch day bias BEAR when session VWAP score stays at/below this (tape > sticky BULL).
VIRTUE_MACRO_BEAR_VWAP_SCORE_MAX = 45.0
VIRTUE_MACRO_BULL_VWAP_SCORE_MIN = 55.0
# Enter on clear edge; exit with hysteresis so mid-band chop cannot scalp every dip.
VIRTUE_SCORE_LONG_ENTER = 58.0   # both VWAP+TWAP >= this to ENTER long from flat
VIRTUE_SCORE_SHORT_ENTER = 42.0  # both VWAP+TWAP <= this to ENTER short from flat
VIRTUE_SCORE_LONG_EXIT = 45.0    # while LONG: flatten when both <= 45 (not mid-50)
VIRTUE_SCORE_SHORT_EXIT = 55.0   # while SHORT: flatten when both >= 55 (not mid-50)
VIRTUE_REQUIRED_STREAK = 2       # 2 clear cycles — faster Courage on clean tape
# Native MACRO participation gates (Wisdom) — no VolumeWatch dependency.
# Dead lift / dead volume cannot print LONG SETUP even if VWAP score is high.
VIRTUE_VOL_CONVICTION_LONG_MIN = 45.0
VIRTUE_MARKET_LIFT_LONG_MIN = 40.0
VIRTUE_VOL_CONVICTION_SHORT_MIN = 40.0
VIRTUE_MARKET_LIFT_SHORT_MAX = 45.0  # shorts need lift in bear/neutral zone
VIRTUE_VOL_DEAD_MAX = 35.0  # below → stand aside both ways (no participation)
# Hard daily round-trip cap for tactical sleeve (Temperance).
VIRTUE_MAX_TACTICAL_TRADES_PER_DAY = 12
# Bull-day asymmetric short filter — counter-trend shorts need confirmation.
# Lowered from 25: sticky BULL bias must not hard-block clear below-VWAP bears.
VIRTUE_BULL_DAY_SHORT_BLEND_MAX = 35.0  # blend must be <= this on BULL days
VIRTUE_BULL_DAY_SHORT_ADX_MIN = 18.0    # with below-VWAP path; was 25 (proxy never cleared)
# COURSE_CORRECT hysteresis — aligned with strategy exits (not mid-50 scalp).
VIRTUE_COURSE_CORRECT_SHORT_BLEND = 55.0  # SHORT + blend >= 55 → force flatten
VIRTUE_COURSE_CORRECT_LONG_BLEND = 45.0   # LONG + blend <= 45 → force flatten
# Chase: block only extreme late entries. 72 was freezing re-entry after $100 TP in bulls.
VIRTUE_SCORE_LONG_CHASE_MAX = 85.0
VIRTUE_SCORE_SHORT_CHASE_MIN = 15.0
# Legacy linear score scale (Wisdom now uses sticky bps vs session VWAP; kept for compat).
VIRTUE_SCORE_PRICE_PCT = 0.004
# After anchor rebase, skip new entries for N cycles (scores are artificially near 50)
VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES = 3
# Bank ~$100 per open position (full flatten), then cool down for the next clean signal.
# Fixes “up $300 → back to $19 with nothing taken” (Temperance).
VIRTUE_POSITION_TP_DOLLARS = 100.0
# Cut losers at ~$75 on the whole position (full flatten) — ~1.33:1 vs $100 TP.
VIRTUE_POSITION_STOP_DOLLARS = 75.0
# --- MACROMATHICS ENGINE PERFORMANCE CONFIGURATION ---
# Post-exit cool-offs must outlast mid-band noise (Temperance > scalp Courage).
VIRTUE_BASE_TP_COOLDOWN_CYCLES = 8
# Added per extra TP in a streak (anti-giveback after multi-TP runs).
VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES = 5
# Thesis broke / wrong side — match hard stop so we do not re-chop immediately.
VIRTUE_POST_COURSE_CORRECT_COOLDOWN_CYCLES = 12
# Hard dollar stop cool-down (Temperance).
VIRTUE_HARD_STOP_COOLDOWN_CYCLES = 12
# Time-decay: stagnation cut still needs a real cool-off (not instant re-fire).
VIRTUE_TIME_DECAY_COOLDOWN_CYCLES = 8
# Compat aliases (older call sites / tests).
VIRTUE_POST_TP_ENTRY_COOLDOWN_CYCLES = VIRTUE_BASE_TP_COOLDOWN_CYCLES
VIRTUE_POST_TP_STREAK_COOLDOWN_EXTRA = VIRTUE_STREAK_BONUS_COOLDOWN_CYCLES
VIRTUE_POST_STOP_ENTRY_COOLDOWN_CYCLES = VIRTUE_HARD_STOP_COOLDOWN_CYCLES
VIRTUE_POST_TIME_DECAY_COOLDOWN_CYCLES = VIRTUE_TIME_DECAY_COOLDOWN_CYCLES
VIRTUE_POST_TP_STREAK_PULLBACK_AFTER = 2  # after this many TPs, require blend pullback to re-enter
VIRTUE_POST_TP_PULLBACK_BLEND_LONG = 62.0   # long re-entry allowed only if blend <= this
VIRTUE_POST_TP_PULLBACK_BLEND_SHORT = 38.0  # short re-entry allowed only if blend >= this
# After 3+ consecutive TPs: wall-clock lock (anti-overtrading after a fully captured leg).
VIRTUE_MULTI_TP_COOLDOWN_STREAK = 3
VIRTUE_MULTI_TP_COOLDOWN_S = 900  # 15 minutes
# After a realized LOSS: extra entry-streak friction (Temperance sizing/confirmation).
VIRTUE_POST_LOSS_EXTRA_STREAK = 1
# Temperance sizing / blend friction from last_trade_outcome (MES irreducible lot = 1).
VIRTUE_TEMPERANCE_BASE_CONTRACTS = 1
VIRTUE_TEMPERANCE_LOSS_STREAK_MIN = 2  # losses >= this → widen entry band
VIRTUE_TEMPERANCE_LOSS_BLEND_BUFFER = 5.0  # +/− points on enter thresholds
VIRTUE_TEMPERANCE_COURSE_CORRECT_BLEND_BUFFER = 3.0  # post course_correct cooling
# Strong aligned trend: Temperance via extra confirmation streak, not unreachable blend.
# Prevents "signal LONG @58 / ADX 40" while live enter is stuck at 63 after loss friction.
VIRTUE_TEMPERANCE_STRONG_ADX_WAIVE = 25.0
# verify_pipeline_entry bases (temperance buffer widens these; bull shorts get extra penalty).
VIRTUE_PIPELINE_LONG_BLEND_BASE = 55.0
VIRTUE_PIPELINE_SHORT_BLEND_BASE = 45.0
VIRTUE_PIPELINE_BULL_SHORT_PENALTY = 5.0
# Velocity gate: widen blend bands when ADX is weak (blocks slow-drift traps).
# VIRTUE_VELOCITY_ADX_FLOOR set above with canonical ADX ladder (= 20.0)
VIRTUE_VELOCITY_PENALTY_PER_ADX = 0.5  # points added/subtracted per ADX unit below floor
# Time-decay: tactical SCALP only — cut dead/red holds, never knife a green trade.
# Green tactical waits for $100 TP / course-correct / $75 stop (Courage).
MAX_STAGNATION_CYCLES = 36  # ~3 min at 5s — enough to see if a scalp is alive
VIRTUE_TIME_DECAY_MAX_CYCLES = MAX_STAGNATION_CYCLES
VIRTUE_TIME_DECAY_MIN_OPEN_PNL = 0.0  # cut only if open_pnl <= 0 (flat/red stagnation)
# Legacy ATR TP helpers (Virtue exits use VIRTUE_POSITION_TP_DOLLARS; kept for tests/compat)
VIRTUE_TP_ATR_MULT = 1.5
VIRTUE_TP_MIN_TICKS = DEFAULT_TARGET_TICKS
# Rebase when |VWAP − TWAP| exceeds this many ATRs (anchor disagreement)
VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT = 3.0
# Concurrent Justice layer: throttle writes to primary data/system_state.json
# 3s keeps dashboard fresh without thrashing disk every tick
VIRTUE_STATE_PERSIST_INTERVAL_S = 3.0
# Listener poll (quote freshness). Engine may skip quiet flat ticks until force timer.
VIRTUE_TICK_POLL_S = 5.0
# Full broker heartbeat every N *engine* cycles (≈ old 30s load when events ~10s)
VIRTUE_HEARTBEAT_EVERY_N_CYCLES = 3
# Listener: skip enqueue when mid unchanged by less than this many ticks
VIRTUE_MIN_PRICE_MOVE_TICKS = 1
# When flat + quiet, still force an engine event this often (Temperance — stay awake)
VIRTUE_FORCE_EVENT_MAX_S = 10.0

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


def databento_api_key() -> str:
    return _env_str("DATABENTO_API_KEY")


def databento_configured() -> bool:
    return bool(databento_api_key())


def primary_data_source() -> str:
    """
    Resolve primary market-data source.
    FM_DATA_SOURCE=databento|alpaca|auto (default auto → databento if key set).
    """
    forced = os.getenv("FM_DATA_SOURCE", "auto").strip().lower()
    if forced in {"databento", "databento_mes", "cme"}:
        return "databento"
    if forced in {"alpaca", "alpaca_spy", "alpaca_spy_mes_proxy", "spy"}:
        return "alpaca"
    # auto
    if databento_configured():
        return "databento"
    return "alpaca"


def virtue_session_mode() -> str:
    """
    Trading timetable: 'cme' (Globex futures) or 'rth' (cash hours).

    VIRTUE_SESSION_MODE=cme|rth|auto
      auto → CME when Databento is primary, else RTH (Alpaca proxy).
    """
    forced = os.getenv("VIRTUE_SESSION_MODE", "auto").strip().lower()
    if forced in {"cme", "globex", "futures", "overnight"}:
        return "cme"
    if forced in {"rth", "cash", "equity"}:
        return "rth"
    # auto — follow the live data source (Databento ⇒ CME timetable)
    return "cme" if primary_data_source() == "databento" else "rth"


def virtue_rth_only() -> bool:
    """True → cash RTH gate; False → full CME Globex hours."""
    if virtue_session_mode() == "cme":
        return False
    return bool(VIRTUE_RTH_ONLY)


def forward_test_force_paper() -> bool:
    """
    Paper lock. Env override (Justice — no silent half-arm):
      FM_FORWARD_TEST_MODE=0|false|live  → paper OFF (live candidate)
      FM_FORWARD_TEST_MODE=1|true|paper → paper ON
      unset → FORWARD_TEST_MODE constant (default True)
    """
    raw = os.getenv("FM_FORWARD_TEST_MODE", "").strip().lower()
    if raw in {"0", "false", "no", "live", "off"}:
        return False
    if raw in {"1", "true", "yes", "paper", "on"}:
        return True
    return bool(FORWARD_TEST_MODE)


def trading_halted() -> bool:
    """Operator kill switch: FM_TRADING_HALTED=1 → no new risk."""
    return os.getenv("FM_TRADING_HALTED", "").strip().lower() in {"1", "true", "yes", "on"}


def live_allow_overnight() -> bool:
    """
    Live cash default: NO overnight Globex holds (Temperance for $15k).
    Paper CME may overnight. Opt-in live overnight: FM_LIVE_ALLOW_OVERNIGHT=1.
    """
    if forward_test_force_paper():
        return True
    return os.getenv("FM_LIVE_ALLOW_OVERNIGHT", "0").strip().lower() in {"1", "true", "yes", "on"}


def live_halt_flattens() -> bool:
    """When kill switch is on, also flatten open risk (default yes for cash)."""
    raw = os.getenv("FM_HALT_FLATTEN", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


def live_max_mes_contracts() -> int:
    """Hard contract cap for cash MES (Temperance). Override: FM_LIVE_MAX_MES_CONTRACTS."""
    raw = os.getenv("FM_LIVE_MAX_MES_CONTRACTS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return int(LIVE_MAX_MES_CONTRACTS)


def max_mes_contracts() -> int:
    """Active hard cap — paper vs live."""
    if risk_mode_paper():
        return paper_max_mes_contracts()
    return live_max_mes_contracts()


def live_cash_arming_status() -> tuple[bool, str]:
    """
    Justice: true live cash requires an unambiguous arming set.
    Returns (armed, reason). Paper mode is never 'armed'.
    """
    if forward_test_force_paper():
        return False, "paper locked (FORWARD_TEST_MODE / FM_FORWARD_TEST_MODE)"
    if trading_halted():
        return False, "FM_TRADING_HALTED=1"
    from engine.webull_futures import futures_live_orders_allowed, webull_is_sandbox

    if webull_is_sandbox():
        return False, "WEBULL_API_HOST is sandbox (not cash live)"
    if not webull_credentials_configured():
        return False, "Webull credentials missing"
    if not futures_live_orders_allowed():
        return False, "FM_ALLOW_LIVE_ORDERS not set to 1"
    if not databento_configured() or primary_data_source() != "databento":
        return False, "DATABENTO_API_KEY + FM_DATA_SOURCE=databento required for live CME"
    if virtue_session_mode() != "cme":
        return False, "VIRTUE_SESSION_MODE must be cme for live Globex MES"
    forced_sym = _env_str("WEBULL_FUTURES_SYMBOL", "FM_EXECUTION_CONTRACT")
    if not forced_sym:
        return False, "WEBULL_FUTURES_SYMBOL must be set (e.g. MESU6) for live symbol lock"
    return True, "live_cash_armed"


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
