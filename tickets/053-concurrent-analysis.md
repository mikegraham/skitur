# 053: Improve analysis latency via concurrency

**Priority:** P1
**Status:** Open

## Problem

The first analysis of a GPX file (cold cache) takes ~9-11s end-to-end. The DEM download dominates (~4-8s), but there may be opportunities to overlap independent operations.

## Investigation areas

1. **DEM download vs GPX parsing**: Can we parse/analyze the GPX track while the DEM is downloading? Currently these are sequential.

2. **Slope grid + contours**: `compute_map_grids()` computes elevation grid, slope grid, and contour grid sequentially. Could slope and contour grids be computed in parallel (both depend on the elevation grid but not on each other)?

3. **Two stitch_dem calls**: For the first request, `load_dem_for_bounds` is called twice — once in `_compute_analysis` (padding=0.02) and once more implicitly when the grid bounds differ. Could we predict the full bounds upfront?

4. **dem-stitcher internals**: The library shows "Opening Datasets" (~2-3s) and "Reading tile imagery" (~1-2s) as separate stages. Is there a way to pipeline or parallelize tile reads?

5. **Async stitch_dem**: dem-stitcher supports `stitch_dem_asyncio()` — could we use that with `asyncio.gather` to overlap I/O with computation?

## Approach

Use `cProfile` to identify the actual hotspots in the cold-cache path. Profile `_compute_analysis()` directly to see where time is spent and which operations could overlap.

## Benchmark reference (dem-stitcher migration)

| Scenario | Cold (first req) | Warm (cached) |
|----------|------------------|---------------|
| US (3DEP, Twin Lakes) | ~11s | 0.08s |
| Japan (GLO-30, Okuteine) | ~8s | 0.06s |
