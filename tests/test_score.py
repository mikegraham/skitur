"""Tests for tour scoring logic.

Tests verify both exact breakpoint values and the shape of scoring curves.
"""

from functools import lru_cache

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from skitur.score import (
    _downhill_segment_score,
    _uphill_segment_score,
    _ground_slope_penalty,
    _avy_slope_danger,
    _avy_slope_dangers,
    _compute_runout_exposure,
    _compute_runout_exposures,
    score_tour,
)
from skitur.analyze import TrackPoint
from skitur.terrain import load_dem_for_bounds

pytestmark = pytest.mark.enable_socket


@pytest.fixture(scope="module")
def dem():
    """Load DEM for runout-exposure and score_tour integration tests."""
    return load_dem_for_bounds(44.9, 45.5, -121.9, -121.4, padding=0.02)


@lru_cache(maxsize=1)
def _fuzz_dem():
    """Cached DEM for Hypothesis fuzzing to avoid rebuilding terrain per example."""
    return load_dem_for_bounds(44.9, 45.5, -121.9, -121.4, padding=0.02)


def _make_point(lat=45.0, lon=-121.0, elevation=2000, distance=0,
                track_slope=None, ground_slope=10):
    """Shorthand for building test TrackPoints."""
    return TrackPoint(
        lat=lat, lon=lon, elevation=elevation,
        distance=distance, track_slope=track_slope,
        ground_slope=ground_slope,
    )


# -- Downhill scoring: exact breakpoints --

def test_downhill_sweet_spot():
    """6-8 degrees is the peak plateau of downhill fun."""
    assert _downhill_segment_score(8) == 115


def test_downhill_breakpoint_values():
    """Verify exact scores at each piecewise boundary."""
    assert _downhill_segment_score(0) == 70
    assert _downhill_segment_score(3) == 100
    assert _downhill_segment_score(6) == 115
    assert _downhill_segment_score(8) == 115
    assert _downhill_segment_score(12) == 90
    assert _downhill_segment_score(18) == 40
    assert _downhill_segment_score(21) == -20
    assert _downhill_segment_score(25) == -20


def test_downhill_flat_is_boring():
    """Flat terrain gets some credit, but not the peak."""
    assert _downhill_segment_score(0) == 70
    assert _downhill_segment_score(1) == 80
    assert _downhill_segment_score(2) == 90


def test_downhill_steep_goes_negative():
    """Slopes beyond 21 degrees floor at a negative score."""
    assert _downhill_segment_score(21) == -20
    assert _downhill_segment_score(25) == -20
    assert _downhill_segment_score(30) == -20
    assert _downhill_segment_score(40) == -20


def test_downhill_monotonic_increase_to_peak():
    scores = [_downhill_segment_score(s) for s in range(0, 7)]
    for i in range(1, len(scores)):
        assert scores[i] >= scores[i - 1]


def test_downhill_monotonic_decrease_from_peak():
    scores = [_downhill_segment_score(s) for s in range(8, 31)]
    for i in range(1, len(scores)):
        assert scores[i] <= scores[i - 1]


def test_downhill_sweet_spot_is_wide():
    """5-12 degrees all score >= 90."""
    for slope in range(5, 13):
        assert _downhill_segment_score(slope) >= 90, f"{slope} deg should be >= 90"


def test_downhill_12_degrees_still_great():
    """12 degrees is still in the sweet spot."""
    assert _downhill_segment_score(12) == pytest.approx(90)


# -- Uphill scoring: exact breakpoints --

def test_uphill_sweet_spot():
    """At or below 7 degrees is pinned at the uphill peak."""
    assert _uphill_segment_score(5) == 100


