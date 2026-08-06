"""
MacroMathics operational observability — CloudWatch METRIC lines + chat webhooks.

CloudWatch Logs → Metric Filters (paste these patterns):

  Velocity Gate Preventions
    [log, prefix="METRIC:EntryPreventedVelocityGate=1", ...]

  In-Flight Course Corrections
    [log, prefix="METRIC:CourseCorrectTriggered=1", ...]

  Stagnant Time-Decay Exits
    [log, prefix="METRIC:TimeDecayTriggered=1", ...]

  Profit Guard Circuit Breaker
    [log, prefix="METRIC:ProfitGuardTriggered=1", ...]

  Trade Closed / Position Opened
    [log, prefix="METRIC:TradeClosed=1", ...]
    [log, prefix="METRIC:PositionOpened=1", ...]

Set FM_CHAT_WEBHOOK_URL (or CHAT_WEBHOOK_URL) to a Discord/Slack incoming webhook.
Unset / placeholder values disable chat (Justice: no fake delivery).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("virtue.metrics")

_PLACEHOLDER_EXACT = {
    "https://discord.com",
    "https://discord.com/",
    "https://hooks.slack.com/services/XXX",
}
_PLACEHOLDER_MARKERS = (
    "your-actual-id",
    "your_webhook",
    "REPLACE_ME",
    "PASTE_YOUR_ACTUAL",
)


def chat_webhook_url() -> str:
    """Resolve webhook from env; empty means chat disabled."""
    raw = (
        os.getenv("FM_CHAT_WEBHOOK_URL", "").strip()
        or os.getenv("CHAT_WEBHOOK_URL", "").strip()
    )
    if not raw:
        return ""
    if raw.rstrip("/") in {u.rstrip("/") for u in _PLACEHOLDER_EXACT}:
        return ""
    if any(marker in raw for marker in _PLACEHOLDER_MARKERS):
        return ""
    if not (raw.startswith("https://") or raw.startswith("http://")):
        return ""
    return raw


def emit_metric(name: str, **fields: Any) -> None:
    """
    Emit a CloudWatch-friendly structured metric line.

    Example: METRIC:EntryPreventedVelocityGate=1 | Side=LONG | Blend=56.0 | Target=59.0
    """
    metric = (name or "").strip()
    if not metric:
        return
    parts = [f"METRIC:{metric}=1"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, float):
            parts.append(f"{key}={value:.4g}")
        else:
            parts.append(f"{key}={value}")
    logger.info(" | ".join(parts))


def send_chat_notification(message: str) -> None:
    """
    Fire-and-forget Discord/Slack webhook (Temperance: never block the hot path).
    """
    url = chat_webhook_url()
    text = (message or "").strip()
    if not url or not text:
        return

    def _post() -> None:
        payload = json.dumps({"text": text, "content": text}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "User-Agent": "MacroMathics/1.0",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.error("chat_webhook_failed err=%s", exc)

    try:
        threading.Thread(target=_post, name="fm-chat-webhook", daemon=True).start()
    except Exception as exc:
        logger.error("chat_webhook_spawn_failed err=%s", exc)


def notify_profit_guard(*, realized: float, floor: float, peak: float) -> None:
    emit_metric(
        "ProfitGuardTriggered",
        PnL=float(realized),
        Floor=float(floor),
        Peak=float(peak),
    )
    send_chat_notification(
        "# 🛑 PROFIT GUARD CIRCUIT BREAKER\n"
        f"**Daily P&L floor breached**\n"
        f"• P&L: `${realized:.2f}`\n"
        f"• Floor: `${floor:.2f}`\n"
        f"• Peak: `${peak:.2f}`\n"
        f"**Action: STAND ASIDE — day shut down**"
    )


def notify_pipeline_stuck(*, depth: int, seconds: float) -> None:
    """Urgent reboot freeze alert — fire-and-forget webhook + metric."""
    emit_metric(
        "PipelineQueueStuck",
        Depth=int(depth),
        Seconds=float(seconds),
    )
    send_chat_notification(
        "CRITICAL: Pipeline Queue Stuck on Reboot\n"
        f"depth={int(depth)} stuck_for={float(seconds):.0f}s"
    )
    logger.error(
        "CRITICAL: Pipeline Queue Stuck on Reboot depth=%s seconds=%.1f",
        depth,
        seconds,
    )


def notify_course_correct(*, exposure: str, blend: float, reason: str = "") -> None:
    emit_metric(
        "CourseCorrectTriggered",
        Exposure=str(exposure or "FLAT").upper(),
        Blend=float(blend),
        Reason=reason or "course_correct",
    )


def notify_time_decay(*, elapsed: int, open_pnl: float, exposure: str = "") -> None:
    emit_metric(
        "TimeDecayTriggered",
        ElapsedCycles=int(elapsed),
        OpenPnL=float(open_pnl),
        Exposure=str(exposure or "").upper() or None,
    )


def notify_velocity_gate_block(
    *,
    side: str,
    blend: float,
    target: float,
    velocity: float = 0.0,
) -> None:
    emit_metric(
        "EntryPreventedVelocityGate",
        Side=str(side or "").upper(),
        Blend=float(blend),
        Target=float(target),
        Velocity=float(velocity),
    )


def _display_reason(reason: str) -> str:
    """Short operator-facing exit tag (COURSE_CORRECT, TIME_DECAY, …)."""
    r = (reason or "unknown").strip().lower()
    if "course_correct" in r:
        return "COURSE_CORRECT"
    if "time_decay" in r:
        return "TIME_DECAY"
    if "take_profit" in r:
        return "TAKE_PROFIT"
    if r.startswith("stop") or "stop_" in r or r == "stop":
        return "STOP"
    if "profit_guard" in r or "virtue_pnl_lock" in r:
        return "PROFIT_GUARD"
    if "structured_exit" in r:
        return "STRUCTURED_EXIT"
    return (reason or "UNKNOWN").strip().upper().replace(" ", "_")[:48]


def _format_signed_usd(amount: float) -> str:
    """Match Discord copy: `-$12.50` / `$100.00`."""
    value = float(amount)
    if value < 0:
        return f"-${abs(value):.2f}"
    return f"${value:.2f}"


def notify_core_macro_invalidation(
    *,
    side: str,
    reason: str,
    blend: float,
    regime: str,
    pnl: float,
    cycle: int,
) -> None:
    """Discord portfolio advisory when Slow Invalidation Core Escaper fires."""
    emit_metric(
        "CoreMacroInvalidation",
        Side=str(side or "").upper(),
        Reason=str(reason or "")[:80],
        Blend=round(float(blend), 1),
        Regime=str(regime or ""),
        PnL=float(pnl),
        Cycle=int(cycle),
    )
    send_chat_notification(
        "# ⚠️ PORTFOLIO ADVISORY — CORE MACRO INVALIDATED\n"
        f"**Sleeve:** `CORE_ANCHOR` `{str(side or '').upper()}`\n"
        f"**Trigger:** `{str(reason or '')}`\n"
        f"**Regime:** `{str(regime or '')}` | **Blend:** `{float(blend):.1f}`\n"
        f"**Realized on Close:** `{_format_signed_usd(pnl)}`\n"
        f"**Engine Cycle:** `#{int(cycle)}`\n"
        "Core anchor closed to preserve capital — tactical temperance untouched."
    )


def notify_trade_closed(
    *,
    reason: str,
    pnl: float,
    cooldown_cycles: int,
    cycle: int | None = None,
) -> None:
    tag = _display_reason(reason)
    emit_metric(
        "TradeClosed",
        Reason=tag,
        PnL=float(pnl),
        CooldownCycles=int(cooldown_cycles),
        Cycle=cycle,
    )
    lines = [
        f"# ⚡ MACROMATHICS FLATTEN — {tag}",
        f"**Reason:** `{tag}`",
        f"**Realized P&L on Trade:** `{_format_signed_usd(pnl)}`",
    ]
    if cycle is not None:
        lines.append(f"**Engine Cycle Account:** `#{int(cycle)}`")
    send_chat_notification("\n".join(lines))


def notify_position_opened(
    *,
    side: str,
    client_order_id: str,
    cycle: int,
    size: int = 1,
) -> None:
    clord = str(client_order_id or "")[:32]
    emit_metric(
        "PositionOpened",
        Side=str(side or "").upper(),
        ID=clord,
        Size=int(size),
        Cycle=int(cycle),
    )
    # Production Discord copy — H1 headers render larger in Discord.
    send_chat_notification(
        f"# 🚀 MACROMATHICS POSITION ENTERED — {str(side or '').upper()}\n"
        f"**Side:** `{str(side or '').upper()}`\n"
        f"**ClOrdID:** `{clord}`\n"
        f"**Entry Cycle:** `#{int(cycle)}`"
    )
