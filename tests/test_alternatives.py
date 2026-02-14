"""Tests for alternative routes (Block 6)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

import app.services.cache as _cache_mod
import app.services.http_client as _hc_mod
from app.models.schemas import (
    AlternativeRoute,
    ConfidenceComponents,
    ConfidenceLevel,
    ConfidenceScore,
    Coordinate,
    PredictionResponse,
    RoadType,
    SegmentDetail,
    SegmentFactors,
)
from app.services.ors import (
    RouteResult,
    _routes_to_dicts,
    compute_route_hash,
    get_routes,
)
from app.services.prediction import (
    _build_alternative_summary,
    _build_alternatives,
    build_prediction,
)


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


# ─── Shared test data ──────────────────────────────────────────────────

ORS_MAIN_ROUTE = {
    "summary": {"duration": 18000.0, "distance": 500000.0},
    "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
    "extras": {"waytypes": {"values": [[0, 2, 0]]}},
}

ORS_ALT_ROUTE = {
    "summary": {"duration": 20000.0, "distance": 550000.0},
    "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
    "extras": {"waytypes": {"values": [[0, 2, 1]]}},
}

OPEN_METEO_RESPONSE = {
    "hourly": {
        "time": ["2026-02-15T08:00", "2026-02-15T09:00", "2026-02-15T10:00"],
        "precipitation": [0.0, 0.0, 0.0],
        "snowfall": [0.0, 0.0, 0.0],
        "wind_speed_10m": [10.0, 10.0, 10.0],
        "visibility": [10000.0, 10000.0, 10000.0],
    }
}

# Heavy rain response — generates delay > 20 min threshold
OPEN_METEO_HEAVY_RAIN = {
    "hourly": {
        "time": ["2026-02-15T08:00", "2026-02-15T09:00", "2026-02-15T10:00"],
        "precipitation": [20.0, 20.0, 20.0],
        "snowfall": [0.0, 0.0, 0.0],
        "wind_speed_10m": [10.0, 10.0, 10.0],
        "visibility": [10000.0, 10000.0, 10000.0],
    }
}

ELEVATION_RESPONSE = {
    "results": [
        {"latitude": 38.5, "longitude": -120.2, "elevation": 150.0},
        {"latitude": 40.7, "longitude": -120.95, "elevation": 450.0},
        {"latitude": 43.25, "longitude": -126.45, "elevation": 120.0},
    ]
}


# ─── get_routes tests ──────────────────────────────────────────────────

class TestGetRoutes:
    @pytest.mark.asyncio
    async def test_get_routes_without_alternatives(self):
        """Without alternatives flag, returns a list of 1 route."""
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(200, json={"routes": [ORS_MAIN_ROUTE]})
            )
            result = await get_routes(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                include_alternatives=False,
            )
            assert len(result) == 1
            assert isinstance(result[0], RouteResult)
            assert result[0].duration_s == 18000.0

    @pytest.mark.asyncio
    async def test_get_routes_with_alternatives(self):
        """With alternatives flag, ORS returns multiple routes."""
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(
                    200, json={"routes": [ORS_MAIN_ROUTE, ORS_ALT_ROUTE]}
                )
            )
            result = await get_routes(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                include_alternatives=True,
            )
            assert len(result) == 2
            assert result[0].duration_s == 18000.0
            assert result[1].duration_s == 20000.0
            assert result[1].distance_m == 550000.0

    @pytest.mark.asyncio
    async def test_get_routes_alternatives_cached(self):
        """Alternative routes cache hit should avoid ORS call."""
        import fakeredis.aioredis

        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        _cache_mod._redis = fake

        origin = Coordinate(lat=45.464, lon=9.190)
        dest = Coordinate(lat=41.902, lon=12.496)
        route_hash = compute_route_hash(origin, dest)

        # Pre-populate cache with alternatives
        routes = [
            RouteResult(
                polyline=[origin, dest],
                duration_s=18000.0,
                distance_m=500000.0,
                road_types=[(0.0, 1.0, RoadType.HIGHWAY)],
            ),
            RouteResult(
                polyline=[origin, dest],
                duration_s=20000.0,
                distance_m=550000.0,
                road_types=[(0.0, 1.0, RoadType.STATE_ROAD)],
            ),
        ]
        await _cache_mod.cache_set(
            f"route:{route_hash}:alt", _routes_to_dicts(routes), ttl=86400
        )

        with respx.mock:
            ors_route = respx.post(
                "https://api.openrouteservice.org/v2/directions/driving-hgv"
            )
            ors_route.mock(return_value=httpx.Response(500, text="should not be called"))

            result = await get_routes(origin, dest, include_alternatives=True)

            assert not ors_route.called
            assert len(result) == 2
            assert result[0].duration_s == 18000.0
            assert result[1].duration_s == 20000.0

        await fake.aclose()

    @pytest.mark.asyncio
    async def test_get_routes_single_route_when_no_viable_alt(self):
        """ORS may return only 1 route even when alternatives requested."""
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(200, json={"routes": [ORS_MAIN_ROUTE]})
            )
            result = await get_routes(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                include_alternatives=True,
            )
            assert len(result) == 1


# ─── build_prediction alternatives tests ───────────────────────────────

class TestBuildPredictionAlternatives:
    @pytest.mark.asyncio
    async def test_build_prediction_no_alternatives_default(self):
        """Without include_alternatives, alternatives is empty list."""
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(200, json={"routes": [ORS_MAIN_ROUTE]})
            )
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(200, json=OPEN_METEO_RESPONSE)
            )
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(200, json=ELEVATION_RESPONSE)
            )

            departure = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
            result = await build_prediction(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                departure,
            )
            assert result.alternatives == []

    @pytest.mark.asyncio
    async def test_build_prediction_below_threshold(self):
        """Delay below threshold → alternatives empty even with flag."""
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(
                    200, json={"routes": [ORS_MAIN_ROUTE, ORS_ALT_ROUTE]}
                )
            )
            # Clear weather → low/zero delay
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(200, json=OPEN_METEO_RESPONSE)
            )
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(200, json=ELEVATION_RESPONSE)
            )

            departure = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
            result = await build_prediction(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                departure,
                include_alternatives=True,
            )
            # Clear weather = 0 delay < 20 min threshold
            assert result.total_delay_minutes <= 20
            assert result.alternatives == []

    @pytest.mark.asyncio
    async def test_build_prediction_above_threshold(self):
        """Delay above threshold → alternatives populated."""
        with respx.mock:
            respx.post("https://api.openrouteservice.org/v2/directions/driving-hgv").mock(
                return_value=httpx.Response(
                    200, json={"routes": [ORS_MAIN_ROUTE, ORS_ALT_ROUTE]}
                )
            )
            # Heavy rain → delay > 20 min
            respx.get("https://api.open-meteo.com/v1/forecast").mock(
                return_value=httpx.Response(200, json=OPEN_METEO_HEAVY_RAIN)
            )
            respx.post("https://api.open-elevation.com/api/v1/lookup").mock(
                return_value=httpx.Response(200, json=ELEVATION_RESPONSE)
            )

            departure = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
            result = await build_prediction(
                Coordinate(lat=45.464, lon=9.190),
                Coordinate(lat=41.902, lon=12.496),
                departure,
                include_alternatives=True,
            )
            assert result.total_delay_minutes > 20
            assert len(result.alternatives) == 1
            alt = result.alternatives[0]
            assert alt.route_index == 1
            assert alt.distance_km > 0
            assert alt.duration_minutes > 0


# ─── Helper tests ──────────────────────────────────────────────────────

class TestAlternativeHelpers:
    def test_delay_savings_calculation(self):
        """delay_savings = main_delay - alt_delay."""
        alt = AlternativeRoute(
            route_index=1,
            total_delay_minutes=15.0,
            duration_minutes=300.0,
            distance_km=500.0,
            delay_savings_minutes=30.0,
            summary="test",
        )
        assert alt.delay_savings_minutes == 30.0
        # Verify the math: if main=45, alt=15 → savings=30
        main_delay = alt.total_delay_minutes + alt.delay_savings_minutes
        assert main_delay == 45.0

    def test_alternative_summary_with_savings(self):
        """Summary includes savings info when positive."""
        route = RouteResult(
            polyline=[Coordinate(lat=45.0, lon=9.0), Coordinate(lat=42.0, lon=12.0)],
            duration_s=19800.0,
            distance_m=520000.0,
            road_types=[(0.0, 1.0, RoadType.HIGHWAY)],
        )
        summary = _build_alternative_summary(1, 15.0, route)
        assert "Alternative 1" in summary
        assert "520.0 km" in summary
        assert "saves 15 min" in summary

    def test_alternative_summary_no_improvement(self):
        """Summary says 'no delay improvement' when savings <= 0."""
        route = RouteResult(
            polyline=[Coordinate(lat=45.0, lon=9.0), Coordinate(lat=42.0, lon=12.0)],
            duration_s=19800.0,
            distance_m=520000.0,
            road_types=[(0.0, 1.0, RoadType.HIGHWAY)],
        )
        summary = _build_alternative_summary(1, -5.0, route)
        assert "no delay improvement" in summary


# ─── Backward compatibility ────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_prediction_response_backward_compatible(self):
        """PredictionResponse without alternatives → default empty list."""
        resp = PredictionResponse(
            origin=Coordinate(lat=45.0, lon=9.0),
            destination=Coordinate(lat=42.0, lon=12.0),
            departure_time=datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc),
            total_delay_minutes=10.0,
            confidence=ConfidenceScore(
                overall=85.0,
                level=ConfidenceLevel.HIGH,
                components=ConfidenceComponents(
                    time_horizon=95.0,
                    weather_stability=90.0,
                    historical_accuracy=70.0,
                    data_completeness=100.0,
                ),
            ),
            segments=[],
        )
        assert resp.alternatives == []
        # Verify JSON serialization includes the field
        data = resp.model_dump()
        assert "alternatives" in data
        assert data["alternatives"] == []


# ─── API integration test ──────────────────────────────────────────────

class TestCreatePredictionApiWithAlternatives:
    def test_create_prediction_api_with_alternatives(self):
        """POST /v1/predictions with include_alternatives returns alternatives in JSON."""
        from fastapi.testclient import TestClient
        from app.main import app

        test_client = TestClient(app)

        stub = PredictionResponse(
            origin=Coordinate(lat=45.464, lon=9.190),
            destination=Coordinate(lat=41.902, lon=12.496),
            departure_time=datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc),
            total_delay_minutes=45.0,
            confidence=ConfidenceScore(
                overall=80.0,
                level=ConfidenceLevel.GOOD,
                components=ConfidenceComponents(
                    time_horizon=90.0,
                    weather_stability=75.0,
                    historical_accuracy=70.0,
                    data_completeness=100.0,
                ),
            ),
            segments=[],
            alternatives=[
                AlternativeRoute(
                    route_index=1,
                    total_delay_minutes=25.0,
                    duration_minutes=330.0,
                    distance_km=550.0,
                    delay_savings_minutes=20.0,
                    summary="Alternative 1: 550.0 km, 330 min base travel, saves 20 min delay",
                ),
            ],
        )

        with patch(
            "app.routes.predictions.build_prediction",
            new_callable=AsyncMock,
            return_value=stub,
        ):
            resp = test_client.post(
                "/v1/predictions",
                json={
                    "origin": {"lat": 45.464, "lon": 9.190},
                    "destination": {"lat": 41.902, "lon": 12.496},
                    "departure_time": "2026-02-15T08:00:00+00:00",
                    "include_alternatives": True,
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert "alternatives" in data
        assert len(data["alternatives"]) == 1
        alt = data["alternatives"][0]
        assert alt["route_index"] == 1
        assert alt["delay_savings_minutes"] == 20.0
        assert alt["summary"].startswith("Alternative 1")
