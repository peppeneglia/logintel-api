"""
Open-Elevation integration.

Fetches elevation data for sampled route points via batch POST.
Falls back to 200m default on failure (graceful degradation).
"""

from __future__ import annotations

import logging

from app.models.schemas import Coordinate
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

DEFAULT_ELEVATION_M = 200.0


async def get_elevations(
    points: list[Coordinate],
    base_url: str = "https://api.open-elevation.com",
) -> list[float]:
    """
    Fetch elevations for all points in a single batch POST request.

    Returns a list of elevation values (meters) matching the input points.
    On failure, returns default elevation (200m) for all points.
    """
    if not points:
        return []

    try:
        return await _fetch_elevations_batch(points, base_url)
    except Exception:
        logger.warning(
            "Elevation API failed for %d points — using default %.0fm",
            len(points),
            DEFAULT_ELEVATION_M,
            exc_info=True,
        )
        return [DEFAULT_ELEVATION_M] * len(points)


async def _fetch_elevations_batch(
    points: list[Coordinate],
    base_url: str,
) -> list[float]:
    """Execute the batch elevation lookup."""
    url = f"{base_url}/api/v1/lookup"
    body = {"locations": [{"latitude": p.lat, "longitude": p.lon} for p in points]}

    response = await request_with_retry(
        "POST",
        url,
        json=body,
        retries=2,
        backoff=0.3,
        service_name="open_elevation",
    )
    response.raise_for_status()
    data = response.json()

    results = data.get("results", [])
    elevations = [
        float(r["elevation"]) if r.get("elevation") is not None else DEFAULT_ELEVATION_M
        for r in results[: len(points)]
    ]
    # Pad with the default if the API returned fewer results than requested
    elevations.extend([DEFAULT_ELEVATION_M] * (len(points) - len(elevations)))
    return elevations
