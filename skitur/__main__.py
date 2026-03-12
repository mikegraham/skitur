"""Report CLI entrypoint for `python -m skitur`."""

from __future__ import annotations

import argparse
from pathlib import Path

from skitur.report import generate_report
from skitur.terrain import TerrainLoader

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "skitur" / "dem"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a static HTML ski-tour report.")
    parser.add_argument("gpx_file", type=Path, help="Path to GPX file to analyze")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output report path (default: <gpx_stem>_report.html)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=_DEFAULT_CACHE_DIR,
        help=f"DEM tile cache directory (default: {_DEFAULT_CACHE_DIR})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loader = TerrainLoader(cache_dir=args.cache_dir)
    out = generate_report(args.gpx_file, args.output, terrain_loader=loader)
    print(f"Generated {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
