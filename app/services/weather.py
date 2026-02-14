"""
Open-Meteo weather integration — FRD Section 4.3.

Fetches hourly weather forecasts for sampled route points and classifies
conditions into WeatherCondition objects for the heuristics engine.

Open-Meteo is free with no API key and no strict rate limit.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.engine.heuristics import classify_weather
from app.models.schemas import Coordinate, WeatherCondition, WeatherType
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
                "Weather fetch failed for (%.4f, %.4f) at %s — using clear sky",
                point.lat, point.lon, arrival.isoformat(),
                exc_info=True,
            )
            results.append([])

    return results


async def _fetch_single_point_weather(
    point: Coordinate,
    arrival: datetime,
    base_url: str,
) -> list[WeatherCondition]:
    """Fetch and classify weather for one point at one time."""
    date_str = arrival.strftime("%Y-%m-%d")
    url = (
        f"{base_url}/v1/forecast"
        f"?latitude={point.lat}"
        f"&longitude={point.lon}"
        f"&hourly={OPEN_METEO_HOURLY_PARAMS}"
        f"&start_date={date_str}"
        f"&end_date={date_str}"
        f"&timezone=auto"
    )

    response = await request_with_retry("GET", url, retries=2, backoff=0.3)
    response.raise_for_status()
    data = response.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    idx = _find_hour_index(times, arrival)

    if idx is None:
        logger.debug(
            "No matching hour for %s in Open-Meteo response", arrival.isoformat()
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
