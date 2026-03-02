"""Smoke tests for plot generation.

These verify that all plot functions run without error and produce output files.
They don't test visual correctness (that requires human review).
"""

from pathlib import Path

import pytest

from skitur.gpx import load_track
from skitur.analyze import analyze_track
from skitur.plot import (
    plot_slopes,
    plot_elevation_profile,
    plot_topo_map,
    plot_slope_map,
    plot_slope_histogram,
    compute_map_grids,
)

TEST_GPX = Path(__file__).parent / "data" / "hood_descent.gpx"


@pytest.fixture(scope="module")
def analyzed_track():
    """Load and analyze test track once for all plot tests."""
    points = load_track(TEST_GPX)
    return analyze_track(points)


@pytest.mark.parametrize("plot_fn", [
    plot_slopes,
    plot_elevation_profile,
    plot_slope_histogram,
])
def test_simple_plots(tmp_path, analyzed_track, plot_fn):
    output = tmp_path / "test.png"
    plot_fn(analyzed_track, output)
    assert output.exists()
    assert output.stat().st_size > 1000  # not an empty/corrupt file


def test_map_plots_with_shared_grids(tmp_path, analyzed_track):
    """Topo and slope maps should work with precomputed grids."""
    grids = compute_map_grids(analyzed_track)

    topo = tmp_path / "topo.png"
    slope = tmp_path / "slope_map.png"
    plot_topo_map(analyzed_track, topo, grids=grids)
    plot_slope_map(analyzed_track, slope, grids=grids)

    assert topo.exists()
    assert topo.stat().st_size > 1000
    assert slope.exists()
    assert slope.stat().st_size > 1000


def test_map_plots_with_decoupled_contour_resolution(tmp_path, analyzed_track):
    """Contour/elevation grid can be coarser than slope shading grid."""
    grids = compute_map_grids(
        analyzed_track,
        resolution=120,
        contour_resolution=60,
    )

    assert grids["slope_grid"].shape == (120, 120)
    assert grids["lon_mesh"].shape == (120, 120)
    assert grids["lat_mesh"].shape == (120, 120)

    assert grids["contour_elev_grid_ft"].shape == (60, 60)
    assert grids["contour_lon_mesh"].shape == (60, 60)
    assert grids["contour_lat_mesh"].shape == (60, 60)

    topo = tmp_path / "topo_decoupled.png"
    slope = tmp_path / "slope_decoupled.png"
    plot_topo_map(analyzed_track, topo, grids=grids)
    plot_slope_map(analyzed_track, slope, grids=grids)

    assert topo.exists()
    assert topo.stat().st_size > 1000
    assert slope.exists()
    assert slope.stat().st_size > 1000
