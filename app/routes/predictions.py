"""
Prediction endpoints — FRD Section 8.2.

POST /v1/predictions       — Create a new prediction
GET  /v1/predictions/{id}  — Retrieve a prediction
GET  /v1/predictions       — List predictions
POST /v1/predictions/{id}/feedback — Submit feedback

Currently uses stub logic (no external API calls).
The engine calculates delays from mock weather data for testing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.engine.confidence import compute_confidence
from app.engine.heuristics import (
    RoadType,
    calculate_segment_delay,
    classify_weather,
    WeatherType,
)
from app.engine.sampler import (
    compute_segment_lengths,
    estimate_arrival_times,
    haversine_distance,
    interpolate_point,
)
from app.errors import NotFoundError
from app.models.schemas import (
    ConfidenceLevel,
    Coordinate,
    FeedbackRequest,
    FeedbackResponse,
    PredictionListResponse,
    PredictionRequest,
    PredictionResponse,
    PredictionSummary,
    SegmentDetail,
    SegmentFactors,
)

router = APIRouter(prefix="/v1/predictions", tags=["predictions"])

# In-memory store for stubs (replaced by Supabase in later blocks)
_predictions_store: dict[str, PredictionResponse] = {}
_feedback_store: dict[str, FeedbackResponse] = {}


def _generate_stub_segments(
    origin: Coordinate,
    destination: Coordinate,
    departure_time: datetime,
) -> list[SegmentDetail]:
    """Generate segments with stub data for testing the engine."""
    total_distance = haversine_distance(origin, destination)
    interval_km = 50.0

    if total_distance <= 0:
        return []

    num_segments = max(1, int(total_distance / interval_km))
    segment_length = total_distance / num_segments

    segments = []
    for i in range(num_segments):
        frac_start = i / num_segments
        frac_end = (i + 1) / num_segments
        start = interpolate_point(origin, destination, frac_start)
        end = interpolate_point(origin, destination, frac_end)

        # Estimate arrival time proportionally
        total_travel_seconds = (total_distance / 70.0) * 3600
        elapsed = frac_start * total_travel_seconds
        from datetime import timedelta
        estimated_arrival = departure_time + timedelta(seconds=elapsed)

        # Stub: no weather conditions (clear sky) — real data comes in Block 2
        weather_conditions = []

        factors = SegmentFactors(
            road_type=RoadType.HIGHWAY,
            road_factor=0.8,
            altitude_m=200.0,
            altitude_factor=1.0,
            time_factor=1.0,
            calibration_factor=1.0,
        )

        delay = calculate_segment_delay(
            weather_conditions=weather_conditions,
            segment_km=segment_length,
            road_type=RoadType.HIGHWAY,
            altitude_m=200.0,
            arrival_time=estimated_arrival,
            calibration_factor=1.0,
        )

        segments.append(
            SegmentDetail(
                index=i,
                start_point=start,
                end_point=end,
                length_km=round(segment_length, 2),
                estimated_arrival=estimated_arrival,
                weather=weather_conditions,
                factors=factors,
                delay_minutes=delay,
            )
        )

    return segments


@router.post("", response_model=PredictionResponse, status_code=201)
async def create_prediction(request: PredictionRequest) -> PredictionResponse:
    """Create a new delay prediction for a route."""
    segments = _generate_stub_segments(
        request.origin, request.destination, request.departure_time
    )

    total_delay = sum(s.delay_minutes for s in segments)

    # Gather weather values for confidence (empty for stubs → high stability)
    weather_values = [0.0] * len(segments) if segments else [0.0]
    confidence = compute_confidence(
        departure=request.departure_time,
        weather_values=weather_values,
        total_points=len(segments),
        successful_points=len(segments),
    )

    prediction = PredictionResponse(
        origin=request.origin,
        destination=request.destination,
        departure_time=request.departure_time,
        total_delay_minutes=round(total_delay, 2),
        confidence=confidence,
        segments=segments,
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
