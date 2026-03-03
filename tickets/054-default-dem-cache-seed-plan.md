# 054: Default DEM Cache Seed Plan
**Status:** OPEN  
**Priority:** P1

## Goal
Build a deterministic "day-0" DEM tile cache so launch traffic avoids cold DEM downloads for common XC/ski-tour regions.

## Scope
- Open-data-first coverage using OSM winter trail geometry (`piste:type=nordic` and optional `skitour`).
- Generate manifest first, then download tiles into `~/.cache/skitur/dem/<dem_name>/`.
- Keep US/non-US source selection simple: `3dep` + `glo_30`.

## Inputs
1. Primary: OSM data (`.osm.pbf` regional extracts or sqlite `ways(raw_json)` dump).
2. Optional ranking overlays (later): Nordic Pulse location catalog, tourism trail portals, club GPX feeds.

## Launch Defaults
1. Trail filters:
   `piste:type in {nordic, skitour}`; optional `sport=cross_country_skiing`.
2. Buffer:
   `3000m` around clustered trail geometry.
3. Batching:
   `1.0` degree cell bins.
4. DEMs:
   `glo_30,3dep`.
5. Run mode:
   Manifest-only first; download only after manifest review.

## Execution Plan
1. Build manifest from OSM source:
   `./.venv/bin/python scripts/prefetch_dem_for_osm_ski_trails.py --sqlite /path/to/osm.db --dem-names glo_30,3dep --buffer-m 3000 --out-manifest manifests/seed_tiles.csv`
2. Validate manifest size/composition:
   - tile count by `dem_name`
   - geographic sanity spot-check
3. Fill cache:
   `./.venv/bin/python scripts/prefetch_dem_for_osm_ski_trails.py --sqlite /path/to/osm.db --dem-names glo_30,3dep --buffer-m 3000 --out-manifest manifests/seed_tiles.csv --download --cache-root ~/.cache/skitur/dem`
4. Snapshot cache metadata:
   - generated timestamp
   - source dataset(s)
   - manifest checksum
   - tile counts per DEM
5. Bake/ship:
   - include cache artifact in deploy image or attach at boot from object storage.

## Session Notes (2026-03-03)
- Pipeline scripts in active use: `scripts/prefetch_dem_for_osm_ski_trails.py`, `scripts/build_nordic_way_tile_manifest.py`, `scripts/visualize_nordic_way_coverage.py`.
- Dry run is the default: no `--download` keeps it manifest-only; `--dry-run` skips writes/downloads entirely.
- Relation-only OSM extraction undercounts global coverage: `9,075` relation centers vs `172,951` way centers observed; way-level extraction is required.
- Tile routing now prefers higher-priority DEMs by real footprint overlap, keeping fallback tiles only where they add uncovered area near dataset boundaries.
- Session artifacts: relation-only tiles `567` (`glo_30`), way-based tiles `2,673` (`glo_30`), runtime seed estimate `226` data rows (`227` with header) in `nordic_seed_tiles_runtime*.csv`.
- Recommended rollout: phase A relation-only seed, phase B way-level global seed, phase C optional ranking overlays after licensing review.
- Related product note (`#034` + `#037`): if we add a no-shading view, it should use an off-the-shelf basemap layer (OSM/OpenTopo-style), not a custom no-shading rendering path.

## Guardrails
1. Do not ingest proprietary trail geometry without license (AllTrails/Strava raw segments/etc).
2. Keep OSM attribution in documentation and user-facing credits.
3. Rebuild manifests with pinned parameters so cache updates are reproducible.

## Success Metrics
1. >90% DEM tile hit rate on first request for target launch regions.
2. P95 first-analysis latency reduced vs cold start baseline.
3. Zero tile download failures in startup smoke tests.
