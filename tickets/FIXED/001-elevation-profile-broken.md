# 001: Elevation Profile Line Not Connected
**Status:** FIXED
**Priority:** P0

## Description
When zoomed in, the elevation profile plot is not connected nicely as one line. It looks broken/discontinuous.

## Root Cause
The elevation profile uses bucketed colored line segments grouped by slope. Each bucket is a separate Plotly trace with null-separated segments. When two adjacent points fall in different buckets, there's a visible gap between them.

## Fix
Added a continuous dark background line (`bgTrace`) underneath all colored segments. The bgTrace draws the full elevation profile as a single connected line in `#444` at the same width (6px), so gaps between bucketed color segments are filled by the dark line underneath.

Changed `Plotly.newPlot` from `[...lineTraces, hoverTrace]` to `[bgTrace, ...lineTraces, hoverTrace]`.

## Files Modified
- `skitur/templates/index.html` — `renderElevationChart()`: added bgTrace, included it first in traces array
