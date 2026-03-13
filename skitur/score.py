"""Tour quality scoring.

All scoring logic lives here. The score_tour function returns a score
and breakdown without leaking implementation details.
"""

import math
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from skitur.analyze import TrackPoint
from skitur.geo import METERS_PER_DEG_LAT
from skitur.terrain import Terrain

CurvePoints = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class PiecewiseCurve:
    points: CurvePoints
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    min_x: float
    max_x: float


def _build_curve(points: CurvePoints) -> PiecewiseCurve:
    if len(points) < 2:
        raise ValueError("Piecewise curve needs at least two points")
    xs, ys = zip(*points, strict=True)
    if any(a >= b for a, b in pairwise(xs)):
        raise ValueError("Piecewise curve x values must be strictly increasing")
    return PiecewiseCurve(
        points=points,
        xs=tuple(xs),
        ys=tuple(ys),
        min_x=xs[0],
        max_x=xs[-1],
    )

# Standing avalanche terrain range (ground slope, degrees)
STANDING_AVY_MIN_DEG = 30.0
STANDING_AVY_MAX_DEG = 45.0

# Avalanche slope-penalty model:
# https://avalanche.org/avalanche-encyclopedia/terrain/slope-characteristics/slope-angle/
# The page summarizes that 96% of slab avalanches released on 30-50 deg slopes,
# with most releases on 34-45 deg slopes. We model this as a full penalty
# band across 30-45 deg with a 4 deg linear taper to zero on either side.
# The 4-degree taper reflects DEM slope uncertainty: Haneberg (2006) reports
# typical slope-angle standard deviations of about +/-3 deg to +/-4 deg for 10 m DEMs.
# DOI: https://doi.org/10.2113/gseegeosci.12.3.247
AVY_TAPER_DEG = 4.0
AVY_CORE_PENALTY = 1.0
AVY_SLOPE_MIN = STANDING_AVY_MIN_DEG - AVY_TAPER_DEG  # 26 deg
AVY_SLOPE_MAX = STANDING_AVY_MAX_DEG + AVY_TAPER_DEG  # 49 deg
AVY_PENALTY_CURVE = _build_curve((
    (AVY_SLOPE_MIN, 0.0),
    (STANDING_AVY_MIN_DEG, AVY_CORE_PENALTY),
    (STANDING_AVY_MAX_DEG, AVY_CORE_PENALTY),
    (AVY_SLOPE_MAX, 0.0),
))

# Runout exposure tracing
AVY_TRACE_MAX_M = 500.0      # Maximum distance to trace uphill (meters)
AVY_TRACE_STEPS = 64         # Fixed number of uphill tracing steps
AVY_TRACE_STEP_M = AVY_TRACE_MAX_M / AVY_TRACE_STEPS

# Avalanche exposure blend weights
AVY_STANDING_WEIGHT = 1.5
AVY_RUNOUT_WEIGHT = 0.5

# Final score blend weights
DOWNHILL_WEIGHT = 0.45
UPHILL_WEIGHT = 0.25
AVY_WEIGHT = 0.30

GROUND_SLOPE_PENALTY_CURVE = _build_curve((
    (0.0, 1.0),
    (20.0, 1.0),
    (40.0, 0.25),
))

DOWNHILL_SCORE_CURVE = _build_curve((
    (0.0, 70.0),
    (3.0, 100.0),
    (6.0, 115.0),
    (8.0, 115.0),
    (12.0, 90.0),
    (18.0, 40.0),
    (21.0, -20.0),
))

UPHILL_SCORE_CURVE = _build_curve((
    (7.0, 100.0),
    (30.0, -30.0),
))


def _interp_piecewise(
    x: float,
    curve: PiecewiseCurve,
) -> float:
    x_clamped = min(max(x, curve.min_x), curve.max_x)
    return float(np.interp(x_clamped, curve.xs, curve.ys))


