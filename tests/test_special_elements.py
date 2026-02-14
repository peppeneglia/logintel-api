"""
Tests for Block 7A: Special Route Elements (FRD 6.4).

Covers special element modifiers, segment proximity filtering,
Overpass service, and heuristics integration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.engine.heuristics import calculate_segment_delay
from app.engine.special_elements import (
    ELEMENT_MULTIPLIERS,
    compute_special_element_modifier,
    find_elements_for_segment,
)
from app.models.schemas import (
    Coordinate,
    RoadType,
    Severity,
    SpecialElement,
    SpecialElementType,
    WeatherCondition,
    WeatherType,
)
from app.services.overpass import (
    _build_overpass_query,
    _compute_route_bbox,
    _osm_cache_key,
    _parse_overpass_response,
    get_special_elements,
)


# ── Helpers ───────────────────────────────────────────────────────


def _make_condition(wtype: WeatherType, severity: Severity) -> WeatherCondition:
    return WeatherCondition(
        type=wtype, severity=severity, raw_value=10.0, description="test"
    )


def _tunnel(lat: float = 45.0, lon: float = 7.0, length_m: float = 1000.0) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.TUNNEL, name="Test Tunnel",
        length_m=length_m, lat=lat, lon=lon,
    )


def _bridge(lat: float = 45.0, lon: float = 7.0) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.BRIDGE, name="Test Bridge",
        lat=lat, lon=lon,
    )


def _mountain_pass(lat: float = 46.5, lon: float = 11.0) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.MOUNTAIN_PASS, name="Brenner Pass",
        lat=lat, lon=lon,
    )


def _urban(lat: float = 45.5, lon: float = 9.2) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.URBAN_CENTER, name="Milano",
        lat=lat, lon=lon,
    )


# ── TestSpecialElementModifiers ──────────────────────────────────


class TestSpecialElementModifiers:
    def test_tunnel_annuls_weather(self):
        elements = [_tunnel()]
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == 0.0
        assert t_mult == 1.0
        assert any(f.element_type == SpecialElementType.TUNNEL for f in factors)

    def test_bridge_amplifies_wind(self):
        elements = [_bridge()]
        conditions = [_make_condition(WeatherType.WIND, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]
        assert t_mult == 1.0

    def test_bridge_no_effect_on_rain(self):
        elements = [_bridge()]
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == 1.0  # No amplification for rain
        assert len(factors) == 0  # No factor added

    def test_bridge_amplifies_snow(self):
        elements = [_bridge()]
        conditions = [_make_condition(WeatherType.SNOW, Severity.MODERATE)]
        w_mult, _, _ = compute_special_element_modifier(elements, conditions)
        assert w_mult == ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]

    def test_mountain_pass_amplifies_snow(self):
        elements = [_mountain_pass()]
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == ELEMENT_MULTIPLIERS[SpecialElementType.MOUNTAIN_PASS]
        assert t_mult == 1.0

    def test_mountain_pass_no_effect_without_snow(self):
        elements = [_mountain_pass()]
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, _, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == 1.0

    def test_urban_center_amplifies_time(self):
        elements = [_urban()]
        conditions = [_make_condition(WeatherType.RAIN, Severity.LIGHT)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == 1.0
        assert t_mult == ELEMENT_MULTIPLIERS[SpecialElementType.URBAN_CENTER]

    def test_tunnel_overrides_other_elements(self):
        """Tunnel takes absolute precedence — weather_multiplier = 0 even with bridge."""
        elements = [_tunnel(), _bridge()]
        conditions = [_make_condition(WeatherType.WIND, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        assert w_mult == 0.0

    def test_multiple_types_combine(self):
        """Bridge + mountain pass + urban should combine weather and time multipliers."""
        elements = [_bridge(), _mountain_pass(), _urban()]
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier(elements, conditions)
        expected_w = (
            ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]
            * ELEMENT_MULTIPLIERS[SpecialElementType.MOUNTAIN_PASS]
        )
        assert w_mult == pytest.approx(expected_w)
        assert t_mult == ELEMENT_MULTIPLIERS[SpecialElementType.URBAN_CENTER]

    def test_no_elements_no_modification(self):
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, t_mult, factors = compute_special_element_modifier([], conditions)
        assert w_mult == 1.0
        assert t_mult == 1.0
        assert factors == []


# ── TestFindElementsForSegment ───────────────────────────────────


class TestFindElementsForSegment:
    def test_element_near_segment_included(self):
        start = Coordinate(lat=45.0, lon=7.0)
        end = Coordinate(lat=45.01, lon=7.01)
        # Element at midpoint
        el = _tunnel(lat=45.005, lon=7.005)
        result = find_elements_for_segment(start, end, [el], proximity_km=5.0)
        assert len(result) == 1

    def test_element_far_from_segment_excluded(self):
        start = Coordinate(lat=45.0, lon=7.0)
        end = Coordinate(lat=45.01, lon=7.01)
        # Element far away
        el = _tunnel(lat=46.0, lon=8.0)
        result = find_elements_for_segment(start, end, [el], proximity_km=5.0)
        assert len(result) == 0


# ── TestOverpassService ──────────────────────────────────────────


class TestOverpassService:
    def test_bbox_computation(self):
        polyline = [
            Coordinate(lat=45.0, lon=7.0),
            Coordinate(lat=46.0, lon=8.0),
        ]
        s, w, n, e = _compute_route_bbox(polyline, padding_deg=0.05)
        assert s == pytest.approx(44.95)
        assert w == pytest.approx(6.95)
        assert n == pytest.approx(46.05)
        assert e == pytest.approx(8.05)

    def test_parse_overpass_response_tunnel_and_bridge(self):
        data = {
            "elements": [
                {
                    "type": "way",
                    "tags": {"tunnel": "yes", "name": "Frejus", "length": "12900"},
                    "center": {"lat": 45.15, "lon": 6.67},
                },
                {
                    "type": "way",
                    "tags": {"bridge": "yes", "name": "Viadotto"},
                    "center": {"lat": 45.2, "lon": 7.1},
                },
                {
                    "type": "way",
                    "tags": {"tunnel": "yes", "length": "200"},  # < 500m, should be filtered
                    "center": {"lat": 45.3, "lon": 7.2},
                },
            ]
        }
        elements = _parse_overpass_response(data)
        assert len(elements) == 2
        types = {e.type for e in elements}
        assert SpecialElementType.TUNNEL in types
        assert SpecialElementType.BRIDGE in types

    def test_parse_mountain_pass_and_urban(self):
        data = {
            "elements": [
                {
                    "type": "node",
                    "tags": {"mountain_pass": "yes", "name": "Brenner"},
                    "lat": 47.0, "lon": 11.5,
                },
                {
                    "type": "node",
                    "tags": {"place": "city", "name": "Milano"},
                    "lat": 45.5, "lon": 9.2,
                },
            ]
        }
        elements = _parse_overpass_response(data)
        assert len(elements) == 2
        types = {e.type for e in elements}
        assert SpecialElementType.MOUNTAIN_PASS in types
        assert SpecialElementType.URBAN_CENTER in types

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_special_elements_fallback_on_error(self):
        """Should return empty list when Overpass API fails."""
        from app.services.http_client import init_client
        init_client()

        polyline = [Coordinate(lat=45.0, lon=7.0), Coordinate(lat=46.0, lon=8.0)]

        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(500, text="Server Error")
        )

        result = await get_special_elements(polyline, base_url="https://overpass-api.de")
        assert result == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_special_elements_success(self):
        """Should parse Overpass response correctly."""
        from app.services.http_client import init_client
        init_client()

        polyline = [Coordinate(lat=45.0, lon=7.0), Coordinate(lat=46.0, lon=8.0)]

        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(200, json={
                "elements": [
                    {
                        "type": "way",
                        "tags": {"tunnel": "yes", "name": "Frejus", "length": "12900"},
                        "center": {"lat": 45.15, "lon": 6.67},
                    },
                ]
            })
        )

        result = await get_special_elements(polyline, base_url="https://overpass-api.de")
        assert len(result) == 1
        assert result[0].type == SpecialElementType.TUNNEL
        assert result[0].name == "Frejus"

    @pytest.mark.asyncio
    async def test_get_special_elements_cache_hit(self):
        """Should return cached elements without calling API."""
        polyline = [Coordinate(lat=45.0, lon=7.0), Coordinate(lat=46.0, lon=8.0)]
        cached_data = [
            {"type": "bridge", "name": "Cached Bridge", "lat": 45.5, "lon": 7.5, "length_m": None}
        ]

        with patch("app.services.overpass.cache_get", new_callable=AsyncMock, return_value=cached_data):
            result = await get_special_elements(polyline)
            assert len(result) == 1
            assert result[0].type == SpecialElementType.BRIDGE


# ── TestHeuristicsWithSpecialElements ────────────────────────────


class TestHeuristicsWithSpecialElements:
    def test_delay_with_tunnel_is_zero(self):
        """Tunnel weather multiplier = 0.0 should eliminate all weather delay."""
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=500.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            special_element_weather_multiplier=0.0,
        )
        assert delay == 0.0

    def test_delay_with_bridge_increases(self):
        """Bridge should increase delay for wind conditions."""
        conditions = [_make_condition(WeatherType.WIND, Severity.HEAVY)]
        base_delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
        )
        bridge_delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            special_element_weather_multiplier=1.3,
        )
        assert bridge_delay > base_delay
        assert bridge_delay == pytest.approx(base_delay * 1.3, abs=0.01)

    def test_default_params_unchanged(self):
        """Default multipliers (1.0) should produce the same result as before."""
        conditions = [_make_condition(WeatherType.RAIN, Severity.MODERATE)]
        delay_default = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
        )
        delay_explicit = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            special_element_weather_multiplier=1.0,
            special_element_time_multiplier=1.0,
        )
        assert delay_default == delay_explicit

    def test_urban_center_time_multiplier(self):
        """Urban center time multiplier should increase delay."""
        conditions = [_make_condition(WeatherType.RAIN, Severity.MODERATE)]
        base_delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
        )
        urban_delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            special_element_time_multiplier=1.3,
        )
        assert urban_delay > base_delay
