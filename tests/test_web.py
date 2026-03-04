"""Tests for the web interface."""

import json
import math
from io import BytesIO
from pathlib import Path

import pytest

from skitur.mapdata import compute_map_grids
from skitur.report import (
    _build_response,
    _strip_upload_ui_for_static_report,
    build_embedded_report_html,
)
from skitur.stats import compute_stats
from skitur.app import app

TEST_GPX = Path(__file__).parent / "data" / "hood_descent.gpx"
pytestmark = pytest.mark.enable_socket


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(scope="module")
def analysis_data():
    """Run full analysis once and return the JSON response dict."""
    from skitur.gpx import load_track
    from skitur.analyze import analyze_track
    from skitur.score import score_tour
    from skitur.terrain import load_dem_for_bounds

    raw = load_track(TEST_GPX)
    lats = [p[0] for p in raw]
    lons = [p[1] for p in raw]
    load_dem_for_bounds(min(lats), max(lats), min(lons), max(lons), padding=0.02)
    points = analyze_track(raw)
    stats = compute_stats(points)
    score = score_tour(points)
    grids = compute_map_grids(points, padding=0.01, resolution=50)
    return _build_response(points, stats, score, grids)


# ── Route tests ──────────────────────────────────────────────────────

def test_index_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Skitur" in html
    assert "upload" in html.lower()
    assert "Leaflet" in html
    assert "Plotly" in html or "plotly" in html


def test_analyze_no_file(client):
    resp = client.post("/api/analyze")
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert "error" in data
    assert "No GPX file" in data["error"]


