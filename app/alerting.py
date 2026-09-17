"""
Alert manager.

Evaluates operational thresholds and emits structured log alerts with cooldown
to prevent spam.  Thresholds:

- CRITICAL: error_rate > 10%, any circuit breaker OPEN > 5 min
- WARNING:  latency p95 > 3000 ms, cache hit rate < 30%
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import StrEnum

from app.circuit_breaker import CircuitBreakerState, service_breakers
from app.config import get_settings
from app.metrics import metrics_collector

logger = logging.getLogger(__name__)


class AlertLevel(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


@dataclass
class Alert:
    level: AlertLevel
    rule: str
    message: str
    value: float
    threshold: float


class AlertManager:
    """Evaluate operational thresholds and emit structured log alerts."""

    # Cooldown per alert level (seconds)
    COOLDOWN_CRITICAL = 300
    COOLDOWN_WARNING = 900
    # Throttle interval for maybe_check()
    CHECK_INTERVAL = 30.0

    def __init__(self) -> None:
        self._cooldowns: dict[str, float] = {}
        self._last_check: float = 0.0

    def check_alerts(self) -> list[Alert]:
        """Evaluate all thresholds and return fired alerts."""
        settings = get_settings()
        snap = metrics_collector.snapshot()
        now = time.monotonic()
        alerts: list[Alert] = []

        # --- CRITICAL: error rate ---
        if snap.error_rate_pct > settings.alert_error_rate_pct:
            alert = Alert(
                level=AlertLevel.CRITICAL,
                rule="error_rate",
                message=f"Error rate {snap.error_rate_pct:.1f}% exceeds threshold {settings.alert_error_rate_pct:.1f}%",
                value=snap.error_rate_pct,
                threshold=settings.alert_error_rate_pct,
            )
            if self._should_fire("error_rate", AlertLevel.CRITICAL, now):
                alerts.append(alert)

        # --- CRITICAL: circuit breaker OPEN > alert_cb_open_seconds ---
        for name, st in service_breakers.all_statuses().items():
            if (
                st["state"] == CircuitBreakerState.OPEN.value
                and st["time_in_state_seconds"] > settings.alert_cb_open_seconds
            ):
                rule = f"cb_open:{name}"
                alert = Alert(
                    level=AlertLevel.CRITICAL,
                    rule=rule,
                    message=f"Circuit breaker '{name}' OPEN for {st['time_in_state_seconds']:.0f}s (threshold {settings.alert_cb_open_seconds}s)",
                    value=st["time_in_state_seconds"],
                    threshold=float(settings.alert_cb_open_seconds),
                )
                if self._should_fire(rule, AlertLevel.CRITICAL, now):
                    alerts.append(alert)

        # --- WARNING: latency p95 ---
        if snap.latency_p95_ms > settings.alert_latency_p95_ms:
            alert = Alert(
                level=AlertLevel.WARNING,
                rule="latency_p95",
                message=f"Latency p95 {snap.latency_p95_ms:.0f}ms exceeds threshold {settings.alert_latency_p95_ms:.0f}ms",
                value=snap.latency_p95_ms,
                threshold=settings.alert_latency_p95_ms,
            )
            if self._should_fire("latency_p95", AlertLevel.WARNING, now):
                alerts.append(alert)

        # --- WARNING: cache hit rate (only if there are cache operations) ---
        if snap.cache_operations > 0 and snap.cache_hit_rate_pct < settings.alert_cache_rate_pct:
            alert = Alert(
                level=AlertLevel.WARNING,
                rule="cache_hit_rate",
                message=f"Cache hit rate {snap.cache_hit_rate_pct:.1f}% below threshold {settings.alert_cache_rate_pct:.1f}%",
                value=snap.cache_hit_rate_pct,
                threshold=settings.alert_cache_rate_pct,
            )
            if self._should_fire("cache_hit_rate", AlertLevel.WARNING, now):
                alerts.append(alert)

        # Emit structured log for each alert
        for alert in alerts:
            log_fn = logger.error if alert.level is AlertLevel.CRITICAL else logger.warning
            log_fn(
                "%s alert: %s",
                alert.level.value,
                alert.message,
                extra={"alert": True, "alert_rule": alert.rule},
            )

        return alerts

    def maybe_check(self) -> list[Alert] | None:
        """Run check_alerts() at most every CHECK_INTERVAL seconds."""
        now = time.monotonic()
        if now - self._last_check < self.CHECK_INTERVAL:
            return None
        self._last_check = now
        return self.check_alerts()

    # ------------------------------------------------------------------
    def _should_fire(self, rule: str, level: AlertLevel, now: float) -> bool:
        """Return True if the cooldown for *rule* has expired."""
        cooldown = self.COOLDOWN_CRITICAL if level is AlertLevel.CRITICAL else self.COOLDOWN_WARNING
        last = self._cooldowns.get(rule, 0.0)
        if now - last < cooldown:
            return False
        self._cooldowns[rule] = now
        return True


# Module-level singleton
alert_manager = AlertManager()
