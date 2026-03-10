import math
from dataclasses import dataclass

import numpy as np

from skitur.geo import equirectangular_distance, resample_track, RESAMPLE_THRESHOLD_M
from skitur.terrain import Terrain


@dataclass
class TrackPoint:
    lat: float
    lon: float
    elevation: float | None
    distance: float  # cumulative distance in meters
    track_slope: float | None  # slope along path (deg), None for first point
    ground_slope: float | None  # terrain slope at this point (deg)
    ground_aspect: float | None = None  # terrain aspect in compass degrees (0=N, 90=E)


def analyze_track(
    points: list[tuple],
    dem: Terrain,
    resample: bool = True,
    max_spacing_m: float = RESAMPLE_THRESHOLD_M,
) -> list[TrackPoint]:
    """Analyze a track and return enriched points with slopes.

    Args:
        points: (lat, lon) or (lat, lon, elevation) tuples.
        dem: Terrain object for elevation/slope queries.
        resample: If True, subdivide long segments to max_spacing_m.
        max_spacing_m: Maximum distance between points when resampling.
    """
    if not points:
        return []

    if resample:
        points = resample_track(points, max_spacing_m)

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]

    n = len(points)
    lats_arr = np.array(lats)
    lons_arr = np.array(lons)
    cumulative_dists = _cumulative_distances(points)

    # DEM-only elevation path (simpler and deterministic across GPX sources).
    elevations = dem.get_elevations(lats_arr, lons_arr)
    cell_size = dem.cell_size
    smooth_distance = cell_size * 3  # 30m for 3DEP, 90m for GLO-30
    elevations = _smooth_elevations(
        elevations,
        cumulative_dists,
        window_m=smooth_distance,
    )

    # Ground slopes from DEM (independent of track elevation source)
    slope_arr = dem.get_ground_slopes(lats_arr, lons_arr)
    ground_slopes: list[float | None] = [
        None if np.isnan(s) else float(s) for s in slope_arr
    ]

    # Ground aspects from DEM (compass bearing: 0=N, 90=E, 180=S, 270=W)
    aspect_arr = dem.get_ground_aspects(lats_arr, lons_arr)
    ground_aspects: list[float | None] = [
        None if np.isnan(a) else float(a) for a in aspect_arr
    ]

    # Minimum slope baseline in DEM cells.
    min_slope_baseline = cell_size * 2  # 20m for 3DEP, 60m for GLO-30

    # TODO(#061): Keep this loop-based baseline slope calculation for now.
    # Next refactor should prioritize correctness over raw speed:
    # 1) compute/load one shared DEM derivative field (dz/dx, dz/dy),
    # 2) estimate local track direction at each point,
    # 3) compute directional slope via dot(grad_z, direction),
    # 4) then vectorize once output stability and boundary behavior are validated.
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
        dists[i] = dists[i - 1] + equirectangular_distance(
            points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
    return dists


def _distance_window_indices(
    distances: np.ndarray, half_window_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return [lo, hi) index bounds for a symmetric distance window at each point."""
    lo = np.searchsorted(distances, distances - half_window_m, side="left")
    hi = np.searchsorted(distances, distances + half_window_m, side="right")
    return lo, hi


def _smooth_elevations(elevations: np.ndarray, distances: np.ndarray,
                        window_m: float = 30.0) -> np.ndarray:
    """Smooth elevation profile using a distance-based rolling average.

    Uses a symmetric window of width window_m centered on each point.
    Only needed for DEM-sourced elevations (integer quantization noise).
    """
    smoothed = np.copy(elevations)
    if elevations.size == 0:
        return smoothed

    lo, hi = _distance_window_indices(distances, window_m / 2.0)

    valid = ~np.isnan(elevations)
    values = np.where(valid, elevations, 0.0)

    prefix_sum = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    prefix_count = np.concatenate(([0], np.cumsum(valid.astype(np.int64), dtype=np.int64)))

    window_sum = prefix_sum[hi] - prefix_sum[lo]
    window_count = prefix_count[hi] - prefix_count[lo]

    np.divide(window_sum, window_count, out=smoothed, where=window_count > 0)
    return smoothed
