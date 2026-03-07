"""Tests for external service integrations (Block 2 + Block 3 cache)."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

import app.services.http_client as _hc_mod
import app.services.cache as _cache_mod
from app.models.schemas import Coordinate, RoadType, WeatherType
from app.services.http_client import (
    close_client,
    get_client,
    init_client,
    request_with_retry,
)
from app.services.ors import (
    RouteResult,
    _dict_to_route,
    _route_to_dict,
    compute_route_hash,
    decode_polyline,
    get_road_type_at_fraction,
    get_route,
)
from app.services.weather import (
    _classify_point_weather,
    _find_hour_index,
    _weather_cache_key,
    get_weather_at_points,
    _extract_conditions_at_hour,
)
from app.services.elevation import (
    DEFAULT_ELEVATION_M,
    get_elevations,
)
from app.services.prediction import build_prediction


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _manage_http_client():
    """Ensure a fresh httpx client is available for each test."""
    _hc_mod._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    yield
    _hc_mod._client = None


@pytest.fixture(autouse=True)
def _disable_redis():
    """Disable Redis for all tests by default (cache passthrough)."""
    _cache_mod._redis = None
    yield
    _cache_mod._redis = None


# ─── HTTP Client Tests ──────────────────────────────────────────────────

class TestHttpClient:
    def test_get_client_returns_instance(self):
        client = get_client()
        assert isinstance(client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_request_with_retry_success(self):
        with respx.mock:
            respx.get("https://example.com/ok").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            resp = await request_with_retry("GET", "https://example.com/ok")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_request_with_retry_retries_on_500(self):
        with respx.mock:
            route = respx.get("https://example.com/flaky")
            route.side_effect = [
                httpx.Response(500, text="error"),
                httpx.Response(200, json={"recovered": True}),
            ]
            resp = await request_with_retry(
                "GET", "https://example.com/flaky", retries=2, backoff=0.01
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_request_with_retry_exhausts_retries(self):
        with respx.mock:
            respx.get("https://example.com/down").mock(
                return_value=httpx.Response(503, text="unavailable")
            )
            with pytest.raises(httpx.HTTPStatusError):
                await request_with_retry(
                    "GET", "https://example.com/down", retries=2, backoff=0.01
                )


# ─── ORS Tests ──────────────────────────────────────────────────────────

class TestPolylineDecode:
    def test_decode_simple_polyline(self):
        # Encoded polyline for approximately (38.5, -120.2) → (40.7, -120.95) → (43.252, -126.453)
        encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        points = decode_polyline(encoded)
        assert len(points) == 3
        assert abs(points[0].lat - 38.5) < 0.01
        assert abs(points[0].lon - (-120.2)) < 0.01

    def test_decode_empty_returns_empty(self):
        points = decode_polyline("")
        assert points == []


class TestRouteHash:
    def test_same_coords_same_hash(self):
        o = Coordinate(lat=45.464, lon=9.190)
        d = Coordinate(lat=41.902, lon=12.496)
        h1 = compute_route_hash(o, d)
        h2 = compute_route_hash(o, d)
        assert h1 == h2

    def test_nearby_coords_same_hash(self):
        o1 = Coordinate(lat=45.464, lon=9.190)
        o2 = Coordinate(lat=45.463, lon=9.191)  # ~150m away, same 0.005° grid cell
        d = Coordinate(lat=41.902, lon=12.496)
        h1 = compute_route_hash(o1, d)
        h2 = compute_route_hash(o2, d)
        assert h1 == h2

    def test_distant_coords_different_hash(self):
        o1 = Coordinate(lat=45.464, lon=9.190)
        o2 = Coordinate(lat=44.400, lon=8.900)  # ~120km away
        d = Coordinate(lat=41.902, lon=12.496)
        h1 = compute_route_hash(o1, d)
        h2 = compute_route_hash(o2, d)
        assert h1 != h2


class TestGetRoadTypeAtFraction:
    def test_returns_matching_type(self):
        road_types = [
            (0.0, 0.5, RoadType.HIGHWAY),
            (0.5, 1.0, RoadType.STATE_ROAD),
        ]
        assert get_road_type_at_fraction(road_types, 0.3) == RoadType.HIGHWAY
        assert get_road_type_at_fraction(road_types, 0.7) == RoadType.STATE_ROAD

    def test_defaults_to_highway(self):
        assert get_road_type_at_fraction([], 0.5) == RoadType.HIGHWAY


class TestOrsGetRoute:
    @pytest.mark.asyncio
    async def test_get_route_parses_response(self):
        ors_response = {
            "routes": [{
                "summary": {"duration": 18000.0, "distance": 500000.0},
                "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                "extras": {
                    "waytypes": {
                        "values": [[0, 1, 0], [1, 2, 1]]
                    }
                },
            }]
        }
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(200, json=ors_response)
            )
            result = await get_route(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
            )
            assert isinstance(result, RouteResult)
            assert result.duration_s == 18000.0
            assert result.distance_m == 500000.0
            assert len(result.polyline) == 3

    @pytest.mark.asyncio
    async def test_get_route_raises_on_error(self):
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(403, json={"error": "forbidden"})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await get_route(
                    Coordinate(lat=45.464, lon=9.190),
                    Coordinate(lat=41.902, lon=12.496),
                )


# ─── Weather Tests ──────────────────────────────────────────────────────

class TestWeatherHelpers:
    def test_find_hour_index_match(self):
        hours = ["2026-02-15T06:00", "2026-02-15T07:00", "2026-02-15T08:00"]
        dt = datetime(2026, 2, 15, 8, 30, tzinfo=timezone.utc)
        assert _find_hour_index(hours, dt) == 2

    def test_find_hour_index_no_match(self):
        hours = ["2026-02-15T06:00", "2026-02-15T07:00"]
        dt = datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc)
        assert _find_hour_index(hours, dt) is None

    def test_classify_rain(self):
        conditions = _classify_point_weather(
            precipitation=10.0, snowfall=0.0, wind_speed=20.0, visibility=10000.0
        )
        assert len(conditions) == 1
        assert conditions[0].type == WeatherType.RAIN

    def test_classify_multiple_conditions(self):
        conditions = _classify_point_weather(
            precipitation=5.0, snowfall=2.0, wind_speed=70.0, visibility=100.0
        )
        types = {c.type for c in conditions}
        assert WeatherType.RAIN in types
        assert WeatherType.SNOW in types
        assert WeatherType.WIND in types
        assert WeatherType.FOG in types

    def test_classify_clear_sky(self):
        conditions = _classify_point_weather(
            precipitation=0.0, snowfall=0.0, wind_speed=10.0, visibility=10000.0
        )
        assert conditions == []


class TestWeatherService:
    @pytest.mark.asyncio
    async def test_get_weather_at_points_success(self):
        open_meteo_response = {
            "hourly": {
                "time": ["2026-02-15T08:00"],
                "precipitation": [5.0],
                "snowfall": [0.0],
                "wind_speed_10m": [20.0],
                "visibility": [10000.0],
            }
        }
        with respx.mock:
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(200, json=open_meteo_response)
            )
            points = [Coordinate(lat=45.0, lon=9.0)]
            times = [datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)]
            result = await get_weather_at_points(points, times)
            assert len(result) == 1
            assert len(result[0]) == 1
            assert result[0][0].type == WeatherType.RAIN

    @pytest.mark.asyncio
    async def test_get_weather_fallback_on_error(self):
        with respx.mock:
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(500, text="error")
            )
            points = [Coordinate(lat=45.0, lon=9.0)]
            times = [datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)]
            result = await get_weather_at_points(points, times)
            assert len(result) == 1
            assert result[0] == []  # clear sky fallback


# ─── Elevation Tests ────────────────────────────────────────────────────

class TestElevationService:
    @pytest.mark.asyncio
    async def test_get_elevations_success(self):
        elevation_response = {
            "results": [
                {"latitude": 45.0, "longitude": 9.0, "elevation": 350.0},
                {"latitude": 44.0, "longitude": 10.0, "elevation": 800.0},
            ]
        }
        with respx.mock:
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(200, json=elevation_response)
            )
            points = [
                Coordinate(lat=45.0, lon=9.0),
                Coordinate(lat=44.0, lon=10.0),
            ]
            result = await get_elevations(points)
            assert result == [350.0, 800.0]

    @pytest.mark.asyncio
    async def test_get_elevations_fallback_on_error(self):
        with respx.mock:
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(500, text="error")
            )
            points = [
                Coordinate(lat=45.0, lon=9.0),
                Coordinate(lat=44.0, lon=10.0),
            ]
            result = await get_elevations(points)
            assert result == [DEFAULT_ELEVATION_M, DEFAULT_ELEVATION_M]

    @pytest.mark.asyncio
    async def test_get_elevations_empty_list(self):
        result = await get_elevations([])
        assert result == []


# ─── Orchestrator Tests ─────────────────────────────────────────────────

class TestBuildPrediction:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocks(self):
        """Test the complete orchestration with all external APIs mocked."""
        ors_response = {
            "routes": [{
                "summary": {"duration": 18000.0, "distance": 500000.0},
                "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                "extras": {
                    "waytypes": {"values": [[0, 2, 0]]}
                },
            }]
        }

        open_meteo_response = {
            "hourly": {
                "time": ["2026-02-15T08:00", "2026-02-15T09:00", "2026-02-15T10:00"],
                "precipitation": [3.0, 5.0, 0.0],
                "snowfall": [0.0, 0.0, 0.0],
                "wind_speed_10m": [15.0, 25.0, 10.0],
                "visibility": [10000.0, 5000.0, 10000.0],
            }
        }

        elevation_response = {
            "results": [
                {"latitude": 38.5, "longitude": -120.2, "elevation": 150.0},
                {"latitude": 40.7, "longitude": -120.95, "elevation": 450.0},
                {"latitude": 43.25, "longitude": -126.45, "elevation": 120.0},
            ]
        }

        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(200, json=ors_response)
            )
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(200, json=open_meteo_response)
            )
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(200, json=elevation_response)
            )

            departure = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
            result = await build_prediction(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                departure,
            )

            assert result.status == "completed"
            assert result.total_delay_minutes >= 0
            assert result.confidence.overall > 0
            assert len(result.segments) > 0

    @pytest.mark.asyncio
    async def test_pipeline_with_elevation_failure(self):
        """Pipeline should still work when elevation API fails."""
        ors_response = {
            "routes": [{
                "summary": {"duration": 18000.0, "distance": 500000.0},
                "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                "extras": {
                    "waytypes": {"values": [[0, 2, 0]]}
                },
            }]
        }

        open_meteo_response = {
            "hourly": {
                "time": ["2026-02-15T08:00"],
                "precipitation": [0.0],
                "snowfall": [0.0],
                "wind_speed_10m": [10.0],
                "visibility": [10000.0],
            }
        }

        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(200, json=ors_response)
            )
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(200, json=open_meteo_response)
            )
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(500, text="error")
            )

            departure = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
            result = await build_prediction(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                departure,
            )

            assert result.status == "completed"
            # Segments should use default elevation (200m)
            for seg in result.segments:
                assert seg.factors.altitude_m == DEFAULT_ELEVATION_M


# ─── Cache Integration Tests (Block 3) ────────────────────────────────

class TestOrsCacheHit:
    @pytest.mark.asyncio
    async def test_get_route_returns_cached_result(self):
        """When cache has the route, ORS API should NOT be called."""
        import fakeredis.aioredis

        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        _cache_mod._redis = fake

        # Pre-populate cache with a route
        origin = Coordinate(lat=45.464, lon=9.190)
        dest = Coordinate(lat=41.902, lon=12.496)
        route_hash = compute_route_hash(origin, dest)
        cached_data = {
            "polyline": [
                {"lat": 45.464, "lon": 9.190},
                {"lat": 43.0, "lon": 10.8},
                {"lat": 41.902, "lon": 12.496},
            ],
            "duration_s": 18000.0,
            "distance_m": 500000.0,
            "road_types": [[0.0, 1.0, "highway"]],
        }
        await _cache_mod.cache_set(f"route:{route_hash}", cached_data, ttl=86400)

        # Call get_route — should NOT hit ORS
        with respx.mock:
            ors_route = respx.post(
                "https://api.openrouteservice.org/v2/directions/driving-hgv"
            )
            ors_route.mock(return_value=httpx.Response(500, text="should not be called"))

            result = await get_route(origin, dest)

            assert not ors_route.called
            assert isinstance(result, RouteResult)
            assert result.duration_s == 18000.0
            assert result.distance_m == 500000.0
            assert len(result.polyline) == 3
            assert result.road_types[0][2] == RoadType.HIGHWAY

        await fake.aclose()


class TestWeatherCacheHit:
    @pytest.mark.asyncio
    async def test_weather_returns_cached_result(self):
        """When cache has hourly weather, Open-Meteo should NOT be called."""
        import fakeredis.aioredis

        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        _cache_mod._redis = fake

        point = Coordinate(lat=45.0, lon=9.0)
        arrival = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
        cache_key = _weather_cache_key(point.lat, point.lon, "2026-02-15T08:00")

        # Pre-populate cache with a single-hour slice
        cached_hourly = {
            "time": ["2026-02-15T08:00"],
            "precipitation": [5.0],
            "snowfall": [0.0],
            "wind_speed_10m": [20.0],
            "visibility": [10000.0],
        }
        await _cache_mod.cache_set(cache_key, cached_hourly, ttl=3600)

        with respx.mock:
            meteo_route = respx.get("https://api.open-meteo.com/v1/forecast")
            meteo_route.mock(return_value=httpx.Response(500, text="should not be called"))

            result = await get_weather_at_points([point], [arrival])

            assert not meteo_route.called
            assert len(result) == 1
            assert len(result[0]) == 1
            assert result[0][0].type == WeatherType.RAIN

        await fake.aclose()


class TestRouteResultSerialization:
    def test_roundtrip(self):
        """RouteResult → dict → RouteResult preserves data."""
        original = RouteResult(
            polyline=[
                Coordinate(lat=45.0, lon=9.0),
                Coordinate(lat=44.0, lon=10.0),
            ],
            duration_s=3600.0,
            distance_m=100000.0,
            road_types=[(0.0, 0.5, RoadType.HIGHWAY), (0.5, 1.0, RoadType.STATE_ROAD)],
        )
        d = _route_to_dict(original)
        restored = _dict_to_route(d)
        assert restored.duration_s == original.duration_s
        assert restored.distance_m == original.distance_m
        assert len(restored.polyline) == len(original.polyline)
        assert restored.road_types[1][2] == RoadType.STATE_ROAD
