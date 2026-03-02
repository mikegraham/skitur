"""Visual regression tests using Playwright.

Generates a debug.html by fetching the template from the Flask app,
uploading Twin_Lakes.gpx for analysis, and injecting the JSON response
into the template. Then loads it in headless Chromium and verifies that
all visual elements render correctly.
"""

import json
import tempfile
import time
from io import BytesIO
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from skitur.web import app

GPX_FILE = Path(__file__).parent.parent / "Twin_Lakes.gpx"


@pytest.fixture(scope="module")
def debug_html_path():
    """Build a self-contained debug.html from the Flask app.

    1. GET / to fetch the template HTML.
    2. POST /api/analyze with Twin_Lakes.gpx to get the analysis JSON.
    3. Inject a <script> that calls renderResults(data, "Twin_Lakes.gpx")
       into the template, just before </body>.
    4. Write the result to a temp file and return its path.
    """
    client = app.test_client()

    # Fetch the template
    resp = client.get("/")
    assert resp.status_code == 200
    template_html = resp.data.decode()

    # Upload the GPX file for analysis
    with open(GPX_FILE, "rb") as f:
        resp = client.post(
            "/api/analyze",
            data={"gpx_file": (f, "Twin_Lakes.gpx")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200, f"Analysis failed: {resp.data.decode()}"
    data = json.loads(resp.data)

    # Inject auto-render script before </body>
    inject_script = (
        "<script>\n"
        "document.addEventListener('DOMContentLoaded', function() {\n"
        "    const data = " + json.dumps(data) + ";\n"
        '    renderResults(data, "Twin_Lakes.gpx");\n'
        "});\n"
        "</script>"
    )
    html = template_html.replace("</body>", inject_script + "\n</body>")

    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", prefix="debug_"
    )
    tmp.write(html)
    tmp.close()

    yield Path(tmp.name)

    Path(tmp.name).unlink(missing_ok=True)


@pytest.fixture(scope="module")
def rendered_page(debug_html_path):
    """Launch headless Chromium, load the debug.html, wait for rendering."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.goto(f"file://{debug_html_path}")

        # Wait for rendering to complete
        time.sleep(4)

        yield page

        browser.close()


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


def test_map_canvas_has_pixels(rendered_page):
    """The map canvas should have non-zero pixels in its center (slope grid rendering)."""
    page = rendered_page
    canvases = page.query_selector_all("#map canvas")
    assert len(canvases) > 0, "No canvas elements found in the map"

    # Check that at least one canvas has non-zero pixel data at its center
    has_pixels = page.evaluate("""() => {
        const canvases = document.querySelectorAll('#map canvas');
        for (const canvas of canvases) {
            const ctx = canvas.getContext('2d');
            if (!ctx) continue;
            const cx = Math.floor(canvas.width / 2);
            const cy = Math.floor(canvas.height / 2);
            // Sample a 10x10 region around center
            const imgData = ctx.getImageData(cx - 5, cy - 5, 10, 10);
            const pixels = imgData.data;
            for (let i = 0; i < pixels.length; i += 4) {
                // Check if any pixel has non-zero RGB (alpha can be 0 for transparent)
                if (pixels[i] > 0 || pixels[i+1] > 0 || pixels[i+2] > 0) {
                    return true;
                }
            }
        }
        return false;
    }""")
    assert has_pixels, "No non-zero pixels found in center of any map canvas"


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
            f"Chart #{chart_id} has no children — Plotly did not render"
        )


def test_score_total_has_number(rendered_page):
    """The score total text should be present and contain a number."""
    page = rendered_page
    score_total = page.query_selector(".score-total")
    assert score_total is not None, "Element with class 'score-total' not found"
    text = score_total.inner_text()
    # Extract digits from the text (e.g. "72/100" -> "72")
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


def test_full_page_screenshot(rendered_page):
    """Take a full-page screenshot and save to /tmp/test_visual_screenshot.png."""
    page = rendered_page
    page.screenshot(path="/tmp/test_visual_screenshot.png", full_page=True)
    screenshot_path = Path("/tmp/test_visual_screenshot.png")
    assert screenshot_path.exists(), "Screenshot file was not created"
    assert screenshot_path.stat().st_size > 0, "Screenshot file is empty"


def test_slope_canvas_fills_after_dpr_change(rendered_page):
    """Regression: slope shading must fully cover the map after a DPR change.

    When the browser zoom changes (Ctrl+/-), devicePixelRatio changes and the
    canvas layers must re-render at the correct resolution. A previous bug
    caused the slope grid to render as horizontal stripes ("thin lines")
    because the canvas backing store dimensions didn't match the viewport
    after a DPR change.
    """
    page = rendered_page

    # Verify canvas is fully filled at baseline
    baseline_fill = page.evaluate("""() => {
        const canvas = document.querySelector('.slope-grid-canvas');
        if (!canvas) return { error: 'no canvas' };
        const ctx = canvas.getContext('2d');
        const w = canvas.width, h = canvas.height;
        let filled = 0, total = 0;
        for (let y = 0; y < h; y += 10) {
            for (let x = 0; x < w; x += 10) {
                const p = ctx.getImageData(x, y, 1, 1).data;
                total++;
                if (p[3] > 0) filled++;
            }
        }
        return { filled, total, pct: (filled / total * 100) };
    }""")
    assert baseline_fill["pct"] > 95, (
        f"Baseline fill rate too low: {baseline_fill['pct']:.1f}%"
    )

    # Simulate DPR change (as browser zoom would)
    page.evaluate("""() => {
        Object.defineProperty(window, 'devicePixelRatio', {
            get: () => 1.5,
            configurable: true,
        });
    }""")

    # Resize viewport to simulate CSS pixel shrinkage from zoom
    page.set_viewport_size({"width": 800, "height": 600})

    # Wait for DPR polling (200ms) + rAF + render time
    time.sleep(1)

    # Verify canvas is still fully filled after DPR change
    after_fill = page.evaluate("""() => {
        const canvas = document.querySelector('.slope-grid-canvas');
        if (!canvas) return { error: 'no canvas' };
        const ctx = canvas.getContext('2d');
        const w = canvas.width, h = canvas.height;
        let filled = 0, total = 0;
        for (let y = 0; y < h; y += 10) {
            for (let x = 0; x < w; x += 10) {
                const p = ctx.getImageData(x, y, 1, 1).data;
                total++;
                if (p[3] > 0) filled++;
            }
        }
        return { filled, total, pct: (filled / total * 100), w, h };
    }""")
    assert after_fill["pct"] > 95, (
        f"After DPR change, fill rate dropped to {after_fill['pct']:.1f}% "
        f"(canvas {after_fill['w']}x{after_fill['h']}). "
        f"Expected >95% — slope shading has gaps/stripes."
    )

    # Verify canvas backing store matches expected DPR scaling
    canvas_dims = page.evaluate("""() => {
        const canvas = document.querySelector('.slope-grid-canvas');
        const mapEl = document.getElementById('map');
        const rect = mapEl.getBoundingClientRect();
        return {
            canvasW: canvas.width,
            canvasH: canvas.height,
            cssW: parseFloat(canvas.style.width),
            cssH: parseFloat(canvas.style.height),
            mapW: rect.width,
            mapH: rect.height,
            dpr: window.devicePixelRatio,
        };
    }""")
    expected_backing = round(canvas_dims["cssW"] * canvas_dims["dpr"])
    assert abs(canvas_dims["canvasW"] - expected_backing) <= 1, (
        f"Canvas backing store {canvas_dims['canvasW']} doesn't match "
        f"CSS width {canvas_dims['cssW']} * DPR {canvas_dims['dpr']} "
        f"= {expected_backing}"
    )
