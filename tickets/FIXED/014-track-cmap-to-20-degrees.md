# 014: Track Angle Colormap Extend to 20 Degrees
**Status:** FIXED
**Priority:** P1

## Description
Extend the track angle colormap to 20 degrees instead of 15. Keep all lower colors where they are - expand the red zone, don't shift green and yellow.

## Notes
- Current: TRACK_CMAP_ANCHORS maps 0-15 degrees
- Need to change to 0-20 degrees
- Keep 0-15 colors identical (rescale anchor positions from /15 to /20)
- Add dark red zone for 15-20 degrees
- Update slopeToColor(), legend, histogram, and slopes chart y-axis
- Must update both index.html and plot.py

## Fix Details
All changes preserve the existing 0-15 degree colors exactly by rescaling anchor positions from `/15` to `/20`, then adding new entries for the 15-20 degree range (very dark red transitioning to black).

### index.html changes:
1. **TRACK_CMAP_ANCHORS**: Rescaled all positions from `/15` to `/20`. Added two new anchors: `[15/20, [0.1, 0.0, 0.0]]` (near-black reddish), `[17/20, [0.05, 0.0, 0.0]]` (very dark red), and `[1.0, [0.0, 0.0, 0.0]]` (pure black at 20 degrees).
2. **slopeToColor()**: Updated clamp and divisor from 15 to 20.
3. **Elevation chart**: Updated `cmax: 15` to `cmax: 20` on the Plotly colorscale; slope bucketing limit changed from 15 to 20.
4. **Track legend**: Updated percent grade tick loop limit from 30% to 40%, degree tick loop from 0-15 to 0-20, and all position calculations from `/15` to `/20`.
5. **Histogram**: Changed gray threshold from `>= 15` to `>= 20` so the new range is properly colored.

### plot.py changes:
1. **_make_track_cmap()**: Same anchor rescaling from `/15` to `/20`, added two new dark red/black anchors for 15-20 degrees, updated `vmax=15` to `vmax=20`.
2. **_add_track_slope_colorbar()**: Updated major ticks to `[0, 5, 10, 15, 20]`, minor ticks range to `0-21`.
3. **plot_slope_histogram()**: Changed gray threshold from `>= 15` to `>= 20`.
