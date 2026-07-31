#!/usr/bin/env python3
"""
Databento MES diagnostic smoke test.

Prints clear status for:
  1) API key loaded from .env.local
  2) Historical GLBX access (credits / plan)
  3) Live mbp-1 stream (requires Standard + CME live license)

Usage:
  python scripts/smoke_databento_mes.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.env_loader import load_project_env


def main() -> int:
    load_project_env(ROOT)
    import os

    import databento as db

    key = os.getenv("DATABENTO_API_KEY", "").strip()
    print(f"configured={bool(key)} key_len={len(key)}")
    if not key:
        print("FAIL: set DATABENTO_API_KEY in C:\\FutureMathics.ai\\.env.local")
        return 1

    # --- Historical probe (works with free credits) ---
    print("\n[1/2] Historical OHLCV probe (MES.FUT)...")
    try:
        hist = db.Historical(key=key)
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=6)
        data = hist.timeseries.get_range(
            dataset="GLBX.MDP3",
            schema="ohlcv-1m",
            symbols="MES.FUT",
            stype_in="parent",
            start=start.isoformat(),
            end=end.isoformat(),
            limit=5,
        )
        rows = list(data)
        print(f"  historical_ok records={len(rows)}")
        if rows:
            last = rows[-1]
            close = getattr(last, "pretty_close", None) or (float(getattr(last, "close", 0)) * 1e-9)
            print(f"  last_close≈{float(close):.2f}")
    except Exception as exc:
        print(f"  historical_FAIL: {type(exc).__name__}: {exc}")
        print("  → Fix API key / historical entitlement in Databento portal first.")

    # --- Live probe (needs Standard + activated CME live license) ---
    print("\n[2/2] Live mbp-1 probe (MES.FUT) — 12s...")
    live_err = ""
    got_quote = False
    try:
        live = db.Live(key=key)
        live.subscribe(
            dataset="GLBX.MDP3",
            schema="mbp-1",
            symbols="MES.FUT",
            stype_in="parent",
        )
        deadline = time.time() + 12.0
        n = 0
        for record in live:
            n += 1
            if isinstance(record, db.ErrorMsg):
                live_err = f"ErrorMsg code={getattr(record, 'err', getattr(record, 'code', '?'))} {record}"
                print(f"  live_error: {live_err}")
                break
            if isinstance(record, db.SymbolMappingMsg):
                print(
                    f"  symbol_map: {getattr(record, 'stype_in_symbol', '')} "
                    f"→ {getattr(record, 'stype_out_symbol', '')}"
                )
                continue
            if isinstance(record, db.MBP1Msg):
                levels = getattr(record, "levels", None) or ()
                if not levels:
                    continue
                bid = float(levels[0].pretty_bid_px or 0.0)
                ask = float(levels[0].pretty_ask_px or 0.0)
                if bid > 0 or ask > 0:
                    mid = (bid + ask) / 2.0 if bid and ask else (bid or ask)
                    print(f"  LIVE_OK mid={mid:.2f} bid={bid:.2f} ask={ask:.2f}")
                    got_quote = True
                    break
            if time.time() >= deadline:
                break
        try:
            live.stop()
        except Exception:
            pass
        if not got_quote and not live_err:
            print(f"  live_FAIL: no MBP-1 in 12s (records_seen={n})")
            print("  → Most common cause: live CME not activated.")
            print("     Databento portal → Plans and live data → Activate live data for CME / GLBX.MDP3")
            print("     Personal use is included with Standard (~$199/mo), but you must complete the license questionnaire.")
            print("     Free historical credits alone do NOT enable Live().")
    except Exception as exc:
        print(f"  live_FAIL: {type(exc).__name__}: {exc}")
        print("  → If you see Not authorized / AuthFailed: activate CME live license in the portal.")
        return 2

    return 0 if got_quote else 2


if __name__ == "__main__":
    raise SystemExit(main())