def _interp_piecewise_array(
    x: np.ndarray,
    curve: PiecewiseCurve,
) -> np.ndarray:
    x_clipped = np.clip(x, curve.min_x, curve.max_x)
    return np.asarray(np.interp(x_clipped, curve.xs, curve.ys), dtype=float)


def _avy_slope_penalty(slope: float) -> float:
    """0-1 avalanche penalty for a slope angle."""
    return _interp_piecewise(slope, AVY_PENALTY_CURVE)


def _avy_slope_penalties(slopes: np.ndarray) -> np.ndarray:
    """Vectorized 0-1 avalanche penalty for an array of slope angles."""
    penalties = _interp_piecewise_array(slopes, AVY_PENALTY_CURVE)
    return np.clip(penalties, 0.0, 1.0)


def _avy_slope_danger(slope: float) -> float:
    """Backward-compatible alias for _avy_slope_penalty."""
    return _avy_slope_penalty(slope)


def _avy_slope_dangers(slopes: np.ndarray) -> np.ndarray:
    """Backward-compatible alias for _avy_slope_penalties."""
    return _avy_slope_penalties(slopes)


def _compute_runout_exposure(lat: float, lon: float, dem: Terrain) -> float:
    """Compute avalanche exposure from terrain above (0-1 scale).

    Traces uphill along local DEM gradient direction until ridge/flat terrain.
    Accumulates penalty from avalanche-prone terrain above the point.
    """
    exposure = 0.0
    curr_lat, curr_lon = lat, lon
    prev_elev = dem.get_elevation(lat, lon)

    if prev_elev is None:
        return 0.0

    curr_dx, curr_dy, curr_invalid = dem.horn_gradients(
        np.array([curr_lat], dtype=float),
        np.array([curr_lon], dtype=float),
    )

    for step_idx in range(1, AVY_TRACE_STEPS + 1):
        dx = float(curr_dx[0])
        dy = float(curr_dy[0])
        if bool(curr_invalid[0]) or (not np.isfinite(dx)) or (not np.isfinite(dy)):
            break

        # In Horn gradients, +dy points south (raster row direction),
        # so uphill north component is -dy.
        east = dx
        north = -dy
        grad_norm = math.hypot(east, north)
        if grad_norm < 1e-12:
            break

        lon_scale = METERS_PER_DEG_LAT * math.cos(math.radians(curr_lat))
        if abs(lon_scale) < 1e-12:
            break

        step_east_m = AVY_TRACE_STEP_M * (east / grad_norm)
        step_north_m = AVY_TRACE_STEP_M * (north / grad_norm)
        next_lat = curr_lat + (step_north_m / METERS_PER_DEG_LAT)
        next_lon = curr_lon + (step_east_m / lon_scale)
        next_elev = dem.get_elevation(next_lat, next_lon)
        if next_elev is None or next_elev <= prev_elev:
            break

        next_dx, next_dy, next_invalid = dem.horn_gradients(
            np.array([next_lat], dtype=float),
            np.array([next_lon], dtype=float),
        )

        curr_lat, curr_lon = next_lat, next_lon
        prev_elev = next_elev
        distance_traced = step_idx * AVY_TRACE_STEP_M

        if not bool(next_invalid[0]):
            slope = math.degrees(math.atan(math.hypot(float(next_dx[0]), float(next_dy[0]))))
            distance_factor = 1.0 - (distance_traced / AVY_TRACE_MAX_M)
            exposure += _avy_slope_penalty(slope) * distance_factor * 0.2

        curr_dx, curr_dy, curr_invalid = next_dx, next_dy, next_invalid

    return min(exposure, 1.0)


