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

# Avalanche terrain characteristics
# Most avalanches release on slopes around 38-39 degrees
AVY_SLOPE_PEAK = 38.5       # Most dangerous slope angle (degrees)
AVY_SLOPE_STDDEV = 6.0      # Spread of danger curve
AVY_SLOPE_MIN = 25.0        # Below this, negligible slide danger
AVY_SLOPE_MAX = 55.0        # Above this, snow won't accumulate

# Runout exposure tracing
AVY_TRACE_STEP_M = 30.0     # Step size when tracing uphill (meters)
AVY_TRACE_MAX_M = 500.0     # Maximum distance to trace uphill


def _gaussian_pdf(x: float, mu: float, sigma: float) -> float:
    """Gaussian probability density function."""
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))


def _avy_slope_danger(slope: float) -> float:
    """0-1 danger score for a slope angle, peaks at AVY_SLOPE_PEAK."""
    if slope < AVY_SLOPE_MIN or slope > AVY_SLOPE_MAX:
        return 0.0
    # Normalize so peak = 1.0
    peak_val = _gaussian_pdf(AVY_SLOPE_PEAK, AVY_SLOPE_PEAK, AVY_SLOPE_STDDEV)
    return _gaussian_pdf(slope, AVY_SLOPE_PEAK, AVY_SLOPE_STDDEV) / peak_val


def _avy_slope_dangers(slopes: np.ndarray) -> np.ndarray:
    """Vectorized 0-1 danger score for an array of slope angles."""
    peak_val = _gaussian_pdf(AVY_SLOPE_PEAK, AVY_SLOPE_PEAK, AVY_SLOPE_STDDEV)
    danger = np.exp(-0.5 * ((slopes - AVY_SLOPE_PEAK) / AVY_SLOPE_STDDEV) ** 2) / (
        AVY_SLOPE_STDDEV * math.sqrt(2 * math.pi) * peak_val
    )
    # Zero outside [AVY_SLOPE_MIN, AVY_SLOPE_MAX]
    danger[(slopes < AVY_SLOPE_MIN) | (slopes > AVY_SLOPE_MAX)] = 0.0
    return danger


