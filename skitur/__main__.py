"""Report CLI entrypoint for `python -m skitur`."""

from __future__ import annotations

import argparse
from pathlib import Path

from skitur.web import generate_report


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = generate_report(args.gpx_file, args.output)
    print(f"Generated {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
