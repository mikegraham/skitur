#!/usr/bin/env python3
"""Smoke test for the live service.

Usage:
    python scripts/smoke_test.py https://fjell.ski
    python scripts/smoke_test.py http://localhost:8000 --gpx tests/data/jotunheimen_short.gpx
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

_DATA_DIR = Path(__file__).resolve().parent.parent / "tests" / "data"
DEFAULT_GPX = _DATA_DIR / "hood_descent.gpx"

failures = 0


def check(name, ok, msg=""):
    global failures  # noqa: PLW0603
    status = "PASS" if ok else "FAIL"
    suffix = f" -- {msg}" if msg else ""
    print(f"  [{status}] {name}{suffix}")
    if not ok:
        failures += 1


def wait_for_service(base_url):
    print("Waiting for service...")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{base_url}/healthz", timeout=10)
            if resp.status_code == 200:
                print("  Service is up.")
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    print("  FAIL: service not ready after 90s")
    sys.exit(1)


def test_landing_page(base_url):
    resp = requests.get(f"{base_url}/", timeout=10)
    check("landing page status", resp.status_code == 200, f"got {resp.status_code}")
    check("landing page has GPX input", "gpx" in resp.text.lower())


def test_healthz(base_url):
    resp = requests.get(f"{base_url}/healthz", timeout=10)
    check("healthz status", resp.status_code == 200)
    data = resp.json()
    check("healthz body", data.get("status") == "ok", f"got {data}")


def test_analyze_json(base_url, gpx_path):
    with gpx_path.open("rb") as f:
        resp = requests.post(
            f"{base_url}/api/analyze",
            files={"gpx_file": (gpx_path.name, f, "application/octet-stream")},
            timeout=300,
        )
    check("analyze JSON status", resp.status_code == 200, f"got {resp.status_code}")
    try:
        data = resp.json()
    except requests.exceptions.JSONDecodeError:
        check("analyze JSON parseable", False, f"got {resp.text[:200]}")
        return

    for key in ("track", "stats", "score", "slope_grid", "contours"):
        check(f"has '{key}'", key in data)

    track = data.get("track", [])
    check("track has points", len(track) > 2, f"got {len(track)}")

    if len(track) > 1:
        pt = track[1]
        for key in ("lat", "lon", "elevation", "distance", "track_slope", "ground_slope"):
            check(f"point has '{key}'", key in pt)

    stats = data.get("stats", {})
    check("has distance", stats.get("total_distance_m", 0) > 0)

    elevs = [p["elevation"] for p in track if p.get("elevation") is not None]
    check("has elevation data", len(elevs) > 0, f"{len(elevs)}/{len(track)} non-null")

    score = data.get("score", {})
    check("score 0-100", 0 <= score.get("total", -1) <= 100)

    sg = data.get("slope_grid", {})
    check("slope grid has data", sg.get("rows", 0) > 0 and sg.get("cols", 0) > 0)


def test_analyze_html(base_url, gpx_path):
    with gpx_path.open("rb") as f:
        resp = requests.post(
            f"{base_url}/analyze",
            files={"gpx_file": (gpx_path.name, f, "application/octet-stream")},
            timeout=300,
        )
    check("analyze HTML status", resp.status_code == 200, f"got {resp.status_code}")
    check("content-type is HTML", "text/html" in resp.headers.get("content-type", ""))
    check("has results-section", 'id="results-section"' in resp.text)
    check("no upload-section", 'id="upload-section"' not in resp.text)
    check("has renderResults", "renderResults" in resp.text)
    check("has embedded track data", gpx_path.stem in resp.text)


def test_browser_render(base_url, gpx_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})

        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{base_url}/")
        page.set_input_files('input[name="gpx_file"]', str(gpx_path))

        try:
            page.wait_for_function(
                """() => {
                    const r = document.getElementById('results-section');
                    if (!r || getComputedStyle(r).display === 'none') return false;
                    const chart = !!document.querySelector('#elevation-chart .plot-container');
                    const map = Array.from(document.querySelectorAll('#map img'))
                        .some(i => i.src && i.src.startsWith('data:image/png'));
                    const score = !!document.querySelector('.score-total');
                    return chart && map && score;
                }""",
                timeout=180_000,
            )
            check("report renders in browser", True)
        except Exception as exc:
            check("report renders in browser", False, str(exc))

        check("no JS errors", len(errors) == 0, str(errors) if errors else "")
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Smoke test the live service.")
    parser.add_argument("url", help="Base URL (e.g. https://fjell.ski)")
    parser.add_argument("--gpx", type=Path, default=DEFAULT_GPX,
                        help="GPX file to upload (default: hood_descent.gpx)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    gpx_path = args.gpx.resolve()

    wait_for_service(base_url)
    print(f"\nRunning smoke tests against {base_url} with {gpx_path.name}...\n")

    test_landing_page(base_url)
    test_healthz(base_url)
    test_analyze_json(base_url, gpx_path)
    test_analyze_html(base_url, gpx_path)
    test_browser_render(base_url, gpx_path)

    print(f"\n{'PASSED' if failures == 0 else 'FAILED'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