def test_uphill_breakpoint_values():
    """Verify exact scores at each piecewise boundary."""
    assert _uphill_segment_score(0) == 100
    assert _uphill_segment_score(2) == 100
    assert _uphill_segment_score(7) == 100
    assert _uphill_segment_score(8) == pytest.approx(94.34782608695652)
    assert _uphill_segment_score(12) == pytest.approx(71.73913043478261)
    assert _uphill_segment_score(15) == pytest.approx(54.78260869565217)
    assert _uphill_segment_score(20) == pytest.approx(26.52173913043478)
    assert _uphill_segment_score(30) == -30


def test_uphill_steep_goes_negative():
    """Very steep uphill eventually goes negative, then floors."""
    assert _uphill_segment_score(25) < 0
    assert _uphill_segment_score(30) == -30
    assert _uphill_segment_score(35) == -30


def test_uphill_monotonic_decrease_from_peak():
    scores = [_uphill_segment_score(s) for s in range(7, 31)]
    for i in range(1, len(scores)):
        assert scores[i] <= scores[i - 1]


def test_uphill_sweet_spot_is_wide():
    """2-8 degrees all score >= 90."""
    for slope in range(2, 9):
        assert _uphill_segment_score(slope) >= 90, f"{slope} deg should be >= 90"


def test_uphill_8_degrees_is_good():
    """8 degrees is efficient all-day touring grade."""
    assert _uphill_segment_score(8) == pytest.approx(94.34782608695652)


# -- Ground slope penalty --

def test_ground_slope_penalty_flat():
    """No penalty on normal terrain."""
    assert _ground_slope_penalty(None) == 1.0
    assert _ground_slope_penalty(10) == 1.0
    assert _ground_slope_penalty(19) == 1.0


def test_ground_slope_penalty_steep():
    """Steep terrain reduces scores."""
    assert _ground_slope_penalty(20) == 1.0
    assert _ground_slope_penalty(25) == pytest.approx(0.8125)
    assert _ground_slope_penalty(30) == pytest.approx(0.625)
    assert _ground_slope_penalty(40) == pytest.approx(0.25)


def test_ground_slope_penalty_extreme():
    """Extreme terrain floors at 0.25."""
    assert _ground_slope_penalty(50) == pytest.approx(0.25)
    assert _ground_slope_penalty(60) == 0.25


def test_ground_slope_penalty_28_is_noticeable():
    """The user's example: 28 degree ground should have a clear penalty."""
    penalty = _ground_slope_penalty(28)
    assert 0.65 <= penalty <= 0.85


# -- Avalanche danger --

def test_avy_danger_full_penalty_band_and_tapers():
    assert _avy_slope_danger(30) == pytest.approx(1.0)
    assert _avy_slope_danger(38.5) == pytest.approx(1.0)
    assert _avy_slope_danger(45) == pytest.approx(1.0)
    assert _avy_slope_danger(28) == pytest.approx(0.5)
    assert _avy_slope_danger(47) == pytest.approx(0.5)


def test_avy_danger_zero_outside_range():
    assert _avy_slope_danger(10) == 0.0
    assert _avy_slope_danger(25) == 0.0
    assert _avy_slope_danger(50) == 0.0
    assert _avy_slope_danger(60) == 0.0


def test_avy_danger_symmetric_tapers():
    """Penalty taper should be symmetric around the core band edges."""
    assert _avy_slope_danger(28) == pytest.approx(_avy_slope_danger(47), abs=1e-6)


# -- Integration: score_tour --

def test_score_tour_empty(dem):
    score = score_tour([], dem)
    assert score.total == 0
    assert score.downhill_quality == 0
    assert score.uphill_quality == 0
    assert score.avy_exposure == 0


def test_score_tour_downhill_only(dem):
    """A pure downhill at sweet spot should score high."""
    points = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=500, track_slope=-8, ground_slope=12),
        _make_point(distance=1000, track_slope=-8, ground_slope=10),
    ]
    score = score_tour(points, dem)
    assert score.downhill_quality == 115  # sweet spot peak
    assert score.uphill_quality == 50     # no uphill = neutral
    assert score.avg_uphill_slope == 0
    assert score.avg_downhill_slope == 8


