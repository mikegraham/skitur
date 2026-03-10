from pathlib import Path

import numpy as np
import pytest

from skitur.analyze import TrackPoint, analyze_track
from skitur.gpx import load_track
from skitur.terrain import load_dem_for_bounds

TEST_GPX = Path(__file__).parent / "data" / "hood_descent.gpx"
pytestmark = pytest.mark.enable_socket


@pytest.fixture(scope="module")
def dem():
    """Load DEM covering the hood_descent test GPX."""
    return load_dem_for_bounds(45.30, 45.40, -121.75, -121.65, padding=0.01)


def test_load_track():
    points = load_track(TEST_GPX)
    assert len(points) == 9
    # Check coordinates are approximately correct (avoid exact float comparison)
    lat0, lon0 = points[0][0], points[0][1]
    assert pytest.approx(lat0, abs=0.001) == 45.370
    assert pytest.approx(lon0, abs=0.001) == -121.698


def test_analyze_track_without_resampling(dem):
    points = load_track(TEST_GPX)
    analysis = analyze_track(points, dem, resample=False)

    assert len(analysis) == len(points)
    assert analysis[0].track_slope is None  # first point has no predecessor
    assert analysis[0].distance == 0.0
    assert analysis[-1].distance > 4000  # ~4.5km route

    # This is a descent route: every segment should be downhill
    for pt in analysis[1:]:
        assert pt.track_slope is not None
        assert pt.track_slope < 0

    # Elevations should be present and decrease overall
    assert analysis[0].elevation is not None
    assert analysis[-1].elevation is not None
    assert analysis[0].elevation > analysis[-1].elevation


def test_resampling_adds_points(dem):
    points = load_track(TEST_GPX)
    raw = analyze_track(points, dem, resample=False)
    resampled = analyze_track(points, dem, resample=True)

    assert len(resampled) > len(raw)
    # Total distance should be similar regardless of resampling
    assert pytest.approx(resampled[-1].distance, rel=0.05) == raw[-1].distance


def test_distance_monotonically_increases(dem):
    """Cumulative distance should never decrease."""
    points = load_track(TEST_GPX)
    analysis = analyze_track(points, dem, resample=False)
    for i in range(1, len(analysis)):
        assert analysis[i].distance >= analysis[i - 1].distance


def test_analyze_empty_track(dem):
    assert analyze_track([], dem) == []


def test_analyze_all_points_have_ground_slope(dem):
    """Every analyzed point should have a ground slope (terrain data exists)."""
    points = load_track(TEST_GPX)
    analysis = analyze_track(points, dem, resample=False)
    for pt in analysis:
        assert pt.ground_slope is not None


class _VoidTerrain:
    """Terrain stub that returns NaN for some elevations (simulates DEM voids)."""

    cell_size = 10.0

    def get_elevations(self, lats, lons):
        elevs = np.full(len(lats), 2000.0)
        # Make the middle point a void
        elevs[len(elevs) // 2] = np.nan
        return elevs

    def get_ground_slopes(self, lats, lons):
        return np.full(len(lats), 10.0)

    def get_ground_aspects(self, lats, lons):
        return np.full(len(lats), 180.0)

    def horn_gradients(self, lats, lons):
        n = len(lats)
        return np.zeros(n), np.zeros(n), np.zeros(n, dtype=bool)


def test_analyze_track_with_dem_void():
    """Points where the DEM has no data should get elevation=None and track_slope=None."""
    points = [
        (45.37, -121.70),
        (45.371, -121.70),
        (45.372, -121.70),
        (45.373, -121.70),
        (45.374, -121.70),
    ]
    analysis = analyze_track(points, _VoidTerrain(), resample=False)
    mid = len(analysis) // 2

    # The void point should have elevation=None
    assert analysis[mid].elevation is None

    # Non-void points should have real elevations
    assert analysis[0].elevation is not None
    assert analysis[-1].elevation is not None
