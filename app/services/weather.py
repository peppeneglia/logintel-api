"""
Open-Meteo weather integration — FRD Section 4.3.

Fetches hourly weather forecasts for sampled route points and classifies
conditions into WeatherCondition objects for the heuristics engine.

Open-Meteo is free with no API key and no strict rate limit.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.config import get_settings
from app.engine.heuristics import classify_weather
from app.models.schemas import Coordinate, WeatherCondition, WeatherType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

OPEN_METEO_HOURLY_PARAMS = (
    "precipitation,snowfall,wind_speed_10m,visibility,weather_code"
)


def _find_hour_index(hours: list[str], target: datetime) -> int | None:
    """
    Find the index of the hourly slot matching target time.

    Open-Meteo returns ISO timestamps like "2026-02-15T08:00".
    We match by truncating the target to the hour.
    """
    target_str = target.strftime("%Y-%m-%dT%H:00")
    try:
        return hours.index(target_str)
    except ValueError:
        return None


def _classify_point_weather(
    precipitation: float,
    snowfall: float,
    wind_speed: float,
    visibility: float,
) -> list[WeatherCondition]:
    """Classify raw weather values into a list of active WeatherConditions."""
    conditions: list[WeatherCondition] = []

    rain_cond = classify_weather(WeatherType.RAIN, precipitation)
    if rain_cond is not None:
        conditions.append(rain_cond)

    snow_cond = classify_weather(WeatherType.SNOW, snowfall)
    if snow_cond is not None:
        conditions.append(snow_cond)

    wind_cond = classify_weather(WeatherType.WIND, wind_speed)
    if wind_cond is not None:
        conditions.append(wind_cond)

    fog_cond = classify_weather(WeatherType.FOG, visibility)
    if fog_cond is not None:
        conditions.append(fog_cond)

    return conditions


async def get_weather_at_points(
    points: list[Coordinate],
    arrival_times: list[datetime],
    base_url: str = "https://api.open-meteo.com",
) -> list[list[WeatherCondition]]:
    """
    Fetch weather for each point at its estimated arrival time.

    Uses Open-Meteo multi-location batch by issuing one request per point
    (Open-Meteo doesn't support true multi-location in a single call,
    but requests are free and fast).

    Returns a list of WeatherCondition lists — one per point.
    On failure for a single point, returns empty conditions for that point.
    """
    results: list[list[WeatherCondition]] = []

    for point, arrival in zip(points, arrival_times):
        try:
            conditions = await _fetch_single_point_weather(
                point, arrival, base_url
            )
            results.append(conditions)
        except Exception:
            logger.warning(
                "Weather fetch failed for (%.2f, %.2f) at %s — using clear sky",
                point.lat, point.lon, arrival.isoformat(),
                exc_info=True,
            )
            results.append([])

    return results


def _weather_cache_key(lat: float, lon: float, date_str: str) -> str:
    """Build a cache key from coordinates rounded to 0.1° (~11km) grid + date."""
    lat_r = round(lat, 1)
    lon_r = round(lon, 1)
    return f"weather:{lat_r}:{lon_r}:{date_str}"


def _extract_hour_from_daily(
    hourly: dict, arrival: datetime
) -> list[WeatherCondition]:
    """Extract and classify weather for a specific hour from a full daily response."""
    times = hourly.get("time", [])
    idx = _find_hour_index(times, arrival)

    if idx is None:
        logger.debug(
            "No matching hour for %s in weather data", arrival.isoformat()
        )
        return []

    precipitation = (hourly.get("precipitation") or [0.0])[idx]
    snowfall = (hourly.get("snowfall") or [0.0])[idx]
    wind_speed = (hourly.get("wind_speed_10m") or [0.0])[idx]
    visibility = (hourly.get("visibility") or [10000.0])[idx]

    return _classify_point_weather(
        precipitation=precipitation or 0.0,
        snowfall=snowfall or 0.0,
        wind_speed=wind_speed or 0.0,
        visibility=visibility or 10000.0,
    )


async def _fetch_single_point_weather(
    point: Coordinate,
    arrival: datetime,
    base_url: str,
) -> list[WeatherCondition]:
    """Fetch and classify weather for one point at one time.

    Caches the full daily response (all 24 hours) keyed on rounded
    coordinates + date.  On a cache hit the specific hour is extracted
    without calling Open-Meteo again.
    """
    settings = get_settings()
    date_str = arrival.strftime("%Y-%m-%d")
    cache_key = _weather_cache_key(point.lat, point.lon, date_str)

    # --- Cache check ---
    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Weather cache HIT (key=%s)", cache_key)
        return _extract_hour_from_daily(cached, arrival)

    # --- API call ---
    url = (
        f"{base_url}/v1/forecast"
        f"?latitude={point.lat}"
        f"&longitude={point.lon}"
        f"&hourly={OPEN_METEO_HOURLY_PARAMS}"
        f"&start_date={date_str}"
        f"&end_date={date_str}"
        f"&timezone=auto"
    )

    response = await request_with_retry(
        "GET", url, retries=2, backoff=0.3, service_name="open_meteo",
    )
    response.raise_for_status()
    data = response.json()

    hourly = data.get("hourly", {})

    # --- Cache store (entire daily response) ---
    await cache_set(cache_key, hourly, ttl=settings.cache_ttl_weather)

    return _extract_hour_from_daily(hourly, arrival)
