"""
In-memory metrics collector.

Thread-safe counters for request latency, error rate, and cache hit rate.
Designed for a single-instance deployment; memory usage is bounded.
"""

from __future__ import annotations

import threading
import time
from collections import deque
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
    cache_operations: int = 0


class MetricsCollector:
    """Thread-safe in-memory metrics collector."""

    # Sliding window for error rate calculation
    ERROR_WINDOW_SECONDS = 120.0
    # Latency percentiles are computed over the most recent requests only
    LATENCY_SAMPLE_SIZE = 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._total_requests = 0
        self._request_log: deque[tuple[float, int]] = deque()  # (timestamp, status_code)
        self._latencies: deque[float] = deque(maxlen=self.LATENCY_SAMPLE_SIZE)
        self._cache_hits = 0
        self._cache_misses = 0

    def record_request(self, path: str, status_code: int, duration_ms: float) -> None:
        """Record a completed HTTP request."""
        with self._lock:
            now = time.monotonic()
            self._total_requests += 1
            self._request_log.append((now, status_code))
            self._prune_request_log(now)
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
            self._prune_request_log(now)

            if self._request_log:
                errors = sum(1 for _, sc in self._request_log if sc >= 500)
                error_rate = errors / len(self._request_log) * 100.0
            else:
                error_rate = 0.0

            latencies = sorted(self._latencies)
            cache_ops = self._cache_hits + self._cache_misses
            cache_rate = (self._cache_hits / cache_ops * 100.0) if cache_ops > 0 else 0.0

            return MetricsSnapshot(
                uptime_seconds=round(now - self._start_time, 2),
                total_requests=self._total_requests,
                error_rate_pct=round(error_rate, 2),
                latency_p50_ms=round(self._percentile(latencies, 50), 2),
                latency_p95_ms=round(self._percentile(latencies, 95), 2),
                latency_p99_ms=round(self._percentile(latencies, 99), 2),
                cache_hit_rate_pct=round(cache_rate, 2),
                cache_operations=cache_ops,
            )

    def _prune_request_log(self, now: float) -> None:
        """Drop entries older than the error-rate window (caller holds the lock)."""
        cutoff = now - self.ERROR_WINDOW_SECONDS
        while self._request_log and self._request_log[0][0] < cutoff:
            self._request_log.popleft()

    @staticmethod
    def _percentile(sorted_data: list[float], pct: int) -> float:
        """Linear-interpolated percentile of already sorted data."""
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * pct / 100.0
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[-1]
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


# Module-level singleton
metrics_collector = MetricsCollector()
