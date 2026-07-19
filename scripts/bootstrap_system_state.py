#!/usr/bin/env python3
"""Seed data/system_state.json on cold boot."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.ui_state_bridge import ensure_boot_system_state


def main() -> None:
    ensure_boot_system_state()


if __name__ == "__main__":
    main()
