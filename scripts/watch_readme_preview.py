#!/usr/bin/env python3
"""Regenerate README_preview.html whenever README.md changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


HTML_SHELL = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>README preview</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body {{
      max-width: 980px;
      margin: 40px auto;
      padding: 0 16px;
      font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif;
      line-height: 1.5;
    }}
    img {{ max-width: 100%; height: auto; }}
    pre {{
      overflow: auto;
      background: #f6f8fa;
      padding: 12px;
      border-radius: 6px;
    }}
    code {{
      background: #f6f8fa;
      padding: 2px 4px;
      border-radius: 4px;
    }}
    h1, h2, h3, h4 {{ line-height: 1.25; }}
  </style>
</head>
<body>{body}</body>
</html>
"""


def render_gfm(markdown_text: str, timeout_s: float) -> str:
    payload = json.dumps({"text": markdown_text, "mode": "gfm"}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/markdown",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": "skitur-readme-preview/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read().decode("utf-8")


def build_preview(readme_path: Path, output_path: Path, timeout_s: float) -> None:
    md = readme_path.read_text(encoding="utf-8")
    body_html = render_gfm(md, timeout_s=timeout_s)
    output_path.write_text(HTML_SHELL.format(body=body_html), encoding="utf-8")


def watch(readme_path: Path, output_path: Path, poll_s: float, timeout_s: float) -> None:
    last_mtime_ns: int | None = None
    while True:
        try:
            mtime_ns = readme_path.stat().st_mtime_ns
        except FileNotFoundError:
            time.sleep(poll_s)
            continue

        if mtime_ns != last_mtime_ns:
            try:
                build_preview(readme_path, output_path, timeout_s=timeout_s)
                print(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"updated {output_path} from {readme_path}",
                    flush=True,
                )
            except urllib.error.URLError as exc:
                print(f"[warn] preview render failed: {exc}", flush=True)
            last_mtime_ns = mtime_ns

        time.sleep(poll_s)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--out", type=Path, default=Path("README_preview.html"))
    parser.add_argument("--poll", type=float, default=1.0, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    parser.add_argument("--once", action="store_true", help="Render once and exit")
    args = parser.parse_args()

    if args.once:
        build_preview(args.readme, args.out, timeout_s=args.timeout)
        print(f"updated {args.out} from {args.readme}")
        return

    watch(args.readme, args.out, poll_s=args.poll, timeout_s=args.timeout)


if __name__ == "__main__":
    main()
