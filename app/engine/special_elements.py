"""
Special route element modifiers — FRD Section 6.4.

Applies multipliers for tunnels, bridges, mountain passes, and urban centers
to weather and time factors in the delay calculation.

Rules:
  - Tunnel > 500m: annuls all weather delay (multiplier = 0.0)
  - Bridge/viaduct: amplifies wind and snow delay (x 1.3)
  - Mountain pass: amplifies snow delay (x 1.9)
  - Urban center: amplifies time factor (x 1.3)
  - Tunnel takes absolute precedence (if tunnel present, weather_multiplier = 0.0)
"""

from __future__ import annotations

from app.engine.sampler import haversine_distance
from app.models.schemas import (
    Coordinate,
    SpecialElement,
    SpecialElementFactor,
    SpecialElementType,
    WeatherCondition,
    WeatherType,
)

# FRD 6.4 multipliers
ELEMENT_MULTIPLIERS: dict[SpecialElementType, float] = {
    SpecialElementType.TUNNEL: 0.0,         # Annuls weather
    SpecialElementType.BRIDGE: 1.3,         # Amplifies wind & snow
    SpecialElementType.MOUNTAIN_PASS: 1.9,  # Amplifies snow
    SpecialElementType.URBAN_CENTER: 1.3,   # Amplifies time factor
}


def find_elements_for_segment(
    start_pt: Coordinate,
    end_pt: Coordinate,
    all_elements: list[SpecialElement],
    proximity_km: float = 5.0,
) -> list[SpecialElement]:
    """Filter special elements within proximity_km of the segment midpoint."""
    mid = Coordinate(
        lat=(start_pt.lat + end_pt.lat) / 2,
        lon=(start_pt.lon + end_pt.lon) / 2,
    )
    result: list[SpecialElement] = []
    for el in all_elements:
        el_coord = Coordinate(lat=el.lat, lon=el.lon)
        dist = haversine_distance(mid, el_coord)
        if dist <= proximity_km:
            result.append(el)
    return result


def compute_special_element_modifier(
    elements: list[SpecialElement],
    weather_conditions: list[WeatherCondition],
) -> tuple[float, float, list[SpecialElementFactor]]:
    """
    Compute weather and time multipliers from special elements.

    Returns:
        (weather_multiplier, time_multiplier, list[SpecialElementFactor])

    Tunnel has absolute precedence: if any tunnel is present,
    weather_multiplier = 0.0 regardless of other elements.
    """
    if not elements:
        return 1.0, 1.0, []

    factors: list[SpecialElementFactor] = []
    weather_multiplier = 1.0
    time_multiplier = 1.0
    has_tunnel = False

    # Collect element types present
    element_types = {el.type for el in elements}

    # Check for tunnel first (absolute precedence)
    if SpecialElementType.TUNNEL in element_types:
        has_tunnel = True
        weather_multiplier = 0.0
        for el in elements:
            if el.type == SpecialElementType.TUNNEL:
                factors.append(SpecialElementFactor(
                    element_type=SpecialElementType.TUNNEL,
                    element_name=el.name,
                    multiplier=0.0,
                ))

    if not has_tunnel:
        # Bridge: amplifies wind and snow
        if SpecialElementType.BRIDGE in element_types:
            has_wind_or_snow = any(
                c.type in (WeatherType.WIND, WeatherType.SNOW)
                for c in weather_conditions
            )
            if has_wind_or_snow:
                bridge_mult = ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]
                weather_multiplier *= bridge_mult
                for el in elements:
                    if el.type == SpecialElementType.BRIDGE:
                        factors.append(SpecialElementFactor(
                            element_type=SpecialElementType.BRIDGE,
                            element_name=el.name,
                            multiplier=bridge_mult,
                        ))

        # Mountain pass: amplifies snow
        if SpecialElementType.MOUNTAIN_PASS in element_types:
            has_snow = any(c.type == WeatherType.SNOW for c in weather_conditions)
            if has_snow:
                pass_mult = ELEMENT_MULTIPLIERS[SpecialElementType.MOUNTAIN_PASS]
                weather_multiplier *= pass_mult
                for el in elements:
                    if el.type == SpecialElementType.MOUNTAIN_PASS:
                        factors.append(SpecialElementFactor(
                            element_type=SpecialElementType.MOUNTAIN_PASS,
                            element_name=el.name,
                            multiplier=pass_mult,
                        ))

    # Urban center: amplifies time factor (independent of tunnel)
    if SpecialElementType.URBAN_CENTER in element_types:
        urban_mult = ELEMENT_MULTIPLIERS[SpecialElementType.URBAN_CENTER]
        time_multiplier *= urban_mult
        for el in elements:
            if el.type == SpecialElementType.URBAN_CENTER:
                factors.append(SpecialElementFactor(
                    element_type=SpecialElementType.URBAN_CENTER,
                    element_name=el.name,
                    multiplier=urban_mult,
                ))

    return weather_multiplier, time_multiplier, factors
