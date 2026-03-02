# 003: Ground Colormap Still Smoothed
**Status:** FIXED
**Priority:** P0

## Description
The ground colormap transitions at 30 and 45 degrees are still smoothed/interpolated. The hard transitions need to be unmistakable.

## Root Cause
Bilinear interpolation averages slope values from 4 neighboring grid cells. When cells on opposite sides of a boundary (e.g., 27° and 33°) are interpolated, the result (~30°) gets a blended color. The previous ±2° snap heuristic was too narrow and still allowed gradual transitions.

## Fix
Replaced snap heuristic with nearest-neighbor sampling at boundaries:
- Check if the 4 corner cells' min/max span a critical boundary (30° or 45°)
- If they do → use nearest-neighbor (pick the closest cell's value, no blending)
- If they don't → bilinear interpolation is safe (all cells are in the same color zone)

This produces perfectly crisp color transitions at 30° and 45° while keeping smooth rendering within each zone.

## Files Modified
- `skitur/templates/index.html` — `SlopeGridLayer._redraw()`
