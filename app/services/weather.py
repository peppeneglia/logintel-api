"""
Open-Meteo weather integration.

Fetches hourly weather forecasts for sampled route points and classifies
conditions into WeatherCondition objects for the heuristics engine.

Uses a single batch request (Open-Meteo accepts latitude/longitude as
comma-separated arrays) to fetch all points at once, then caches each
coordinate+hour slice individually for cross-route reuse.

All hourly slots are requested and matched in UTC, regardless of the
timezone of the departure time sent by the client.

Open-Meteo is free with no API key and no strict rate limit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.engine.heuristics import classify_weather
from app.models.schemas import Coordinate, WeatherCondition, WeatherType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

# Only the variables actually used by the prediction engine
OPEN_METEO_HOURLY_PARAMS = "precipitation,snowfall,wind_speed_10m,visibility"

_HOURLY_FIELDS_DEFAULTS: dict[str, float] = {
    "precipitation": 0.0,
    "snowfall": 0.0,
    "wind_speed_10m": 0.0,
    "visibility": 10000.0,
}


def _to_utc(dt: datetime) -> datetime:
    """Convert to UTC; naive datetimes are assumed to already be in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _hour_key(dt: datetime) -> str:
    """Open-Meteo hourly slot for *dt*, e.g. "2026-02-15T08:00" (UTC)."""
    return _to_utc(dt).strftime("%Y-%m-%dT%H:00")


def _find_hour_index(hours: list[str], target: datetime) -> int | None:
    """Return the index of the hourly slot containing *target*, or None."""
    try:
        return hours.index(_hour_key(target))
    except ValueError:
        return None


def _classify_point_weather(
    precipitation: float,
    snowfall: float,
    wind_speed: float,
    visibility: float,
) -> list[WeatherCondition]:
    """Classify raw weather values into a list of active WeatherConditions."""
    readings = (
        (WeatherType.RAIN, precipitation),
        (WeatherType.SNOW, snowfall),
        (WeatherType.WIND, wind_speed),
        (WeatherType.FOG, visibility),
    )
    conditions = (classify_weather(weather_type, value) for weather_type, value in readings)
    return [c for c in conditions if c is not None]


def _weather_cache_key(lat: float, lon: float, hour_str: str) -> str:
    """Build a cache key from coordinates rounded to a 0.1° (~11 km) grid + hour.

    Key format: meteo:{lat_rounded}:{lon_rounded}:{hour}
    This allows cross-route reuse when different routes pass through the
    same geographic area in the same hourly window.
    """
    return f"meteo:{round(lat, 1)}:{round(lon, 1)}:{hour_str}"


def _hourly_value(hourly: dict[str, Any], field: str, idx: int) -> float:
    values = hourly.get(field) or []
    value = values[idx] if idx < len(values) else None
    return _HOURLY_FIELDS_DEFAULTS[field] if value is None else float(value)


def _extract_conditions_at_hour(hourly: dict[str, Any], arrival: datetime) -> list[WeatherCondition] | None:
    """Classify the weather at *arrival*; None when the hour is not in the data."""
    idx = _find_hour_index(hourly.get("time", []), arrival)
    if idx is None:
        logger.debug("No matching hour for %s in weather data", arrival.isoformat())
        return None

    return _classify_point_weather(
        precipitation=_hourly_value(hourly, "precipitation", idx),
        snowfall=_hourly_value(hourly, "snowfall", idx),
        wind_speed=_hourly_value(hourly, "wind_speed_10m", idx),
        visibility=_hourly_value(hourly, "visibility", idx),
    )


def _hourly_slice_for_hour(hourly: dict[str, Any], arrival: datetime) -> dict[str, Any] | None:
    """Extract a single-hour slice from a full hourly response for caching."""
    times = hourly.get("time", [])
    idx = _find_hour_index(times, arrival)
    if idx is None:
        return None
    return {
        "time": [times[idx]],
        **{field: [_hourly_value(hourly, field, idx)] for field in _HOURLY_FIELDS_DEFAULTS},
    }


async def get_weather_at_points(
    points: list[Coordinate],
    arrival_times: list[datetime],
    base_url: str = "https://api.open-meteo.com",
) -> list[list[WeatherCondition] | None]:
    """
    Fetch weather for each point at its estimated arrival time.

    Strategy:
      1. Check the per-coordinate+hour cache for each point.
      2. Batch-fetch all uncached points in a single Open-Meteo call.
      3. Cache each coordinate+hour slice individually.

    Returns one entry per point: the list of active conditions (empty list
    means clear weather) or None when no forecast data could be obtained.
    """
    settings = get_settings()
    results: list[list[WeatherCondition] | None] = [None] * len(points)

    # --- Step 1: Check cache per point ---
    uncached_indices: list[int] = []
    for i, (point, arrival) in enumerate(zip(points, arrival_times, strict=True)):
        cached = await cache_get(_weather_cache_key(point.lat, point.lon, _hour_key(arrival)))
        if cached is not None:
            results[i] = _extract_conditions_at_hour(cached, arrival)
        else:
            uncached_indices.append(i)

    if not uncached_indices:
        return results

    # --- Step 2: Batch fetch uncached points ---
    uncached_arrivals = [_to_utc(arrival_times[i]) for i in uncached_indices]
    params = {
        "latitude": ",".join(str(points[i].lat) for i in uncached_indices),
        "longitude": ",".join(str(points[i].lon) for i in uncached_indices),
        "hourly": OPEN_METEO_HOURLY_PARAMS,
        "start_date": min(uncached_arrivals).strftime("%Y-%m-%d"),
        "end_date": max(uncached_arrivals).strftime("%Y-%m-%d"),
        "timezone": "UTC",
    }

    try:
        response = await request_with_retry(
            "GET",
            f"{base_url}/v1/forecast",
            params=params,
            retries=2,
            backoff=0.3,
            service_name="open_meteo",
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning(
            "Batch weather fetch failed for %d points — no weather data",
            len(uncached_indices),
            exc_info=True,
        )
        return results

    # Open-Meteo returns a list for multiple locations, a single object for one.
    location_data = data if isinstance(data, list) else [data]

    # --- Step 3: Process results and cache per coordinate+hour ---
    for idx, location in zip(uncached_indices, location_data, strict=False):
        arrival = arrival_times[idx]
        point = points[idx]
        hourly = location.get("hourly", {})

        results[idx] = _extract_conditions_at_hour(hourly, arrival)

        hour_slice = _hourly_slice_for_hour(hourly, arrival)
        if hour_slice is not None:
            await cache_set(
                _weather_cache_key(point.lat, point.lon, _hour_key(arrival)),
                hour_slice,
                ttl=settings.cache_ttl_weather,
            )

    return results
