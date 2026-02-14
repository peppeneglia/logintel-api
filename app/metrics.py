"""
In-memory metrics collector — FRD Section 9.2.

Thread-safe counters for request latency, error rate, and cache hit rate.
Designed for single-instance deployment (Railway free tier).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class MetricsSnapshot:
    """Point-in-time snapshot of collected metrics."""

    uptime_seconds: float
    total_requests: int
    error_rate_pct: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    cache_hit_rate_pct: float


class MetricsCollector:
    """Thread-safe in-memory metrics collector."""

    # Sliding window for error rate calculation
    ERROR_WINDOW_SECONDS = 120.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._total_requests = 0
        self._request_log: list[tuple[float, int]] = []  # (timestamp, status_code)
        self._latencies: list[float] = []
        self._cache_hits = 0
        self._cache_misses = 0

    def record_request(self, path: str, status_code: int, duration_ms: float) -> None:
        """Record a completed HTTP request."""
        with self._lock:
            self._total_requests += 1
            self._request_log.append((time.monotonic(), status_code))
            self._latencies.append(duration_ms)

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def snapshot(self) -> MetricsSnapshot:
        """Return a point-in-time snapshot of all metrics."""
        with self._lock:
            now = time.monotonic()
            uptime = now - self._start_time

            # Error rate over sliding window
            cutoff = now - self.ERROR_WINDOW_SECONDS
            recent = [(ts, sc) for ts, sc in self._request_log if ts >= cutoff]
            if recent:
                errors = sum(1 for _, sc in recent if sc >= 500)
                error_rate = (errors / len(recent)) * 100.0
            else:
                error_rate = 0.0

            # Latency percentiles
            p50 = self._percentile(self._latencies, 50)
            p95 = self._percentile(self._latencies, 95)
            p99 = self._percentile(self._latencies, 99)

            # Cache hit rate
            total_cache = self._cache_hits + self._cache_misses
            cache_rate = (self._cache_hits / total_cache * 100.0) if total_cache > 0 else 0.0

            return MetricsSnapshot(
                uptime_seconds=round(uptime, 2),
                total_requests=self._total_requests,
                error_rate_pct=round(error_rate, 2),
                latency_p50_ms=round(p50, 2),
                latency_p95_ms=round(p95, 2),
                latency_p99_ms=round(p99, 2),
                cache_hit_rate_pct=round(cache_rate, 2),
            )

    @staticmethod
    def _percentile(data: list[float], pct: int) -> float:
        """Compute the pct-th percentile of a list of floats."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * pct / 100.0
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[-1]
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f)
        return d0 + d1


# Module-level singleton
metrics_collector = MetricsCollector()
