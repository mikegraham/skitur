#!/bin/sh
set -e

CACHE_DIR="/data/dem"
SEED_MARKER="$CACHE_DIR/.seeded"

# On first boot with a persistent volume, pre-fetch DEM tiles for
# high-traffic areas so users don't wait for cold fetches.
if [ ! -f "$SEED_MARKER" ]; then
    echo "First boot — seeding DEM cache at $CACHE_DIR"
    mkdir -p "$CACHE_DIR"
    python scripts/preseed_dem_cache.py --download --max-gb 24 --cache-dir "$CACHE_DIR" || true
    touch "$SEED_MARKER"
    echo "DEM seeding complete"
fi

exec "$@"
