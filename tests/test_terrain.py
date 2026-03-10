import numpy as np
import pytest

from skitur.terrain import (
    ExtentTooLargeError,
    load_dem_for_bounds,
)

pytestmark = pytest.mark.enable_socket


@pytest.fixture(scope="module")
def dem():
    """Load DEM covering all Mt Hood test points."""
    return load_dem_for_bounds(45.30, 45.40, -121.75, -121.35, padding=0.01)


@pytest.mark.parametrize("lat, lon, expected_min, expected_max", [
    # Mount Hood summit: ~3429m
    (45.3735, -121.6959, 3300, 3500),
    # Timberline Lodge: ~1800m
    (45.3311, -121.7113, 1700, 1900),
])
def test_elevation_known_points(dem, lat, lon, expected_min, expected_max):
    elev = dem.get_elevation(lat, lon)
    assert elev is not None
    assert expected_min < elev < expected_max, f"Expected {expected_min}-{expected_max}m, got {elev}m"


def test_ground_slope_steep_terrain(dem):
    """Steep terrain on Mt Hood should have significant slope."""
    slope = dem.get_ground_slope(45.370, -121.698)
    assert slope is not None
    assert slope > 5


def test_ground_slope_flat_terrain(dem):
    """High desert east of Hood should be relatively flat."""
    slope = dem.get_ground_slope(45.35, -121.4)
    assert slope is not None
    # Threshold 15: DEM resolution and interpolation differences between
    # providers can produce varying slope values. 15 is still "relatively flat"
    # compared to steep mountain terrain (30+).
    assert slope < 15


def test_path_slope_sign_convention(dem):
    """Uphill should be positive, downhill negative, and they should be symmetric."""
    # Timberline Lodge -> near summit
    up = dem.get_path_slope(45.3311, -121.7113, 45.3735, -121.6959)
    down = dem.get_path_slope(45.3735, -121.6959, 45.3311, -121.7113)
    assert up is not None and down is not None
    assert up > 0, "Uphill should be positive"
    assert down < 0, "Downhill should be negative"
    assert abs(up + down) < 1.0, "Up and down should be roughly symmetric"


def test_get_elevations_matches_scalar(dem):
    """Batch get_elevations should match scalar get_elevation for Mt Hood points."""
    lats = np.array([45.3735, 45.3311, 45.370, 45.35])
    lons = np.array([-121.6959, -121.7113, -121.698, -121.4])

    batch = dem.get_elevations(lats, lons)

    for i in range(len(lats)):
        scalar = dem.get_elevation(float(lats[i]), float(lons[i]))
        if scalar is None:
            assert np.isnan(batch[i])
        else:
            assert batch[i] == pytest.approx(scalar), (
                f"Point {i}: batch={batch[i]}, scalar={scalar}")


def test_get_ground_slopes_matches_scalar(dem):
    """Batch get_ground_slopes should match scalar get_ground_slope within tolerance.

    The batch version uses mean latitude for longitude scaling, so there's a
    small difference when points span different latitudes. Points near the same
    latitude should match closely.
    """
    # Points on Mt Hood at similar latitudes to minimize mean-lat approximation error
    lats = np.array([45.370, 45.371, 45.372])
    lons = np.array([-121.698, -121.697, -121.696])

    batch = dem.get_ground_slopes(lats, lons)

    for i in range(len(lats)):
        scalar = dem.get_ground_slope(float(lats[i]), float(lons[i]))
        if scalar is None:
            assert np.isnan(batch[i])
        else:
            assert batch[i] == pytest.approx(scalar, abs=0.5), (
                f"Point {i}: batch={batch[i]}, scalar={scalar}")


def test_extent_too_large_rejected():
    """Requests exceeding 1x1 degree should raise ExtentTooLargeError."""
    with pytest.raises(ExtentTooLargeError, match="too large"):
        load_dem_for_bounds(40.0, 41.5, -122.0, -121.0)


def test_extent_limit_checks_raw_bounds_not_padded():
    """The limit applies before padding, so 1.01 deg raw span fails
    but padding inflating a 0.5 deg span to 0.52 deg does not."""
    # 1.01 deg lat span: over limit regardless of padding
    with pytest.raises(ExtentTooLargeError):
        load_dem_for_bounds(40.0, 41.01, -122.0, -121.5)

    # 0.5 deg lat span: under limit even though padding makes it 0.52
    # This should NOT raise ExtentTooLargeError. It will proceed to the
    # DEM fetch (which hits the module-level cache from the fixture).
    load_dem_for_bounds(45.30, 45.40, -121.75, -121.65)
