"""
Open-Meteo weather integration — FRD Section 4.3.

Fetches hourly weather forecasts for sampled route points and classifies
conditions into WeatherCondition objects for the heuristics engine.

Uses a single batch request (Open-Meteo accepts latitude/longitude as
comma-separated arrays) to fetch all points at once, then caches each
coordinate+hour slice individually for cross-route reuse.

Open-Meteo is free with no API key and no strict rate limit.
"""

from __future__ import annotations

import logging
from datetime import datetime
from itertools import groupby

from app.config import get_settings
from app.engine.heuristics import classify_weather
from app.models.schemas import Coordinate, WeatherCondition, WeatherType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

# Only the variables actually used by the Prediction Engine
OPEN_METEO_HOURLY_PARAMS = (
    "precipitation,snowfall,wind_speed_10m,visibility,temperature_2m"
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


def _weather_cache_key(lat: float, lon: float, hour_str: str) -> str:
    """Build a cache key from coordinates rounded to 0.1° (~11km) grid + hour.

    Key format: meteo:{lat_rounded}:{lon_rounded}:{hour}
    This allows cross-route reuse when different routes pass through the
    same geographic area in the same hourly window.
    """
    lat_r = round(lat, 1)
    lon_r = round(lon, 1)
    return f"meteo:{lat_r}:{lon_r}:{hour_str}"


def _extract_conditions_at_hour(
    hourly: dict, arrival: datetime
) -> list[WeatherCondition]:
    """Extract and classify weather for a specific hour from hourly data."""
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


def _hourly_slice_for_hour(hourly: dict, arrival: datetime) -> dict | None:
    """Extract a single-hour slice from a full hourly response for caching."""
    times = hourly.get("time", [])
    idx = _find_hour_index(times, arrival)
    if idx is None:
        return None
    return {
        "time": [times[idx]],
        "precipitation": [(hourly.get("precipitation") or [0.0])[idx]],
        "snowfall": [(hourly.get("snowfall") or [0.0])[idx]],
        "wind_speed_10m": [(hourly.get("wind_speed_10m") or [0.0])[idx]],
        "visibility": [(hourly.get("visibility") or [10000.0])[idx]],
    }


async def get_weather_at_points(
    points: list[Coordinate],
    arrival_times: list[datetime],
    base_url: str = "https://api.open-meteo.com",
) -> list[list[WeatherCondition]]:
    """
    Fetch weather for each point at its estimated arrival time.

    Strategy:
      1. Check per-coordinate+hour cache for each point.
      2. Collect uncached points and batch-fetch them in a single
         Open-Meteo API call (latitude/longitude as comma-separated arrays).
      3. Cache each coordinate+hour slice individually (TTL 1h).

    Returns a list of WeatherCondition lists — one per point.
    On failure, returns empty conditions (clear sky) for uncached points.
    """
    settings = get_settings()
    n = len(points)
    results: list[list[WeatherCondition] | None] = [None] * n

    # --- Step 1: Check cache per point ---
    uncached_indices: list[int] = []
    for i, (point, arrival) in enumerate(zip(points, arrival_times)):
        hour_str = arrival.strftime("%Y-%m-%dT%H:00")
        cache_key = _weather_cache_key(point.lat, point.lon, hour_str)
        cached = await cache_get(cache_key)
        if cached is not None:
            logger.debug("Weather cache HIT (key=%s)", cache_key)
            results[i] = _extract_conditions_at_hour(cached, arrival)
        else:
            uncached_indices.append(i)

    if not uncached_indices:
        return [r if r is not None else [] for r in results]

    # --- Step 2: Batch fetch uncached points ---
    try:
        uncached_points = [points[i] for i in uncached_indices]
        uncached_arrivals = [arrival_times[i] for i in uncached_indices]

        # Determine date range for the batch request
        dates = sorted({a.strftime("%Y-%m-%d") for a in uncached_arrivals})
        start_date = dates[0]
        end_date = dates[-1]

        # Build batch request with comma-separated coordinate arrays
        lats = ",".join(f"{p.lat}" for p in uncached_points)
        lons = ",".join(f"{p.lon}" for p in uncached_points)

        url = (
            f"{base_url}/v1/forecast"
            f"?latitude={lats}"
            f"&longitude={lons}"
            f"&hourly={OPEN_METEO_HOURLY_PARAMS}"
            f"&start_date={start_date}"
            f"&end_date={end_date}"
            f"&timezone=UTC"
        )

        response = await request_with_retry(
            "GET", url, retries=2, backoff=0.3, service_name="open_meteo",
        )
        response.raise_for_status()
        data = response.json()

        # Open-Meteo returns a list for multi-location, single dict for one location
        if isinstance(data, list):
            location_data = data
        else:
            location_data = [data]

        # --- Step 3: Process results and cache per coordinate+hour ---
        for j, loc in enumerate(location_data):
            if j >= len(uncached_indices):
                break
            idx = uncached_indices[j]
            arrival = arrival_times[idx]
            point = points[idx]
            hourly = loc.get("hourly", {})

            conditions = _extract_conditions_at_hour(hourly, arrival)
            results[idx] = conditions

            # Cache the single-hour slice for this coordinate
            hour_str = arrival.strftime("%Y-%m-%dT%H:00")
            cache_key = _weather_cache_key(point.lat, point.lon, hour_str)
            hour_slice = _hourly_slice_for_hour(hourly, arrival)
            if hour_slice is not None:
                await cache_set(cache_key, hour_slice, ttl=settings.cache_ttl_weather)

    except Exception:
        logger.warning(
            "Batch weather fetch failed for %d points — using clear sky",
            len(uncached_indices),
            exc_info=True,
        )
        for idx in uncached_indices:
            if results[idx] is None:
                results[idx] = []

    return [r if r is not None else [] for r in results]
