"""Command-line interface for skitur."""

import argparse
import sys
from pathlib import Path

from skitur.gpx import load_track
from skitur.analyze import analyze_track
from skitur.score import score_tour, TourScore
from skitur.terrain import load_dem_for_bounds
from skitur.plot import (
    plot_slopes,
    plot_elevation_profile,
    plot_topo_map,
    plot_slope_map,
    plot_slope_histogram,
    compute_map_grids,
)

# Unit conversions for display
M_TO_FT = 3.28084
KM_TO_MI = 0.621371


def _sustained_max(slopes: list[float], window: int = 3) -> float:
    """Return the max of a rolling average over *window* consecutive values.

    This filters out single-point GPS noise spikes; a slope is only
    considered the maximum if it is sustained over at least *window*
    consecutive points (~100 m of distance at typical resampling).
    If there are fewer than *window* values, fall back to the plain max.
    """
    if not slopes:
        return 0.0
    if len(slopes) < window:
        return max(slopes)
    best = 0.0
    for i in range(len(slopes) - window + 1):
        avg = sum(slopes[i:i + window]) / window
        if avg > best:
            best = avg
    return best


def compute_stats(points: list) -> dict:
    """Compute track statistics."""
    uphill_slopes = []
    downhill_slopes = []

    # Collect slopes in track order, keeping separate runs of
    # consecutive uphill / downhill values so the rolling window
    # operates on genuinely adjacent points.
    uphill_run: list[float] = []
    downhill_run: list[float] = []
    uphill_runs: list[list[float]] = []
    downhill_runs: list[list[float]] = []

    for p in points:
        if p.track_slope is None:
            # Break in data -- flush current runs
            if uphill_run:
                uphill_runs.append(uphill_run)
                uphill_run = []
            if downhill_run:
                downhill_runs.append(downhill_run)
                downhill_run = []
            continue
        if p.track_slope > 0:
            uphill_slopes.append(p.track_slope)
            uphill_run.append(p.track_slope)
            # End any active downhill run
            if downhill_run:
                downhill_runs.append(downhill_run)
                downhill_run = []
        elif p.track_slope < 0:
            downhill_slopes.append(abs(p.track_slope))
            downhill_run.append(abs(p.track_slope))
            # End any active uphill run
            if uphill_run:
                uphill_runs.append(uphill_run)
                uphill_run = []
        else:
            # Zero slope -- flush both runs
            if uphill_run:
                uphill_runs.append(uphill_run)
                uphill_run = []
            if downhill_run:
                downhill_runs.append(downhill_run)
                downhill_run = []

    # Flush any remaining runs
    if uphill_run:
        uphill_runs.append(uphill_run)
    if downhill_run:
        downhill_runs.append(downhill_run)

    # Sustained max: best rolling-window average across all runs
    uphill_max = max(
        (_sustained_max(run) for run in uphill_runs),
        default=0.0,
    )
    downhill_max = max(
        (_sustained_max(run) for run in downhill_runs),
        default=0.0,
    )

    # Elevation stats
    elevations = [p.elevation for p in points if p.elevation is not None]
    min_elev = min(elevations) if elevations else None
    max_elev = max(elevations) if elevations else None

    # Distance
    total_dist = points[-1].distance if points else 0

    # Elevation gain/loss
    gain = 0.0
    loss = 0.0
    for i in range(1, len(points)):
        if points[i].elevation is not None and points[i - 1].elevation is not None:
            diff = points[i].elevation - points[i-1].elevation
            if diff > 0:
                gain += diff
            else:
                loss += abs(diff)

    return {
        'total_distance_m': total_dist,
        'min_elevation_m': min_elev,
        'max_elevation_m': max_elev,
        'elevation_gain_m': gain,
        'elevation_loss_m': loss,
        'uphill_avg': sum(uphill_slopes) / len(uphill_slopes) if uphill_slopes else 0,
        'uphill_max': uphill_max,
        'downhill_avg': sum(downhill_slopes) / len(downhill_slopes) if downhill_slopes else 0,
        'downhill_max': downhill_max,
        'num_points': len(points),
    }


