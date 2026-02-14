"""
In-memory prediction and feedback stores.

Extracted from routes/predictions.py to allow shared access
from engine/calibration and routes/analytics without circular imports.

Will be swapped for Supabase persistence in Block 5.
"""

from __future__ import annotations

from app.models.schemas import FeedbackResponse, PredictionResponse

_predictions_store: dict[str, PredictionResponse] = {}
_feedback_store: dict[str, FeedbackResponse] = {}


def save_prediction(prediction: PredictionResponse) -> None:
    _predictions_store[prediction.id] = prediction


def get_prediction(prediction_id: str) -> PredictionResponse | None:
    return _predictions_store.get(prediction_id)


def list_predictions() -> list[PredictionResponse]:
    """Return all predictions sorted by created_at descending."""
    items = list(_predictions_store.values())
    items.sort(key=lambda p: p.created_at, reverse=True)
    return items


def prediction_count() -> int:
    return len(_predictions_store)


def save_feedback(feedback: FeedbackResponse) -> None:
    _feedback_store[feedback.prediction_id] = feedback


def get_feedback(prediction_id: str) -> FeedbackResponse | None:
    return _feedback_store.get(prediction_id)


def all_feedback() -> list[FeedbackResponse]:
    return list(_feedback_store.values())


def feedback_count() -> int:
    return len(_feedback_store)


def clear_all() -> None:
    """Clear all stores. Used in tests."""
    _predictions_store.clear()
    _feedback_store.clear()
