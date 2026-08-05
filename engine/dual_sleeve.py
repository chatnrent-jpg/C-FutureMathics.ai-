"""
Dual-sleeve Unified Rules of Engagement.

CORE (anchor)  — slow structural bias, 1 MES, closed on structural invalidation.
TACTICAL       — existing virtue satellite; temperance / outcome state owned here only.

Rules:
1. Net exposure ceiling = 2 MES account-wide.
2. Tactical may add only when aligned with core (or core is flat).
3. Core closes on high-timeframe regime flip / invalidation blend — not blind expiry.
4. Core PnL never feeds tactical temperance (consecutive_losses / blend buffers).
"""

from __future__ import annotations

from typing import Any

from engine.config import (
    MAX_ACCOUNT_CONTRACT_CEILING,
    TICK_SIZE,
    TICK_VALUE,
    VIRTUE_CORE_CONFIRM_CYCLES,
    VIRTUE_CORE_INVALIDATION_BLEND_LONG,
    VIRTUE_CORE_MAX_ADVERSE_DOLLARS,
    VIRTUE_CORE_MAX_HOLD_CYCLES,
    VIRTUE_CORE_STRUCTURAL_ADX_MIN,
    VIRTUE_CORE_SIZE,
    VIRTUE_SCORE_LONG_ENTER,
    VIRTUE_SCORE_SHORT_ENTER,
)


STRUCTURAL_BULL = "STRUCTURAL_BULL"
STRUCTURAL_BEAR = "STRUCTURAL_BEAR"
STRUCTURAL_NEUTRAL = "STRUCTURAL_NEUTRAL"


def account_contract_ceiling() -> int:
    return max(1, int(MAX_ACCOUNT_CONTRACT_CEILING))


def classify_structural_regime(
    *,
    macro_bias: str,
    adx: float,
    confirm_cycles: int,
    bull_streak: int,
    bear_streak: int,
    blend: float | None = None,
) -> tuple[str, int, int]:
    """
    Update structural confirm streaks and return (regime, new_bull_streak, new_bear_streak).

    Prefer live blend+ADX (tape) over sticky macro_bias when blend is provided.
    """
    bias = (macro_bias or "NEUTRAL").strip().upper()
    try:
        adx_val = float(adx or 0.0)
    except (TypeError, ValueError):
        adx_val = 0.0
    need = max(1, int(confirm_cycles))
    adx_ok = adx_val >= float(VIRTUE_CORE_STRUCTURAL_ADX_MIN)

    tape_side = ""
    if blend is not None:
        try:
            b = float(blend)
            if adx_ok and b >= float(VIRTUE_SCORE_LONG_ENTER):
                tape_side = "BULL"
            elif adx_ok and b <= float(VIRTUE_SCORE_SHORT_ENTER):
                tape_side = "BEAR"
        except (TypeError, ValueError):
            tape_side = ""

    if tape_side == "BULL" or (not tape_side and bias == "BULL" and adx_ok):
        bull = int(bull_streak) + 1
        bear = 0
    elif tape_side == "BEAR" or (not tape_side and bias == "BEAR" and adx_ok):
        bear = int(bear_streak) + 1
        bull = 0
    else:
        bull = 0
        bear = 0

    if bull >= need:
        return STRUCTURAL_BULL, bull, bear
    if bear >= need:
        return STRUCTURAL_BEAR, bull, bear
    return STRUCTURAL_NEUTRAL, bull, bear


def core_should_open(structural_regime: str, *, core_active: bool) -> tuple[bool, str]:
    if core_active:
        return False, ""
    if structural_regime == STRUCTURAL_BULL:
        return True, "LONG"
    if structural_regime == STRUCTURAL_BEAR:
        return True, "SHORT"
    return False, ""