# TODO: When we revisit performance, move this loop to a compiled kernel
# (Numba/Cython) while keeping the same gradient-following behavior.
def _compute_runout_exposures(lats: np.ndarray, lons: np.ndarray,
                               dem: Terrain) -> np.ndarray:
    """Batch runout exposure by following DEM gradient ascent for all points."""
    n = len(lats)
    exposure = np.zeros(n)

    curr_lats = lats.copy()
    curr_lons = lons.copy()
    prev_elevs = dem.get_elevations(lats, lons)

    # Points with no elevation data get zero exposure.
    active = ~np.isnan(prev_elevs)
    grad_dx = np.full(n, np.nan, dtype=float)
    grad_dy = np.full(n, np.nan, dtype=float)
    grad_invalid = np.ones(n, dtype=bool)
    grad_ready = np.zeros(n, dtype=bool)

    for step_idx in range(1, AVY_TRACE_STEPS + 1):
        idx = np.where(active)[0]
        if idx.size == 0:
            break

        need_grad_idx = idx[~grad_ready[idx]]
        if need_grad_idx.size:
            dx, dy, invalid = dem.horn_gradients(
                curr_lats[need_grad_idx],
                curr_lons[need_grad_idx],
            )
            grad_dx[need_grad_idx] = dx
            grad_dy[need_grad_idx] = dy
            grad_invalid[need_grad_idx] = invalid
            grad_ready[need_grad_idx] = True

        a_lats = curr_lats[idx]
        a_prev = prev_elevs[idx]
        east = grad_dx[idx]
        north = -grad_dy[idx]
        invalid = grad_invalid[idx]
        grad_norm = np.hypot(east, north)
        lon_scale = METERS_PER_DEG_LAT * np.cos(np.radians(a_lats))

        finite = np.isfinite(east) & np.isfinite(north)
        good_lon_scale = np.abs(lon_scale) >= 1e-12
        has_gradient = finite & (~invalid) & (grad_norm >= 1e-12) & good_lon_scale

        bad_idx = idx[~has_gradient]
        if bad_idx.size:
            active[bad_idx] = False
            grad_ready[bad_idx] = False

        move_idx = idx[has_gradient]
        if move_idx.size == 0:
            continue

        m_east = east[has_gradient]
        m_north = north[has_gradient]
        m_norm = grad_norm[has_gradient]
        m_lats = a_lats[has_gradient]
        m_lons = curr_lons[idx][has_gradient]
        m_prev = a_prev[has_gradient]
        m_lon_scale = lon_scale[has_gradient]

        step_east_m = AVY_TRACE_STEP_M * (m_east / m_norm)
        step_north_m = AVY_TRACE_STEP_M * (m_north / m_norm)
        next_lats = m_lats + (step_north_m / METERS_PER_DEG_LAT)
        next_lons = m_lons + (step_east_m / m_lon_scale)
        next_elevs = dem.get_elevations(next_lats, next_lons)

        have_next = ~np.isnan(next_elevs)
        going_up = have_next & (next_elevs > m_prev)

        stop_idx = move_idx[~going_up]
        if stop_idx.size:
            active[stop_idx] = False
            grad_ready[stop_idx] = False

        up_idx = move_idx[going_up]
        if up_idx.size == 0:
            continue

        up_lats = next_lats[going_up]
        up_lons = next_lons[going_up]
        up_elevs = next_elevs[going_up]
        next_dx, next_dy, next_invalid = dem.horn_gradients(
            up_lats,
            up_lons,
        )

        curr_lats[up_idx] = up_lats
        curr_lons[up_idx] = up_lons
        prev_elevs[up_idx] = up_elevs

        slopes = np.degrees(np.arctan(np.hypot(next_dx, next_dy)))
        slopes[next_invalid] = np.nan
        penalties = _avy_slope_penalties(slopes)
        penalties[np.isnan(slopes)] = 0.0

        distance_traced = step_idx * AVY_TRACE_STEP_M
        distance_factor = 1.0 - (distance_traced / AVY_TRACE_MAX_M)
        exposure[up_idx] += penalties * distance_factor * 0.2

        # Reuse gradients at the moved-to points for next iteration direction.
        grad_dx[up_idx] = next_dx
        grad_dy[up_idx] = next_dy
        grad_invalid[up_idx] = next_invalid
        grad_ready[up_idx] = True

    return np.minimum(exposure, 1.0)


