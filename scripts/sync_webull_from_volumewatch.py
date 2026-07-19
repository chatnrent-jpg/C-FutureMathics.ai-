#!/usr/bin/env python3
"""Copy WEBULL_* keys from VolumeWatch .env.local into FutureMathics .env.local."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VW = Path(r"C:\Volumewatch")
KEYS = (
    "WEBULL_APP_KEY",
    "WEBULL_API_KEY",
    "WEBULL_APP_SECRET",
    "WEBULL_API_SECRET",
    "WEBULL_ACCOUNT_ID",
    "WEBULL_FUTURES_ACCOUNT_ID",
    "WEBULL_API_HOST",
    "WEBULL_ACCESS_TOKEN",
    "WEBULL_TOKEN",
)


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def main() -> None:
    src = VW / ".env.local"
    if not src.exists():
        src = VW / ".env"
    dst = ROOT / ".env.local"
    if not src.exists():
        print(f"FAIL: No VolumeWatch env at {src}")
        sys.exit(1)

    src_vals = parse_env(src)
    dst_lines: list[str] = []
    if dst.exists():
        dst_lines = dst.read_text(encoding="utf-8").splitlines()

    merged_keys = {k for k in KEYS if k in src_vals}
    if not any(k in src_vals for k in ("WEBULL_APP_KEY", "WEBULL_API_KEY")):
        print("FAIL: No WEBULL_APP_KEY in VolumeWatch env.")
        sys.exit(1)

    out_lines: list[str] = []
    seen: set[str] = set()
    for line in dst_lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in KEYS and key in src_vals:
                out_lines.append(f"{key}={src_vals[key]}")
                seen.add(key)
                continue
        out_lines.append(line)

    out_lines.append("")
    out_lines.append("# Webull (synced from VolumeWatch)")
    for key in KEYS:
        if key in src_vals and key not in seen:
            out_lines.append(f"{key}={src_vals[key]}")
            seen.add(key)

    dst.write_text("\n".join(out_lines).strip() + "\n", encoding="utf-8")
    print(f"OK: Synced {len(seen)} Webull vars to {dst}")
    print("Set WEBULL_FUTURES_ACCOUNT_ID to your futures SIM account if different from WEBULL_ACCOUNT_ID.")


if __name__ == "__main__":
    main()
