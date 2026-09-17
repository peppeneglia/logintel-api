"""
Special route element modifiers.

Adjusts the weather and time factors of a segment based on infrastructure
found along it (from OpenStreetMap):

  - Tunnel (> 500 m): shields the covered share of the segment from weather,
    i.e. weather delays are scaled by (1 - tunnel_km / segment_km)
  - Bridge / viaduct: amplifies wind and snow delays (x 1.3)
  - Mountain pass: amplifies snow delays (x 1.9)
  - Urban center: amplifies the time factor (x 1.3)
"""

from __future__ import annotations

import math

from app.engine.sampler import EARTH_RADIUS_KM
from app.models.schemas import (
    Coordinate,
    SpecialElement,
    SpecialElementFactor,
    SpecialElementType,
    WeatherCondition,
    WeatherType,
)

ELEMENT_MULTIPLIERS: dict[SpecialElementType, float] = {
    SpecialElementType.BRIDGE: 1.3,
    SpecialElementType.MOUNTAIN_PASS: 1.9,
    SpecialElementType.URBAN_CENTER: 1.3,
}

# Weather types amplified by each element
AMPLIFIED_WEATHER: dict[SpecialElementType, frozenset[WeatherType]] = {
    SpecialElementType.BRIDGE: frozenset({WeatherType.WIND, WeatherType.SNOW}),
    SpecialElementType.MOUNTAIN_PASS: frozenset({WeatherType.SNOW}),
}

MIN_TUNNEL_LENGTH_M = 500.0


def distance_to_segment_km(point: Coordinate, start: Coordinate, end: Coordinate) -> float:
    """
    Distance in km from *point* to the straight segment start→end.

    Uses a local equirectangular projection, accurate enough for segments of
    a few tens of kilometres.
    """
    lat0 = math.radians((start.lat + end.lat) / 2)
    km_per_deg = math.pi * EARTH_RADIUS_KM / 180.0

    def project(c: Coordinate) -> tuple[float, float]:
        return (c.lon * math.cos(lat0) * km_per_deg, c.lat * km_per_deg)

    px, py = project(point)
    ax, ay = project(start)
    bx, by = project(end)
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy

    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def find_elements_for_segment(
    start_pt: Coordinate,
    end_pt: Coordinate,
    all_elements: list[SpecialElement],
    proximity_km: float = 5.0,
) -> list[SpecialElement]:
    """Return the elements lying within *proximity_km* of the segment."""
    return [
        el
        for el in all_elements
        if distance_to_segment_km(Coordinate(lat=el.lat, lon=el.lon), start_pt, end_pt) <= proximity_km
    ]


def compute_special_element_modifier(
    elements: list[SpecialElement],
    weather_conditions: list[WeatherCondition],
    segment_km: float,
) -> tuple[dict[WeatherType, float], float, list[SpecialElementFactor]]:
    """
    Compute per-weather-type multipliers and the time multiplier for a segment.

    Returns:
        (weather_multipliers, time_multiplier, applied_factors)

    ``weather_multipliers`` only contains the weather types that are affected;
    missing types have an implicit multiplier of 1.0.
    """
    factors: list[SpecialElementFactor] = []
    active_types = {c.type for c in weather_conditions}
    weather_multipliers: dict[WeatherType, float] = {}
    time_multiplier = 1.0

    # Amplifiers (bridges, mountain passes): applied once per element type,
    # only when the weather they amplify is actually present.
    for element_type, amplified in AMPLIFIED_WEATHER.items():
        matching = [el for el in elements if el.type == element_type]
        affected = amplified & active_types
        if not matching or not affected:
            continue
        multiplier = ELEMENT_MULTIPLIERS[element_type]
        for weather_type in affected:
            weather_multipliers[weather_type] = weather_multipliers.get(weather_type, 1.0) * multiplier
        factors.extend(
            SpecialElementFactor(element_type=element_type, element_name=el.name, multiplier=multiplier)
            for el in matching
        )

    # Tunnels: shield the covered share of the segment from every weather type.
    tunnels = [el for el in elements if el.type == SpecialElementType.TUNNEL and el.length_m]
    if tunnels and weather_conditions and segment_km > 0:
        covered_km = min(sum(t.length_m for t in tunnels) / 1000.0, segment_km)
        shield = 1.0 - covered_km / segment_km
        for weather_type in active_types:
            weather_multipliers[weather_type] = weather_multipliers.get(weather_type, 1.0) * shield
        factors.extend(
            SpecialElementFactor(
                element_type=SpecialElementType.TUNNEL,
                element_name=t.name,
                multiplier=round(1.0 - min(t.length_m / 1000.0 / segment_km, 1.0), 4),
            )
            for t in tunnels
        )

    # Urban centers: amplify the time factor regardless of weather.
    urban = [el for el in elements if el.type == SpecialElementType.URBAN_CENTER]
    if urban:
        time_multiplier = ELEMENT_MULTIPLIERS[SpecialElementType.URBAN_CENTER]
        factors.extend(
            SpecialElementFactor(
                element_type=SpecialElementType.URBAN_CENTER,
                element_name=el.name,
                multiplier=time_multiplier,
            )
            for el in urban
        )

    return weather_multipliers, time_multiplier, factors
