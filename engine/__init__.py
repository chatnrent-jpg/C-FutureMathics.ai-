"""FutureMathics engine package."""

from __future__ import annotations

from typing import Any

__all__ = ["FutureMathicsEngine"]


def __getattr__(name: str) -> Any:
    if name == "FutureMathicsEngine":
        from engine.futuremathics_engine import FutureMathicsEngine

        return FutureMathicsEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
