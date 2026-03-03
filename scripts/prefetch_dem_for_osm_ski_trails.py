#!/usr/bin/env python3
"""Build DEM tile manifests from OSM nordic/ski-touring trail geometry.

This script supports two source formats:
- OSM PBF extracts (`--pbf`) via pyosmium
- SQLite dump with `ways(raw_json)` records (`--sqlite`)

Default behavior is "dry run" in practice: if you omit `--download`, the script
only computes/writes a manifest and does not fetch DEM tiles.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyproj
import requests_cache
from shapely.geometry import LineString, shape
from shapely.ops import transform, unary_union

from dem_stitcher.datasets import get_global_dem_tile_extents, get_overlapping_dem_tiles
from dem_stitcher.stitcher import download_tiles_to_gtiff


GEOD = pyproj.Geod(ellps="WGS84")


def parse_csv_set(value: str) -> set[str]:
    """Parse a comma-separated string into a non-empty, stripped set."""
    return {part.strip() for part in value.split(",") if part.strip()}


def parse_csv_list(value: str) -> list[str]:
    """Parse a comma-separated string into a non-empty, stripped list."""
    return [part.strip() for part in value.split(",") if part.strip()]


def matches_way_tags(tags: dict[str, Any], piste_types: set[str], include_sport_tag: bool) -> bool:
    """Return True when tags identify an XC/touring way."""
    piste_type = tags.get("piste:type")
    if piste_type in piste_types:
        return True
    return bool(include_sport_tag and tags.get("sport") == "cross_country_skiing")


def matches_relation_tags(
    tags: dict[str, Any], piste_types: set[str], include_route_ski: bool
) -> bool:
    """Return True when relation tags identify a piste/touring route relation."""
    if tags.get("route") == "piste" and tags.get("piste:type") in piste_types:
        return True
    return bool(
        include_route_ski
        and tags.get("route") == "ski"
        and tags.get("piste:type") == "skitour"
    )


def geodesic_length_m(geometry: Any) -> float:
    """Compute geodesic length in meters for lon/lat geometry."""
    try:
        return float(GEOD.geometry_length(geometry))
    except Exception:
        return 0.0


def buffer_geometry_m(geometry: Any, radius_m: float) -> Any:
    """Buffer geometry by meters using a local azimuthal-equidistant CRS."""
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


@dataclass
class RelationStats:
    relations_seen: int = 0
    relations_matched: int = 0
    member_ways_collected: int = 0


@dataclass
class WayStats:
    ways_seen: int = 0
    ways_matched_tag: int = 0
    ways_matched_relation: int = 0
    ways_dropped_short: int = 0
    geometry_errors: int = 0
    segments_added: int = 0


@dataclass
class CellBucket:
    """Geometry accumulator for one coarse lat/lon cell."""

    pending: list[Any] = field(default_factory=list)
    merged: Any | None = None
    segments: int = 0

    def flush(self) -> None:
        """Union pending geometry into merged geometry."""
        if not self.pending:
            return
        pending_union = unary_union(self.pending)
        self.pending.clear()
        if self.merged is None:
            self.merged = pending_union
        else:
            self.merged = unary_union([self.merged, pending_union])


def cell_key(lon: float, lat: float, cell_deg: float) -> tuple[int, int]:
    """Return integer cell key for a lon/lat coordinate."""
    return (math.floor(lon / cell_deg), math.floor(lat / cell_deg))


def add_geometry_to_cells(
    geometry: Any,
    cells: dict[tuple[int, int], CellBucket],
    stats: WayStats,
    *,
    cell_deg: float,
    min_way_length_m: float,
    flush_every: int,
) -> None:
    """Filter and add one linestring-like geometry into the appropriate cell."""
    if geometry.is_empty:
        stats.geometry_errors += 1
        return
    if min_way_length_m > 0 and geodesic_length_m(geometry) < min_way_length_m:
        stats.ways_dropped_short += 1
        return

    rp = geometry.representative_point()
    key = cell_key(rp.x, rp.y, cell_deg)
    bucket = cells.setdefault(key, CellBucket())
    bucket.pending.append(geometry)
    bucket.segments += 1
    stats.segments_added += 1
    if len(bucket.pending) >= flush_every:
        bucket.flush()


def finalize_cells(cells: dict[tuple[int, int], CellBucket]) -> None:
    """Flush all pending geometry in all cells."""
    for bucket in cells.values():
        bucket.flush()


def merge_cell_maps(cell_maps: list[dict[tuple[int, int], CellBucket]]) -> dict[tuple[int, int], CellBucket]:
    """Merge multiple cell maps into one map."""
    merged: dict[tuple[int, int], CellBucket] = {}
    for cell_map in cell_maps:
        for key, src in cell_map.items():
            dst = merged.setdefault(key, CellBucket())
            if src.merged is not None and not src.merged.is_empty:
                if dst.merged is None:
                    dst.merged = src.merged
                else:
                    dst.merged = unary_union([dst.merged, src.merged])
            if src.pending:
                dst.pending.extend(src.pending)
            dst.segments += src.segments
    finalize_cells(merged)
    return merged


def collect_from_sqlite(
    sqlite_path: Path,
    *,
    piste_types: set[str],
    include_sport_tag: bool,
    cell_deg: float,
    min_way_length_m: float,
    flush_every: int,
) -> tuple[dict[tuple[int, int], CellBucket], RelationStats, WayStats]:
    """Collect trail geometry from sqlite ways(raw_json)."""
    cells: dict[tuple[int, int], CellBucket] = {}
    relation_stats = RelationStats()
    way_stats = WayStats()

    conn = sqlite3.connect(sqlite_path)
    try:
        cursor = conn.execute("SELECT raw_json FROM ways")
        for (raw_json,) in cursor:
            way_stats.ways_seen += 1
            try:
                obj = json.loads(raw_json)
            except Exception:
                way_stats.geometry_errors += 1
                continue

            tags = obj.get("tags") or {}
            if not isinstance(tags, dict):
                way_stats.geometry_errors += 1
                continue

            if not matches_way_tags(tags, piste_types, include_sport_tag):
                continue
            way_stats.ways_matched_tag += 1

            geom_raw = obj.get("geometry")
            if not isinstance(geom_raw, list) or len(geom_raw) < 2:
                way_stats.geometry_errors += 1
                continue

            coords: list[tuple[float, float]] = []
            for point in geom_raw:
                try:
                    lat = float(point["lat"])
                    lon = float(point["lon"])
                except Exception:
                    continue
                coords.append((lon, lat))
            if len(coords) < 2:
                way_stats.geometry_errors += 1
                continue

            add_geometry_to_cells(
                LineString(coords),
                cells,
                way_stats,
                cell_deg=cell_deg,
                min_way_length_m=min_way_length_m,
                flush_every=max(1, flush_every),
            )
    finally:
        conn.close()

    finalize_cells(cells)
    return cells, relation_stats, way_stats


def load_osmium() -> tuple[Any, Any]:
    """Import pyosmium lazily so sqlite-only runs do not require it."""
    try:
        import osmium
        from osmium.geom import GeoJSONFactory
    except Exception as exc:
        raise SystemExit(
            "pyosmium is required for --pbf inputs. Install with "
            "`./.venv/bin/pip install pyosmium`."
        ) from exc
    return osmium, GeoJSONFactory


def collect_from_pbf(
    pbf_path: Path,
    *,
    piste_types: set[str],
    include_sport_tag: bool,
    include_route_ski: bool,
    cell_deg: float,
    min_way_length_m: float,
    flush_every: int,
) -> tuple[dict[tuple[int, int], CellBucket], RelationStats, WayStats]:
    """Collect trail geometry from OSM PBF using pyosmium."""
    osmium, geojson_factory_cls = load_osmium()

    relation_stats = RelationStats()
    member_way_ids: set[int] = set()

    class RelationHandler(osmium.SimpleHandler):
        def relation(self, relation: Any) -> None:
            relation_stats.relations_seen += 1
            tags = dict(relation.tags)
            if not matches_relation_tags(tags, piste_types, include_route_ski):
                return
            relation_stats.relations_matched += 1
            for member in relation.members:
                if member.type == "w":
                    member_way_ids.add(member.ref)
                    relation_stats.member_ways_collected += 1

    RelationHandler().apply_file(str(pbf_path))

    cells: dict[tuple[int, int], CellBucket] = {}
    way_stats = WayStats()

    class WayHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.geojson_factory = geojson_factory_cls()

        def way(self, way: Any) -> None:
            way_stats.ways_seen += 1

            tags = dict(way.tags)
            matched_by_tag = matches_way_tags(tags, piste_types, include_sport_tag)
            matched_by_relation = way.id in member_way_ids
            if not (matched_by_tag or matched_by_relation):
                return

            if matched_by_tag:
                way_stats.ways_matched_tag += 1
            if matched_by_relation:
                way_stats.ways_matched_relation += 1

            try:
                geom_json = self.geojson_factory.create_linestring(way)
                geometry = shape(json.loads(geom_json))
            except Exception:
                way_stats.geometry_errors += 1
                return

            add_geometry_to_cells(
                geometry,
                cells,
                way_stats,
                cell_deg=cell_deg,
                min_way_length_m=min_way_length_m,
                flush_every=max(1, flush_every),
            )

    WayHandler().apply_file(str(pbf_path), locations=True)
    finalize_cells(cells)
    return cells, relation_stats, way_stats


def build_manifest(
    cells: dict[tuple[int, int], CellBucket],
    *,
    dem_names: list[str],
    buffer_m: float,
    exact: bool,
) -> pd.DataFrame:
    """Build a unique tile manifest from buffered cell geometry."""
    rows: list[pd.DataFrame] = []
    processed = 0
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

        processed += 1
        if processed % 50 == 0:
            sys.stderr.write(f"[tiles] processed_cells={processed}\n")

    if not rows:
        return pd.DataFrame(columns=["dem_name", "tile_id", "url", "cell_x", "cell_y"])

    manifest = pd.concat(rows, ignore_index=True)
    manifest = _route_tiles_by_priority(manifest, dem_names)
    manifest = manifest.drop_duplicates(subset=["dem_name", "tile_id"])
    manifest = manifest.sort_values(["dem_name", "tile_id"]).reset_index(drop=True)
    return manifest


def _route_tiles_by_priority(manifest: pd.DataFrame, dem_names: list[str]) -> pd.DataFrame:
    """Prefer earlier DEMs in dem_names where tile footprints overlap.

    Keeps all tiles from the highest-priority DEM. For lower-priority DEMs, keeps
    only tiles with positive-area footprint not already covered by higher-priority
    selected tiles. This avoids border artifacts from coarse cell-based routing.
    """
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
            # Keep fallback tiles only where they add coverage beyond boundaries.
            # A pure boundary touch is not considered overlap for routing.
            overlaps = extents.geometry.intersects(covered_union) & ~extents.geometry.touches(
                covered_union
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


def write_manifest(manifest: pd.DataFrame, output_path: Path) -> None:
    """Write manifest to CSV or Parquet based on filename extension."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        manifest.to_csv(output_path, index=False)
        return
    if suffix == ".parquet":
        manifest.to_parquet(output_path, index=False)
        return
    raise ValueError("`--out-manifest` must end with .csv or .parquet")


