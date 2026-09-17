"""
Supabase-backed store implementations.

Uses the PostgREST client from app.services.supabase for CRUD.
PredictionResponse is stored as JSONB via model_dump/model_validate.
Calibration coefficients use string keys ("rain:moderate") in JSONB,
converted to/from tuple[WeatherType, Severity] at the boundary.
"""

from __future__ import annotations

from typing import Any

from app.models.schemas import FeedbackResponse, PredictionResponse, Severity, WeatherType
from app.services import supabase
from app.stores.base import CalibrationVersion, CoefficientKey, FeedbackPair


def _coeff_key_to_str(key: CoefficientKey) -> str:
    return f"{key[0].value}:{key[1].value}"


def _str_to_coeff_key(s: str) -> CoefficientKey:
    wt_str, sev_str = s.split(":")
    return (WeatherType(wt_str), Severity(sev_str))


def _row_to_feedback(row: dict[str, Any]) -> FeedbackResponse:
    return FeedbackResponse(
        prediction_id=row["prediction_id"],
        actual_delay_minutes=row["actual_delay_minutes"],
        predicted_delay_minutes=row["predicted_delay_minutes"],
        deviation_minutes=row["deviation_minutes"],
        notes=row.get("notes"),
        received_at=row["received_at"],
    )


class SupabasePredictionStore:
    """Supabase-backed prediction and feedback store."""

    async def save_prediction(self, prediction: PredictionResponse, org_id: str) -> None:
        await supabase.insert(
            "predictions",
            {
                "id": prediction.id,
                "organization_id": org_id,
                "data": prediction.model_dump(mode="json"),
                "total_delay_minutes": prediction.total_delay_minutes,
                "departure_time": prediction.departure_time.isoformat(),
            },
        )

    async def get_prediction(self, prediction_id: str, org_id: str) -> PredictionResponse | None:
        row = await supabase.select(
            "predictions",
            params={
                "id": f"eq.{prediction_id}",
                "organization_id": f"eq.{org_id}",
                "select": "data",
            },
            single=True,
        )
        if row is None:
            return None
        return PredictionResponse.model_validate(row["data"])

    async def list_predictions(self, org_id: str, limit: int, offset: int) -> list[PredictionResponse]:
        rows = await supabase.select(
            "predictions",
            params={
                "organization_id": f"eq.{org_id}",
                "select": "data",
                "order": "created_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        if not isinstance(rows, list):
            return []
        return [PredictionResponse.model_validate(r["data"]) for r in rows]

    async def prediction_count(self, org_id: str) -> int:
        return await supabase.count("predictions", {"organization_id": f"eq.{org_id}"})

    async def save_feedback(self, feedback: FeedbackResponse, org_id: str) -> None:
        await supabase.insert(
            "feedback",
            {
                "prediction_id": feedback.prediction_id,
                "organization_id": org_id,
                "actual_delay_minutes": feedback.actual_delay_minutes,
                "predicted_delay_minutes": feedback.predicted_delay_minutes,
                "deviation_minutes": feedback.deviation_minutes,
                "notes": feedback.notes,
            },
        )

    async def get_feedback(self, prediction_id: str, org_id: str) -> FeedbackResponse | None:
        row = await supabase.select(
            "feedback",
            params={
                "prediction_id": f"eq.{prediction_id}",
                "organization_id": f"eq.{org_id}",
                "select": "*",
            },
            single=True,
        )
        return _row_to_feedback(row) if row is not None else None

    async def feedback_count(self, org_id: str) -> int:
        return await supabase.count("feedback", {"organization_id": f"eq.{org_id}"})

    async def list_feedback_pairs(self, org_id: str | None = None) -> list[FeedbackPair]:
        # Embed the referenced prediction to avoid one extra query per feedback row.
        params = {"select": "*,predictions(data)"}
        if org_id is not None:
            params["organization_id"] = f"eq.{org_id}"
        rows = await supabase.select("feedback", params=params)
        if not isinstance(rows, list):
            return []
        return [
            FeedbackPair(
                prediction=PredictionResponse.model_validate(row["predictions"]["data"]),
                feedback=_row_to_feedback(row),
            )
            for row in rows
            if row.get("predictions")
        ]

    async def recent_feedback(self, limit: int) -> list[FeedbackResponse]:
        rows = await supabase.select(
            "feedback",
            params={"select": "*", "order": "received_at.desc", "limit": str(limit)},
        )
        return [_row_to_feedback(r) for r in rows] if isinstance(rows, list) else []


class SupabaseCalibrationStore:
    """Supabase-backed, append-only calibration history."""

    async def get_coefficients(self) -> dict[CoefficientKey, float]:
        row = await supabase.select(
            "calibration_versions",
            params={"select": "coefficients", "order": "version.desc", "limit": "1"},
            single=True,
        )
        if row is None:
            return {}
        return {_str_to_coeff_key(k): v for k, v in row["coefficients"].items()}

    async def save_version(
        self, coefficients: dict[CoefficientKey, float], feedback_count: int
    ) -> CalibrationVersion:
        row = await supabase.insert(
            "calibration_versions",
            {
                "coefficients": {_coeff_key_to_str(k): v for k, v in coefficients.items()},
                "feedback_count": feedback_count,
            },
        )
        return CalibrationVersion(
            version=row["version"],
            coefficients=dict(coefficients),
            feedback_count=feedback_count,
            created_at=row["created_at"],
        )

    async def get_current_version(self) -> int:
        row = await supabase.select(
            "calibration_versions",
            params={"select": "version", "order": "version.desc", "limit": "1"},
            single=True,
        )
        return row["version"] if row is not None else 0