@dataclass
class TourScore:
    """Tour quality score with breakdown."""
    total: float  # 0-100, clamped (higher is better)

    # Component scores - can exceed 100 (bonus) or go negative (terrible)
    downhill_quality: float  # how fun are the descents
    uphill_quality: float    # how reasonable are the climbs
    avy_exposure: float      # lower exposure is better (inverted for scoring)

    # Raw stats for context
    pct_avy_terrain: float   # % of track on 30-45 degree ground (standing)
    pct_runout_exposed: float  # avg runout penalty (0-100, higher = more exposed)
    avg_downhill_slope: float
    avg_uphill_slope: float


def score_tour(points: list[TrackPoint], dem: Terrain) -> TourScore:
    """Score an XC ski tour for quality.

    Returns a TourScore with total 0-100 (higher = better tour).

    Philosophy (XC skiing focus):
    - Downhill: 5-13 deg is prime XC fun, >18 deg is scary, >25 not skiable
    - Uphill: <6 deg (~10% grade) is ideal, steeper needs skins and sucks
    - Avy exposure: penalize time on 30-45 degree terrain
    """
    if len(points) < 2:
        return TourScore(0, 0, 0, 0, 0, 0, 0, 0)

    n_points = len(points)
    lats = np.fromiter((p.lat for p in points), dtype=float, count=n_points)
    lons = np.fromiter((p.lon for p in points), dtype=float, count=n_points)
    track_slopes = np.fromiter(
        (np.nan if p.track_slope is None else p.track_slope for p in points),
        dtype=float,
        count=n_points,
    )
    ground_slopes = np.fromiter(
        (np.nan if p.ground_slope is None else p.ground_slope for p in points),
        dtype=float,
        count=n_points,
    )

    ground_valid = ~np.isnan(ground_slopes)
    ground_penalties = np.ones(n_points, dtype=float)
    if np.any(ground_valid):
        ground_penalties[ground_valid] = _interp_piecewise_array(
            ground_slopes[ground_valid],
            GROUND_SLOPE_PENALTY_CURVE,
        )

    uphill_mask = track_slopes > 0.0
    downhill_mask = track_slopes < 0.0

    uphill_slopes = track_slopes[uphill_mask]
    downhill_slopes = -track_slopes[downhill_mask]

    if uphill_slopes.size:
        uphill_raw = _interp_piecewise_array(uphill_slopes, UPHILL_SCORE_CURVE)
        uphill_scores = np.where(
            uphill_raw >= 0.0,
            uphill_raw * ground_penalties[uphill_mask],
            uphill_raw,
        )
    else:
        uphill_scores = np.empty(0, dtype=float)

    if downhill_slopes.size:
        downhill_raw = _interp_piecewise_array(downhill_slopes, DOWNHILL_SCORE_CURVE)
        downhill_scores = np.where(
            downhill_raw >= 0.0,
            downhill_raw * ground_penalties[downhill_mask],
            downhill_raw,
        )
    else:
        downhill_scores = np.empty(0, dtype=float)

    # Vectorized runout exposure: collect all points with ground slope
    avy_indices = np.flatnonzero(ground_valid)
    if avy_indices.size:
        avy_runout_penalty = _compute_runout_exposures(
            lats[avy_indices],
            lons[avy_indices],
            dem,
        )
    else:
        avy_runout_penalty = np.empty(0, dtype=float)

    # === Downhill quality ===
    # Unclamped average - negative scores for terrible segments naturally
    # drag the average down without needing a separate penalty.
    downhill_quality = float(np.mean(downhill_scores)) if downhill_scores.size else 0.0

    # === Uphill quality ===
    # Unclamped average; no uphill = neutral (not penalized)
    uphill_quality = float(np.mean(uphill_scores)) if uphill_scores.size else 50.0

    # === Avy exposure (0-100, higher = safer) ===
    # What % of track is exposed to avalanche penalties?
    # Two types: standing ON avy terrain (30-45 deg), or UNDER it (runout).
    # These overlap -- being on avy terrain is already max penalty.
    # We want: pct_exposed = pct_on_avy + pct_under_avy_but_not_on_it
    total_ground = int(np.count_nonzero(ground_valid))

    if total_ground > 0 and avy_indices.size:
        # Per-point standing penalty: 1 if on 30-45 deg ground, 0 otherwise.
        avy_ground = ground_slopes[avy_indices]
        standing_flags = (
            (avy_ground >= STANDING_AVY_MIN_DEG)
            & (avy_ground <= STANDING_AVY_MAX_DEG)
        ).astype(float)

        pct_avy = float(np.sum(standing_flags) / total_ground * 100.0)

        # Per-point runout penalty (0-1 scale, from uphill tracing)
        # Only count runout for points NOT already on avy terrain
        not_on_avy = standing_flags == 0.0
        if np.any(not_on_avy):
            pct_runout = float(np.mean(avy_runout_penalty[not_on_avy]) * 100.0)
        else:
            pct_runout = 0.0

        # Total exposed = on avy terrain + under avy terrain (non-overlapping)
        # 1.5x multiplier: being on avy terrain is worse than just exposure
        total_penalty_pct = pct_avy * AVY_STANDING_WEIGHT + pct_runout * AVY_RUNOUT_WEIGHT
        avy_exposure = max(0, 100 - total_penalty_pct)
    else:
        avy_exposure = 100.0
        pct_avy = 0.0
        pct_runout = 0.0

    # === Total score (clamped 0-100) ===
    total = max(0, min(100,
        downhill_quality * DOWNHILL_WEIGHT +
        uphill_quality * UPHILL_WEIGHT +
        avy_exposure * AVY_WEIGHT
    ))

    return TourScore(
        total=total,
        downhill_quality=downhill_quality,
        uphill_quality=uphill_quality,
        avy_exposure=avy_exposure,
        pct_avy_terrain=pct_avy,
        pct_runout_exposed=pct_runout,
        avg_downhill_slope=float(np.mean(downhill_slopes)) if downhill_slopes.size else 0.0,
        avg_uphill_slope=float(np.mean(uphill_slopes)) if uphill_slopes.size else 0.0,
    )


