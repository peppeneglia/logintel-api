"""
Prediction orchestrator.

Coordinates the full prediction pipeline:
  1. ORS -> route polyline, duration and road types (plus alternatives)
  2. Sampler -> points every N km and their estimated arrival times
  3. Elevation, special elements and weather -> fetched concurrently
  4. Heuristics -> delay per segment
  5. Confidence -> overall reliability score
  6. Alternatives -> evaluated only when the main delay exceeds the threshold

Handles partial failures: elevation falls back to 200 m, missing weather is
treated as clear sky and lowers the data-completeness score, and special
elements are skipped when Overpass is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import app.stores as stores
from app.config import get_settings
from app.engine.calibration import compute_historical_accuracy, dominant_condition
from app.engine.confidence import compute_confidence
from app.engine.heuristics import (
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
    SegmentDetail,
    SegmentFactors,
    SpecialElement,
)
from app.services.elevation import get_elevations
from app.services.ors import RouteResult, get_road_type_at_fraction, get_routes
from app.services.overpass import get_special_elements
from app.services.weather import get_weather_at_points
from app.stores.base import CoefficientKey

logger = logging.getLogger(__name__)

# Number of most recent feedback entries used for the historical-accuracy score
HISTORICAL_ACCURACY_WINDOW = 500


@dataclass
class RouteEvaluation:
    """Result of running the delay heuristics over one route."""

    segments: list[SegmentDetail]
    total_delay_minutes: float
    weather_values: list[float]
    points_with_weather: int


async def build_prediction(
    origin: Coordinate,
    destination: Coordinate,
    departure_time: datetime,
    include_alternatives: bool = False,
) -> PredictionResponse:
    """Execute the full prediction pipeline and return a PredictionResponse."""
    settings = get_settings()
    t_pipeline = time.monotonic()

    routes, coefficients, recent_feedback = await asyncio.gather(
        get_routes(origin, destination, include_alternatives),
        stores.calibration_store.get_coefficients(),
        stores.prediction_store.recent_feedback(limit=HISTORICAL_ACCURACY_WINDOW),
    )
    evaluation = await evaluate_route(routes[0], departure_time, coefficients)

    confidence = compute_confidence(
        departure=departure_time,
        weather_values=evaluation.weather_values or [0.0],
        total_points=len(evaluation.segments),
        successful_points=evaluation.points_with_weather,
        historical_accuracy=compute_historical_accuracy(recent_feedback),
    )

    alternatives: list[AlternativeRoute] = []
    if (
        include_alternatives
        and len(routes) > 1
        and evaluation.total_delay_minutes > settings.alternative_delay_threshold_minutes
    ):
        alternatives = await _build_alternatives(
            routes[1:], departure_time, evaluation.total_delay_minutes, coefficients
        )

    logger.info("Prediction pipeline completed in %.3fs", time.monotonic() - t_pipeline)

    return PredictionResponse(
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        total_delay_minutes=round(evaluation.total_delay_minutes, 2),
        confidence=confidence,
        segments=evaluation.segments,
        alternatives=alternatives,
    )


async def evaluate_route(
    route: RouteResult,
    departure_time: datetime,
    coefficients: dict[CoefficientKey, float],
) -> RouteEvaluation:
    """Sample a route, fetch external data concurrently and apply the heuristics."""
    settings = get_settings()

    sample_points = sample_points_from_polyline(
        route.polyline, interval_km=float(settings.sampling_interval_km)
    )
    if len(sample_points) < 2:
        # Degenerate geometry (origin and destination coincide): nothing to evaluate.
        return RouteEvaluation(segments=[], total_delay_minutes=0.0, weather_values=[], points_with_weather=0)

    arrival_times = estimate_arrival_times(
        sample_points, departure_time, total_duration_seconds=route.duration_s
    )

    t0 = time.monotonic()
    elevations, special_elements, weather_per_point = await asyncio.gather(
        get_elevations(sample_points, base_url=settings.open_elevation_base_url),
        _fetch_special_elements(route.polyline),
        get_weather_at_points(sample_points, arrival_times, base_url=settings.open_meteo_base_url),
    )
    logger.debug("External data fetched in %.3fs", time.monotonic() - t0)

    segment_lengths = compute_segment_lengths(sample_points)
    sampled_km = sum(segment_lengths)

    segments: list[SegmentDetail] = []
    weather_values: list[float] = []
    points_with_weather = 0
    total_delay = 0.0
    distance_so_far = 0.0

    for i, seg_km in enumerate(segment_lengths):
        start_pt, end_pt = sample_points[i], sample_points[i + 1]
        arrival = arrival_times[i]
        altitude = elevations[i]
        road_type = get_road_type_at_fraction(
            route.road_types, distance_so_far / sampled_km if sampled_km > 0 else 0.0
        )
        distance_so_far += seg_km

        point_weather = weather_per_point[i]
        if point_weather is not None:
            points_with_weather += 1
        conditions = point_weather or []
        weather_values.append(sum(c.raw_value for c in conditions))

        dominant = dominant_condition(conditions)
        calibration_factor = coefficients.get(dominant, 1.0) if dominant is not None else 1.0

        seg_elements = find_elements_for_segment(start_pt, end_pt, special_elements)
        weather_multipliers, time_multiplier, element_factors = compute_special_element_modifier(
            seg_elements, conditions, segment_km=seg_km
        )

        delay = calculate_segment_delay(
            weather_conditions=conditions,
            segment_km=seg_km,
            road_type=road_type,
            altitude_m=altitude,
            arrival_time=arrival,
            calibration_factor=calibration_factor,
            special_element_weather_multipliers=weather_multipliers,
            special_element_time_multiplier=time_multiplier,
        )
        total_delay += delay

        segments.append(
            SegmentDetail(
                index=i,
                start_point=start_pt,
                end_point=end_pt,
                length_km=round(seg_km, 2),
                estimated_arrival=arrival,
                weather=conditions,
                factors=SegmentFactors(
                    road_type=road_type,
                    road_factor=get_road_factor(road_type),
                    altitude_m=altitude,
                    altitude_factor=get_altitude_factor(altitude),
                    time_factor=get_time_factor(arrival),
                    calibration_factor=calibration_factor,
                    special_elements=element_factors,
                ),
                delay_minutes=delay,
            )
        )

    return RouteEvaluation(
        segments=segments,
        total_delay_minutes=total_delay,
        weather_values=weather_values,
        points_with_weather=points_with_weather,
    )


async def _fetch_special_elements(polyline: list[Coordinate]) -> list[SpecialElement]:
    if not get_settings().enable_special_elements:
        return []
    return await get_special_elements(polyline)


async def _build_alternatives(
    alt_routes: list[RouteResult],
    departure_time: datetime,
    main_delay: float,
    coefficients: dict[CoefficientKey, float] | None = None,
) -> list[AlternativeRoute]:
    """Evaluate alternative routes concurrently and compare them with the main route."""
    coefficients = coefficients or {}
    evaluations = await asyncio.gather(
        *(evaluate_route(route, departure_time, coefficients) for route in alt_routes)
    )

    alternatives: list[AlternativeRoute] = []
    for idx, (route, evaluation) in enumerate(zip(alt_routes, evaluations, strict=True), start=1):
        alt_delay = round(evaluation.total_delay_minutes, 2)
        savings = round(main_delay - alt_delay, 2)
        alternatives.append(
            AlternativeRoute(
                route_index=idx,
                total_delay_minutes=alt_delay,
                duration_minutes=round(route.duration_s / 60.0, 1),
                distance_km=round(route.distance_m / 1000.0, 1),
                delay_savings_minutes=savings,
                summary=_build_alternative_summary(idx, savings, route),
            )
        )
    return alternatives


def _build_alternative_summary(index: int, savings: float, route: RouteResult) -> str:
    """Build a human-readable summary for an alternative route."""
    dist_km = round(route.distance_m / 1000.0, 1)
    dur_min = round(route.duration_s / 60.0)
    outcome = f"saves {savings:.0f} min delay" if savings > 0 else "no delay improvement"
    return f"Alternative {index}: {dist_km} km, {dur_min} min base travel, {outcome}"
