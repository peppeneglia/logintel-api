"""Tests for the API endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health(self):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data


class TestPredictionEndpoints:
    def _create_prediction(self):
        payload = {
            "origin": {"lat": 45.464, "lon": 9.190},
            "destination": {"lat": 41.902, "lon": 12.496},
            "departure_time": "2026-02-15T08:00:00+01:00",
        }
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