def _ground_slope_penalty(ground_slope: float | None) -> float:
    """Penalty multiplier for being on steep terrain (0-1 scale).

    Even with switchbacks (low track_slope), being on very steep ground
    is uncomfortable and exposed. Skiing a 28-degree slope with lots of
    sidehill still sucks.
    """
    if ground_slope is None:
        return 1.0
    return _interp_piecewise(ground_slope, GROUND_SLOPE_PENALTY_CURVE)


def _apply_ground_slope_multiplier(segment_score: float, multiplier: float) -> float:
    """Apply ground-steepness multiplier without softening negative scores.

    Positive scores are reduced by the multiplier. Negative scores are left as-is
    so steep ground cannot make a bad segment look better.
    """
    if segment_score >= 0:
        return segment_score * multiplier
    return segment_score


def _downhill_segment_score(slope: float) -> float:
    """Score a single downhill segment for XC skiing.

    Piecewise-linear anchors:
    - 0 deg -> 70
    - 3 deg -> 100
    - 6 deg -> 115
    - 8 deg -> 115
    - 12 deg -> 90
    - 18 deg -> 40
    - 21 deg -> -20
    - >=21 deg -> -20
    """
    return _interp_piecewise(slope, DOWNHILL_SCORE_CURVE)


def _uphill_segment_score(slope: float) -> float:
    """Score a single uphill segment for XC ski touring.

    Piecewise-linear with clamped ends:
    - <=7 deg: 100
    - 7..30 deg: linear decay to -30
    - >=30 deg: -30
    """
    return _interp_piecewise(slope, UPHILL_SCORE_CURVE)
