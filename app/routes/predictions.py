"""
Prediction endpoints — FRD Section 8.2.

POST /v1/predictions       — Create a new prediction
GET  /v1/predictions/{id}  — Retrieve a prediction
GET  /v1/predictions       — List predictions
POST /v1/predictions/{id}/feedback — Submit feedback
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.errors import NotFoundError
from app.models.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    PredictionListResponse,
    PredictionRequest,
    PredictionResponse,
    PredictionSummary,
)
from app.services.prediction import build_prediction

router = APIRouter(prefix="/v1/predictions", tags=["predictions"])

# In-memory store (replaced by Supabase in later blocks)
_predictions_store: dict[str, PredictionResponse] = {}
_feedback_store: dict[str, FeedbackResponse] = {}


@router.post("", response_model=PredictionResponse, status_code=201)
async def create_prediction(request: PredictionRequest) -> PredictionResponse:
    """Create a new delay prediction for a route."""
    prediction = await build_prediction(
        request.origin, request.destination, request.departure_time
    )

    _predictions_store[prediction.id] = prediction
    return prediction


@router.get("/{prediction_id}", response_model=PredictionResponse)
async def get_prediction(prediction_id: str) -> PredictionResponse:
    """Retrieve a prediction by ID."""
    prediction = _predictions_store.get(prediction_id)
    if prediction is None:
        raise NotFoundError(f"Prediction {prediction_id} not found")
    return prediction


@router.get("", response_model=PredictionListResponse)
async def list_predictions(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> PredictionListResponse:
    """List predictions with pagination."""
    all_predictions = list(_predictions_store.values())
    # Sort by created_at descending
    all_predictions.sort(key=lambda p: p.created_at, reverse=True)

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


@router.post("/{prediction_id}/feedback", response_model=FeedbackResponse, status_code=201)
async def submit_feedback(
    prediction_id: str, request: FeedbackRequest
) -> FeedbackResponse:
    """Submit actual delay feedback for a prediction."""
    prediction = _predictions_store.get(prediction_id)
    if prediction is None:
        raise NotFoundError(f"Prediction {prediction_id} not found")

    if prediction_id in _feedback_store:
        from app.errors import InvalidRequestError
        raise InvalidRequestError("Feedback already submitted for this prediction")

    deviation = request.actual_delay_minutes - prediction.total_delay_minutes

    feedback = FeedbackResponse(
        prediction_id=prediction_id,
        actual_delay_minutes=request.actual_delay_minutes,
        predicted_delay_minutes=prediction.total_delay_minutes,
        deviation_minutes=round(deviation, 2),
    )

    _feedback_store[prediction_id] = feedback
    return feedback
