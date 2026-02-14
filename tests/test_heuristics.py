"""Tests for the heuristics engine — validates against FRD Section 6."""

from datetime import datetime, timezone

import pytest

from app.engine.heuristics import (
    classify_weather,
    calculate_segment_delay,
    get_altitude_factor,
    get_road_factor,
    get_time_factor,
    WeatherType,
    RoadType,
    Severity,
)
from app.models.schemas import WeatherCondition


# --- classify_weather ---

class TestClassifyWeather:
    def test_rain_light(self):
        cond = classify_weather(WeatherType.RAIN, 1.0)
        assert cond is not None
        assert cond.severity == Severity.LIGHT

    def test_rain_moderate(self):
        cond = classify_weather(WeatherType.RAIN, 5.0)
        assert cond.severity == Severity.MODERATE

    def test_rain_heavy(self):
        cond = classify_weather(WeatherType.RAIN, 10.0)
        assert cond.severity == Severity.HEAVY

    def test_rain_very_heavy(self):
        cond = classify_weather(WeatherType.RAIN, 20.0)
        assert cond.severity == Severity.VERY_HEAVY

    def test_rain_below_threshold(self):
        cond = classify_weather(WeatherType.RAIN, 0.05)
        assert cond is None

    def test_snow_moderate(self):
        cond = classify_weather(WeatherType.SNOW, 2.0)
        assert cond.severity == Severity.MODERATE

    def test_wind_storm(self):
        cond = classify_weather(WeatherType.WIND, 90.0)
        assert cond.severity == Severity.VERY_HEAVY

    def test_wind_below_threshold(self):
        cond = classify_weather(WeatherType.WIND, 30.0)
        assert cond is None

    def test_fog_dense(self):
        cond = classify_weather(WeatherType.FOG, 30.0)
        assert cond.severity == Severity.HEAVY

    def test_fog_no_impact(self):
        cond = classify_weather(WeatherType.FOG, 1000.0)
        assert cond is None


# --- Context factors ---

class TestContextFactors:
    def test_road_factors(self):
        assert get_road_factor(RoadType.HIGHWAY) == 0.8
        assert get_road_factor(RoadType.STATE_ROAD) == 1.0
        assert get_road_factor(RoadType.PROVINCIAL) == 1.3
        assert get_road_factor(RoadType.MOUNTAIN) == 1.8

    def test_altitude_factors(self):
        assert get_altitude_factor(100) == 1.0
        assert get_altitude_factor(400) == 1.1
        assert get_altitude_factor(800) == 1.3
        assert get_altitude_factor(1200) == 1.6
        assert get_altitude_factor(2000) == 2.0

    def test_time_factor_night(self):
        dt = datetime(2026, 2, 10, 3, 0, tzinfo=timezone.utc)  # Tuesday 3am
        assert get_time_factor(dt) == 0.7

    def test_time_factor_rush_hour(self):
        dt = datetime(2026, 2, 10, 8, 0, tzinfo=timezone.utc)  # Tuesday 8am
        assert get_time_factor(dt) == 1.4

    def test_time_factor_weekend(self):
        dt = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)  # Saturday noon
        assert get_time_factor(dt) == 0.85

    def test_time_factor_summer_exodus(self):
        dt = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)  # Saturday August
        assert get_time_factor(dt) == 1.5

    def test_time_factor_normal_daytime(self):
        dt = datetime(2026, 2, 10, 14, 0, tzinfo=timezone.utc)  # Tuesday 2pm
        assert get_time_factor(dt) == 1.0


# --- calculate_segment_delay ---

class TestCalculateSegmentDelay:
    def test_no_weather_no_delay(self):
        dt = datetime(2026, 2, 10, 10, 0, tzinfo=timezone.utc)
        delay = calculate_segment_delay([], 100.0, RoadType.HIGHWAY, 200, dt)
        assert delay == 0.0

    def test_moderate_rain_100km_highway(self):
        """Moderate rain on 100km highway: 8 * 0.8 * 1.0 * 1.0 * 1.0 = 6.4 min."""
        dt = datetime(2026, 2, 10, 14, 0, tzinfo=timezone.utc)  # normal daytime
        cond = classify_weather(WeatherType.RAIN, 5.0)
        delay = calculate_segment_delay([cond], 100.0, RoadType.HIGHWAY, 200, dt)
        assert delay == 6.4

    def test_heavy_snow_50km_mountain_night(self):
        """Heavy snow, 50km, mountain road, night:
        45 * 1.8 * 1.0 * 0.7 * 1.0 * (50/100) = 28.35 min."""
        dt = datetime(2026, 2, 10, 2, 0, tzinfo=timezone.utc)
        cond = classify_weather(WeatherType.SNOW, 4.0)
        delay = calculate_segment_delay([cond], 50.0, RoadType.MOUNTAIN, 200, dt)
        assert delay == 28.35

    def test_high_altitude_multiplier(self):
        """Moderate rain, 100km, state road, 1200m altitude, normal time:
        8 * 1.0 * 1.6 * 1.0 * 1.0 = 12.8 min."""
        dt = datetime(2026, 2, 10, 14, 0, tzinfo=timezone.utc)
        cond = classify_weather(WeatherType.RAIN, 5.0)
        delay = calculate_segment_delay([cond], 100.0, RoadType.STATE_ROAD, 1200, dt)
        assert delay == 12.8

    def test_multiple_weather_conditions(self):
        """Rain + wind on same segment: delays sum."""
        dt = datetime(2026, 2, 10, 14, 0, tzinfo=timezone.utc)
        rain = classify_weather(WeatherType.RAIN, 5.0)   # moderate: 8 base
        wind = classify_weather(WeatherType.WIND, 50.0)   # strong: 6 base
        # Highway, 200m, normal: (8+6) * 0.8 * 1.0 * 1.0 = 11.2
        delay = calculate_segment_delay([rain, wind], 100.0, RoadType.HIGHWAY, 200, dt)
        assert delay == 11.2