def test_score_tour_uphill_only(dem):
    """A pure uphill track at 4 deg should score well."""
    points = [
        _make_point(distance=0, track_slope=None, elevation=1000),
        _make_point(distance=500, track_slope=4, elevation=1500),
        _make_point(distance=1000, track_slope=4, elevation=2000),
    ]
    score = score_tour(points, dem)
    assert score.uphill_quality > 95  # 4 deg is near peak
    assert score.downhill_quality == 0
    assert score.avg_uphill_slope == 4


def test_score_tour_mixed(dem):
    """A track with both up and down should score both components."""
    points = [
        _make_point(distance=0, track_slope=None, elevation=1000),
        _make_point(distance=500, track_slope=5, elevation=1500),
        _make_point(distance=1000, track_slope=5, elevation=2000),
        _make_point(distance=1500, track_slope=-7, elevation=1500),
        _make_point(distance=2000, track_slope=-7, elevation=1000),
    ]
    score = score_tour(points, dem)
    assert score.uphill_quality == 100
    assert score.downhill_quality > 100  # sweet spot bonus


def test_score_tour_no_ground_slope(dem):
    """Points without ground slope should give perfect avy safety."""
    points = [
        _make_point(distance=0, track_slope=None, ground_slope=None),
        _make_point(distance=500, track_slope=-8, ground_slope=None),
    ]
    score = score_tour(points, dem)
    assert score.avy_exposure == 100.0
    assert score.pct_avy_terrain == 0.0


def test_sweet_spot_downhill_beats_steep(dem):
    """A tour at the sweet spot should outscore a scary steep one."""
    sweet = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-7),
        _make_point(distance=1000, track_slope=-7),
    ]
    steep = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-25),
        _make_point(distance=1000, track_slope=-25),
    ]
    assert score_tour(sweet, dem).downhill_quality > 100
    assert score_tour(steep, dem).downhill_quality == -20


def test_sweet_spot_downhill_beats_flat(dem):
    """Sweet spot slopes outscore boring flat traverses."""
    sweet = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-7),
        _make_point(distance=1000, track_slope=-7),
    ]
    flat = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-1),
        _make_point(distance=1000, track_slope=-1),
    ]
    assert score_tour(sweet, dem).downhill_quality > 100
    assert score_tour(flat, dem).downhill_quality == 80


def test_scary_descent_scores_negative(dem):
    """A consistently steep descent (22 deg) should score negative."""
    points = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-22),
        _make_point(distance=1000, track_slope=-22),
        _make_point(distance=1500, track_slope=-22),
    ]
    score = score_tour(points, dem)
    assert score.downhill_quality < 0


def test_brutal_uphill_scores_negative(dem):
    """Very steep uphill (18 deg) should score much worse than mellow."""
    points = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=18),
        _make_point(distance=1000, track_slope=18),
    ]
    score = score_tour(points, dem)
    assert score.uphill_quality < 60


def test_mixed_descent_worse_than_pure_sweet_spot(dem):
    """A descent with scary segments should score worse than pure sweet spot."""
    pure_sweet = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-7),
        _make_point(distance=1000, track_slope=-7),
        _make_point(distance=1500, track_slope=-7),
        _make_point(distance=2000, track_slope=-7),
    ]
    mixed = [
        _make_point(distance=0, track_slope=None),
        _make_point(distance=500, track_slope=-7),
        _make_point(distance=1000, track_slope=-7),
        _make_point(distance=1500, track_slope=-22),
        _make_point(distance=2000, track_slope=-22),
    ]
    assert score_tour(pure_sweet, dem).downhill_quality > score_tour(mixed, dem).downhill_quality


def test_avy_safe_terrain_scores_higher(dem):
    """Flat ground should score better for avy safety than prime avy terrain."""
    safe = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=500, track_slope=-8, ground_slope=10),
    ]
    dangerous = [
        _make_point(distance=0, track_slope=None, ground_slope=38),
        _make_point(distance=500, track_slope=-8, ground_slope=38),
    ]
    assert score_tour(safe, dem).avy_exposure > score_tour(dangerous, dem).avy_exposure


