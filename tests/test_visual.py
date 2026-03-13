"""Visual regression tests using Playwright.

Builds a debug page by injecting analysis JSON into the Flask template,
serves it over HTTP, and verifies rendering in headless Chromium.

Static assets (Plotly, Leaflet) are intercepted via Playwright's route API
and served from local copies so tests work without internet access.
"""

import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from skitur.app import app
from skitur.report import build_embedded_report_html

GPX_FILE = Path(__file__).parent / "data" / "Twin_Lakes.gpx"
_STATIC = Path(__file__).resolve().parent.parent / "skitur" / "static"
PLOTLY_JS = _STATIC / "plotly-3.3.1.min.js"
LEAFLET_JS = Path(__file__).parent / "data" / "leaflet-1.9.4.js"
LEAFLET_CSS = Path(__file__).parent / "data" / "leaflet-1.9.4.css"

pytestmark = pytest.mark.enable_socket


def _wait_for_report_render(page, timeout_ms: int = 30_000) -> None:
    page.wait_for_function(
        """() => {
            const results = document.getElementById('results-section');
            if (!results || window.getComputedStyle(results).display === 'none') return false;

            const qs = (s) => document.querySelector(s) !== null;
            const slope = Array.from(document.querySelectorAll('#map img'))
              .some((img) => img.src && img.src.startsWith('data:image/png'));
            const track = qs('#map canvas');
            const elev = qs('#elevation-chart .plot-container');
            const hist = qs('#histogram-chart .plot-container');
            const score = qs('.score-total');

            return slope && track && elev && hist && score;
        }""",
        timeout=timeout_ms,
    )


def _intercept_cdn(route):
    """Serve CDN scripts from local files so tests work offline."""
    url = route.request.url
    if "plotly" in url and url.endswith(".js"):
        route.fulfill(path=str(PLOTLY_JS), content_type="application/javascript")
    elif "leaflet" in url and url.endswith(".js"):
        route.fulfill(path=str(LEAFLET_JS), content_type="application/javascript")
    elif "leaflet" in url and url.endswith(".css"):
        route.fulfill(path=str(LEAFLET_CSS), content_type="text/css")
    else:
        route.continue_()


@pytest.fixture(scope="module")
def rendered_page():
    """Build debug report, serve via local HTTP, render in Playwright.

    Uses a lightweight stdlib HTTPServer (not pytest-flask's live_server)
    because live_server runs in a separate process and can't share
    in-memory state with the test.
    """
    client = app.test_client()

    resp = client.get("/")
    assert resp.status_code == 200
    template_html = resp.data.decode()

    with GPX_FILE.open("rb") as f:
        resp = client.post(
            "/api/analyze",
            data={"gpx_file": (f, "Twin_Lakes.gpx")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200, f"Analysis failed: {resp.data.decode()}"
    data = resp.get_json()
    assert data is not None

    html = build_embedded_report_html(
        template_html=template_html,
        data=data,
        filename="Twin_Lakes.gpx",
        hide_upload_section=True,
        hide_new_upload_button=False,
    )
    # Strip SRI integrity attributes so locally-served CDN scripts
    # aren't blocked by hash mismatches.
    import re
    html = re.sub(r'\s+integrity="[^"]*"', "", html)

    # Write to temp dir and serve over HTTP.
    import tempfile

    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", prefix="debug_"
    )
    tmp.write(html)
    tmp.close()
    tmp_path = Path(tmp.name)

    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path.parent))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})

        # Intercept CDN requests to serve local copies (no internet needed)
        page.route("**/cdn.plot.ly/**", _intercept_cdn)
        page.route("**/unpkg.com/leaflet**", _intercept_cdn)

        page.goto(
            f"http://127.0.0.1:{port}/{tmp_path.name}",
            wait_until="domcontentloaded",
        )
        _wait_for_report_render(page)

        yield page

        browser.close()

    server.shutdown()
    tmp_path.unlink(missing_ok=True)


def test_results_section_visible(rendered_page):
    """The results section should be displayed (display != 'none')."""
    page = rendered_page
    results = page.query_selector("#results-section")
    assert results is not None, "Results section element not found"
    display = page.evaluate(
        "window.getComputedStyle(document.getElementById('results-section')).display"
    )
    assert display != "none", f"Results section display is '{display}', expected not 'none'"


def test_upload_section_hidden(rendered_page):
    """The upload section should be hidden after results render."""
    page = rendered_page
    display = page.evaluate(
        "window.getComputedStyle(document.getElementById('upload-section')).display"
    )
    assert display == "none", f"Upload section display is '{display}', expected 'none'"


def test_map_has_slope_overlay(rendered_page):
    """The map should have a slope grid image overlay and a track canvas."""
    page = rendered_page

    has_slope_img = page.evaluate("""() => {
        const imgs = document.querySelectorAll('#map img');
        for (const img of imgs) {
            if (img.src && img.src.startsWith('data:image/png')) return true;
        }
        return false;
    }""")
    assert has_slope_img, "No slope grid image overlay found in the map"

    canvases = page.query_selector_all("#map canvas")
    assert len(canvases) > 0, "No canvas elements found in the map (track layer)"


