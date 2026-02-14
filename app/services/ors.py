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
    waytypes = extra_info.get("waytypes", {}).get("values", [])
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


async def get_route(origin: Coordinate, destination: Coordinate) -> RouteResult:
    """
    Call ORS directions API to get a route for heavy-goods vehicles.

    Returns decoded polyline, total duration, distance, and road types.
    """
    settings = get_settings()
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
        "extra_info": ["waytypes"],
        "instructions": False,
        "geometry": True,
    }

    response = await request_with_retry("POST", url, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()

    route = data["routes"][0]
    summary = route["summary"]
    geometry = route["geometry"]
    extra_info = route.get("extras", {})

    polyline = decode_polyline(geometry)
    duration_s = summary["duration"]
    distance_m = summary["distance"]
    road_types = _parse_road_types(extra_info, distance_m)

    logger.info(
        "ORS route: %.1f km, %.0f min, %d polyline points",
        distance_m / 1000, duration_s / 60, len(polyline),
    )

    return RouteResult(
        polyline=polyline,
        duration_s=duration_s,
        distance_m=distance_m,
        road_types=road_types,
    )
