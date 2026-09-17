"""
Tests for special route elements.

Covers special element modifiers, segment proximity filtering,
Overpass service, and heuristics integration.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    _osm_cache_key,
    _parse_overpass_response,
    _sample_polyline,
    get_special_elements,
)

# ── Helpers ───────────────────────────────────────────────────────


def _make_condition(wtype: WeatherType, severity: Severity) -> WeatherCondition:
    return WeatherCondition(type=wtype, severity=severity, raw_value=10.0, description="test")


def _tunnel(lat: float = 45.0, lon: float = 7.0, length_m: float = 1000.0) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.TUNNEL,
        name="Test Tunnel",
        length_m=length_m,
        lat=lat,
        lon=lon,
    )


def _bridge(lat: float = 45.0, lon: float = 7.0) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.BRIDGE,
        name="Test Bridge",
        lat=lat,
        lon=lon,
    )


def _mountain_pass(lat: float = 46.5, lon: float = 11.0) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.MOUNTAIN_PASS,
        name="Brenner Pass",
        lat=lat,
        lon=lon,
    )


def _urban(lat: float = 45.5, lon: float = 9.2) -> SpecialElement:
    return SpecialElement(
        type=SpecialElementType.URBAN_CENTER,
        name="Milano",
        lat=lat,
        lon=lon,
    )


# ── TestSpecialElementModifiers ──────────────────────────────────


class TestSpecialElementModifiers:
    SEGMENT_KM = 50.0

    def _modifier(self, elements, conditions):
        return compute_special_element_modifier(elements, conditions, segment_km=self.SEGMENT_KM)

    def test_tunnel_shields_covered_share(self):
        """A 1 km tunnel on a 50 km segment removes 2% of the weather delay."""
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, t_mult, factors = self._modifier([_tunnel(length_m=1000.0)], conditions)
        assert w_mult[WeatherType.RAIN] == pytest.approx(0.98)
        assert t_mult == 1.0
        assert any(f.element_type == SpecialElementType.TUNNEL for f in factors)

    def test_tunnel_longer_than_segment_annuls_weather(self):
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        w_mult, _, _ = self._modifier([_tunnel(length_m=60_000.0)], conditions)
        assert w_mult[WeatherType.SNOW] == 0.0

    def test_tunnel_without_length_is_ignored(self):
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        tunnel = SpecialElement(type=SpecialElementType.TUNNEL, name="Unknown", lat=45.0, lon=7.0)
        w_mult, _, factors = self._modifier([tunnel], conditions)
        assert w_mult == {}
        assert factors == []

    def test_bridge_amplifies_wind(self):
        conditions = [_make_condition(WeatherType.WIND, Severity.HEAVY)]
        w_mult, t_mult, _ = self._modifier([_bridge()], conditions)
        assert w_mult[WeatherType.WIND] == ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]
        assert t_mult == 1.0

    def test_bridge_no_effect_on_rain(self):
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, _, factors = self._modifier([_bridge()], conditions)
        assert w_mult == {}
        assert factors == []

    def test_bridge_does_not_amplify_rain_alongside_wind(self):
        conditions = [
            _make_condition(WeatherType.WIND, Severity.HEAVY),
            _make_condition(WeatherType.RAIN, Severity.HEAVY),
        ]
        w_mult, _, _ = self._modifier([_bridge()], conditions)
        assert w_mult[WeatherType.WIND] == ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]
        assert WeatherType.RAIN not in w_mult

    def test_bridge_amplifies_snow(self):
        conditions = [_make_condition(WeatherType.SNOW, Severity.MODERATE)]
        w_mult, _, _ = self._modifier([_bridge()], conditions)
        assert w_mult[WeatherType.SNOW] == ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]

    def test_mountain_pass_amplifies_snow(self):
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        w_mult, t_mult, _ = self._modifier([_mountain_pass()], conditions)
        assert w_mult[WeatherType.SNOW] == ELEMENT_MULTIPLIERS[SpecialElementType.MOUNTAIN_PASS]
        assert t_mult == 1.0

    def test_mountain_pass_no_effect_without_snow(self):
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, _, _ = self._modifier([_mountain_pass()], conditions)
        assert w_mult == {}

    def test_urban_center_amplifies_time(self):
        conditions = [_make_condition(WeatherType.RAIN, Severity.LIGHT)]
        w_mult, t_mult, _ = self._modifier([_urban()], conditions)
        assert w_mult == {}
        assert t_mult == ELEMENT_MULTIPLIERS[SpecialElementType.URBAN_CENTER]

    def test_tunnel_combines_with_bridge(self):
        """Bridge amplification applies to the part of the segment not covered by tunnels."""
        conditions = [_make_condition(WeatherType.WIND, Severity.HEAVY)]
        w_mult, _, _ = self._modifier([_tunnel(length_m=5000.0), _bridge()], conditions)
        expected = ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE] * 0.9
        assert w_mult[WeatherType.WIND] == pytest.approx(expected)

    def test_multiple_types_combine(self):
        """Bridge + mountain pass + urban should combine weather and time multipliers."""
        elements = [_bridge(), _mountain_pass(), _urban()]
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        w_mult, t_mult, _ = self._modifier(elements, conditions)
        expected_w = (
            ELEMENT_MULTIPLIERS[SpecialElementType.BRIDGE]
            * ELEMENT_MULTIPLIERS[SpecialElementType.MOUNTAIN_PASS]
        )
        assert w_mult[WeatherType.SNOW] == pytest.approx(expected_w)
        assert t_mult == ELEMENT_MULTIPLIERS[SpecialElementType.URBAN_CENTER]

    def test_no_elements_no_modification(self):
        conditions = [_make_condition(WeatherType.RAIN, Severity.HEAVY)]
        w_mult, t_mult, factors = self._modifier([], conditions)
        assert w_mult == {}
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

    def test_element_near_segment_end_included(self):
        """Elements far from the midpoint but close to the segment itself are included."""
        start = Coordinate(lat=45.0, lon=7.0)
        end = Coordinate(lat=45.45, lon=7.0)  # ~50 km segment
        el = _tunnel(lat=45.43, lon=7.01)
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
    def test_sample_polyline_short(self):
        """Polyline shorter than max_points should be returned as-is."""
        polyline = [
            Coordinate(lat=45.0, lon=7.0),
            Coordinate(lat=46.0, lon=8.0),
        ]
        sampled = _sample_polyline(polyline, max_points=40)
        assert len(sampled) == 2

    def test_sample_polyline_long(self):
        """Long polyline should be evenly sampled down."""
        polyline = [Coordinate(lat=45.0 + i * 0.01, lon=7.0) for i in range(100)]
        sampled = _sample_polyline(polyline, max_points=10)
        assert len(sampled) == 10
        # First and last should be preserved
        assert sampled[0].lat == polyline[0].lat
        assert sampled[-1].lat == polyline[-1].lat

    def test_cache_key_stability(self):
        """Same polyline should always produce the same cache key."""
        polyline = [
            Coordinate(lat=45.0, lon=7.0),
            Coordinate(lat=46.0, lon=8.0),
        ]
        k1 = _osm_cache_key(polyline)
        k2 = _osm_cache_key(polyline)
        assert k1 == k2
        assert k1.startswith("osm:corridor:")

    def test_build_query_uses_around(self):
        """Query should use 'around' filter instead of bbox."""
        polyline = [
            Coordinate(lat=45.0, lon=7.0),
            Coordinate(lat=46.0, lon=8.0),
        ]
        query = _build_overpass_query(polyline)
        assert "around:" in query
        assert "45.0,7.0" in query
        assert "46.0,8.0" in query

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
                    "lat": 47.0,
                    "lon": 11.5,
                },
                {
                    "type": "node",
                    "tags": {"place": "city", "name": "Milano"},
                    "lat": 45.5,
                    "lon": 9.2,
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
        from app.services.overpass import _mem_cache

        init_client()

        polyline = [Coordinate(lat=45.0, lon=7.0), Coordinate(lat=46.0, lon=8.0)]

        # Clear in-memory cache to force API call
        _mem_cache.clear()

        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "way",
                            "tags": {"tunnel": "yes", "name": "Frejus", "length": "12900"},
                            "center": {"lat": 45.15, "lon": 6.67},
                        },
                    ]
                },
            )
        )

        result = await get_special_elements(polyline, base_url="https://overpass-api.de")
        assert len(result) == 1
        assert result[0].type == SpecialElementType.TUNNEL
        assert result[0].name == "Frejus"

    @pytest.mark.asyncio
    async def test_get_special_elements_cache_hit(self):
        """Should return cached elements without calling API."""
        polyline = [Coordinate(lat=45.0, lon=7.0), Coordinate(lat=46.0, lon=8.0)]
        cached_data = [{"type": "bridge", "name": "Cached Bridge", "lat": 45.5, "lon": 7.5, "length_m": None}]

        with patch("app.services.overpass.cache_get", new_callable=AsyncMock, return_value=cached_data):
            result = await get_special_elements(polyline)
            assert len(result) == 1
            assert result[0].type == SpecialElementType.BRIDGE

    @pytest.mark.asyncio
    async def test_get_special_elements_memory_cache_hit(self):
        """Should return from in-memory cache when Redis misses."""
        from app.services.overpass import _mem_cache

        polyline = [Coordinate(lat=45.0, lon=7.0), Coordinate(lat=46.0, lon=8.0)]
        cache_key = _osm_cache_key(polyline)
        _mem_cache[cache_key] = [
            {"type": "tunnel", "name": "Mem Tunnel", "lat": 45.1, "lon": 7.1, "length_m": 2000.0}
        ]

        with patch("app.services.overpass.cache_get", new_callable=AsyncMock, return_value=None):
            result = await get_special_elements(polyline)
            assert len(result) == 1
            assert result[0].type == SpecialElementType.TUNNEL
            assert result[0].name == "Mem Tunnel"

        # Cleanup
        del _mem_cache[cache_key]


# ── TestHeuristicsWithSpecialElements ────────────────────────────


class TestHeuristicsWithSpecialElements:
    def test_delay_with_full_tunnel_coverage_is_zero(self):
        """A zero weather multiplier should eliminate the weather delay."""
        conditions = [_make_condition(WeatherType.SNOW, Severity.HEAVY)]
        delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=500.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
            special_element_weather_multipliers={WeatherType.SNOW: 0.0},
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
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
        )
        bridge_delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
            special_element_weather_multipliers={WeatherType.WIND: 1.3},
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
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
        )
        delay_explicit = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
            special_element_weather_multipliers={},
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
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
        )
        urban_delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=50.0,
            road_type=RoadType.HIGHWAY,
            altitude_m=100.0,
            arrival_time=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
            special_element_time_multiplier=1.3,
        )
        assert urban_delay > base_delay