def print_score(score: TourScore) -> None:
    """Print tour quality score."""
    print("\n=== Tour Quality Score ===\n")
    print(f"Overall:         {score.total:.0f}/100")
    dh = score.avg_downhill_slope
    uh = score.avg_uphill_slope
    avy_pct = score.pct_avy_terrain
    print(f"  Downhill fun:  {score.downhill_quality:.0f}/100 (avg {dh:.1f} deg)")
    print(f"  Uphill doability: {score.uphill_quality:.0f}/100 (avg {uh:.1f} deg)")
    print(f"  Avy safety:    {score.avy_exposure:.0f}/100 ({avy_pct:.1f}% avy terrain)")


def print_stats(stats: dict) -> None:
    """Print statistics to stdout."""
    print("\n=== Track Statistics ===\n")

    dist_mi = stats['total_distance_m'] / 1000 * KM_TO_MI
    print(f"Distance:        {dist_mi:.2f} mi ({stats['total_distance_m'] / 1000:.2f} km)")
    print(f"Points:          {stats['num_points']}")

    if stats['min_elevation_m'] is not None and stats['max_elevation_m'] is not None:
        min_ft = stats['min_elevation_m'] * M_TO_FT
        max_ft = stats['max_elevation_m'] * M_TO_FT
        print(f"Elevation range: {min_ft:.0f} - {max_ft:.0f} ft")

    gain_ft = stats['elevation_gain_m'] * M_TO_FT
    loss_ft = stats['elevation_loss_m'] * M_TO_FT
    print(f"Elevation gain:  {gain_ft:.0f} ft")
    print(f"Elevation loss:  {loss_ft:.0f} ft")

    print("\nUphill slopes:")
    print(f"  Average: {stats['uphill_avg']:.1f} deg")
    print(f"  Maximum: {stats['uphill_max']:.1f} deg")

    print("\nDownhill slopes:")
    print(f"  Average: {stats['downhill_avg']:.1f} deg")
    print(f"  Maximum: {stats['downhill_max']:.1f} deg")


def main(
    gpx_path: str | Path,
    output_dir: str | Path | None = None,
    generate_plots: bool = True,
    resample: bool = True,
) -> None:
    """Analyze a GPX file and generate plots."""
    gpx_path = Path(gpx_path)
    if not gpx_path.exists():
        print(f"Error: {gpx_path} not found", file=sys.stderr)
        sys.exit(1)

    # Default output directory: same as GPX file, with _output suffix
    if output_dir is None:
        output_dir = gpx_path.parent / f"{gpx_path.stem}_output"
    output_dir = Path(output_dir)

    print(f"Loading {gpx_path}...")
    raw_points = load_track(gpx_path)
    print(f"Loaded {len(raw_points)} raw points")

    # Pre-load DEM with generous padding so all subsequent calls (analyze +
    # plot grids) hit the in-memory cache instead of re-downloading.
    lats = [p[0] for p in raw_points]
    lons = [p[1] for p in raw_points]
    load_dem_for_bounds(min(lats), max(lats), min(lons), max(lons), padding=0.02)

    print(f"Analyzing track {'(with resampling)' if resample else '(no resampling)'}...")
    points = analyze_track(raw_points, resample=resample)
    print(f"Analysis complete: {len(points)} points")

    # Compute and print stats
    stats = compute_stats(points)
    print_stats(stats)

    # Compute and print score
    score = score_tour(points)
    print_score(score)

    if not generate_plots:
        return

    # Generate plots
    output_dir.mkdir(exist_ok=True)
    print(f"\nGenerating plots in {output_dir}/...")

    print("  slopes.png")
    plot_slopes(points, output_dir / "slopes.png")

    print("  elevation.png")
    plot_elevation_profile(points, output_dir / "elevation.png")

    print("  histogram.png")
    plot_slope_histogram(points, output_dir / "histogram.png")

    print("  Computing terrain grids...")
    grids = compute_map_grids(points)

    print("  topo.png")
    plot_topo_map(points, output_dir / "topo.png", grids=grids)

    print("  slope_map.png")
    plot_slope_map(points, output_dir / "slope_map.png", grids=grids)

    print("\nDone!")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="skitur",
        description="Analyze ski touring routes from GPX files.",
    )
    parser.add_argument(
        "gpx_file",
        type=Path,
        help="Path to GPX file to analyze",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output directory for plots (default: <gpx_name>_output/)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip generating plots, only show stats",
    )
    parser.add_argument(
        "--no-resample",
        action="store_true",
        help="Disable track resampling",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(
        args.gpx_file, args.output,
        generate_plots=not args.no_plots, resample=not args.no_resample,
    )
