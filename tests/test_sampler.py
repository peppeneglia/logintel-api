"""Tests for the route sampler."""

from datetime import datetime, timezone

from app.engine.sampler import (
    compute_segment_lengths,
    estimate_arrival_times,
    haversine_distance,
    sample_points_from_polyline,
)
from app.models.schemas import Coordinate


class TestHaversine:
    def test_same_point(self):
        p = Coordinate(lat=45.0, lon=9.0)
        assert haversine_distance(p, p) == 0.0

    def test_known_distance(self):
        # Milan (45.464, 9.190) to Rome (41.902, 12.496) ≈ 477 km
        milan = Coordinate(lat=45.464, lon=9.190)
        rome = Coordinate(lat=41.902, lon=12.496)
        dist = haversine_distance(milan, rome)
        assert 470 < dist < 490


class TestSampling:
    def test_short_route_returns_endpoints(self):
        p1 = Coordinate(lat=45.0, lon=9.0)
        p2 = Coordinate(lat=45.1, lon=9.1)
        # Distance ~14km, interval 50km → just start + end
        sampled = sample_points_from_polyline([p1, p2], interval_km=50)
        assert len(sampled) == 2
        assert sampled[0] == p1
        assert sampled[-1] == p2

    def test_long_route_creates_segments(self):
        # Milan to Rome: ~477km, 50km interval → ~10 segments
        milan = Coordinate(lat=45.464, lon=9.190)
        rome = Coordinate(lat=41.902, lon=12.496)
        sampled = sample_points_from_polyline([milan, rome], interval_km=50)
        assert len(sampled) >= 9
        assert sampled[0] == milan
        assert sampled[-1] == rome

    def test_single_point(self):
        p = Coordinate(lat=45.0, lon=9.0)
        sampled = sample_points_from_polyline([p], interval_km=50)
        assert len(sampled) == 1


class TestArrivalTimes:
    def test_proportional_distribution(self):
        p1 = Coordinate(lat=45.0, lon=9.0)
        p2 = Coordinate(lat=45.5, lon=9.5)
        p3 = Coordinate(lat=46.0, lon=10.0)
        dep = datetime(2026, 2, 14, 8, 0, tzinfo=timezone.utc)

        times = estimate_arrival_times([p1, p2, p3], dep, total_duration_seconds=7200)
        assert times[0] == dep
        assert times[-1] == dep.replace(hour=10)
        # Middle point should be roughly halfway
        assert times[1] > dep
        assert times[1] < times[2]

    def test_single_point_returns_departure(self):
        p = Coordinate(lat=45.0, lon=9.0)
        dep = datetime(2026, 2, 14, 8, 0, tzinfo=timezone.utc)
        times = estimate_arrival_times([p], dep)
        assert times == [dep]


class TestSegmentLengths:
    def test_lengths(self):
        p1 = Coordinate(lat=45.0, lon=9.0)
        p2 = Coordinate(lat=45.5, lon=9.0)
        p3 = Coordinate(lat=46.0, lon=9.0)
        lengths = compute_segment_lengths([p1, p2, p3])
        assert len(lengths) == 2
        # Each segment ~55km (0.5 degrees latitude)
        assert all(50 < l < 60 for l in lengths)
