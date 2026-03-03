# 051: Investigate Topo Line Density for Flat Maps
**Status:** OPEN
**Priority:** P4

## Description
Investigate different contour line densities and use higher densities (smaller intervals) on especially flat maps, following traditional cartographic practice.

## Background
Traditional topo maps adjust contour interval based on terrain relief:
- **Mountainous terrain** (>1000m relief): 40m or 50m minor contours
- **Moderate terrain** (200-1000m): 20m minor contours
- **Flat terrain** (<200m relief): 5m or 10m minor contours
- **Very flat terrain** (<50m relief): 2m or even 1m contours

Currently our backend uses adaptive spacing based on relief (in `web.py:_compute_contours`):
- Relief > 3000ft: 200ft minor / 1000ft major
- Relief > 1500ft: 100ft minor / 500ft major
- Otherwise: default from CONTOUR_MINOR / CONTOUR_MAJOR

## Tasks
- Research USGS and European topo map standards for contour interval selection
- Test with flat-terrain GPX files (e.g., cross-country trails in the Midwest)
- Consider whether the current adaptive spacing thresholds need more granularity
- Ensure labels remain readable at higher densities (more lines = more label clutter)
