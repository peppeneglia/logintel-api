"""
In-memory sliding-window rate limiter.

Uses a defaultdict of deques to track request timestamps per org.
State resets on restart — acceptable for MVP single-instance deployment.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.auth import OrgContext
from app.errors import RateLimitError

WINDOW_SECONDS = 3600  # 1 hour


class RateLimiter:
    """Sliding-window rate limiter keyed by org_id."""

    def __init__(self) -> None:
        self._windows: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, org: OrgContext) -> None:
        """
        Record a request and raise RateLimitError if the org exceeds its hourly limit.
        """
        now = time.monotonic()
        window = self._windows[org.org_id]

        # Prune entries older than the window
        cutoff = now - WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= org.rate_limit_hour:
            retry_after = int(window[0] - cutoff) + 1
            raise RateLimitError(
                message=f"Rate limit exceeded: {org.rate_limit_hour} requests per hour",
                retry_after=retry_after,
            )

        window.append(now)


# Module-level singleton
rate_limiter = RateLimiter()
