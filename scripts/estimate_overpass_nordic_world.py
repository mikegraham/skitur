#!/usr/bin/env python3
"""Estimate Overpass time to fetch worldwide OSM nordic trail locations.

This script uses adaptive quadtree tiling:
1) Preflight each tile with a tiny `out ids` query.
2) Split tiles whose count exceeds a threshold.
3) Sample accepted tiles with the real query (`out tags center`) to estimate
   per-request cost for the full download pass.

It does not download full geometries and does not write OSM features.
The goal is planning runtime and request volume.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import requests_cache


@dataclass(frozen=True)
class BBox:
    """WGS84 bbox in degrees with no antimeridian wrap."""

    s: float
    w: float
    n: float
    e: float

    def validate(self) -> None:
        if not self.s < self.n:
            raise ValueError(f"Invalid bbox latitude: {self.s}..{self.n}")
        if not self.w < self.e:
            raise ValueError(f"Invalid bbox longitude: {self.w}..{self.e}")

    def area_deg2(self) -> float:
        return (self.n - self.s) * (self.e - self.w)

    def split4(self) -> tuple["BBox", "BBox", "BBox", "BBox"]:
        mid_lat = (self.s + self.n) / 2.0
        mid_lon = (self.w + self.e) / 2.0
        # SW, SE, NW, NE
        return (
            BBox(self.s, self.w, mid_lat, mid_lon),
            BBox(self.s, mid_lon, mid_lat, self.e),
            BBox(mid_lat, self.w, self.n, mid_lon),
            BBox(mid_lat, mid_lon, self.n, self.e),
        )


@dataclass
class RequestStats:
    count: int = 0
    uncached_count: int = 0
    failed_count: int = 0
    net_seconds: float = 0.0
    delay_seconds: float = 0.0
    bytes: int = 0

    def add(self, *, cached: bool, net_s: float, delay_s: float, nbytes: int) -> None:
        self.count += 1
        if not cached:
            self.uncached_count += 1
        self.net_seconds += net_s
        self.delay_seconds += delay_s
        self.bytes += nbytes

    def avg_total_per_request(self) -> float:
        if self.count == 0:
            return 0.0
        return (self.net_seconds + self.delay_seconds) / self.count

    def avg_uncached_total(self) -> float:
        if self.uncached_count == 0:
            return 0.0
        return (self.net_seconds + self.delay_seconds) / self.uncached_count


def make_cached_session(
    *,
    cache_name: str,
    backend: str,
    expire_after_s: int | None,
    user_agent: str,
) -> requests_cache.CachedSession:
    session = requests_cache.CachedSession(
        cache_name=cache_name,
        backend=backend,
        expire_after=expire_after_s,
        allowable_methods=("GET", "POST"),
        allowable_codes=(200,),
    )
    session.headers.update({"User-Agent": user_agent})
    return session


def build_query(
    *,
    bbox: BBox,
    tag_key: str,
    tag_regex: str,
    include_relations: bool,
    out_mode: str,
    overpass_timeout_s: int,
) -> str:
    bbox.validate()
    rel_line = (
        f'relation["{tag_key}"~"{tag_regex}"]({bbox.s},{bbox.w},{bbox.n},{bbox.e});\n'
        if include_relations
        else ""
    )
    return (
        f"[out:json][timeout:{int(overpass_timeout_s)}];\n"
        "(\n"
        f'way["{tag_key}"~"{tag_regex}"]({bbox.s},{bbox.w},{bbox.n},{bbox.e});\n'
        f"{rel_line}"
        ");\n"
        f"out {out_mode};"
    )


def post_overpass_json(
    *,
    session: requests_cache.CachedSession,
    endpoint: str,
    query: str,
    http_timeout_s: float,
    min_delay_s: float,
    max_delay_s: float,
    retries: int,
) -> tuple[dict[str, Any], bool, float, float, int]:
    """Return (json, cached, net_seconds, delay_seconds, response_bytes)."""
    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            t0 = time.perf_counter()
            resp = session.post(endpoint, data=query.encode("utf-8"), timeout=http_timeout_s)
            net_s = time.perf_counter() - t0
            cached = bool(getattr(resp, "from_cache", False))
            resp.raise_for_status()

            delay_s = 0.0
            if not cached:
                delay_s = random.uniform(min_delay_s, max_delay_s)
                time.sleep(delay_s)

            nbytes = len(resp.content or b"")
            return resp.json(), cached, net_s, delay_s, nbytes
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt >= retries:
                break
            # Exponential backoff with jitter.
            sleep_s = min(30.0, (2 ** (attempt - 1)) + random.uniform(0.2, 0.8))
            time.sleep(sleep_s)

    assert last_err is not None
    raise last_err


@dataclass(frozen=True)
class AcceptedTile:
    bbox: BBox
    depth: int
    preflight_count: int


def preflight_world(
    *,
    roots: list[BBox],
    session: requests_cache.CachedSession,
    endpoint: str,
    tag_key: str,
    tag_regex: str,
    include_relations: bool,
    preflight_timeout_s: int,
    http_timeout_s: float,
    min_delay_s: float,
    max_delay_s: float,
    max_preflight_count: int,
    max_depth: int,
    min_area_deg2: float,
    retries: int,
) -> tuple[list[AcceptedTile], RequestStats]:
    accepted: list[AcceptedTile] = []
    stats = RequestStats()

    stack: list[tuple[BBox, int]] = []
    for root in roots:
        root.validate()
        stack.append((root, 0))

    while stack:
        bbox, depth = stack.pop()

        query = build_query(
            bbox=bbox,
            tag_key=tag_key,
            tag_regex=tag_regex,
            include_relations=include_relations,
            out_mode="ids",
            overpass_timeout_s=preflight_timeout_s,
        )
        data, cached, net_s, delay_s, nbytes = post_overpass_json(
            session=session,
            endpoint=endpoint,
            query=query,
            http_timeout_s=http_timeout_s,
            min_delay_s=min_delay_s,
            max_delay_s=max_delay_s,
            retries=retries,
        )
        stats.add(cached=cached, net_s=net_s, delay_s=delay_s, nbytes=nbytes)

        elements = data.get("elements", [])
        count = len(elements) if isinstance(elements, list) else 0
        if count <= 0:
            continue

        should_split = (
            count > max_preflight_count
            and depth < max_depth
            and bbox.area_deg2() > min_area_deg2
        )
        if should_split:
            stack.extend((child, depth + 1) for child in bbox.split4())
        else:
            accepted.append(AcceptedTile(bbox=bbox, depth=depth, preflight_count=count))

        if stats.count % 50 == 0:
            print(
                f"[preflight] requests={stats.count} accepted={len(accepted)} pending={len(stack)}",
                flush=True,
            )

    return accepted, stats


def sample_real_queries(
    *,
    accepted_tiles: list[AcceptedTile],
    sample_size: int,
    session: requests_cache.CachedSession,
    endpoint: str,
    tag_key: str,
    tag_regex: str,
    include_relations: bool,
    real_timeout_s: int,
    http_timeout_s: float,
    min_delay_s: float,
    max_delay_s: float,
    retries: int,
    rng: random.Random,
) -> tuple[RequestStats, int]:
    sample_stats = RequestStats()
    if sample_size <= 0 or not accepted_tiles:
        return sample_stats, 0

    sample = rng.sample(accepted_tiles, min(sample_size, len(accepted_tiles)))
    total_elements = 0

    for idx, tile in enumerate(sample, 1):
        query = build_query(
            bbox=tile.bbox,
            tag_key=tag_key,
            tag_regex=tag_regex,
            include_relations=include_relations,
            out_mode="tags center",
            overpass_timeout_s=real_timeout_s,
        )
        try:
            data, cached, net_s, delay_s, nbytes = post_overpass_json(
                session=session,
                endpoint=endpoint,
                query=query,
                http_timeout_s=http_timeout_s,
                min_delay_s=min_delay_s,
                max_delay_s=max_delay_s,
                retries=retries,
            )
            sample_stats.add(cached=cached, net_s=net_s, delay_s=delay_s, nbytes=nbytes)
        except Exception as exc:  # noqa: BLE001
            sample_stats.failed_count += 1
            print(f"[sample] warning: tile request failed: {exc}", flush=True)
            continue

        elems = data.get("elements", [])
        total_elements += len(elems) if isinstance(elems, list) else 0

        if idx % 10 == 0 or idx == len(sample):
            print(f"[sample] {idx}/{len(sample)}", flush=True)

    return sample_stats, total_elements


def write_tiles_csv(path: Path, accepted_tiles: list[AcceptedTile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["south", "west", "north", "east", "depth", "preflight_count"])
        for t in accepted_tiles:
            w.writerow([t.bbox.s, t.bbox.w, t.bbox.n, t.bbox.e, t.depth, t.preflight_count])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate Overpass runtime for worldwide nordic trail location fetches.",
    )
    parser.add_argument(
        "--endpoint",
        default="https://overpass-api.de/api/interpreter",
        help="Overpass endpoint URL.",
    )
    parser.add_argument(
        "--cache-name",
        default=str(Path.home() / ".cache" / "skitur" / "overpass_nordic_estimate"),
        help="requests-cache db base path.",
    )
    parser.add_argument(
        "--cache-backend",
        default="sqlite",
        choices=["sqlite", "filesystem", "memory"],
        help="requests-cache backend.",
    )
    parser.add_argument(
        "--cache-expire-hours",
        type=int,
        default=168,
        help="Cache TTL in hours (default: 168 = 7 days).",
    )
    parser.add_argument(
        "--user-agent",
        default="skitur-overpass-estimator/0.1 (+local-run)",
        help="HTTP user agent sent to Overpass.",
    )
    parser.add_argument("--min-delay-s", type=float, default=0.8)
    parser.add_argument("--max-delay-s", type=float, default=1.6)
    parser.add_argument("--http-timeout-s", type=float, default=180.0)
    parser.add_argument("--preflight-timeout-s", type=int, default=60)
    parser.add_argument("--real-timeout-s", type=int, default=180)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--tag-key",
        default="piste:type",
        help="OSM tag key to filter.",
    )
    parser.add_argument(
        "--tag-regex",
        default="(^|;)nordic($|;)",
        help='Overpass regex for tag value (default matches "nordic" in semicolon lists).',
    )
    parser.add_argument(
        "--include-relations",
        action="store_true",
        help="Include relations in way+relation queries.",
    )
    parser.add_argument("--max-preflight-count", type=int, default=8000)
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--min-area-deg2", type=float, default=1e-6)
    parser.add_argument(
        "--sample-real-tiles",
        type=int,
        default=30,
        help="How many accepted tiles to sample with the real query for runtime estimate.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--roots",
        action="append",
        default=None,
        help=(
            "Root bboxes as 's,w,n,e'. Default splits world into west/east hemispheres to "
            "avoid antimeridian handling."
        ),
    )
    parser.add_argument(
        "--tiles-csv",
        default=None,
        help="Optional output CSV of accepted tiles and preflight counts.",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="Optional output JSON summary path.",
    )
    return parser.parse_args()


def parse_roots(values: list[str]) -> list[BBox]:
    roots: list[BBox] = []
    for raw in values:
        if isinstance(raw, (list, tuple)):
            for inner in raw:
                roots.extend(parse_roots([str(inner)]))
            continue
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Invalid root bbox format: {raw!r}")
        s, w, n, e = map(float, parts)
        bbox = BBox(s=s, w=w, n=n, e=e)
        bbox.validate()
        roots.append(bbox)
    return roots


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    rng = random.Random(args.seed)

    raw_roots = args.roots if args.roots else ["-85,-180,85,0", "-85,0,85,180"]
    roots = parse_roots(raw_roots)
    session = make_cached_session(
        cache_name=args.cache_name,
        backend=args.cache_backend,
        expire_after_s=args.cache_expire_hours * 3600 if args.cache_expire_hours > 0 else None,
        user_agent=args.user_agent,
    )

    print("[run] starting preflight quadtree pass", flush=True)
    t0 = time.perf_counter()
    accepted, preflight_stats = preflight_world(
        roots=roots,
        session=session,
        endpoint=args.endpoint,
        tag_key=args.tag_key,
        tag_regex=args.tag_regex,
        include_relations=args.include_relations,
        preflight_timeout_s=args.preflight_timeout_s,
        http_timeout_s=args.http_timeout_s,
        min_delay_s=args.min_delay_s,
        max_delay_s=args.max_delay_s,
        max_preflight_count=args.max_preflight_count,
        max_depth=args.max_depth,
        min_area_deg2=args.min_area_deg2,
        retries=args.retries,
    )
    preflight_elapsed = time.perf_counter() - t0

    print(
        f"[run] preflight done: tiles={len(accepted)} requests={preflight_stats.count} "
        f"elapsed={preflight_elapsed:.1f}s",
        flush=True,
    )

    if args.tiles_csv:
        write_tiles_csv(Path(args.tiles_csv), accepted)
        print(f"[out] wrote tiles csv: {args.tiles_csv}", flush=True)

    print("[run] sampling real query cost", flush=True)
    real_sample_stats, real_sample_elements = sample_real_queries(
        accepted_tiles=accepted,
        sample_size=args.sample_real_tiles,
        session=session,
        endpoint=args.endpoint,
        tag_key=args.tag_key,
        tag_regex=args.tag_regex,
        include_relations=args.include_relations,
        real_timeout_s=args.real_timeout_s,
        http_timeout_s=args.http_timeout_s,
        min_delay_s=args.min_delay_s,
        max_delay_s=args.max_delay_s,
        retries=args.retries,
        rng=rng,
    )

    real_req_est = len(accepted)
    preflight_req = preflight_stats.count
    sample_n = real_sample_stats.count

    avg_real_req_s = (
        real_sample_stats.avg_total_per_request()
        if sample_n > 0
        else preflight_stats.avg_total_per_request() * 1.2
    )
    est_real_phase_s = real_req_est * avg_real_req_s
    est_total_s = preflight_elapsed + est_real_phase_s

    summary = {
        "roots": [vars(r) for r in roots],
        "query": {
            "tag_key": args.tag_key,
            "tag_regex": args.tag_regex,
            "include_relations": args.include_relations,
        },
        "tiling": {
            "max_preflight_count": args.max_preflight_count,
            "max_depth": args.max_depth,
            "min_area_deg2": args.min_area_deg2,
            "accepted_tiles": len(accepted),
        },
        "preflight": {
            "requests": preflight_req,
            "uncached_requests": preflight_stats.uncached_count,
            "failed_requests": preflight_stats.failed_count,
            "elapsed_seconds": preflight_elapsed,
            "net_seconds": preflight_stats.net_seconds,
            "delay_seconds": preflight_stats.delay_seconds,
            "bytes": preflight_stats.bytes,
            "avg_request_seconds": preflight_stats.avg_total_per_request(),
        },
        "real_sample": {
            "requests": sample_n,
            "uncached_requests": real_sample_stats.uncached_count,
            "failed_requests": real_sample_stats.failed_count,
            "net_seconds": real_sample_stats.net_seconds,
            "delay_seconds": real_sample_stats.delay_seconds,
            "bytes": real_sample_stats.bytes,
            "avg_request_seconds": real_sample_stats.avg_total_per_request(),
            "total_elements_in_sample": real_sample_elements,
        },
        "estimate": {
            "real_phase_requests": real_req_est,
            "real_phase_seconds_est": est_real_phase_s,
            "total_seconds_est": est_total_s,
            "total_minutes_est": est_total_s / 60.0,
            "total_hours_est": est_total_s / 3600.0,
            "assumption": (
                "real phase avg request time estimated from sampled accepted tiles; "
                "if no sample, fallback heuristic uses 1.2x preflight avg."
            ),
        },
    }

    print(json.dumps(summary, indent=2))
    if args.summary_json:
        out = Path(args.summary_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2))
        print(f"[out] wrote summary json: {out}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
