"""Track statistics for report and API outputs."""

from __future__ import annotations


def _sustained_max(slopes: list[float], window: int = 3) -> float:
    """Return max rolling-average slope over ``window`` consecutive values."""
    if not slopes:
        return 0.0
    if len(slopes) < window:
        return max(slopes)
    best = 0.0
    for i in range(len(slopes) - window + 1):
        avg = sum(slopes[i:i + window]) / window
        best = max(best, avg)
    return best


def compute_stats(points: list) -> dict:
    """Compute track statistics from analyzed points."""
    uphill_slopes = []
    downhill_slopes = []

    uphill_run: list[float] = []
    downhill_run: list[float] = []
    uphill_runs: list[list[float]] = []
    downhill_runs: list[list[float]] = []

    for p in points:
        if p.track_slope is None:
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
            if downhill_run:
                downhill_runs.append(downhill_run)
                downhill_run = []
        elif p.track_slope < 0:
            downhill_slopes.append(abs(p.track_slope))
            downhill_run.append(abs(p.track_slope))
            if uphill_run:
                uphill_runs.append(uphill_run)
                uphill_run = []
        else:
            if uphill_run:
                uphill_runs.append(uphill_run)
                uphill_run = []
            if downhill_run:
                downhill_runs.append(downhill_run)
                downhill_run = []

    if uphill_run:
        uphill_runs.append(uphill_run)
    if downhill_run:
        downhill_runs.append(downhill_run)

    uphill_max = max((_sustained_max(run) for run in uphill_runs), default=0.0)
    downhill_max = max((_sustained_max(run) for run in downhill_runs), default=0.0)

    elevations = [p.elevation for p in points if p.elevation is not None]
    min_elev = min(elevations) if elevations else None
    max_elev = max(elevations) if elevations else None

    total_dist = points[-1].distance if points else 0

    gain = 0.0
    loss = 0.0
    for i in range(1, len(points)):
        if points[i].elevation is not None and points[i - 1].elevation is not None:
            diff = points[i].elevation - points[i - 1].elevation
            if diff > 0:
                gain += diff
            else:
                loss += abs(diff)

    return {
        "total_distance_m": total_dist,
        "min_elevation_m": min_elev,
        "max_elevation_m": max_elev,
        "elevation_gain_m": gain,
        "elevation_loss_m": loss,
        "uphill_avg": sum(uphill_slopes) / len(uphill_slopes) if uphill_slopes else 0,
        "uphill_max": uphill_max,
        "downhill_avg": sum(downhill_slopes) / len(downhill_slopes) if downhill_slopes else 0,
        "downhill_max": downhill_max,
        "num_points": len(points),
    }
