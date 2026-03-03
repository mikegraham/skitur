"""Web interface for skitur.

Run with: flask --app skitur.web run
Generate static report: python -m skitur.web --report path/to/file.gpx [-o output.html]
"""

import logging
import math
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import contourpy
import numpy as np
import orjson
from flask import Flask, Response, request, render_template

logger = logging.getLogger(__name__)

from skitur.gpx import load_track
from skitur.analyze import analyze_track, TrackPoint
from skitur.score import score_tour, TourScore
from skitur.terrain import load_dem_for_bounds
from skitur.plot import compute_map_grids, M_TO_FT, choose_contour_steps_ft
from skitur.cli import compute_stats

app = Flask(__name__, template_folder=Path(__file__).parent / "templates")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit


def _project_display_name() -> str:
    """Read project name from pyproject.toml with a safe fallback."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    default_name = "Skitur"

    try:
        raw = pyproject_path.read_text(encoding="utf-8")
    except Exception:
        return default_name

    # Preferred: tomllib (Python 3.11+)
    try:
        import tomllib  # type: ignore[attr-defined]
        data = tomllib.loads(raw)
        name = data.get("project", {}).get("name")
        if isinstance(name, str) and name.strip():
            value = name.strip().replace("-", " ")
            return value.title() if value == value.lower() else value
    except Exception:
        pass

    # Fallback parser for environments without tomllib.
    section_match = re.search(r"(?ms)^\[project\]\s*(.*?)^\[", raw + "\n[")
    if section_match:
        section = section_match.group(1)
        name_match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"\s*$', section)
        if name_match:
            value = name_match.group(1).strip().replace("-", " ")
            if value:
                return value.title() if value == value.lower() else value

    return default_name


def _copyright_notice() -> str:
    year = datetime.now(timezone.utc).year
    display_name = _project_display_name()
    owner = display_name if display_name.lower().endswith("team") else f"{display_name} Team"
    return f"Copyright (c) {year} {owner}."


@app.route("/")
def index():
    return render_template("index.html", copyright_notice=_copyright_notice())


def _json_error(msg: str, status: int = 400) -> tuple[Response, int]:
    body = orjson.dumps({"error": msg})
    return Response(body, status=status, content_type="application/json"), status


@app.route("/api/analyze", methods=["POST"])
def analyze():
    if "gpx_file" not in request.files:
        return _json_error("No GPX file uploaded")

    gpx_file = request.files["gpx_file"]
    if gpx_file.filename == "":
        return _json_error("No file selected")

    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        gpx_file.save(tmp)
        tmp_path = Path(tmp.name)

    # Reject XML bombs: valid GPX files never contain DOCTYPE or ENTITY declarations.
    raw = tmp_path.read_text(errors="replace")
    if "<!DOCTYPE" in raw or "<!ENTITY" in raw:
        tmp_path.unlink(missing_ok=True)
        return _json_error("Invalid GPX file")

    try:
        points, stats, score, grids = _compute_analysis(tmp_path)
        data = _build_response(points, stats, score, grids)
        body = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
        return Response(body, content_type="application/json")
    except Exception as e:
        logger.exception("Analysis failed")
        return _json_error("Analysis failed. Please check your GPX file.", 500)
    finally:
        tmp_path.unlink(missing_ok=True)


def _compute_contours(grids: dict) -> dict:
    """Extract contour lines from the elevation grid.

    Returns dict with 'minor' and 'major' lists of polylines.
    Each polyline is a list of [lat, lon] pairs.
    """
    elev_grid_ft = grids["contour_elev_grid_ft"]
    lat_mesh = grids["contour_lat_mesh"]
    lon_mesh = grids["contour_lon_mesh"]

    valid = elev_grid_ft[~np.isnan(elev_grid_ft)]
    if len(valid) == 0:
        return {"minor": [], "major": [], "minor_step_ft": None, "major_step_ft": None}

    # Standard U.S. contour handling: 10/20/40/80 with index every 5th line.
    minor_step, major_step = choose_contour_steps_ft(float(valid.min()), float(valid.max()))

    start = int(np.floor(valid.min() / minor_step) * minor_step)
    end = int(np.ceil(valid.max() / minor_step) * minor_step)
    all_levels = list(range(start, end + 1, minor_step))

    # Use contourpy to extract line coordinates
    # lat_mesh and lon_mesh are 2D arrays from meshgrid
    generator = contourpy.contour_generator(
        x=lon_mesh, y=lat_mesh, z=elev_grid_ft,
    )

    minor_lines = []
    major_lines = []

    for level in all_levels:
        lines = generator.lines(level)
        is_major = level % major_step == 0

        for line in lines:
            # line is an Nx2 array of [lon, lat] (x, y)
            # Simplify: keep every 3rd point to reduce payload
            coords = line[::3].tolist()
            if len(coords) < 2:
                coords = line.tolist()
            if len(coords) < 2:
                continue
            # Convert to [lat, lon] pairs for Leaflet
            polyline = [[pt[1], pt[0]] for pt in coords]

            if is_major:
                major_lines.append({"level": level, "coords": polyline})
            else:
                minor_lines.append(polyline)

    return {
        "minor": minor_lines,
        "major": major_lines,
        "minor_step_ft": minor_step,
        "major_step_ft": major_step,
    }


def _build_response(
    points: list[TrackPoint],
    stats: dict,
    score: TourScore,
    grids: dict,
) -> dict:
    """Build the JSON response from analysis results."""
    track_data = []
    for p in points:
        track_data.append({
            "lat": p.lat,
            "lon": p.lon,
            "elevation": p.elevation,
            "distance": p.distance,
            "track_slope": p.track_slope,
            "ground_slope": p.ground_slope,
            "ground_aspect": p.ground_aspect,
        })

    slope_grid = grids["slope_grid"]
    rows, cols = slope_grid.shape
    slope_flat = np.where(np.isnan(slope_grid), -1, np.round(slope_grid, 1))

    contours = _compute_contours(grids)

    return {
        "track": track_data,
        "stats": stats,
        "contours": contours,
        "score": {
            "total": score.total,
            "downhill_quality": score.downhill_quality,
            "uphill_quality": score.uphill_quality,
            "avy_exposure": score.avy_exposure,
            "pct_avy_terrain": score.pct_avy_terrain,
            "pct_runout_exposed": score.pct_runout_exposed,
            "avg_downhill_slope": score.avg_downhill_slope,
            "avg_uphill_slope": score.avg_uphill_slope,
        },
        "slope_grid": {
            "data": slope_flat.flatten(),
            "rows": rows,
            "cols": cols,
            "lat_min": grids["lat_min"],
            "lat_max": grids["lat_max"],
            "lon_min": grids["lon_min"],
            "lon_max": grids["lon_max"],
        },
    }


def _compute_analysis(gpx_path: Path) -> tuple[list[TrackPoint], dict, "TourScore", dict]:
    """Run the full analysis pipeline on a GPX file.

    Returns (points, stats, score, grids).
    """
    raw_points = load_track(gpx_path)

    lats = [p[0] for p in raw_points]
    lons = [p[1] for p in raw_points]
    lat_min_t, lat_max_t = min(lats), max(lats)
    lon_min_t, lon_max_t = min(lons), max(lons)

    # Pre-compute grid bounds so we can fetch the DEM ONCE for both track
    # analysis and slope/contour grids. Without this, compute_map_grids
    # triggers a second stitch_dem call (~2-3s) when the 1.5x grid extent
    # exceeds the track's 0.02-degree padding.
    mid_lat = (lat_max_t + lat_min_t) / 2
    mid_lon = (lon_max_t + lon_min_t) / 2
    lon_scale = math.cos(math.radians(mid_lat))
    lat_span = lat_max_t - lat_min_t
    lon_span_scaled = (lon_max_t - lon_min_t) * lon_scale
    # Grid must cover the square map container at the default zoom level.
    # The viewport constraint (maxBounds + minZoom) prevents users from
    # seeing past the grid edges, so we only need enough margin for the
    # default view (track fitted with 7.5% padding in a 1:1 container).
    # 1.5x handles elongated tracks (e.g. 3:1 aspect ratio) safely.
    side = max(lat_span, lon_span_scaled) * 1.5
    half_lat = side / 2
    half_lon = (side / lon_scale) / 2
    grid_bounds = (mid_lat - half_lat, mid_lat + half_lat,
                   mid_lon - half_lon, mid_lon + half_lon)

    # Single DEM fetch: union of track padding and grid extent.
    # The 0.01 padding covers downstream callers (_sample_elevation_grid and
    # analyze_track both call load_dem_for_bounds with default padding=0.01).
    dem_lat_min = min(lat_min_t - 0.02, grid_bounds[0])
    dem_lat_max = max(lat_max_t + 0.02, grid_bounds[1])
    dem_lon_min = min(lon_min_t - 0.02, grid_bounds[2])
    dem_lon_max = max(lon_max_t + 0.02, grid_bounds[3])
    load_dem_for_bounds(dem_lat_min, dem_lat_max, dem_lon_min, dem_lon_max, padding=0.01)

    points = analyze_track(raw_points)
    stats = compute_stats(points)
    score = score_tour(points)

    # Use a display grid resolution that matches the native DEM to avoid
    # downsampling moire. Downsampling a 925-cell DEM to 300 cells creates
    # aliased speckle patterns in the slope shading. By matching the native
    # resolution (capped at 800 to limit payload size), we skip the
    # downsampling step entirely.
    from skitur.terrain import _dem_cache
    slope_resolution = 300
    if _dem_cache is not None:
        native_max = max(_dem_cache.data.shape)
        slope_resolution = min(native_max, 800)

    # Decouple contour detail from slope shading detail. Keep slope shading at
    # full resolution (anti-moire path unchanged), while capping contour
    # extraction density for speed and payload size.
    contour_resolution = min(slope_resolution, 300)

    grids = compute_map_grids(
        points,
        resolution=slope_resolution,
        contour_resolution=contour_resolution,
        bounds=grid_bounds,
    )

    return points, stats, score, grids


def generate_report(gpx_path: Path, output_path: Path | None = None) -> Path:
    """Generate a self-contained static HTML report for a GPX file.

    Args:
        gpx_path: Path to the GPX file.
        output_path: Where to write the HTML. Defaults to <gpx_stem>_report.html.

    Returns:
        Path to the generated HTML file.
    """
    gpx_path = Path(gpx_path)
    if output_path is None:
        output_path = gpx_path.with_name(gpx_path.stem + "_report.html")
    output_path = Path(output_path)

    points, stats, score, grids = _compute_analysis(gpx_path)
    data = _build_response(points, stats, score, grids)

    # Render the template
    with app.app_context():
        template_html = render_template("index.html", copyright_notice=_copyright_notice())

    filename = gpx_path.stem.replace("_", " ")
    data_json = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY).decode()
    filename_json = orjson.dumps(filename).decode()

    # Inject data and auto-render, hiding the upload form and "Analyze Another"
    inject = (
        "<script>\n"
        "document.addEventListener('DOMContentLoaded', function() {\n"
        f"  const data = {data_json};\n"
        "  trackData = data;\n"
        "  document.getElementById('upload-section').style.display = 'none';\n"
        f"  renderResults(data, {filename_json});\n"
        "  document.getElementById('new-upload-btn').style.display = 'none';\n"
        "});\n"
        "</script>"
    )
    html = template_html.replace("</body>", inject + "</body>")

    output_path.write_text(html)
    return output_path


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="skitur web server / report generator")
    parser.add_argument("--report", type=Path, metavar="GPX",
                        help="Generate a static HTML report from a GPX file")
    parser.add_argument("-o", "--output", type=Path, metavar="HTML",
                        help="Output path for the report (default: <gpx_stem>_report.html)")
    parser.add_argument("--port", type=int, default=5000,
                        help="Port for the web server (default: 5000)")

    args = parser.parse_args()

    if args.report:
        out = generate_report(args.report, args.output)
        print(f"Generated {out} ({out.stat().st_size:,} bytes)")
    else:
        import os
        app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"),
                port=args.port)
