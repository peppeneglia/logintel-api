"""Tests for the calibration engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.engine.calibration import (
    COEFF_LOWER,
    COEFF_UPPER,
    DEFAULT_HISTORICAL_ACCURACY,
    LEARNING_RATE,
    MIN_CONDITION_GROUPS,
    MIN_FEEDBACK_COUNT,
    MIN_SPAN_DAYS,
    check_prerequisites,
    compute_error_factors,
    compute_historical_accuracy,
    compute_new_coefficients,
)
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
from app.stores.base import FeedbackPair


def _make_prediction(
    delay: float = 10.0,
    weather_type: WeatherType = WeatherType.RAIN,
    severity: Severity = Severity.MODERATE,
    departure_offset_days: int = 0,
) -> PredictionResponse:
    """Helper to create a PredictionResponse with a single weather condition."""
    base_time = datetime(2026, 2, 1, 8, 0, tzinfo=UTC) + timedelta(days=departure_offset_days)
    origin = Coordinate(lat=45.0, lon=9.0)
    dest = Coordinate(lat=42.0, lon=12.0)

    segment = SegmentDetail(
        index=0,
        start_point=origin,
        end_point=dest,
        length_km=100.0,
        estimated_arrival=base_time,
        weather=[
            WeatherCondition(
                type=weather_type,
                severity=severity,
                raw_value=5.0,
                description="test condition",
            )
        ],
        factors=SegmentFactors(
            road_type=RoadType.HIGHWAY,
            road_factor=0.8,
            altitude_m=200.0,
            altitude_factor=1.0,
            time_factor=1.0,
            calibration_factor=1.0,
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
        departure_time=base_time,
        total_delay_minutes=delay,
        confidence=confidence,
        segments=[segment],
    )


def _make_feedback(
    prediction_id: str,
    actual_delay: int = 10,
    offset_days: int = 0,
) -> FeedbackResponse:
    """Helper to create a FeedbackResponse."""
    base_time = datetime(2026, 2, 1, 10, 0, tzinfo=UTC) + timedelta(days=offset_days)
    return FeedbackResponse(
        prediction_id=prediction_id,
        actual_delay_minutes=actual_delay,
        predicted_delay_minutes=10.0,
        deviation_minutes=actual_delay - 10.0,
        received_at=base_time,
    )


def _make_inputs(
    count: int = 25,
    delay: float = 10.0,
    actual_delay: int = 10,
    span_days: int = 20,
    weather_types: list[tuple[WeatherType, Severity]] | None = None,
) -> list[FeedbackPair]:
    """
    Build a list of FeedbackPair entries.

    Distributes feedback over span_days and cycles through weather_types.
    """
    if weather_types is None:
        weather_types = [
            (WeatherType.RAIN, Severity.MODERATE),
            (WeatherType.SNOW, Severity.LIGHT),
            (WeatherType.FOG, Severity.MODERATE),
        ]

    inputs = []
    for i in range(count):
        wt, sev = weather_types[i % len(weather_types)]
        day_offset = int(i * span_days / max(count - 1, 1)) if count > 1 else 0
        pred = _make_prediction(delay=delay, weather_type=wt, severity=sev, departure_offset_days=day_offset)
        fb = _make_feedback(pred.id, actual_delay=actual_delay, offset_days=day_offset)
        inputs.append(FeedbackPair(prediction=pred, feedback=fb))

    return inputs


# --- Prerequisites ---


class TestCheckPrerequisites:
    def test_insufficient_feedback(self):
        inputs = _make_inputs(count=5)
        ok, reason = check_prerequisites(inputs)
        assert not ok
        assert str(MIN_FEEDBACK_COUNT) in reason

    def test_short_span(self):
        inputs = _make_inputs(count=25, span_days=5)
        ok, reason = check_prerequisites(inputs)
        assert not ok
        assert str(MIN_SPAN_DAYS) in reason

    def test_too_few_condition_groups(self):
        inputs = _make_inputs(
            count=25,
            span_days=20,
            weather_types=[(WeatherType.RAIN, Severity.MODERATE)],
        )
        ok, reason = check_prerequisites(inputs)
        assert not ok
        assert str(MIN_CONDITION_GROUPS) in reason

    def test_prerequisites_met(self):
        inputs = _make_inputs(count=25, span_days=20)
        ok, reason = check_prerequisites(inputs)
        assert ok
        assert reason == "ok"


# --- Error factors ---


class TestComputeErrorFactors:
    def test_perfect_prediction(self):
        """When actual == predicted, error_factor should be 1.0."""
        inputs = _make_inputs(count=25, delay=10.0, actual_delay=10)
        factors = compute_error_factors(inputs)
        for ef in factors.values():
            assert ef == pytest.approx(1.0, abs=0.01)

    def test_under_prediction(self):
        """When actual > predicted, error_factor should be > 1."""
        inputs = _make_inputs(count=25, delay=10.0, actual_delay=20)
        factors = compute_error_factors(inputs)
        for ef in factors.values():
            assert ef > 1.0

    def test_over_prediction(self):
        """When actual < predicted, error_factor should be < 1."""
        inputs = _make_inputs(count=25, delay=10.0, actual_delay=5)
        factors = compute_error_factors(inputs)
        for ef in factors.values():
            assert ef < 1.0


# --- Coefficients ---


class TestComputeNewCoefficients:
    def test_no_change_when_ef_is_one(self):
        current = {(WeatherType.RAIN, Severity.MODERATE): 1.0}
        error_factors = {(WeatherType.RAIN, Severity.MODERATE): 1.0}
        result = compute_new_coefficients(current, error_factors)
        assert result[(WeatherType.RAIN, Severity.MODERATE)] == pytest.approx(1.0)

    def test_formula_applied_correctly(self):
        current = {(WeatherType.RAIN, Severity.MODERATE): 1.0}
        error_factors = {(WeatherType.RAIN, Severity.MODERATE): 1.5}
        result = compute_new_coefficients(current, error_factors)
        # new = 1.0 + 0.15 * (1.5 - 1.0) * 1.0 = 1.075
        expected = 1.0 + LEARNING_RATE * (1.5 - 1.0) * 1.0
        assert result[(WeatherType.RAIN, Severity.MODERATE)] == pytest.approx(expected, abs=0.001)

    def test_clamp_lower_bound(self):
        current = {(WeatherType.SNOW, Severity.HEAVY): 0.5}
        error_factors = {(WeatherType.SNOW, Severity.HEAVY): 0.1}
        result = compute_new_coefficients(current, error_factors)
        assert result[(WeatherType.SNOW, Severity.HEAVY)] >= COEFF_LOWER

    def test_clamp_upper_bound(self):
        current = {(WeatherType.FOG, Severity.MODERATE): 2.0}
        error_factors = {(WeatherType.FOG, Severity.MODERATE): 3.0}
        result = compute_new_coefficients(current, error_factors)
        assert result[(WeatherType.FOG, Severity.MODERATE)] <= COEFF_UPPER


# --- Historical accuracy ---


class TestComputeHistoricalAccuracy:
    def test_all_within_threshold(self):
        inputs = _make_inputs(count=10, delay=10.0, actual_delay=10)
        accuracy = compute_historical_accuracy([i.feedback for i in inputs])
        assert accuracy == 100.0

    def test_none_within_threshold(self):
        # Actual delay = 100, predicted = 10 → deviation = 90 > 15
        inputs = _make_inputs(count=10, delay=10.0, actual_delay=100)
        accuracy = compute_historical_accuracy([i.feedback for i in inputs])
        assert accuracy == 0.0

    def test_insufficient_data_returns_default(self):
        inputs = _make_inputs(count=3, delay=10.0, actual_delay=10)
        accuracy = compute_historical_accuracy([i.feedback for i in inputs])
        assert accuracy == DEFAULT_HISTORICAL_ACCURACY

    def test_mixed_accuracy(self):
        """Half within threshold, half outside."""
        inputs = []
        for i in range(10):
            pred = _make_prediction(delay=10.0, departure_offset_days=i)
            # 5 within threshold (actual=10), 5 outside (actual=50)
            actual = 10 if i < 5 else 50
            fb = _make_feedback(pred.id, actual_delay=actual, offset_days=i)
            inputs.append(FeedbackPair(prediction=pred, feedback=fb))
        accuracy = compute_historical_accuracy([i.feedback for i in inputs])
        assert accuracy == pytest.approx(50.0, abs=0.1)
