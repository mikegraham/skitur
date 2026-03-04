import numpy as np
import pytest

from skitur.score import (
    _apply_ground_slope_multiplier,
    _avy_slope_penalties,
    _avy_slope_penalty,
    _downhill_segment_score,
    _ground_slope_penalty,
    _uphill_segment_score,
)


def test_avy_penalty_core_and_tapers():
    # Full penalty across 30-45 deg.
    for slope in [30.0, 34.0, 39.0, 45.0]:
        assert _avy_slope_penalty(slope) == pytest.approx(1.0)

    # Four-degree linear tapers on each side.
    assert _avy_slope_penalty(26.0) == pytest.approx(0.0)
    assert _avy_slope_penalty(28.0) == pytest.approx(0.5)
    assert _avy_slope_penalty(47.0) == pytest.approx(0.5)
    assert _avy_slope_penalty(49.0) == pytest.approx(0.0)

    # Outside the taper window is zero.
    assert _avy_slope_penalty(20.0) == pytest.approx(0.0)
    assert _avy_slope_penalty(55.0) == pytest.approx(0.0)


def test_avy_penalty_vectorized_matches_scalar():
    slopes = np.array([20.0, 26.0, 28.0, 30.0, 39.0, 45.0, 47.0, 49.0, 55.0])
    batch = _avy_slope_penalties(slopes)
    expected = np.array([_avy_slope_penalty(float(s)) for s in slopes])
    np.testing.assert_allclose(batch, expected, rtol=0.0, atol=1e-12)


def test_uphill_score_two_anchor_shape():
    assert _uphill_segment_score(0.0) == pytest.approx(100.0)
    assert _uphill_segment_score(7.0) == pytest.approx(100.0)
    assert _uphill_segment_score(16.0) == pytest.approx(49.1304347826, abs=1e-9)
    assert _uphill_segment_score(20.0) == pytest.approx(26.5217391304, abs=1e-9)
    assert _uphill_segment_score(30.0) == pytest.approx(-30.0)
    assert _uphill_segment_score(35.0) == pytest.approx(-30.0)


def test_downhill_score_anchor_points():
    assert _downhill_segment_score(0.0) == pytest.approx(70.0)
    assert _downhill_segment_score(3.0) == pytest.approx(100.0)
    assert _downhill_segment_score(6.0) == pytest.approx(115.0)
    assert _downhill_segment_score(8.0) == pytest.approx(115.0)
    assert _downhill_segment_score(12.0) == pytest.approx(90.0)
    assert _downhill_segment_score(18.0) == pytest.approx(40.0)
    assert _downhill_segment_score(21.0) == pytest.approx(-20.0)
    assert _downhill_segment_score(30.0) == pytest.approx(-20.0)


def test_ground_slope_multiplier_shape():
    assert _ground_slope_penalty(10.0) == pytest.approx(1.0)
    assert _ground_slope_penalty(20.0) == pytest.approx(1.0)
    assert _ground_slope_penalty(30.0) == pytest.approx(0.625)
    assert _ground_slope_penalty(40.0) == pytest.approx(0.25)
    assert _ground_slope_penalty(45.0) == pytest.approx(0.25)
    assert _ground_slope_penalty(60.0) == pytest.approx(0.25)


def test_ground_multiplier_does_not_soften_negative_scores():
    m = _ground_slope_penalty(40.0)  # 0.25
    assert _apply_ground_slope_multiplier(100.0, m) == pytest.approx(25.0)
    assert _apply_ground_slope_multiplier(-20.0, m) == pytest.approx(-20.0)
