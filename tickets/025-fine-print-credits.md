# 025: Fine Print Section with Credits and Explanations
**Status:** OPEN  
**Priority:** P2

## Description
Add fine print to the bottom: (c) 2026 Skitur, explanations/caveats for each metric, credits for libraries (DEM source, Leaflet, etc.). Collect explanations programmatically (tagged comments or appended file). Credit: DEM data source/library, OpenTopo if used, Leaflet, Python/Flask/Jinja2/GPX library. Use standard tooling if possible. If not dynamic about which DEM source, add tooltip to "Elevation Profile" title.

## Notes
- Get project name from pyproject.toml or a constant
- Make it dynamic where practical
- Should explain what each metric means
