#!/usr/bin/env python3
"""Regenerate README assets: report screenshots and scoring curve SVGs.

Usage:
  python3 scripts/update_docs.py                     # regenerate everything
  python3 scripts/update_docs.py screenshots          # report screenshots only
  python3 scripts/update_docs.py curves               # scoring curve SVGs only
  python3 scripts/update_docs.py screenshots --watch   # re-capture on file change
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

DOCS_DIR = Path("docs")
SCREENSHOT_DIR = DOCS_DIR / "readme_screenshots"
DEG = "\N{DEGREE SIGN}"


# ===========================================================================
# Screenshots (Playwright)
# ===========================================================================


@dataclass(frozen=True)
class _Shot:
    name: str
    selector: str | None  # None => full page screenshot


_SHOTS: tuple[_Shot, ...] = (
    _Shot("01_full_report.png", "#results-section"),
    _Shot("02_score_panel.png", "#score-panel"),
    _Shot("03_stats_panel.png", "#stats-panel"),
    _Shot("04_map_row.png", "#map-row"),
    _Shot("05_map.png", "#map"),
    _Shot("06_ground_legend.png", "#ground-legend"),
    _Shot("07_track_legend.png", "#track-legend"),
    _Shot("08_elevation_profile.png", "#elevation-chart"),
    _Shot("09_slope_profile.png", "#slopes-chart"),
    _Shot("10_track_slope_distribution.png", "#violin-chart"),
    _Shot("11_ground_angle_distribution.png", "#histogram-chart"),
    _Shot("12_slope_by_aspect.png", "#aspect-rose-chart"),
    _Shot("13_fraction_on_aspect.png", "#aspect-dist-chart"),
)


def _as_uri(page_arg: str, cwd: Path) -> str:
    if page_arg.startswith(("http://", "https://", "file://")):
        return page_arg
    return (cwd / page_arg).resolve().as_uri()


def _prepare_page(page) -> None:
    page.evaluate("""() => {
        const results = document.getElementById('results-section');
        if (results) results.style.display = 'block';
        const upload = document.getElementById('upload-section');
        if (upload) upload.style.display = 'none';
    }""")


def _trim_white_margin(img, *, threshold=245, min_non_bg=8, pad=4):
    rgb = img.convert("RGB")
    px = rgb.load()
    if px is None:
        return rgb
    w, h = rgb.size
    non_bg_cols = [0] * w
    non_bg_rows = [0] * h
    for y in range(h):
        row_count = 0
        for x in range(w):
            pixel = px[x, y]
            if not isinstance(pixel, tuple) or len(pixel) < 3:
                continue
            if int(pixel[0]) < threshold or int(pixel[1]) < threshold or int(pixel[2]) < threshold:
                non_bg_cols[x] += 1
                row_count += 1
        non_bg_rows[y] = row_count
    left = next((i for i, c in enumerate(non_bg_cols) if c >= min_non_bg), None)
    right = next((i for i in range(w - 1, -1, -1) if non_bg_cols[i] >= min_non_bg), None)
    top = next((i for i, c in enumerate(non_bg_rows) if c >= min_non_bg), None)
    bottom = next((i for i in range(h - 1, -1, -1) if non_bg_rows[i] >= min_non_bg), None)
    if left is None or right is None or top is None or bottom is None:
        return rgb
    return rgb.crop((max(0, left - pad), max(0, top - pad),
                     min(w - 1, right + pad) + 1, min(h - 1, bottom + pad) + 1))


def _compose_aspect_pair(out_dir: Path) -> None:
    from PIL import Image
    left_path = out_dir / "12_slope_by_aspect.png"
    right_path = out_dir / "13_fraction_on_aspect.png"
    if not left_path.exists() or not right_path.exists():
        return
    trim_kw = dict(threshold=245, min_non_bg=10, pad=2)
    left = _trim_white_margin(Image.open(left_path), **trim_kw)
    right = _trim_white_margin(Image.open(right_path), **trim_kw)
    gap = 4
    h = max(left.height, right.height)
    merged = Image.new("RGB", (left.width + gap + right.width, h), (255, 255, 255))
    merged.paste(left, (0, (h - left.height) // 2))
    merged.paste(right, (left.width + gap, (h - right.height) // 2))
    out_path = out_dir / "12_aspect_pair.png"
    merged.save(out_path)
    print(f"wrote {out_path}")


def capture_screenshots(page_uri: str, out_dir: Path, width: int = 1700, height: int = 1200) -> None:
    from playwright.sync_api import sync_playwright
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(page_uri)
        page.wait_for_timeout(1800)
        _prepare_page(page)
        page.wait_for_timeout(1200)
        for selector in ("#score-panel", "#map", "#elevation-chart", "#aspect-rose-chart"):
            try:
                page.wait_for_selector(selector, timeout=12000)
            except Exception:
                pass
        page.wait_for_timeout(900)
        for shot in _SHOTS:
            out_path = out_dir / shot.name
            if shot.selector is None:
                page.screenshot(path=str(out_path), full_page=True)
            else:
                locator = page.locator(shot.selector).first
                try:
                    locator.scroll_into_view_if_needed(timeout=8000)
                    page.wait_for_timeout(180)
                    locator.screenshot(path=str(out_path))
                except Exception as exc:
                    print(f"[warn] could not capture {shot.name} ({shot.selector}): {exc}")
                    continue
            print(f"wrote {out_path}")
        _compose_aspect_pair(out_dir)
        browser.close()


def _watch_mtime(paths: Iterable[Path]) -> int:
    return max((p.stat().st_mtime_ns for p in paths if p.exists()), default=0)


def watch_screenshots(page_path: Path, out_dir: Path, interval: float = 1.0,
                      width: int = 1700, height: int = 1200) -> None:
    watch_paths = [page_path]
    template = Path("skitur/templates/index.html")
    if template.exists():
        watch_paths.append(template.resolve())
    page_uri = page_path.resolve().as_uri()
    print(f"[watch] monitoring {', '.join(str(p) for p in watch_paths)}")
    last = _watch_mtime(watch_paths)
    while True:
        time.sleep(max(0.2, interval))
        now = _watch_mtime(watch_paths)
        if now <= last:
            continue
        last = now
        try:
            capture_screenshots(page_uri, out_dir, width, height)
        except Exception as exc:
            print(f"[warn] capture failed: {exc}")


# ===========================================================================
# Scoring curve SVGs (matplotlib)
# ===========================================================================


@dataclass(frozen=True)
class _InsetSpec:
    title: str
    fn: Callable[[float], float]
    ground_slope: float


@dataclass(frozen=True)
class _AnnotationSpec:
    text: str
    x: float
    y: float
    target_x: float | None = None
    target_y: float | None = None


@dataclass(frozen=True)
class _Panel:
    file_name: str
    title: str
    x_label: str
    fn: Callable[[float], float]
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    x_ticks: tuple[float, ...]
    y_ticks: tuple[float, ...]
    line_cmap: str  # "track" | "ground"
    top_percent_axis: bool = False
    top_percent_step: int = 10
    highlight_min: float | None = None
    highlight_max: float | None = None
    annotations: tuple[_AnnotationSpec, ...] = ()
    insets: tuple[_InsetSpec, ...] = ()


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _smoothstep(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _track_cmap_rgb01(slope_deg: float) -> tuple[float, float, float]:
    anchors = (
        (0.0 / 20.0, (0.15, 0.75, 0.15)),
        (2.0 / 20.0, (0.15, 0.75, 0.15)),
        (4.5 / 20.0, (1.00, 0.92, 0.00)),
        (6.0 / 20.0, (1.00, 0.65, 0.00)),
        (8.0 / 20.0, (1.00, 0.35, 0.00)),
        (11.0 / 20.0, (0.85, 0.08, 0.00)),
        (13.0 / 20.0, (0.65, 0.00, 0.00)),
        (15.0 / 20.0, (0.35, 0.00, 0.00)),
        (1.0, (0.00, 0.00, 0.00)),
    )
    t = min(max(abs(slope_deg), 0.0), 20.0) / 20.0
    i = 0
    while i < len(anchors) - 1 and anchors[i + 1][0] <= t:
        i += 1
    if i >= len(anchors) - 1:
        return anchors[-1][1]
    pos0, c0 = anchors[i]
    pos1, c1 = anchors[i + 1]
    f = (t - pos0) / (pos1 - pos0)
    return (_lerp(c0[0], c1[0], f), _lerp(c0[1], c1[1], f), _lerp(c0[2], c1[2], f))


def _ground_cmap_rgb01(slope_deg: float) -> tuple[float, float, float]:
    slope = min(max(slope_deg, 0.0), 47.0)
    if slope < 30.0:
        t = slope / 30.0
        if t < 0.45:
            s = _smoothstep(t / 0.45)
            r, g, b = _lerp(0.62, 0.68, s), _lerp(0.74, 0.73, s), _lerp(0.58, 0.56, s)
        elif t < 0.70:
            s = _smoothstep((t - 0.45) / 0.25)
            r, g, b = _lerp(0.68, 0.77, s), _lerp(0.73, 0.76, s), _lerp(0.56, 0.54, s)
        else:
            s = _smoothstep((t - 0.70) / 0.30)
            r, g, b = _lerp(0.77, 0.88, s), _lerp(0.76, 0.86, s), _lerp(0.54, 0.43, s)
    elif slope < 45.0:
        if slope < 34.0:
            s = (slope - 30.0) / 4.0
            r, g, b = 0.95, 0.55 - 0.05 * s, 0.05
        elif slope < 37.0:
            s = (slope - 34.0) / 3.0
            r, g, b = 0.95 - 0.10 * s, 0.50 - 0.25 * s, 0.05
        else:
            s = (slope - 37.0) / 8.0
            r, g, b = 0.85 - 0.30 * s, 0.25 - 0.15 * s, 0.05 + 0.01 * s
    else:
        r, g, b = 0.5, 0.5, 0.5
    return (r, g, b)


def _line_color(cmap_name: str, x_value: float) -> tuple[float, float, float]:
    if cmap_name == "track":
        return _track_cmap_rgb01(x_value)
    if cmap_name == "ground":
        return _ground_cmap_rgb01(x_value)
    raise ValueError(f"Unsupported line_cmap: {cmap_name}")


def _add_colored_curve(ax, *, x, y, cmap_name, lw=4.0, linestyle="solid",
                       alpha=1.0, zorder=3.0, dash_pattern=None) -> None:
    from matplotlib.collections import LineCollection
    pts = np.column_stack([x, y])
    segs_arr = np.stack([pts[:-1], pts[1:]], axis=1)
    segments = [((float(s[0, 0]), float(s[0, 1])), (float(s[1, 0]), float(s[1, 1])))
                for s in segs_arr]
    mids = 0.5 * (x[:-1] + x[1:])
    if dash_pattern is not None:
        on, off = dash_pattern
        period = max(1, on + off)
        idx = np.arange(len(segments))
        keep = (idx % period) < max(1, on)
        segments = [seg for seg, k in zip(segments, keep, strict=True) if k]
        mids = mids[keep]
    colors = [(*_line_color(cmap_name, float(v)), alpha) for v in mids]
    lc = LineCollection(segments, colors=colors, linewidths=lw, linestyles=linestyle,
                        capstyle="round", joinstyle="round", zorder=zorder)
    ax.add_collection(lc)


def _configure_axes(ax, spec: _Panel) -> None:
    ax.set_facecolor("white")
    ax.set_xlim(spec.x_min, spec.x_max)
    ax.set_ylim(spec.y_min, spec.y_max)
    ax.set_xticks(spec.x_ticks)
    ax.set_yticks(spec.y_ticks)
    ax.set_xticklabels([f"{_fmt(x)}{DEG}" for x in spec.x_ticks])
    ax.grid(True, color="#e2e8f0", linewidth=1)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=11, colors="#475569")
    ax.set_xlabel(spec.x_label, fontsize=13, color="#1f2a44", labelpad=8)
    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")
        spine.set_linewidth(1.1)
    if spec.y_min <= 0 <= spec.y_max:
        ax.axhline(0, color="#94a3b8", linewidth=1.6)
    if spec.y_min <= 100 <= spec.y_max:
        ax.axhline(100, color="#15803d", linewidth=1.8, linestyle=(0, (6, 5)))
    if spec.highlight_min is not None and spec.highlight_max is not None:
        ax.axvspan(spec.highlight_min, spec.highlight_max, color="#f59e0b", alpha=0.12)
    if spec.top_percent_axis:
        sec = ax.secondary_xaxis("top", functions=(
            lambda deg: np.tan(np.radians(deg)) * 100.0,
            lambda pct: np.degrees(np.arctan(np.asarray(pct) / 100.0)),
        ))
        max_pct = math.tan(math.radians(spec.x_max)) * 100.0
        top_max = int(max_pct // spec.top_percent_step) * spec.top_percent_step + 1
        top_ticks = np.arange(0, top_max, spec.top_percent_step)
        sec.set_xticks(top_ticks)
        sec.set_xticklabels([f"{int(t)}%" for t in top_ticks])
        sec.tick_params(axis="x", labelsize=11, colors="#475569", pad=3)


def _draw_annotations(ax, spec: _Panel) -> None:
    for ann in spec.annotations:
        if ann.target_x is None:
            ax.text(ann.x, ann.y, ann.text, fontsize=11, color="#334155",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor=(1, 1, 1, 0.42), edgecolor="none"))
            continue
        target_y = ann.target_y if ann.target_y is not None else spec.fn(ann.target_x)
        ax.annotate(ann.text, xy=(ann.target_x, target_y), xytext=(ann.x, ann.y),
                    fontsize=11, color="#334155",
                    arrowprops=dict(arrowstyle="-|>", color="#334155", lw=1.3))


def _draw_inset(inset_ax, inset: _InsetSpec) -> None:
    from skitur.score import _apply_ground_slope_multiplier, _ground_slope_penalty
    x = np.linspace(0.0, 30.0, 300)
    base = np.array([inset.fn(float(v)) for v in x])
    mult = _ground_slope_penalty(inset.ground_slope)
    adjusted = np.array([_apply_ground_slope_multiplier(inset.fn(float(v)), mult) for v in x])
    inset_ax.set_facecolor((1, 1, 1, 0.96))
    inset_ax.set_xlim(0, 30)
    inset_ax.set_ylim(-60, 130)
    inset_ax.axhline(0, color="#cbd5e1", linewidth=0.8)
    inset_ax.axhline(100, color="#16a34a", linewidth=0.9, linestyle=(0, (4, 4)))
    _add_colored_curve(inset_ax, x=x, y=base, cmap_name="track",
                       lw=1.4, alpha=0.30, zorder=2.0, dash_pattern=(6, 5))
    _add_colored_curve(inset_ax, x=x, y=adjusted, cmap_name="track",
                       lw=2.2, alpha=0.95, zorder=3.0)
    sample_x = 14.0
    sample_base = inset.fn(sample_x)
    sample_adjusted = _apply_ground_slope_multiplier(sample_base, mult)
    inset_ax.annotate("", xy=(sample_x, sample_adjusted), xytext=(sample_x, sample_base),
                      arrowprops=dict(arrowstyle="-|>", color="#334155", lw=1.1))
    inset_ax.text(0.03, 1.03, inset.title, transform=inset_ax.transAxes, va="bottom",
                  ha="left", fontsize=10, color="#1f2a44", weight="semibold", clip_on=False)
    inset_ax.text(0.03, 0.03, f"multiplier={_fmt(mult)} at {int(inset.ground_slope)}{DEG} ground",
                  transform=inset_ax.transAxes, va="bottom", ha="left", fontsize=9, color="#475569")
    inset_ax.set_xticks([])
    inset_ax.set_yticks([])
    for spine in inset_ax.spines.values():
        spine.set_color("#cbd5e1")
        spine.set_linewidth(1.0)


def _draw_steepness_insets(ax, spec: _Panel) -> None:
    from matplotlib.patches import ConnectionPatch
    left = ax.inset_axes((0.06, 0.03, 0.40, 0.50))
    right = ax.inset_axes((0.63, 0.55, 0.38, 0.46))
    _draw_inset(left, spec.insets[0])
    _draw_inset(right, spec.insets[1])
    source_x = spec.insets[0].ground_slope
    source_y = spec.fn(source_x)
    ax.scatter([source_x], [source_y], s=34, color="#1f2937",
               edgecolors="white", linewidths=0.9, zorder=6)
    for target_axes, target_xy, rad in [
        (left, (0.98, 0.70), 0.12),
        (right, (0.02, 0.06), -0.10),
    ]:
        conn = ConnectionPatch(
            xyA=(source_x, source_y), coordsA=ax.transData,
            xyB=target_xy, coordsB=target_axes.transAxes,
            arrowstyle="-", linewidth=1.2, linestyle=(0, (3, 3)),
            connectionstyle=f"arc3,rad={rad}", color="#64748b",
        )
        ax.add_artist(conn)


def _build_panel(spec: _Panel, out_path: Path) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9.8, 4.6), dpi=100)
    fig.patch.set_facecolor("#f8fafc")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.17, top=0.88)
    _configure_axes(ax, spec)
    x = np.linspace(spec.x_min, spec.x_max, 500)
    y = np.array([spec.fn(float(v)) for v in x])
    _add_colored_curve(ax, x=x, y=y, cmap_name=spec.line_cmap, lw=4.0)
    _draw_annotations(ax, spec)
    if spec.insets:
        _draw_steepness_insets(ax, spec)
    manager = fig.canvas.manager
    if manager is not None:
        manager.set_window_title(spec.title)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="svg", facecolor=fig.get_facecolor())
    plt.close(fig)


def _get_panels() -> tuple[_Panel, ...]:
    """Build panel specs. Deferred so skitur.score is only imported when needed."""
    from skitur.score import (
        STANDING_AVY_MAX_DEG, STANDING_AVY_MIN_DEG,
        _avy_slope_penalty, _downhill_segment_score,
        _ground_slope_penalty, _uphill_segment_score,
    )
    return (
        _Panel(
            file_name="rating_curve_downhill_fun.svg",
            title='Downhill track angle "fun" rating',
            x_label="Track slope",
            fn=_downhill_segment_score,
            x_min=0, x_max=30, y_min=-60, y_max=130,
            x_ticks=(0, 5, 10, 15, 20, 25, 30),
            y_ticks=(-50, 0, 50, 100),
            line_cmap="track",
            top_percent_axis=True,
            annotations=(_AnnotationSpec("Getting too thrilling", 18.0, 84.0, target_x=16.0),),
        ),
        _Panel(
            file_name="rating_curve_uphill_doability.svg",
            title='Uphill track angle "doability" rating',
            x_label="Track slope",
            fn=_uphill_segment_score,
            x_min=0, x_max=30, y_min=-60, y_max=130,
            x_ticks=(0, 5, 10, 15, 20, 25, 30),
            y_ticks=(-50, 0, 50, 100),
            line_cmap="track",
            top_percent_axis=True,
        ),
        _Panel(
            file_name="rating_curve_ground_slope_penalty.svg",
            title="Ground slope penalty curve",
            x_label="Ground slope angle",
            fn=_avy_slope_penalty,
            x_min=0, x_max=60, y_min=0, y_max=1.05,
            x_ticks=(0, 10, 20, 30, 40, 50, 60),
            y_ticks=(0, 0.25, 0.5, 0.75, 1.0),
            line_cmap="ground",
            highlight_min=STANDING_AVY_MIN_DEG,
            highlight_max=STANDING_AVY_MAX_DEG,
        ),
        _Panel(
            file_name="rating_curve_ground_steepness_multiplier.svg",
            title='Ground steepness multiplier (AKA sidehilling is tough)',
            x_label="Ground slope angle",
            fn=_ground_slope_penalty,
            x_min=0, x_max=55, y_min=0, y_max=1.05,
            x_ticks=(0, 10, 20, 30, 40, 50),
            y_ticks=(0, 0.25, 0.5, 0.75, 1.0),
            line_cmap="ground",
            highlight_min=20, highlight_max=40,
            annotations=(_AnnotationSpec("No adjustment needed on fairly normal ground slopes.", 2.2, 0.91),),
            insets=(
                _InsetSpec("Downhill rating at 35\N{DEGREE SIGN} ground", _downhill_segment_score, 35.0),
                _InsetSpec("Uphill rating at 35\N{DEGREE SIGN} ground", _uphill_segment_score, 35.0),
            ),
        ),
    )


def generate_curves(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for panel in _get_panels():
        out_path = out_dir / panel.file_name
        _build_panel(panel, out_path)
        print(f"wrote {out_path}")


# ===========================================================================
# CLI
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    ss = sub.add_parser("screenshots", help="Capture report screenshots")
    ss.add_argument("--page", default="debug.html", help="Report page path or URL")
    ss.add_argument("--width", type=int, default=1700)
    ss.add_argument("--height", type=int, default=1200)
    ss.add_argument("--watch", action="store_true", help="Re-capture on file change")

    sub.add_parser("curves", help="Generate scoring curve SVGs")

    args = parser.parse_args()
    cwd = Path.cwd()

    if args.command == "screenshots":
        page_uri = _as_uri(args.page, cwd)
        capture_screenshots(page_uri, SCREENSHOT_DIR, args.width, args.height)
        if args.watch:
            page_path = (cwd / args.page).resolve()
            watch_screenshots(page_path, SCREENSHOT_DIR, width=args.width, height=args.height)
    elif args.command == "curves":
        generate_curves(DOCS_DIR)
    else:
        generate_curves(DOCS_DIR)
        page_uri = _as_uri("debug.html", cwd)
        capture_screenshots(page_uri, SCREENSHOT_DIR)

    return 0


if __name__ == "__main__":
    sys.exit(main())
