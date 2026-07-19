"""Multi-broker heartbeat — connection latency and liveness monitoring."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class HeartbeatState(str, Enum):
    GREEN = "GREEN"
    DEGRADED = "DEGRADED"
    DEAD = "DEAD"


@dataclass(frozen=True, slots=True)
class HeartbeatEvent:
    broker_id: str
    state: HeartbeatState
    latency_ms: float
    ok: bool
    detail: str = ""


HealthCheckFn = Callable[[], Awaitable[tuple[bool, str]]]


@dataclass
class BrokerHeartbeatAgent:
    """Polls each broker every `interval_s`; classifies latency into GREEN/DEGRADED/DEAD."""

    brokers: dict[str, HealthCheckFn]
    interval_s: float = 5.0
    green_ms: float = 200.0
    degraded_ms: float = 500.0
    missed_beats_halt: int = 3
    _last_events: dict[str, HeartbeatEvent] = field(default_factory=dict, init=False)
    _missed: dict[str, int] = field(default_factory=dict, init=False)

    def classify(self, latency_ms: float, ok: bool) -> HeartbeatState:
        if not ok or latency_ms > self.degraded_ms:
            return HeartbeatState.DEAD
        if latency_ms > self.green_ms:
            return HeartbeatState.DEGRADED
        return HeartbeatState.GREEN

    async def probe_broker(self, broker_id: str, check: HealthCheckFn) -> HeartbeatEvent:
        t0 = time.perf_counter()
        try:
            ok, detail = await asyncio.wait_for(check(), timeout=self.degraded_ms / 1000.0)
        except (asyncio.TimeoutError, OSError) as exc:
            ok, detail = False, str(exc)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        state = self.classify(latency_ms, ok)
        if state == HeartbeatState.DEAD:
            self._missed[broker_id] = self._missed.get(broker_id, 0) + 1
        else:
            self._missed[broker_id] = 0
        if self._missed.get(broker_id, 0) >= self.missed_beats_halt:
            state = HeartbeatState.DEAD
        event = HeartbeatEvent(broker_id=broker_id, state=state, latency_ms=latency_ms, ok=ok, detail=detail)
        self._last_events[broker_id] = event
        return event

    async def run_once(self) -> list[HeartbeatEvent]:
        tasks = [self.probe_broker(bid, fn) for bid, fn in self.brokers.items()]
        return list(await asyncio.gather(*tasks))

    def all_green(self) -> bool:
        if not self._last_events:
            return False
        return all(e.state == HeartbeatState.GREEN for e in self._last_events.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            bid: {
                "state": ev.state.value,
                "latency_ms": round(ev.latency_ms, 2),
                "ok": ev.ok,
                "detail": ev.detail,
            }
            for bid, ev in self._last_events.items()
        }
