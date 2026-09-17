"""Tests for the in-memory sliding-window rate limiter."""

from __future__ import annotations

import time

import pytest

from app.auth import OrgContext
from app.errors import RateLimitError
from app.rate_limit import RateLimiter


def _make_org(org_id: str = "org-1", rate_limit_hour: int = 5) -> OrgContext:
    return OrgContext(
        org_id=org_id,
        org_name="Test",
        tier="starter",
        rate_limit_hour=rate_limit_hour,
        predictions_limit_month=10000,
    )


class TestRateLimiter:
    def test_under_limit_allowed(self):
        limiter = RateLimiter()
        org = _make_org(rate_limit_hour=5)
        for _ in range(5):
            limiter.check(org)  # Should not raise

    def test_at_limit_raises(self):
        limiter = RateLimiter()
        org = _make_org(rate_limit_hour=3)
        for _ in range(3):
            limiter.check(org)

        with pytest.raises(RateLimitError) as exc_info:
            limiter.check(org)
        assert "Rate limit exceeded" in str(exc_info.value)
        assert exc_info.value.retry_after > 0

    def test_window_expiry_allows_new_requests(self):
        limiter = RateLimiter()
        org = _make_org(rate_limit_hour=2)

        # Fill up the window
        limiter.check(org)
        limiter.check(org)

        # Simulate time passing beyond the window (1 hour)
        window = limiter._windows[org.org_id]
        # Shift all timestamps back by 3601 seconds
        now = time.monotonic()
        for i in range(len(window)):
            window[i] = now - 3601

        # Should be allowed now (old entries pruned)
        limiter.check(org)

    def test_different_orgs_independent(self):
        limiter = RateLimiter()
        org_a = _make_org(org_id="org-a", rate_limit_hour=2)
        org_b = _make_org(org_id="org-b", rate_limit_hour=2)

        # Fill org_a's limit
        limiter.check(org_a)
        limiter.check(org_a)

        # org_a should be rate limited
        with pytest.raises(RateLimitError):
            limiter.check(org_a)

        # org_b should still be allowed
        limiter.check(org_b)

    def test_retry_after_header_value(self):
        limiter = RateLimiter()
        org = _make_org(rate_limit_hour=1)
        limiter.check(org)

        with pytest.raises(RateLimitError) as exc_info:
            limiter.check(org)
        assert exc_info.value.retry_after >= 1
