#!/usr/bin/env python3
"""Regenerate README screenshots from current code.

Usage: .venv/bin/python scripts/regen_readme_screenshots.py
"""

from __future__ import annotations

import re
import tempfile
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from playwright.sync_api import sync_playwright

from skitur.app import app
from skitur.report import build_embedded_report_html

ROOT = Path(__file__).resolve().parent.parent
GPX = ROOT / "tests" / "data" / "Twin_Lakes.gpx"
OUT = ROOT / "docs" / "readme_screenshots"
PLOTLY_JS = str(ROOT / "skitur" / "static" / "plotly-3.3.1.min.js")
LEAFLET_JS = str(ROOT / "tests" / "data" / "leaflet-1.9.4.js")
LEAFLET_CSS = str(ROOT / "tests" / "data" / "leaflet-1.9.4.css")

WAIT_JS = """() => {
    const r = document.getElementById('results-section');
    if (!r || getComputedStyle(r).display === 'none') return false;
    const slope = Array.from(document.querySelectorAll('#map img'))
      .some(i => i.src && i.src.startsWith('data:image/png'));
    const track = !!document.querySelector('#map canvas');
    const elev = !!document.querySelector('#elevation-chart .plot-container');
    const hist = !!document.querySelector('#histogram-chart .plot-container');
    const score = !!document.querySelector('.score-total');
    return slope && track && elev && hist && score;
}"""

# element ID or CSS selector -> output filename
SCREENSHOTS = [
    (None, "01_full_report.png"),        # full page
    ("#map-row", "03_map_and_legends.png"),
    ("#violin-chart", "06_track_slope_distribution.png"),
    ("#histogram-chart", "07_ground_angle_distribution.png"),
    (".aspect-charts-grid", "12_aspect_pair.png"),
]


def _intercept(route):
    url = route.request.url
    if "plotly" in url and url.endswith(".js"):
        route.fulfill(path=PLOTLY_JS, content_type="application/javascript")
    elif "leaflet" in url and url.endswith(".js"):
        route.fulfill(path=LEAFLET_JS, content_type="application/javascript")
    elif "leaflet" in url and url.endswith(".css"):
        route.fulfill(path=LEAFLET_CSS, content_type="text/css")
    else:
        route.continue_()


def main():
    client = app.test_client()
    with GPX.open("rb") as f:
        resp = client.post(
            "/api/analyze",
            data={"gpx_file": (f, "Twin_Lakes.gpx")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200

    tpl = (ROOT / "skitur" / "templates" / "report.html").read_text()
    html = build_embedded_report_html(tpl, resp.data, "Twin_Lakes.gpx")
    html = re.sub(r'\s+integrity="[^"]*"', "", html)

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w")
    tmp.write(html)
    tmp.close()
    tmp_path = Path(tmp.name)

    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path.parent))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.route("**/static/**", _intercept)
        page.goto(
            f"http://127.0.0.1:{port}/{tmp_path.name}",
            wait_until="domcontentloaded",
        )
        page.wait_for_function(WAIT_JS, timeout=30000)

        for selector, filename in SCREENSHOTS:
            path = OUT / filename
            if selector is None:
                page.screenshot(path=str(path), full_page=True)
            else:
                el = page.query_selector(selector)
                if el is None:
                    # Try parent .chart-wrapper for chart elements
                    inner = page.query_selector(selector)
                    if inner:
                        parent = inner.evaluate_handle(
                            'el => el.closest(".chart-wrapper")'
                        )
                        parent.as_element().screenshot(path=str(path))
                    else:
                        print(f"  SKIP {filename}: {selector} not found")
                        continue
                else:
                    el.screenshot(path=str(path))
            print(f"  {filename}")

        browser.close()

    server.shutdown()
    tmp_path.unlink(missing_ok=True)
    print("Done")


if __name__ == "__main__":
    main()
