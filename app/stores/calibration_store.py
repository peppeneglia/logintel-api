"""
Calibration store constants and dataclass.

Store logic now lives in app/stores/memory.py and app/stores/supabase_store.py.
Access the active store via ``from app.stores import calibration_store``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.schemas import Severity, WeatherType

COEFF_LOWER = 0.5
COEFF_UPPER = 2.0


@dataclass
class CalibrationVersion:
    version: int
    coefficients: dict[tuple[WeatherType, Severity], float]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    feedback_count: int = 0