def test_chart_containers_have_children(rendered_page):
    """All chart containers should have children (Plotly rendered)."""
    page = rendered_page
    chart_ids = ["elevation-chart", "slopes-chart", "violin-chart", "histogram-chart"]
    for chart_id in chart_ids:
        el = page.query_selector(f"#{chart_id}")
        assert el is not None, f"Chart container #{chart_id} not found"
        child_count = page.evaluate(
            f"document.getElementById('{chart_id}').children.length"
        )
        assert child_count > 0, (
            f"Chart #{chart_id} has no children -- Plotly did not render"
        )


def test_score_total_has_number(rendered_page):
    """The score total text should be present and contain a number."""
    page = rendered_page
    score_total = page.query_selector(".score-total")
    assert score_total is not None, "Element with class 'score-total' not found"
    text = score_total.inner_text()
    digits = "".join(c for c in text if c.isdigit())
    assert len(digits) > 0, f"Score total text '{text}' does not contain a number"


def test_stats_table_has_gps_points(rendered_page):
    """The stats table should have a 'GPS points' label."""
    page = rendered_page
    stats_panel = page.query_selector("#stats-panel")
    assert stats_panel is not None, "Stats panel not found"
    text = stats_panel.inner_text()
    assert "GPS points" in text, (
        f"Stats table does not contain 'GPS points'. Content: {text[:200]}"
    )


def test_elevation_chart_colored_with_dense_track(rendered_page):
    """Regression: dense tracks (2000+ pts) must show colored lines, not gray.

    Before the fix, tracks with many points produced sub-pixel SVG segments
    that rendered as all-gray (#ccc background showing through). The
    downsampling + segment merging fix ensures the line is visibly colored.

    This test injects a 5000-point synthetic track, renders the chart, takes
    a screenshot, and verifies that the line area contains non-gray colored
    pixels (green, orange, red, black from the slope colormap).
    """
    page = rendered_page

    # Inject a synthetic 5000-point track and re-render the elevation chart.
    result = page.evaluate("""async () => {
        const N = 5000;
        const track = [];
        for (let i = 0; i < N; i++) {
            const t = i / N;
            track.push({
                lat: 41.0 + t * 0.01,
                lon: -122.0 + t * 0.01,
                elevation: 2000 + 1500 * Math.sin(t * Math.PI),
                distance: i * 3.2,  // ~16km total
                track_slope: 5 + 15 * Math.sin(t * Math.PI * 4),
                ground_slope: 10,
                ground_aspect: 180,
            });
        }
        try {
            renderElevationChart(track);
        } catch (e) {
            return { error: e.message };
        }

        // Render chart to image and sample pixel colors along the line.
        const chartEl = document.getElementById('elevation-chart');
        const dataUrl = await Plotly.toImage(chartEl, {format: 'png', width: 500, height: 200});
        const img = new Image();
        await new Promise(resolve => { img.onload = resolve; img.src = dataUrl; });

        const canvas = document.createElement('canvas');
        canvas.width = 500; canvas.height = 200;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);

        let grayPx = 0, coloredPx = 0;
        for (let x = 50; x < 450; x += 2) {
            for (let y = 30; y < 170; y += 2) {
                const [r, g, b, a] = ctx.getImageData(x, y, 1, 1).data;
                if (a < 128) continue;
                if (r > 240 && g > 240 && b > 240) continue;  // white bg
                const avg = (r + g + b) / 3;
                const maxDev = Math.max(
                    Math.abs(r - avg), Math.abs(g - avg), Math.abs(b - avg)
                );
                if (maxDev < 15 && avg > 100 && avg < 230) {
                    grayPx++;
                } else if (r + g + b < 700) {
                    coloredPx++;
                }
            }
        }
        return { grayPx, coloredPx };
    }""")

    assert "error" not in result, f"renderElevationChart failed: {result['error']}"
    colored = result["coloredPx"]
    gray = result["grayPx"]
    # The colored pixels should outnumber gray pixels along the line.
    # Before the fix, the line was almost entirely gray (#ccc).
    assert colored > gray, (
        f"Elevation chart line is mostly gray ({gray} gray vs {colored} colored px) -- "
        "dense track slope colors are not rendering visibly"
    )


def test_slope_overlay_survives_viewport_resize(rendered_page):
    """Regression: slope shading must remain visible after viewport resize."""
    page = rendered_page

    baseline = page.evaluate("""() => {
        const imgs = document.querySelectorAll('#map img');
        for (const img of imgs) {
            if (img.src && img.src.startsWith('data:image/png')) {
                const rect = img.getBoundingClientRect();
                return { found: true, width: rect.width, height: rect.height };
            }
        }
        return { found: false };
    }""")
    assert baseline["found"], "Slope grid image overlay not found at baseline"
    assert baseline["width"] > 100, f"Slope overlay too narrow: {baseline['width']}px"

    page.set_viewport_size({"width": 800, "height": 600})
    page.wait_for_function("""() => {
        const imgs = document.querySelectorAll('#map img');
        for (const img of imgs) {
            if (img.src && img.src.startsWith('data:image/png')) {
                const rect = img.getBoundingClientRect();
                return rect.width > 100 && rect.height > 100;
            }
        }
        return false;
    }""")

    after = page.evaluate("""() => {
        const imgs = document.querySelectorAll('#map img');
        for (const img of imgs) {
            if (img.src && img.src.startsWith('data:image/png')) {
                const rect = img.getBoundingClientRect();
                return { found: true, width: rect.width, height: rect.height };
            }
        }
        return { found: false };
    }""")
    assert after["found"], "Slope grid image overlay disappeared after resize"
    assert after["width"] > 100, (
        f"Slope overlay too narrow after resize: {after['width']}px"
    )
