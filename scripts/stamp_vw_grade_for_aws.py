"""Stamp Macro Hub state for AWS delivery (keep hub score, refresh timestamp)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: stamp_vw_grade_for_aws.py SRC TMP", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    tmp = Path(sys.argv[2])
    data = json.loads(src.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    vw = data.get("volumewatch") or {}
    vw["hub_timestamp"] = vw.get("timestamp")
    vw["timestamp"] = now
    data["volumewatch"] = vw
    meta = data.get("metadata") or {}
    meta["aws_synced_at"] = now
    meta["source"] = "macro_hub_screen"
    data["metadata"] = meta
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    score = float(vw.get("overall_score") or 0.0)
    grade = vw.get("overall_grade")
    perm = vw.get("trade_permission")
    print(f"{score:.2f}|{grade}|{perm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
