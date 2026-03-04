#!/usr/bin/env python3
"""Capture README screenshots from a generated report page.

Usage (one-shot):
  python3 scripts/update_readme_screenshots.py --page debug.html

Usage (watch local file for changes):
  python3 scripts/update_readme_screenshots.py --page debug.html --watch
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Iterable

from PIL import Image
from playwright.sync_api import sync_playwright


@dataclass(frozen=True)
class Shot:
    name: str
    selector: str | None  # None => full page screenshot


SHOTS: tuple[Shot, ...] = (
    Shot("01_full_report.png", "#results-section"),
    Shot("02_score_panel.png", "#score-panel"),
    Shot("03_stats_panel.png", "#stats-panel"),
    Shot("04_map_row.png", "#map-row"),
    Shot("05_map.png", "#map"),
    Shot("06_ground_legend.png", "#ground-legend"),
    Shot("07_track_legend.png", "#track-legend"),
    Shot("08_elevation_profile.png", "#elevation-chart"),
    Shot("09_slope_profile.png", "#slopes-chart"),
    Shot("10_track_slope_distribution.png", "#violin-chart"),
    Shot("11_ground_angle_distribution.png", "#histogram-chart"),
    Shot("12_slope_by_aspect.png", "#aspect-rose-chart"),
    Shot("13_fraction_on_aspect.png", "#aspect-dist-chart"),
)


def _as_uri(page_arg: str, cwd: Path) -> str:
    if page_arg.startswith(("http://", "https://", "file://")):
        return page_arg
    return (cwd / page_arg).resolve().as_uri()


def _prepare_page(page) -> None:
    # If this is a generated static report, force the results section visible.
    page.evaluate(
        """
        () => {
          const results = document.getElementById('results-section');
          if (results) results.style.display = 'block';
          const upload = document.getElementById('upload-section');
          if (upload) upload.style.display = 'none';
        }
        """
    )


def _trim_white_margin(
    img: Image.Image,
    *,
    threshold: int = 245,
    min_non_bg_per_col: int = 8,
    min_non_bg_per_row: int = 8,
    pad: int = 4,
) -> Image.Image:
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
            r = int(pixel[0])
            g = int(pixel[1])
            b = int(pixel[2])
            if r < threshold or g < threshold or b < threshold:
                non_bg_cols[x] += 1
                row_count += 1
        non_bg_rows[y] = row_count

    left = next((i for i, c in enumerate(non_bg_cols) if c >= min_non_bg_per_col), None)
    right = next((i for i in range(w - 1, -1, -1) if non_bg_cols[i] >= min_non_bg_per_col), None)
    top = next((i for i, c in enumerate(non_bg_rows) if c >= min_non_bg_per_row), None)
    bottom = next((i for i in range(h - 1, -1, -1) if non_bg_rows[i] >= min_non_bg_per_row), None)

    if left is None or right is None or top is None or bottom is None:
        return rgb

    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w - 1, right + pad)
    bottom = min(h - 1, bottom + pad)
    return rgb.crop((left, top, right + 1, bottom + 1))


def _compose_aspect_pair(out_dir: Path) -> None:
    left_path = out_dir / "12_slope_by_aspect.png"
    right_path = out_dir / "13_fraction_on_aspect.png"
    out_path = out_dir / "12_aspect_pair.png"
    if not left_path.exists() or not right_path.exists():
        return

    left = _trim_white_margin(
        Image.open(left_path),
        threshold=245,
        min_non_bg_per_col=10,
        min_non_bg_per_row=10,
        pad=2,
    )
    right = _trim_white_margin(
        Image.open(right_path),
        threshold=245,
        min_non_bg_per_col=10,
        min_non_bg_per_row=10,
        pad=2,
    )
    gap = 4
    h = max(left.height, right.height)
    w = left.width + gap + right.width
    merged = Image.new("RGB", (w, h), (255, 255, 255))
    merged.paste(left, (0, (h - left.height) // 2))
    merged.paste(right, (left.width + gap, (h - right.height) // 2))
    merged.save(out_path)
    print(f"wrote {out_path}")


def capture(page_uri: str, out_dir: Path, viewport_w: int, viewport_h: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": viewport_w, "height": viewport_h})
        page.goto(page_uri)
        page.wait_for_timeout(1800)
        _prepare_page(page)
        page.wait_for_timeout(1200)

        # Wait for core chart containers; plotting can be async.
        for selector in ("#score-panel", "#map", "#elevation-chart", "#aspect-rose-chart"):
            try:
                page.wait_for_selector(selector, timeout=12000)
            except Exception:
                # Capture anyway; this keeps the script resilient for partial pages.
                pass

        page.wait_for_timeout(900)

        for shot in SHOTS:
            out_path = out_dir / shot.name
            if shot.selector is None:
                page.screenshot(path=str(out_path), full_page=True)
                print(f"wrote {out_path}")
                continue
            locator = page.locator(shot.selector).first
            try:
                locator.scroll_into_view_if_needed(timeout=8000)
                page.wait_for_timeout(180)
                locator.screenshot(path=str(out_path))
                print(f"wrote {out_path}")
            except Exception as exc:
                print(f"[warn] could not capture {shot.name} ({shot.selector}): {exc}")

        _compose_aspect_pair(out_dir)

        browser.close()


def _watch_files(candidates: Iterable[Path]) -> int:
    latest = 0
    for path in candidates:
        if path.exists():
            latest = max(latest, path.stat().st_mtime_ns)
    return latest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", default="debug.html", help="Report page path or URL")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs/readme_screenshots"),
        help="Output screenshot directory",
    )
    parser.add_argument("--width", type=int, default=1700, help="Viewport width")
    parser.add_argument("--height", type=int, default=1200, help="Viewport height")
    parser.add_argument("--watch", action="store_true", help="Watch local page file for changes")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Watch polling interval seconds",
    )
    args = parser.parse_args()

    cwd = Path.cwd()
    page_uri = _as_uri(args.page, cwd)
    page_path = (cwd / args.page).resolve()

    capture(page_uri, args.out_dir, args.width, args.height)

    if not args.watch:
        return

    if page_uri.startswith(("http://", "https://")):
        raise SystemExit("--watch supports local files only")

    watch_paths = [page_path]
    template_path = cwd / "skitur" / "templates" / "index.html"
    if template_path.exists():
        watch_paths.append(template_path.resolve())

    print(f"[watch] monitoring {', '.join(str(p) for p in watch_paths)}")
    last = _watch_files(watch_paths)
    while True:
        time.sleep(max(0.2, args.interval))
        now = _watch_files(watch_paths)
        if now <= last:
            continue
        last = now
        try:
            capture(page_uri, args.out_dir, args.width, args.height)
        except Exception as exc:
            print(f"[warn] capture failed: {exc}")


if __name__ == "__main__":
    main()
