"""
Route sampler — samples points along a route at regular intervals.

Used to determine where to fetch weather and elevation data.
The sampling interval is configured in settings (default 50km).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.models.schemas import Coordinate


# Average freight speed in km/h for ETA estimation
DEFAULT_FREIGHT_SPEED_KMH = 70.0

# Earth radius in km for haversine
EARTH_RADIUS_KM = 6371.0


def haversine_distance(p1: Coordinate, p2: Coordinate) -> float:
    """Calculate the great-circle distance between two points in km."""
    lat1, lon1 = math.radians(p1.lat), math.radians(p1.lon)
    lat2, lon2 = math.radians(p2.lat), math.radians(p2.lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_KM * c


def interpolate_point(p1: Coordinate, p2: Coordinate, fraction: float) -> Coordinate:
    """Linearly interpolate between two coordinates by a fraction [0, 1]."""
    lat = p1.lat + (p2.lat - p1.lat) * fraction
    lon = p1.lon + (p2.lon - p1.lon) * fraction
    return Coordinate(lat=round(lat, 6), lon=round(lon, 6))


def sample_points_from_polyline(
    polyline: list[Coordinate], interval_km: float = 50.0
) -> list[Coordinate]:
    """
    Sample points at regular km intervals along a polyline.

    Always includes the first and last point.
    """
    if len(polyline) < 2:
        return list(polyline)

    sampled = [polyline[0]]
    accumulated_km = 0.0
    next_sample_at = interval_km

    for i in range(1, len(polyline)):
        segment_dist = haversine_distance(polyline[i - 1], polyline[i])

        while accumulated_km + segment_dist >= next_sample_at:
            remaining = next_sample_at - accumulated_km
            fraction = remaining / segment_dist if segment_dist > 0 else 0.0
            point = interpolate_point(polyline[i - 1], polyline[i], fraction)
            sampled.append(point)
            next_sample_at += interval_km

        accumulated_km += segment_dist

    # Always include the last point if it's not already there
    last = polyline[-1]
    if sampled[-1].lat != last.lat or sampled[-1].lon != last.lon:
        sampled.append(last)

    return sampled


def estimate_arrival_times(
    sample_points: list[Coordinate],
    departure_time: datetime,
    total_duration_seconds: float | None = None,
    speed_kmh: float = DEFAULT_FREIGHT_SPEED_KMH,
) -> list[datetime]:
    """
    Estimate arrival time at each sample point.

    If total_duration_seconds is provided (from ORS), distribute proportionally.
    Otherwise, estimate from straight-line distances at given speed.
    """
    if len(sample_points) <= 1:
        return [departure_time]

    # Calculate cumulative distances
    distances = [0.0]
    for i in range(1, len(sample_points)):
        d = haversine_distance(sample_points[i - 1], sample_points[i])
        distances.append(distances[-1] + d)

    total_distance = distances[-1]

    if total_distance == 0:
        return [departure_time] * len(sample_points)

    # Determine total travel time
    if total_duration_seconds is not None:
        total_seconds = total_duration_seconds
    else:
        total_seconds = (total_distance / speed_kmh) * 3600.0

    # Proportional arrival times
    arrival_times = []
    for dist in distances:
        fraction = dist / total_distance
        seconds_elapsed = fraction * total_seconds
        arrival_times.append(departure_time + timedelta(seconds=seconds_elapsed))

    return arrival_times


def compute_segment_lengths(sample_points: list[Coordinate]) -> list[float]:
    """Compute the distance in km between consecutive sample points."""
    lengths = []
    for i in range(1, len(sample_points)):
        lengths.append(round(haversine_distance(sample_points[i - 1], sample_points[i]), 2))
    return lengths
