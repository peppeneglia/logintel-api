"""
Circuit breaker for external service calls.

Per-service circuit breakers with CLOSED → OPEN → HALF_OPEN state machine.
After `failure_threshold` consecutive failures the breaker opens and all
requests fail fast. After `recovery_timeout` seconds the breaker becomes
HALF_OPEN and lets probe requests through: the first success closes it,
a failure re-opens it.
"""

from __future__ import annotations

import threading
import time
from enum import StrEnum

from app.config import get_settings


class CircuitBreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Per-service circuit breaker (thread-safe)."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._lock = threading.Lock()
        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: float | None = None
        self._state_changed_at = time.monotonic()

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            return self._state

    def allow_request(self) -> bool:
        """Return True if a request is allowed through the breaker."""
        with self._lock:
            if self._state is CircuitBreakerState.CLOSED:
                return True

            if self._state is CircuitBreakerState.OPEN:
                if time.monotonic() - self._state_changed_at >= self.recovery_timeout:
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._state_changed_at = time.monotonic()
                    return True
                return False

            # HALF_OPEN — allow probe requests
            return True

    def record_success(self) -> None:
        """Record a successful request."""
        with self._lock:
            self._consecutive_failures = 0
            if self._state is CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.CLOSED
                self._state_changed_at = time.monotonic()

    def record_failure(self) -> None:
        """Record a failed request."""
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_time = time.monotonic()

            if self._state is CircuitBreakerState.HALF_OPEN or (
                self._state is CircuitBreakerState.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                self._state = CircuitBreakerState.OPEN
                self._state_changed_at = time.monotonic()

    def status(self) -> dict:
        """Return a snapshot of the breaker status."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "consecutive_failures": self._consecutive_failures,
                "last_failure_time": self._last_failure_time,
                "time_in_state_seconds": round(time.monotonic() - self._state_changed_at, 2),
            }


class ServiceBreakers:
    """Registry of per-service circuit breakers (lazy creation)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, service_name: str) -> CircuitBreaker:
        """Return the breaker for *service_name*, creating it on first access."""
        with self._lock:
            if service_name not in self._breakers:
                settings = get_settings()
                self._breakers[service_name] = CircuitBreaker(
                    name=service_name,
                    failure_threshold=settings.cb_failure_threshold,
                    recovery_timeout=settings.cb_recovery_timeout,
                )
            return self._breakers[service_name]

    def all_statuses(self) -> dict[str, dict]:
        """Return a snapshot of every registered breaker."""
        with self._lock:
            return {name: cb.status() for name, cb in self._breakers.items()}


# Module-level singleton
service_breakers = ServiceBreakers()