def core_should_invalidate(
    *,
    core_active: bool,
    core_side: str,
    structural_regime: str,
    blend: float,
    invalidation_depth: float | None = None,
) -> tuple[bool, str]:
    """
    Slow Invalidation Core Escaper (pure predicate).

    Sticky through STRUCTURAL_NEUTRAL / chop — only hard opposite structural
    regime or deep momentum breakdown closes the anchor (not suicidal mid-band).
    """
    if not core_active:
        return False, ""
    side = (core_side or "FLAT").upper()
    regime = str(structural_regime or STRUCTURAL_NEUTRAL).upper()
    try:
        b = float(blend)
    except (TypeError, ValueError):
        b = 50.0
    depth = float(
        invalidation_depth
        if invalidation_depth is not None
        else VIRTUE_CORE_INVALIDATION_BLEND_LONG
    )
    # Depth from the long edge (default 40). Short threshold = 100 - depth.
    long_level = depth
    short_level = 100.0 - depth

    if side == "LONG":
        # Condition 1 — confirmed opposite structure only (not NEUTRAL).
        if regime == STRUCTURAL_BEAR:
            return True, f"core_invalidation:regime_flip→{regime}"
        # Condition 2 — deep momentum breakdown.
        if b <= long_level:
            return True, f"core_invalidation:momentum_breakdown blend={b:.1f}<={long_level:.1f}"
    elif side == "SHORT":
        if regime == STRUCTURAL_BULL:
            return True, f"core_invalidation:regime_flip→{regime}"
        if b >= short_level:
            return True, (
                f"core_invalidation:momentum_breakdown blend={b:.1f}>={short_level:.1f}"
            )
    return False, ""


def evaluate_core_macro_safety(
    session: Any,
    current_cycle: int | None = None,
    *,
    blend: float | None = None,
    structural_regime: str | None = None,
) -> tuple[bool, str]:
    """
    Failsafe: macro core stays sticky, but not suicidal.

    Returns (should_close, reason). Does not dispatch orders — caller closes
    via the multi-sleeve router and fires Discord advisory.
    """
    if isinstance(session, dict) and "core_anchor_sleeve" in session:
        core = session.get("core_anchor_sleeve") or {}
        if not bool(core.get("active")):
            return False, ""
        side = str(core.get("side") or "FLAT").upper()
        regime = str(
            structural_regime
            or (session.get("regime_engine") or {}).get("macro_structural_regime")
            or STRUCTURAL_NEUTRAL
        )
        try:
            b = float(
                blend
                if blend is not None
                else session.get("vwap_twap_blend", session.get("blended_score", 50.0))
            )
        except (TypeError, ValueError):
            b = 50.0
        try:
            depth = float(core.get("structural_invalidation_blend", 40.0) or 40.0)
        except (TypeError, ValueError):
            depth = float(VIRTUE_CORE_INVALIDATION_BLEND_LONG)
        # If operator stored the short absolute threshold (60), recover depth.
        if side == "SHORT" and depth > 50.0:
            depth = 100.0 - depth
        return core_should_invalidate(
            core_active=True,
            core_side=side,
            structural_regime=regime,
            blend=b,
            invalidation_depth=depth,
        )

    if not bool(getattr(session, "core_active", False)):
        return False, ""
    try:
        b = float(
            blend
            if blend is not None
            else getattr(session, "last_blended_score", 50.0)
        )
    except (TypeError, ValueError):
        b = 50.0
    regime = str(
        structural_regime
        or getattr(session, "macro_structural_regime", STRUCTURAL_NEUTRAL)
        or STRUCTURAL_NEUTRAL
    )
    side = str(getattr(session, "core_side", "FLAT") or "FLAT")
    # Adverse $ / max-hold failsafes (Temperance) — before slow invalidation.
    fail = _core_adverse_or_stale_failsafe(session, current_cycle=current_cycle, side=side)
    if fail[0]:
        return fail
    return core_should_invalidate(
        core_active=True,
        core_side=side,
        structural_regime=regime,
        blend=b,
        invalidation_depth=float(VIRTUE_CORE_INVALIDATION_BLEND_LONG),
    )