def test_total_score_bounded(dem):
    """Total score is always 0-100 even when components exceed that."""
    points = [
        _make_point(lat=45.37, lon=-121.70, distance=0,
                    track_slope=None, ground_slope=10, elevation=3000),
        _make_point(lat=45.36, lon=-121.70, distance=1000,
                    track_slope=-7, ground_slope=10, elevation=2500),
        _make_point(lat=45.35, lon=-121.70, distance=2000,
                    track_slope=-7, ground_slope=10, elevation=2000),
    ]
    score = score_tour(points, dem)
    assert 0 <= score.total <= 100


def test_component_can_exceed_100(dem):
    """Sweet spot tours should give component scores above 100."""
    points = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=500, track_slope=-7, ground_slope=10),
        _make_point(distance=1000, track_slope=-7, ground_slope=10),
    ]
    score = score_tour(points, dem)
    assert score.downhill_quality > 100


def test_component_can_go_negative(dem):
    """Impossible terrain should give negative component scores."""
    points = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=500, track_slope=-30, ground_slope=10),
        _make_point(distance=1000, track_slope=-30, ground_slope=10),
    ]
    score = score_tour(points, dem)
    assert score.downhill_quality < 0


def test_total_clamped_at_zero(dem):
    """Total can't go below 0 even with terrible components."""
    points = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=500, track_slope=-30, ground_slope=40),
        _make_point(distance=1000, track_slope=-30, ground_slope=40),
    ]
    score = score_tour(points, dem)
    assert score.total >= 0


def test_ground_penalty_reduces_quality(dem):
    """Steep ground should reduce downhill quality even with good track slope."""
    flat_ground = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=500, track_slope=-7, ground_slope=10),
        _make_point(distance=1000, track_slope=-7, ground_slope=10),
    ]
    steep_ground = [
        _make_point(distance=0, track_slope=None, ground_slope=35),
        _make_point(distance=500, track_slope=-7, ground_slope=35),
        _make_point(distance=1000, track_slope=-7, ground_slope=35),
    ]
    assert score_tour(flat_ground, dem).downhill_quality > score_tour(steep_ground, dem).downhill_quality


# -- Realistic touring scenarios --

def _make_tour(segments, ground_slope=10):
    """Build a track from a list of (track_slope, count) tuples.

    Each segment is repeated `count` times at 100m spacing.
    Simulates a real tour where terrain changes gradually.
    """
    points = [_make_point(distance=0, track_slope=None, ground_slope=ground_slope)]
    d = 0
    for slope, count in segments:
        for _ in range(count):
            d += 100
            points.append(_make_point(distance=d, track_slope=slope,
                                      ground_slope=ground_slope))
    return points


def test_lake_crossing_flat_traverse(dem):
    """Long flat crossing (like skiing across a frozen lake or meadow).

    Even shallow slopes are now classified by sign only.
    """
    # 0.3 is still counted as downhill (small but non-zero credit)
    dead_flat = _make_tour([(-0.3, 20)], ground_slope=2)
    score_flat = score_tour(dead_flat, dem)
    assert score_flat.downhill_quality > 70
    assert score_flat.uphill_quality == 50   # neutral

    # 1.5 is still gentle, but now receives moderate downhill credit.
    slight_tilt = _make_tour([(-1.5, 20)], ground_slope=2)
    score_tilt = score_tour(slight_tilt, dem)
    assert 80 < score_tilt.downhill_quality < 90
    assert score_tilt.total > 30


