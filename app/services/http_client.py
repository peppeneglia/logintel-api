"""
Shared HTTP client with retry and exponential backoff — FRD 4.5.

Provides a singleton httpx.AsyncClient reused by all service modules.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Module-level singleton
_client: httpx.AsyncClient | None = None

# Retry configuration
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5
REQUEST_TIMEOUT = 15.0


def get_client() -> httpx.AsyncClient:
    """Return the shared httpx client. Must be initialised first via init_client()."""
    if _client is None:
        raise RuntimeError("HTTP client not initialised. Call init_client() first.")
    return _client


def init_client(timeout: float = REQUEST_TIMEOUT) -> httpx.AsyncClient:
    """Create the shared httpx client (called at app startup)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    """Gracefully close the shared httpx client (called at app shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = MAX_RETRIES,
    backoff: float = BASE_BACKOFF_SECONDS,
    service_name: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """
    Execute an HTTP request with exponential-backoff retry.

    Retries on 5xx responses and connection/timeout errors.
    Raises the last encountered exception after all retries are exhausted.

    When *service_name* is given the request is gated by the corresponding
    circuit breaker — if the breaker is OPEN the call fails fast with
    ``ServiceUnavailableError``.
    """
    # --- Circuit breaker check ---
    breaker = None
    if service_name is not None:
        from app.circuit_breaker import service_breakers
        from app.errors import ServiceUnavailableError

        breaker = service_breakers.get(service_name)
        if not breaker.allow_request():
            logger.warning(
                "Circuit breaker OPEN for %s — fail-fast", service_name,
            )
            raise ServiceUnavailableError(
                f"Service '{service_name}' circuit breaker is OPEN"
            )

    client = get_client()
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code < 500:
                if breaker is not None:
                    breaker.record_success()
                return response
            # 5xx — worth retrying
            last_exc = httpx.HTTPStatusError(
                f"Server error {response.status_code}",
                request=response.request,
                response=response,
            )
            logger.warning(
                "HTTP %s %s returned %s (attempt %d/%d)",
                method, url, response.status_code, attempt, retries,
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
            last_exc = exc
            logger.warning(
                "HTTP %s %s failed: %s (attempt %d/%d)",
                method, url, exc, attempt, retries,
            )

        if attempt < retries:
            wait = backoff * (2 ** (attempt - 1))
            await asyncio.sleep(wait)

    # All retries exhausted — record failure on breaker
    if breaker is not None:
        breaker.record_failure()
    logger.error("HTTP %s %s failed after %d attempts", method, url, retries)
    raise last_exc  # type: ignore[misc]
