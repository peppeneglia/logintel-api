"""Tests for the analytics endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import app.stores as stores
from app.main import app
from app.models.schemas import (
    ConfidenceComponents,
    ConfidenceLevel,
    ConfidenceScore,
    Coordinate,
    FeedbackResponse,
    PredictionResponse,
    RoadType,
    SegmentDetail,
    SegmentFactors,
    Severity,
    WeatherCondition,
    WeatherType,
)

client = TestClient(app)


def _make_prediction(
    delay: float = 10.0,
    weather_type: WeatherType = WeatherType.RAIN,
    severity: Severity = Severity.MODERATE,
) -> PredictionResponse:
    origin = Coordinate(lat=45.0, lon=9.0)
    dest = Coordinate(lat=42.0, lon=12.0)
    dep = datetime(2026, 2, 10, 8, 0, tzinfo=UTC)

    segment = SegmentDetail(
        index=0,
        start_point=origin,
        end_point=dest,
        length_km=100.0,
        estimated_arrival=dep,
        weather=[
            WeatherCondition(
                type=weather_type,
                severity=severity,
                raw_value=5.0,
                description="test",
            )
        ],
        factors=SegmentFactors(
            road_type=RoadType.HIGHWAY,
            road_factor=0.8,
            altitude_m=200.0,
            altitude_factor=1.0,
            time_factor=1.0,
        ),
        delay_minutes=delay,
    )

    confidence = ConfidenceScore(
        overall=80.0,
        level=ConfidenceLevel.GOOD,
        components=ConfidenceComponents(
            time_horizon=90.0,
            weather_stability=85.0,
            historical_accuracy=70.0,
            data_completeness=100.0,
        ),
    )

    return PredictionResponse(
        origin=origin,
        destination=dest,
        departure_time=dep,
        total_delay_minutes=delay,
        confidence=confidence,
        segments=[segment],
    )


class TestAnalyticsEmpty:
    def test_no_data(self):
        resp = client.get("/v1/analytics/accuracy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_predictions"] == 0
        assert data["total_feedback"] == 0
        assert data["feedback_rate"] == 0.0
        assert data["mae"] == 0.0
        assert data["calibration_version"] == 0
        assert data["breakdown_by_weather"] == []


class TestAnalyticsWithFeedback:
    def test_mae_and_percentages(self):
        # Create 4 predictions with known delays
        for delay, actual in [(10.0, 15), (20.0, 25), (10.0, 10), (10.0, 35)]:
            pred = _make_prediction(delay=delay)
            stores.prediction_store.save_prediction_sync(pred)
            fb = FeedbackResponse(
                prediction_id=pred.id,
                actual_delay_minutes=actual,
                predicted_delay_minutes=delay,
                deviation_minutes=actual - delay,
            )
            stores.prediction_store.save_feedback_sync(fb)

        resp = client.get("/v1/analytics/accuracy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_predictions"] == 4
        assert data["total_feedback"] == 4
        assert data["feedback_rate"] == 100.0

        # MAE = mean(|15-10|, |25-20|, |10-10|, |35-10|) = mean(5, 5, 0, 25) = 8.75
        assert data["mae"] == pytest.approx(8.75, abs=0.01)

        # within 10: deviations 5, 5, 0, 25 -> 3/4 = 75%
        assert data["within_10min_pct"] == pytest.approx(75.0, abs=0.1)

        # within 20: deviations 5, 5, 0, 25 -> 3/4 = 75%
        assert data["within_20min_pct"] == pytest.approx(75.0, abs=0.1)

    def test_breakdown_by_weather_type(self):
        # Rain predictions
        for _ in range(2):
            pred = _make_prediction(delay=10.0, weather_type=WeatherType.RAIN)
            stores.prediction_store.save_prediction_sync(pred)
            stores.prediction_store.save_feedback_sync(
                FeedbackResponse(
                    prediction_id=pred.id,
                    actual_delay_minutes=15,
                    predicted_delay_minutes=10.0,
                    deviation_minutes=5.0,
                )
            )

        # Snow predictions
        pred = _make_prediction(delay=20.0, weather_type=WeatherType.SNOW)
        stores.prediction_store.save_prediction_sync(pred)
        stores.prediction_store.save_feedback_sync(
            FeedbackResponse(
                prediction_id=pred.id,
                actual_delay_minutes=30,
                predicted_delay_minutes=20.0,
                deviation_minutes=10.0,
            )
        )

        resp = client.get("/v1/analytics/accuracy")
        data = resp.json()
        breakdown = {b["weather_type"]: b for b in data["breakdown_by_weather"]}

        assert "rain" in breakdown
        assert breakdown["rain"]["count"] == 2
        assert breakdown["rain"]["mae"] == pytest.approx(5.0, abs=0.01)

        assert "snow" in breakdown
        assert breakdown["snow"]["count"] == 1
        assert breakdown["snow"]["mae"] == pytest.approx(10.0, abs=0.01)

    def test_feedback_rate(self):
        # 3 predictions, 1 with feedback
        for i in range(3):
            pred = _make_prediction(delay=10.0)
            stores.prediction_store.save_prediction_sync(pred)
            if i == 0:
                stores.prediction_store.save_feedback_sync(
                    FeedbackResponse(
                        prediction_id=pred.id,
                        actual_delay_minutes=12,
                        predicted_delay_minutes=10.0,
                        deviation_minutes=2.0,
                    )
                )

        resp = client.get("/v1/analytics/accuracy")
        data = resp.json()
        assert data["total_predictions"] == 3
        assert data["total_feedback"] == 1
        assert data["feedback_rate"] == pytest.approx(33.3, abs=0.1)