def test_switchbacks_on_steep_face(dem):
    """Climbing a 32-degree slope via switchbacks (8-deg track slope).

    Track slope is in the uphill sweet spot, but ground slope penalty
    should noticeably reduce the score -- skiing a 32-deg face sucks
    even if your track angle is fine.
    """
    # 8 skin track on 32 ground
    steep_face = _make_tour([(8, 15)], ground_slope=32)
    gentle_bowl = _make_tour([(8, 15)], ground_slope=12)

    steep_score = score_tour(steep_face, dem).uphill_quality
    gentle_score = score_tour(gentle_bowl, dem).uphill_quality

    assert gentle_score > steep_score, "same track slope, steeper ground should score worse"
    # Ground penalty at 32 is 0.55, so ~94 * 0.55 = 52
    assert 40 < steep_score < 75


def test_one_cliff_band_in_mellow_descent(dem):
    """Mellow 8-degree descent with one short cliff band at 25 degrees.

    Should score noticeably worse than pure mellow, but the cliff band
    is short enough that it doesn't destroy the whole score.
    """
    # 1.5km mellow, 200m cliff, 1km mellow
    mellow_with_cliff = _make_tour([(-8, 15), (-25, 2), (-8, 10)])
    pure_mellow = _make_tour([(-8, 27)])

    cliff_score = score_tour(mellow_with_cliff, dem).downhill_quality
    mellow_score = score_tour(pure_mellow, dem).downhill_quality

    assert mellow_score > cliff_score
    # The 2 cliff segments (-50 each) drag the 27-segment average
    # down, but 25 mellow segments at ~105 still dominate
    assert cliff_score > 65, "short cliff shouldn't ruin a mostly-great tour"


def test_bootpack_section_in_skin_track(dem):
    """A skin track that hits a bootpack section (25+ degrees).

    Common in real touring: the approach is fine but you have to
    bootpack a headwall. Should reduce uphill score significantly.
    """
    # 1km gentle skin, 300m bootpack, 500m gentle
    with_bootpack = _make_tour([(6, 10), (25, 3), (6, 5)])
    pure_skin = _make_tour([(6, 18)])

    boot_score = score_tour(with_bootpack, dem).uphill_quality
    skin_score = score_tour(pure_skin, dem).uphill_quality

    assert skin_score > boot_score
    # 3 segments around -1.7 should hurt but not annihilate
    assert boot_score > 30


def test_sustained_steep_descent_is_terrible(dem):
    """Long sustained steep descent (18-20 degrees) -- scary on XC gear.

    This should score very poorly for downhill quality.
    A real example: accidentally skiing a black diamond on XC skis.
    """
    points = _make_tour([(-19, 20)], ground_slope=22)
    score = score_tour(points, dem)
    # 19 scores about +20 in the updated curve, then gets a mild ground multiplier.
    assert score.downhill_quality < 25


def test_out_and_back_symmetry(dem):
    """An out-and-back tour should score reasonably on both components.

    Climb at 7 degrees, ski down at 7 degrees -- both should be
    in the sweet spot. The uphill and downhill avgs should match.
    """
    points = _make_tour([(7, 10), (-7, 10)])
    score = score_tour(points, dem)

    assert score.avg_uphill_slope == pytest.approx(7, abs=0.1)
    assert score.avg_downhill_slope == pytest.approx(7, abs=0.1)
    assert score.uphill_quality > 85
    assert score.downhill_quality > 100  # 7 is near the DH peak
    assert score.total > 75


def test_ridge_traverse_barely_any_vertical(dem):
    """A ridge traverse: mostly flat with tiny undulations.

    Very little elevation change -- should score okay but not exciting.
    Track slopes alternate between +1 and -1.
    """
    # Alternating tiny ups and downs
    segments = [(-1, 2), (1, 2)] * 5  # 2km of ridge traverse
    points = _make_tour(segments, ground_slope=15)
    score = score_tour(points, dem)

    # Flat terrain gets modest DH and pinned UH at 100 for tiny climbs.
    assert 70 < score.downhill_quality < 90
    assert score.uphill_quality == 100
    assert score.total > 40


