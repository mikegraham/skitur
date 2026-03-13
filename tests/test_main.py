"""Tests for the `python -m skitur` entrypoint module."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ENTRYPOINT_PATH = Path(__file__).resolve().parents[1] / "skitur" / "__main__.py"


def test_entrypoint_with_explicit_output(monkeypatch, tmp_path, capsys):
    """Entrypoint should forward explicit -o path to report generation."""
    calls: list[tuple[Path, Path | None]] = []

    def fake_generate_report(gpx_file: Path, output: Path | None, **kwargs) -> Path:
        calls.append((gpx_file, output))
        assert output is not None
        output.write_text("<html>ok</html>", encoding="utf-8")
        return output

    import skitur.report as report_mod

    monkeypatch.setattr(report_mod, "generate_report", fake_generate_report)
    gpx = tmp_path / "route.gpx"
    out = tmp_path / "custom_report.html"
    monkeypatch.setattr(sys, "argv", [str(ENTRYPOINT_PATH), str(gpx), "-o", str(out)])

    runpy.run_path(str(ENTRYPOINT_PATH), run_name="__main__")

    assert calls == [(gpx, out)]
    assert out.exists()
    printed = capsys.readouterr().out
    assert "Generated" in printed
    assert str(out) in printed


def test_entrypoint_default_output(monkeypatch, tmp_path, capsys):
    """Entrypoint should pass None output when -o is omitted."""
    calls: list[tuple[Path, Path | None]] = []

    def fake_generate_report(gpx_file: Path, output: Path | None, **kwargs) -> Path:
        calls.append((gpx_file, output))
        target = gpx_file.with_name(f"{gpx_file.stem}_report.html")
        target.write_text("<html>ok</html>", encoding="utf-8")
        return target

    import skitur.report as report_mod

    monkeypatch.setattr(report_mod, "generate_report", fake_generate_report)
    gpx = tmp_path / "tour_name.gpx"
    monkeypatch.setattr(sys, "argv", [str(ENTRYPOINT_PATH), str(gpx)])

    runpy.run_path(str(ENTRYPOINT_PATH), run_name="__main__")

    assert calls == [(gpx, None)]
    default_out = tmp_path / "tour_name_report.html"
    assert default_out.exists()
    printed = capsys.readouterr().out
    assert "Generated" in printed
    assert str(default_out) in printed
