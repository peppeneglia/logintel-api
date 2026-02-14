"""
In-memory store implementations.

Used in development/testing or as fallback when Supabase is not configured.
All methods are async for interface compatibility with Supabase stores.
sync_* convenience methods are provided for test setup code.
"""

from __future__ import annotations

from app.models.schemas import FeedbackResponse, PredictionResponse, Severity, WeatherType
from app.stores.calibration_store import CalibrationVersion, COEFF_LOWER, COEFF_UPPER


class InMemoryPredictionStore:
    """In-memory prediction and feedback store."""

    def __init__(self) -> None:
        self._predictions: dict[str, PredictionResponse] = {}
        self._feedback: dict[str, FeedbackResponse] = {}

    # --- Async interface ---

    async def save_prediction(self, prediction: PredictionResponse, org_id: str = "") -> None:
        self._predictions[prediction.id] = prediction

    async def get_prediction(self, prediction_id: str, org_id: str = "") -> PredictionResponse | None:
        return self._predictions.get(prediction_id)

    async def list_predictions(self, org_id: str = "") -> list[PredictionResponse]:
        items = list(self._predictions.values())
        items.sort(key=lambda p: p.created_at, reverse=True)
        return items

    async def prediction_count(self, org_id: str = "") -> int:
        return len(self._predictions)

    async def save_feedback(self, feedback: FeedbackResponse, org_id: str = "") -> None:
        self._feedback[feedback.prediction_id] = feedback

    async def get_feedback(self, prediction_id: str, org_id: str = "") -> FeedbackResponse | None:
        return self._feedback.get(prediction_id)

    async def all_feedback(self, org_id: str = "") -> list[FeedbackResponse]:
        return list(self._feedback.values())

    async def feedback_count(self, org_id: str = "") -> int:
        return len(self._feedback)

    async def clear_all(self) -> None:
        self._predictions.clear()
        self._feedback.clear()

    # --- Sync helpers for test setup ---

    def save_prediction_sync(self, prediction: PredictionResponse, org_id: str = "") -> None:
        self._predictions[prediction.id] = prediction

    def save_feedback_sync(self, feedback: FeedbackResponse, org_id: str = "") -> None:
        self._feedback[feedback.prediction_id] = feedback

    def clear_all_sync(self) -> None:
        self._predictions.clear()
        self._feedback.clear()


class InMemoryCalibrationStore:
    """In-memory calibration coefficient store."""

    def __init__(self) -> None:
        self._versions: list[CalibrationVersion] = []
        self._current_version: int = 0

    # --- Async interface ---

    async def get_coefficient(self, weather_type: WeatherType, severity: Severity, org_id: str = "") -> float:
        if not self._versions:
            return 1.0
        current = self._versions[self._current_version - 1] if self._current_version > 0 else None
        if current is None:
            return 1.0
        return current.coefficients.get((weather_type, severity), 1.0)

    async def save_version(
        self,
        coefficients: dict[tuple[WeatherType, Severity], float],
        feedback_count: int,
        org_id: str = "",
    ) -> CalibrationVersion:
        self._current_version = len(self._versions) + 1
        version = CalibrationVersion(
            version=self._current_version,
            coefficients=coefficients,
            feedback_count=feedback_count,
        )
        self._versions.append(version)
        return version

    async def get_current_version(self, org_id: str = "") -> int:
        return self._current_version

    async def get_all_versions(self, org_id: str = "") -> list[CalibrationVersion]:
        return list(self._versions)

    async def clear_all(self) -> None:
        self._versions.clear()
        self._current_version = 0

    # --- Sync helpers for test setup ---

    def clear_all_sync(self) -> None:
        self._versions.clear()
        self._current_version = 0