def test_avy_terrain_throughout(dem):
    """Tour entirely on 35-degree avalanche terrain.

    Track slopes are fine (8 down) but the ground under you is
    prime avalanche terrain. Should crush the avy score and also
    reduce DH quality via ground penalty.
    """
    points = _make_tour([(-8, 15)], ground_slope=38)
    score = score_tour(points, dem)

    assert score.avy_exposure < 50, "walking through avy terrain all day"
    # Ground penalty at 38 = 0.38, so DH = 105 * 0.38 = 40
    assert score.downhill_quality < 60


def test_gentle_approach_steep_summit(dem):
    """Common pattern: gentle approach, then steep summit push.

    Like skinning to a hut (4 for 3km) then a steep final pitch
    (14 for 500m). Should score well overall with some penalty
    for the steep bit.
    """
    points = _make_tour([(4, 30), (14, 5)])
    score = score_tour(points, dem)

    # 30 gentle segments at 100 and 5 steep around ~60.
    assert score.uphill_quality > 70


def test_gps_spike_doesnt_dominate(dem):
    """A single GPS noise spike (45 for one point) in mellow terrain.

    A real GPS glitch. With averages (not max), one bad point in 20
    shouldn't ruin the tour score.
    """
    # 19 good segments, 1 spike
    good = [(-7, 19)]
    spike = [(-45, 1)]
    points = _make_tour(good + spike)
    score = score_tour(points, dem)

    # 19 x ~103 + 1 x (-50) = 1907/20 = 95 -- still great
    assert score.downhill_quality > 85


def test_pure_traverse_no_up_no_down(dem):
    """A pure traverse where track_slope is always near zero.

    All points have tiny +/- slopes; these are still classified by sign.
    """
    points = [_make_point(distance=0, track_slope=None, ground_slope=15)]
    for i in range(10):
        points.append(_make_point(
            distance=(i + 1) * 100,
            track_slope=0.3 * (1 if i % 2 else -1),
            ground_slope=15,
        ))
    score = score_tour(points, dem)

    assert score.downhill_quality > 70
    assert score.uphill_quality == 100


def test_extremely_short_tour(dem):
    """A tour with exactly 2 points -- minimum viable input.

    Should produce a score without crashing. Only 1 segment
    actually has a slope.
    """
    points = [
        _make_point(distance=0, track_slope=None, ground_slope=10),
        _make_point(distance=50, track_slope=-8, ground_slope=10),
    ]
    score = score_tour(points, dem)
    # Should work, single downhill segment
    assert score.downhill_quality > 90
    assert score.total > 0


def test_steep_ground_flat_track(dem):
    """Traversing steep (30) ground at a near-flat track angle.

    The track barely goes up or down but the ground is scary steep.
    DH/UH will be sparse (few classified segments), but any that
    do register should be penalized by ground slope.
    """
    points = _make_tour([(-2, 10)], ground_slope=30)
    score = score_tour(points, dem)
    # 2 DH on 30 ground: base score 75, penalty 0.7 -> 52.5
    assert score.downhill_quality < 60


def test_variable_ground_slope(dem):
    """Tour where ground slope varies wildly per point.

    Some points on flat meadow (5), some on steep face (35).
    The ground penalty should apply per-segment, not as an average.
    """
    points = [_make_point(distance=0, track_slope=None, ground_slope=5)]
    for i in range(10):
        gs = 5 if i < 5 else 35  # half on flat, half on steep
        points.append(_make_point(
            distance=(i + 1) * 100,
            track_slope=-7,
            ground_slope=gs,
        ))
    score = score_tour(points, dem)

    # 5 segments: ~103 * 1.0 = 103, 5 segments: ~103 * 0.5 = 52
    # Average = 77.5
    assert 70 < score.downhill_quality < 90


# -- Vectorized scoring --

def test_avy_slope_dangers_matches_scalar():
    """Vectorized _avy_slope_dangers should match scalar _avy_slope_danger."""
    slopes = np.array([10.0, 25.0, 30.0, 38.5, 45.0, 50.0, 55.0, 60.0])
    batch = _avy_slope_dangers(slopes)
    for i, s in enumerate(slopes):
        expected = _avy_slope_danger(float(s))
        assert batch[i] == pytest.approx(expected, abs=1e-10), (
            f"slope={s}: batch={batch[i]}, scalar={expected}")


