"""
Analytics endpoints — FRD Section 8.2.

GET /v1/analytics/accuracy — Accuracy metrics and calibration info
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends

import app.stores as stores
from app.auth import OrgContext, get_current_org
from app.models.schemas import (
    AnalyticsResponse,
    ErrorResponse,
    WeatherType,
    WeatherTypeBreakdown,
)

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get(
    "/accuracy",
    response_model=AnalyticsResponse,
    summary="Get accuracy metrics",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid authentication credentials."},
    },
)
async def get_accuracy(
    org: OrgContext = Depends(get_current_org),
) -> AnalyticsResponse:
    """Return accuracy metrics computed from stored prediction feedback.

    Metrics include:
    - **MAE** — Mean Absolute Error between predicted and actual delays (minutes).
    - **within_10min_pct / within_20min_pct** — percentage of predictions within 10/20 min of actual.
    - **feedback_rate** — percentage of predictions that received feedback.
    - **calibration_version** — current calibration coefficient version.
    - **breakdown_by_weather** — per-weather-type accuracy (rain, snow, wind, fog).

    Metrics are computed from all feedback entries for the authenticated organization.
    """
    total_predictions = await stores.prediction_store.prediction_count(org_id=org.org_id)
    total_feedback = await stores.prediction_store.feedback_count(org_id=org.org_id)

    feedback_rate = 0.0
    if total_predictions > 0:
        feedback_rate = round((total_feedback / total_predictions) * 100.0, 1)

    all_fb = await stores.prediction_store.all_feedback(org_id=org.org_id)

    # Compute overall metrics
    deviations: list[float] = []
    # Per weather-type buckets: weather_type -> list of (abs_deviation)
    weather_buckets: dict[WeatherType, list[float]] = defaultdict(list)

    for fb in all_fb:
        pred = await stores.prediction_store.get_prediction(fb.prediction_id, org_id=org.org_id)
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
        calibration_version=await stores.calibration_store.get_current_version(org_id=org.org_id),
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
