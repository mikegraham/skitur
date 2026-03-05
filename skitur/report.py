"""Analysis-to-report pipeline for static HTML reports."""

from __future__ import annotations

import math
from pathlib import Path

import contourpy
import numpy as np
import orjson

from skitur.analyze import TrackPoint, analyze_track
from skitur.gpx import load_track
from skitur.mapdata import choose_contour_steps_ft, compute_map_grids
from skitur.score import TourScore, score_tour
from skitur.stats import compute_stats
from skitur.terrain import current_dem_native_max_dimension, load_dem_for_bounds


def _strip_upload_ui_for_static_report(template_html: str) -> str:
    """Remove upload-only UI blocks from static report output."""
    html = template_html

    upload_start = html.find('<div id="upload-section">')
    if upload_start != -1:
        results_start = html.find('<div id="results-section">', upload_start)
        if results_start != -1:
            html = html[:upload_start] + html[results_start:]

    html = html.replace('<button id="new-upload-btn" type="button">Analyze Another</button>', "")
    return html


def _compute_contours(grids: dict) -> dict:
    """Extract contour polylines from the elevation grid."""
    elev_grid_ft = grids["contour_elev_grid_ft"]
    lat_mesh = grids["contour_lat_mesh"]
    lon_mesh = grids["contour_lon_mesh"]

    valid = elev_grid_ft[~np.isnan(elev_grid_ft)]
    if len(valid) == 0:
        return {"minor": [], "major": [], "minor_step_ft": None, "major_step_ft": None}

    minor_step, major_step = choose_contour_steps_ft(float(valid.min()), float(valid.max()))
    start = int(np.floor(valid.min() / minor_step) * minor_step)
    end = int(np.ceil(valid.max() / minor_step) * minor_step)
    all_levels = list(range(start, end + 1, minor_step))

    generator = contourpy.contour_generator(x=lon_mesh, y=lat_mesh, z=elev_grid_ft)

    minor_lines = []
    major_lines = []

    for level in all_levels:
        lines = generator.lines(level)
        is_major = level % major_step == 0
        for line in lines:
            line_arr = np.asarray(line)
            coords = line_arr[::3].tolist()
            if len(coords) < 2:
                coords = line_arr.tolist()
            if len(coords) < 2:
                continue
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
    """Build API/report JSON payload from analysis outputs."""
    track_data = []
    for p in points:
        track_data.append(
            {
                "lat": p.lat,
                "lon": p.lon,
                "elevation": p.elevation,
                "distance": p.distance,
                "track_slope": p.track_slope,
                "ground_slope": p.ground_slope,
                "ground_aspect": p.ground_aspect,
            }
        )

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


def _compute_analysis(gpx_path: Path) -> tuple[list[TrackPoint], dict, TourScore, dict]:
    """Run full analysis for one GPX path."""
    raw_points = load_track(gpx_path)

    lats = [p[0] for p in raw_points]
    lons = [p[1] for p in raw_points]
    lat_min_t, lat_max_t = min(lats), max(lats)
    lon_min_t, lon_max_t = min(lons), max(lons)

    mid_lat = (lat_max_t + lat_min_t) / 2
    mid_lon = (lon_max_t + lon_min_t) / 2
    lon_scale = math.cos(math.radians(mid_lat))
    lat_span = lat_max_t - lat_min_t
    lon_span_scaled = (lon_max_t - lon_min_t) * lon_scale
    side = max(lat_span, lon_span_scaled) * 1.5
    half_lat = side / 2
    half_lon = (side / lon_scale) / 2
    grid_bounds = (
        mid_lat - half_lat,
        mid_lat + half_lat,
        mid_lon - half_lon,
        mid_lon + half_lon,
    )

    dem_lat_min = min(lat_min_t - 0.02, grid_bounds[0])
    dem_lat_max = max(lat_max_t + 0.02, grid_bounds[1])
    dem_lon_min = min(lon_min_t - 0.02, grid_bounds[2])
    dem_lon_max = max(lon_max_t + 0.02, grid_bounds[3])
    load_dem_for_bounds(dem_lat_min, dem_lat_max, dem_lon_min, dem_lon_max, padding=0.01)

    points = analyze_track(raw_points)
    stats = compute_stats(points)
    score = score_tour(points)

    slope_resolution = 300
    native_max = current_dem_native_max_dimension()
    if native_max is not None:
        slope_resolution = min(native_max, 800)

    contour_resolution = min(slope_resolution, 300)

    grids = compute_map_grids(
        points,
        resolution=slope_resolution,
        contour_resolution=contour_resolution,
        bounds=grid_bounds,
    )

    return points, stats, score, grids


def build_analysis_payload(gpx_path: Path) -> dict:
    """Compute report/API payload from a GPX file path."""
    points, stats, score, grids = _compute_analysis(gpx_path)
    return _build_response(points, stats, score, grids)


def build_embedded_report_html(
    template_html: str,
    data: dict,
    filename: str,
    *,
    hide_upload_section: bool = True,
    hide_new_upload_button: bool = True,
) -> str:
    """Inject analysis JSON into template HTML and auto-render results."""
    data_json = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY).decode()
    filename_json = orjson.dumps(filename).decode()

    script_lines = [
        "<script>",
        "document.addEventListener('DOMContentLoaded', function() {",
        f"  const data = {data_json};",
        "  trackData = data;",
    ]
    if hide_upload_section:
        script_lines.extend(
            [
                "  const uploadSectionEl = document.getElementById('upload-section');",
                "  if (uploadSectionEl) uploadSectionEl.style.display = 'none';",
            ]
        )
    script_lines.append(f"  renderResults(data, {filename_json});")
    if hide_new_upload_button:
        script_lines.extend(
            [
                "  const newUploadBtnEl = document.getElementById('new-upload-btn');",
                "  if (newUploadBtnEl) newUploadBtnEl.style.display = 'none';",
            ]
        )
    script_lines.extend(["});", "</script>"])

    inject = "\n".join(script_lines) + "\n"
    return template_html.replace("</body>", inject + "</body>")


def generate_report(gpx_path: Path, output_path: Path | None = None) -> Path:
    """Generate a self-contained static HTML report for a GPX file."""
    gpx_path = Path(gpx_path)
    if output_path is None:
        output_path = gpx_path.with_name(gpx_path.stem + "_report.html")
    output_path = Path(output_path)

    data = build_analysis_payload(gpx_path)
    template_html = (Path(__file__).parent / "templates" / "index.html").read_text()
    template_html = _strip_upload_ui_for_static_report(template_html)

    filename = gpx_path.stem.replace("_", " ")
    html = build_embedded_report_html(
        template_html=template_html,
        data=data,
        filename=filename,
        hide_upload_section=True,
        hide_new_upload_button=True,
    )

    output_path.write_text(html)
    return output_path