def test_compute_runout_exposures_matches_scalar(dem):
    """Vectorized _compute_runout_exposures should closely match scalar version."""
    lats = np.array([45.350, 45.351, 45.352])
    lons = np.array([-121.710, -121.711, -121.712])

    batch = _compute_runout_exposures(lats, lons, dem)

    for i in range(len(lats)):
        scalar = _compute_runout_exposure(float(lats[i]), float(lons[i]), dem)
        assert batch[i] == pytest.approx(scalar, abs=0.05), (
            f"Point {i}: batch={batch[i]}, scalar={scalar}")


# -- Property-based fuzz tests (Hypothesis) --

# Slopes from 0 to 60 degrees, including fractional values
slope_st = st.floats(min_value=0, max_value=60, allow_nan=False, allow_infinity=False)
# Ground slopes including None
ground_slope_st = st.one_of(st.none(), slope_st)


@given(slope=slope_st)
def test_fuzz_downhill_monotonic(slope):
    """Downhill score should never increase past the 8-degree peak."""
    if slope >= 8:
        assert _downhill_segment_score(slope) <= _downhill_segment_score(8)


@given(slope=slope_st)
def test_fuzz_downhill_bounded(slope):
    """Downhill score should be between -20 and 115 for any slope."""
    score = _downhill_segment_score(slope)
    assert -20 <= score <= 115


@given(slope=slope_st)
def test_fuzz_uphill_monotonic(slope):
    """Uphill score should never increase past the 7-degree peak plateau."""
    if slope >= 7:
        assert _uphill_segment_score(slope) <= _uphill_segment_score(7)


@given(slope=slope_st)
def test_fuzz_uphill_bounded(slope):
    """Uphill score should be between -30 and 100 for any slope."""
    score = _uphill_segment_score(slope)
    assert -30 <= score <= 100


@given(ground_slope=ground_slope_st)
def test_fuzz_ground_penalty_bounded(ground_slope):
    """Ground slope penalty should always be in [0.25, 1.0]."""
    penalty = _ground_slope_penalty(ground_slope)
    assert 0.25 <= penalty <= 1.0


@given(slope=slope_st)
def test_fuzz_avy_danger_bounded(slope):
    """Avy danger should always be in [0, 1]."""
    danger = _avy_slope_danger(slope)
    assert 0.0 <= danger <= 1.0


@given(slopes=st.lists(slope_st, min_size=1, max_size=20))
def test_fuzz_avy_dangers_matches_scalar(slopes):
    """Vectorized avy danger should match scalar for any slopes."""
    arr = np.array(slopes)
    batch = _avy_slope_dangers(arr)
    for i, s in enumerate(slopes):
        expected = _avy_slope_danger(s)
        assert batch[i] == pytest.approx(expected, abs=1e-10), (
            f"slope={s}: batch={batch[i]}, scalar={expected}")


@given(
    track_slopes=st.lists(
        st.floats(min_value=-40, max_value=40, allow_nan=False, allow_infinity=False),
        min_size=2, max_size=10,
    ),
    ground_slope=st.floats(min_value=0, max_value=50, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, deadline=None)
def test_fuzz_score_tour_total_bounded(track_slopes, ground_slope):
    """Total score should always be clamped to [0, 100] for any input."""
    dem = _fuzz_dem()
    points = [_make_point(distance=0, track_slope=None, ground_slope=ground_slope)]
    for i, ts in enumerate(track_slopes):
        points.append(_make_point(
            distance=(i + 1) * 100,
            track_slope=ts,
            ground_slope=ground_slope,
        ))
    score = score_tour(points, dem)
    assert 0 <= score.total <= 100, f"total={score.total}"
