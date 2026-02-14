"""
OpenRouteService integration — FRD Section 4.2.

Provides route calculation for heavy-goods vehicles (driving-hgv profile).
Decodes encoded polylines and extracts road-type information.

Critical constraint: ORS has a 2,000 req/day free limit.
Route hash allows cache reuse for nearby coordinates (~500m).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.config import get_settings
from app.models.schemas import Coordinate, RoadType
from app.services.cache import cache_get, cache_set
from app.services.http_client import request_with_retry

logger = logging.getLogger(__name__)

# ORS way_type mapping → our RoadType enum
# See: https://giscience.github.io/openrouteservice/documentation/extra-info
# ORS way_types: 0=Unknown, 1=StateRoad, 2=Road, 3=Street, 4=Path,
#                5=Track, 6=Cycleway, 7=Footway, 8=Steps, 9=Ferry, 10=Construction
_ORS_WAY_TYPE_MAP: dict[int, RoadType] = {
    0: RoadType.HIGHWAY,       # Unknown → default to highway (motorway segments)
    1: RoadType.STATE_ROAD,    # State road
    2: RoadType.PROVINCIAL,    # Road
    3: RoadType.PROVINCIAL,    # Street
    4: RoadType.MOUNTAIN,      # Path (likely mountain/rural)
    5: RoadType.MOUNTAIN,      # Track
}


@dataclass
class RouteResult:
    """Result from ORS directions call."""
    polyline: list[Coordinate]
    duration_s: float
    distance_m: float
    road_types: list[tuple[float, float, RoadType]]  # (start_pct, end_pct, road_type)


def decode_polyline(encoded: str, precision: int = 5) -> list[Coordinate]:
    """
    Decode a Google-encoded polyline string into a list of Coordinates.

    ORS uses precision=5 by default (divide by 1e5).
    """
    coordinates: list[Coordinate] = []
    index = 0
    lat = 0
    lon = 0
    factor = 10 ** precision

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
        lat += (~(result >> 1) if result & 1 else result >> 1)

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
        lon += (~(result >> 1) if result & 1 else result >> 1)

        coordinates.append(
            Coordinate(lat=round(lat / factor, 6), lon=round(lon / factor, 6))
        )

    return coordinates


def compute_route_hash(origin: Coordinate, destination: Coordinate) -> str:
    """
    Compute a stable hash for a route based on rounded coordinates.

    Coordinates are rounded to ±0.005° (~500m) so nearby origins/destinations
    produce the same hash, enabling cache reuse.
    """
    def _round(val: float) -> float:
        return round(val / 0.01) * 0.01  # round to nearest 0.01° (~1.1km)

    key = (
        f"{_round(origin.lat):.2f},{_round(origin.lon):.2f}"
        f"→{_round(destination.lat):.2f},{_round(destination.lon):.2f}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _parse_road_types(
    extra_info: dict, total_distance_m: float
) -> list[tuple[float, float, RoadType]]:
    """
    Parse ORS extra_info.waytypes into (start_pct, end_pct, RoadType) tuples.

    ORS returns waytypes as [[start_idx, end_idx, way_type_code], ...].
    We convert index-based ranges to distance-percentage ranges.
    """
    waytypes = extra_info.get("waytype", extra_info.get("waytypes", {})).get("values", [])
    if not waytypes:
        return [(0.0, 1.0, RoadType.HIGHWAY)]

    result: list[tuple[float, float, RoadType]] = []
    # ORS summary gives total steps; we normalise indices to percentages
    total_steps = max(wt[1] for wt in waytypes) if waytypes else 1
    if total_steps == 0:
        total_steps = 1

    for start_idx, end_idx, way_code in waytypes:
        road = _ORS_WAY_TYPE_MAP.get(way_code, RoadType.HIGHWAY)
        start_pct = start_idx / total_steps
        end_pct = end_idx / total_steps
        result.append((start_pct, end_pct, road))

    return result


def get_road_type_at_fraction(
    road_types: list[tuple[float, float, RoadType]], fraction: float
) -> RoadType:
    """Return the road type at a given fraction (0-1) along the route."""
    for start_pct, end_pct, road_type in road_types:
        if start_pct <= fraction <= end_pct:
            return road_type
    return RoadType.HIGHWAY


def _parse_single_route(route_data: dict) -> RouteResult:
    """Parse a single ORS route object into a RouteResult."""
    summary = route_data["summary"]
    geometry = route_data["geometry"]
    extra_info = route_data.get("extras", {})

    polyline = decode_polyline(geometry)
    duration_s = summary["duration"]
    distance_m = summary["distance"]
    road_types = _parse_road_types(extra_info, distance_m)

    return RouteResult(
        polyline=polyline,
        duration_s=duration_s,
        distance_m=distance_m,
        road_types=road_types,
    )


def _route_to_dict(r: RouteResult) -> dict:
    """Serialise RouteResult to a JSON-safe dict for caching."""
    return {
        "polyline": [{"lat": c.lat, "lon": c.lon} for c in r.polyline],
        "duration_s": r.duration_s,
        "distance_m": r.distance_m,
        "road_types": [
            [s, e, rt.value] for s, e, rt in r.road_types
        ],
    }


def _dict_to_route(d: dict) -> RouteResult:
    """Deserialise a cached dict back into a RouteResult."""
    return RouteResult(
        polyline=[Coordinate(lat=p["lat"], lon=p["lon"]) for p in d["polyline"]],
        duration_s=d["duration_s"],
        distance_m=d["distance_m"],
        road_types=[
            (s, e, RoadType(rt)) for s, e, rt in d["road_types"]
        ],
    )


def _routes_to_dicts(routes: list[RouteResult]) -> list[dict]:
    """Serialise a list of RouteResults for caching."""
    return [_route_to_dict(r) for r in routes]


def _dicts_to_routes(dicts: list[dict]) -> list[RouteResult]:
    """Deserialise a cached list of dicts back into RouteResults."""
    return [_dict_to_route(d) for d in dicts]


async def get_route(origin: Coordinate, destination: Coordinate) -> RouteResult:
    """
    Call ORS directions API to get a route for heavy-goods vehicles.

    Returns decoded polyline, total duration, distance, and road types.
    Cached for 24h keyed on rounded coordinates (~1km grid).
    """
    settings = get_settings()
    route_hash = compute_route_hash(origin, destination)
    cache_key = f"route:{route_hash}"

    # --- Cache check ---
    cached = await cache_get(cache_key)
    if cached is not None:
        logger.info("ORS route cache HIT (key=%s)", cache_key)
        return _dict_to_route(cached)

    # --- API call ---
    base_url = settings.ors_base_url.rstrip("/")
    url = f"{base_url}/v2/directions/driving-hgv"

    headers = {
        "Authorization": settings.ors_api_key,
        "Content-Type": "application/json",
    }

    body = {
        "coordinates": [
            [origin.lon, origin.lat],
            [destination.lon, destination.lat],
        ],
        "extra_info": ["waytype"],
        "instructions": False,
        "geometry": True,
    }

    response = await request_with_retry(
        "POST", url, headers=headers, json=body, service_name="ors",
    )
    response.raise_for_status()
    data = response.json()

    result = _parse_single_route(data["routes"][0])

    logger.info(
        "ORS route: %.1f km, %.0f min, %d polyline points",
        result.distance_m / 1000, result.duration_s / 60, len(result.polyline),
    )

    # --- Cache store ---
    await cache_set(cache_key, _route_to_dict(result), ttl=settings.cache_ttl_route)

    return result


async def get_routes(
    origin: Coordinate,
    destination: Coordinate,
    include_alternatives: bool = False,
) -> list[RouteResult]:
    """
    Get route(s) from ORS. When include_alternatives is True, requests up to
    3 routes (1 main + 2 alternatives) in a single ORS API call.

    Uses a separate cache key from get_route to avoid conflicts.
    When include_alternatives is False, delegates to get_route.
    """
    if not include_alternatives:
        return [await get_route(origin, destination)]

    settings = get_settings()
    route_hash = compute_route_hash(origin, destination)
    cache_key = f"route:{route_hash}:alt"

    # --- Cache check ---
    cached = await cache_get(cache_key)
    if cached is not None:
        logger.info("ORS routes (alt) cache HIT (key=%s)", cache_key)
        return _dicts_to_routes(cached)

    # --- API call with alternatives ---
    base_url = settings.ors_base_url.rstrip("/")
    url = f"{base_url}/v2/directions/driving-hgv"

    headers = {
        "Authorization": settings.ors_api_key,
        "Content-Type": "application/json",
    }

    body = {
        "coordinates": [
            [origin.lon, origin.lat],
            [destination.lon, destination.lat],
        ],
        "extra_info": ["waytype"],
        "instructions": False,
        "geometry": True,
        "alternative_routes": {
            "target_count": 2,
            "share_factor": 0.6,
            "weight_factor": 1.4,
        },
    }

    response = await request_with_retry(
        "POST", url, headers=headers, json=body, service_name="ors",
    )
    response.raise_for_status()
    data = response.json()

    results = [_parse_single_route(r) for r in data["routes"]]

    logger.info(
        "ORS routes: %d routes returned (main: %.1f km, %.0f min)",
        len(results),
        results[0].distance_m / 1000,
        results[0].duration_s / 60,
    )

    # --- Cache store ---
    await cache_set(cache_key, _routes_to_dicts(results), ttl=settings.cache_ttl_route)

    return results
