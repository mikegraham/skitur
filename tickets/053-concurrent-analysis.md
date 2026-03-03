# 053: Improve analysis latency via concurrency

**Priority:** P1
**Status:** Open (partially addressed)

## Problem

The first analysis of a GPX file (cold cache) takes ~3-6s end-to-end. The DEM download dominates — specifically the "Opening Datasets" phase (~2s botocore credential resolution) and "Reading tile imagery" (~1-2s S3 download).

## Completed: Single DEM fetch

**Issue #3 is fixed.** `_compute_analysis` now pre-computes the union of track bounds and grid bounds, making a single `load_dem_for_bounds` call that covers both. This eliminated a redundant `stitch_dem` call that cost ~2-3s per request (botocore + tile re-read).

Before: US ~15s, Japan ~8s (2 stitch_dem calls)
After: US ~3.5s, Japan ~2.1s (1 stitch_dem call)

## Remaining investigation areas

1. **"Opening Datasets" bottleneck (~2s)**: botocore credential resolution dominates every stitch_dem call. This is a fixed-cost floor. Options:
   - Pre-warm credentials on server startup
   - Use `stitch_dem_asyncio()` to start credential resolution earlier
   - Cache the S3 client/session between requests

2. **Slope grid + contours**: `compute_map_grids()` computes elevation grid, slope grid, and contour grid sequentially. Slope and contour grids could be computed in parallel (both depend on the elevation grid but not on each other). Small win (~50ms).

3. **Async stitch_dem**: dem-stitcher supports `stitch_dem_asyncio()` — could we use that with `asyncio.gather` to overlap I/O? Not clear this helps with a single tile, but for multi-tile fetches it could.

4. **DEM download vs GPX parsing**: GPX parsing is ~1ms, so overlapping it with DEM download wouldn't help.

## Benchmark reference (after single-fetch fix)

| Scenario | Fully Cold (1st in process) | Fully Cold (subsequent) | Disk-Warm | Memory-Warm |
|----------|----------------------------|------------------------|-----------|-------------|
| US (3DEP, Twin Lakes) | ~5.8s | ~3.5s | ~3.4s | 0.06s |
| Japan (GLO-30, Okuteine) | ~6.3s | ~2.1s | ~2.1s | 0.04s |

The ~2s "Opening Datasets" (botocore) phase is the remaining floor for any cold request.
