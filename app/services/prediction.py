"""
Prediction orchestrator — FRD Section 6.

Coordinates the full prediction pipeline:
  1. ORS -> route polyline + duration
  2. Sampler -> sample points every 50km + arrival times
  3. Elevation -> altitude at each point (batch)
  4. Weather -> conditions at each point for its arrival time
  5. Heuristics -> delay per segment
  6. Confidence -> overall score

Handles partial failures: elevation fallback -> 200m, weather fallback -> clear sky.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import app.stores as stores
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
from app.engine.special_elements import (
    compute_special_element_modifier,
    find_elements_for_segment,
)
from app.models.schemas import (
    AlternativeRoute,
    Coordinate,
    PredictionResponse,
    RoadType,
    SegmentDetail,
    SegmentFactors,
    SpecialElement,
    WeatherCondition,
)
from app.services.elevation import DEFAULT_ELEVATION_M, get_elevations
from app.services.ors import RouteResult, get_road_type_at_fraction, get_route, get_routes
from app.services.overpass import get_special_elements
from app.services.weather import get_weather_at_points

logger = logging.getLogger(__name__)


async def build_prediction(
    origin: Coordinate,
    destination: Coordinate,
    departure_time: datetime,
    include_alternatives: bool = False,
) -> PredictionResponse:
    """
    Execute the full prediction pipeline and return a PredictionResponse.

    Steps:
      1. Get route(s) from ORS (polyline + duration + road types)
      2. Sample points along the polyline at configured interval
      3. Fetch elevations for all sample points (batch)
      4. Fetch weather at each point for its estimated arrival time
      5. Calculate delay for each segment using heuristics
      6. Compute confidence score
      7. Build alternatives if delay exceeds threshold
      8. Assemble and return PredictionResponse
    """
    settings = get_settings()
    t_pipeline = time.monotonic()

    # --- Step 1: Route(s) ---
    t0 = time.monotonic()
    all_routes = await get_routes(origin, destination, include_alternatives)
    route = all_routes[0]
    logger.info("[TIMING] ors_route: %.3fs", time.monotonic() - t0)

    # --- Step 2: Sample points ---
    t0 = time.monotonic()
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
    logger.info("[TIMING] sampling: %.3fs (%d points)", time.monotonic() - t0, len(sample_points))

    # --- Steps 3/3.5/4: Elevation + Special Elements + Weather (parallel) ---
    t0 = time.monotonic()

    async def _fetch_elevation() -> tuple[list[float], bool]:
        try:
            elev = await get_elevations(
                sample_points, base_url=settings.open_elevation_base_url
            )
            return elev, False
        except Exception:
            logger.warning("Elevation service unavailable — using defaults")
            return [DEFAULT_ELEVATION_M] * len(sample_points), True

    async def _fetch_special_elements() -> list[SpecialElement]:
        if not settings.enable_special_elements:
            return []
        try:
            return await get_special_elements(route.polyline)
        except Exception:
            logger.warning("Special elements fetch failed — skipping")
            return []

    async def _fetch_weather() -> list[list[WeatherCondition]]:
        return await get_weather_at_points(
            sample_points, arrival_times, base_url=settings.open_meteo_base_url
        )

    (elevations, elevation_failed), special_elements, weather_per_point = (
        await asyncio.gather(
            _fetch_elevation(),
            _fetch_special_elements(),
            _fetch_weather(),
        )
    )
    logger.info(
        "[TIMING] parallel(elevation+special+weather): %.3fs",
        time.monotonic() - t0,
    )

    # --- Step 5: Build segments ---
    t0 = time.monotonic()
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
        cal_factor = await _get_segment_calibration_factor(conditions)

        # Special element modifiers (FRD 6.4)
        se_weather_mult = 1.0
        se_time_mult = 1.0
        se_factors = []
        if special_elements:
            seg_elements = find_elements_for_segment(
                start_pt, end_pt, special_elements
            )
            if seg_elements:
                se_weather_mult, se_time_mult, se_factors = (
                    compute_special_element_modifier(seg_elements, conditions)
                )

        # Calculate delay
        delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=seg_km,
            road_type=road_type,
            altitude_m=altitude,
            arrival_time=est_arrival,
            calibration_factor=cal_factor,
            special_element_weather_multiplier=se_weather_mult,
            special_element_time_multiplier=se_time_mult,
        )
        total_delay += delay

        factors = SegmentFactors(
            road_type=road_type,
            road_factor=get_road_factor(road_type),
            altitude_m=altitude,
            altitude_factor=get_altitude_factor(altitude),
            time_factor=get_time_factor(est_arrival),
            calibration_factor=cal_factor,
            special_elements=se_factors,
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

    logger.info("[TIMING] heuristics: %.3fs", time.monotonic() - t0)

    # --- Step 6: Confidence ---
    t0 = time.monotonic()
    total_points = len(segment_lengths)
    historical_accuracy = await _compute_real_historical_accuracy()
    confidence = compute_confidence(
        departure=departure_time,
        weather_values=weather_values if weather_values else [0.0],
        total_points=total_points,
        successful_points=successful_points,
        historical_accuracy=historical_accuracy,
    )

    logger.info("[TIMING] confidence: %.3fs", time.monotonic() - t0)

    # --- Step 7: Alternatives (lightweight pipeline) ---
    t0 = time.monotonic()
    alternatives: list[AlternativeRoute] = []
    if (
        include_alternatives
        and total_delay > settings.alternative_delay_threshold_minutes
        and len(all_routes) > 1
    ):
        alternatives = await _build_alternatives(
            all_routes[1:], departure_time, total_delay
        )
    logger.info("[TIMING] alternatives: %.3fs", time.monotonic() - t0)

    logger.info("[TIMING] pipeline_total: %.3fs", time.monotonic() - t_pipeline)

    # --- Step 8: Assemble response ---
    return PredictionResponse(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        total_delay_minutes=round(total_delay, 2),
        confidence=confidence,
        segments=segments,
        alternatives=alternatives,
    )


async def _compute_route_delay(route: RouteResult, departure_time: datetime) -> float:
    """
    Lightweight pipeline: compute total delay for a route without building SegmentDetail.

    Same logic as the main pipeline (sample → elevation → weather → heuristics)
    but only returns the total delay minutes.
    """
    settings = get_settings()

    sample_points = sample_points_from_polyline(
        route.polyline, interval_km=float(settings.sampling_interval_km)
    )
    if len(sample_points) < 2:
        return 0.0

    arrival_times = estimate_arrival_times(
        sample_points, departure_time, total_duration_seconds=route.duration_s
    )

    async def _elev():
        try:
            return await get_elevations(sample_points, base_url=settings.open_elevation_base_url)
        except Exception:
            return [DEFAULT_ELEVATION_M] * len(sample_points)

    async def _se():
        if not settings.enable_special_elements:
            return []
        try:
            return await get_special_elements(route.polyline)
        except Exception:
            return []

    async def _wx():
        return await get_weather_at_points(
            sample_points, arrival_times, base_url=settings.open_meteo_base_url
        )

    elevations, special_elements, weather_per_point = await asyncio.gather(
        _elev(), _se(), _wx()
    )

    segment_lengths = compute_segment_lengths(sample_points)
    total_distance = route.distance_m / 1000.0
    total_delay = 0.0

    for i in range(len(segment_lengths)):
        seg_km = segment_lengths[i]
        start_pt = sample_points[i]
        end_pt = sample_points[i + 1]
        altitude = elevations[i] if i < len(elevations) else DEFAULT_ELEVATION_M
        fraction = (sum(segment_lengths[:i]) / total_distance) if total_distance > 0 else 0.0
        road_type = get_road_type_at_fraction(route.road_types, fraction)
        conditions = weather_per_point[i] if i < len(weather_per_point) else []
        cal_factor = await _get_segment_calibration_factor(conditions)

        # Special element modifiers
        se_weather_mult = 1.0
        se_time_mult = 1.0
        if special_elements:
            seg_elements = find_elements_for_segment(
                start_pt, end_pt, special_elements
            )
            if seg_elements:
                se_weather_mult, se_time_mult, _ = (
                    compute_special_element_modifier(seg_elements, conditions)
                )

        delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=seg_km,
            road_type=road_type,
            altitude_m=altitude,
            arrival_time=arrival_times[i],
            calibration_factor=cal_factor,
            special_element_weather_multiplier=se_weather_mult,
            special_element_time_multiplier=se_time_mult,
        )
        total_delay += delay

    return round(total_delay, 2)


async def _build_alternatives(
    alt_routes: list[RouteResult],
    departure_time: datetime,
    main_delay: float,
) -> list[AlternativeRoute]:
    """Build AlternativeRoute objects for each alternative route."""
    alternatives: list[AlternativeRoute] = []

    for idx, route in enumerate(alt_routes, start=1):
        alt_delay = await _compute_route_delay(route, departure_time)
        savings = round(main_delay - alt_delay, 2)
        summary = _build_alternative_summary(idx, savings, route)

        alternatives.append(
            AlternativeRoute(
                route_index=idx,
                total_delay_minutes=alt_delay,
                duration_minutes=round(route.duration_s / 60.0, 1),
                distance_km=round(route.distance_m / 1000.0, 1),
                delay_savings_minutes=savings,
                summary=summary,
            )
        )

    return alternatives


def _build_alternative_summary(
    index: int, savings: float, route: RouteResult
) -> str:
    """Build a human-readable summary for an alternative route."""
    dist_km = round(route.distance_m / 1000.0, 1)
    dur_min = round(route.duration_s / 60.0, 0)
    if savings > 0:
        return (
            f"Alternative {index}: {dist_km} km, {dur_min:.0f} min base travel, "
            f"saves {savings:.0f} min delay"
        )
    return (
        f"Alternative {index}: {dist_km} km, {dur_min:.0f} min base travel, "
        f"no delay improvement"
    )


async def _get_segment_calibration_factor(conditions: list[WeatherCondition]) -> float:
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

    return await stores.calibration_store.get_coefficient(best_key[0], best_key[1])


async def _compute_real_historical_accuracy() -> float:
    """
    Compute historical accuracy from real feedback data.

    Returns default 70.0 if insufficient feedback.
    """
    all_fb = await stores.prediction_store.all_feedback()
    inputs: list[CalibrationInput] = []
    for fb in all_fb:
        pred = await stores.prediction_store.get_prediction(fb.prediction_id)
        if pred is not None:
            inputs.append(CalibrationInput(prediction=pred, feedback=fb))

    return compute_historical_accuracy(inputs)
