"""Tests for the API endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import (
    ConfidenceComponents,
    ConfidenceLevel,
    ConfidenceScore,
    Coordinate,
    PredictionResponse,
    RoadType,
    SegmentDetail,
    SegmentFactors,
)

client = TestClient(app)


def _make_stub_prediction(
    origin: Coordinate | None = None,
    destination: Coordinate | None = None,
    departure_time: datetime | None = None,
) -> PredictionResponse:
    """Create a fake PredictionResponse for mocking build_prediction."""
    if origin is None:
        origin = Coordinate(lat=45.464, lon=9.190)
    if destination is None:
        destination = Coordinate(lat=41.902, lon=12.496)
    if departure_time is None:
        departure_time = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)

    segment = SegmentDetail(
        index=0,
        start_point=origin,
        end_point=destination,
        length_km=477.0,
        estimated_arrival=departure_time,
        weather=[],
        factors=SegmentFactors(
            road_type=RoadType.HIGHWAY,
            road_factor=0.8,
            altitude_m=200.0,
            altitude_factor=1.0,
            time_factor=1.0,
            calibration_factor=1.0,
        ),
        delay_minutes=0.0,
    )

    confidence = ConfidenceScore(
        overall=85.0,
        level=ConfidenceLevel.HIGH,
        components=ConfidenceComponents(
            time_horizon=95.0,
            weather_stability=90.0,
            historical_accuracy=70.0,
            data_completeness=100.0,
        ),
    )

    return PredictionResponse(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        total_delay_minutes=0.0,
        confidence=confidence,
        segments=[segment],
    )


class TestHealthEndpoint:
    def test_health(self):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data


class TestPredictionEndpoints:
    def _create_prediction(self, departure_time: str = "2026-02-15T08:00:00+01:00"):
        payload = {
            "origin": {"lat": 45.464, "lon": 9.190},
            "destination": {"lat": 41.902, "lon": 12.496},
            "departure_time": departure_time,
        }
        stub = _make_stub_prediction(
            departure_time=datetime.fromisoformat(departure_time),
        )
        with patch(
            "app.routes.predictions.build_prediction",
            new_callable=AsyncMock,
            return_value=stub,
        ):
            return client.post("/v1/predictions", json=payload)

    def test_create_prediction(self):
        resp = self._create_prediction()
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "completed"
        assert "total_delay_minutes" in data
        assert "confidence" in data
        assert "segments" in data
        assert len(data["segments"]) > 0

    def test_get_prediction(self):
        create_resp = self._create_prediction()
        pred_id = create_resp.json()["id"]

        resp = client.get(f"/v1/predictions/{pred_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == pred_id

    def test_get_prediction_not_found(self):
        resp = client.get("/v1/predictions/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "NOT_FOUND"

    def test_list_predictions(self):
        self._create_prediction()
        resp = client.get("/v1/predictions")
        assert resp.status_code == 200
        data = resp.json()
        assert "predictions" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_submit_feedback(self):
        create_resp = self._create_prediction()
        pred_id = create_resp.json()["id"]

        feedback_payload = {"actual_delay_minutes": 15, "notes": "Moderate delay"}
        resp = client.post(f"/v1/predictions/{pred_id}/feedback", json=feedback_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["prediction_id"] == pred_id
        assert data["actual_delay_minutes"] == 15

    def test_duplicate_feedback_rejected(self):
        create_resp = self._create_prediction()
        pred_id = create_resp.json()["id"]

        payload = {"actual_delay_minutes": 10}
        client.post(f"/v1/predictions/{pred_id}/feedback", json=payload)
        resp = client.post(f"/v1/predictions/{pred_id}/feedback", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_feedback_rejected_after_7_days(self):
        """Feedback for predictions with departure > 7 days ago should be rejected."""
        # Create a prediction with departure_time 10 days in the past
        old_departure = datetime.now(timezone.utc) - timedelta(days=10)
        old_departure_str = old_departure.isoformat()

        create_resp = self._create_prediction(departure_time=old_departure_str)
        pred_id = create_resp.json()["id"]

        resp = client.post(
            f"/v1/predictions/{pred_id}/feedback",
            json={"actual_delay_minutes": 5},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"
        assert "expired" in resp.json()["error"]["message"].lower()


class TestValidationErrors:
    def test_missing_origin(self):
        payload = {
            "destination": {"lat": 41.902, "lon": 12.496},
            "departure_time": "2026-02-15T08:00:00+01:00",
        }
        resp = client.post("/v1/predictions", json=payload)
        assert resp.status_code == 400 or resp.status_code == 422

    def test_invalid_coordinates(self):
        payload = {
            "origin": {"lat": 200, "lon": 9.190},
            "destination": {"lat": 41.902, "lon": 12.496},
            "departure_time": "2026-02-15T08:00:00+01:00",
        }
        resp = client.post("/v1/predictions", json=payload)
        assert resp.status_code == 400 or resp.status_code == 422

    def test_departure_without_timezone(self):
        payload = {
            "origin": {"lat": 45.464, "lon": 9.190},
            "destination": {"lat": 41.902, "lon": 12.496},
            "departure_time": "2026-02-15T08:00:00",
        }
        resp = client.post("/v1/predictions", json=payload)
        assert resp.status_code == 400 or resp.status_code == 422

    def test_feedback_out_of_range(self):
        resp = client.post(
            "/v1/predictions/some-id/feedback",
            json={"actual_delay_minutes": 9999},
        )
        assert resp.status_code == 400 or resp.status_code == 422