def _core_adverse_or_stale_failsafe(
    session: Any,
    *,
    current_cycle: int | None,
    side: str,
) -> tuple[bool, str]:
    """Close core on hard adverse dollars or max hold cycles."""
    try:
        entry = float(getattr(session, "core_entry_price", 0.0) or 0.0)
        px = float(getattr(session, "last_price", 0.0) or 0.0)
        size = int(getattr(session, "core_size", 0) or 0)
    except (TypeError, ValueError):
        entry, px, size = 0.0, 0.0, 0
    if entry > 0 and px > 0 and size > 0 and side in {"LONG", "SHORT"}:
        points = (px - entry) if side == "LONG" else (entry - px)
        # MES: $5 per point (= TICK_VALUE / TICK_SIZE when tick=0.25, $1.25)
        dollars_per_point = float(TICK_VALUE) / max(float(TICK_SIZE), 1e-9)
        pnl = points * dollars_per_point * size
        if pnl <= -abs(float(VIRTUE_CORE_MAX_ADVERSE_DOLLARS)):
            return (
                True,
                f"core_failsafe:adverse_dollars pnl={pnl:.2f}"
                f"<=-{float(VIRTUE_CORE_MAX_ADVERSE_DOLLARS):.0f}",
            )
    marker = getattr(session, "core_entry_cycle", None)
    if marker is not None and current_cycle is not None:
        elapsed = int(current_cycle) - int(marker)
        if elapsed >= int(VIRTUE_CORE_MAX_HOLD_CYCLES):
            return (
                True,
                f"core_failsafe:max_hold elapsed={elapsed}>={int(VIRTUE_CORE_MAX_HOLD_CYCLES)}",
            )
    return False, ""


def reconcile_sleeves_to_broker(session: Any, broker: Any) -> tuple[bool, str]:
    """
    Justice: logical sleeve sizes must match broker net exposure (same side).

    On mismatch, repair books toward broker truth and block new risk until stable.
    Returns (ok, detail).
    """
    try:
        net_dir, net_size = broker.net_exposure()
    except Exception as exc:
        return False, f"sleeve_reconcile:broker_error:{exc}"
    net_dir = str(net_dir or "FLAT").upper()
    net_size = int(net_size or 0)
    core_sz, tac_sz = sleeve_sizes_from_session(session)
    book = core_sz + tac_sz
    core_side = str(getattr(session, "core_side", "FLAT") or "FLAT").upper()
    tac_side = str(getattr(session, "tactical_side", "FLAT") or "FLAT").upper()

    if net_size <= 0:
        if book != 0:
            session.core_active = False
            session.core_side = "FLAT"
            session.core_size = 0
            session.core_entry_price = 0.0
            session.core_entry_cycle = None
            session.tactical_active = False
            session.tactical_side = "FLAT"
            session.tactical_size = 0
            session.tactical_entry_price = 0.0
            return False, "sleeve_reconcile:repaired_flat_broker_nonzero_books"
        return True, "sleeve_reconcile:ok_flat"

    # Broker has exposure — books must sum to net and agree on side.
    sides = {s for s in (core_side, tac_side) if s in {"LONG", "SHORT"}}
    if book != net_size or (sides and net_dir not in sides) or (
        core_sz > 0 and tac_sz > 0 and core_side != tac_side
    ):
        # Repair: assign all broker size to tactical; clear phantom core.
        session.core_active = False
        session.core_side = "FLAT"
        session.core_size = 0
        session.core_entry_price = 0.0
        session.core_entry_cycle = None
        session.tactical_active = True
        session.tactical_side = net_dir if net_dir in {"LONG", "SHORT"} else "FLAT"
        session.tactical_size = net_size
        return False, (
            f"sleeve_reconcile:repaired book={book} broker={net_dir}/{net_size} "
            f"→ tac={net_dir}/{net_size}"
        )
    return True, f"sleeve_reconcile:ok book={book} broker={net_dir}/{net_size}"


def tactical_entry_allowed(
    *,
    core_active: bool,
    core_side: str,
    core_size: int,
    tactical_side: str,
    tactical_size: int = 1,
    ceiling: int | None = None,
) -> tuple[bool, str]:
    """
    Net Exposure Rule: cap account contracts; tactical only scales with core alignment.
    """
    cap = int(ceiling if ceiling is not None else account_contract_ceiling())
    side = (tactical_side or "").upper()
    if side not in {"LONG", "SHORT"}:
        return False, "tactical_blocked:unsupported_side"
    tac = max(1, int(tactical_size))
    core_sz = int(core_size) if core_active else 0
    core_dir = (core_side or "FLAT").upper() if core_active else "FLAT"

    if core_sz > 0 and core_dir in {"LONG", "SHORT"} and side != core_dir:
        return False, (
            f"tactical_blocked:opposite_core core={core_dir} proposed={side}"
        )

    occupied = core_sz
    # Same-side stack
    if core_sz > 0 and side == core_dir:
        occupied = core_sz + tac
    else:
        occupied = tac

    if occupied > cap:
        return False, f"tactical_blocked:ceiling occupied={occupied}>{cap}"
    return True, "tactical_ok"


