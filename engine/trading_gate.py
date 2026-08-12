"""Virtue position-trading gate pillars — shared by dashboard + tests (Justice)."""

from __future__ import annotations


def trading_gate_pillars(
    *,
    entry_pipeline: dict,
    session: dict,
    age_s: float | None,
    last_price: object,
) -> list[dict[str, object]]:
    """
    Position-trading gate including Temperance day-cap + soft-loss.

    ALL CLEAR only when every pillar passes — day-cap / soft-loss are first-class.
    """
    structure_reason = str(entry_pipeline.get("entry_structure_reason") or "")
    structure_ok = bool(entry_pipeline.get("entry_structure_ok"))
    # Never treat "waived" structure as a pass under quality policy.
    if "waived" in structure_reason.lower():
        structure_ok = False
    risk_clear = not (
        bool(session.get("virtue_pnl_lock_active"))
        or bool(session.get("circuit_breaker_tripped"))
        or bool(session.get("halted"))
    )
    trades_today = int(
        session.get("trades_today") or entry_pipeline.get("trades_today") or 0
    )
    day_cap = int(
        session.get("max_tactical_trades_per_day")
        or entry_pipeline.get("max_tactical_trades_per_day")
        or 3
    )
    if "day_cap_clear" in entry_pipeline:
        day_cap_clear = bool(entry_pipeline.get("day_cap_clear"))
    else:
        day_cap_clear = trades_today < day_cap
    if "soft_loss_clear" in entry_pipeline:
        soft_clear = bool(entry_pipeline.get("soft_loss_clear"))
    else:
        soft_cap = float(session.get("soft_daily_loss_cap") or 0.0)
        pnl = float(session.get("realized_pnl_today") or 0.0)
        blocks = bool(session.get("soft_daily_loss_blocks_entries", True))
        soft_clear = not (blocks and soft_cap > 0 and pnl <= -soft_cap)
    tape_live = bool(last_price) and (age_s is not None and float(age_s) <= 30.0)
    block = str(
        session.get("last_entry_block_reason")
        or entry_pipeline.get("last_entry_block_reason")
        or ""
    )
    return [
        {
            "key": "cooldown",
            "name": "Cooldown",
            "pass": bool(entry_pipeline.get("layer1_streak_clear", True)),
            "detail": "clear"
            if entry_pipeline.get("layer1_streak_clear", True)
            else "locked",
        },
        {
            "key": "window",
            "name": "Window",
            "pass": bool(entry_pipeline.get("allow_new_entries")),
            "detail": "open" if entry_pipeline.get("allow_new_entries") else "closed",
        },
        {
            "key": "structure",
            "name": "Structure",
            "pass": structure_ok,
            "detail": "ok" if structure_ok else "block",
        },
        {
            "key": "reconcile",
            "name": "Reconcile",
            "pass": bool(entry_pipeline.get("sleeve_reconcile_ok", True)),
            "detail": "ok"
            if entry_pipeline.get("sleeve_reconcile_ok", True)
            else "fail",
        },
        {
            "key": "risk",
            "name": "Risk",
            "pass": risk_clear,
            "detail": "clear" if risk_clear else "halt",
        },
        {
            "key": "day_cap",
            "name": "Day Cap",
            "pass": day_cap_clear,
            "detail": f"{trades_today}/{day_cap}",
        },
        {
            "key": "soft_loss",
            "name": "Soft Loss",
            "pass": soft_clear,
            "detail": "clear" if soft_clear else "blocked",
        },
        {
            "key": "tape",
            "name": "Tape",
            "pass": tape_live,
            "detail": ("live" if tape_live else "stale")
            + (f" · {block}" if block else ""),
        },
    ]
