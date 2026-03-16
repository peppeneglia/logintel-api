"""
Overpass API (OpenStreetMap) integration — FRD Section 6.4.

Fetches special route elements (tunnels, bridges, mountain passes, urban centers)
along a route polyline. Results cached for 7 days (infrastructure is static).

Uses an ``around`` corridor filter (5 km buffer around the polyline) instead of
a bounding box, reducing the search area by ~95 % on long routes and bringing
Overpass response times from 25-30 s down to < 1 s.

Graceful degradation: on failure returns empty list (no special element modifiers).
"""

from __future__ import annotations

import hashlib
import logging
import time

from app.config import get_settings
from app.models.schemas import Coordinate, SpecialElement, SpecialElementType
from app.services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# In-memory fallback cache for when Redis is unavailable.
# Key: cache_key, Value: list[dict]  (serialised SpecialElement dicts).
# Safe for a single-process deployment; evicted only on restart.
_mem_cache: dict[str, list[dict]] = {}

# Maximum polyline points sent to Overpass (evenly sampled).
_MAX_POLY_POINTS = 40

# Corridor radius in metres for the ``around`` filter.
_CORRIDOR_RADIUS_M = 5000


def _sample_polyline(polyline: list[Coordinate], max_points: int) -> list[Coordinate]:
    """Evenly sample up to *max_points* from the polyline."""
    n = len(polyline)
    if n <= max_points:
        return polyline
    step = (n - 1) / (max_points - 1)
    return [polyline[round(i * step)] for i in range(max_points)]


def _build_overpass_query(polyline: list[Coordinate]) -> str:
    """Build Overpass QL query using an ``around`` corridor filter.

    Instead of a huge bounding box, we pass the polyline coordinates to the
    ``around`` filter so that only elements within 5 km of the actual route
    are returned.
    """
    sampled = _sample_polyline(polyline, _MAX_POLY_POINTS)
    coords = ",".join(f"{p.lat},{p.lon}" for p in sampled)
    radius = _CORRIDOR_RADIUS_M
    return f"""[out:json][timeout:10];
(
  way["tunnel"="yes"](around:{radius},{coords});
  way["bridge"="yes"](around:{radius},{coords});
  node["mountain_pass"="yes"](around:{radius},{coords});
  node["place"~"city|town"](around:{radius},{coords});
);
out center;"""


def _osm_cache_key(polyline: list[Coordinate]) -> str:
    """Stable cache key derived from sampled polyline coordinates."""
    sampled = _sample_polyline(polyline, _MAX_POLY_POINTS)
    raw = "|".join(f"{p.lat:.2f},{p.lon:.2f}" for p in sampled)
    digest = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"osm:corridor:{digest}"


def _parse_overpass_response(data: dict) -> list[SpecialElement]:
    """Parse Overpass JSON response into SpecialElement list."""
    elements: list[SpecialElement] = []

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        el_type = el.get("type", "")

        # Determine coordinates
        if el_type == "way":
            center = el.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")
        else:  # node
            lat = el.get("lat")
            lon = el.get("lon")

        if lat is None or lon is None:
            continue

        name = tags.get("name")

        # Tunnel
        if tags.get("tunnel") == "yes":
            length_m = None
            if "length" in tags:
                try:
                    length_m = float(tags["length"])
                except (ValueError, TypeError):
                    pass
            # Skip tunnels < 500m (no significant weather shielding)
            if length_m is not None and length_m < 500:
                continue
            elements.append(SpecialElement(
                type=SpecialElementType.TUNNEL,
                name=name,
                length_m=length_m,
                lat=lat,
                lon=lon,
            ))
            continue

        # Bridge / viaduct
        if tags.get("bridge") == "yes":
            elements.append(SpecialElement(
                type=SpecialElementType.BRIDGE,
                name=name,
                lat=lat,
                lon=lon,
            ))
            continue

        # Mountain pass
        if tags.get("mountain_pass") == "yes":
            elements.append(SpecialElement(
                type=SpecialElementType.MOUNTAIN_PASS,
                name=name,
                lat=lat,
                lon=lon,
            ))
            continue

        # Urban center (city or town)
        place = tags.get("place", "")
        if place in ("city", "town"):
            elements.append(SpecialElement(
                type=SpecialElementType.URBAN_CENTER,
                name=name,
                lat=lat,
                lon=lon,
            ))

    return elements


async def get_special_elements(
    polyline: list[Coordinate],
    base_url: str | None = None,
) -> list[SpecialElement]:
    """
    Fetch special route elements from Overpass API.

    Returns empty list on failure (graceful degradation).
    Results cached for 7 days (Redis) + in-memory fallback.
    """
    if not polyline:
        return []

    settings = get_settings()
    if base_url is None:
        base_url = settings.overpass_base_url

    cache_key = _osm_cache_key(polyline)

    # 1. Redis cache check
    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Overpass Redis cache HIT (key=%s)", cache_key)
        return [SpecialElement(**e) for e in cached]

    # 2. In-memory fallback cache check
    if cache_key in _mem_cache:
        logger.debug("Overpass memory cache HIT (key=%s)", cache_key)
        return [SpecialElement(**e) for e in _mem_cache[cache_key]]

    # 3. API call
    from app.services.http_client import request_with_retry

    query = _build_overpass_query(polyline)
    url = f"{base_url.rstrip('/')}/api/interpreter"

    t0 = time.monotonic()
    try:
        response = await request_with_retry(
            "POST", url, data={"data": query}, retries=1, backoff=0.3,
            service_name="overpass",
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("Overpass API failed — skipping special elements", exc_info=True)
        return []

    elapsed = time.monotonic() - t0
    elements = _parse_overpass_response(data)
    logger.info(
        "Overpass: found %d special elements in %.3fs", len(elements), elapsed
    )

    # 4. Cache store — Redis (7 days) + in-memory
    serialized = [e.model_dump() for e in elements]
    await cache_set(cache_key, serialized, ttl=settings.cache_ttl_osm)
    _mem_cache[cache_key] = serialized

    return elements
