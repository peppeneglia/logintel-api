"""
Tests for Block 7B: Monitoring and Observability.

Covers MetricsCollector, RequestIdMiddleware, TimingMiddleware,
enriched health endpoint, and structured logging.
"""

from __future__ import annotations

import json
import logging
import time
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.logging_config import LogintelJsonFormatter, org_id_var, request_id_var
from app.metrics import MetricsCollector
from app.middleware import RequestIdMiddleware, TimingMiddleware


# ── MetricsCollector ──────────────────────────────────────────────


class TestMetricsCollector:
    def test_record_request_increments_total(self):
        mc = MetricsCollector()
        mc.record_request("/v1/health", 200, 5.0)
        mc.record_request("/v1/predictions", 201, 10.0)
        snap = mc.snapshot()
        assert snap.total_requests == 2

    def test_error_rate_counts_5xx(self):
        mc = MetricsCollector()
        mc.record_request("/a", 200, 1.0)
        mc.record_request("/b", 500, 1.0)
        mc.record_request("/c", 502, 1.0)
        mc.record_request("/d", 404, 1.0)
        snap = mc.snapshot()
        # 2 errors out of 4 requests = 50%
        assert snap.error_rate_pct == 50.0

    def test_error_rate_zero_when_no_requests(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.error_rate_pct == 0.0

    def test_latency_percentiles(self):
        mc = MetricsCollector()
        # Insert 100 values: 1.0, 2.0, ..., 100.0
        for i in range(1, 101):
            mc.record_request("/x", 200, float(i))
        snap = mc.snapshot()
        assert snap.latency_p50_ms == pytest.approx(50.5, abs=1.0)
        assert snap.latency_p95_ms == pytest.approx(95.05, abs=1.0)
        assert snap.latency_p99_ms == pytest.approx(99.01, abs=1.0)

    def test_latency_empty(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.latency_p50_ms == 0.0
        assert snap.latency_p95_ms == 0.0

    def test_cache_hit_miss(self):
        mc = MetricsCollector()
        mc.record_cache_hit()
        mc.record_cache_hit()
        mc.record_cache_miss()
        snap = mc.snapshot()
        # 2 hits out of 3 total = 66.67%
        assert snap.cache_hit_rate_pct == pytest.approx(66.67, abs=0.01)

    def test_cache_rate_zero_when_no_cache_ops(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.cache_hit_rate_pct == 0.0

    def test_snapshot_has_uptime(self):
        mc = MetricsCollector()
        time.sleep(0.05)
        snap = mc.snapshot()
        assert snap.uptime_seconds >= 0.04

    def test_snapshot_fields_present(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert hasattr(snap, "uptime_seconds")
        assert hasattr(snap, "total_requests")
        assert hasattr(snap, "error_rate_pct")
        assert hasattr(snap, "latency_p50_ms")
        assert hasattr(snap, "latency_p95_ms")
        assert hasattr(snap, "latency_p99_ms")
        assert hasattr(snap, "cache_hit_rate_pct")


# ── Middleware (via TestClient) ───────────────────────────────────


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


class TestRequestIdMiddleware:
    def test_generates_request_id(self, client):
        resp = client.get("/v1/health")
        assert "X-Request-ID" in resp.headers
        # Should be a valid UUID-ish string
        assert len(resp.headers["X-Request-ID"]) > 10

    def test_preserves_client_request_id(self, client):
        custom_id = "my-custom-req-123"
        resp = client.get("/v1/health", headers={"X-Request-ID": custom_id})
        assert resp.headers["X-Request-ID"] == custom_id


class TestTimingMiddleware:
    def test_records_duration(self, client):
        """TimingMiddleware should feed metrics_collector on each request."""
        from app.metrics import metrics_collector
        before = metrics_collector.snapshot().total_requests
        client.get("/v1/health")
        after = metrics_collector.snapshot().total_requests
        assert after > before


# ── Health Endpoint ───────────────────────────────────────────────


class TestHealthEndpoint:
    def test_health_has_dependencies(self, client):
        resp = client.get("/v1/health")
        data = resp.json()
        assert "dependencies" in data
        assert "redis" in data["dependencies"]

    def test_health_has_metrics(self, client):
        resp = client.get("/v1/health")
        data = resp.json()
        assert "metrics" in data
        metrics = data["metrics"]
        assert "uptime_seconds" in metrics
        assert "total_requests" in metrics
        assert "error_rate_pct" in metrics
        assert "latency_p95_ms" in metrics
        assert "cache_hit_rate_pct" in metrics

    def test_health_degraded_when_redis_down(self, client):
        """When Redis ping fails, status should be degraded."""
        with patch(
            "app.routes.health._check_redis",
            new_callable=AsyncMock,
            return_value={"status": "unhealthy", "reason": "connection refused"},
        ):
            resp = client.get("/v1/health")
            data = resp.json()
            assert data["status"] == "degraded"


# ── Structured Logging ────────────────────────────────────────────


class TestStructuredLogging:
    def test_json_output(self):
        """LogintelJsonFormatter should produce valid JSON."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = LogintelJsonFormatter(
            fmt="%(timestamp)s %(level)s %(logger)s %(message)s"
        )
        handler.setFormatter(formatter)

        test_logger = logging.getLogger("test.json_output")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.DEBUG)

        test_logger.info("hello world")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed
        assert parsed["level"] == "INFO"

    def test_request_id_in_log(self):
        """Log output should include request_id from ContextVar."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = LogintelJsonFormatter(
            fmt="%(timestamp)s %(level)s %(logger)s %(message)s"
        )
        handler.setFormatter(formatter)

        test_logger = logging.getLogger("test.request_id")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.DEBUG)

        token = request_id_var.set("req-abc-123")
        try:
            test_logger.info("with request id")
            output = stream.getvalue().strip()
            parsed = json.loads(output)
            assert parsed["request_id"] == "req-abc-123"
        finally:
            request_id_var.reset(token)

    def test_org_id_in_log(self):
        """Log output should include organization_id from ContextVar."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = LogintelJsonFormatter(
            fmt="%(timestamp)s %(level)s %(logger)s %(message)s"
        )
        handler.setFormatter(formatter)

        test_logger = logging.getLogger("test.org_id")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.DEBUG)

        token = org_id_var.set("org-xyz-456")
        try:
            test_logger.info("with org id")
            output = stream.getvalue().strip()
            parsed = json.loads(output)
            assert parsed["organization_id"] == "org-xyz-456"
        finally:
            org_id_var.reset(token)
