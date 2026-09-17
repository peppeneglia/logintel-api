"""
Store interfaces shared by the in-memory and Supabase implementations.

Predictions and feedback are always scoped to an organization.
Calibration coefficients are global: every organization's feedback
contributes to (and benefits from) the same calibration history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.models.schemas import FeedbackResponse, PredictionResponse, Severity, WeatherType

CoefficientKey = tuple[WeatherType, Severity]


@dataclass
class CalibrationVersion:
    version: int
    coefficients: dict[CoefficientKey, float]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    feedback_count: int = 0


@dataclass
class FeedbackPair:
    """A feedback entry joined with the prediction it refers to."""

    prediction: PredictionResponse
    feedback: FeedbackResponse


class PredictionStore(Protocol):
    async def save_prediction(self, prediction: PredictionResponse, org_id: str) -> None: ...

    async def get_prediction(self, prediction_id: str, org_id: str) -> PredictionResponse | None: ...

    async def list_predictions(self, org_id: str, limit: int, offset: int) -> list[PredictionResponse]: ...

    async def prediction_count(self, org_id: str) -> int: ...

    async def save_feedback(self, feedback: FeedbackResponse, org_id: str) -> None: ...

    async def get_feedback(self, prediction_id: str, org_id: str) -> FeedbackResponse | None: ...

    async def feedback_count(self, org_id: str) -> int: ...

    async def list_feedback_pairs(self, org_id: str | None = None) -> list[FeedbackPair]:
        """Return feedback joined with its prediction; all organizations when org_id is None."""
        ...

    async def recent_feedback(self, limit: int) -> list[FeedbackResponse]:
        """Return the most recent feedback entries across all organizations."""
        ...


class CalibrationStore(Protocol):
    async def get_coefficients(self) -> dict[CoefficientKey, float]:
        """Return the coefficients of the current calibration version (empty if none)."""
        ...

    async def save_version(
        self, coefficients: dict[CoefficientKey, float], feedback_count: int
    ) -> CalibrationVersion: ...

    async def get_current_version(self) -> int: ...
