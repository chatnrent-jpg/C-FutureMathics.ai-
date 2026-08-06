#!/usr/bin/env python3
"""Merge key=value pairs from a temp file into .env.local (Justice: no stdout of secrets)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, help="Temp file with KEY=VALUE lines")
    parser.add_argument("--envf", required=True, help="Target .env.local path")
    args = parser.parse_args()
    src = Path(args.src)
    envf = Path(args.envf)
    if not src.is_file():
        print(f"missing_src={src}", file=sys.stderr)
        return 1
    incoming: dict[str, str] = {}
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        incoming[key.strip()] = val.strip()
    if not incoming:
        print("no_keys", file=sys.stderr)
        return 2
    lines = envf.read_text(encoding="utf-8").splitlines() if envf.is_file() else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in incoming:
            out.append(f"{key}={incoming[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in incoming.items():
        if key not in seen:
            out.append(f"{key}={val}")
    envf.parent.mkdir(parents=True, exist_ok=True)
    envf.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        src.unlink()
    except OSError:
        pass
    print("env_keys_merged count=%s" % len(incoming))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
