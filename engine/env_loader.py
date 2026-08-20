"""Load .env / .env.local into os.environ (does not override existing)."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        raw = path.read_bytes().replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            try:
                os.environ[key] = val
            except ValueError:
                continue


def load_project_env(root: Path | None = None) -> None:
    base = root or Path(__file__).resolve().parent.parent
    for name in (".env.local", ".env"):
        load_env_file(base / name)
