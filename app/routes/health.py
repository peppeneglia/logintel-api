"""
Health check endpoint.

Returns service status, dependency health, and operational metrics.
The endpoint is public, so failure details are logged but never returned.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app import __version__
from app.alerting import alert_manager
from app.circuit_breaker import CircuitBreakerState, service_breakers
from app.config import get_settings
from app.metrics import metrics_collector
from app.services import supabase
from app.services.cache import get_redis

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


async def _check_redis() -> dict[str, str]:
    """Ping Redis and return its status."""
    client = get_redis()
    if client is None:
        return {"status": "degraded", "reason": "not configured"}
    try:
        await client.ping()
        return {"status": "healthy"}
    except Exception:
        logger.warning("Redis health check failed", exc_info=True)
        return {"status": "unhealthy", "reason": "unreachable"}


async def _check_supabase() -> dict[str, str]:
    """Check Supabase PostgREST connectivity."""
    if not supabase.is_configured():
        return {"status": "unhealthy", "reason": "not configured"}
    try:
        await supabase.select("predictions", params={"select": "id", "limit": "1"})
        return {"status": "healthy"}
    except Exception:
        logger.warning("Supabase health check failed", exc_info=True)
        return {"status": "unhealthy", "reason": "unreachable"}


@router.get("/v1/health", summary="Service health check")
async def health_check() -> dict[str, Any]:
    """Return service health status, dependency states, and operational metrics.

    **No authentication required.**

    Response fields:
    - **status** — `healthy` or `degraded` (degraded if Redis or Supabase is unhealthy or any circuit breaker is open).
    - **dependencies** — health of Redis, Supabase and circuit breaker state for each upstream service.
    - **metrics** — uptime, total requests, error rate, p95 latency, cache hit rate.
    - **alerts** — list of active alerts (critical/warning) triggered by operational thresholds.
    """
    settings = get_settings()

    redis_status = await _check_redis()
    supabase_status = await _check_supabase()

    cb_statuses = service_breakers.all_statuses()
    any_cb_open = any(s["state"] == CircuitBreakerState.OPEN.value for s in cb_statuses.values())

    degraded = redis_status["status"] == "unhealthy" or supabase_status["status"] != "healthy" or any_cb_open

    snap = metrics_collector.snapshot()
    alerts = alert_manager.check_alerts()

    return {
        "status": "degraded" if degraded else "healthy",
        "version": __version__,
        "environment": settings.app_env,
        "dependencies": {
            "redis": redis_status,
            "supabase": supabase_status,
            **{f"cb:{name}": st for name, st in cb_statuses.items()},
        },
        "metrics": {
            "uptime_seconds": snap.uptime_seconds,
            "total_requests": snap.total_requests,
            "error_rate_pct": snap.error_rate_pct,
            "latency_p95_ms": snap.latency_p95_ms,
            "cache_hit_rate_pct": snap.cache_hit_rate_pct,
        },
        "alerts": [{"level": a.level.value, "rule": a.rule, "message": a.message} for a in alerts],
    }
