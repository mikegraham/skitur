#!/bin/sh
set -e

CACHE_DIR="/data/dem"

# Seed DEM tiles in background on every boot until disk budget is filled.
# Already-downloaded tiles are skipped by dem-stitcher, so this is a no-op
# once the cache is full. Runs in background so gunicorn starts immediately.
mkdir -p "$CACHE_DIR"
(python scripts/preseed_dem_cache.py --download --max-gb 24 --cache-dir "$CACHE_DIR") &

exec "$@"
