# 010: Map Should Fill All Space at Max Zoom-Out
**Status:** REOPENED
**Priority:** P1

## Description
The most zoomed out view should have all the space of the map filled, not showing the generated map as only partial. Gray borders are visible.

## Previous Attempts
1. `fitBounds` with negative padding — still leaves gray borders when aspect ratios mismatch
2. `getBoundsZoom(bounds, true)` — fills container but clips the tour data at edges
3. `fitBounds` with `padding: [20, 20]` — current code, shows full track but large gray borders

## What's Needed
The map container's aspect ratio needs to match the data's aspect ratio so there are no gray borders AND no clipping. The fix likely needs to dynamically resize the map container height to match the data bounds' aspect ratio.
