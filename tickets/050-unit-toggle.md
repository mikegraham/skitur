# 050: Allow Choice of mi/ft or km/m Units
**Status:** OPEN
**Priority:** P2

## Description
Add a toggle switch on the rendered page to switch between imperial (mi/ft) and metric (km/m) units. The switch should update all displayed values without re-running analysis.

## Scope
- **Easy**: Chart axes (elevation profile, slope profile tick suffixes), stats table values, hover tooltips
- **Medium**: Contour line labels (currently show ft values from backend)
- **Hard**: Contour line *intervals* — metric topo maps traditionally use round meter intervals (e.g. 50m, 100m) rather than converted foot values

## Contour Lines in Metric
Traditional metric topo maps use:
- **Minor contours**: 10m or 20m intervals (depending on terrain steepness)
- **Index/major contours**: every 5th line (50m or 100m)
- In steep terrain (Alps, Himalayas): 20m minor / 100m major
- In moderate terrain: 10m minor / 50m major

This means metric contours can't just be imperial contours with converted labels — the *intervals themselves* differ. Options:
1. **Simple**: Convert ft labels to m labels (non-round values like "1219m" instead of "1200m")
2. **Proper**: Re-generate contours at metric intervals from the backend (requires backend changes or sending raw elevation grid)
3. **Hybrid**: Frontend re-contours from the elevation grid data (complex, performance concerns)

## Implementation Notes
- Store a global `units` variable ('imperial' | 'metric')
- Toggle button in header bar area
- On toggle: re-render charts with new units, update stats table, update contour labels
- Backend already sends metric data (meters); imperial conversion happens in frontend
