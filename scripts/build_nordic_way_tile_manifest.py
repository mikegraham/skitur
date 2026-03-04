#!/usr/bin/env python3
"""Build DEM tile manifest from Overpass nordic ways.

Single-source pipeline (by design):
1) Query Overpass for way centers tagged `piste:type`.
2) Cache HTTP responses with requests-cache.
3) Buffer way-center points and resolve overlapping DEM tiles.
4) Write both way locations and tile manifest.

Default behavior is manifest-only. Add `--download` to fill local DEM cache.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyproj
import requests_cache
from shapely.geometry import Point, box
from shapely.ops import transform

from dem_stitcher.datasets import get_global_dem_tile_extents, get_overlapping_dem_tiles
from dem_stitcher.stitcher import download_tiles_to_gtiff

WORLD_BBOX = (-90.0, -180.0, 90.0, 180.0)  # s, w, n, e


@dataclass
class OverpassStats:
    request_count: int = 0
    uncached_request_count: int = 0
    retries: int = 0
    response_bytes: int = 0
    network_seconds: float = 0.0
    politeness_delay_seconds: float = 0.0
    elements_seen: int = 0
    elements_with_points: int = 0


@dataclass
class CellBucket:
    merged: Any | None = None
    points: int = 0

    def flush(self) -> None:
        return


def parse_csv_set(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def parse_csv_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_bbox(value: str | None) -> tuple[float, float, float, float]:
    if value is None:
        return WORLD_BBOX
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if len(parts) != 4:
        raise ValueError("`--bbox` must be 's,w,n,e'")
    s, w, n, e = (float(x) for x in parts)
    if not s < n:
        raise ValueError("Invalid bbox: south must be < north")
    if not w < e:
        raise ValueError("Invalid bbox: west must be < east")
    return s, w, n, e


def build_piste_regex(piste_types: set[str]) -> str:
    escaped = [re.escape(v) for v in sorted(piste_types)]
    return f"(^|;)({'|'.join(escaped)})(;|$)"


def build_overpass_query(
    *,
    piste_types: set[str],
    bbox: tuple[float, float, float, float],
    overpass_timeout_s: int,
) -> str:
    s, w, n, e = bbox
    tag_regex = build_piste_regex(piste_types)
    return "\n".join(
        [
            f"[out:json][timeout:{int(overpass_timeout_s)}];",
            "(",
            f'way["piste:type"~"{tag_regex}"]({s},{w},{n},{e});',
            ");",
            "out tags center;",
        ]
    )


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


def fetch_overpass_json(
    *,
    session: requests_cache.CachedSession,
    endpoint: str,
    query: str,
    http_timeout_s: float,
    min_delay_s: float,
    max_delay_s: float,
    retries: int,
    stats: OverpassStats,
) -> dict[str, Any]:
    last_exc: Exception | None = None
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

            stats.request_count += 1
            if not cached:
                stats.uncached_request_count += 1
            stats.response_bytes += len(resp.content or b"")
            stats.network_seconds += net_s
            stats.politeness_delay_seconds += delay_s
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries:
                break
            stats.retries += 1
            time.sleep(min(30.0, (2 ** (attempt - 1)) + random.uniform(0.2, 0.8)))

    assert last_exc is not None
    raise last_exc


def cell_key(lon: float, lat: float, cell_deg: float) -> tuple[int, int]:
    return (math.floor(lon / cell_deg), math.floor(lat / cell_deg))


def add_point_to_cells(
    point: Point,
    cells: dict[tuple[int, int], CellBucket],
    *,
    cell_deg: float,
    flush_every: int,
) -> None:
    key = cell_key(point.x, point.y, cell_deg)
    bucket = cells.setdefault(key, CellBucket())
    if bucket.merged is None:
        west = key[0] * cell_deg
        south = key[1] * cell_deg
        east = west + cell_deg
        north = south + cell_deg
        bucket.merged = box(west, south, east, north)
    bucket.points += 1


def finalize_cells(cells: dict[tuple[int, int], CellBucket]) -> None:
    for bucket in cells.values():
        bucket.flush()


def collect_way_centers(
    *,
    endpoint: str,
    piste_types: set[str],
    bbox: tuple[float, float, float, float],
    overpass_timeout_s: int,
    http_timeout_s: float,
    cache_name: str,
    cache_backend: str,
    cache_expire_s: int | None,
    user_agent: str,
    min_delay_s: float,
    max_delay_s: float,
    retries: int,
    cell_deg: float,
    flush_every: int,
) -> tuple[dict[tuple[int, int], CellBucket], OverpassStats, pd.DataFrame]:
    stats = OverpassStats()
    session = make_cached_session(
        cache_name=cache_name,
        backend=cache_backend,
        expire_after_s=cache_expire_s,
        user_agent=user_agent,
    )
    query = build_overpass_query(
        piste_types=piste_types,
        bbox=bbox,
        overpass_timeout_s=overpass_timeout_s,
    )
    data = fetch_overpass_json(
        session=session,
        endpoint=endpoint,
        query=query,
        http_timeout_s=http_timeout_s,
        min_delay_s=min_delay_s,
        max_delay_s=max_delay_s,
        retries=max(1, retries),
        stats=stats,
    )

    cells: dict[tuple[int, int], CellBucket] = {}
    rows: list[dict[str, Any]] = []

    elements = data.get("elements", [])
    if not isinstance(elements, list):
        elements = []
    stats.elements_seen = len(elements)

    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("type") != "way":
            continue
        osm_id = element.get("id")
        if not isinstance(osm_id, int):
            continue

        center = element.get("center")
        if not (isinstance(center, dict) and "lat" in center and "lon" in center):
            continue

        lat = float(center["lat"])
        lon = float(center["lon"])
        add_point_to_cells(
            Point(lon, lat),
            cells,
            cell_deg=cell_deg,
            flush_every=max(1, flush_every),
        )
        stats.elements_with_points += 1

        raw_tags = element.get("tags")
        tags: dict[str, Any] = raw_tags if isinstance(raw_tags, dict) else {}
        rows.append(
            {
                "osm_type": "way",
                "osm_id": osm_id,
                "lat": lat,
                "lon": lon,
                "piste_type": tags.get("piste:type"),
                "name": tags.get("name"),
                "route": tags.get("route"),
            }
        )

    finalize_cells(cells)
    locations = pd.DataFrame(rows)
    if not locations.empty:
        locations = locations.drop_duplicates(subset=["osm_type", "osm_id"]).reset_index(drop=True)
    return cells, stats, locations


def buffer_geometry_m(geometry: Any, radius_m: float) -> Any:
    if geometry.is_empty:
        return geometry

    center = geometry.representative_point()
    local_crs = pyproj.CRS.from_proj4(
        f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs"
    )
    to_local = pyproj.Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
    to_wgs84 = pyproj.Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
    geometry_local = transform(to_local, geometry)
    buffered_local = geometry_local.buffer(radius_m)
    return transform(to_wgs84, buffered_local)


def route_tiles_by_priority(manifest: pd.DataFrame, dem_names: list[str]) -> pd.DataFrame:
    if manifest.empty:
        return manifest

    selected: list[tuple[str, set[str]]] = []
    covered_union = None

    for dem_name in dem_names:
        ids = set(manifest.loc[manifest["dem_name"] == dem_name, "tile_id"].astype(str))
        if not ids:
            continue

        extents = get_global_dem_tile_extents(dem_name)
        extents = extents[extents["tile_id"].isin(ids)][["tile_id", "geometry"]].copy()
        if extents.empty:
            continue

        if covered_union is not None:
            overlaps = (
                extents.geometry.intersects(covered_union)
                & ~extents.geometry.touches(covered_union)
            )
            extents = extents[~overlaps]
            if extents.empty:
                continue

        keep_ids = set(extents["tile_id"].astype(str))
        selected.append((dem_name, keep_ids))

        new_union = extents.geometry.union_all()
        covered_union = new_union if covered_union is None else covered_union.union(new_union)

    if not selected:
        return manifest.iloc[0:0].copy()

    keep = pd.DataFrame(
        [(dem_name, tile_id) for dem_name, ids in selected for tile_id in ids],
        columns=["dem_name", "tile_id"],
    )
    return manifest.merge(keep, on=["dem_name", "tile_id"], how="inner")


def build_manifest(
    cells: dict[tuple[int, int], CellBucket],
    *,
    dem_names: list[str],
    buffer_m: float,
    exact: bool,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []

    for key, bucket in cells.items():
        if bucket.merged is None or bucket.merged.is_empty:
            continue

        try:
            buffered = buffer_geometry_m(bucket.merged, buffer_m)
        except Exception as exc:
            sys.stderr.write(f"[buffer_error] cell={key} err={exc}\n")
            continue
        if buffered.is_empty:
            continue

        bounds = list(buffered.bounds)
        for dem_name in dem_names:
            try:
                tiles = get_overlapping_dem_tiles(bounds, dem_name)
            except Exception as exc:
                sys.stderr.write(f"[tile_query_error] cell={key} dem={dem_name} err={exc}\n")
                continue
            if tiles.empty:
                continue
            if exact and "geometry" in tiles.columns:
                tiles = tiles[tiles.intersects(buffered)]
                if tiles.empty:
                    continue

            frame = tiles[["tile_id", "url"]].copy()
            frame["dem_name"] = dem_name
            frame["cell_x"] = key[0]
            frame["cell_y"] = key[1]
            rows.append(frame)

    if not rows:
        return pd.DataFrame(columns=["dem_name", "tile_id", "url", "cell_x", "cell_y"])

    manifest = pd.concat(rows, ignore_index=True)
    manifest = route_tiles_by_priority(manifest, dem_names)
    manifest = manifest.drop_duplicates(subset=["dem_name", "tile_id"])
    return manifest.sort_values(["dem_name", "tile_id"]).reset_index(drop=True)


def write_df(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(output_path, index=False)
        return
    if suffix == ".parquet":
        df.to_parquet(output_path, index=False)
        return
    raise ValueError(f"Unsupported format for {output_path}; use .csv or .parquet")


def download_tiles(
    manifest: pd.DataFrame,
    *,
    cache_root: Path,
    max_workers: int,
    overwrite: bool,
) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    if manifest.empty:
        sys.stderr.write("[download] manifest empty; nothing to do\n")
        return

    for dem_name in sorted(manifest["dem_name"].unique()):
        urls = sorted(set(manifest.loc[manifest["dem_name"] == dem_name, "url"]))
        dest = cache_root / dem_name
        dest.mkdir(parents=True, exist_ok=True)
        sys.stderr.write(f"[download] dem={dem_name} tiles={len(urls)} dest={dest}\n")
        download_tiles_to_gtiff(
            urls=urls,
            dem_name=dem_name,
            dest_dir=dest,
            max_workers_for_download=max_workers,
            overwrite_existing_tiles=overwrite,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build DEM tile manifest from Overpass nordic ways (cached HTTP).",
    )
    parser.add_argument(
        "--piste-types",
        default="nordic",
        help="Comma-separated piste:type values to match (default: nordic).",
    )
    parser.add_argument(
        "--bbox",
        default=None,
        help="Optional bbox 's,w,n,e'. Defaults to world.",
    )
    parser.add_argument(
        "--overpass-endpoint",
        default="https://overpass.kumi.systems/api/interpreter",
        help="Overpass API endpoint.",
    )
    parser.add_argument(
        "--overpass-timeout-s",
        type=int,
        default=300,
        help="Overpass server-side timeout in seconds.",
    )
    parser.add_argument(
        "--http-timeout-s",
        type=float,
        default=420.0,
        help="HTTP client timeout in seconds.",
    )
    parser.add_argument(
        "--overpass-cache",
        default=str(Path.home() / ".cache" / "skitur" / "overpass_nordic_ways"),
        help="requests-cache DB/path.",
    )
    parser.add_argument(
        "--overpass-cache-backend",
        default="sqlite",
        choices=["sqlite", "filesystem", "memory"],
        help="requests-cache backend.",
    )
    parser.add_argument(
        "--overpass-cache-expire-s",
        type=int,
        default=7 * 24 * 3600,
        help="Cache TTL seconds (<=0 means no expiry).",
    )
    parser.add_argument(
        "--overpass-user-agent",
        default="skitur-nordic-ways/1.0 (+local-run)",
        help="User-Agent header for Overpass requests.",
    )
    parser.add_argument(
        "--overpass-min-delay-s",
        type=float,
        default=0.8,
        help="Min politeness delay for uncached responses.",
    )
    parser.add_argument(
        "--overpass-max-delay-s",
        type=float,
        default=1.6,
        help="Max politeness delay for uncached responses.",
    )
    parser.add_argument(
        "--overpass-retries",
        type=int,
        default=3,
        help="Retry attempts for Overpass HTTP failures.",
    )

    parser.add_argument(
        "--dem-names",
        default="glo_30,3dep",
        help="DEM names in priority order.",
    )
    parser.add_argument(
        "--buffer-m",
        type=float,
        default=5000.0,
        help="Point buffer radius in meters.",
    )
    parser.add_argument(
        "--cell-deg",
        type=float,
        default=1.0,
        help="Coarse processing cell size in degrees.",
    )
    parser.add_argument(
        "--flush-every",
        type=int,
        default=1000,
        help="Flush pending points per cell every N features.",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Refine candidate tiles by true polygon intersection.",
    )

    parser.add_argument(
        "--out-locations",
        default="nordic_way_centers.csv",
        help="Output way centers (.csv/.parquet).",
    )
    parser.add_argument(
        "--out-manifest",
        default="nordic_way_tiles.csv",
        help="Output tile manifest (.csv/.parquet).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download tiles listed in manifest.",
    )
    parser.add_argument(
        "--cache-root",
        default=str(Path.home() / ".cache" / "skitur" / "dem"),
        help="Root directory for downloaded DEM files.",
    )
    parser.add_argument("--max-workers", type=int, default=20, help="Downloader worker count.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing tile files.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip writing outputs and downloads.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    piste_types = parse_csv_set(args.piste_types)
    dem_names = parse_csv_list(args.dem_names)
    if not piste_types:
        raise ValueError("`--piste-types` must contain at least one value")
    if not dem_names:
        raise ValueError("`--dem-names` must contain at least one value")

    bbox = parse_bbox(args.bbox)
    sys.stderr.write(
        "[overpass] "
        f"endpoint={args.overpass_endpoint} "
        f"bbox={bbox} "
        f"piste_types={sorted(piste_types)}\n"
    )

    cells, stats, locations = collect_way_centers(
        endpoint=args.overpass_endpoint,
        piste_types=piste_types,
        bbox=bbox,
        overpass_timeout_s=args.overpass_timeout_s,
        http_timeout_s=args.http_timeout_s,
        cache_name=args.overpass_cache,
        cache_backend=args.overpass_cache_backend,
        cache_expire_s=None if args.overpass_cache_expire_s <= 0 else args.overpass_cache_expire_s,
        user_agent=args.overpass_user_agent,
        min_delay_s=args.overpass_min_delay_s,
        max_delay_s=args.overpass_max_delay_s,
        retries=max(1, args.overpass_retries),
        cell_deg=args.cell_deg,
        flush_every=max(1, args.flush_every),
    )
    sys.stderr.write(
        "[overpass] "
        + json.dumps(
            {
                "request_count": stats.request_count,
                "uncached_request_count": stats.uncached_request_count,
                "retries": stats.retries,
                "network_seconds": round(stats.network_seconds, 3),
                "politeness_delay_seconds": round(stats.politeness_delay_seconds, 3),
                "response_bytes": stats.response_bytes,
                "elements_seen": stats.elements_seen,
                "elements_with_points": stats.elements_with_points,
                "cells": len(cells),
            },
            sort_keys=True,
        )
        + "\n"
    )

    manifest = build_manifest(
        cells,
        dem_names=dem_names,
        buffer_m=args.buffer_m,
        exact=args.exact,
    )
    counts = (
        manifest.groupby("dem_name")["tile_id"].nunique().to_dict()
        if not manifest.empty
        else {}
    )
    sys.stderr.write(
        "[manifest] "
        f"rows={len(manifest)} "
        f"tiles_by_dem={json.dumps(counts, sort_keys=True)}\n"
    )

    if args.dry_run:
        sys.stderr.write("[dry-run] skipping file writes and downloads\n")
        return 0

    out_locations = Path(args.out_locations)
    write_df(locations, out_locations)
    sys.stderr.write(f"[out] locations={out_locations} rows={len(locations)}\n")

    out_manifest = Path(args.out_manifest)
    write_df(manifest, out_manifest)
    sys.stderr.write(f"[out] manifest={out_manifest} rows={len(manifest)}\n")

    if args.download:
        download_tiles(
            manifest,
            cache_root=Path(args.cache_root),
            max_workers=args.max_workers,
            overwrite=args.overwrite,
        )
        sys.stderr.write("[download] complete\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
