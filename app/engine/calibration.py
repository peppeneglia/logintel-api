"""
Calibration logic.

Pure functions that compute calibration coefficients from feedback data;
they receive data as arguments and never touch the stores directly.

Formula: new = old + 0.15 * (error_factor - 1.0) * old
Clamped to [0.5, 2.0].
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from app.engine.heuristics import WEATHER_IMPACT
from app.models.schemas import (
    FeedbackResponse,
    PredictionResponse,
    Severity,
    WeatherCondition,
    WeatherType,
)
from app.stores.base import FeedbackPair

# Bounds that keep calibration coefficients from drifting
COEFF_LOWER = 0.5
COEFF_UPPER = 2.0

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


def check_prerequisites(inputs: list[FeedbackPair]) -> tuple[bool, str]:
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
    inputs: list[FeedbackPair],
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
        dominant = get_dominant_condition(inp.prediction)
        if dominant is None:
            continue

        predicted = inp.prediction.total_delay_minutes
        actual = float(inp.feedback.actual_delay_minutes)

        if predicted <= 0:
            continue

        grouped.setdefault(dominant, []).append((actual, predicted))

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
    Apply the calibration formula to compute updated coefficients.

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


def compute_historical_accuracy(feedback: list[FeedbackResponse]) -> float:
    """
    Compute the percentage of predictions within ACCURACY_THRESHOLD_MINUTES of actual.

    Returns DEFAULT_HISTORICAL_ACCURACY if fewer than MIN_FEEDBACK_FOR_ACCURACY entries.
    """
    if len(feedback) < MIN_FEEDBACK_FOR_ACCURACY:
        return DEFAULT_HISTORICAL_ACCURACY

    within_threshold = sum(
        1
        for fb in feedback
        if abs(fb.actual_delay_minutes - fb.predicted_delay_minutes) <= ACCURACY_THRESHOLD_MINUTES
    )

    return round((within_threshold / len(feedback)) * 100.0, 1)


def dominant_condition(
    conditions: Iterable[WeatherCondition],
) -> tuple[WeatherType, Severity] | None:
    """Return the (type, severity) with the highest base impact, or None if there is none."""
    best: tuple[WeatherType, Severity] | None = None
    best_impact = 0.0

    for condition in conditions:
        impact = WEATHER_IMPACT[condition.type][condition.severity]
        if impact > best_impact:
            best_impact = impact
            best = (condition.type, condition.severity)

    return best


def get_dominant_condition(
    prediction: PredictionResponse,
) -> tuple[WeatherType, Severity] | None:
    """Find the dominant weather condition across all segments of a prediction."""
    return dominant_condition(condition for segment in prediction.segments for condition in segment.weather)


def _extract_condition_groups(
    inputs: list[FeedbackPair],
) -> set[tuple[WeatherType, Severity]]:
    """Extract distinct (WeatherType, Severity) groups from predictions."""
    groups: set[tuple[WeatherType, Severity]] = set()
    for inp in inputs:
        dominant = get_dominant_condition(inp.prediction)
        if dominant is not None:
            groups.add(dominant)
    return groups