def download_tiles(
    manifest: pd.DataFrame,
    *,
    cache_root: Path,
    max_workers: int,
    overwrite: bool,
) -> None:
    """Download manifest URLs into `cache_root/<dem_name>/`."""
    cache_root.mkdir(parents=True, exist_ok=True)
    if manifest.empty:
        sys.stderr.write("[download] manifest is empty; nothing to do\n")
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


def print_summary(manifest: pd.DataFrame) -> None:
    """Print a compact manifest summary to stderr."""
    sys.stderr.write(f"[summary] rows={len(manifest)}\n")
    if manifest.empty:
        return
    counts = manifest.groupby("dem_name")["tile_id"].nunique().to_dict()
    sample = (
        manifest[["dem_name", "tile_id"]]
        .drop_duplicates()
        .head(20)
        .to_dict(orient="records")
    )
    sys.stderr.write(f"[summary] tile_counts_by_dem={json.dumps(counts, sort_keys=True)}\n")
    sys.stderr.write(f"[summary] sample_tile_ids={json.dumps(sample)}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build DEM tile manifests for OSM nordic/ski-tour trail networks.",
    )
    parser.add_argument("--pbf", action="append", default=[], help="Input OSM .pbf path. Repeatable.")
    parser.add_argument(
        "--sqlite",
        action="append",
        default=[],
        help="Input sqlite path with `ways(raw_json)` records. Repeatable.",
    )
    parser.add_argument(
        "--dem-names",
        default="glo_30,3dep",
        help="Comma-separated dem-stitcher datasets (default: glo_30,3dep).",
    )
    parser.add_argument(
        "--piste-types",
        default="nordic,skitour",
        help="Comma-separated piste:type values to keep (default: nordic,skitour).",
    )
    parser.add_argument(
        "--include-sport-tag",
        action="store_true",
        help="Also keep ways tagged sport=cross_country_skiing.",
    )
    parser.add_argument(
        "--include-route-ski",
        action="store_true",
        help="For PBF input, include route=ski relations tagged piste:type=skitour.",
    )
    parser.add_argument(
        "--min-way-length-m",
        type=float,
        default=0.0,
        help="Drop segments shorter than this length in meters.",
    )
    parser.add_argument(
        "--buffer-m",
        type=float,
        default=5000.0,
        help="Buffer radius around trail geometry in meters (default: 5000).",
    )
    parser.add_argument(
        "--cell-deg",
        type=float,
        default=1.0,
        help="Coarse processing cell size in degrees (default: 1.0).",
    )
    parser.add_argument(
        "--flush-every",
        type=int,
        default=500,
        help="Union pending geometry after N segments per cell (default: 500).",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Refine candidate tiles by true polygon intersection.",
    )
    parser.add_argument(
        "--out-manifest",
        default=None,
        help="Output manifest path (.csv/.parquet). Optional.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download tiles listed in manifest to cache directories.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute summary only; skip writing manifest and downloading.",
    )
    parser.add_argument(
        "--cache-root",
        default=str(Path.home() / ".cache" / "skitur" / "dem"),
        help="Root directory for downloaded tiles (default: ~/.cache/skitur/dem).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=20,
        help="Parallel download workers (default: 20).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing cached tile files when downloading.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    pbf_paths = [Path(path) for path in args.pbf]
    sqlite_paths = [Path(path) for path in args.sqlite]
    if not pbf_paths and not sqlite_paths:
        raise ValueError("Provide at least one input via --pbf and/or --sqlite")

    for path in pbf_paths + sqlite_paths:
        if not path.exists():
            raise FileNotFoundError(path)

    dem_names = parse_csv_list(args.dem_names)
    piste_types = parse_csv_set(args.piste_types)
    if not dem_names:
        raise ValueError("`--dem-names` must contain at least one dataset")
    if not piste_types:
        raise ValueError("`--piste-types` must contain at least one value")

    cell_maps: list[dict[tuple[int, int], CellBucket]] = []

    for pbf_path in pbf_paths:
        sys.stderr.write(f"[source:pbf] {pbf_path}\n")
        cells, relation_stats, way_stats = collect_from_pbf(
            pbf_path,
            piste_types=piste_types,
            include_sport_tag=args.include_sport_tag,
            include_route_ski=args.include_route_ski,
            cell_deg=args.cell_deg,
            min_way_length_m=args.min_way_length_m,
            flush_every=args.flush_every,
        )
        sys.stderr.write(
            "[pass1] "
            + json.dumps(
                {
                    "relations_seen": relation_stats.relations_seen,
                    "relations_matched": relation_stats.relations_matched,
                    "member_ways_collected": relation_stats.member_ways_collected,
                },
                sort_keys=True,
            )
            + "\n"
        )
        sys.stderr.write(
            "[pass2] "
            + json.dumps(
                {
                    "ways_seen": way_stats.ways_seen,
                    "ways_matched_tag": way_stats.ways_matched_tag,
                    "ways_matched_relation": way_stats.ways_matched_relation,
                    "ways_dropped_short": way_stats.ways_dropped_short,
                    "geometry_errors": way_stats.geometry_errors,
                    "segments_added": way_stats.segments_added,
                    "cells": len(cells),
                },
                sort_keys=True,
            )
            + "\n"
        )
        cell_maps.append(cells)

    for sqlite_path in sqlite_paths:
        sys.stderr.write(f"[source:sqlite] {sqlite_path}\n")
        cells, relation_stats, way_stats = collect_from_sqlite(
            sqlite_path,
            piste_types=piste_types,
            include_sport_tag=args.include_sport_tag,
            cell_deg=args.cell_deg,
            min_way_length_m=args.min_way_length_m,
            flush_every=args.flush_every,
        )
        sys.stderr.write(
            "[sqlite] "
            + json.dumps(
                {
                    "relations_seen": relation_stats.relations_seen,
                    "relations_matched": relation_stats.relations_matched,
                    "ways_seen": way_stats.ways_seen,
                    "ways_matched_tag": way_stats.ways_matched_tag,
                    "ways_dropped_short": way_stats.ways_dropped_short,
                    "geometry_errors": way_stats.geometry_errors,
                    "segments_added": way_stats.segments_added,
                    "cells": len(cells),
                },
                sort_keys=True,
            )
            + "\n"
        )
        cell_maps.append(cells)

    merged_cells = merge_cell_maps(cell_maps)
    sys.stderr.write(f"[cells] merged={len(merged_cells)}\n")

    manifest = build_manifest(
        merged_cells,
        dem_names=dem_names,
        buffer_m=args.buffer_m,
        exact=args.exact,
    )
    print_summary(manifest)

    if args.dry_run:
        sys.stderr.write("[dry-run] skipping manifest write and downloads\n")
        return 0

    if args.out_manifest:
        out_path = Path(args.out_manifest)
        write_manifest(manifest, out_path)
        sys.stderr.write(f"[out] manifest={out_path} rows={len(manifest)}\n")

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
