import subprocess
import sys
from pathlib import Path

from skitur.cli import main, compute_stats, parse_args
from skitur.gpx import load_track
from skitur.analyze import analyze_track, TrackPoint

TEST_GPX = Path(__file__).parent / "data" / "hood_descent.gpx"


def test_parse_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["skitur", "test.gpx"])
    args = parse_args()
    assert args.gpx_file == Path("test.gpx")
    assert args.output is None
    assert args.no_plots is False
    assert args.no_resample is False


def test_parse_args_all_flags(monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["skitur", "test.gpx", "-o", "out/", "--no-plots", "--no-resample"],
    )
    args = parse_args()
    assert args.output == Path("out/")
    assert args.no_plots is True
    assert args.no_resample is True


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


def test_main_generates_all_plots(tmp_path):
    main(TEST_GPX, output_dir=tmp_path, generate_plots=True)

    expected = ["slopes.png", "elevation.png", "histogram.png",
                "topo.png", "slope_map.png"]
    for name in expected:
        assert (tmp_path / name).exists(), f"Missing {name}"


def test_main_no_plots_skips_output(tmp_path):
    main(TEST_GPX, output_dir=tmp_path, generate_plots=False)
    # Output dir should have no plot files
    assert not list(tmp_path.glob("*.png"))


def test_cli_subprocess():
    """Full CLI invocation should print stats and score."""
    result = subprocess.run(
        [sys.executable, "-m", "skitur.cli", str(TEST_GPX), "--no-plots"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Track Statistics" in result.stdout
    assert "Tour Quality Score" in result.stdout
