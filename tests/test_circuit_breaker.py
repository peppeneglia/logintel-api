"""
Tests for the circuit breaker.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest
import respx

from app.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    ServiceBreakers,
)
from app.errors import ServiceUnavailableError
from app.services.http_client import init_client, request_with_retry

# ---------------------------------------------------------------------------
# CircuitBreaker unit tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        assert cb.state is CircuitBreakerState.CLOSED

    def test_allow_request_when_closed(self):
        cb = CircuitBreaker("test")
        assert cb.allow_request() is True

    def test_record_success_resets_counter(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.status()["consecutive_failures"] == 0

    def test_transition_closed_to_open_after_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state is CircuitBreakerState.OPEN

    def test_allow_request_false_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=999)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_transition_open_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state is CircuitBreakerState.OPEN
        # Force _state_changed_at far enough in the past
        cb._state_changed_at = time.monotonic() - 2
        assert cb.allow_request() is True
        assert cb.state is CircuitBreakerState.HALF_OPEN

    def test_transition_half_open_to_closed_on_success(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0)
        cb.record_failure()
        cb.record_failure()
        # Timeout = 0 so allow_request transitions immediately
        cb.allow_request()
        assert cb.state is CircuitBreakerState.HALF_OPEN
        cb.record_success()
        assert cb.state is CircuitBreakerState.CLOSED

    def test_transition_half_open_to_open_on_failure(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0)
        cb.record_failure()
        cb.record_failure()
        cb.allow_request()  # → HALF_OPEN
        assert cb.state is CircuitBreakerState.HALF_OPEN
        cb.record_failure()
        assert cb.state is CircuitBreakerState.OPEN

    def test_status_dict_contains_all_fields(self):
        cb = CircuitBreaker("myservice", failure_threshold=5, recovery_timeout=30)
        s = cb.status()
        assert s["name"] == "myservice"
        assert s["state"] == "CLOSED"
        assert s["consecutive_failures"] == 0
        assert s["last_failure_time"] is None
        assert "time_in_state_seconds" in s


# ---------------------------------------------------------------------------
# ServiceBreakers registry tests
# ---------------------------------------------------------------------------


class TestServiceBreakers:
    def test_lazy_creation(self):
        sb = ServiceBreakers()
        with patch("app.circuit_breaker.get_settings") as mock_settings:
            mock_settings.return_value.cb_failure_threshold = 5
            mock_settings.return_value.cb_recovery_timeout = 30
            cb = sb.get("ors")
        assert cb.name == "ors"
        assert cb.failure_threshold == 5

    def test_same_breaker_for_same_name(self):
        sb = ServiceBreakers()
        with patch("app.circuit_breaker.get_settings") as mock_settings:
            mock_settings.return_value.cb_failure_threshold = 5
            mock_settings.return_value.cb_recovery_timeout = 30
            cb1 = sb.get("ors")
            cb2 = sb.get("ors")
        assert cb1 is cb2

    def test_all_statuses(self):
        sb = ServiceBreakers()
        with patch("app.circuit_breaker.get_settings") as mock_settings:
            mock_settings.return_value.cb_failure_threshold = 5
            mock_settings.return_value.cb_recovery_timeout = 30
            sb.get("ors")
            sb.get("open_meteo")
        statuses = sb.all_statuses()
        assert "ors" in statuses
        assert "open_meteo" in statuses
        assert statuses["ors"]["state"] == "CLOSED"


# ---------------------------------------------------------------------------
# Integration with http_client
# ---------------------------------------------------------------------------


class TestHttpClientWithCircuitBreaker:
    @pytest.fixture(autouse=True)
    def _setup_client(self):
        """Ensure HTTP client is initialised."""
        init_client()

    @respx.mock
    @pytest.mark.asyncio
    async def test_request_passes_when_cb_closed(self):
        respx.get("https://example.com/api").mock(return_value=httpx.Response(200, json={"ok": True}))
        # Fresh breaker for this test
        with patch("app.services.http_client.service_breakers") as mock_sb:
            mock_breaker = CircuitBreaker("test_svc")
            mock_sb.get.return_value = mock_breaker

            response = await request_with_retry(
                "GET",
                "https://example.com/api",
                service_name="test_svc",
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_request_failfast_when_cb_open(self):
        with patch("app.services.http_client.service_breakers") as mock_sb:
            mock_breaker = CircuitBreaker("test_svc", failure_threshold=1)
            mock_breaker.record_failure()  # opens the breaker
            mock_sb.get.return_value = mock_breaker

            with pytest.raises(ServiceUnavailableError, match="circuit breaker is OPEN"):
                await request_with_retry(
                    "GET",
                    "https://example.com/api",
                    service_name="test_svc",
                )

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_cb_without_service_name(self):
        respx.get("https://example.com/api").mock(return_value=httpx.Response(200, json={"ok": True}))
        # No service_name → no CB involvement
        response = await request_with_retry("GET", "https://example.com/api")
        assert response.status_code == 200
