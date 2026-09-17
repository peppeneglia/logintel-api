"""
OpenRouteService integration.

Provides route calculation for heavy-goods vehicles (driving-hgv profile).
Decodes encoded polylines and extracts road-type information.

Critical constraint: the ORS free tier allows 2,000 requests/day, so routes
are cached for 24h and keyed on coordinates snapped to a ~500 m grid.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.config import get_settings
from app.engine.sampler import haversine_distance
from app.models.schemas import Coordinate, RoadType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

# ORS way_type mapping → our RoadType enum
# See: https://giscience.github.io/openrouteservice/documentation/extra-info
# ORS way_types: 0=Unknown, 1=StateRoad, 2=Road, 3=Street, 4=Path,
#                5=Track, 6=Cycleway, 7=Footway, 8=Steps, 9=Ferry, 10=Construction
_ORS_WAY_TYPE_MAP: dict[int, RoadType] = {
    0: RoadType.HIGHWAY,  # Unknown → default to highway (motorway segments)
    1: RoadType.STATE_ROAD,  # State road
    2: RoadType.PROVINCIAL,  # Road
    3: RoadType.PROVINCIAL,  # Street
    4: RoadType.MOUNTAIN,  # Path (likely mountain/rural)
    5: RoadType.MOUNTAIN,  # Track
}


@dataclass
class RouteResult:
    """Result from ORS directions call."""

    polyline: list[Coordinate]
    duration_s: float
    distance_m: float
    road_types: list[tuple[float, float, RoadType]]  # (start_fraction, end_fraction, road_type)


def decode_polyline(encoded: str, precision: int = 5) -> list[Coordinate]:
    """
    Decode a Google-encoded polyline string into a list of Coordinates.

    ORS uses precision=5 by default (divide by 1e5).
    """
    coordinates: list[Coordinate] = []
    index = 0
    lat = 0
    lon = 0
    factor = 10**precision

    while index < len(encoded):
        # Decode latitude
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lat += ~(result >> 1) if result & 1 else result >> 1

        # Decode longitude
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lon += ~(result >> 1) if result & 1 else result >> 1

        coordinates.append(Coordinate(lat=round(lat / factor, 6), lon=round(lon / factor, 6)))

    return coordinates


def compute_route_hash(origin: Coordinate, destination: Coordinate) -> str:
    """
    Compute a stable hash for a route based on rounded coordinates.

    Coordinates are rounded to ±0.005° (~500m) so nearby origins/destinations
    produce the same hash, enabling cache reuse.
    """

    def _round(val: float) -> float:
        return round(val / 0.005) * 0.005  # round to nearest 0.005° (~500m)

    key = (
        f"{_round(origin.lat):.3f},{_round(origin.lon):.3f}"
        f"→{_round(destination.lat):.3f},{_round(destination.lon):.3f}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _parse_road_types(
    extras: dict[str, Any], polyline: list[Coordinate]
) -> list[tuple[float, float, RoadType]]:
    """
    Convert ORS way-type extras into (start_fraction, end_fraction, RoadType) tuples.

    ORS returns ranges of polyline point indices, ``[[start_idx, end_idx, code], ...]``;
    they are converted into fractions of the total route length.
    """
    # The request parameter is "waytype", but responses use the "waytypes" key.
    waytypes = extras.get("waytypes") or extras.get("waytype") or {}
    values = waytypes.get("values", [])
    if not values or len(polyline) < 2:
        return [(0.0, 1.0, RoadType.HIGHWAY)]

    cumulative_km = [0.0]
    for prev, curr in pairwise(polyline):
        cumulative_km.append(cumulative_km[-1] + haversine_distance(prev, curr))
    total_km = cumulative_km[-1] or 1.0
    last_idx = len(cumulative_km) - 1

    return [
        (
            cumulative_km[min(start_idx, last_idx)] / total_km,
            cumulative_km[min(end_idx, last_idx)] / total_km,
            _ORS_WAY_TYPE_MAP.get(code, RoadType.HIGHWAY),
        )
        for start_idx, end_idx, code in values
    ]


def get_road_type_at_fraction(road_types: list[tuple[float, float, RoadType]], fraction: float) -> RoadType:
    """Return the road type at a given fraction (0-1) along the route."""
    for start_pct, end_pct, road_type in road_types:
        if start_pct <= fraction <= end_pct:
            return road_type
    return RoadType.HIGHWAY


def _parse_single_route(route_data: dict[str, Any]) -> RouteResult:
    """Parse a single ORS route object into a RouteResult."""
    summary = route_data["summary"]
    polyline = decode_polyline(route_data["geometry"])
    return RouteResult(
        polyline=polyline,
        duration_s=summary["duration"],
        distance_m=summary["distance"],
        road_types=_parse_road_types(route_data.get("extras", {}), polyline),
    )


def _route_to_dict(r: RouteResult) -> dict:
    """Serialise RouteResult to a JSON-safe dict for caching."""
    return {
        "polyline": [{"lat": c.lat, "lon": c.lon} for c in r.polyline],
        "duration_s": r.duration_s,
        "distance_m": r.distance_m,
        "road_types": [[s, e, rt.value] for s, e, rt in r.road_types],
    }


def _dict_to_route(d: dict) -> RouteResult:
    """Deserialise a cached dict back into a RouteResult."""
    return RouteResult(
        polyline=[Coordinate(lat=p["lat"], lon=p["lon"]) for p in d["polyline"]],
        duration_s=d["duration_s"],
        distance_m=d["distance_m"],
        road_types=[(s, e, RoadType(rt)) for s, e, rt in d["road_types"]],
    )


def _routes_to_dicts(routes: list[RouteResult]) -> list[dict]:
    """Serialise a list of RouteResults for caching."""
    return [_route_to_dict(r) for r in routes]


def _dicts_to_routes(dicts: list[dict]) -> list[RouteResult]:
    """Deserialise a cached list of dicts back into RouteResults."""
    return [_dict_to_route(d) for d in dicts]


async def _request_directions(
    origin: Coordinate,
    destination: Coordinate,
    alternatives: bool,
):
    """POST a driving-hgv directions request to ORS and return the raw response."""
    settings = get_settings()
    body: dict[str, Any] = {
        "coordinates": [[origin.lon, origin.lat], [destination.lon, destination.lat]],
        "extra_info": ["waytype"],
        "instructions": False,
        "geometry": True,
    }
    if alternatives:
        body["alternative_routes"] = {
            "target_count": 2,
            "share_factor": 0.6,
            "weight_factor": 1.4,
        }

    return await request_with_retry(
        "POST",
        f"{settings.ors_base_url.rstrip('/')}/v2/directions/driving-hgv",
        headers={"Authorization": settings.ors_api_key},
        json=body,
        service_name="ors",
    )


async def get_route(origin: Coordinate, destination: Coordinate) -> RouteResult:
    """
    Get the main route for heavy-goods vehicles.

    Returns decoded polyline, total duration, distance, and road types.
    Cached for 24h, keyed on coordinates snapped to a ~500 m grid.
    """
    settings = get_settings()
    cache_key = f"route:{compute_route_hash(origin, destination)}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.info("ORS route cache HIT (key=%s)", cache_key)
        return _dict_to_route(cached)

    response = await _request_directions(origin, destination, alternatives=False)
    response.raise_for_status()
    result = _parse_single_route(response.json()["routes"][0])

    logger.info(
        "ORS route: %.1f km, %.0f min, %d polyline points",
        result.distance_m / 1000,
        result.duration_s / 60,
        len(result.polyline),
    )

    await cache_set(cache_key, _route_to_dict(result), ttl=settings.cache_ttl_route)
    return result


async def get_routes(
    origin: Coordinate,
    destination: Coordinate,
    include_alternatives: bool = False,
) -> list[RouteResult]:
    """
    Get the main route, plus up to 2 alternatives when requested.

    Alternatives come from the same ORS call, so they cost no extra quota.
    If ORS rejects the alternatives request (e.g. the route exceeds the
    free-tier distance limit for alternatives), falls back to a single route.
    """
    if not include_alternatives:
        return [await get_route(origin, destination)]

    settings = get_settings()
    cache_key = f"route:{compute_route_hash(origin, destination)}:alt"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.info("ORS routes (alt) cache HIT (key=%s)", cache_key)
        return _dicts_to_routes(cached)

    response = await _request_directions(origin, destination, alternatives=True)

    if response.status_code in (400, 413):
        logger.warning(
            "ORS rejected alternative_routes (HTTP %s): %s — falling back to single route",
            response.status_code,
            response.text[:200],
        )
        return [await get_route(origin, destination)]

    response.raise_for_status()
    results = [_parse_single_route(r) for r in response.json()["routes"]]

    logger.info(
        "ORS routes: %d routes returned (main: %.1f km, %.0f min)",
        len(results),
        results[0].distance_m / 1000,
        results[0].duration_s / 60,
    )

    await cache_set(cache_key, _routes_to_dicts(results), ttl=settings.cache_ttl_route)
    return results
