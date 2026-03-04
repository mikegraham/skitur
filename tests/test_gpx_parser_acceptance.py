from __future__ import annotations

import math
from pathlib import Path

import gpxpy
import pytest

from skitur.gpx import load_track


def _load_track_gpxpy(path: Path) -> list[tuple[float, float]]:
    with open(path) as f:
        gpx = gpxpy.parse(f)

    points: list[tuple[float, float]] = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((float(point.latitude), float(point.longitude)))
    return points


def _real_fixture_corpus() -> list[Path]:
    project_root = Path(__file__).resolve().parents[1]
    root_gpx = project_root.glob("*.gpx")
    test_data_gpx = (project_root / "tests" / "data").glob("*.gpx")
    return sorted({*root_gpx, *test_data_gpx})


def test_real_fixture_corpus_not_empty() -> None:
    assert _real_fixture_corpus(), "No GPX fixtures found for acceptance testing"


@pytest.mark.parametrize(
    "gpx_path",
    _real_fixture_corpus(),
    ids=lambda p: str(p.relative_to(Path(__file__).resolve().parents[1])),
)
def test_lxml_loader_matches_gpxpy_on_real_fixture_corpus(gpx_path: Path) -> None:
    expected = _load_track_gpxpy(gpx_path)
    actual = load_track(gpx_path)

    assert len(actual) == len(expected), f"point count mismatch for {gpx_path}"

    for i, ((alat, alon), (elat, elon)) in enumerate(zip(actual, expected)):
        assert math.isclose(
            alat, elat, abs_tol=1e-12
        ), f"lat mismatch at idx={i} for {gpx_path}: {alat} != {elat}"
        assert math.isclose(
            alon, elon, abs_tol=1e-12
        ), f"lon mismatch at idx={i} for {gpx_path}: {alon} != {elon}"
