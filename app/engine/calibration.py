"""
Calibration logic — FRD Section 7.4.

Pure functions that compute calibration coefficients from feedback data.
No imports from stores or routes — receives data as arguments.

Formula: new = old + 0.15 * (error_factor - 1.0) * old
Clamped to [0.5, 2.0].
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.models.schemas import (
    FeedbackResponse,
    PredictionResponse,
    Severity,
    WeatherType,
)
from app.stores.calibration_store import COEFF_LOWER, COEFF_UPPER

# Minimum feedback entries before calibration is attempted
MIN_FEEDBACK_COUNT = 20
# Minimum span of feedback data
MIN_SPAN_DAYS = 14
# Minimum distinct weather condition groups
MIN_CONDITION_GROUPS = 3
# Learning rate
LEARNING_RATE = 0.15
# Minimum feedback for historical accuracy calculation
MIN_FEEDBACK_FOR_ACCURACY = 5
# Default historical accuracy when insufficient data
DEFAULT_HISTORICAL_ACCURACY = 70.0
# Threshold for "within tolerance" accuracy measurement (minutes)
ACCURACY_THRESHOLD_MINUTES = 15.0


@dataclass
class CalibrationInput:
    prediction: PredictionResponse
    feedback: FeedbackResponse


def check_prerequisites(inputs: list[CalibrationInput]) -> tuple[bool, str]:
    """
    Check whether calibration prerequisites are met.

    Returns (True, "ok") or (False, reason).
    Requires:
      - At least MIN_FEEDBACK_COUNT feedback entries
      - Feedback spanning at least MIN_SPAN_DAYS
      - At least MIN_CONDITION_GROUPS distinct weather condition groups
    """
    if len(inputs) < MIN_FEEDBACK_COUNT:
        return False, f"Need at least {MIN_FEEDBACK_COUNT} feedback entries, have {len(inputs)}"

    # Check time span
    times = [inp.feedback.received_at for inp in inputs]
    span = max(times) - min(times)
    if span < timedelta(days=MIN_SPAN_DAYS):
        return False, f"Feedback span is {span.days} days, need at least {MIN_SPAN_DAYS}"

    # Check distinct condition groups
    groups = _extract_condition_groups(inputs)
    if len(groups) < MIN_CONDITION_GROUPS:
        return False, f"Only {len(groups)} condition groups, need at least {MIN_CONDITION_GROUPS}"

    return True, "ok"


def compute_error_factors(
    inputs: list[CalibrationInput],
) -> dict[tuple[WeatherType, Severity], float]:
    """
    Group feedback by dominant weather condition and compute mean(actual/predicted)
    for each group.

    Returns a dict mapping (WeatherType, Severity) -> error_factor.
    Only includes groups with at least 1 feedback entry where predicted > 0.
    """
    # Group by dominant condition
    grouped: dict[tuple[WeatherType, Severity], list[tuple[float, float]]] = {}

    for inp in inputs:
        dominant = _get_dominant_condition(inp.prediction)
        if dominant is None:
            continue

        predicted = inp.prediction.total_delay_minutes
        actual = float(inp.feedback.actual_delay_minutes)

        if predicted <= 0:
            continue

        key = (dominant[0], dominant[1])
        grouped.setdefault(key, []).append((actual, predicted))

    error_factors: dict[tuple[WeatherType, Severity], float] = {}
    for key, pairs in grouped.items():
        ratios = [actual / predicted for actual, predicted in pairs]
        error_factors[key] = sum(ratios) / len(ratios)

    return error_factors


def compute_new_coefficients(
    current: dict[tuple[WeatherType, Severity], float],
    error_factors: dict[tuple[WeatherType, Severity], float],
) -> dict[tuple[WeatherType, Severity], float]:
    """
    Apply FRD 7.4 formula to compute updated coefficients.

    new = old + 0.15 * (error_factor - 1.0) * old
    Clamped to [COEFF_LOWER, COEFF_UPPER].
    """
    new_coefficients: dict[tuple[WeatherType, Severity], float] = {}

    # Start with all current coefficients
    for key, value in current.items():
        new_coefficients[key] = value

    # Apply error factors
    for key, ef in error_factors.items():
        old = current.get(key, 1.0)
        new_val = old + LEARNING_RATE * (ef - 1.0) * old
        new_val = max(COEFF_LOWER, min(COEFF_UPPER, new_val))
        new_coefficients[key] = round(new_val, 4)

    return new_coefficients


def compute_historical_accuracy(inputs: list[CalibrationInput]) -> float:
    """
    Compute the percentage of predictions within ACCURACY_THRESHOLD_MINUTES of actual.

    Returns DEFAULT_HISTORICAL_ACCURACY if fewer than MIN_FEEDBACK_FOR_ACCURACY entries.
    """
    if len(inputs) < MIN_FEEDBACK_FOR_ACCURACY:
        return DEFAULT_HISTORICAL_ACCURACY

    within_threshold = sum(
        1
        for inp in inputs
        if abs(inp.feedback.actual_delay_minutes - inp.prediction.total_delay_minutes)
        <= ACCURACY_THRESHOLD_MINUTES
    )

    return round((within_threshold / len(inputs)) * 100.0, 1)


def _get_dominant_condition(
    prediction: PredictionResponse,
) -> tuple[WeatherType, Severity] | None:
    """
    Find the dominant weather condition across all segments.

    The dominant condition is the one with the highest base_impact.
    """
    from app.engine.heuristics import WEATHER_IMPACT

    best: tuple[WeatherType, Severity] | None = None
    best_impact = 0.0

    for segment in prediction.segments:
        for condition in segment.weather:
            impact = WEATHER_IMPACT.get(condition.type, {}).get(condition.severity, 0.0)
            if impact > best_impact:
                best_impact = impact
                best = (condition.type, condition.severity)

    return best


def _extract_condition_groups(
    inputs: list[CalibrationInput],
) -> set[tuple[WeatherType, Severity]]:
    """Extract distinct (WeatherType, Severity) groups from predictions."""
    groups: set[tuple[WeatherType, Severity]] = set()
    for inp in inputs:
        dominant = _get_dominant_condition(inp.prediction)
        if dominant is not None:
            groups.add(dominant)
    return groups
