import numpy as np
import pytest

from skitur.terrain import (
    get_elevation, get_elevations,
    get_ground_slope, get_ground_slopes,
    get_path_slope,
    load_dem_for_bounds,
)

pytestmark = pytest.mark.enable_socket


@pytest.fixture(autouse=True, scope="module")
def _load_mt_hood_dem():
    """Load DEM covering all Mt Hood test points before running tests."""
    load_dem_for_bounds(45.30, 45.40, -121.75, -121.35, padding=0.01)


@pytest.mark.parametrize("lat, lon, expected_min, expected_max", [
    # Mount Hood summit: ~3429m
    (45.3735, -121.6959, 3300, 3500),
    # Timberline Lodge: ~1800m
    (45.3311, -121.7113, 1700, 1900),
])
def test_elevation_known_points(lat, lon, expected_min, expected_max):
    elev = get_elevation(lat, lon)
    assert elev is not None
    assert expected_min < elev < expected_max, f"Expected {expected_min}-{expected_max}m, got {elev}m"


def test_ground_slope_steep_terrain():
    """Steep terrain on Mt Hood should have significant slope."""
    slope = get_ground_slope(45.370, -121.698)
    assert slope is not None
    assert slope > 5


def test_ground_slope_flat_terrain():
    """High desert east of Hood should be relatively flat."""
    slope = get_ground_slope(45.35, -121.4)
    assert slope is not None
    # Threshold 15: DEM resolution and interpolation differences between
    # providers can produce varying slope values. 15° is still "relatively flat"
    # compared to steep mountain terrain (30°+).
    assert slope < 15


def test_path_slope_sign_convention():
    """Uphill should be positive, downhill negative, and they should be symmetric."""
    # Timberline Lodge -> near summit
    up = get_path_slope(45.3311, -121.7113, 45.3735, -121.6959)
    down = get_path_slope(45.3735, -121.6959, 45.3311, -121.7113)
    assert up is not None and down is not None
    assert up > 0, "Uphill should be positive"
    assert down < 0, "Downhill should be negative"
    assert abs(up + down) < 1.0, "Up and down should be roughly symmetric"


def test_get_elevations_matches_scalar():
    """Batch get_elevations should match scalar get_elevation for Mt Hood points."""
    lats = np.array([45.3735, 45.3311, 45.370, 45.35])
    lons = np.array([-121.6959, -121.7113, -121.698, -121.4])

    batch = get_elevations(lats, lons)

    for i in range(len(lats)):
        scalar = get_elevation(float(lats[i]), float(lons[i]))
        if scalar is None:
            assert np.isnan(batch[i])
        else:
            assert batch[i] == pytest.approx(scalar), (
                f"Point {i}: batch={batch[i]}, scalar={scalar}")


def test_get_ground_slopes_matches_scalar():
    """Batch get_ground_slopes should match scalar get_ground_slope within tolerance.

    The batch version uses mean latitude for longitude scaling, so there's a
    small difference when points span different latitudes. Points near the same
    latitude should match closely.
    """
    # Points on Mt Hood at similar latitudes to minimize mean-lat approximation error
    lats = np.array([45.370, 45.371, 45.372])
    lons = np.array([-121.698, -121.697, -121.696])

    batch = get_ground_slopes(lats, lons)

    for i in range(len(lats)):
        scalar = get_ground_slope(float(lats[i]), float(lons[i]))
        if scalar is None:
            assert np.isnan(batch[i])
        else:
            assert batch[i] == pytest.approx(scalar, abs=0.5), (
                f"Point {i}: batch={batch[i]}, scalar={scalar}")


def test_descending_route_has_monotonic_elevation():
    """A descending route should have decreasing elevation at each point."""
    points = [
        (45.370, -121.698),   # Below summit
        (45.3500, -121.7050),  # Mid-mountain
        (45.3311, -121.7113),  # Timberline
    ]
    elevations = [get_elevation(lat, lon) for lat, lon in points]
    assert all(e is not None for e in elevations)
    elevation_values = [float(e) for e in elevations if e is not None]
    assert len(elevation_values) == len(elevations)
    # Each point should be lower than the previous
    for i in range(1, len(elevation_values)):
        assert elevation_values[i] < elevation_values[i - 1]
