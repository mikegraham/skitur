import math
from dataclasses import dataclass

import numpy as np

from skitur.geo import haversine_distance, resample_track, RESAMPLE_THRESHOLD_M
from skitur.terrain import (
    get_elevations,
    get_ground_slopes,
    get_ground_aspects,
    load_dem_for_bounds,
)


@dataclass
class TrackPoint:
    lat: float
    lon: float
    elevation: float | None
    distance: float  # cumulative distance in meters
    track_slope: float | None  # slope along path (deg), None for first point
    ground_slope: float | None  # terrain slope at this point (deg)
    ground_aspect: float | None = None  # terrain aspect in compass degrees (0=N, 90=E)


def _dem_cell_size() -> float:
    """Return the current DEM cell size in meters (10 for 3DEP, 30 for GLO-30)."""
    from skitur.terrain import _dem_cache
    if _dem_cache is not None:
        return _dem_cache.cell_size
    return 10.0  # default to 3DEP


def analyze_track(
    points: list[tuple],
    resample: bool = True,
    max_spacing_m: float = RESAMPLE_THRESHOLD_M,
) -> list[TrackPoint]:
    """Analyze a track and return enriched points with slopes.

    Args:
        points: (lat, lon) or (lat, lon, elevation) tuples.
        resample: If True, subdivide long segments to max_spacing_m.
        max_spacing_m: Maximum distance between points when resampling.
    """
    if not points:
        return []

    if resample:
        points = resample_track(points, max_spacing_m)

    # Load DEM for the track's bounding box (enables fast local queries)
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    load_dem_for_bounds(min(lats), max(lats), min(lons), max(lons))

    n = len(points)
    lats_arr = np.array(lats)
    lons_arr = np.array(lons)
    cumulative_dists = _cumulative_distances(points)

    # DEM-only elevation path (simpler and deterministic across GPX sources).
    elevations = get_elevations(lats_arr, lons_arr)
    cell_size = _dem_cell_size()
    smooth_distance = cell_size * 3  # 30m for 3DEP, 90m for GLO-30
    elevations = _smooth_elevations(
        elevations,
        cumulative_dists,
        window_m=smooth_distance,
    )

    # Ground slopes from DEM (independent of track elevation source)
    slope_arr = get_ground_slopes(lats_arr, lons_arr)
    ground_slopes: list[float | None] = [
        None if np.isnan(s) else float(s) for s in slope_arr
    ]

    # Ground aspects from DEM (compass bearing: 0=N, 90=E, 180=S, 270=W)
    aspect_arr = get_ground_aspects(lats_arr, lons_arr)
    ground_aspects: list[float | None] = [
        None if np.isnan(a) else float(a) for a in aspect_arr
    ]

    # Minimum slope baseline in DEM cells.
    min_slope_baseline = cell_size * 2  # 20m for 3DEP, 60m for GLO-30

    # Compute track slopes
    result = []
    for i in range(n):
        lat, lon = points[i][0], points[i][1]
        elev = elevations[i]
        if np.isnan(elev):
            elev = None

        if i == 0:
            track_slope = None
        else:
            j = i - 1
            while j > 0 and (cumulative_dists[i] - cumulative_dists[j]) < min_slope_baseline:
                j -= 1

            dist_seg = cumulative_dists[i] - cumulative_dists[j]
            prev_elev = elevations[j]
            if np.isnan(prev_elev) or np.isnan(elevations[i]) or dist_seg == 0:
                track_slope = None
            else:
                dz = elevations[i] - prev_elev
                track_slope = math.degrees(math.atan2(dz, dist_seg))

        result.append(TrackPoint(
            lat=lat,
            lon=lon,
            elevation=elev,
            distance=float(cumulative_dists[i]),
            track_slope=track_slope,
            ground_slope=ground_slopes[i],
            ground_aspect=ground_aspects[i],
        ))

    return result


def _cumulative_distances(points: list[tuple]) -> np.ndarray:
    """Compute cumulative distances along a track."""
    n = len(points)
    dists = np.zeros(n)
    for i in range(1, n):
        dists[i] = dists[i - 1] + haversine_distance(
            points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
    return dists


def _smooth_elevations(elevations: np.ndarray, distances: np.ndarray,
                        window_m: float = 30.0) -> np.ndarray:
    """Smooth elevation profile using a distance-based rolling average.

    Uses a symmetric window of width window_m centered on each point.
    Only needed for DEM-sourced elevations (integer quantization noise).
    """
    smoothed = np.copy(elevations)
    n = len(elevations)

    for i in range(n):
        half = window_m / 2
        d_i = distances[i]
        lo = np.searchsorted(distances, d_i - half, side='left')
        hi = np.searchsorted(distances, d_i + half, side='right')
        window = elevations[lo:hi]
        valid = window[~np.isnan(window)]
        if len(valid) > 0:
            smoothed[i] = np.mean(valid)

    return smoothed
