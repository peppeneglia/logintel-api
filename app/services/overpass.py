"""
Overpass API (OpenStreetMap) integration.

Fetches special route elements (tunnels, bridges, mountain passes, urban centers)
along a route polyline. Results are cached for 7 days (infrastructure is static).

Uses an ``around`` corridor filter (5 km buffer around the polyline) instead of
a bounding box, which keeps the searched area — and the response time — small
even on long routes.

Graceful degradation: on failure returns an empty list (no special element modifiers).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any

from app.config import get_settings
from app.engine.sampler import haversine_distance
from app.engine.special_elements import MIN_TUNNEL_LENGTH_M
from app.models.schemas import Coordinate, SpecialElement, SpecialElementType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

# Maximum polyline points sent to Overpass (evenly sampled).
_MAX_POLY_POINTS = 40

# Corridor radius in metres for the ``around`` filter.
_CORRIDOR_RADIUS_M = 5000

# Bounded in-process fallback cache, used when Redis is unavailable.
_MEM_CACHE_MAX_ENTRIES = 256
_mem_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()


def _mem_cache_get(key: str) -> list[dict[str, Any]] | None:
    value = _mem_cache.get(key)
    if value is not None:
        _mem_cache.move_to_end(key)
    return value


def _mem_cache_set(key: str, value: list[dict[str, Any]]) -> None:
    _mem_cache[key] = value
    _mem_cache.move_to_end(key)
    while len(_mem_cache) > _MEM_CACHE_MAX_ENTRIES:
        _mem_cache.popitem(last=False)


def _sample_polyline(polyline: list[Coordinate], max_points: int) -> list[Coordinate]:
    """Evenly sample up to *max_points* from the polyline."""
    n = len(polyline)
    if n <= max_points:
        return polyline
    step = (n - 1) / (max_points - 1)
    return [polyline[round(i * step)] for i in range(max_points)]


def _build_overpass_query(polyline: list[Coordinate]) -> str:
    """Build an Overpass QL query restricted to a corridor around the polyline.

    ``out bb`` returns the bounding box of ways, used to locate them and to
    estimate tunnel length when the ``length`` tag is missing.
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
out bb;"""


def _osm_cache_key(polyline: list[Coordinate]) -> str:
    """Stable cache key derived from sampled polyline coordinates."""
    sampled = _sample_polyline(polyline, _MAX_POLY_POINTS)
    raw = "|".join(f"{p.lat:.2f},{p.lon:.2f}" for p in sampled)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"osm:corridor:{digest}"


def _element_position(el: dict[str, Any]) -> tuple[float, float] | None:
    """Return (lat, lon) for a node, or the center of a way (``center`` or ``bounds``)."""
    if el.get("type") != "way":
        lat, lon = el.get("lat"), el.get("lon")
        return (lat, lon) if lat is not None and lon is not None else None

    center = el.get("center")
    if center:
        return center["lat"], center["lon"]
    bounds = el.get("bounds")
    if bounds:
        return (
            (bounds["minlat"] + bounds["maxlat"]) / 2,
            (bounds["minlon"] + bounds["maxlon"]) / 2,
        )
    return None


def _tunnel_length_m(el: dict[str, Any]) -> float | None:
    """Tunnel length from the ``length`` tag, else estimated from the bounding box diagonal."""
    raw = el.get("tags", {}).get("length")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass

    bounds = el.get("bounds")
    if not bounds:
        return None
    return 1000.0 * haversine_distance(
        Coordinate(lat=bounds["minlat"], lon=bounds["minlon"]),
        Coordinate(lat=bounds["maxlat"], lon=bounds["maxlon"]),
    )


def _parse_overpass_response(data: dict[str, Any]) -> list[SpecialElement]:
    """Parse an Overpass JSON response into a list of SpecialElement."""
    elements: list[SpecialElement] = []
    # Twin-bore tunnels are mapped as one way per carriageway: keep one per bore
    # (same name, or same ~500 m cell when unnamed) so coverage is not counted twice.
    tunnels: dict[str, SpecialElement] = {}

    for el in data.get("elements", []):
        position = _element_position(el)
        if position is None:
            continue
        lat, lon = position
        tags = el.get("tags", {})
        name = tags.get("name")

        if tags.get("tunnel") == "yes":
            length_m = _tunnel_length_m(el)
            if length_m is None or length_m < MIN_TUNNEL_LENGTH_M:
                continue
            key = name or f"{round(lat / 0.005)}:{round(lon / 0.005)}"
            existing = tunnels.get(key)
            if existing is None or (existing.length_m or 0.0) < length_m:
                tunnels[key] = SpecialElement(
                    type=SpecialElementType.TUNNEL, name=name, length_m=length_m, lat=lat, lon=lon
                )
        elif tags.get("bridge") == "yes":
            elements.append(SpecialElement(type=SpecialElementType.BRIDGE, name=name, lat=lat, lon=lon))
        elif tags.get("mountain_pass") == "yes":
            elements.append(
                SpecialElement(type=SpecialElementType.MOUNTAIN_PASS, name=name, lat=lat, lon=lon)
            )
        elif tags.get("place") in ("city", "town"):
            elements.append(SpecialElement(type=SpecialElementType.URBAN_CENTER, name=name, lat=lat, lon=lon))

    return list(tunnels.values()) + elements


async def get_special_elements(
    polyline: list[Coordinate],
    base_url: str | None = None,
) -> list[SpecialElement]:
    """
    Fetch special route elements from the Overpass API.

    Returns an empty list on failure (graceful degradation).
    Results are cached for 7 days in Redis, with a bounded in-memory fallback.
    """
    if not polyline:
        return []

    settings = get_settings()
    if base_url is None:
        base_url = settings.overpass_base_url

    cache_key = _osm_cache_key(polyline)

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Overpass Redis cache HIT (key=%s)", cache_key)
        return [SpecialElement(**e) for e in cached]

    mem_cached = _mem_cache_get(cache_key)
    if mem_cached is not None:
        logger.debug("Overpass memory cache HIT (key=%s)", cache_key)
        return [SpecialElement(**e) for e in mem_cached]

    query = _build_overpass_query(polyline)
    url = f"{base_url.rstrip('/')}/api/interpreter"

    t0 = time.monotonic()
    try:
        response = await request_with_retry(
            "POST",
            url,
            data={"data": query},
            retries=1,
            backoff=0.3,
            service_name="overpass",
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("Overpass API failed — skipping special elements", exc_info=True)
        return []

    elements = _parse_overpass_response(data)
    logger.info("Overpass: found %d special elements in %.3fs", len(elements), time.monotonic() - t0)

    serialized = [e.model_dump() for e in elements]
    await cache_set(cache_key, serialized, ttl=settings.cache_ttl_osm)
    _mem_cache_set(cache_key, serialized)

    return elements
