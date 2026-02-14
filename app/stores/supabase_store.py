"""
Supabase-backed store implementations.

Uses the PostgREST client from app.services.supabase for CRUD.
PredictionResponse is stored as JSONB via model_dump/model_validate.
Calibration coefficients use string keys ("rain:moderate") in JSONB,
converted to/from tuple[WeatherType, Severity] at the boundary.
"""

from __future__ import annotations

import logging

from app.models.schemas import (
    FeedbackResponse,
    PredictionResponse,
    Severity,
    WeatherType,
)
from app.services import supabase
from app.stores.calibration_store import CalibrationVersion, COEFF_LOWER, COEFF_UPPER

logger = logging.getLogger(__name__)


def _coeff_key_to_str(key: tuple[WeatherType, Severity]) -> str:
    return f"{key[0].value}:{key[1].value}"


def _str_to_coeff_key(s: str) -> tuple[WeatherType, Severity]:
    wt_str, sev_str = s.split(":")
    return (WeatherType(wt_str), Severity(sev_str))


class SupabasePredictionStore:
    """Supabase-backed prediction and feedback store."""

    async def save_prediction(self, prediction: PredictionResponse, org_id: str = "") -> None:
        await supabase.insert("predictions", {
            "id": prediction.id,
            "organization_id": org_id,
            "data": prediction.model_dump(mode="json"),
            "total_delay_minutes": prediction.total_delay_minutes,
            "departure_time": prediction.departure_time.isoformat(),
        })

    async def get_prediction(self, prediction_id: str, org_id: str = "") -> PredictionResponse | None:
        params: dict[str, str] = {"id": f"eq.{prediction_id}", "select": "data"}
        if org_id:
            params["organization_id"] = f"eq.{org_id}"
        row = await supabase.select("predictions", params=params, single=True)
        if row is None:
            return None
        return PredictionResponse.model_validate(row["data"])

    async def list_predictions(self, org_id: str = "") -> list[PredictionResponse]:
        params: dict[str, str] = {
            "select": "data",
            "order": "created_at.desc",
        }
        if org_id:
            params["organization_id"] = f"eq.{org_id}"
        rows = await supabase.select("predictions", params=params)
        if not isinstance(rows, list):
            return []
        return [PredictionResponse.model_validate(r["data"]) for r in rows]

    async def prediction_count(self, org_id: str = "") -> int:
        params: dict[str, str] = {"select": "id"}
        if org_id:
            params["organization_id"] = f"eq.{org_id}"
        rows = await supabase.select("predictions", params=params)
        return len(rows) if isinstance(rows, list) else 0

    async def save_feedback(self, feedback: FeedbackResponse, org_id: str = "") -> None:
        await supabase.insert("feedback", {
            "prediction_id": feedback.prediction_id,
            "organization_id": org_id,
            "actual_delay_minutes": feedback.actual_delay_minutes,
            "predicted_delay_minutes": feedback.predicted_delay_minutes,
            "deviation_minutes": feedback.deviation_minutes,
        })

    async def get_feedback(self, prediction_id: str, org_id: str = "") -> FeedbackResponse | None:
        params: dict[str, str] = {
            "prediction_id": f"eq.{prediction_id}",
            "select": "*",
        }
        if org_id:
            params["organization_id"] = f"eq.{org_id}"
        row = await supabase.select("feedback", params=params, single=True)
        if row is None:
            return None
        return FeedbackResponse(
            prediction_id=row["prediction_id"],
            actual_delay_minutes=row["actual_delay_minutes"],
            predicted_delay_minutes=row["predicted_delay_minutes"],
            deviation_minutes=row["deviation_minutes"],
            received_at=row["received_at"],
        )

    async def all_feedback(self, org_id: str = "") -> list[FeedbackResponse]:
        params: dict[str, str] = {"select": "*"}
        if org_id:
            params["organization_id"] = f"eq.{org_id}"
        rows = await supabase.select("feedback", params=params)
        if not isinstance(rows, list):
            return []
        return [
            FeedbackResponse(
                prediction_id=r["prediction_id"],
                actual_delay_minutes=r["actual_delay_minutes"],
                predicted_delay_minutes=r["predicted_delay_minutes"],
                deviation_minutes=r["deviation_minutes"],
                received_at=r["received_at"],
            )
            for r in rows
        ]

    async def feedback_count(self, org_id: str = "") -> int:
        params: dict[str, str] = {"select": "id"}
        if org_id:
            params["organization_id"] = f"eq.{org_id}"
        rows = await supabase.select("feedback", params=params)
        return len(rows) if isinstance(rows, list) else 0

    async def clear_all(self) -> None:
        pass  # Not implemented for production store


class SupabaseCalibrationStore:
    """Supabase-backed calibration coefficient store."""

    async def get_coefficient(self, weather_type: WeatherType, severity: Severity, org_id: str = "") -> float:
        row = await supabase.select(
            "calibration_versions",
            params={"select": "coefficients", "order": "version.desc", "limit": "1"},
            single=True,
        )
        if row is None:
            return 1.0
        key_str = _coeff_key_to_str((weather_type, severity))
        return row["coefficients"].get(key_str, 1.0)

    async def save_version(
        self,
        coefficients: dict[tuple[WeatherType, Severity], float],
        feedback_count: int,
        org_id: str = "",
    ) -> CalibrationVersion:
        json_coeffs = {_coeff_key_to_str(k): v for k, v in coefficients.items()}
        row = await supabase.insert("calibration_versions", {
            "coefficients": json_coeffs,
            "feedback_count": feedback_count,
        })
        # Convert back to CalibrationVersion
        tuple_coeffs = {_str_to_coeff_key(k): v for k, v in json_coeffs.items()}
        return CalibrationVersion(
            version=row["version"],
            coefficients=tuple_coeffs,
            feedback_count=feedback_count,
        )

    async def get_current_version(self, org_id: str = "") -> int:
        row = await supabase.select(
            "calibration_versions",
            params={"select": "version", "order": "version.desc", "limit": "1"},
            single=True,
        )
        if row is None:
            return 0
        return row["version"]

    async def get_all_versions(self, org_id: str = "") -> list[CalibrationVersion]:
        rows = await supabase.select(
            "calibration_versions",
            params={"select": "*", "order": "version.asc"},
        )
        if not isinstance(rows, list):
            return []
        versions = []
        for r in rows:
            tuple_coeffs = {_str_to_coeff_key(k): v for k, v in r["coefficients"].items()}
            versions.append(CalibrationVersion(
                version=r["version"],
                coefficients=tuple_coeffs,
                feedback_count=r["feedback_count"],
                created_at=r["created_at"],
            ))
        return versions

    async def clear_all(self) -> None:
        pass  # Not implemented for production store
