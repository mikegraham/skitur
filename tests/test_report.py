from __future__ import annotations

from skitur.report import build_embedded_report_html, generate_report


def _minimal_payload() -> dict:
    return {
        "track": [],
        "stats": {},
        "contours": {"minor": [], "major": [], "minor_step_ft": None, "major_step_ft": None},
        "score": {},
        "slope_grid": {
            "data": [],
            "rows": 0,
            "cols": 0,
            "lat_min": 0.0,
            "lat_max": 0.0,
            "lon_min": 0.0,
            "lon_max": 0.0,
        },
    }


def test_generate_report_omits_upload_ui(monkeypatch, tmp_path):
    monkeypatch.setattr("skitur.report.build_analysis_payload", lambda _: _minimal_payload())

    gpx_path = tmp_path / "route.gpx"
    gpx_path.write_text("ignored", encoding="utf-8")
    output_path = tmp_path / "route_report.html"

    out = generate_report(gpx_path, output_path)
    html = out.read_text(encoding="utf-8")

    assert 'id="upload-section"' not in html
    assert 'id="new-upload-btn"' not in html
    assert "Analyze Another" not in html


def test_build_embedded_report_hide_flags_are_null_safe():
    template = "<html><body><div id='results-section'></div></body></html>"

    html = build_embedded_report_html(
        template_html=template,
        data=_minimal_payload(),
        filename="route.gpx",
        hide_upload_section=True,
        hide_new_upload_button=True,
    )

    assert "if (uploadSectionEl)" in html
    assert "if (newUploadBtnEl)" in html
