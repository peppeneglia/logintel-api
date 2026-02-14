"""
Health check endpoint — FRD Section 9.2.

Returns service status, dependency health, and operational metrics.
"""

import logging

from fastapi import APIRouter

from app.alerting import alert_manager
from app.circuit_breaker import CircuitBreakerState, service_breakers
from app.config import get_settings
from app.metrics import metrics_collector
from app.services.cache import get_redis

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


async def _check_redis() -> dict:
    """Ping Redis and return status."""
    client = get_redis()
    if client is None:
        return {"status": "unhealthy", "reason": "not configured"}
    try:
        await client.ping()
        return {"status": "healthy"}
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return {"status": "unhealthy", "reason": str(exc)}


@router.get("/v1/health")
async def health_check():
    settings = get_settings()

    redis_status = await _check_redis()

    # Circuit breaker statuses
    cb_statuses = service_breakers.all_statuses()
    any_cb_open = any(
        s["state"] == CircuitBreakerState.OPEN.value for s in cb_statuses.values()
    )

    overall = (
        "degraded"
        if redis_status["status"] != "healthy" or any_cb_open
        else "healthy"
    )

    snap = metrics_collector.snapshot()

    # Active alerts
    alerts = alert_manager.check_alerts()

    return {
        "status": overall,
        "version": "0.1.0",
        "environment": settings.app_env,
        "dependencies": {
            "redis": redis_status,
            **{f"cb:{name}": st for name, st in cb_statuses.items()},
        },
        "metrics": {
            "uptime_seconds": snap.uptime_seconds,
            "total_requests": snap.total_requests,
            "error_rate_pct": snap.error_rate_pct,
            "latency_p95_ms": snap.latency_p95_ms,
            "cache_hit_rate_pct": snap.cache_hit_rate_pct,
        },
        "alerts": [
            {
                "level": a.level.value,
                "rule": a.rule,
                "message": a.message,
            }
            for a in alerts
        ],
    }
