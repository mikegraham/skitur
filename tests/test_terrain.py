from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pytest
from dem_stitcher.exceptions import NoDEMCoverage
from shapely.geometry import box as shapely_box

from skitur.terrain import (
    ExtentTooLargeError,
    Terrain,
    _DEM_SOURCES,
    _source_covers_bounds,
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


# -- DEM source fallback tests (mocked, no network) --



def _make_terrain_from_source(source, lat_min, lat_max, lon_min, lon_max):
    """Build a minimal Terrain for the given bounds (test helper)."""
    lons = np.linspace(lon_min, lon_max, 10)
    lats = np.linspace(lat_min, lat_max, 10)
    data = np.full((10, 10), 1000.0, dtype=np.float32)
    return Terrain(lons, lats, data)


def test_dem_fallback_uses_second_source_on_no_coverage():
    """When the first DEM source has no coverage, the next source is tried."""
    fetch_log = []

    def mock_covers(source, fetch_bounds):
        return source.name != _DEM_SOURCES[0].name

    def mock_fetch(source, lat_min, lat_max, lon_min, lon_max):
        fetch_log.append(source.name)
        return _make_terrain_from_source(source, lat_min, lat_max, lon_min, lon_max)

    with patch("skitur.terrain._source_covers_bounds", side_effect=mock_covers), \
         patch("skitur.terrain._fetch_dem", side_effect=mock_fetch):
        dem = load_dem_for_bounds(45.30, 45.35, -121.70, -121.65)

    # First source skipped, second source fetched.
    assert fetch_log == [_DEM_SOURCES[1].name]
    assert isinstance(dem, Terrain)


def test_dem_fallback_raises_when_all_sources_fail():
    """When no DEM source covers the bounds, NoDEMCoverage is raised."""
    with patch("skitur.terrain._source_covers_bounds", return_value=False):
        with pytest.raises(NoDEMCoverage, match="No DEM source covers"):
            load_dem_for_bounds(45.30, 45.35, -121.70, -121.65)


def test_dem_source_order_is_best_first():
    """Sources should be ordered from best to worst resolution."""
    resolutions = [s.resolution for s in _DEM_SOURCES]
    assert resolutions == sorted(resolutions), (
        f"DEM sources not in best-first order: {[s.name for s in _DEM_SOURCES]}"
    )


def test_partial_coverage_rejected():
    """A source whose tiles only partially cover the bounds should be skipped.

    Simulates the US/Canada border: 3DEP covers up to 49°N but the route
    extends to 49.08°N. The tiles don't fully contain the request, so
    _source_covers_bounds returns False and the caller falls through to GLO-30.
    """
    # Request straddles 49°N (US/Canada border)
    lat_min, lat_max = 48.90, 49.10
    lon_min, lon_max = -121.80, -121.60

    source_3dep = _DEM_SOURCES[0]
    fetch_bounds = [lon_min - source_3dep.resolution, lat_min - source_3dep.resolution,
                    lon_max + source_3dep.resolution, lat_max + source_3dep.resolution]

    # 3DEP tile covers only up to 49°N (doesn't contain full request)
    partial_tile = gpd.GeoDataFrame(
        {"tile_id": ["partial"], "url": ["fake"], "dem_name": ["3dep"]},
        geometry=[shapely_box(lon_min - 1, lat_min - 1, lon_max + 1, 49.0)],
    )

    with patch("skitur.terrain.get_overlapping_dem_tiles", return_value=partial_tile):
        assert not _source_covers_bounds(source_3dep, fetch_bounds)


def test_grid_spacing_derived_from_coords():
    """Terrain should derive grid spacing from coordinate arrays, not from parameters."""
    # ~10m resolution at 45°N: dlat ≈ 1/10800 deg
    dlat = 1 / 10800
    dlon = 1 / 10800
    lats = np.arange(45.0, 45.0 + 10 * dlat, dlat)
    lons = np.arange(-121.0, -121.0 + 10 * dlon, dlon)
    data = np.zeros((len(lats), len(lons)), dtype=np.float32)

    t = Terrain(lons, lats, data)

    # NS spacing: dlat * 111120 ≈ 10.3m
    assert 9.5 < t.grid_spacing_ns < 11.0
    # EW spacing: dlon * 111120 * cos(45°) ≈ 7.3m
    assert 6.5 < t.grid_spacing_ew < 8.0
    # EW must be smaller than NS at 45°N
    assert t.grid_spacing_ew < t.grid_spacing_ns
