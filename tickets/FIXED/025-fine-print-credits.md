# 025: Fine Print Section with Credits and Explanations
**Status:** FIXED (commit e4c3edc)
**Priority:** P2

## Description
Add fine print to the bottom: (c) 2026 Skitur, explanations/caveats for each metric, credits for libraries (DEM source, Leaflet, etc.).

## Fix
Added acknowledgements footer to the results page with:
- Python libraries: Flask, gpxpy, NumPy, SciPy, Matplotlib (with links)
- Frontend libraries: Leaflet, Plotly.js (with links)
- Elevation data: USGS 3DEP (US, 10m), NASA SRTM (global, 30m) via seamless-3dep & srtm.py (with links)

Styled as small, muted gray text centered at the bottom, only visible when results are displayed.

## Remaining (not done)
- Explanations/caveats for each metric (could be a separate ticket)
- Copyright notice
