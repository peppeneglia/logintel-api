"""
In-memory store implementations.

Used in development and tests, or as a fallback when Supabase is not configured.
Data is lost on restart. Async methods mirror the Supabase stores; the
``*_sync`` helpers exist only to simplify test setup.
"""

from __future__ import annotations

from app.models.schemas import FeedbackResponse, PredictionResponse
from app.stores.base import CalibrationVersion, CoefficientKey, FeedbackPair


class InMemoryPredictionStore:
    """In-memory prediction and feedback store, scoped by organization."""

    def __init__(self) -> None:
        # Keyed by prediction id; values carry the owning organization id.
        self._predictions: dict[str, tuple[str, PredictionResponse]] = {}
        self._feedback: dict[str, tuple[str, FeedbackResponse]] = {}

    async def save_prediction(self, prediction: PredictionResponse, org_id: str) -> None:
        self.save_prediction_sync(prediction, org_id)

    async def get_prediction(self, prediction_id: str, org_id: str) -> PredictionResponse | None:
        entry = self._predictions.get(prediction_id)
        if entry is None or entry[0] != org_id:
            return None
        return entry[1]

    async def list_predictions(self, org_id: str, limit: int, offset: int) -> list[PredictionResponse]:
        items = [p for owner, p in self._predictions.values() if owner == org_id]
        items.sort(key=lambda p: p.created_at, reverse=True)
        return items[offset : offset + limit]

    async def prediction_count(self, org_id: str) -> int:
        return sum(1 for owner, _ in self._predictions.values() if owner == org_id)

    async def save_feedback(self, feedback: FeedbackResponse, org_id: str) -> None:
        self.save_feedback_sync(feedback, org_id)

    async def get_feedback(self, prediction_id: str, org_id: str) -> FeedbackResponse | None:
        entry = self._feedback.get(prediction_id)
        if entry is None or entry[0] != org_id:
            return None
        return entry[1]

    async def feedback_count(self, org_id: str) -> int:
        return sum(1 for owner, _ in self._feedback.values() if owner == org_id)

    async def list_feedback_pairs(self, org_id: str | None = None) -> list[FeedbackPair]:
        pairs: list[FeedbackPair] = []
        for owner, feedback in self._feedback.values():
            if org_id is not None and owner != org_id:
                continue
            prediction_entry = self._predictions.get(feedback.prediction_id)
            if prediction_entry is not None:
                pairs.append(FeedbackPair(prediction=prediction_entry[1], feedback=feedback))
        return pairs

    async def recent_feedback(self, limit: int) -> list[FeedbackResponse]:
        items = sorted((fb for _, fb in self._feedback.values()), key=lambda fb: fb.received_at, reverse=True)
        return items[:limit]

    # --- Sync helpers for test setup ---

    def save_prediction_sync(self, prediction: PredictionResponse, org_id: str = "dev") -> None:
        self._predictions[prediction.id] = (org_id, prediction)

    def save_feedback_sync(self, feedback: FeedbackResponse, org_id: str = "dev") -> None:
        self._feedback[feedback.prediction_id] = (org_id, feedback)


class InMemoryCalibrationStore:
    """In-memory, append-only calibration history."""

    def __init__(self) -> None:
        self._versions: list[CalibrationVersion] = []

    async def get_coefficients(self) -> dict[CoefficientKey, float]:
        if not self._versions:
            return {}
        return dict(self._versions[-1].coefficients)

    async def save_version(
        self, coefficients: dict[CoefficientKey, float], feedback_count: int
    ) -> CalibrationVersion:
        version = CalibrationVersion(
            version=len(self._versions) + 1,
            coefficients=dict(coefficients),
            feedback_count=feedback_count,
        )
        self._versions.append(version)
        return version

    async def get_current_version(self) -> int:
        return len(self._versions)
