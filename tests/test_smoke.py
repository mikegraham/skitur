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
    assert len(data["track"]) > 10
    assert data["stats"]["total_distance_m"] > 0
    assert 0 <= data["score"]["total"] <= 100


def test_analyze_html(warm_service):
    with TEST_GPX.open("rb") as f:
        resp = requests.post(
            f"{BASE_URL}/analyze",
            files={"gpx_file": ("hood_descent.gpx", f, "application/octet-stream")},
            timeout=120,
        )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "renderResults" in resp.text
    assert 'id="results-section"' in resp.text
