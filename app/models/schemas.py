from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class SpecialElementType(str, Enum):
    TUNNEL = "tunnel"
    BRIDGE = "bridge"
    MOUNTAIN_PASS = "mountain_pass"
    URBAN_CENTER = "urban_center"


class ConfidenceLevel(str, Enum):
    HIGH = "high"           # 85-100%
    GOOD = "good"           # 70-84%
    MODERATE = "moderate"   # 55-69%
    LOW = "low"             # <55%


# --- Coordinate ---

class Coordinate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"lat": 45.4642, "lon": 9.1900}],
    })

    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lon: float = Field(..., ge=-180, le=180, description="Longitude")


# --- Request models ---

class PredictionRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "origin": {"lat": 45.4642, "lon": 9.1900},
                "destination": {"lat": 41.9028, "lon": 12.4964},
                "departure_time": "2026-02-15T08:00:00+01:00",
                "include_alternatives": False,
            }
        ],
    })

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
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "actual_delay_minutes": 35,
                "notes": "Heavy rain near Florence caused slowdown",
            }
        ],
    })

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


class SpecialElement(BaseModel):
    type: SpecialElementType
    name: Optional[str] = None
    length_m: Optional[float] = None  # For tunnels
    lat: float
    lon: float


class SpecialElementFactor(BaseModel):
    element_type: SpecialElementType
    element_name: Optional[str] = None
    multiplier: float


class SegmentFactors(BaseModel):
    road_type: RoadType
    road_factor: float
    altitude_m: float
    altitude_factor: float
    time_factor: float
    calibration_factor: float = 1.0
    special_elements: list[SpecialElementFactor] = Field(default_factory=list)


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
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "status": "completed",
                "origin": {"lat": 45.4642, "lon": 9.1900},
                "destination": {"lat": 41.9028, "lon": 12.4964},
                "departure_time": "2026-02-15T08:00:00+01:00",
                "total_delay_minutes": 28.5,
                "confidence": {
                    "overall": 82.3,
                    "level": "good",
                    "components": {
                        "time_horizon": 95.0,
                        "weather_stability": 72.0,
                        "historical_accuracy": 70.0,
                        "data_completeness": 100.0,
                    },
                },
                "segments": [
                    {
                        "index": 0,
                        "start_point": {"lat": 45.4642, "lon": 9.1900},
                        "end_point": {"lat": 45.0, "lon": 9.8},
                        "length_km": 50.0,
                        "estimated_arrival": "2026-02-15T08:40:00+01:00",
                        "weather": [
                            {
                                "type": "rain",
                                "severity": "moderate",
                                "raw_value": 4.5,
                                "description": "Moderate rain (4.5 mm/h)",
                            }
                        ],
                        "factors": {
                            "road_type": "highway",
                            "road_factor": 0.8,
                            "altitude_m": 120.0,
                            "altitude_factor": 1.0,
                            "time_factor": 1.0,
                            "calibration_factor": 1.0,
                            "special_elements": [],
                        },
                        "delay_minutes": 3.2,
                    }
                ],
                "alternatives": [],
                "created_at": "2026-02-15T07:00:00Z",
            }
        ],
    })

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
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "prediction_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "actual_delay_minutes": 35,
                "predicted_delay_minutes": 28.5,
                "deviation_minutes": 6.5,
                "received_at": "2026-02-15T18:30:00Z",
            }
        ],
    })

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
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "total_predictions": 150,
                "total_feedback": 42,
                "feedback_rate": 28.0,
                "mae": 8.3,
                "within_10min_pct": 71.4,
                "within_20min_pct": 90.5,
                "calibration_version": 3,
                "breakdown_by_weather": [
                    {
                        "weather_type": "rain",
                        "count": 18,
                        "mae": 6.2,
                        "within_10min_pct": 77.8,
                        "within_20min_pct": 94.4,
                    },
                    {
                        "weather_type": "snow",
                        "count": 10,
                        "mae": 14.1,
                        "within_10min_pct": 50.0,
                        "within_20min_pct": 80.0,
                    },
                ],
            }
        ],
    })

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
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "code": "INVALID_REQUEST",
                "message": "departure_time must include timezone info",
                "details": [
                    {"field": "departure_time", "message": "Value error, departure_time must include timezone info"}
                ],
            }
        ],
    })

    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


# Rebuild ErrorResponse
ErrorResponse.model_rebuild()
