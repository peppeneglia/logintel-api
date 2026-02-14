"""Tests for the confidence scoring — validates against FRD Section 6.5."""

from datetime import datetime, timedelta, timezone

from app.engine.confidence import (
    compute_confidence,
    compute_time_horizon_score,
    compute_weather_stability_score,
    compute_data_completeness_score,
)
from app.models.schemas import ConfidenceLevel


class TestTimeHorizon:
    def test_under_6h(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=3)
        assert compute_time_horizon_score(dep, now) == 95.0

    def test_6_to_24h(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=12)
        assert compute_time_horizon_score(dep, now) == 80.0

    def test_24_to_48h(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=36)
        assert compute_time_horizon_score(dep, now) == 65.0

    def test_over_48h(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=60)
        assert compute_time_horizon_score(dep, now) == 50.0


class TestWeatherStability:
    def test_stable_weather(self):
        # All same values → high score
        score = compute_weather_stability_score([5.0, 5.0, 5.0, 5.0])
        assert score >= 90.0

    def test_variable_weather(self):
        # High variance → lower score
        score = compute_weather_stability_score([0.0, 20.0, 0.0, 20.0])
        assert score < 80.0

    def test_single_point(self):
        score = compute_weather_stability_score([5.0])
        assert score == 90.0

    def test_empty(self):
        score = compute_weather_stability_score([])
        assert score == 90.0


class TestDataCompleteness:
    def test_all_complete(self):
        assert compute_data_completeness_score(10, 10) == 100.0

    def test_partial(self):
        assert compute_data_completeness_score(10, 7) == 70.0

    def test_none(self):
        assert compute_data_completeness_score(10, 0) == 0.0

    def test_zero_total(self):
        assert compute_data_completeness_score(0, 0) == 0.0


class TestComputeConfidence:
    def test_high_confidence_near_departure(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=2)
        result = compute_confidence(
            departure=dep,
            weather_values=[2.0, 2.0, 2.0],
            total_points=3,
            successful_points=3,
            now=now,
        )
        assert result.level == ConfidenceLevel.HIGH
        assert result.overall >= 85.0

    def test_low_confidence_far_departure_bad_data(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=60)
        result = compute_confidence(
            departure=dep,
            weather_values=[0.0, 30.0, 0.0, 30.0],
            total_points=10,
            successful_points=3,
            now=now,
        )
        assert result.overall < 70.0

    def test_components_present(self):
        now = datetime(2026, 2, 14, 10, 0, tzinfo=timezone.utc)
        dep = now + timedelta(hours=5)
        result = compute_confidence(
            departure=dep,
            weather_values=[1.0, 1.0],
            total_points=2,
            successful_points=2,
            now=now,
        )
        assert result.components.time_horizon == 95.0
        assert result.components.data_completeness == 100.0
