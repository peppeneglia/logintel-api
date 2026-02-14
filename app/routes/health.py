"""
Health check endpoint — FRD Section 9.2.

Returns service status, dependency health, and operational metrics.
"""

import logging

from fastapi import APIRouter

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

    overall = "healthy" if redis_status["status"] == "healthy" else "degraded"

    snap = metrics_collector.snapshot()

    return {
        "status": overall,
        "version": "0.1.0",
        "environment": settings.app_env,
        "dependencies": {
            "redis": redis_status,
        },
        "metrics": {
            "uptime_seconds": snap.uptime_seconds,
            "total_requests": snap.total_requests,
            "error_rate_pct": snap.error_rate_pct,
            "latency_p95_ms": snap.latency_p95_ms,
            "cache_hit_rate_pct": snap.cache_hit_rate_pct,
        },
    }
