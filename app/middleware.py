"""
ASGI middleware for request tracing and timing.

RequestIdMiddleware: assigns or propagates X-Request-ID.
TimingMiddleware: measures request duration and feeds MetricsCollector.
"""

from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.alerting import alert_manager
from app.logging_config import request_id_var
from app.metrics import metrics_collector

# Accept only short, log-safe client-provided request ids.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate or propagate X-Request-ID and set the ContextVar."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_id = request.headers.get("X-Request-ID", "")
        rid = client_id if _VALID_REQUEST_ID.match(client_id) else str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)


class TimingMiddleware(BaseHTTPMiddleware):
    """Measure request duration and record it in the metrics collector."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        status_code = 500  # unhandled exceptions propagate past this middleware
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            metrics_collector.record_request(
                path=request.url.path,
                status_code=status_code,
                duration_ms=(time.monotonic() - start) * 1000.0,
            )
            alert_manager.maybe_check()
