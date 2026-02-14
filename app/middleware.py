"""
ASGI middleware for request tracing and timing — FRD Section 9.1.

RequestIdMiddleware: assigns or propagates X-Request-ID.
TimingMiddleware: measures request duration and feeds MetricsCollector.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import request_id_var
from app.metrics import metrics_collector


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate or propagate X-Request-ID and set the ContextVar."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)


class TimingMiddleware(BaseHTTPMiddleware):
    """Measure request duration and record in metrics collector."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000.0
        metrics_collector.record_request(
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response
