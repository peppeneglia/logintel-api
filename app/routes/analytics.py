"""
Analytics endpoints — FRD Section 8.2.

GET /v1/analytics/accuracy — Accuracy metrics and calibration info
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter

from app.models.schemas import (
    AnalyticsResponse,
    WeatherType,
    WeatherTypeBreakdown,
)
from app.stores import calibration_store, prediction_store

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/accuracy", response_model=AnalyticsResponse)
async def get_accuracy() -> AnalyticsResponse:
    """Return accuracy metrics computed from stored feedback."""
    total_predictions = prediction_store.prediction_count()
    total_feedback = prediction_store.feedback_count()

    feedback_rate = 0.0
    if total_predictions > 0:
        feedback_rate = round((total_feedback / total_predictions) * 100.0, 1)

    all_fb = prediction_store.all_feedback()

    # Compute overall metrics
    deviations: list[float] = []
    # Per weather-type buckets: weather_type -> list of (abs_deviation)
    weather_buckets: dict[WeatherType, list[float]] = defaultdict(list)

    for fb in all_fb:
        pred = prediction_store.get_prediction(fb.prediction_id)
        if pred is None:
            continue

        abs_dev = abs(fb.actual_delay_minutes - pred.total_delay_minutes)
        deviations.append(abs_dev)

        # Find dominant weather type for this prediction
        dominant_type = _get_dominant_weather_type(pred)
        if dominant_type is not None:
            weather_buckets[dominant_type].append(abs_dev)

    mae = 0.0
    within_10 = 0.0
    within_20 = 0.0

    if deviations:
        mae = round(sum(deviations) / len(deviations), 2)
        within_10 = round(sum(1 for d in deviations if d <= 10) / len(deviations) * 100, 1)
        within_20 = round(sum(1 for d in deviations if d <= 20) / len(deviations) * 100, 1)

    # Breakdown by weather type
    breakdown: list[WeatherTypeBreakdown] = []
    for wt in WeatherType:
        devs = weather_buckets.get(wt, [])
        if not devs:
            continue
        wt_mae = round(sum(devs) / len(devs), 2)
        wt_10 = round(sum(1 for d in devs if d <= 10) / len(devs) * 100, 1)
        wt_20 = round(sum(1 for d in devs if d <= 20) / len(devs) * 100, 1)
        breakdown.append(
            WeatherTypeBreakdown(
                weather_type=wt,
                count=len(devs),
                mae=wt_mae,
                within_10min_pct=wt_10,
                within_20min_pct=wt_20,
            )
        )

    return AnalyticsResponse(
        total_predictions=total_predictions,
        total_feedback=total_feedback,
        feedback_rate=feedback_rate,
        mae=mae,
        within_10min_pct=within_10,
        within_20min_pct=within_20,
        calibration_version=calibration_store.get_current_version(),
        breakdown_by_weather=breakdown,
    )


def _get_dominant_weather_type(pred) -> WeatherType | None:
    """Find the weather type with highest impact across all segments."""
    from app.engine.heuristics import WEATHER_IMPACT

    best_type: WeatherType | None = None
    best_impact = 0.0

    for segment in pred.segments:
        for condition in segment.weather:
            impact = WEATHER_IMPACT.get(condition.type, {}).get(condition.severity, 0.0)
            if impact > best_impact:
                best_impact = impact
                best_type = condition.type

    return best_type
