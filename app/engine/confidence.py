"""
Confidence score calculation.

confidence = (C_horizon x 0.40) + (C_stability x 0.30) + (C_historical x 0.20) + (C_data x 0.10)

Components:
  - Time horizon: <6h 95%, 6-24h 80%, 24-48h 65%, >48h 50%
  - Weather stability: variance of forecasts across consecutive hours
  - Historical accuracy: performance on similar conditions (default 70% until calibrated)
  - Data completeness: % of data points successfully fetched
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.schemas import ConfidenceComponents, ConfidenceLevel, ConfidenceScore

# Component weights
W_HORIZON = 0.40
W_STABILITY = 0.30
W_HISTORICAL = 0.20
W_DATA = 0.10

# Default historical accuracy until calibration has data
DEFAULT_HISTORICAL_ACCURACY = 70.0


def compute_time_horizon_score(departure: datetime, now: datetime | None = None) -> float:
    """Score based on how far in the future the departure is."""
    if now is None:
        now = datetime.now(UTC)

    # Ensure both are timezone-aware for comparison
    if departure.tzinfo is None:
        hours_ahead = 0.0
    else:
        delta = departure - now
        hours_ahead = max(delta.total_seconds() / 3600.0, 0.0)

    if hours_ahead < 6:
        return 95.0
    elif hours_ahead < 24:
        return 80.0
    elif hours_ahead < 48:
        return 65.0
    else:
        return 50.0


def compute_weather_stability_score(weather_values: list[float]) -> float:
    """
    Score based on variance in weather measurements across consecutive sample points.

    Lower variance = more stable = higher confidence.
    Input: list of raw weather values (e.g. precipitation mm/h) at consecutive points.
    """
    if not weather_values or len(weather_values) < 2:
        return 90.0  # single point or no data: assume stable

    mean = sum(weather_values) / len(weather_values)
    variance = sum((v - mean) ** 2 for v in weather_values) / len(weather_values)

    # Normalize: low variance → high score
    # Heuristic: variance of 0 → 95, variance >= 100 → 40
    score = max(40.0, 95.0 - (variance * 0.55))
    return min(score, 95.0)


def compute_data_completeness_score(total_points: int, successful_points: int) -> float:
    """Score based on percentage of data points successfully fetched."""
    if total_points == 0:
        return 0.0
    ratio = successful_points / total_points
    return round(ratio * 100.0, 1)


def compute_confidence(
    departure: datetime,
    weather_values: list[float],
    total_points: int,
    successful_points: int,
    historical_accuracy: float = DEFAULT_HISTORICAL_ACCURACY,
    now: datetime | None = None,
) -> ConfidenceScore:
    """Compute the overall confidence score for a prediction."""
    c_horizon = compute_time_horizon_score(departure, now)
    c_stability = compute_weather_stability_score(weather_values)
    c_historical = historical_accuracy
    c_data = compute_data_completeness_score(total_points, successful_points)

    overall = (
        c_horizon * W_HORIZON + c_stability * W_STABILITY + c_historical * W_HISTORICAL + c_data * W_DATA
    )
    overall = round(min(max(overall, 0.0), 100.0), 1)

    if overall >= 85:
        level = ConfidenceLevel.HIGH
    elif overall >= 70:
        level = ConfidenceLevel.GOOD
    elif overall >= 55:
        level = ConfidenceLevel.MODERATE
    else:
        level = ConfidenceLevel.LOW

    components = ConfidenceComponents(
        time_horizon=c_horizon,
        weather_stability=round(c_stability, 1),
        historical_accuracy=c_historical,
        data_completeness=c_data,
    )

    return ConfidenceScore(overall=overall, level=level, components=components)
