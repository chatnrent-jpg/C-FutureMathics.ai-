#!/usr/bin/env python3
"""FutureMathics — Streamlit dashboard (polls data/system_state.json)."""

from __future__ import annotations

import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import HANDSHAKE_EQUITY_BASE, POINT_VALUE
from engine.trading_gate import trading_gate_pillars
from engine.ui_state_bridge import ensure_boot_system_state

STATE_PATH = ROOT / "data" / "system_state.json"
ensure_boot_system_state(STATE_PATH)

# Dark desk palette — long=teal, short=rose, flat=amber, money green/red
C_LONG = "#2dd4bf"
C_LONG_BG = "rgba(45,212,191,0.14)"
C_SHORT = "#fb7185"
C_SHORT_BG = "rgba(251,113,133,0.14)"
C_FLAT = "#fbbf24"
C_FLAT_BG = "rgba(251,191,36,0.12)"
C_UP = "#4ade80"
C_UP_BG = "rgba(74,222,128,0.14)"
C_DOWN = "#f87171"
C_DOWN_BG = "rgba(248,113,113,0.14)"
C_MUTED = "#94a3b8"
C_INK = "#e2e8f0"
C_PANEL = "rgba(148,163,184,0.10)"
C_CARD = "rgba(15,23,42,0.72)"
C_BG0 = "#070b12"
C_BG1 = "#0f172a"
C_GATE_PASS = "#34d399"
C_GATE_FAIL = "#f87171"
C_GATE_PARTIAL = "#fbbf24"


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _state_age_seconds(data: dict) -> float | None:
    raw = data.get("updated_at") or (data.get("dashboard") or {}).get("as_of_utc")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace("UTC", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _pnl_colors(amount: float) -> tuple[str, str]:
    if amount > 0:
        return C_UP, C_UP_BG
    if amount < 0:
        return C_DOWN, C_DOWN_BG
    return C_MUTED, C_PANEL


def _side_colors(side: str) -> tuple[str, str]:
    s = (side or "FLAT").upper()
    if s == "LONG" or "BULL" in s:
        return C_LONG, C_LONG_BG
    if s == "SHORT" or "BEAR" in s:
        return C_SHORT, C_SHORT_BG
    return C_FLAT, C_FLAT_BG


def _operator_reason_parts(reason: str) -> tuple[str, str]:
    """Split machine/human reason into a big headline + supporting line."""
    raw = (reason or "").strip()
    if not raw:
        return "WAITING", "No engine reason yet."
    if "|" in raw:
        left, right = raw.split("|", 1)
        return left.strip(), right.strip()
    legacy = {
        "vwap_twap_neutral_band": "STAND ASIDE | NEUTRAL BAND",
        "adx_too_weak": "STAND ASIDE | ADX TOO WEAK",
        "short_adx_too_weak": "STAND ASIDE | SHORT ADX TOO WEAK",
        "vwap_twap_long": "LONG SETUP | ENTRY BAND CLEARED",
        "vwap_twap_short": "SHORT SETUP | ENTRY BAND CLEARED",
        "vwap_twap_hold_long": "HOLDING LONG | THESIS STILL VALID",
        "vwap_twap_hold_short": "HOLDING SHORT | THESIS STILL VALID",
        "thesis_invalid_long": "FLATTEN SIGNAL | LONG THESIS BROKEN",
        "thesis_invalid_short": "FLATTEN SIGNAL | SHORT THESIS BROKEN",
        "ema_disagree_long": "STAND ASIDE | EMA DISAGREES WITH LONG",
        "ema_disagree_short": "STAND ASIDE | EMA DISAGREES WITH SHORT",
        "atr_chaos": "STAND ASIDE | CHAOS VOLATILITY",
    }
    token = raw.split()[0] if raw.split() else ""
    if token in legacy:
        title = legacy[token]
        detail = raw[len(token) :].strip(" -—")
        if "|" in title:
            left, right = title.split("|", 1)
            return left.strip(), f"{right.strip()} — {detail}" if detail else right.strip()
        return title, detail or raw
    if " — " in raw:
        left, right = raw.split(" — ", 1)
        return left.strip().upper().replace("_", " "), right.strip()
    return "ENGINE", raw


def _inject_css() -> None:
    st.markdown(
        f"""
        <style>
          .stApp {{
            color: {C_INK};
            background:
              radial-gradient(1100px 520px at 8% -8%, rgba(45,212,191,0.14), transparent 55%),
              radial-gradient(900px 420px at 92% 0%, rgba(251,113,133,0.10), transparent 52%),
              linear-gradient(180deg, {C_BG0} 0%, {C_BG1} 55%, #111827 100%);
          }}
          .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
          .stMarkdown, .stCaption, [data-testid="stMarkdownContainer"],
          [data-testid="stCaptionContainer"], [data-testid="stHeader"] {{
            color: {C_INK} !important;
          }}
          h1, h2, h3, h4 {{
            letter-spacing: 0.04em !important;
            color: {C_INK} !important;
          }}
          [data-testid="stHeader"] {{
            background: rgba(7,11,18,0.72) !important;
          }}
          [data-testid="stToolbar"] {{
            background: transparent !important;
          }}
          [data-testid="stSidebar"] {{
            background: {C_BG1} !important;
          }}
          div[data-testid="stMetricValue"] {{
            font-size: 1.55rem !important;
            color: {C_INK} !important;
          }}
          div[data-testid="stMetricLabel"] {{
            color: {C_MUTED} !important;
          }}
          .stDataFrame, [data-testid="stDataFrame"] {{
            background: {C_CARD} !important;
          }}
          hr {{
            border-color: rgba(148,163,184,0.18) !important;
          }}
          .fm-gate {{
            border-radius: 14px;
            padding: 1rem 1.1rem 1.05rem 1.1rem;
            margin: 0.35rem 0 0.85rem 0;
            box-shadow: 0 12px 30px rgba(0,0,0,0.28);
          }}
          .fm-gate-logo {{
            font-family: Georgia, "Times New Roman", serif;
            font-size: clamp(1.35rem, 2.4vw, 1.85rem);
            font-weight: 800;
            letter-spacing: 0.01em;
          }}
          .fm-gate-sub {{
            font-size: 1.05rem;
            font-weight: 700;
            margin-top: 0.25rem;
          }}
          .fm-gate-bar-track {{
            height: 8px;
            border-radius: 999px;
            background: rgba(148,163,184,0.18);
            margin-top: 0.75rem;
            overflow: hidden;
          }}
          .fm-gate-bar {{
            height: 100%;
            border-radius: 999px;
          }}
          .fm-pillars {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.55rem;
            margin-top: 1rem;
          }}
          @media (max-width: 900px) {{
            .fm-pillars {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
          }}
          .fm-pillar {{
            border-radius: 12px;
            padding: 0.65rem 0.55rem;
            text-align: center;
            min-height: 4.4rem;
          }}
          .fm-pillar-name {{
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
          }}
          .fm-pillar-state {{
            font-size: 0.95rem;
            font-weight: 700;
            margin-top: 0.28rem;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _trading_gate_pillars(
    *,
    entry_pipeline: dict,
    session: dict,
    age_s: float | None,
    last_price: object,
) -> list[dict[str, object]]:
    return trading_gate_pillars(
        entry_pipeline=entry_pipeline,
        session=session,
        age_s=age_s,
        last_price=last_price,
    )


def _render_trading_gate_logo(
    pillars: list[dict[str, object]],
    *,
    timeframe: str = "1D",
) -> None:
    """Color-coded position trading gate — ALL CLEAR only when every pillar passes."""
    passed = sum(1 for p in pillars if p.get("pass"))
    total = max(1, len(pillars))
    frac = passed / total
    if passed >= total:
        accent, bg, status = C_GATE_PASS, "rgba(52,211,153,0.16)", "ALL CLEAR — GATE OPEN"
        cls = "fm-gate"
    elif passed >= max(4, total - 2):
        accent, bg, status = C_GATE_PARTIAL, "rgba(251,191,36,0.14)", "PARTIAL — STAND READY"
        cls = "fm-gate"
    else:
        accent, bg, status = C_GATE_FAIL, "rgba(248,113,113,0.14)", "BLOCKED — STAND ASIDE"
        cls = "fm-gate"

    logo = (
        f"Virtue Position Gate · {_esc(timeframe)} · "
        f"{passed}/{total} pillars pass"
    )
    pillar_html = []
    for p in pillars:
        ok = bool(p.get("pass"))
        p_color = C_GATE_PASS if ok else C_GATE_FAIL
        p_bg = "rgba(52,211,153,0.14)" if ok else "rgba(248,113,113,0.14)"
        pillar_html.append(
            f"<div class='fm-pillar' style='background:{p_bg};border:1px solid {p_color}66;'>"
            f"<div class='fm-pillar-name' style='color:{p_color};'>{_esc(p.get('name'))}</div>"
            f"<div class='fm-pillar-state' style='color:{C_INK};'>"
            f"{'PASS' if ok else 'FAIL'} · {_esc(p.get('detail'))}</div>"
            f"</div>"
        )

    st.markdown(
        f"<div class='{cls}' style='background:linear-gradient(135deg,{bg},{C_CARD});"
        f"border:1px solid {accent}66;border-left:10px solid {accent};'>"
        f"<div class='fm-gate-logo' style='color:{accent};'>{logo}</div>"
        f"<div class='fm-gate-sub' style='color:{C_INK};'>{_esc(status)}</div>"
        f"<div class='fm-gate-bar-track'>"
        f"<div class='fm-gate-bar' style='width:{frac * 100:.1f}%;"
        f"background:linear-gradient(90deg,{accent},{accent}aa);'></div></div>"
        f"<div class='fm-pillars'>{''.join(pillar_html)}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _metric_card(label: str, value: str, *, color: str, bg: str, sub: str = "") -> str:
    sub_html = (
        f"<div style='font-size:0.85rem;margin-top:0.35rem;opacity:0.85;color:{C_MUTED};'>"
        f"{_esc(sub)}</div>"
        if sub
        else ""
    )
    return (
        f"<div style='background:linear-gradient(180deg,{bg},{C_CARD});"
        f"border:1px solid {color}66;border-top:4px solid {color};"
        f"border-radius:10px;padding:0.9rem 1rem;min-height:6.2rem;"
        f"box-shadow:0 10px 28px rgba(0,0,0,0.28);'>"
        f"<div style='font-size:0.78rem;font-weight:700;letter-spacing:0.06em;"
        f"text-transform:uppercase;color:{C_MUTED};'>{_esc(label)}</div>"
        f"<div style='font-size:1.7rem;font-weight:800;color:{color};margin-top:0.25rem;"
        f"font-variant-numeric:tabular-nums;'>{_esc(value)}</div>"
        f"{sub_html}</div>"
    )


def _chip(label: str, value: str, *, color: str, bg: str) -> str:
    return (
        f"<div style='background:linear-gradient(180deg,{bg},{C_CARD});"
        f"border:1px solid {color}77;border-radius:10px;padding:0.75rem 0.9rem;"
        f"box-shadow:0 8px 22px rgba(0,0,0,0.25);'>"
        f"<div style='font-size:0.72rem;font-weight:700;letter-spacing:0.07em;"
        f"text-transform:uppercase;color:{C_MUTED};'>{_esc(label)}</div>"
        f"<div style='font-size:1.25rem;font-weight:800;color:{color};margin-top:0.2rem;'>"
        f"{_esc(value)}</div></div>"
    )


def _render_engine_message(reason: str, *, action: str = "FLAT") -> None:
    """Large color-coded status block."""
    headline, detail = _operator_reason_parts(reason)
    action_u = (action or "FLAT").upper()
    accent, bg = _side_colors(action_u)
    head_u = headline.upper()
    if "STAND ASIDE" in head_u or "FLATTEN" in head_u:
        accent, bg = C_FLAT, C_FLAT_BG
    elif "HOLDING LONG" in head_u or "LONG SETUP" in head_u:
        accent, bg = C_LONG, C_LONG_BG
    elif "HOLDING SHORT" in head_u or "SHORT SETUP" in head_u:
        accent, bg = C_SHORT, C_SHORT_BG

    st.markdown(
        f"<div style='margin:0.4rem 0 1rem 0;padding:1rem 1.15rem;border-radius:12px;"
        f"background:{bg};border:1px solid {accent}55;border-left:8px solid {accent};'>"
        f"<div style='font-size:1.55rem;font-weight:850;color:{accent};letter-spacing:0.02em;'>"
        f"{_esc(headline)}</div>"
        f"<div style='font-size:1.28rem;line-height:1.45;font-weight:600;color:{C_INK};"
        f"margin-top:0.35rem;'>{_esc(detail)}</div>"
        f"<div style='margin-top:0.55rem;font-size:1.05rem;color:{accent};font-weight:700;'>"
        f"Signal: {_esc(action_u)}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _unrealized(positions: list, last_price: float | None) -> float:
    if not last_price or last_price <= 0:
        return 0.0
    total = 0.0
    for p in positions:
        entry = float(p.get("entry_price") or 0)
        contracts = int(p.get("contracts") or 0)
        direction = str(p.get("direction") or "LONG").upper()
        if entry <= 0 or contracts <= 0:
            continue
        points = (last_price - entry) if direction == "LONG" else (entry - last_price)
        total += points * POINT_VALUE * contracts
    return round(total, 2)


st.set_page_config(
    page_title="MACROMATHICS.AI",
    layout="wide",
    initial_sidebar_state="collapsed",
)
_inject_css()
st.title("MACROMATHICS.AI")

if os.environ.get("FM_EXTERNAL_ORCHESTRATOR", "").strip().lower() in {"1", "true", "yes"}:
    st.caption("Cloud view — fed by background virtue loop (`system_state.json` poll)")

data = load_state()
age_s = _state_age_seconds(data)
dash = data.get("dashboard") or {}
session = data.get("session") or {}
grade = data.get("grade") or {}
virtue = data.get("virtue") or {}
entry_pipeline = data.get("entry_pipeline") or {}
positions = data.get("open_positions") or dash.get("open_positions") or []
last_price = dash.get("last_price") if dash.get("last_price") is not None else data.get("last_price")
unrealized = dash.get("unrealized_pnl")
if unrealized is None:
    unrealized = data.get("unrealized_pnl")
if unrealized is None:
    unrealized = _unrealized(positions, float(last_price) if last_price else None)

strategy = str(data.get("strategy") or dash.get("strategy") or "—")
data_source = str(data.get("data_source") or dash.get("data_source") or "—")
session_mode = str(data.get("session_mode") or dash.get("session_mode") or "").upper()
session_label = str(data.get("session_label") or dash.get("session_label") or "")
if session_mode == "CME" or "databento" in data_source.lower():
    st.caption("MES futures — Virtue Wisdom · Manus risk · Databento CME Globex + Webull execution")
elif session_mode == "RTH":
    st.caption("MES futures — Virtue Wisdom · Manus risk · Alpaca RTH proxy + Webull execution")
else:
    st.caption("MES futures — Virtue Wisdom brain · Manus risk (Webull execution)")
if session_label:
    st.caption(session_label)

_gate_pillars = _trading_gate_pillars(
    entry_pipeline=entry_pipeline,
    session=session,
    age_s=age_s,
    last_price=last_price,
)
_render_trading_gate_logo(_gate_pillars, timeframe="1D")

if age_s is not None and age_s > 30:
    st.warning(
        f"Engine looks idle — last update {int(age_s)}s ago. "
        "Check `futuremathics_virtue` on AWS (legacy grade path is disabled)."
    )

book = float(
    data.get("book_equity")
    or dash.get("book_equity")
    or HANDSHAKE_EQUITY_BASE
)
pnl = float(dash.get("daily_pnl") or session.get("realized_pnl_today") or 0)
open_pnl = float(unrealized or 0)
nav = round(book + open_pnl, 2)
mode = str(dash.get("mode") or "—")

# Position strip
if positions:
    p0 = positions[0]
    direction = str(p0.get("direction") or "?").upper()
    contracts = int(p0.get("contracts") or 0)
    entry = float(p0.get("entry_price") or 0)
    accent, bg = _side_colors(direction)
    pnl_c, _ = _pnl_colors(open_pnl)
    st.markdown(
        f"<div style='background:{bg};border:1px solid {accent}66;border-left:8px solid {accent};"
        f"border-radius:12px;padding:0.9rem 1.1rem;margin:0.2rem 0 0.9rem 0;'>"
        f"<span style='font-size:1.35rem;font-weight:850;color:{accent};'>"
        f"IN A TRADE — {_esc(direction)} {_esc(contracts)} MES @ {_esc(f'{entry:.2f}')}"
        f"</span>"
        f"<span style='font-size:1.15rem;font-weight:700;color:{pnl_c};margin-left:0.75rem;'>"
        f"open P&L ${_esc(f'{open_pnl:+,.2f}')}"
        + (f" · last {_esc(f'{float(last_price):.2f}')}" if last_price else "")
        + "</span></div>",
        unsafe_allow_html=True,
    )
else:
    exposure = str(
        (virtue.get("exposure") if virtue else None) or grade.get("exposure") or "FLAT"
    ).upper()
    accent, bg = _side_colors(exposure)
    st.markdown(
        f"<div style='background:{bg};border:1px solid {accent}55;border-left:8px solid {accent};"
        f"border-radius:12px;padding:0.85rem 1.1rem;margin:0.2rem 0 0.9rem 0;"
        f"font-size:1.2rem;font-weight:750;color:{accent};'>"
        f"FLAT — no open MES position (engine exposure: {_esc(exposure)})"
        f"</div>",
        unsafe_allow_html=True,
    )

# Color metric row
nav_c, nav_bg = _pnl_colors(pnl)  # day tone on NAV strip
open_c, open_bg = _pnl_colors(open_pnl)
day_c, day_bg = _pnl_colors(pnl)
risk_val = float(session.get("open_risk_notional", 0) or 0)
risk_c, risk_bg = (C_FLAT, C_FLAT_BG) if risk_val > 0 else (C_MUTED, C_PANEL)

m1, m2, m3, m4 = st.columns(4)
m1.markdown(
    _metric_card(
        "Account NAV",
        f"${nav:,.2f}",
        color="#f8fafc",
        bg=nav_bg,
        sub=f"mode {mode}",
    ),
    unsafe_allow_html=True,
)
m2.markdown(
    _metric_card("Open P&L", f"${open_pnl:+,.2f}", color=open_c, bg=open_bg),
    unsafe_allow_html=True,
)
m3.markdown(
    _metric_card("Closed today P&L", f"${pnl:+,.2f}", color=day_c, bg=day_bg),
    unsafe_allow_html=True,
)
m4.markdown(
    _metric_card(
        "Open risk",
        f"${risk_val:,.0f}",
        color=risk_c,
        bg=risk_bg,
        sub="at risk if stopped",
    ),
    unsafe_allow_html=True,
)

st.markdown(
    f"<div style='margin:0.55rem 0 0.35rem 0;color:{C_MUTED};font-size:0.95rem;'>"
    f"Book equity <strong style='color:{C_INK};'>${book:,.2f}</strong> "
    f"(compounded paper ledger)"
    f"</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='margin-bottom:0.85rem;color:{C_INK};font-size:0.98rem;'>"
    f"<span style='background:{C_PANEL};padding:0.2rem 0.55rem;border-radius:999px;"
    f"font-weight:700;color:{C_LONG if mode == 'PAPER' else C_SHORT};'>{_esc(mode)}</span>"
    f" · Strategy: <strong>{_esc(strategy)}</strong>"
    f" · Updated: {_esc(data.get('updated_at', '—'))}"
    f"</div>",
    unsafe_allow_html=True,
)

if virtue:
    action = str(virtue.get("action") or "FLAT")
    regime = str(virtue.get("regime") or "—")
    exposure = str(virtue.get("exposure") or "FLAT")
    contracts = virtue.get("contracts") or 0
    adx = float(virtue.get("adx") or 0)
    _render_engine_message(str(virtue.get("reason") or ""), action=action)

    r_c, r_bg = _side_colors(regime)
    s_c, s_bg = _side_colors(action)
    e_c, e_bg = _side_colors(exposure)
    if adx >= 25:
        a_c, a_bg = C_LONG, C_LONG_BG
    elif adx >= 18:
        a_c, a_bg = C_FLAT, C_FLAT_BG
    else:
        a_c, a_bg = C_MUTED, C_PANEL

    v1, v2, v3, v4 = st.columns(4)
    v1.markdown(_chip("Regime", regime, color=r_c, bg=r_bg), unsafe_allow_html=True)
    v2.markdown(_chip("Signal", action, color=s_c, bg=s_bg), unsafe_allow_html=True)
    v3.markdown(_chip("ADX", f"{adx:.1f}", color=a_c, bg=a_bg), unsafe_allow_html=True)
    v4.markdown(
        _chip("Exposure", f"{exposure} {contracts}", color=e_c, bg=e_bg),
        unsafe_allow_html=True,
    )
elif grade and strategy == "volumewatch_grade_path":
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("VW grade score", f"{float(grade.get('last_score') or 0):.1f}")
    g2.metric("Path", str(grade.get("path") or "—"))
    g3.metric("Exposure", str(grade.get("exposure") or "—"))
    g4.metric(
        "Peak / trough",
        f"{float(grade.get('peak_score') or 0):.1f} / {float(grade.get('trough_score') or 0):.1f}",
    )
    st.caption(
        "Rising path: cash below 50 → long at ≥50 → exit long at ≥85 → short at ≥90. "
        "Falling path: hold short until ≤50."
    )

verdict = str(session.get("last_risk_verdict", "—") or "—")
risk_reason = str(session.get("last_risk_reason", "") or "")
if verdict.upper() == "APPROVED":
    rv_c, rv_bg = C_UP, C_UP_BG
elif "HALT" in verdict.upper() or "REJECT" in verdict.upper():
    rv_c, rv_bg = C_DOWN, C_DOWN_BG
else:
    rv_c, rv_bg = C_FLAT, C_FLAT_BG

st.markdown(
    f"<div style='margin-top:0.9rem;padding:0.7rem 0.95rem;border-radius:10px;"
    f"background:{rv_bg};border:1px solid {rv_c}55;color:{C_INK};'>"
    f"<strong style='color:{rv_c};'>Risk: {_esc(verdict)}</strong>"
    f" — {_esc(risk_reason)}"
    f"</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='margin:0.55rem 0 0.85rem 0;color:{C_MUTED};'>"
    f"Cycles: <strong style='color:{C_INK};'>{_esc(session.get('cycle_count', 0))}</strong>"
    f" · Trades today: <strong style='color:{C_INK};'>"
    f"{_esc(session.get('trades_today', 0))}</strong>"
    f"</div>",
    unsafe_allow_html=True,
)

if positions:
    st.subheader("Open positions")
    st.dataframe(positions, use_container_width=True)
else:
    st.markdown(
        f"<div style='padding:0.65rem 0.9rem;border-radius:10px;background:{C_FLAT_BG};"
        f"border:1px solid {C_FLAT}44;color:{C_FLAT};font-weight:650;'>"
        f"No open MES positions right now."
        f"</div>",
        unsafe_allow_html=True,
    )

time.sleep(2)
st.rerun()
