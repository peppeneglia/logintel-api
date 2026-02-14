"""
In-memory calibration coefficient store.

Stores versioned calibration coefficients keyed by (WeatherType, Severity).
Coefficients are clamped to [0.5, 2.0].

Will be swapped for Supabase persistence in Block 5.
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


_versions: list[CalibrationVersion] = []
_current_version: int = 0


def get_coefficient(weather_type: WeatherType, severity: Severity) -> float:
    """Return the current calibration coefficient, or 1.0 if none exists."""
    if not _versions:
        return 1.0
    current = _versions[_current_version - 1] if _current_version > 0 else None
    if current is None:
        return 1.0
    return current.coefficients.get((weather_type, severity), 1.0)


def save_version(
    coefficients: dict[tuple[WeatherType, Severity], float],
    feedback_count: int,
) -> CalibrationVersion:
    """Save a new calibration version and make it current."""
    global _current_version
    _current_version = len(_versions) + 1
    version = CalibrationVersion(
        version=_current_version,
        coefficients=coefficients,
        feedback_count=feedback_count,
    )
    _versions.append(version)
    return version


def get_current_version() -> int:
    return _current_version


def get_all_versions() -> list[CalibrationVersion]:
    return list(_versions)


def clear_all() -> None:
    """Clear all versions. Used in tests."""
    global _current_version
    _versions.clear()
    _current_version = 0
