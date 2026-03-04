"""Tour quality scoring.

All scoring logic lives here. The score_tour function returns a score
and breakdown without leaking implementation details.
"""

import math
from dataclasses import dataclass

import numpy as np

from skitur.analyze import TrackPoint
from skitur.geo import METERS_PER_DEG_LAT
from skitur.terrain import get_elevation, get_elevations, get_ground_slope, get_ground_slopes

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
AVY_PENALTY_X = (AVY_SLOPE_MIN, STANDING_AVY_MIN_DEG, STANDING_AVY_MAX_DEG, AVY_SLOPE_MAX)
AVY_PENALTY_Y = (0.0, AVY_CORE_PENALTY, AVY_CORE_PENALTY, 0.0)

# Runout exposure tracing
AVY_TRACE_STEP_M = 30.0     # Step size when tracing uphill (meters)
AVY_TRACE_MAX_M = 500.0     # Maximum distance to trace uphill

# Avalanche exposure blend weights
AVY_STANDING_WEIGHT = 1.5
AVY_RUNOUT_WEIGHT = 0.5

# Final score blend weights
DOWNHILL_WEIGHT = 0.45
UPHILL_WEIGHT = 0.25
AVY_WEIGHT = 0.30

GROUND_SLOPE_PENALTY_X = (0.0, 20.0, 40.0)
GROUND_SLOPE_PENALTY_Y = (1.0, 1.0, 0.25)

DOWNHILL_SCORE_X = (0.0, 3.0, 6.0, 8.0, 12.0, 18.0, 21.0)
DOWNHILL_SCORE_Y = (70.0, 100.0, 115.0, 115.0, 90.0, 40.0, -20.0)

UPHILL_SCORE_X = (7.0, 30.0)
UPHILL_SCORE_Y = (100.0, -30.0)


def _interp_piecewise(
    x: float,
    xs: tuple[float, ...],
    ys: tuple[float, ...],
) -> float:
    x_clamped = min(max(x, xs[0]), xs[-1])
    return float(np.interp(x_clamped, xs, ys))


def _interp_piecewise_array(
    x: np.ndarray,
    xs: tuple[float, ...],
    ys: tuple[float, ...],
) -> np.ndarray:
    x_clipped = np.clip(x, xs[0], xs[-1])
    return np.asarray(np.interp(x_clipped, xs, ys), dtype=float)


def _avy_slope_penalty(slope: float) -> float:
    """0-1 avalanche penalty for a slope angle."""
    return _interp_piecewise(
        slope,
        AVY_PENALTY_X,
        AVY_PENALTY_Y,
    )


def _avy_slope_penalties(slopes: np.ndarray) -> np.ndarray:
    """Vectorized 0-1 avalanche penalty for an array of slope angles."""
    penalties = _interp_piecewise_array(
        slopes,
        AVY_PENALTY_X,
        AVY_PENALTY_Y,
    )
    return np.clip(penalties, 0.0, 1.0)


def _avy_slope_danger(slope: float) -> float:
    """Backward-compatible alias for _avy_slope_penalty."""
    return _avy_slope_penalty(slope)


def _avy_slope_dangers(slopes: np.ndarray) -> np.ndarray:
    """Backward-compatible alias for _avy_slope_penalties."""
    return _avy_slope_penalties(slopes)


def _compute_runout_exposure(lat: float, lon: float) -> float:
    """Compute avalanche exposure from terrain above (0-1 scale).

    Traces uphill in the fall line direction until hitting a ridge,
    accumulating penalty from any avalanche terrain above.
    """
    exposure = 0.0
    curr_lat, curr_lon = lat, lon
    prev_elev = get_elevation(lat, lon)

    if prev_elev is None:
        return 0.0

    # Get fall line direction (steepest ascent) at starting point
    step_deg_lat = AVY_TRACE_STEP_M / METERS_PER_DEG_LAT
    step_deg_lon = AVY_TRACE_STEP_M / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))

    distance_traced = 0.0

    while distance_traced < AVY_TRACE_MAX_M:
        # Find steepest uphill direction by sampling neighbors
        best_elev = prev_elev
        best_lat, best_lon = curr_lat, curr_lon

        for dlat_sign in [-1, 0, 1]:
            for dlon_sign in [-1, 0, 1]:
                if dlat_sign == 0 and dlon_sign == 0:
                    continue
                test_lat = curr_lat + dlat_sign * step_deg_lat
                test_lon = curr_lon + dlon_sign * step_deg_lon
                test_elev = get_elevation(test_lat, test_lon)

                if test_elev is not None and test_elev > best_elev:
                    best_elev = test_elev
                    best_lat, best_lon = test_lat, test_lon

        # Check if we've hit a ridge (no uphill direction found)
        if best_elev <= prev_elev:
            break

        # Move to the new position
        curr_lat, curr_lon = best_lat, best_lon
        distance_traced += AVY_TRACE_STEP_M

        # Check slope penalty at this position
        slope = get_ground_slope(curr_lat, curr_lon)
        if slope is not None:
            # Weight by distance (closer terrain is more consequential)
            distance_factor = 1.0 - (distance_traced / AVY_TRACE_MAX_M)
            exposure += _avy_slope_penalty(slope) * distance_factor * 0.2

        prev_elev = best_elev

    return min(exposure, 1.0)


