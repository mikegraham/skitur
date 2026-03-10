from __future__ import annotations

import math

import pytest

from skitur.geo import METERS_PER_DEG_LAT
from skitur.report import (
    GRID_MIN_SCALE,
    GRID_SQUARE_EXTRA_LIMIT_M,
    _grid_bounds_for_shading,
    generate_report,
)


def _minimal_payload() -> dict:
    return {
        "track": [],
        "stats": {},
        "contours": {"minor": [], "major": [], "minor_step_ft": None, "major_step_ft": None},
        "score": {},
        "slope_grid": {
            "data": [],
            "rows": 0,
            "cols": 0,
            "lat_min": 0.0,
            "lat_max": 0.0,
            "lon_min": 0.0,
            "lon_max": 0.0,
        },
    }


def test_generate_report_omits_upload_ui(monkeypatch, tmp_path):
    monkeypatch.setattr("skitur.report.build_analysis_payload", lambda _: _minimal_payload())

    gpx_path = tmp_path / "route.gpx"
    gpx_path.write_text("ignored", encoding="utf-8")
    output_path = tmp_path / "route_report.html"

    out = generate_report(gpx_path, output_path)
    html = out.read_text(encoding="utf-8")

    assert 'id="upload-section"' not in html
    assert 'id="new-upload-btn"' not in html
    assert "Analyze Another" not in html


def test_grid_bounds_have_at_least_min_scale():
    lat_min_t, lat_max_t = 45.00, 45.20
    lon_min_t, lon_max_t = -121.70, -121.20

    lat_min_g, lat_max_g, lon_min_g, lon_max_g = _grid_bounds_for_shading(
        lat_min_t, lat_max_t, lon_min_t, lon_max_t
    )

    mid_lat = (lat_min_t + lat_max_t) / 2
    lon_scale = math.cos(math.radians(mid_lat))

    track_lat_span = lat_max_t - lat_min_t
    track_lon_span_scaled = (lon_max_t - lon_min_t) * lon_scale
    grid_lat_span = lat_max_g - lat_min_g
    grid_lon_span_scaled = (lon_max_g - lon_min_g) * lon_scale

    min_lat_span = track_lat_span * GRID_MIN_SCALE
    min_lon_span_scaled = track_lon_span_scaled * GRID_MIN_SCALE
    tol = 1e-12
    assert grid_lat_span >= (min_lat_span - tol)
    assert grid_lon_span_scaled >= (min_lon_span_scaled - tol)


def test_grid_bounds_square_when_extra_is_under_limit():
    lat_min_t, lat_max_t = 45.00, 45.20
    lon_min_t, lon_max_t = -121.70, -121.51

    lat_min_g, lat_max_g, lon_min_g, lon_max_g = _grid_bounds_for_shading(
        lat_min_t, lat_max_t, lon_min_t, lon_max_t
    )

    mid_lat = (lat_min_t + lat_max_t) / 2
    lon_scale = math.cos(math.radians(mid_lat))
    grid_lat_span_m = (lat_max_g - lat_min_g) * METERS_PER_DEG_LAT
    grid_lon_span_m = (lon_max_g - lon_min_g) * METERS_PER_DEG_LAT * lon_scale

    assert grid_lat_span_m == pytest.approx(grid_lon_span_m, rel=1e-9)


def test_grid_bounds_not_square_when_extra_would_exceed_limit():
    lat_min_t, lat_max_t = 65.17817, 65.55504
    lon_min_t, lon_max_t = -148.0756, -147.57262

    lat_min_g, lat_max_g, lon_min_g, lon_max_g = _grid_bounds_for_shading(
        lat_min_t, lat_max_t, lon_min_t, lon_max_t
    )

    mid_lat = (lat_min_t + lat_max_t) / 2
    lon_scale = math.cos(math.radians(mid_lat))

    track_lat_span = lat_max_t - lat_min_t
    track_lon_span_scaled = (lon_max_t - lon_min_t) * lon_scale
    base_lat_span = track_lat_span * GRID_MIN_SCALE
    base_lon_span_scaled = track_lon_span_scaled * GRID_MIN_SCALE

    square_side_scaled = max(base_lat_span, base_lon_span_scaled)
    extra_lat_m = ((square_side_scaled - base_lat_span) / 2) * METERS_PER_DEG_LAT
    extra_lon_m = ((square_side_scaled - base_lon_span_scaled) / 2) * METERS_PER_DEG_LAT
    assert max(extra_lat_m, extra_lon_m) > GRID_SQUARE_EXTRA_LIMIT_M

    grid_lat_span = lat_max_g - lat_min_g
    grid_lon_span_scaled = (lon_max_g - lon_min_g) * lon_scale

    assert grid_lat_span == pytest.approx(base_lat_span, rel=1e-9)
    assert grid_lon_span_scaled == pytest.approx(base_lon_span_scaled, rel=1e-9)
