# 049: Aspect Rose — Polar Heatmap of Ground Slope by Compass Direction
**Status:** OPEN
**Priority:** P2

## Description
Add a polar heatmap chart showing the distribution of ground slope angles by compass direction (aspect) for the terrain under the track. This is the #2 most important terrain factor in ski touring after slope angle, critical for avalanche assessment and snow quality prediction.

## Why Aspect Matters
- **North-facing slopes** (northern hemisphere): retain cold snow, more persistent weak layers (avy risk in cold conditions), better powder preservation
- **South-facing slopes**: more sun exposure, corn snow in spring, wet avalanche risk during warming
- **Every serious avy tool** (CalTopo, Skitourenguru, Fatmap) shows aspect
- We already have the DEM data — aspect is just `atan2(dz_dy, dz_dx)` from the same gradients we compute for slope angle

## Current Gap
The existing charts thoroughly cover **how steep** (slope profile, two distribution charts) but say nothing about **which direction** the terrain faces. A skier currently has no way to know "is my route mostly on north-facing avy terrain or south-facing corn?"

## Design: Polar Heatmap with Fixed Radial Axis

### Encoding
- **Angular axis** = compass direction (N, NE, E, SE, S, SW, W, NW) — which way the ground faces
- **Radial axis** = slope angle (0° at center, 45°+ at edge) — how steep
- **Color** = ground colormap (`groundSlopeRGBA`) — matches map shading and ground distribution chart
- **Opacity/intensity** = density (how much track distance is in each direction × slope bin)

### Why This Encoding

**Radial axis = slope angle (fixed mapping):**
The key design decision. In a stacked rose (where radius = cumulative count), the 30-45° avalanche band ends up at different radial positions in different directions because the safe terrain below it varies in height. This makes cross-direction comparison of avy terrain unfair — you're comparing arc lengths at different radii.

With a fixed radial mapping (radius always = slope angle), the 30-45° band is at the same position in every direction. You compare brightness/opacity at the same radius, which is visually fair. A dark red ring-segment at North vs a faint one at South immediately tells you "more steep north-facing terrain."

**Why not steep-at-center (inverted radial axis)?**
Considered putting avy terrain (30-45°) near the center for emphasis. Rejected because:
1. Center of polar chart has the least pixel area per unit — the important data gets compressed
2. Visual area grows with r², so outer = more visual weight = more emphasis on danger
3. Green core fading to red edges matches the natural safe→dangerous metaphor
4. Consistent with the ground colormap direction (low angle = green, high = red)

**Why not discrete bands (5-band stacked coxcomb)?**
Also considered stacking 5 discrete slope bands (0-25°, 25-30°, 30-35°, 35-45°, 45°+) in each wedge. Rejected because:
1. With stacking, the avy band radial position depends on the safe-terrain band height, making cross-direction comparison unfair
2. Loses the continuous colormap information
3. Arbitrary bin boundaries (why 5 bands and not 4 or 7?)

**Why not a polar scatter plot?**
Considered plotting each track point at (aspect, ground_slope) with semi-transparent dots. Rejected because:
1. With ~375 points, it could look messy
2. Sparse directions would have individual dots rather than a clear pattern
3. Harder to read density than a heatmap

### Other Design Options Considered (for avy comparison)

**Avy ring overlay:** Draw the full rose, then overlay a bold line at fixed radius r=30-45° whose thickness varies by direction. Fair comparison, but adds visual complexity to an already dense chart.

**Companion bar chart:** Simple 8-bar chart below the rose showing "% on avy terrain by aspect direction." Most readable for direct comparison, but requires a second chart for what should be one integrated view.

Both are bolt-on fixes for the stacked-bar encoding problem. The polar heatmap with fixed radial axis avoids the problem entirely.

## Implementation Plan

### Backend: Compute Aspect
In `web.py` or a shared module, compute ground aspect for each track point:
- Use the same `dz_dx`, `dz_dy` gradients from Horn's method (already computed for slope)
- `aspect = atan2(-dz_dy, dz_dx)` → compass bearing in degrees (0=N, 90=E, 180=S, 270=W)
- Add `ground_aspect` to each point in the track data JSON response

### Frontend: Plotly Polar Heatmap
Use Plotly's `barpolar` with fine-grained bins:
- 16 angular bins (N, NNE, NE, ENE, ...) for smooth directional resolution, or 8 for simplicity
- Many thin radial bins (1° or 2° slope increments) per direction
- Each bin segment colored by `groundColorStr(slope_midpoint)`
- Opacity proportional to `count / max_count` for that bin (normalized density)
- Fixed radial axis: r = slope angle, 0° center to 45° edge
- Angular labels: N, NE, E, SE, S, SW, W, NW

### Chart Placement
Add as a third chart in the bottom row alongside Track Slope Distribution and Ground Angle Distribution. May need to adjust the bottom charts layout to accommodate three charts (could go to a 3-column row or a 2+1 layout).

### Hover
Hover text: "NE, 32-33°: 4.2% of track" — direction, slope range, percentage.

## Data Requirements
- Need `ground_aspect` added to each track point (backend change)
- `ground_slope` already available per point
- `distance` per point for weighting by track distance (not just point count)

## Colormap
Must use the existing `groundSlopeRGBA()` function — same colors as the map shading, the ground legend bar, and the ground angle distribution chart. No new colormap.