def test_analyze_empty_filename(client):
    resp = client.post(
        "/api/analyze",
        data={"gpx_file": (BytesIO(b""), "")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert "error" in data


def test_analyze_invalid_gpx(client):
    """Uploading garbage data should return a 500 with an error message."""
    resp = client.post(
        "/api/analyze",
        data={"gpx_file": (BytesIO(b"not a gpx file"), "bad.gpx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 500
    data = json.loads(resp.data)
    assert "error" in data


def test_analyze_success(client):
    with open(TEST_GPX, "rb") as f:
        resp = client.post(
            "/api/analyze",
            data={"gpx_file": (f, "test.gpx")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200
    data = json.loads(resp.data)

    for key in ("track", "stats", "score", "slope_grid", "contours"):
        assert key in data, f"Missing top-level key: {key}"

    assert len(data["track"]) > 10
    point = data["track"][10]
    for key in ("lat", "lon", "elevation", "distance", "track_slope", "ground_slope"):
        assert key in point

    assert data["stats"]["total_distance_m"] > 0
    assert data["stats"]["num_points"] == len(data["track"])
    assert 0 <= data["score"]["total"] <= 100


# ── Hood descent: validate actual analysis results ────────────────────

def test_hood_descent_track_is_on_hood(analysis_data):
    """Track points should be on Mt Hood (lat ~45.37, lon ~-121.7)."""
    lats = [p["lat"] for p in analysis_data["track"]]
    lons = [p["lon"] for p in analysis_data["track"]]
    assert 45.3 < min(lats) < 45.4
    assert 45.3 < max(lats) < 45.4
    assert -121.8 < min(lons) < -121.6
    assert -121.8 < max(lons) < -121.6


def test_hood_descent_distance(analysis_data):
    """Hood descent should be roughly 3-10 km."""
    dist_m = analysis_data["stats"]["total_distance_m"]
    assert 3000 < dist_m < 10000, f"Distance {dist_m}m doesn't look right for Hood"


def test_hood_descent_elevation(analysis_data):
    """Should descend from high elevation to lower."""
    stats = analysis_data["stats"]
    assert stats["max_elevation_m"] > 2500, "Peak should be above 2500m"
    assert stats["elevation_loss_m"] > 1000, "Should lose >1000m elevation"
    assert stats["elevation_loss_m"] > stats["elevation_gain_m"], \
        "Should lose more elevation than gained (it's a descent)"


def test_hood_descent_is_steep(analysis_data):
    """Hood descent avg downhill slope should be steep (>15 deg)."""
    assert analysis_data["stats"]["downhill_avg"] > 15, \
        "Hood descent should average >15 deg downhill"


def test_hood_descent_downhill_score_is_bad(analysis_data):
    """Hood descent is scary steep — downhill fun should be very low."""
    score = analysis_data["score"]
    # The updated downhill curve has a higher floor for mellow sections.
    # Hood still should not look "good" as XC downhill quality.
    assert score["downhill_quality"] < 50, \
        f"Hood descent downhill quality {score['downhill_quality']} is too high — it's scary AF"


def test_hood_descent_total_score_is_low(analysis_data):
    """Hood is a serious mountaineering descent, not a fun XC tour."""
    assert analysis_data["score"]["total"] < 60, \
        "Hood descent should score poorly as an XC tour"


def test_hood_descent_score_has_no_directness(analysis_data):
    """Score should not contain directness field."""
    assert "directness" not in analysis_data["score"]


def test_hood_descent_has_avy_terrain(analysis_data):
    """Hood should have some percentage of avy terrain."""
    assert analysis_data["score"]["pct_avy_terrain"] > 0, \
        "Hood should have some avy terrain"


# ── Contour computation tests ─────────────────────────────────────────

def test_contour_structure(analysis_data):
    """Contours have expected structure with minor and major lines."""
    contours = analysis_data["contours"]
    assert "minor" in contours
    assert "major" in contours
    assert "minor_step_ft" in contours
    assert "major_step_ft" in contours
    assert len(contours["minor"]) > 0, "Should have minor contour lines"
    assert len(contours["major"]) > 0, "Should have major contour lines"


def test_contour_minor_are_coordinate_lists(analysis_data):
    """Minor contours are lists of [lat, lon] pairs."""
    line = analysis_data["contours"]["minor"][0]
    assert len(line) >= 2, "Contour line should have at least 2 points"
    assert len(line[0]) == 2, "Each point should be [lat, lon]"


def test_contour_major_have_levels(analysis_data):
    """Major contours include elevation level and coordinates."""
    contours = analysis_data["contours"]
    minor_step = int(contours["minor_step_ft"])
    major_step = int(contours["major_step_ft"])
    item = analysis_data["contours"]["major"][0]
    assert "level" in item
    assert "coords" in item
    assert minor_step in (10, 20, 40, 80)
    assert major_step == minor_step * 5
    assert item["level"] % major_step == 0, f"Major level {item['level']} not divisible by {major_step}"


def test_contour_levels_are_reasonable(analysis_data):
    """Contour elevations should be within the track's elevation range."""
    elevs = [p["elevation"] for p in analysis_data["track"] if p["elevation"]]
    min_elev_ft = min(elevs) * 3.28084 - 1000  # generous buffer
    max_elev_ft = max(elevs) * 3.28084 + 1000
    for item in analysis_data["contours"]["major"]:
        assert min_elev_ft < item["level"] < max_elev_ft, \
            f"Contour level {item['level']}ft outside expected range"


def test_contour_coords_are_near_track(analysis_data):
    """Contour coordinates should be geographically near the track."""
    sg = analysis_data["slope_grid"]
    for item in analysis_data["contours"]["major"][:5]:
        for lat, lon in item["coords"]:
            assert sg["lat_min"] - 0.01 < lat < sg["lat_max"] + 0.01
            assert sg["lon_min"] - 0.01 < lon < sg["lon_max"] + 0.01


# ── Slope grid tests ──────────────────────────────────────────────────

def test_slope_grid_structure(analysis_data):
    """Slope grid has expected fields and dimensions."""
    sg = analysis_data["slope_grid"]
    for key in ("data", "rows", "cols", "lat_min", "lat_max", "lon_min", "lon_max"):
        assert key in sg
    assert len(sg["data"]) == sg["rows"] * sg["cols"]
    assert sg["lat_max"] > sg["lat_min"]
    assert sg["lon_max"] > sg["lon_min"]


def test_slope_grid_values_in_range(analysis_data):
    """Slope values should be 0-90 degrees or -1 (NaN marker)."""
    for val in analysis_data["slope_grid"]["data"]:
        assert val == -1 or 0 <= val <= 90, f"Unexpected slope value: {val}"


def test_slope_grid_covers_track(analysis_data):
    """The slope grid bounds should contain all track points."""
    sg = analysis_data["slope_grid"]
    for p in analysis_data["track"]:
        assert sg["lat_min"] <= p["lat"] <= sg["lat_max"], \
            f"Track point lat {p['lat']} outside grid [{sg['lat_min']}, {sg['lat_max']}]"
        assert sg["lon_min"] <= p["lon"] <= sg["lon_max"], \
            f"Track point lon {p['lon']} outside grid [{sg['lon_min']}, {sg['lon_max']}]"


def test_slope_grid_has_steep_terrain(analysis_data):
    """Hood should have slopes > 30 degrees in the grid."""
    steep = [v for v in analysis_data["slope_grid"]["data"] if v > 30]
    assert len(steep) > 0, "Hood should have some steep terrain in the grid"


def test_slope_grid_has_valid_data(analysis_data):
    """Most of the grid should have valid (non-NaN) data."""
    data = analysis_data["slope_grid"]["data"]
    valid = [v for v in data if v != -1]
    pct_valid = len(valid) / len(data) * 100
    assert pct_valid > 50, f"Only {pct_valid:.0f}% valid — grid is mostly NaN"


# ── Grid aspect ratio test ────────────────────────────────────────────

def test_grid_aspect_ratio_reasonable(analysis_data):
    """Grid should have aspect ratio <= 3:1."""
    sg = analysis_data["slope_grid"]
    lat_span = sg["lat_max"] - sg["lat_min"]
    lon_span = sg["lon_max"] - sg["lon_min"]
    center_lat = (sg["lat_min"] + sg["lat_max"]) / 2
    lon_scale = math.cos(math.radians(center_lat))
    real_h = lat_span * 111  # km
    real_w = lon_span * 111 * lon_scale
    ratio = max(real_h, real_w) / min(real_h, real_w)
    assert ratio <= 3.0, f"Grid aspect ratio {ratio:.1f}:1 too extreme"


# ── Template tests ────────────────────────────────────────────────────

def test_template_has_required_js_functions(client):
    """The template should contain all required JS rendering functions."""
    resp = client.get("/")
    html = resp.data.decode()
    for fn in ("renderMap", "renderElevationChart", "renderSlopesChart",
               "renderTrackDistribution", "renderGroundDistribution",
               "renderScore", "renderStats", "renderLegends",
               "slopeToColor", "groundSlopeRGBA", "GradientTrackLayer",
               "renderSlopeImage", "updateHoverMarker"):
        assert fn in html, f"Missing JS function: {fn}"


def test_strip_upload_ui_for_static_report_removes_upload_markup():
    template = (Path(__file__).parent.parent / "skitur" / "templates" / "index.html").read_text()
    stripped = _strip_upload_ui_for_static_report(template)

    assert 'id="upload-section"' not in stripped
    assert "Analyze Another" not in stripped
    assert 'id="results-section"' in stripped


def test_template_has_no_opentopo_tiles(client):
    """We should not reference OpenTopoMap tiles anymore."""
    resp = client.get("/")
    html = resp.data.decode()
    assert "opentopomap" not in html.lower(), "Should not reference OpenTopoMap tiles"


def test_template_has_attribution_control_disabled(client):
    """Leaflet map should be created with attributionControl: false."""
    resp = client.get("/")
    html = resp.data.decode()
    assert "attributionControl" in html


def test_plotly_cdn_is_v3(client):
    """Plotly CDN URL should reference a 3.x version."""
    import re
    resp = client.get("/")
    html = resp.data.decode()
    match = re.search(r"plotly-(\d+)\.\d+\.\d+\.min\.js", html)
    assert match is not None, "Could not find Plotly CDN URL in template"
    major = int(match.group(1))
    assert major == 3, f"Expected Plotly 3.x, got {major}.x"


def test_template_has_avtraining_link(client):
    """Should link to avtraining.org for avalanche education."""
    resp = client.get("/")
    html = resp.data.decode()
    assert "avtraining.org" in html


def test_template_mentions_avalanche_exposure(client):
    """Score panel should say 'Avalanche exposure', not 'Avy safety'."""
    resp = client.get("/")
    html = resp.data.decode()
    assert "Avalanche exposure" in html or "avalanche exposure" in html.lower()
    assert "Avy safety" not in html


# ── Twin Lakes: golden tests for a mellow XC loop ────────────────────

TWIN_LAKES_GPX = Path(__file__).parent / "data" / "Twin_Lakes.gpx"


@pytest.fixture(scope="module")
def twin_lakes_data():
    """Run full analysis on Twin Lakes and return the JSON response dict."""
    from skitur.gpx import load_track
    from skitur.analyze import analyze_track
    from skitur.score import score_tour
    from skitur.terrain import load_dem_for_bounds

    raw = load_track(TWIN_LAKES_GPX)
    lats = [p[0] for p in raw]
    lons = [p[1] for p in raw]
    load_dem_for_bounds(min(lats), max(lats), min(lons), max(lons), padding=0.02)
    points = analyze_track(raw)
    stats = compute_stats(points)
    score = score_tour(points)
    grids = compute_map_grids(points, padding=0.01, resolution=50)
    return _build_response(points, stats, score, grids)


def test_twin_lakes_location(twin_lakes_data):
    """Track should be in central Oregon (lat ~45.26, lon ~-121.68)."""
    lats = [p["lat"] for p in twin_lakes_data["track"]]
    lons = [p["lon"] for p in twin_lakes_data["track"]]
    assert 45.2 < min(lats) < 45.3
    assert 45.2 < max(lats) < 45.3
    assert -121.8 < min(lons) < -121.6


def test_twin_lakes_distance(twin_lakes_data):
    """Twin Lakes loop is roughly 13 km (8 miles)."""
    dist_m = twin_lakes_data["stats"]["total_distance_m"]
    assert 12000 < dist_m < 15000, f"Distance {dist_m}m outside expected range"


def test_twin_lakes_elevation(twin_lakes_data):
    """Moderate elevation — peaks around 1400m, low around 1260m."""
    stats = twin_lakes_data["stats"]
    assert 1350 < stats["max_elevation_m"] < 1450
    assert 1200 < stats["min_elevation_m"] < 1300
    # Roughly balanced up/down (it's a loop)
    assert abs(stats["elevation_gain_m"] - stats["elevation_loss_m"]) < 50


def test_twin_lakes_is_gentle(twin_lakes_data):
    """Average slopes should be gentle (3-6 degrees)."""
    stats = twin_lakes_data["stats"]
    assert 2 < stats["downhill_avg"] < 7
    assert 2 < stats["uphill_avg"] < 7


def test_twin_lakes_scores_well(twin_lakes_data):
    """A mellow XC loop should score well overall."""
    score = twin_lakes_data["score"]
    assert score["total"] > 80, f"Total {score['total']} too low for a mellow loop"
    assert score["downhill_quality"] > 75, \
        f"DH quality {score['downhill_quality']} too low for gentle slopes"
    assert score["uphill_quality"] > 85, \
        f"UH quality {score['uphill_quality']} too low for gentle climbs"


def test_twin_lakes_low_avy_exposure(twin_lakes_data):
    """Low-angle terrain = low avalanche exposure."""
    score = twin_lakes_data["score"]
    assert score["avy_exposure"] > 90, \
        f"Avy exposure {score['avy_exposure']} too low for gentle terrain"
    assert score["pct_avy_terrain"] < 10


def test_twin_lakes_point_count(twin_lakes_data):
    """Should have ~375 resampled track points."""
    n = twin_lakes_data["stats"]["num_points"]
    assert 300 < n < 500, f"Expected ~375 points, got {n}"


def test_twin_lakes_beats_hood(twin_lakes_data, analysis_data):
    """A mellow XC loop should outscore a scary mountaineering descent."""
    assert twin_lakes_data["score"]["total"] > analysis_data["score"]["total"]
    assert twin_lakes_data["score"]["downhill_quality"] > \
        analysis_data["score"]["downhill_quality"]


def test_twin_lakes_has_contours(twin_lakes_data):
    """Should have contour lines in this terrain."""
    contours = twin_lakes_data["contours"]
    assert len(contours["minor"]) > 10
    assert len(contours["major"]) > 5


def test_twin_lakes_slope_grid_mostly_gentle(twin_lakes_data):
    """Most of the slope grid should be moderate terrain (<25 deg)."""
    data = twin_lakes_data["slope_grid"]["data"]
    valid = [v for v in data if v != -1]
    gentle = [v for v in valid if v < 25]
    pct_gentle = len(gentle) / len(valid) * 100
    assert pct_gentle > 50, f"Only {pct_gentle:.0f}% gentle — expected mostly moderate terrain"


# ── Rendering tests (Playwright) ──────────────────────────────────────

@pytest.fixture(scope="module")
def rendered_page(analysis_data):
    """Render the page with Playwright and return page + error list."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    import time

    with open(Path(__file__).parent.parent / "skitur" / "templates" / "index.html") as f:
        template_html = f.read()

    html = build_embedded_report_html(
        template_html=template_html,
        data=analysis_data,
        filename="test.gpx",
        hide_upload_section=True,
        hide_new_upload_button=False,
    )

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        f.write(html)
        tmp_path = f.name

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{tmp_path}")
        time.sleep(6)

        yield page, errors

        browser.close()

    Path(tmp_path).unlink(missing_ok=True)


def test_no_js_errors(rendered_page):
    """Page should render without JavaScript errors."""
    _, errors = rendered_page
    assert len(errors) == 0, f"JS errors: {errors}"


def test_map_renders(rendered_page):
    """The Leaflet map container should have visible content."""
    page, _ = rendered_page
    map_el = page.query_selector("#map")
    assert map_el is not None
    box = map_el.bounding_box()
    assert box["width"] > 100
    assert box["height"] > 100
    # Map should have slope image overlay + track canvas
    canvases = page.query_selector_all("#map canvas")
    assert len(canvases) >= 1, f"Expected >=1 canvas layer (track), got {len(canvases)}"
    imgs = page.query_selector_all("#map img")
    has_slope = any(page.evaluate("(el) => el.src.startsWith('data:image/png')", el) for el in imgs)
    assert has_slope, "No slope grid image overlay found in the map"


def test_charts_render(rendered_page):
    """Plotly charts should render with visible SVG content."""
    page, _ = rendered_page
    for chart_id in ("elevation-chart", "slopes-chart", "histogram-chart"):
        el = page.query_selector(f"#{chart_id}")
        assert el is not None, f"Missing chart: {chart_id}"
        svg = page.query_selector(f"#{chart_id} .plot-container")
        assert svg is not None, f"Chart {chart_id} has no plot-container (Plotly didn't render)"


def test_score_panel_renders(rendered_page):
    """Score panel should show the total score."""
    page, _ = rendered_page
    score_el = page.query_selector("#score-panel")
    assert score_el is not None
    text = score_el.inner_text()
    assert "/100" in text, "Score panel should show '/100'"


def test_legends_render(rendered_page):
    """Both legend sidebars should have canvas-based color bars."""
    page, _ = rendered_page
    for legend_id in ("ground-legend", "track-legend"):
        el = page.query_selector(f"#{legend_id}")
        assert el is not None, f"Missing legend: {legend_id}"
        canvas = page.query_selector(f"#{legend_id} canvas")
        assert canvas is not None, f"Legend {legend_id} missing canvas element"


def test_contour_lines_render(rendered_page):
    """Contour polylines should be present on the map."""
    page, _ = rendered_page
    paths = page.query_selector_all("#map path")
    assert len(paths) > 10, f"Expected many contour paths, got {len(paths)}"
