"""
Analytics endpoints.

GET /v1/analytics/accuracy — Accuracy metrics and calibration info
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends

import app.stores as stores
from app.auth import OrgContext, get_current_org
from app.engine.calibration import get_dominant_condition
from app.models.schemas import (
    AnalyticsResponse,
    ErrorResponse,
    WeatherType,
    WeatherTypeBreakdown,
)

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


def _accuracy_stats(deviations: list[float]) -> tuple[float, float, float]:
    """Return (MAE, % within 10 min, % within 20 min) for absolute deviations."""
    if not deviations:
        return 0.0, 0.0, 0.0
    n = len(deviations)
    return (
        round(sum(deviations) / n, 2),
        round(sum(1 for d in deviations if d <= 10) / n * 100, 1),
        round(sum(1 for d in deviations if d <= 20) / n * 100, 1),
    )


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
    - **calibration_version** — current (global) calibration coefficient version.
    - **breakdown_by_weather** — per-weather-type accuracy (rain, snow, wind, fog).

    Metrics are computed from all feedback entries for the authenticated organization.
    """
    total_predictions = await stores.prediction_store.prediction_count(org_id=org.org_id)
    total_feedback = await stores.prediction_store.feedback_count(org_id=org.org_id)
    pairs = await stores.prediction_store.list_feedback_pairs(org_id=org.org_id)

    deviations: list[float] = []
    weather_buckets: dict[WeatherType, list[float]] = defaultdict(list)

    for pair in pairs:
        abs_dev = abs(pair.feedback.actual_delay_minutes - pair.prediction.total_delay_minutes)
        deviations.append(abs_dev)

        dominant = get_dominant_condition(pair.prediction)
        if dominant is not None:
            weather_buckets[dominant[0]].append(abs_dev)

    mae, within_10, within_20 = _accuracy_stats(deviations)

    breakdown: list[WeatherTypeBreakdown] = []
    for weather_type in WeatherType:
        devs = weather_buckets.get(weather_type)
        if not devs:
            continue
        wt_mae, wt_10, wt_20 = _accuracy_stats(devs)
        breakdown.append(
            WeatherTypeBreakdown(
                weather_type=weather_type,
                count=len(devs),
                mae=wt_mae,
                within_10min_pct=wt_10,
                within_20min_pct=wt_20,
            )
        )

    feedback_rate = round(total_feedback / total_predictions * 100.0, 1) if total_predictions else 0.0

    return AnalyticsResponse(
        total_predictions=total_predictions,
        total_feedback=total_feedback,
        feedback_rate=feedback_rate,
        mae=mae,
        within_10min_pct=within_10,
        within_20min_pct=within_20,
        calibration_version=await stores.calibration_store.get_current_version(),
        breakdown_by_weather=breakdown,
    )
