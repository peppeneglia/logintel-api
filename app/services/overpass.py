"""
Overpass API (OpenStreetMap) integration — FRD Section 6.4.

Fetches special route elements (tunnels, bridges, mountain passes, urban centers)
along a route polyline. Results cached for 7 days (infrastructure is static).

Graceful degradation: on failure returns empty list (no special element modifiers).
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.models.schemas import Coordinate, SpecialElement, SpecialElementType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)


def _compute_route_bbox(
    polyline: list[Coordinate], padding_deg: float = 0.05
) -> tuple[float, float, float, float]:
    """Compute bounding box (south, west, north, east) with padding."""
    lats = [p.lat for p in polyline]
    lons = [p.lon for p in polyline]
    return (
        min(lats) - padding_deg,
        min(lons) - padding_deg,
        max(lats) + padding_deg,
        max(lons) + padding_deg,
    )


def _build_overpass_query(bbox: tuple[float, float, float, float]) -> str:
    """Build Overpass QL query for tunnels, bridges, mountain passes, and urban centers."""
    s, w, n, e = bbox
    bb = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:25];
(
  way["tunnel"="yes"]({bb});
  way["bridge"="yes"]({bb});
  node["mountain_pass"="yes"]({bb});
  node["place"~"city|town"]({bb});
);
out center;"""


def _osm_cache_key(bbox: tuple[float, float, float, float]) -> str:
    """Cache key from bbox rounded to 0.1 degree."""
    s, w, n, e = bbox
    return f"osm:{round(s,1)}:{round(w,1)}:{round(n,1)}:{round(e,1)}"


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
            # Estimate tunnel length from tags if available
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
    Results cached for 7 days.
    """
    if not polyline:
        return []

    settings = get_settings()
    if base_url is None:
        base_url = settings.overpass_base_url

    bbox = _compute_route_bbox(polyline)
    cache_key = _osm_cache_key(bbox)

    # Cache check
    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Overpass cache HIT (key=%s)", cache_key)
        return [SpecialElement(**e) for e in cached]

    # API call
    query = _build_overpass_query(bbox)
    url = f"{base_url.rstrip('/')}/api/interpreter"

    try:
        response = await request_with_retry(
            "POST", url, data={"data": query}, retries=2, backoff=0.5,
            service_name="overpass",
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.warning("Overpass API failed — skipping special elements", exc_info=True)
        return []

    elements = _parse_overpass_response(data)
    logger.info("Overpass: found %d special elements along route", len(elements))

    # Cache store (7 days)
    await cache_set(
        cache_key,
        [e.model_dump() for e in elements],
        ttl=settings.cache_ttl_osm,
    )

    return elements
