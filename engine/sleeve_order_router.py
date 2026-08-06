"""
Multi-Sleeve Order Router.

Routine tactical exits MUST never call whole-account flatten_all.
Each sleeve dispatches an explicit offsetting qty; the other book is left untouched.

Account-level emergencies (day lock / RTH) close sleeves sequentially via this router
rather than a blind total flatten.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.config import MAX_ACCOUNT_CONTRACT_CEILING
from engine.dual_sleeve import account_contract_ceiling, tactical_entry_allowed

log = logging.getLogger("virtue.sleeve_router")


def _books(session: Any) -> dict[str, Any]:
    """Normalize VirtueSession or dual_sleeve dict into sleeve books."""
    if isinstance(session, dict) and "core_anchor_sleeve" in session:
        core = dict(session.get("core_anchor_sleeve") or {})
        tac = dict(session.get("tactical_satellite_sleeve") or {})
        ceiling = int(
            session.get("max_account_contract_ceiling")
            or account_contract_ceiling()
        )
        return {
            "core_side": str(core.get("side") or "FLAT").upper(),
            "core_size": int(core.get("size") or 0),
            "core_active": bool(core.get("active"))
            or (
                str(core.get("side") or "FLAT").upper() in {"LONG", "SHORT"}
                and int(core.get("size") or 0) > 0
            ),
            "tactical_side": str(
                tac.get("engine_exposure") or tac.get("side") or "FLAT"
            ).upper(),
            "tactical_size": int(tac.get("size") or 0),
            "tactical_active": bool(tac.get("active", False))
            or (
                str(tac.get("engine_exposure") or "FLAT").upper() in {"LONG", "SHORT"}
                and int(tac.get("size") or 0) > 0
            ),
            "ceiling": ceiling,
        }

    core_active = bool(getattr(session, "core_active", False))
    tac_active = bool(getattr(session, "tactical_active", False))
    return {
        "core_side": str(getattr(session, "core_side", "FLAT") or "FLAT").upper(),
        "core_size": int(getattr(session, "core_size", 0) or 0) if core_active else 0,
        "core_active": core_active,
        "tactical_side": str(
            getattr(session, "tactical_side", "FLAT") or "FLAT"
        ).upper(),
        "tactical_size": int(getattr(session, "tactical_size", 0) or 0)
        if tac_active
        else 0,
        "tactical_active": tac_active,
        "ceiling": int(MAX_ACCOUNT_CONTRACT_CEILING),
    }


def calculate_net_account_exposure(session: Any) -> int:
    """
    True operational market footprint across both sleeves (signed contracts).
    LONG = +, SHORT = -.
    """
    b = _books(session)
    core_side = b["core_side"] if b["core_active"] else "FLAT"
    core_size = int(b["core_size"]) if core_side != "FLAT" else 0
    tac_side = b["tactical_side"] if b["tactical_active"] else "FLAT"
    tac_size = int(b["tactical_size"]) if tac_side != "FLAT" else 0

    net = 0
    net += core_size if core_side == "LONG" else (-core_size if core_side == "SHORT" else 0)
    net += tac_size if tac_side == "LONG" else (-tac_size if tac_side == "SHORT" else 0)
    return int(net)


def absolute_contract_footprint(session: Any) -> int:
    """Gross contracts occupied (same-side stack counts toward ceiling)."""
    b = _books(session)
    return int(b["core_size"] if b["core_active"] else 0) + int(
        b["tactical_size"] if b["tactical_active"] else 0
    )


async def _dispatch_close_qty(
    broker: Any,
    *,
    contracts: int,
    price: float,
    stop_ticks: int,
    reason: str,
    entry_price: float | None = None,
    sleeve: str = "",
) -> tuple[bool, float]:
    """
    Close exactly `contracts` of broker net exposure.
    Never flattens the remainder (core/tactical isolation).
    """
    qty = max(0, int(contracts))
    if qty < 1:
        return True, 0.0
    try:
        if hasattr(broker, "close_contracts"):
            return await broker.close_contracts(
                contracts=qty,
                price=price,
                stop_ticks=stop_ticks,
                reason=reason,
                entry_price=entry_price,
                sleeve=sleeve,
            )
        # Legacy fallback — leave = net - qty (still not flatten_all).
        net_dir, net_sz = broker.net_exposure()
        if net_sz <= 0:
            return True, 0.0
        leave = max(0, int(net_sz) - qty)
        return await broker.partial_close(
            contracts=qty,
            price=price,
            stop_ticks=stop_ticks,
            reason=reason,
            leave=leave,
            entry_price=entry_price,
            sleeve=sleeve,
        )
    except Exception as exc:
        log.exception("sleeve_dispatch_close_failed err=%s reason=%s", exc, reason)
        return False, 0.0


async def execute_tactical_action(
    session: Any,
    *,
    target_side: str,
    target_size: int = 1,
    current_cycle: int,
    price: float,
    stop_ticks: int,
    reason: str,
    mark_open: Any | None = None,
    mark_flat: Any | None = None,
    on_filled: Any | None = None,
) -> tuple[bool, float, str]:
    """
    Execute trades exclusively for the short-term tactical sleeve.

    target_side FLAT → close only tactical qty (core untouched).
    target_side LONG/SHORT → open satellite after hedge/ceiling gates.
    """
    side = str(target_side or "FLAT").upper()
    b = _books(session)
    ceiling = int(b["ceiling"])
    net_exposure = calculate_net_account_exposure(session)
    broker = getattr(session, "broker", None)
    if broker is None:
        return False, 0.0, "router_blocked:no_broker"

    # Close path — explicit offsetting size, never flatten_all.
    if side == "FLAT":
        if not b["tactical_active"] or int(b["tactical_size"]) <= 0:
            return True, 0.0, "tactical_already_flat"
        tac_side = b["tactical_side"]
        tac_size = int(b["tactical_size"])
        tac_entry = float(getattr(session, "tactical_entry_price", 0.0) or 0.0)
        order_side = "SELL" if tac_side == "LONG" else "BUY"
        tagged_reason = f"tac:{reason}" if not str(reason).startswith("tac:") else reason
        log.info(
            "ROUTER tactical_close dispatch=%s qty=%s leave_core=%s reason=%s "
            "entry=%.2f net_exposure=%s",
            order_side,
            tac_size,
            int(b["core_size"]) if b["core_active"] else 0,
            tagged_reason,
            tac_entry,
            net_exposure,
        )
        ok, pnl = await _dispatch_close_qty(
            broker,
            contracts=tac_size,
            price=price,
            stop_ticks=stop_ticks,
            reason=tagged_reason,
            entry_price=tac_entry if tac_entry > 0 else None,
            sleeve="tac",
        )
        if ok:
            # Justice: recompute from sleeve entry + mark if broker blended avg leaked.
            if tac_entry > 0 and tac_side in {"LONG", "SHORT"}:
                from engine.config import POINT_VALUE

                pts = (
                    (float(price) - tac_entry)
                    if tac_side == "LONG"
                    else (tac_entry - float(price))
                )
                sleeve_pnl = round(pts * float(POINT_VALUE) * tac_size, 2)
                if abs(sleeve_pnl - float(pnl or 0.0)) > 0.01:
                    log.info(
                        "ROUTER tactical_pnl_sleeve_override broker=%.2f sleeve=%.2f "
                        "entry=%.2f",
                        float(pnl or 0.0),
                        sleeve_pnl,
                        tac_entry,
                    )
                    pnl = sleeve_pnl
            if mark_flat is not None:
                mark_flat(session)
            if on_filled is not None:
                on_filled(
                    session,
                    pnl=pnl,
                    reason=tagged_reason,
                    side="FLAT",
                    entry_price=tac_entry,
                    exit_price=float(price),
                    contracts=tac_size,
                    direction=tac_side,
                )
            return True, float(pnl or 0.0), "tactical_closed"
        return False, 0.0, "tactical_close_failed"

    if side not in {"LONG", "SHORT"}:
        return False, 0.0, "router_blocked:unsupported_side"

    # Safety: forbid expensive cross-hedge vs core.
    core_side = b["core_side"] if b["core_active"] else "FLAT"
    if core_side in {"LONG", "SHORT"} and side != core_side:
        msg = (
            f"router_blocked:internal_hedge_forbidden target={side} core={core_side}"
        )
        log.warning("ROUTER %s", msg)
        return False, 0.0, msg

    # Safety: capital protection / ceiling (gross footprint).
    footprint = absolute_contract_footprint(session)
    # Opening new tactical size while already flat tactical.
    add = max(1, int(target_size))
    if b["tactical_active"] and b["tactical_side"] == side:
        msg = "router_blocked:tactical_already_in_side"
        log.info("ROUTER %s", msg)
        return False, 0.0, msg
    if footprint + add > ceiling and not b["tactical_active"]:
        msg = (
            f"router_blocked:ceiling footprint={footprint}+{add}>{ceiling} "
            f"net={net_exposure}"
        )
        log.warning("ROUTER %s", msg)
        return False, 0.0, msg

    allowed, allow_reason = tactical_entry_allowed(
        core_active=bool(b["core_active"]),
        core_side=str(b["core_side"]),
        core_size=int(b["core_size"]),
        tactical_side=side,
        tactical_size=add,
        ceiling=ceiling,
    )
    if not allowed:
        log.warning("ROUTER %s", allow_reason)
        return False, 0.0, allow_reason

    from broker import Order
    from engine.config import EXECUTION_SYMBOL
    import time

    order = Order(
        symbol=EXECUTION_SYMBOL,
        direction=side,
        size=add,
        price=price,
        stop_ticks=stop_ticks,
        quote_ts=time.time(),
    )
    log.info(
        "ROUTER tactical_entry side=%s qty=%s cycle=%s reason=%s net_exposure=%s",
        side,
        add,
        current_cycle,
        reason,
        net_exposure,
    )
    try:
        result = await broker.fire_order(order)
    except Exception as exc:
        log.exception("ROUTER tactical_entry_failed err=%s", exc)
        return False, 0.0, "tactical_entry_exception"
    if result is None:
        return False, 0.0, "tactical_entry_no_fill"

    fill_side = str(result.direction or side).upper()
    fill_sz = int(result.contracts or add)
    fill_px = float(result.fill_price or price)
    if mark_open is not None:
        mark_open(
            session,
            side=fill_side,
            price=fill_px,
            size=fill_sz,
            cycle=int(current_cycle),
        )
    elif hasattr(session, "tactical_active"):
        session.tactical_active = True
        session.tactical_side = fill_side
        session.tactical_size = fill_sz
        session.tactical_entry_price = fill_px
        session.entry_cycle_marker = int(current_cycle)
    if on_filled is not None:
        on_filled(
            session,
            pnl=0.0,
            reason=reason,
            side=fill_side,
            result=result,
        )
    return True, 0.0, "tactical_opened"


async def execute_core_action(
    session: Any,
    *,
    target_side: str,
    target_size: int = 1,
    price: float,
    stop_ticks: int,
    reason: str,
    mark_open: Any | None = None,
    mark_flat: Any | None = None,
    on_filled: Any | None = None,
) -> tuple[bool, float, str]:
    """Structural sleeve only — never touches tactical temperance books."""
    side = str(target_side or "FLAT").upper()
    b = _books(session)
    broker = getattr(session, "broker", None)
    if broker is None:
        return False, 0.0, "router_blocked:no_broker"

    if side == "FLAT":
        if not b["core_active"] or int(b["core_size"]) <= 0:
            return True, 0.0, "core_already_flat"
        core_sz = int(b["core_size"])
        core_side = str(b["core_side"] or "FLAT").upper()
        core_entry = float(getattr(session, "core_entry_price", 0.0) or 0.0)
        tagged_reason = (
            f"core:{reason}" if not str(reason).startswith("core:") else reason
        )
        log.info(
            "ROUTER core_close qty=%s leave_tactical=%s reason=%s entry=%.2f",
            core_sz,
            int(b["tactical_size"]) if b["tactical_active"] else 0,
            tagged_reason,
            core_entry,
        )
        ok, pnl = await _dispatch_close_qty(
            broker,
            contracts=core_sz,
            price=price,
            stop_ticks=stop_ticks,
            reason=tagged_reason,
            entry_price=core_entry if core_entry > 0 else None,
            sleeve="core",
        )
        if ok:
            if core_entry > 0 and core_side in {"LONG", "SHORT"}:
                from engine.config import POINT_VALUE

                pts = (
                    (float(price) - core_entry)
                    if core_side == "LONG"
                    else (core_entry - float(price))
                )
                sleeve_pnl = round(pts * float(POINT_VALUE) * core_sz, 2)
                if abs(sleeve_pnl - float(pnl or 0.0)) > 0.01:
                    log.info(
                        "ROUTER core_pnl_sleeve_override broker=%.2f sleeve=%.2f "
                        "entry=%.2f",
                        float(pnl or 0.0),
                        sleeve_pnl,
                        core_entry,
                    )
                    pnl = sleeve_pnl
            if mark_flat is not None:
                mark_flat(session)
            if on_filled is not None:
                on_filled(
                    session,
                    pnl=pnl,
                    reason=tagged_reason,
                    side="FLAT",
                    entry_price=core_entry,
                    exit_price=float(price),
                    contracts=core_sz,
                    direction=core_side,
                )
            return True, float(pnl or 0.0), "core_closed"
        return False, 0.0, "core_close_failed"

    if side not in {"LONG", "SHORT"}:
        return False, 0.0, "router_blocked:unsupported_side"

    tac_side = b["tactical_side"] if b["tactical_active"] else "FLAT"
    if tac_side in {"LONG", "SHORT"} and tac_side != side:
        msg = f"router_blocked:opposite_tactical tac={tac_side} core_want={side}"
        log.info("ROUTER %s", msg)
        return False, 0.0, msg

    need = max(1, int(target_size))
    if absolute_contract_footprint(session) + need > int(b["ceiling"]):
        msg = "router_blocked:ceiling_core"
        log.info("ROUTER %s", msg)
        return False, 0.0, msg

    from broker import Order
    from engine.config import EXECUTION_SYMBOL
    import time

    order = Order(
        symbol=EXECUTION_SYMBOL,
        direction=side,
        size=need,
        price=price,
        stop_ticks=stop_ticks,
        quote_ts=time.time(),
    )
    log.info("ROUTER core_entry side=%s qty=%s reason=%s", side, need, reason)
    try:
        result = await broker.fire_order(order)
    except Exception as exc:
        log.exception("ROUTER core_entry_failed err=%s", exc)
        return False, 0.0, "core_entry_exception"
    if result is None:
        return False, 0.0, "core_entry_no_fill"

    fill_side = str(result.direction or side).upper()
    fill_sz = int(result.contracts or need)
    fill_px = float(result.fill_price or price)
    if mark_open is not None:
        mark_open(session, side=fill_side, price=fill_px, size=fill_sz)
    if on_filled is not None:
        on_filled(session, pnl=0.0, reason=reason, side=fill_side, result=result)
    return True, 0.0, "core_opened"


async def close_all_sleeves_sequential(
    session: Any,
    *,
    price: float,
    stop_ticks: int,
    reason: str,
    close_tactical: Any,
    close_core: Any,
    credit_residual: Any | None = None,
) -> tuple[bool, float]:
    """
    Account emergency: close tactical then core via sleeve routers (no flatten_all).
    Returns (ok, total_pnl). Temperance updates are owned by close_tactical.
    """
    total = 0.0
    ok_all = True
    if bool(getattr(session, "tactical_active", False)):
        ok, pnl = await close_tactical(
            session, price=price, stop_ticks=stop_ticks, reason=reason
        )
        total += float(pnl or 0.0)
        ok_all = ok_all and ok
    if bool(getattr(session, "core_active", False)):
        ok, pnl = await close_core(
            session, price=price, stop_ticks=stop_ticks, reason=reason
        )
        total += float(pnl or 0.0)
        ok_all = ok_all and ok
    # Residual desync — last resort close whatever broker still shows.
    try:
        net_dir, net_sz = session.broker.net_exposure()
    except Exception:
        net_dir, net_sz = "FLAT", 0
    if int(net_sz) > 0:
        log.error(
            "ROUTER residual_after_sleeve_close dir=%s sz=%s reason=%s — "
            "dispatching exact residual close (not flatten_all of unknown books)",
            net_dir,
            net_sz,
            reason,
        )
        ok, pnl = await _dispatch_close_qty(
            session.broker,
            contracts=int(net_sz),
            price=price,
            stop_ticks=stop_ticks,
            reason=f"{reason}:residual",
        )
        total += float(pnl or 0.0)
        ok_all = ok_all and ok
        if ok and credit_residual is not None and abs(float(pnl or 0.0)) >= 1e-12:
            try:
                credit_residual(session, float(pnl))
            except Exception as exc:
                log.exception("ROUTER residual_credit_failed err=%s", exc)
    return ok_all, round(total, 2)
