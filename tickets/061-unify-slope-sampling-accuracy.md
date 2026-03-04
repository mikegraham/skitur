# 061: Unify Slope Sampling Pipeline (Current Version Harms Accuracy)
**Status:** OPEN  
**Priority:** P1

## Description
The biggest problem in the current implementation is **accuracy**.

Today we compute ground slope in two different ways:
1. `get_ground_slopes(...)` (used by analysis/scoring): interpolate elevations at offset points, then compute Horn gradients.
2. `get_slope_grid(...)` (used by map rendering): compute Horn gradients on native DEM cells first, then interpolate slope values.

Because these pipelines are different, the same location can get different slope values depending on which code path asks for it. That creates inconsistent hazard/quality evaluation versus what the map shows, and can understate or distort steepness in sensitive terrain.

This should be treated as a P1 correctness issue, not just a performance cleanup.

## Why This Is P1
1. Slope drives avalanche exposure and quality scoring, so bias here directly affects safety-related outputs.
2. Track-level stats and rendered map can disagree for the same coordinate.
3. Interpolate-then-differentiate is more smoothing-prone than differentiate-then-interpolate for DEM terrain analysis.

## Proposed Direction (Refactor, No Hidden Sidecar State)
1. Create one explicit terrain-derivative representation per loaded DEM extent (native-resolution slope/aspect gradients).
2. Sample from that shared representation for:
   - track point slope/aspect queries,
   - score-time slope lookups,
   - map slope grid generation.
3. Pass this representation through explicit function boundaries (analysis context / terrain field object), rather than attaching implicit sidecar data to module globals.

## Acceptance Criteria
1. There is one authoritative slope/aspect computation path in production code.
2. For identical `(lat, lon)` queries, analysis/score/map sampling agrees within a defined tolerance.
3. The interpolate-elevation-then-differentiate path is removed from mainline ground-slope computation.
4. Tests cover:
   - cross-caller consistency at shared coordinates,
   - behavior near tile edges and NaN regions,
   - regression on representative GPX fixtures.
5. Runtime remains within an acceptable bound versus current warm-cache baseline.
6. Interpolation boundary behavior is explicit and tested (no silent edge-value smearing).

## Boundary Handling Note (`map_coordinates`)
Current code frequently uses `map_coordinates(..., order=1, mode='nearest')`.
That can silently smear edge values when sample points fall just outside valid DEM support.

As part of this refactor, evaluate switching slope/elevation sampling to:
- `mode='constant', cval=np.nan` (or equivalent explicit out-of-bounds masking),
- then propagate/handle NaNs intentionally at scoring/report boundaries.

Goal: convert boundary ambiguity into explicit missing-data handling, reducing silent accuracy errors near edges and nodata zones.

## Notes
This ticket is intentionally scoped to correctness and consistency first. Performance improvements from reusing shared derivative fields are a secondary benefit.
