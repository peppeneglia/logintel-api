"""
Prediction orchestrator — FRD Section 6.

Coordinates the full prediction pipeline:
  1. ORS → route polyline + duration
  2. Sampler → sample points every 50km + arrival times
  3. Elevation → altitude at each point (batch)
  4. Weather → conditions at each point for its arrival time
  5. Heuristics → delay per segment
  6. Confidence → overall score

Handles partial failures: elevation fallback → 200m, weather fallback → clear sky.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.config import get_settings
from app.engine.calibration import CalibrationInput, compute_historical_accuracy
from app.engine.confidence import compute_confidence
from app.engine.heuristics import (
    WEATHER_IMPACT,
    calculate_segment_delay,
    get_altitude_factor,
    get_road_factor,
    get_time_factor,
)
from app.engine.sampler import (
    compute_segment_lengths,
    estimate_arrival_times,
    sample_points_from_polyline,
)
from app.models.schemas import (
    Coordinate,
    PredictionResponse,
    RoadType,
    SegmentDetail,
    SegmentFactors,
    WeatherCondition,
)
from app.services.elevation import DEFAULT_ELEVATION_M, get_elevations
from app.services.ors import get_road_type_at_fraction, get_route
from app.services.weather import get_weather_at_points
from app.stores import calibration_store, prediction_store

logger = logging.getLogger(__name__)


async def build_prediction(
    origin: Coordinate,
    destination: Coordinate,
    departure_time: datetime,
) -> PredictionResponse:
    """
    Execute the full prediction pipeline and return a PredictionResponse.

    Steps:
      1. Get route from ORS (polyline + duration + road types)
      2. Sample points along the polyline at configured interval
      3. Fetch elevations for all sample points (batch)
      4. Fetch weather at each point for its estimated arrival time
      5. Calculate delay for each segment using heuristics
      6. Compute confidence score
      7. Assemble and return PredictionResponse
    """
    settings = get_settings()

    # --- Step 1: Route ---
    route = await get_route(origin, destination)

    # --- Step 2: Sample points ---
    sample_points = sample_points_from_polyline(
        route.polyline, interval_km=float(settings.sampling_interval_km)
    )

    if len(sample_points) < 2:
        sample_points = [
            Coordinate(lat=origin.lat, lon=origin.lon),
            Coordinate(lat=destination.lat, lon=destination.lon),
        ]

    arrival_times = estimate_arrival_times(
        sample_points, departure_time, total_duration_seconds=route.duration_s
    )

    # --- Step 3: Elevation (batch, with fallback) ---
    elevation_failed = False
    try:
        elevations = await get_elevations(
            sample_points, base_url=settings.open_elevation_base_url
        )
    except Exception:
        logger.warning("Elevation service unavailable — using defaults")
        elevations = [DEFAULT_ELEVATION_M] * len(sample_points)
        elevation_failed = True

    # --- Step 4: Weather (per-point, with fallback) ---
    weather_per_point = await get_weather_at_points(
        sample_points, arrival_times, base_url=settings.open_meteo_base_url
    )

    # --- Step 5: Build segments ---
    segment_lengths = compute_segment_lengths(sample_points)
    total_distance = route.distance_m / 1000.0  # km

    segments: list[SegmentDetail] = []
    total_delay = 0.0
    weather_values: list[float] = []
    successful_points = 0

    for i in range(len(segment_lengths)):
        seg_km = segment_lengths[i]
        start_pt = sample_points[i]
        end_pt = sample_points[i + 1]
        est_arrival = arrival_times[i]
        altitude = elevations[i] if i < len(elevations) else DEFAULT_ELEVATION_M

        # Road type from ORS route info at this fraction of the route
        fraction = (sum(segment_lengths[:i]) / total_distance) if total_distance > 0 else 0.0
        road_type = get_road_type_at_fraction(route.road_types, fraction)

        # Weather conditions for this segment
        conditions: list[WeatherCondition] = (
            weather_per_point[i] if i < len(weather_per_point) else []
        )

        # Track weather values for stability scoring
        seg_weather_val = sum(c.raw_value for c in conditions) if conditions else 0.0
        weather_values.append(seg_weather_val)

        if conditions or not elevation_failed:
            successful_points += 1

        # Get dynamic calibration factor from dominant condition
        cal_factor = _get_segment_calibration_factor(conditions)

        # Calculate delay
        delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=seg_km,
            road_type=road_type,
            altitude_m=altitude,
            arrival_time=est_arrival,
            calibration_factor=cal_factor,
        )
        total_delay += delay

        factors = SegmentFactors(
            road_type=road_type,
            road_factor=get_road_factor(road_type),
            altitude_m=altitude,
            altitude_factor=get_altitude_factor(altitude),
            time_factor=get_time_factor(est_arrival),
            calibration_factor=cal_factor,
        )

        segments.append(
            SegmentDetail(
                index=i,
                start_point=start_pt,
                end_point=end_pt,
                length_km=round(seg_km, 2),
                estimated_arrival=est_arrival,
                weather=conditions,
                factors=factors,
                delay_minutes=delay,
            )
        )

    # --- Step 6: Confidence ---
    total_points = len(segment_lengths)
    historical_accuracy = _compute_real_historical_accuracy()
    confidence = compute_confidence(
        departure=departure_time,
        weather_values=weather_values if weather_values else [0.0],
        total_points=total_points,
        successful_points=successful_points,
        historical_accuracy=historical_accuracy,
    )

    # --- Step 7: Assemble response ---
    return PredictionResponse(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        total_delay_minutes=round(total_delay, 2),
        confidence=confidence,
        segments=segments,
    )


def _get_segment_calibration_factor(conditions: list[WeatherCondition]) -> float:
    """
    Get the calibration factor for a segment based on its dominant weather condition.

    The dominant condition is the one with the highest base_impact.
    Returns 1.0 if no conditions or no calibration data.
    """
    if not conditions:
        return 1.0

    best_impact = 0.0
    best_key = None

    for c in conditions:
        impact = WEATHER_IMPACT.get(c.type, {}).get(c.severity, 0.0)
        if impact > best_impact:
            best_impact = impact
            best_key = (c.type, c.severity)

    if best_key is None:
        return 1.0

    return calibration_store.get_coefficient(best_key[0], best_key[1])


def _compute_real_historical_accuracy() -> float:
    """
    Compute historical accuracy from real feedback data.

    Returns default 70.0 if insufficient feedback.
    """
    all_fb = prediction_store.all_feedback()
    inputs: list[CalibrationInput] = []
    for fb in all_fb:
        pred = prediction_store.get_prediction(fb.prediction_id)
        if pred is not None:
            inputs.append(CalibrationInput(prediction=pred, feedback=fb))

    return compute_historical_accuracy(inputs)