def sleeve_sizes_from_session(session: Any) -> tuple[int, int]:
    """Return (core_size, tactical_size) from session logical books."""
    core = int(getattr(session, "core_size", 0) or 0) if bool(
        getattr(session, "core_active", False)
    ) else 0
    tac = int(getattr(session, "tactical_size", 0) or 0) if bool(
        getattr(session, "tactical_active", False)
    ) else 0
    return max(0, core), max(0, tac)


def build_dual_sleeve_state(session: Any, *, account_nav: float) -> dict[str, Any]:
    """Operator JSON contract for system_state / dashboards."""
    core_active = bool(getattr(session, "core_active", False))
    tac_active = bool(getattr(session, "tactical_active", False))
    return {
        "account_nav": round(float(account_nav or 0.0), 2),
        "max_account_contract_ceiling": account_contract_ceiling(),
        "regime_engine": {
            "macro_structural_regime": str(
                getattr(session, "macro_structural_regime", STRUCTURAL_NEUTRAL)
                or STRUCTURAL_NEUTRAL
            ),
            "micro_tactical_regime": str(
                getattr(session, "last_regime", "CHOP_NO_TRADE") or "CHOP_NO_TRADE"
            ),
        },
        "core_anchor_sleeve": {
            "active": core_active,
            "side": str(getattr(session, "core_side", "FLAT") or "FLAT"),
            "size": int(getattr(session, "core_size", 0) or 0) if core_active else 0,
            "entry_price": round(float(getattr(session, "core_entry_price", 0.0) or 0.0), 2),
            # Depth from the long edge (default 40). Short uses 100 - depth.
            "structural_invalidation_blend": float(
                VIRTUE_CORE_INVALIDATION_BLEND_LONG
            ),
            "realized_pnl_today": round(
                float(getattr(session, "core_realized_pnl_today", 0.0) or 0.0), 2
            ),
            "realized_pnl_this_cycle": round(
                float(getattr(session, "core_realized_pnl_this_cycle", 0.0) or 0.0), 2
            ),
        },
        "tactical_satellite_sleeve": {
            "active": tac_active,
            "engine_exposure": (
                str(getattr(session, "tactical_side", "FLAT") or "FLAT")
                if tac_active
                else "FLAT"
            ),
            "size": int(getattr(session, "tactical_size", 0) or 0) if tac_active else 0,
            "entry_price": round(
                float(getattr(session, "tactical_entry_price", 0.0) or 0.0), 2
            ),
            "entry_cycle_marker": getattr(session, "entry_cycle_marker", None),
            "pipeline_resume_cycle": int(
                getattr(session, "pipeline_resume_cycle", 0) or 0
            ),
            "realized_pnl_today": round(
                float(getattr(session, "tactical_realized_pnl_today", 0.0) or 0.0), 2
            ),
            "last_trade_outcome": {
                "last_result": str(getattr(session, "last_result", "FLAT") or "FLAT"),
                "last_reason": str(getattr(session, "last_reason", "none") or "none"),
                "consecutive_wins": int(getattr(session, "consecutive_wins", 0) or 0),
                "consecutive_losses": int(
                    getattr(session, "consecutive_losses", 0) or 0
                ),
                "last_trade_pnl": round(
                    float(getattr(session, "last_trade_pnl", 0.0) or 0.0), 2
                ),
            },
        },
    }


def core_size_default() -> int:
    return max(1, int(VIRTUE_CORE_SIZE))


def structural_confirm_cycles() -> int:
    return max(1, int(VIRTUE_CORE_CONFIRM_CYCLES))
