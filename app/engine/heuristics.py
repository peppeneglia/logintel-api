"""
Prediction engine heuristics — FRD Section 6.

Calculates weather-based delay for a route segment using the formula:
    delay = base_impact x severity x F_road x F_altitude x F_time x C_calibration
    effective_delay = delay x (segment_km / 100)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.models.schemas import (
    RoadType,
    Severity,
    WeatherCondition,
    WeatherType,
)

# --- Weather impact: base delay in minutes per 100km (FRD 6.2) ---

WEATHER_IMPACT: dict[WeatherType, dict[Severity, float]] = {
    WeatherType.RAIN: {
        Severity.LIGHT: 3.0,
        Severity.MODERATE: 8.0,
        Severity.HEAVY: 15.0,
        Severity.VERY_HEAVY: 25.0,
    },
    WeatherType.SNOW: {
        Severity.LIGHT: 10.0,
        Severity.MODERATE: 25.0,
        Severity.HEAVY: 45.0,
        Severity.VERY_HEAVY: 70.0,
    },
    WeatherType.WIND: {
        Severity.LIGHT: 0.0,
        Severity.MODERATE: 6.0,
        Severity.HEAVY: 12.0,
        Severity.VERY_HEAVY: 25.0,
    },
    WeatherType.FOG: {
        Severity.LIGHT: 8.0,
        Severity.MODERATE: 18.0,
        Severity.HEAVY: 35.0,
        Severity.VERY_HEAVY: 35.0,  # dense fog caps at same value
    },
}

# --- Weather severity thresholds ---

RAIN_THRESHOLDS: list[tuple[float, Severity]] = [
    (15.0, Severity.VERY_HEAVY),
    (7.5, Severity.HEAVY),
    (2.5, Severity.MODERATE),
    (0.1, Severity.LIGHT),
]

SNOW_THRESHOLDS: list[tuple[float, Severity]] = [
    (5.0, Severity.VERY_HEAVY),
    (3.0, Severity.HEAVY),
    (1.0, Severity.MODERATE),
    (0.1, Severity.LIGHT),
]

WIND_THRESHOLDS: list[tuple[float, Severity]] = [
    (80.0, Severity.VERY_HEAVY),   # storm
    (60.0, Severity.HEAVY),        # very strong
    (40.0, Severity.MODERATE),     # strong
]

# Fog: thresholds are visibility in meters (lower = worse)
FOG_THRESHOLDS: list[tuple[float, Severity]] = [
    (50.0, Severity.HEAVY),        # dense: <50m
    (200.0, Severity.MODERATE),    # moderate: 50-200m
    (500.0, Severity.LIGHT),       # light: 200-500m
]

# --- Context multipliers (FRD 6.3) ---

ROAD_FACTORS: dict[RoadType, float] = {
    RoadType.HIGHWAY: 0.8,
    RoadType.STATE_ROAD: 1.0,
    RoadType.PROVINCIAL: 1.3,
    RoadType.MOUNTAIN: 1.8,
}

ALTITUDE_BANDS: list[tuple[float, float]] = [
    (1500.0, 2.0),
    (1000.0, 1.6),
    (600.0, 1.3),
    (300.0, 1.1),
    (0.0, 1.0),
]

# Summer exodus periods (approximate: mid-July to mid-August weekends)
SUMMER_EXODUS_MONTHS = {7, 8}


def classify_rain(mm_per_hour: float) -> Optional[Severity]:
    for threshold, severity in RAIN_THRESHOLDS:
        if mm_per_hour >= threshold:
            return severity
    return None


def classify_snow(cm_per_hour: float) -> Optional[Severity]:
    for threshold, severity in SNOW_THRESHOLDS:
        if cm_per_hour >= threshold:
            return severity
    return None


def classify_wind(km_per_hour: float) -> Optional[Severity]:
    for threshold, severity in WIND_THRESHOLDS:
        if km_per_hour >= threshold:
            return severity
    return None


def classify_fog(visibility_m: float) -> Optional[Severity]:
    """Lower visibility = more severe. Thresholds are upper bounds."""
    for upper_bound, severity in FOG_THRESHOLDS:
        if visibility_m < upper_bound:
            return severity
    # visibility >= 500m: check if still reduced enough to count as light fog
    if visibility_m <= 500.0:
        return Severity.LIGHT
    return None


SEVERITY_CLASSIFIERS = {
    WeatherType.RAIN: classify_rain,
    WeatherType.SNOW: classify_snow,
    WeatherType.WIND: classify_wind,
    WeatherType.FOG: classify_fog,
}

WEATHER_DESCRIPTIONS: dict[WeatherType, dict[Severity, str]] = {
    WeatherType.RAIN: {
        Severity.LIGHT: "Light rain",
        Severity.MODERATE: "Moderate rain, aquaplaning possible",
        Severity.HEAVY: "Heavy rain, significant slowdowns",
        Severity.VERY_HEAVY: "Very heavy rain, possible temporary stops",
    },
    WeatherType.SNOW: {
        Severity.LIGHT: "Light snow, roads treated",
        Severity.MODERATE: "Moderate snow, chains recommended",
        Severity.HEAVY: "Heavy snow, chains required",
        Severity.VERY_HEAVY: "Very heavy snow, closures possible",
    },
    WeatherType.WIND: {
        Severity.MODERATE: "Strong wind",
        Severity.HEAVY: "Very strong wind",
        Severity.VERY_HEAVY: "Storm-force wind",
    },
    WeatherType.FOG: {
        Severity.LIGHT: "Light fog, reduced visibility",
        Severity.MODERATE: "Moderate fog",
        Severity.HEAVY: "Dense fog, severely reduced visibility",
    },
}


def classify_weather(
    weather_type: WeatherType, raw_value: float
) -> Optional[WeatherCondition]:
    """Classify a raw weather measurement into a WeatherCondition."""
    classifier = SEVERITY_CLASSIFIERS[weather_type]
    severity = classifier(raw_value)
    if severity is None:
        return None
    descriptions = WEATHER_DESCRIPTIONS.get(weather_type, {})
    description = descriptions.get(severity, f"{weather_type.value} ({severity.value})")
    return WeatherCondition(
        type=weather_type,
        severity=severity,
        raw_value=raw_value,
        description=description,
    )


def get_base_impact(condition: WeatherCondition) -> float:
    """Get base delay in minutes per 100km for a weather condition."""
    return WEATHER_IMPACT[condition.type][condition.severity]


def get_road_factor(road_type: RoadType) -> float:
    return ROAD_FACTORS[road_type]


def get_altitude_factor(altitude_m: float) -> float:
    for threshold, factor in ALTITUDE_BANDS:
        if altitude_m >= threshold:
            return factor
    return 1.0


def get_time_factor(dt: datetime) -> float:
    """Calculate time-based multiplier from FRD 6.3."""
    hour = dt.hour
    weekday = dt.weekday()  # 0=Monday, 6=Sunday
    is_weekend = weekday >= 5

    # Summer exodus check (July-August weekends/Fridays)
    if dt.month in SUMMER_EXODUS_MONTHS and (is_weekend or weekday == 4):
        return 1.5

    if is_weekend:
        return 0.85

    # Night: 22-06
    if hour >= 22 or hour < 6:
        return 0.7

    # Rush hour: 7-9, 17-19
    if (7 <= hour <= 9) or (17 <= hour <= 19):
        return 1.4

    # Default: normal daytime
    return 1.0


def calculate_segment_delay(
    weather_conditions: list[WeatherCondition],
    segment_km: float,
    road_type: RoadType,
    altitude_m: float,
    arrival_time: datetime,
    calibration_factor: float = 1.0,
    special_element_weather_multiplier: float = 1.0,
    special_element_time_multiplier: float = 1.0,
) -> float:
    """
    Calculate total delay for a single segment.

    Sums delays from all active weather conditions on the segment.
    Formula per condition:
        delay = base_impact x F_road x F_altitude x F_time x C_calibration x (km/100)

    Special element multipliers (FRD 6.4):
        - special_element_weather_multiplier: applied to each weather condition delay
          (0.0 for tunnels = annuls all weather delay)
        - special_element_time_multiplier: applied to the time factor
          (1.3 for urban centers)
    """
    if not weather_conditions:
        return 0.0

    f_road = get_road_factor(road_type)
    f_altitude = get_altitude_factor(altitude_m)
    f_time = get_time_factor(arrival_time) * special_element_time_multiplier

    total_delay = 0.0
    for condition in weather_conditions:
        base = get_base_impact(condition)
        delay = base * f_road * f_altitude * f_time * calibration_factor
        delay *= special_element_weather_multiplier
        effective_delay = delay * (segment_km / 100.0)
        total_delay += effective_delay

    return round(total_delay, 2)
