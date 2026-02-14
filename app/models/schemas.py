from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# --- Enums ---

class WeatherType(str, Enum):
    RAIN = "rain"
    SNOW = "snow"
    WIND = "wind"
    FOG = "fog"


class Severity(str, Enum):
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    VERY_HEAVY = "very_heavy"


class RoadType(str, Enum):
    HIGHWAY = "highway"
    STATE_ROAD = "state_road"
    PROVINCIAL = "provincial"
    MOUNTAIN = "mountain"


class ConfidenceLevel(str, Enum):
    HIGH = "high"           # 85-100%
    GOOD = "good"           # 70-84%
    MODERATE = "moderate"   # 55-69%
    LOW = "low"             # <55%


# --- Coordinate ---

class Coordinate(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lon: float = Field(..., ge=-180, le=180, description="Longitude")


# --- Request models ---

class PredictionRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    departure_time: datetime = Field(
        ..., description="Departure time in ISO 8601 with timezone"
    )
    include_alternatives: bool = Field(
        default=False, description="Include alternative routes if delay exceeds threshold"
    )

    @field_validator("departure_time")
    @classmethod
    def departure_must_be_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("departure_time must include timezone info")
        return v


class FeedbackRequest(BaseModel):
    actual_delay_minutes: int = Field(
        ..., ge=-60, le=1440,
        description="Actual delay in minutes (-60 to 1440)"
    )
    notes: Optional[str] = Field(
        default=None, max_length=500, description="Optional notes"
    )


# --- Response sub-models ---

class WeatherCondition(BaseModel):
    type: WeatherType
    severity: Severity
    raw_value: float = Field(..., description="Raw measurement value (mm/h, cm/h, km/h, or meters visibility)")
    description: str = Field(..., description="Human-readable description")


class SegmentFactors(BaseModel):
    road_type: RoadType
    road_factor: float
    altitude_m: float
    altitude_factor: float
    time_factor: float
    calibration_factor: float = 1.0


class SegmentDetail(BaseModel):
    index: int
    start_point: Coordinate
    end_point: Coordinate
    length_km: float
    estimated_arrival: datetime
    weather: list[WeatherCondition]
    factors: SegmentFactors
    delay_minutes: float = Field(..., description="Predicted delay for this segment")


class ConfidenceScore(BaseModel):
    overall: float = Field(..., ge=0, le=100, description="Overall confidence 0-100%")
    level: ConfidenceLevel
    components: ConfidenceComponents


class ConfidenceComponents(BaseModel):
    time_horizon: float = Field(..., ge=0, le=100)
    weather_stability: float = Field(..., ge=0, le=100)
    historical_accuracy: float = Field(..., ge=0, le=100)
    data_completeness: float = Field(..., ge=0, le=100)


# Rebuild ConfidenceScore now that ConfidenceComponents is defined
ConfidenceScore.model_rebuild()


# --- Alternative route model ---

class AlternativeRoute(BaseModel):
    route_index: int = Field(..., description="Alternative index (1, 2)")
    total_delay_minutes: float = Field(..., description="Predicted delay for this alternative")
    duration_minutes: float = Field(..., description="Base travel time without delay")
    distance_km: float = Field(..., description="Total route distance in km")
    delay_savings_minutes: float = Field(..., description="Delay saved vs main route (main_delay - alt_delay)")
    summary: str = Field(..., description="Human-readable summary")


# --- Response models ---

class PredictionResponse(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "completed"
    origin: Coordinate
    destination: Coordinate
    departure_time: datetime
    total_delay_minutes: float
    confidence: ConfidenceScore
    segments: list[SegmentDetail]
    alternatives: list[AlternativeRoute] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PredictionSummary(BaseModel):
    id: str
    origin: Coordinate
    destination: Coordinate
    departure_time: datetime
    total_delay_minutes: float
    confidence_level: ConfidenceLevel
    created_at: datetime


class PredictionListResponse(BaseModel):
    predictions: list[PredictionSummary]
    total: int
    page: int
    per_page: int


class FeedbackResponse(BaseModel):
    prediction_id: str
    actual_delay_minutes: int
    predicted_delay_minutes: float
    deviation_minutes: float
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Analytics models ---

class WeatherTypeBreakdown(BaseModel):
    weather_type: WeatherType
    count: int
    mae: float = Field(..., description="Mean absolute error in minutes")
    within_10min_pct: float = Field(..., ge=0, le=100)
    within_20min_pct: float = Field(..., ge=0, le=100)


class AnalyticsResponse(BaseModel):
    total_predictions: int
    total_feedback: int
    feedback_rate: float = Field(..., ge=0, le=100, description="Feedback rate as percentage")
    mae: float = Field(..., description="Overall mean absolute error in minutes")
    within_10min_pct: float = Field(..., ge=0, le=100)
    within_20min_pct: float = Field(..., ge=0, le=100)
    calibration_version: int
    breakdown_by_weather: list[WeatherTypeBreakdown]


# --- Error models ---

class ErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


# Rebuild ErrorResponse
ErrorResponse.model_rebuild()