# Per-point loop version (_compute_runout_exposure above) does ~900k scalar
# numpy searchsorted calls (~12s on 10k points). This batched version processes
# all points at each trace step with one get_elevations() call (~16 total),
# bringing it to ~0.1s. Only 8% of the final score - safe to delete if it's
# not worth the code.
def _compute_runout_exposures(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized runout exposure for all points simultaneously."""
    n = len(lats)
    exposure = np.zeros(n)

    curr_lats = lats.copy()
    curr_lons = lons.copy()
    prev_elevs = get_elevations(lats, lons)

    # Points with no elevation data get zero exposure
    active = ~np.isnan(prev_elevs)

    step_deg_lat = AVY_TRACE_STEP_M / METERS_PER_DEG_LAT
    # Use mean lat for lon scaling (same approximation as scalar version)
    mean_lat = np.mean(lats) if n > 0 else 45.0
    step_deg_lon = AVY_TRACE_STEP_M / (
        METERS_PER_DEG_LAT * math.cos(math.radians(float(mean_lat)))
    )

    max_steps = int(AVY_TRACE_MAX_M / AVY_TRACE_STEP_M)
    distance_traced = 0.0

    # Neighbor offsets: 8 directions (excluding center)
    dlat_signs = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
    dlon_signs = np.array([-1, 0, 1, -1, 1, -1, 0, 1])

    for _ in range(max_steps):
        n_active = np.count_nonzero(active)
        if n_active == 0:
            break

        # Get indices of active points
        idx = np.where(active)[0]
        a_lats = curr_lats[idx]
        a_lons = curr_lons[idx]
        a_prev = prev_elevs[idx]
        na = len(idx)

        # Build neighbor coordinates for all active points x 8 directions
        # Shape: (8, na)
        nb_lats = a_lats[np.newaxis, :] + dlat_signs[:, np.newaxis] * step_deg_lat
        nb_lons = a_lons[np.newaxis, :] + dlon_signs[:, np.newaxis] * step_deg_lon

        # Flatten to (8*na,), get elevations, reshape back to (8, na)
        nb_elevs = get_elevations(nb_lats.ravel(), nb_lons.ravel()).reshape(8, na)

        # Replace NaN with -inf so they lose the argmax
        nb_elevs_safe = np.where(np.isnan(nb_elevs), -np.inf, nb_elevs)

        # Find highest neighbor for each active point
        best_dir = np.argmax(nb_elevs_safe, axis=0)  # shape (na,)
        best_elev = nb_elevs_safe[best_dir, np.arange(na)]

        # Check which points found an uphill direction
        going_up = best_elev > a_prev

        # Points that hit a ridge become inactive
        ridge_idx = idx[~going_up]
        active[ridge_idx] = False

        # For points still going up, move to the new position
        up_mask = going_up
        up_idx = idx[up_mask]
        if len(up_idx) == 0:
            continue

        best_dir_up = best_dir[up_mask]
        curr_lats[up_idx] = a_lats[up_mask] + dlat_signs[best_dir_up] * step_deg_lat
        curr_lons[up_idx] = a_lons[up_mask] + dlon_signs[best_dir_up] * step_deg_lon
        prev_elevs[up_idx] = best_elev[up_mask]

        distance_traced += AVY_TRACE_STEP_M

        # Compute slope penalties at new positions
        slopes = get_ground_slopes(curr_lats[up_idx], curr_lons[up_idx])
        penalties = _avy_slope_penalties(slopes)
        # NaN slopes -> zero penalty
        penalties[np.isnan(slopes)] = 0.0

        distance_factor = 1.0 - (distance_traced / AVY_TRACE_MAX_M)
        exposure[up_idx] += penalties * distance_factor * 0.2

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


def score_tour(points: list[TrackPoint]) -> TourScore:
    """Score an XC ski tour for quality.

    Returns a TourScore with total 0-100 (higher = better tour).

    Philosophy (XC skiing focus):
    - Downhill: 5-13 deg is prime XC fun, >18 deg is scary, >25 not skiable
    - Uphill: <6 deg (~10% grade) is ideal, steeper needs skins and sucks
    - Avy exposure: penalize time on 30-45 degree terrain
    """
    if len(points) < 2:
        return TourScore(0, 0, 0, 0, 0, 0, 0, 0)

    uphill_slopes: list[float] = []
    downhill_slopes: list[float] = []
    uphill_scores: list[float] = []
    downhill_scores: list[float] = []

    for p in points:
        if p.track_slope is not None:
            gp = _ground_slope_penalty(p.ground_slope)
            if p.track_slope > 0.0:  # uphill
                slope = p.track_slope
                uphill_slopes.append(slope)
                raw_score = _uphill_segment_score(slope)
                uphill_scores.append(_apply_ground_slope_multiplier(raw_score, gp))
            elif p.track_slope < 0.0:  # downhill
                slope = abs(p.track_slope)
                downhill_slopes.append(slope)
                raw_score = _downhill_segment_score(slope)
                downhill_scores.append(_apply_ground_slope_multiplier(raw_score, gp))

    # Vectorized runout exposure: collect all points with ground slope
    avy_indices = [i for i, p in enumerate(points) if p.ground_slope is not None]
    if avy_indices:
        avy_lats = np.array([points[i].lat for i in avy_indices])
        avy_lons = np.array([points[i].lon for i in avy_indices])
        avy_runout_penalty = _compute_runout_exposures(avy_lats, avy_lons).tolist()
    else:
        avy_runout_penalty = []

    # === Downhill quality ===
    # Unclamped average - negative scores for terrible segments naturally
    # drag the average down without needing a separate penalty.
    if downhill_scores:
        downhill_quality = sum(downhill_scores) / len(downhill_scores)
    else:
        downhill_quality = 0.0

    # === Uphill quality ===
    # Unclamped average; no uphill = neutral (not penalized)
    if uphill_scores:
        uphill_quality = sum(uphill_scores) / len(uphill_scores)
    else:
        # No uphill at all - neutral, not a flaw
        uphill_quality = 50.0

    # === Avy exposure (0-100, higher = safer) ===
    # What % of track is exposed to avalanche penalties?
    # Two types: standing ON avy terrain (30-45 deg), or UNDER it (runout).
    # These overlap -- being on avy terrain is already max penalty.
    # We want: pct_exposed = pct_on_avy + pct_under_avy_but_not_on_it
    total_ground = sum(1 for p in points if p.ground_slope is not None)

    if total_ground > 0 and avy_indices:
        # Per-point standing penalty: 1 if on 30-45 deg ground, 0 otherwise
        standing_flags = np.zeros(len(avy_indices))
        for k, idx in enumerate(avy_indices):
            gs = points[idx].ground_slope
            if gs is not None and STANDING_AVY_MIN_DEG <= gs <= STANDING_AVY_MAX_DEG:
                standing_flags[k] = 1.0

        pct_avy = float(np.sum(standing_flags) / total_ground * 100)

        # Per-point runout penalty (0-1 scale, from uphill tracing)
        runout_arr = np.array(avy_runout_penalty)
        # Only count runout for points NOT already on avy terrain
        not_on_avy = standing_flags == 0
        if np.any(not_on_avy):
            pct_runout = float(np.mean(runout_arr[not_on_avy]) * 100)
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
        avg_downhill_slope=sum(downhill_slopes) / len(downhill_slopes) if downhill_slopes else 0,
        avg_uphill_slope=sum(uphill_slopes) / len(uphill_slopes) if uphill_slopes else 0,
    )


def _ground_slope_penalty(ground_slope: float | None) -> float:
    """Penalty multiplier for being on steep terrain (0-1 scale).

    Even with switchbacks (low track_slope), being on very steep ground
    is uncomfortable and exposed. Skiing a 28-degree slope with lots of
    sidehill still sucks.
    """
    if ground_slope is None:
        return 1.0
    return _interp_piecewise(
        ground_slope,
        GROUND_SLOPE_PENALTY_X,
        GROUND_SLOPE_PENALTY_Y,
    )


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
    return _interp_piecewise(
        slope,
        DOWNHILL_SCORE_X,
        DOWNHILL_SCORE_Y,
    )


def _uphill_segment_score(slope: float) -> float:
    """Score a single uphill segment for XC ski touring.

    Piecewise-linear with clamped ends:
    - <=7 deg: 100
    - 7..30 deg: linear decay to -30
    - >=30 deg: -30
    """
    return _interp_piecewise(
        slope,
        UPHILL_SCORE_X,
        UPHILL_SCORE_Y,
    )
