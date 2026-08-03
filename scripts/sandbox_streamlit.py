#!/usr/bin/env python3
"""FutureMathics — Streamlit dashboard (polls data/system_state.json)."""

from __future__ import annotations

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
from engine.ui_state_bridge import ensure_boot_system_state

STATE_PATH = ROOT / "data" / "system_state.json"
ensure_boot_system_state(STATE_PATH)


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
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" UTC", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


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


st.set_page_config(page_title="FutureMathics", layout="wide")
st.title("FutureMathics.ai")

if os.environ.get("FM_EXTERNAL_ORCHESTRATOR", "").strip().lower() in {"1", "true", "yes"}:
    st.caption("Cloud view — fed by background virtue loop (`system_state.json` poll)")

data = load_state()
age_s = _state_age_seconds(data)
dash = data.get("dashboard") or {}
session = data.get("session") or {}
grade = data.get("grade") or {}
virtue = data.get("virtue") or {}
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
if age_s is not None and age_s > 30:
    st.warning(
        f"Engine looks idle — last update {int(age_s)}s ago. "
        "Check `futuremathics_virtue` on AWS (legacy grade path is disabled)."
    )

# Big status first — this is what you look at
if positions:
    p0 = positions[0]
    direction = str(p0.get("direction") or "?").upper()
    contracts = int(p0.get("contracts") or 0)
    entry = float(p0.get("entry_price") or 0)
    st.success(
        f"IN A TRADE — {direction} {contracts} MES @ {entry:.2f}"
        + (f" · last {float(last_price):.2f}" if last_price else "")
        + f" · open P&L ${float(unrealized):+,.2f}"
    )
else:
    exposure = str(grade.get("exposure") or "FLAT").upper()
    st.info(f"FLAT — no open MES position (engine exposure: {exposure})")

# Mark-to-market from compounded book equity (never handshake $15k starting_nav).
book = float(
    data.get("book_equity")
    or dash.get("book_equity")
    or HANDSHAKE_EQUITY_BASE
)
pnl = float(dash.get("daily_pnl") or session.get("realized_pnl_today") or 0)
open_pnl = float(unrealized or 0)
nav = round(book + open_pnl, 2)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Account NAV", f"${nav:,.2f}")
c2.metric("Open P&L", f"${open_pnl:+,.2f}")
c3.metric("Closed today P&L", f"${pnl:,.2f}")
c4.metric("Open risk", f"${session.get('open_risk_notional', 0):,.0f}")
st.caption(f"Book equity ${book:,.2f} (compounded paper ledger)")

st.write(
    f"**Mode:** {dash.get('mode', '—')} · **Strategy:** {strategy} · "
    f"**Updated:** {data.get('updated_at', '—')}"
)

if virtue:
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Regime", str(virtue.get("regime") or "—"))
    v2.metric("Signal", str(virtue.get("action") or "—"))
    v3.metric("ADX", f"{float(virtue.get('adx') or 0):.1f}")
    v4.metric("Exposure", f"{virtue.get('exposure') or 'FLAT'} {virtue.get('contracts') or 0}")
    st.caption(str(virtue.get("reason") or ""))
elif grade and strategy == "volumewatch_grade_path":
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("VW grade score", f"{float(grade.get('last_score') or 0):.1f}")
    g2.metric("Path", str(grade.get("path") or "—"))
    g3.metric("Exposure", str(grade.get("exposure") or "—"))
    g4.metric("Peak / trough", f"{float(grade.get('peak_score') or 0):.1f} / {float(grade.get('trough_score') or 0):.1f}")
    st.caption(
        "Rising path: cash below 50 → long at ≥50 → exit long at ≥85 → short at ≥90. "
        "Falling path: hold short until ≤50."
    )

st.write(f"**Risk:** {session.get('last_risk_verdict', '—')} — {session.get('last_risk_reason', '')}")
st.write(f"**Cycles:** {session.get('cycle_count', 0)} · **Trades today:** {session.get('trades_today', 0)}")

if positions:
    st.subheader("Open positions")
    st.dataframe(positions, use_container_width=True)
else:
    st.info("No open MES positions right now.")

time.sleep(2)
st.rerun()
