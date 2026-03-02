import pytest

from skitur.geo import haversine_distance, resample_track


def test_haversine_zero_distance():
    assert haversine_distance(45.0, -121.0, 45.0, -121.0) == 0.0


def test_haversine_known_distance():
    # 1 degree of latitude ~ 111km
    dist = haversine_distance(45.0, -121.0, 46.0, -121.0)
    assert 110_000 < dist < 112_000


def test_haversine_symmetry():
    d1 = haversine_distance(45.0, -121.0, 45.1, -121.1)
    d2 = haversine_distance(45.1, -121.1, 45.0, -121.0)
    assert d1 == pytest.approx(d2)


def test_resample_short_segments_unchanged():
    """Points already closer than threshold should not be subdivided."""
    points = [(45.0, -121.0), (45.0001, -121.0001)]
    result = resample_track(points, max_spacing_m=1000)
    assert len(result) == 2


def test_resample_long_segment_subdivided():
    """A segment longer than max_spacing should get intermediate points."""
    # ~11km apart (0.1 deg lat)
    points = [(45.0, -121.0), (45.1, -121.0)]
    result = resample_track(points, max_spacing_m=100)
    assert len(result) > 2
    # First and last should be the originals
    assert result[0] == points[0]
    assert result[-1] == points[-1]


def test_resample_preserves_original_points():
    """All original points should appear in the resampled output."""
    points = [(45.0, -121.0), (45.05, -121.0), (45.1, -121.0)]
    result = resample_track(points, max_spacing_m=100)
    for p in points:
        assert p in result


def test_resample_single_point():
    points = [(45.0, -121.0)]
    result = resample_track(points)
    assert result == points


def test_resample_empty():
    assert resample_track([]) == []


def test_resample_max_spacing_invariant():
    """After resampling, no consecutive pair should exceed max_spacing."""
    points = [(45.0, -121.0), (45.05, -121.0), (45.1, -121.0)]
    max_spacing = 100
    result = resample_track(points, max_spacing_m=max_spacing)
    for i in range(1, len(result)):
        dist = haversine_distance(*result[i - 1], *result[i])
        assert dist <= max_spacing * 1.01, f"Segment {i} is {dist:.0f}m"