def _compute_runout_exposure(lat: float, lon: float) -> float:
    """Compute avalanche exposure from terrain above (0-1 scale).

    Traces uphill in the fall line direction until hitting a ridge,
    accumulating danger from any avalanche terrain above.
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

        # Check slope danger at this position
        slope = get_ground_slope(curr_lat, curr_lon)
        if slope is not None:
            # Weight by distance (closer terrain is more dangerous)
            distance_factor = 1.0 - (distance_traced / AVY_TRACE_MAX_M)
            exposure += _avy_slope_danger(slope) * distance_factor * 0.2

        prev_elev = best_elev

    return min(exposure, 1.0)


# Per-point loop version (_compute_runout_exposure above) does ~900k scalar
# numpy searchsorted calls (~12s on 10k points). This batched version processes
# all points at each trace step with one get_elevations() call (~16 total),
# bringing it to ~0.1s. Only 8% of the final score — safe to delete if it's
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

        # Build neighbor coordinates for all active points × 8 directions
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

        # Compute slope danger at new positions
        slopes = get_ground_slopes(curr_lats[up_idx], curr_lons[up_idx])
        dangers = _avy_slope_dangers(slopes)
        # NaN slopes → zero danger
        dangers[np.isnan(slopes)] = 0.0

        distance_factor = 1.0 - (distance_traced / AVY_TRACE_MAX_M)
        exposure[up_idx] += dangers * distance_factor * 0.2

    return np.minimum(exposure, 1.0)


@dataclass
class TourScore:
    """Tour quality score with breakdown."""
    total: float  # 0-100, clamped (higher is better)

    # Component scores — can exceed 100 (bonus) or go negative (terrible)
    downhill_quality: float  # how fun are the descents
    uphill_quality: float    # how reasonable are the climbs
    avy_exposure: float      # lower exposure is better (inverted for scoring)

    # Raw stats for context
    pct_avy_terrain: float   # % of track on 30-45 degree ground (standing)
    pct_runout_exposed: float  # avg runout danger (0-100, higher = more exposed)
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

    uphill_slopes = []
    downhill_slopes = []
    uphill_scores = []
    downhill_scores = []

    avy_standing_danger = []  # danger from terrain you're standing on

    for p in points:
        if p.track_slope is not None:
            gp = _ground_slope_penalty(p.ground_slope)
            if p.track_slope > 0.5:  # uphill
                slope = p.track_slope
                uphill_slopes.append(slope)
                uphill_scores.append(_uphill_segment_score(slope) * gp)
            elif p.track_slope < -0.5:  # downhill
                slope = abs(p.track_slope)
                downhill_slopes.append(slope)
                downhill_scores.append(_downhill_segment_score(slope) * gp)

        # Avalanche standing danger
        if p.ground_slope is not None:
            avy_standing_danger.append(_avy_slope_danger(p.ground_slope))

    # Vectorized runout exposure: collect all points with ground slope
    avy_indices = [i for i, p in enumerate(points) if p.ground_slope is not None]
    if avy_indices:
        avy_lats = np.array([points[i].lat for i in avy_indices])
        avy_lons = np.array([points[i].lon for i in avy_indices])
        avy_runout_danger = _compute_runout_exposures(avy_lats, avy_lons).tolist()
    else:
        avy_runout_danger = []

    # === Downhill quality ===
    # Unclamped average — negative scores for terrible segments naturally
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
        # No uphill at all — neutral, not a flaw
        uphill_quality = 50.0

    # === Avy exposure (0-100, higher = safer) ===
    # What % of track is exposed to avalanche danger?
    # Two types: standing ON avy terrain (30-45°), or UNDER it (runout).
    # These overlap — being on avy terrain is already max danger.
    # We want: pct_exposed = pct_on_avy + pct_under_avy_but_not_on_it
    total_ground = sum(1 for p in points if p.ground_slope is not None)

    if total_ground > 0 and avy_indices:
        # Per-point standing danger: 1 if on 30-45° ground, 0 otherwise
        standing_flags = np.zeros(len(avy_indices))
        for k, idx in enumerate(avy_indices):
            gs = points[idx].ground_slope
            if gs is not None and 30 <= gs <= 45:
                standing_flags[k] = 1.0

        pct_avy = np.sum(standing_flags) / total_ground * 100

        # Per-point runout danger (0-1 scale, from uphill tracing)
        runout_arr = np.array(avy_runout_danger)
        # Only count runout for points NOT already on avy terrain
        not_on_avy = standing_flags == 0
        if np.any(not_on_avy):
            pct_runout = np.mean(runout_arr[not_on_avy]) * 100
        else:
            pct_runout = 0.0

        # Total exposed = on avy terrain + under avy terrain (non-overlapping)
        # 1.5x multiplier: being on avy terrain is worse than just exposure
        total_danger_pct = pct_avy * 1.5 + pct_runout * 0.5
        avy_exposure = max(0, 100 - total_danger_pct)
    else:
        avy_exposure = 100.0
        pct_avy = 0.0
        pct_runout = 0.0

    # === Total score (clamped 0-100) ===
    total = max(0, min(100,
        downhill_quality * 0.47 +  # Fun descents matter most
        uphill_quality * 0.29 +
        avy_exposure * 0.24
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
    if ground_slope is None or ground_slope < 20:
        return 1.0
    elif ground_slope <= 30:
        # Noticeable penalty: 1.0 → 0.7
        return 1.0 - (ground_slope - 20) * 0.03
    elif ground_slope <= 40:
        # Significant penalty: 0.7 → 0.3
        return 0.7 - (ground_slope - 30) * 0.04
    else:
        # Extreme terrain: 0.3 → 0.1
        return max(0.1, 0.3 - (ground_slope - 40) * 0.02)


def _downhill_segment_score(slope: float) -> float:
    """Score a single downhill segment for XC skiing.

    Can exceed 100 (bonus for sweet spot) or go negative (impossible).
    Wide sweet spot: 5-13 deg is prime XC fun zone, peaks at 8 deg.
    Generous toward gentle slopes — a 4° glide is still good XC skiing.
    - <2 deg: gentle glide, decent
    - 2-5 deg: getting fun, ramps quickly
    - 5-8 deg: entering sweet spot
    - 8-13 deg: sweet spot plateau (peak 105 at 8 deg)
    - 13-18 deg: getting scary fast
    - 18-22 deg: barely skiable on XC gear
    - 22-25 deg: not XC skiable, negative
    - >25 deg: impossible, hard negative
    """
    if slope < 2:
        return 55 + slope * 10               # 55→75
    elif slope <= 5:
        return 75 + (slope - 2) * (25 / 3)   # 75→100
    elif slope <= 8:
        return 100 + (slope - 5) * (5 / 3)   # 100→105
    elif slope <= 13:
        return 105 - (slope - 8) * 3          # 105→90
    elif slope <= 18:
        return 90 - (slope - 13) * 18         # 90→0
    elif slope <= 22:
        return -(slope - 18) * 7.5            # 0→-30
    elif slope <= 25:
        return -30 - (slope - 22) * (20 / 3)  # -30→-50
    else:
        return -50


def _uphill_segment_score(slope: float) -> float:
    """Score a single uphill segment for XC ski touring.

    Can exceed 100 (bonus for ideal grades) or go negative (bootpack).
    Skin track reference: 8-12 deg efficient, 12-15 common, 15-18
    workable, 18-20 near max, >20 bootpack.
    - <2 deg: easy but slow
    - 2-5 deg: ideal skin track grade
    - 5-8 deg: still very good
    - 8-12 deg: efficient all-day touring
    - 12-15 deg: common steep approaches
    - 15-18 deg: steep but workable
    - 18-22 deg: near max for straight skinning
    - >22 deg: bootpack territory, hard negative
    """
    if slope < 2:
        return 80 + slope * 7.5              # 80→95
    elif slope <= 5:
        return 95 + (slope - 2) * (10 / 3)   # 95→105
    elif slope <= 8:
        return 105 - (slope - 5) * 5          # 105→90
    elif slope <= 12:
        return 90 - (slope - 8) * 10          # 90→50
    elif slope <= 15:
        return 50 - (slope - 12) * (50 / 3)   # 50→0
    elif slope <= 20:
        return -(slope - 15) * 6              # 0→-30
    elif slope <= 25:
        return -30 - (slope - 20) * 4         # -30→-50
    else:
        return -50
