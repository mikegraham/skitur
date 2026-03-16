"""Post-deploy smoke tests for the live service.

Run with: SMOKE_TEST_URL=https://fjell.ski pytest tests/test_smoke.py -v

Skipped entirely if SMOKE_TEST_URL is not set.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import requests  # type: ignore[import-untyped]

BASE_URL = os.environ.get("SMOKE_TEST_URL", "")
TEST_GPX = Path(__file__).parent / "data" / "hood_descent.gpx"

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.enable_socket,
    pytest.mark.skipif(not BASE_URL, reason="SMOKE_TEST_URL not set"),
]


@pytest.fixture(scope="module")
def warm_service():
    """Wait for the service to be ready (handles scale-to-zero cold starts)."""
    url = f"{BASE_URL}/healthz"
    deadline = time.monotonic() + 90
    last_err = None
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(3)
    pytest.fail(f"Service not ready after 90s: {last_err}")


def test_landing_page(warm_service):
    resp = requests.get(f"{BASE_URL}/", timeout=10)
    assert resp.status_code == 200
    assert "gpx" in resp.text.lower()


def test_healthz(warm_service):
    resp = requests.get(f"{BASE_URL}/healthz", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_analyze_json(warm_service):
    with TEST_GPX.open("rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/analyze",
            files={"gpx_file": ("hood_descent.gpx", f, "application/octet-stream")},
            timeout=120,
        )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("track", "stats", "score", "slope_grid", "contours"):
        assert key in data, f"Missing key: {key}"

    # Track structure
    assert len(data["track"]) > 10
    pt = data["track"][5]
    for key in ("lat", "lon", "elevation", "distance", "track_slope", "ground_slope"):
        assert key in pt, f"Track point missing key: {key}"
    assert 45.3 < pt["lat"] < 45.4, "Track should be on Mt Hood"

    # Stats
    stats = data["stats"]
    assert stats["total_distance_m"] > 3000, "Hood descent should be >3km"
    assert stats["elevation_loss_m"] > 1000, "Hood descent should lose >1000m"

    # Score
    score = data["score"]
    assert 0 <= score["total"] <= 100
    assert score["total"] < 60, "Hood descent should score poorly as XC tour"
    assert score["pct_avy_terrain"] > 0, "Hood should have avy terrain"
    for key in ("downhill_quality", "uphill_quality", "avy_exposure"):
        assert key in score, f"Score missing key: {key}"

    # Slope grid
    sg = data["slope_grid"]
    assert sg["rows"] > 0 and sg["cols"] > 0
    assert len(sg["data"]) == sg["rows"] * sg["cols"]

    # Contours
    assert len(data["contours"]["major"]) > 0, "Should have contour lines"


def test_analyze_html(warm_service):
    with TEST_GPX.open("rb") as f:
        resp = requests.post(
            f"{BASE_URL}/analyze",
            files={"gpx_file": ("hood_descent.gpx", f, "application/octet-stream")},
            timeout=120,
        )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert 'id="results-section"' in resp.text
    assert 'id="upload-section"' not in resp.text
    # Verify the report has embedded data and rendering code
    assert "renderResults" in resp.text
    assert "Plotly" in resp.text or "plotly" in resp.text
    assert "leaflet" in resp.text.lower()
    # Verify it contains actual track data (not just the template)
    assert "hood_descent" in resp.text
