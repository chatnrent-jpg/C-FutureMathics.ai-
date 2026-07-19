"""Manus live stream watchdog — latency ceiling and disconnect circuit breaker."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from engine.config import LATENCY_CEILING_MS

STREAM_CONNECT_TIMEOUT_S = 5.0


class StreamCircuitState(str, Enum):
    OK = "OK"
    DEGRADED_PAPER = "DEGRADED_PAPER"
    HALT = "HALT"


@dataclass
class StreamWatchdog:
    """
    Live Alpaca websocket watchdog.

    - Latency > ceiling  → DEGRADED_PAPER (force PAPER_ROUTE)
    - Disconnect/stale → HALT
    - Sequence gap     → DEGRADED_PAPER
    """

    max_latency_ms: float = LATENCY_CEILING_MS
    max_stale_s: float = 3.0
    state: StreamCircuitState = StreamCircuitState.OK
    last_reason: str = ""
    alerts: list[str] = field(default_factory=list)
    _last_packet_mono: float = 0.0
    _last_latency_ms: float = 0.0
    _last_sequence: int = 0

    @property
    def force_paper(self) -> bool:
        return self.state == StreamCircuitState.DEGRADED_PAPER

    @property
    def halted(self) -> bool:
        return self.state == StreamCircuitState.HALT

    def record_packet(
        self,
        *,
        latency_ms: float,
        sequence_id: int | None = None,
        received_mono: float | None = None,
    ) -> StreamCircuitState:
        now = received_mono if received_mono is not None else time.monotonic()
        self._last_packet_mono = now
        self._last_latency_ms = latency_ms

        if sequence_id is not None and self._last_sequence > 0:
            if sequence_id > self._last_sequence + 1:
                self._trip(
                    StreamCircuitState.DEGRADED_PAPER,
                    f"packet_gap_{self._last_sequence}_to_{sequence_id}",
                )
            self._last_sequence = max(self._last_sequence, sequence_id)

        if latency_ms > self.max_latency_ms:
            self._trip(
                StreamCircuitState.DEGRADED_PAPER,
                f"feed_latency_{latency_ms:.0f}ms_exceeds_{self.max_latency_ms:.0f}ms",
            )
        elif self.state == StreamCircuitState.DEGRADED_PAPER and latency_ms <= self.max_latency_ms:
            self._recover_ok()

        return self.state

    def record_disconnect(self, detail: str = "websocket_disconnect") -> StreamCircuitState:
        self._trip(StreamCircuitState.HALT, detail)
        return self.state

    def emit_connect_timeout(self, target: str, seconds: float = STREAM_CONNECT_TIMEOUT_S) -> None:
        """Print a TIMEOUT alert and degrade safely — never freeze the event loop."""
        reason = f"connect_timeout_{target}"
        alert = (
            f"MANUS_STREAM_ALERT [TIMEOUT] {target} handshake exceeded "
            f"{seconds:.0f}s — connection dropped safely"
        )
        self.alerts.append(alert)
        print(alert, flush=True)
        self._trip(StreamCircuitState.DEGRADED_PAPER, reason)

    def check_stale(self) -> StreamCircuitState:
        if self._last_packet_mono <= 0:
            return self.state
        elapsed = time.monotonic() - self._last_packet_mono
        if elapsed > self.max_stale_s:
            self._trip(StreamCircuitState.HALT, f"stream_stale_{elapsed:.1f}s")
        return self.state

    def seed_sequence(self, sequence_id: int) -> None:
        self._last_sequence = sequence_id

    def _trip(self, state: StreamCircuitState, reason: str) -> None:
        if self.state == state and self.last_reason == reason:
            return
        self.state = state
        self.last_reason = reason
        alert = f"MANUS_NETWORK_ALERT [{state.value}] {reason}"
        self.alerts.append(alert)
        print(alert, flush=True)

    def _recover_ok(self) -> None:
        if self.state != StreamCircuitState.OK:
            print("MANUS_NETWORK_ALERT [OK] stream_recovered", flush=True)
        self.state = StreamCircuitState.OK
        self.last_reason = ""

    def clear_degraded(self) -> None:
        """Clear DEGRADED_PAPER after a successful latency recovery pause."""
        self._recover_ok()

    def pop_alerts(self) -> list[str]:
        out = list(self.alerts)
        self.alerts.clear()
        return out
