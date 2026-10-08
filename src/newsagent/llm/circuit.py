"""Circuit breaker per provider (TASK-019)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..logging_setup import get_logger

log = get_logger("llm.circuit")

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 120


@dataclass
class Circuit:
    state: str = "CLOSED"  # CLOSED | OPEN | HALF-OPEN
    failures: int = 0
    opened_at: float = 0.0
    half_open_probe: bool = False

    def allow(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if time.time() - self.opened_at >= COOLDOWN_SECONDS:
                self.state = "HALF-OPEN"
                self.half_open_probe = False
                log.info("circuit -> HALF-OPEN")
                return True
            return False
        # HALF-OPEN: allow a single probe at a time
        if self.half_open_probe:
            return False
        self.half_open_probe = True
        return True

    def on_success(self) -> None:
        if self.state != "CLOSED":
            log.info("circuit -> CLOSED (recovered)")
        self.state = "CLOSED"
        self.failures = 0
        self.half_open_probe = False

    def on_failure(self) -> None:
        self.failures += 1
        if self.state == "HALF-OPEN":
            self.state = "OPEN"
            self.opened_at = time.time()
            self.half_open_probe = False
            log.warning("circuit -> OPEN (half-open probe failed)")
        elif self.failures >= FAILURE_THRESHOLD:
            self.state = "OPEN"
            self.opened_at = time.time()
            log.warning("circuit -> OPEN after %d failures", self.failures)


_circuits: dict[str, Circuit] = {}


def get_circuit(provider: str) -> Circuit:
    if provider not in _circuits:
        _circuits[provider] = Circuit()
    return _circuits[provider]


def reset_circuits() -> None:
    _circuits.clear()
