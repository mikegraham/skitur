#!/usr/bin/env python3
"""Pre-seed the DEM tile cache by nordic trail density.

Queries OpenStreetMap for nordic/skitour piste ways, bins them into 1x1 degree
tiles, and downloads DEMs in order of trail density (US trails weighted 2x).

Usage:
    .venv/bin/python scripts/preseed_dem_cache.py                  # show plan
    .venv/bin/python scripts/preseed_dem_cache.py --download        # download all
    .venv/bin/python scripts/preseed_dem_cache.py --max-gb 30       # cap at 30GB
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections import Counter
from pathlib import Path

import requests

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TRAIL_CACHE = Path("_preseed_trail_counts.json")

# Approximate tile sizes on disk (measured from existing cache).
TILE_SIZE_3DEP_MB = 435
TILE_SIZE_GLO30_MB = 25



def _is_us_coverage(lat: float, lon: float) -> bool:
    """Check if location is within approximate US 3DEP coverage."""
    if 24 <= lat <= 50 and -125 <= lon <= -66:
        return True
    if 51 <= lat <= 72 and -180 <= lon <= -129:
        return True
    if 18 <= lat <= 23 and -161 <= lon <= -154:
        return True
    return False


def _tile_source(lat: int, lon: int) -> str:
    """Return dem-stitcher source name for a tile."""
    return "3dep" if _is_us_coverage(lat + 0.5, lon + 0.5) else "glo_30"


def _tile_size_mb(lat: int, lon: int) -> float:
    return TILE_SIZE_3DEP_MB if _tile_source(lat, lon) == "3dep" else TILE_SIZE_GLO30_MB


def _tile_region(lat: int, lon: int) -> str:
    """Rough region label for a 1x1 degree tile."""
    # North America
    if 24 <= lat <= 50 and -125 <= lon <= -104:
        if 48 <= lat: return "BC/Alberta"
        if 45 <= lat and -125 <= lon <= -120: return "OR/WA Cascades"
        if 45 <= lat and -115 <= lon <= -110: return "MT/ID"
        if 43 <= lat <= 45 and -112 <= lon <= -109: return "Yellowstone/Tetons"
        if 38 <= lat <= 41 and -112 <= lon <= -104: return "CO/UT Rockies"
        if 35 <= lat <= 38 and -112 <= lon <= -105: return "NM/AZ"
        if 38 <= lat <= 42 and -122 <= lon <= -119: return "Tahoe/Sierra"
        return "Western US"
    if 24 <= lat <= 50 and -104 <= lon <= -80:
        if 44 <= lat and -96 <= lon <= -89: return "MN/WI"
        if 41 <= lat <= 44 and -90 <= lon <= -82: return "MI/Great Lakes"
        return "Central US"
    if 24 <= lat <= 55 and -80 <= lon <= -50:
        if 45 <= lat and -77 <= lon <= -74: return "Ottawa/Laurentians"
        if 43 <= lat <= 45 and -77 <= lon <= -73: return "Adirondacks"
        if 43 <= lat <= 45 and -73 <= lon <= -69: return "VT/NH/ME"
        if 45 <= lat and -80 <= lon <= -72: return "Quebec/Ontario"
        return "Eastern US/Canada"
    if 51 <= lat <= 72 and -180 <= lon <= -129:
        return "Alaska"

    # Scandinavia
    if 57 <= lat <= 72 and 4 <= lon <= 32:
        if lon <= 13: return "Norway"
        if lon <= 20: return "Sweden"
        return "Finland"
    # Russia
    if 50 <= lat <= 75 and 30 <= lon <= 180:
        return "Russia"
    # Alps + Central Europe
    if 45 <= lat <= 48 and 5 <= lon <= 8:
        return "Swiss Alps"
    if 46 <= lat <= 48 and 9 <= lon <= 13:
        return "Austrian Alps"
    if 44 <= lat <= 46 and 6 <= lon <= 8:
        return "French Alps"
    if 44 <= lat <= 47 and 10 <= lon <= 13:
        return "Italian Alps/Dolomites"
    if 49 <= lat <= 52 and 5 <= lon <= 16:
        return "Germany"
    if 48 <= lat <= 51 and 12 <= lon <= 19:
        return "Czech Republic"
    if 47 <= lat <= 50 and 8 <= lon <= 14:
        return "S. Germany/Bavaria"
    if 45 <= lat <= 49 and 13 <= lon <= 23:
        return "E. Europe/Balkans"
    # Nordic-adjacent
    if 54 <= lat <= 58 and 8 <= lon <= 16:
        return "Denmark/N. Germany"
    if 53 <= lat <= 62 and -10 <= lon <= 3:
        return "UK/Scotland"
    # Japan
    if 30 <= lat <= 46 and 128 <= lon <= 146:
        return "Japan"
    # New Zealand
    if -48 <= lat <= -34 and 166 <= lon <= 178:
        return "New Zealand"
    # Patagonia
    if -55 <= lat <= -35 and -76 <= lon <= -66:
        return "Patagonia"

    if 35 <= lat <= 55 and -10 <= lon <= 40:
        return "Europe"
    return ""


def query_piste_centers() -> list[tuple[float, float]]:
    """Query OSM Overpass for center points of all nordic/skitour piste ways."""
    query = """\
