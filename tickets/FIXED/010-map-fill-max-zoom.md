# 010: Map Should Fill All Space at Max Zoom-Out
**Status:** FIXED (commit e4c3edc)
**Priority:** P1

## Description
The most zoomed out view should have all the space of the map filled, not showing the generated map as only partial. Gray borders are visible.

## Previous Attempts
1. `fitBounds` with negative padding — still leaves gray borders when aspect ratios mismatch
2. `getBoundsZoom(bounds, true)` — fills container but clips the tour data at edges
3. `fitBounds` with `padding: [20, 20]` — current code, shows full track but large gray borders
4. `moveend` clamp handler — worked for panning but not zoom-out
5. `maxBounds = gridBounds` — prevented zoom-out entirely
6. `maxBounds = gridBounds.pad(0.02)` — still prevented zoom-out

## Fix
Combined approach:
- **Square map container** (`aspect-ratio: 1/1`) + **square grid** (2.5x track extent) = no aspect ratio mismatch
- `fitBounds(gridBounds, {padding: [1,1]})` for minZoom = grid fills viewport with 1px safety margin
- `maxBounds = gridBounds` with `maxBoundsViscosity: 1.0` = hard drag stops during pan/inertia
- maxBounds temporarily cleared on `zoomstart`, re-set on `zoomend` = zoom-out to minZoom works freely
- `moveend` clamp as safety net: when viewport >= grid (minZoom), centers on grid instead of skipping
