"""
Prediction endpoints — FRD Section 8.2.

POST /v1/predictions       — Create a new prediction
GET  /v1/predictions/{id}  — Retrieve a prediction
GET  /v1/predictions       — List predictions
POST /v1/predictions/{id}/feedback — Submit feedback
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

import app.stores as stores
from app.auth import OrgContext, get_current_org
from app.errors import InvalidRequestError, NotFoundError
from app.models.schemas import (
    ErrorResponse,
    FeedbackRequest,
    FeedbackResponse,
    PredictionListResponse,
    PredictionRequest,
    PredictionResponse,
    PredictionSummary,
)
from app.rate_limit import rate_limiter
from app.services.prediction import build_prediction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/predictions", tags=["predictions"])

# Maximum age of a prediction that can receive feedback (7 days)
FEEDBACK_WINDOW_DAYS = 7


@router.post(
    "",
    response_model=PredictionResponse,
    status_code=201,
    summary="Create a prediction",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request — missing fields, invalid coordinates, or departure_time without timezone."},
        401: {"model": ErrorResponse, "description": "Missing or invalid authentication credentials."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded. Check the `Retry-After` header."},
        502: {"model": ErrorResponse, "description": "Upstream service (routing/weather) unavailable. Retry later."},
    },
)
async def create_prediction(
    request: PredictionRequest,
    org: OrgContext = Depends(get_current_org),
) -> PredictionResponse:
    """Create a new weather-aware delay prediction for a route.

    Calculates a route between origin and destination, samples weather forecasts
    at points every 50 km, and applies heuristics to estimate per-segment delays.

    Set `include_alternatives` to `true` to receive up to 2 alternative routes
    when the predicted delay on the main route exceeds the threshold (20 min).

    The prediction is persisted and can be retrieved later by its `id`.
    """
    rate_limiter.check(org)

    prediction = await build_prediction(
        request.origin,
        request.destination,
        request.departure_time,
        include_alternatives=request.include_alternatives,
    )

    await stores.prediction_store.save_prediction(prediction, org_id=org.org_id)
    return prediction


@router.get(
    "/{prediction_id}",
    response_model=PredictionResponse,
    summary="Get a prediction",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid authentication credentials."},
        404: {"model": ErrorResponse, "description": "Prediction not found."},
    },
)
async def get_prediction(
    prediction_id: str,
    org: OrgContext = Depends(get_current_org),
) -> PredictionResponse:
    """Retrieve a previously created prediction by its ID.

    Returns the full prediction including segments, confidence score,
    and alternative routes (if they were requested at creation time).
    """
    prediction = await stores.prediction_store.get_prediction(prediction_id, org_id=org.org_id)
    if prediction is None:
        raise NotFoundError(f"Prediction {prediction_id} not found")
    return prediction


@router.get(
    "",
    response_model=PredictionListResponse,
    summary="List predictions",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid authentication credentials."},
    },
)
async def list_predictions(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    org: OrgContext = Depends(get_current_org),
) -> PredictionListResponse:
    """List predictions for the authenticated organization with pagination.

    Returns a summary for each prediction (no per-segment details).
    Use `GET /v1/predictions/{id}` to retrieve full details for a specific prediction.
    """
    all_predictions = await stores.prediction_store.list_predictions(org_id=org.org_id)

    total = len(all_predictions)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = all_predictions[start:end]

    summaries = [
        PredictionSummary(
            id=p.id,
            origin=p.origin,
            destination=p.destination,
            departure_time=p.departure_time,
            total_delay_minutes=p.total_delay_minutes,
            confidence_level=p.confidence.level,
            created_at=p.created_at,
        )
        for p in page_items
    ]

    return PredictionListResponse(
        predictions=summaries, total=total, page=page, per_page=per_page
    )


@router.post(
    "/{prediction_id}/feedback",
    response_model=FeedbackResponse,
    status_code=201,
    summary="Submit feedback",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request — feedback already submitted, feedback window expired (> 7 days), or invalid delay value."},
        401: {"model": ErrorResponse, "description": "Missing or invalid authentication credentials."},
        404: {"model": ErrorResponse, "description": "Prediction not found."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded. Check the `Retry-After` header."},
    },
)
async def submit_feedback(
    prediction_id: str,
    request: FeedbackRequest,
    org: OrgContext = Depends(get_current_org),
) -> FeedbackResponse:
    """Submit the actual delay observed for a prediction.

    Only one feedback entry is allowed per prediction. The feedback window
    is 7 days from the prediction's departure time.

    Feedback is used to calibrate prediction coefficients and improve accuracy.
    The response includes the deviation between predicted and actual delay.
    """
    rate_limiter.check(org)

    prediction = await stores.prediction_store.get_prediction(prediction_id, org_id=org.org_id)
    if prediction is None:
        raise NotFoundError(f"Prediction {prediction_id} not found")

    if await stores.prediction_store.get_feedback(prediction_id, org_id=org.org_id) is not None:
        raise InvalidRequestError("Feedback already submitted for this prediction")

    # Reject feedback for predictions older than 7 days
    now = datetime.now(timezone.utc)
    departure = prediction.departure_time
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)
    if (now - departure) > timedelta(days=FEEDBACK_WINDOW_DAYS):
        raise InvalidRequestError(
            f"Feedback window expired: prediction departure was more than {FEEDBACK_WINDOW_DAYS} days ago"
        )

    deviation = request.actual_delay_minutes - prediction.total_delay_minutes

    feedback = FeedbackResponse(
        prediction_id=prediction_id,
        actual_delay_minutes=request.actual_delay_minutes,
        predicted_delay_minutes=prediction.total_delay_minutes,
        deviation_minutes=round(deviation, 2),
    )

    await stores.prediction_store.save_feedback(feedback, org_id=org.org_id)

    # Try to recalibrate after new feedback
    await _try_calibrate(org.org_id)

    return feedback


async def _try_calibrate(org_id: str = "") -> None:
    """Attempt recalibration if prerequisites are met."""
    from app.engine.calibration import (
        CalibrationInput,
        check_prerequisites,
        compute_error_factors,
        compute_new_coefficients,
    )

    # Build calibration inputs from paired prediction+feedback
    inputs: list[CalibrationInput] = []
    for fb in await stores.prediction_store.all_feedback(org_id=org_id):
        pred = await stores.prediction_store.get_prediction(fb.prediction_id, org_id=org_id)
        if pred is not None:
            inputs.append(CalibrationInput(prediction=pred, feedback=fb))

    ok, _reason = check_prerequisites(inputs)
    if not ok:
        return

    error_factors = compute_error_factors(inputs)
    if not error_factors:
        return

    # Get current coefficients
    current: dict[tuple, float] = {}
    for key in error_factors:
        current[key] = await stores.calibration_store.get_coefficient(key[0], key[1], org_id=org_id)

    new_coefficients = compute_new_coefficients(current, error_factors)
    await stores.calibration_store.save_version(new_coefficients, feedback_count=len(inputs), org_id=org_id)
    logger.info(
        "Calibration updated to version %d with %d feedback entries",
        await stores.calibration_store.get_current_version(org_id=org_id),
        len(inputs),
    )
