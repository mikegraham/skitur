#!/usr/bin/env python3
"""Generate rating-curve SVGs with a compact matplotlib-based pipeline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Callable

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import ConnectionPatch
import numpy as np

from skitur.score import (
    STANDING_AVY_MAX_DEG,
    STANDING_AVY_MIN_DEG,
    _apply_ground_slope_multiplier,
    _avy_slope_penalty,
    _downhill_segment_score,
    _ground_slope_penalty,
    _uphill_segment_score,
)

DEG = "\N{DEGREE SIGN}"


@dataclass(frozen=True)
class InsetSpec:
    title: str
    fn: Callable[[float], float]
    ground_slope: float


@dataclass(frozen=True)
class AnnotationSpec:
    text: str
    x: float
    y: float
    target_x: float | None = None
    target_y: float | None = None


@dataclass(frozen=True)
class Panel:
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
    annotations: tuple[AnnotationSpec, ...] = ()
    insets: tuple[InsetSpec, ...] = ()


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
    return (
        _lerp(c0[0], c1[0], f),
        _lerp(c0[1], c1[1], f),
        _lerp(c0[2], c1[2], f),
    )


def _ground_cmap_rgb01(slope_deg: float) -> tuple[float, float, float]:
    slope = min(max(slope_deg, 0.0), 47.0)
    if slope < 30.0:
        t = slope / 30.0
        if t < 0.45:
            s = _smoothstep(t / 0.45)
            r = _lerp(0.62, 0.68, s)
            g = _lerp(0.74, 0.73, s)
            b = _lerp(0.58, 0.56, s)
        elif t < 0.70:
            s = _smoothstep((t - 0.45) / 0.25)
            r = _lerp(0.68, 0.77, s)
            g = _lerp(0.73, 0.76, s)
            b = _lerp(0.56, 0.54, s)
        else:
            s = _smoothstep((t - 0.70) / 0.30)
            r = _lerp(0.77, 0.88, s)
            g = _lerp(0.76, 0.86, s)
            b = _lerp(0.54, 0.43, s)
    elif slope < 45.0:
        if slope < 34.0:
            s = (slope - 30.0) / 4.0
            r = 0.95
            g = 0.55 - 0.05 * s
            b = 0.05
        elif slope < 37.0:
            s = (slope - 34.0) / 3.0
            r = 0.95 - 0.10 * s
            g = 0.50 - 0.25 * s
            b = 0.05
        else:
            s = (slope - 37.0) / 8.0
            r = 0.85 - 0.30 * s
            g = 0.25 - 0.15 * s
            b = 0.05 + 0.01 * s
    else:
        r, g, b = 0.5, 0.5, 0.5
    return (r, g, b)


def _line_color(cmap_name: str, x_value: float) -> tuple[float, float, float]:
    if cmap_name == "track":
        return _track_cmap_rgb01(x_value)
    if cmap_name == "ground":
        return _ground_cmap_rgb01(x_value)
    raise ValueError(f"Unsupported line_cmap: {cmap_name}")


def _add_colored_curve(
    ax: plt.Axes,
    *,
    x: np.ndarray,
    y: np.ndarray,
    cmap_name: str,
    lw: float = 4.0,
    linestyle: str | tuple[int, tuple[int, ...]] = "solid",
    alpha: float = 1.0,
    zorder: float = 3.0,
    dash_pattern: tuple[int, int] | None = None,
) -> None:
    pts = np.column_stack([x, y])
    segs_arr = np.stack([pts[:-1], pts[1:]], axis=1)
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = [
        (
            (float(seg[0, 0]), float(seg[0, 1])),
            (float(seg[1, 0]), float(seg[1, 1])),
        )
        for seg in segs_arr
    ]
    mids = 0.5 * (x[:-1] + x[1:])

    if dash_pattern is not None:
        on, off = dash_pattern
        period = max(1, on + off)
        idx = np.arange(len(segments))
        keep = (idx % period) < max(1, on)
        segments = [segment for segment, keep_seg in zip(segments, keep, strict=True) if keep_seg]
        mids = mids[keep]

    colors = [(*_line_color(cmap_name, float(v)), alpha) for v in mids]
    lc = LineCollection(
        segments,
        colors=colors,
        linewidths=lw,
        linestyles=linestyle,
        capstyle="round",
        joinstyle="round",
        zorder=zorder,
    )
    ax.add_collection(lc)


def _configure_axes(ax: plt.Axes, spec: Panel) -> None:
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
        sec = ax.secondary_xaxis(
            "top",
            functions=(
                lambda deg: np.tan(np.radians(deg)) * 100.0,
                lambda pct: np.degrees(np.arctan(np.asarray(pct) / 100.0)),
            ),
        )
        max_pct = math.tan(math.radians(spec.x_max)) * 100.0
        top_max = int(max_pct // spec.top_percent_step) * spec.top_percent_step + 1
        top_ticks = np.arange(0, top_max, spec.top_percent_step)
        sec.set_xticks(top_ticks)
        sec.set_xticklabels([f"{int(t)}%" for t in top_ticks])
        sec.tick_params(axis="x", labelsize=11, colors="#475569", pad=3)


def _draw_annotations(ax: plt.Axes, spec: Panel) -> None:
    for ann in spec.annotations:
        if ann.target_x is None:
            ax.text(
                ann.x,
                ann.y,
                ann.text,
                fontsize=11,
                color="#334155",
                bbox=dict(
                    boxstyle="round,pad=0.2",
                    facecolor=(1.0, 1.0, 1.0, 0.42),
                    edgecolor="none",
                ),
            )
            continue
        target_y = ann.target_y if ann.target_y is not None else spec.fn(ann.target_x)
        ax.annotate(
            ann.text,
            xy=(ann.target_x, target_y),
            xytext=(ann.x, ann.y),
            fontsize=11,
            color="#334155",
            arrowprops=dict(arrowstyle="-|>", color="#334155", lw=1.3),
        )


def _draw_inset(inset_ax: plt.Axes, inset: InsetSpec) -> None:
    x = np.linspace(0.0, 30.0, 300)
    base = np.array([inset.fn(float(v)) for v in x])
    mult = _ground_slope_penalty(inset.ground_slope)
    adjusted = np.array([_apply_ground_slope_multiplier(inset.fn(float(v)), mult) for v in x])

    inset_ax.set_facecolor((1.0, 1.0, 1.0, 0.96))
    inset_ax.set_xlim(0, 30)
    inset_ax.set_ylim(-60, 130)
    inset_ax.axhline(0, color="#cbd5e1", linewidth=0.8)
    inset_ax.axhline(100, color="#16a34a", linewidth=0.9, linestyle=(0, (4, 4)))
    _add_colored_curve(
        inset_ax,
        x=x,
        y=base,
        cmap_name="track",
        lw=1.4,
        linestyle="solid",
        alpha=0.30,
        zorder=2.0,
        dash_pattern=(6, 5),
    )
    _add_colored_curve(
        inset_ax,
        x=x,
        y=adjusted,
        cmap_name="track",
        lw=2.2,
        alpha=0.95,
        zorder=3.0,
    )

    sample_x = 14.0
    sample_base = inset.fn(sample_x)
    sample_adjusted = _apply_ground_slope_multiplier(sample_base, mult)
    inset_ax.annotate(
        "",
        xy=(sample_x, sample_adjusted),
        xytext=(sample_x, sample_base),
        arrowprops=dict(arrowstyle="-|>", color="#334155", lw=1.1),
    )

    inset_ax.text(
        0.03,
        1.03,
        inset.title,
        transform=inset_ax.transAxes,
        va="bottom",
        ha="left",
        fontsize=10,
        color="#1f2a44",
        weight="semibold",
        clip_on=False,
    )
    inset_ax.text(
        0.03,
        0.03,
        f"multiplier={_fmt(mult)} at {int(inset.ground_slope)}{DEG} ground",
        transform=inset_ax.transAxes,
        va="bottom",
        ha="left",
        fontsize=9,
        color="#475569",
    )

    inset_ax.set_xticks([])
    inset_ax.set_yticks([])
    for spine in inset_ax.spines.values():
        spine.set_color("#cbd5e1")
        spine.set_linewidth(1.0)


def _draw_steepness_insets(ax: plt.Axes, spec: Panel) -> None:
    left = ax.inset_axes((0.06, 0.03, 0.40, 0.50))
    right = ax.inset_axes((0.63, 0.55, 0.38, 0.46))
    _draw_inset(left, spec.insets[0])
    _draw_inset(right, spec.insets[1])

    source_x = spec.insets[0].ground_slope
    source_y = spec.fn(source_x)
    ax.scatter(
        [source_x],
        [source_y],
        s=34,
        color="#1f2937",
        edgecolors="white",
        linewidths=0.9,
        zorder=6,
    )

    left_conn = ConnectionPatch(
        xyA=(source_x, source_y),
        coordsA=ax.transData,
        xyB=(0.98, 0.70),
        coordsB=left.transAxes,
        arrowstyle="-",
        linewidth=1.2,
        linestyle=(0, (3, 3)),
        connectionstyle="arc3,rad=0.12",
        color="#64748b",
    )
    right_conn = ConnectionPatch(
        xyA=(source_x, source_y),
        coordsA=ax.transData,
        xyB=(0.02, 0.06),
        coordsB=right.transAxes,
        arrowstyle="-",
        linewidth=1.2,
        linestyle=(0, (3, 3)),
        connectionstyle="arc3,rad=-0.10",
        color="#64748b",
    )
    ax.add_artist(left_conn)
    ax.add_artist(right_conn)


def build_panel(spec: Panel, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9.8, 4.6), dpi=100)
    fig.patch.set_facecolor("#f8fafc")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.17, top=0.88)

    _configure_axes(ax, spec)

    x = np.linspace(spec.x_min, spec.x_max, 500)
    y = np.array([spec.fn(float(v)) for v in x])

    _add_colored_curve(ax, x=x, y=y, cmap_name=spec.line_cmap, lw=4.0)
    _draw_annotations(ax, spec)

    if spec.insets:
        if len(spec.insets) != 2:
            raise ValueError("Ground steepness panel expects exactly 2 insets")
        _draw_steepness_insets(ax, spec)

    # Keep this metadata for downstream docs tooling and accessibility.
    manager = fig.canvas.manager
    if manager is not None:
        manager.set_window_title(spec.title)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="svg", facecolor=fig.get_facecolor())
    plt.close(fig)


PANELS: tuple[Panel, ...] = (
    Panel(
        file_name="rating_curve_downhill_fun.svg",
        title='Downhill track angle "fun" rating',
        x_label="Track slope",
        fn=_downhill_segment_score,
        x_min=0,
        x_max=30,
        y_min=-60,
        y_max=130,
        x_ticks=(0, 5, 10, 15, 20, 25, 30),
        y_ticks=(-50, 0, 50, 100),
        line_cmap="track",
        top_percent_axis=True,
        annotations=(
            AnnotationSpec(
                text="Getting too thrilling",
                x=18.0,
                y=84.0,
                target_x=16.0,
            ),
        ),
    ),
    Panel(
        file_name="rating_curve_uphill_doability.svg",
        title='Uphill track angle "doability" rating',
        x_label="Track slope",
        fn=_uphill_segment_score,
        x_min=0,
        x_max=30,
        y_min=-60,
        y_max=130,
        x_ticks=(0, 5, 10, 15, 20, 25, 30),
        y_ticks=(-50, 0, 50, 100),
        line_cmap="track",
        top_percent_axis=True,
    ),
    Panel(
        file_name="rating_curve_ground_slope_penalty.svg",
        title="Ground slope penalty curve",
        x_label="Ground slope angle",
        fn=_avy_slope_penalty,
        x_min=0,
        x_max=60,
        y_min=0,
        y_max=1.05,
        x_ticks=(0, 10, 20, 30, 40, 50, 60),
        y_ticks=(0, 0.25, 0.5, 0.75, 1.0),
        line_cmap="ground",
        highlight_min=STANDING_AVY_MIN_DEG,
        highlight_max=STANDING_AVY_MAX_DEG,
    ),
    Panel(
        file_name="rating_curve_ground_steepness_multiplier.svg",
        title='Ground steepness multiplier for "fun" and "doability" (AKA sidehilling is tough)',
        x_label="Ground slope angle",
        fn=_ground_slope_penalty,
        x_min=0,
        x_max=55,
        y_min=0,
        y_max=1.05,
        x_ticks=(0, 10, 20, 30, 40, 50),
        y_ticks=(0, 0.25, 0.5, 0.75, 1.0),
        line_cmap="ground",
        highlight_min=20,
        highlight_max=40,
        annotations=(
            AnnotationSpec(
                text="No adjustment needed on fairly normal ground slopes.",
                x=2.2,
                y=0.91,
            ),
        ),
        insets=(
            InsetSpec(
                title="Downhill rating at 35° ground",
                fn=_downhill_segment_score,
                ground_slope=35.0,
            ),
            InsetSpec(
                title="Uphill rating at 35° ground",
                fn=_uphill_segment_score,
                ground_slope=35.0,
            ),
        ),
    ),
)


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for panel in PANELS:
        out_path = out_dir / panel.file_name
        build_panel(panel, out_path)
        written.append(out_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-panel rating-curve SVGs.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs"),
        help="Output directory (default: docs)",
    )
    args = parser.parse_args()

    written = write_all(args.out_dir)
    for path in written:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