[out:json][timeout:600];
(
  way["piste:type"~"^(nordic|skitour)$"];
  relation["piste:type"~"^(nordic|skitour)$"];
);
out center;
"""
    print("Querying Overpass API for nordic/skitour piste ways...")
    print("  (this may take a few minutes for the first run)")
    t0 = time.perf_counter()
    resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=660)
    resp.raise_for_status()
    dt = time.perf_counter() - t0
    data = resp.json()

    centers = []
    for element in data["elements"]:
        if "center" in element:
            centers.append((element["center"]["lat"], element["center"]["lon"]))
    print(f"  Got {len(centers)} piste features in {dt:.1f}s")
    return centers


def bin_to_tiles(
    centers: list[tuple[float, float]],
) -> list[tuple[tuple[int, int], int]]:
    """Bin (lat, lon) centers into 1x1 degree tiles, return sorted by count desc."""
    tile_counts: Counter[tuple[int, int]] = Counter()
    for lat, lon in centers:
        tile = (math.floor(lat), math.floor(lon))
        tile_counts[tile] += 1
    return tile_counts.most_common()


def load_or_query_tiles(
    no_cache: bool = False,
) -> list[tuple[tuple[int, int], int]]:
    """Load cached tile counts or query Overpass and cache the result."""
    if not no_cache and TRAIL_CACHE.exists():
        data = json.loads(TRAIL_CACHE.read_text())
        tiles = [((t[0], t[1]), t[2]) for t in data]
        print(f"Loaded {len(tiles)} tiles from {TRAIL_CACHE}")
        return tiles

    centers = query_piste_centers()
    tiles = bin_to_tiles(centers)

    # Cache for re-runs
    cache_data = [[lat, lon, count] for (lat, lon), count in tiles]
    TRAIL_CACHE.write_text(json.dumps(cache_data))
    print(f"Cached tile counts to {TRAIL_CACHE}")
    return tiles


def prioritize_tiles(
    tiles: list[tuple[tuple[int, int], int]],
    max_gb: float | None,
) -> list[tuple[tuple[int, int], int]]:
    """Sort by trail count descending, then apply disk budget."""
    tiles = sorted(tiles, key=lambda t: t[1], reverse=True)

    if max_gb is not None:
        budget_mb = max_gb * 1024
        cumulative = 0.0
        for i, ((lat, lon), _count) in enumerate(tiles):
            cumulative += _tile_size_mb(lat, lon)
            if cumulative > budget_mb:
                tiles = tiles[:i]
                break

    return tiles


def download_tile(lat_floor: int, lon_floor: int, cache_dir: Path) -> bool:
    """Download the single DEM source tile for a 1x1 degree cell.

    Uses get_dem_tile_paths to download the tile directly to the cache dir,
    skipping stitching and reprojection entirely.
    """
    from dem_stitcher.stitcher import get_dem_tile_paths

    source_name = _tile_source(lat_floor, lon_floor)
    tile_dir = cache_dir / source_name
    tile_dir.mkdir(parents=True, exist_ok=True)

    # Exact 1x1 degree bounds — no padding, matches the source tile exactly.
    bounds = [float(lon_floor), float(lat_floor),
              float(lon_floor + 1), float(lat_floor + 1)]

    try:
        t0 = time.perf_counter()
        get_dem_tile_paths(
            bounds=bounds,
            dem_name=source_name,
            localize_tiles_to_gtiff=True,
            tile_dir=tile_dir,
        )
        dt = time.perf_counter() - t0
        print(f"    downloaded ({source_name}) in {dt:.1f}s")
        return True
    except Exception as e:
        print(f"    FAILED: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Actually download tiles (default is dry-run showing the plan).",
    )
    parser.add_argument(
        "--max-gb", type=float, default=None,
        help="Stop after approximately this many GB of estimated disk use.",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Re-query Overpass even if cached trail counts exist.",
    )
    parser.add_argument(
        "--cache-dir", type=Path,
        default=Path.home() / ".cache" / "skitur" / "dem",
        help="DEM tile cache directory.",
    )
    args = parser.parse_args()

    tiles = load_or_query_tiles(no_cache=args.no_cache)
    grand_total_trails = sum(c for _, c in tiles)
    tiles = prioritize_tiles(tiles, max_gb=args.max_gb)

    # Print table
    total_mb = 0.0
    cumul_trails = 0
    print(f"\n{'Rank':>4}  {'Tile':>12}  {'Trails':>7}  {'Cover':>6}  {'Source':>6}  {'~Size':>6}  {'Cumul':>7}  Region")
    print("-" * 95)

    for i, ((lat, lon), count) in enumerate(tiles[:60], 1):
        source = _tile_source(lat, lon)
        size_mb = _tile_size_mb(lat, lon)
        total_mb += size_mb
        cumul_trails += count
        region = _tile_region(lat, lon)
        if total_mb < 1024:
            cumul_disk = f"{total_mb:.0f}MB"
        else:
            cumul_disk = f"{total_mb / 1024:.1f}GB"
        pct = cumul_trails / grand_total_trails * 100 if grand_total_trails else 0
        print(f"{i:>4}  ({lat:>3},{lon:>5})  {count:>7}  {pct:>5.1f}%  {source:>6}  {size_mb:>4.0f}MB  {cumul_disk:>7}  {region}")

    if len(tiles) > 60:
        for (lat, lon), count in tiles[60:]:
            total_mb += _tile_size_mb(lat, lon)
            cumul_trails += count
        pct = cumul_trails / grand_total_trails * 100 if grand_total_trails else 0
        print(f"  ... and {len(tiles) - 60} more tiles ({pct:.1f}% coverage)")

    total = len(tiles)
    total_trails = sum(c for _, c in tiles)
    n_3dep = sum(1 for (lat, lon), _ in tiles if _tile_source(lat, lon) == "3dep")
    n_glo30 = total - n_3dep

    print(f"\nTotal: {total} tiles ({n_3dep} 3DEP, {n_glo30} GLO-30)")
    print(f"Trails covered: {total_trails} / {grand_total_trails} ({total_trails / grand_total_trails * 100:.0f}%)")
    print(f"Estimated disk: {total_mb / 1024:.1f} GB")

    if not args.download:
        print("\nDry run. Pass --download to actually fetch tiles.")
        return 0

    print(f"\nDownloading {total} tiles...\n")
    ok = 0
    failed = 0
    used_mb = 0.0
    for i, ((lat, lon), count) in enumerate(tiles, 1):
        region = _tile_region(lat, lon)
        label = f" ({region})" if region else ""
        print(f"[{i}/{total}] ({lat}, {lon}) - {count} trails{label}")
        if download_tile(lat, lon, args.cache_dir):
            ok += 1
            used_mb += _tile_size_mb(lat, lon)
        else:
            failed += 1
        if i % 10 == 0:
            print(f"  -- progress: {ok} ok, {failed} failed, ~{used_mb / 1024:.1f} GB used --")

    print(f"\nDone: {ok} downloaded, {failed} failed, ~{used_mb / 1024:.1f} GB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
