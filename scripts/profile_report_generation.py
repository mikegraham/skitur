#!/usr/bin/env python3
"""Profile static HTML report generation performance.

Examples:
  .venv/bin/python scripts/profile_report_generation.py \
    --gpx Twin_Lakes.gpx --passes 8 --output /tmp/profile_after.json

  .venv/bin/python scripts/profile_report_generation.py \
    --repo-root /tmp/skitur_prev --gpx Twin_Lakes.gpx \
    --passes 8 --output /tmp/profile_before.json

  .venv/bin/python scripts/profile_report_generation.py \
    --gpx Twin_Lakes.gpx --passes 8 \
    --baseline /tmp/profile_before.json --output /tmp/profile_after.json
"""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import pstats

HOTSPOT_TARGETS = [
    ("web._compute_analysis", "_compute_analysis", "skitur/web.py"),
    ("plot.compute_map_grids", "compute_map_grids", "skitur/plot.py"),
    ("terrain.get_slope_grid", "get_slope_grid", "skitur/terrain.py"),
    ("score._compute_runout_exposures", "_compute_runout_exposures", "skitur/score.py"),
    ("json.encoder.iterencode", "iterencode", "/json/encoder.py"),
]


def _resolve_under_repo(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (repo_root / path)


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _min(values: list[float]) -> float:
    return min(values) if values else 0.0


def _max(values: list[float]) -> float:
    return max(values) if values else 0.0


def _phase_summary(phase: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    total_avg = _mean(phase["total"])
    out: dict[str, dict[str, float]] = {}
    for key, values in phase.items():
        avg = _mean(values)
        out[key] = {
            "avg_s": avg,
            "min_s": _min(values),
            "max_s": _max(values),
            "pct_of_total": (avg / total_avg * 100.0) if total_avg else 0.0,
        }
    return out


def _match_stat_row(
    stats: dict[tuple[str, int, str], tuple[int, int, float, float, dict]],
    func_name: str,
    file_substring: str,
) -> tuple[tuple[str, int, str], tuple[int, int, float, float, dict]] | tuple[None, None]:
    for key, val in stats.items():
        filename, line, function = key
        if function == func_name and file_substring in filename:
            return key, val
    return None, None


def _collect_hotspots(agg: pstats.Stats, passes: int) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    total_cpu_s = agg.total_tt
    for label, function, file_sub in HOTSPOT_TARGETS:
        key, val = _match_stat_row(agg.stats, function, file_sub)
        if key is None or val is None:
            continue
        cc, nc, tt, ct, _callers = val
        row = {
            "label": label,
            "file": key[0],
            "line": key[1],
            "func": key[2],
            "ncalls_prim": cc,
            "ncalls_total": nc,
            "tottime_total_s": tt,
            "cumtime_total_s": ct,
            "tot_per_run_s": tt / passes,
            "cum_per_run_s": ct / passes,
            "tot_pct_of_profile": (tt / total_cpu_s * 100.0) if total_cpu_s else 0.0,
            "cum_pct_of_profile": (ct / total_cpu_s * 100.0) if total_cpu_s else 0.0,
        }
        rows.append(row)
    rows.sort(key=lambda r: float(r["cum_pct_of_profile"]), reverse=True)
    return rows


def _compare_against_baseline(
    current: dict,
    baseline: dict,
) -> dict:
    curr_total = float(current["avg_total_s"])
    base_total = float(baseline["avg_total_s"])
    delta_s = curr_total - base_total
    delta_pct = (delta_s / base_total * 100.0) if base_total else 0.0

    phase_delta: dict[str, dict[str, float]] = {}
    curr_phases = current.get("phases", {})
    base_phases = baseline.get("phases", {})
    for key in sorted(set(curr_phases) & set(base_phases)):
        c = float(curr_phases[key]["avg_s"])
        b = float(base_phases[key]["avg_s"])
        d = c - b
        phase_delta[key] = {
            "delta_s": d,
            "delta_pct": (d / b * 100.0) if b else 0.0,
        }

    return {
        "baseline_avg_total_s": base_total,
        "current_avg_total_s": curr_total,
        "delta_s": delta_s,
        "delta_pct": delta_pct,
        "phase_delta": phase_delta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile skitur HTML report generation.")
    parser.add_argument("--repo-root", default=".", help="Repository root to profile.")
    parser.add_argument("--gpx", required=True, help="GPX file path (absolute or relative to repo root).")
    parser.add_argument("--passes", type=int, default=8, help="Number of warm passes to measure.")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup iterations before timed passes.")
    parser.add_argument("--output", type=str, default="", help="Write JSON summary to this path.")
    parser.add_argument(
        "--baseline",
        type=str,
        default="",
        help="Optional baseline JSON (from this script) for comparison.",
    )
    parser.add_argument(
        "--no-cprofile",
        action="store_true",
        help="Skip cProfile hotspot aggregation.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        raise SystemExit(f"repo root does not exist: {repo_root}")
    gpx_path = _resolve_under_repo(repo_root, args.gpx).resolve()
    if not gpx_path.exists():
        raise SystemExit(f"GPX not found: {gpx_path}")
    if args.passes <= 0:
        raise SystemExit("--passes must be >= 1")
    if args.warmup < 0:
        raise SystemExit("--warmup must be >= 0")

    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from flask import render_template

    from skitur.web import _build_response, _compute_analysis, app, generate_report

    phase_names = [
        "compute_analysis",
        "build_response",
        "render_template",
        "json_dumps",
        "inject_replace",
        "write_text",
        "total",
    ]
    phase: dict[str, list[float]] = {name: [] for name in phase_names}
    report_out = Path(tempfile.gettempdir()) / f"skitur_profile_{os.getpid()}.html"

    for _ in range(args.warmup):
        points, stats, score, grids = _compute_analysis(gpx_path)
        data = _build_response(points, stats, score, grids)
        with app.app_context():
            template_html = render_template("index.html")
        _ = template_html.replace("</body>", f"<script>const data={json.dumps(data, separators=(',', ':'))};</script></body>")

    for _ in range(args.passes):
        t_total = time.perf_counter()

        t0 = time.perf_counter()
        points, stats, score, grids = _compute_analysis(gpx_path)
        phase["compute_analysis"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        data = _build_response(points, stats, score, grids)
        phase["build_response"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        with app.app_context():
            template_html = render_template("index.html")
        phase["render_template"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        data_json = json.dumps(data, separators=(",", ":"))
        phase["json_dumps"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        html = template_html.replace("</body>", f"<script>const data={data_json};</script></body>")
        phase["inject_replace"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        report_out.write_text(html)
        phase["write_text"].append(time.perf_counter() - t0)

        phase["total"].append(time.perf_counter() - t_total)

    result: dict[str, object] = {
        "repo_root": str(repo_root),
        "gpx": str(gpx_path),
        "passes": args.passes,
        "warmup": args.warmup,
        "avg_total_s": _mean(phase["total"]),
        "min_total_s": _min(phase["total"]),
        "max_total_s": _max(phase["total"]),
        "phases": _phase_summary(phase),
    }

    if not args.no_cprofile:
        # Warm cProfile path once, then profile N warm passes.
        generate_report(gpx_path, report_out)
        stats_runs: list[pstats.Stats] = []
        cprofile_wall: list[float] = []
        for _ in range(args.passes):
            prof = cProfile.Profile()
            t0 = time.perf_counter()
            prof.enable()
            generate_report(gpx_path, report_out)
            prof.disable()
            cprofile_wall.append(time.perf_counter() - t0)
            stats_runs.append(pstats.Stats(prof))

        agg = stats_runs[0]
        for s in stats_runs[1:]:
            agg.add(s)

        result["cprofile"] = {
            "avg_wall_s": _mean(cprofile_wall),
            "min_wall_s": _min(cprofile_wall),
            "max_wall_s": _max(cprofile_wall),
            "profile_total_time_s": agg.total_tt,
            "hotspots": _collect_hotspots(agg, args.passes),
        }

    if args.baseline:
        baseline_path = Path(args.baseline).resolve()
        baseline = json.loads(baseline_path.read_text())
        result["comparison"] = _compare_against_baseline(result, baseline)

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2))

    print(f"repo_root={result['repo_root']}")
    print(f"gpx={result['gpx']}")
    print(f"passes={args.passes} warmup={args.warmup}")
    print(f"avg_total_s={float(result['avg_total_s']):.6f}")
    print(f"min_total_s={float(result['min_total_s']):.6f}")
    print(f"max_total_s={float(result['max_total_s']):.6f}")

    phases = result["phases"]
    assert isinstance(phases, dict)
    print("phase_avg_s:")
    for key in phase_names:
        phase_data = phases[key]
        assert isinstance(phase_data, dict)
        print(f"  {key}: {float(phase_data['avg_s']):.6f}s ({float(phase_data['pct_of_total']):.2f}%)")

    cprofile = result.get("cprofile")
    if isinstance(cprofile, dict):
        print("hotspots_cum_pct:")
        hotspots = cprofile.get("hotspots", [])
        if isinstance(hotspots, list):
            for row in hotspots:
                if isinstance(row, dict):
                    print(f"  {row['label']}: {float(row['cum_pct_of_profile']):.2f}%")

    comparison = result.get("comparison")
    if isinstance(comparison, dict):
        print("comparison_to_baseline:")
        print(f"  baseline_avg_total_s={float(comparison['baseline_avg_total_s']):.6f}")
        print(f"  current_avg_total_s={float(comparison['current_avg_total_s']):.6f}")
        print(f"  delta_s={float(comparison['delta_s']):+.6f}")
        print(f"  delta_pct={float(comparison['delta_pct']):+.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
