import subprocess
import sys
from pathlib import Path

import pytest

from skitur.analyze import TrackPoint, analyze_track
from skitur.gpx import load_track
from skitur.stats import compute_stats

TEST_GPX = Path(__file__).parent / "data" / "hood_descent.gpx"
pytestmark = pytest.mark.enable_socket


def test_compute_stats():
    """Stats should reflect known properties of the descent route."""
    points = load_track(TEST_GPX)
    analysis = analyze_track(points, resample=False)
    stats = compute_stats(analysis)

    assert stats['total_distance_m'] > 4000
    assert stats['elevation_loss_m'] > 0  # downhill route
    assert stats['elevation_gain_m'] < stats['elevation_loss_m']
    assert stats['max_elevation_m'] > stats['min_elevation_m']
    assert stats['downhill_max'] > stats['downhill_avg'] > 0


def test_compute_stats_uphill_only():
    """Stats for an uphill-only track should show gain but no downhill."""
    points = [
        TrackPoint(45.0, -121.0, 1000, 0, None, 10),
        TrackPoint(45.01, -121.0, 1500, 500, 5.0, 10),
        TrackPoint(45.02, -121.0, 2000, 1000, 5.0, 10),
    ]
    stats = compute_stats(points)
    assert stats['uphill_avg'] > 0
    assert stats['downhill_avg'] == 0
    assert stats['elevation_gain_m'] > 0
    assert stats['elevation_loss_m'] == 0


def test_main_subprocess_generates_report(tmp_path):
    """`python -m skitur` should generate a static HTML report."""
    out = tmp_path / "hood_report.html"
    result = subprocess.run(
        [sys.executable, "-m", "skitur", str(TEST_GPX), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert out.exists(), "Expected report HTML was not created"
    html = out.read_text()
    assert "renderResults" in html
    assert "Tour Quality Score" in html
