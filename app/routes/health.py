"""
Health check endpoint — FRD Section 9.2.

Returns service status, dependency health, and operational metrics.
"""

import logging
import traceback

from fastapi import APIRouter

from app.alerting import alert_manager
from app.circuit_breaker import CircuitBreakerState, service_breakers
from app.config import get_settings
from app.metrics import metrics_collector
from app.models.schemas import Coordinate
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


@router.get("/v1/health", summary="Service health check")
async def health_check():
    """Return service health status, dependency states, and operational metrics.

    **No authentication required.**

    Response fields:
    - **status** — `healthy` or `degraded` (degraded if Redis is down or any circuit breaker is open).
    - **dependencies** — health of Redis and circuit breaker state for each upstream service.
    - **metrics** — uptime, total requests, error rate, p95 latency, cache hit rate.
    - **alerts** — list of active alerts (critical/warning) triggered by operational thresholds.
    """
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


@router.get("/v1/debug/pipeline")
async def debug_pipeline():
    """Temporary diagnostic endpoint — tests each pipeline step independently."""
    from datetime import datetime, timedelta, timezone

    results = {}
    settings = get_settings()

    # Config check
    results["config"] = {
        "ors_api_key_set": bool(settings.ors_api_key),
        "ors_api_key_length": len(settings.ors_api_key),
        "ors_base_url": settings.ors_base_url,
        "open_meteo_base_url": settings.open_meteo_base_url,
        "open_elevation_base_url": settings.open_elevation_base_url,
    }

    origin = Coordinate(lat=45.4642, lon=9.19)
    dest = Coordinate(lat=41.9028, lon=12.4964)
    departure = datetime.now(timezone.utc) + timedelta(hours=6)

    # Step 1: ORS
    try:
        from app.services.ors import get_route
        route = await get_route(origin, dest)
        results["ors"] = {
            "status": "ok",
            "polyline_points": len(route.polyline),
            "duration_s": route.duration_s,
            "distance_m": route.distance_m,
            "road_types": len(route.road_types),
        }
    except Exception as e:
        results["ors"] = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}
        return results

    # Step 2: Sampler
    try:
        from app.engine.sampler import sample_points_from_polyline, estimate_arrival_times
        sample_points = sample_points_from_polyline(route.polyline, interval_km=50.0)
        arrival_times = estimate_arrival_times(sample_points, departure, total_duration_seconds=route.duration_s)
        results["sampler"] = {"status": "ok", "points": len(sample_points)}
    except Exception as e:
        results["sampler"] = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}
        return results

    # Step 3: Elevation
    try:
        from app.services.elevation import get_elevations
        elevations = await get_elevations(sample_points, base_url=settings.open_elevation_base_url)
        results["elevation"] = {"status": "ok", "count": len(elevations), "sample": elevations[:3]}
    except Exception as e:
        results["elevation"] = {"status": "error (fallback ok)", "error": str(e)}

    # Step 4: Special elements
    try:
        from app.services.overpass import get_special_elements
        elements = await get_special_elements(route.polyline)
        results["overpass"] = {"status": "ok", "elements": len(elements)}
    except Exception as e:
        results["overpass"] = {"status": "error (fallback ok)", "error": str(e)}

    # Step 5: Weather
    try:
        from app.services.weather import get_weather_at_points
        weather = await get_weather_at_points(sample_points, arrival_times, base_url=settings.open_meteo_base_url)
        results["weather"] = {
            "status": "ok",
            "points_with_weather": sum(1 for w in weather if w),
            "total_points": len(weather),
        }
    except Exception as e:
        results["weather"] = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # Step 6: Full pipeline
    try:
        from app.services.prediction import build_prediction
        pred = await build_prediction(origin, dest, departure)
        results["full_pipeline"] = {
            "status": "ok",
            "total_delay_minutes": pred.total_delay_minutes,
            "segments": len(pred.segments),
            "confidence": pred.confidence.overall,
        }
    except Exception as e:
        results["full_pipeline"] = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    return results
