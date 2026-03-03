# 034: Named Features on Map
**Status:** OPEN  
**Priority:** P2

## Description
Is there a good way to add named stuff like peaks, lakes, etc.? And line features like rivers, trails, roads? Should be minimal, not reinventing the wheel. Rivers and peaks seem especially nice. Peaks should be easy enough, but drawing rivers could be trickier.

## Research Notes
- GNIS database for US peaks/features
- OpenStreetMap Overpass API for querying by bounding box
- Could bundle a small dataset for common areas

## Findings (2026-03-03)
1. `#034` and `#037` are tightly related:
   - named feature readability drops when slope shading + contours + labels are all on at once
   - feature labels are much clearer on a standard basemap without custom slope shading.
2. Lowest-complexity path:
   - add a standard off-the-shelf basemap mode (OSM/OpenTopo-style) for "features context"
   - keep current custom slope-shaded map as the analysis mode.
3. Data source for dynamic named features:
   - OSM Overpass by current map bbox is feasible for point/line feature overlays
   - must be cached server-side and bounded to avoid latency/rate-limit issues.
4. GNIS is useful for US-only enrichment, but OSM is the better global default and aligns with existing ski-trail sourcing.

## Recommended Scope (Phase 1)
1. Add map mode toggle:
   - Analysis: current slope shading + contours + track
   - Context: standard basemap + track + optional contours
2. Add lightweight named overlays from OSM:
   - points: `natural=peak`, major `place=*`
   - water: `natural=water`, `waterway=river/stream`
3. Defer dense linear features (all roads/trails) until we add zoom-dependent filtering/clustering.

## Implementation Notes
1. Query only within visible map bounds (or track bbox pad), with max-area guardrails.
2. Cache responses by rounded bbox + zoom bucket + feature set.
3. Keep rendering non-interactive by default (minimal tooltips only) to avoid clutter.

## Availability Snapshot (2026-03-03)
1. OSMF standard tile server (`tile.openstreetmap.org`):
   - good for low-volume interactive use
   - not suitable for bulk prefetch/offline use (policy explicitly disallows bulk/offline pre-seeding).
2. OSM US Tile Service:
   - offers vector/raster resources with tiered usage
   - free low-volume non-commercial starter tier; higher/commercial use requires partner terms.
3. OpenTopoMap:
   - usable as an off-the-shelf topo basemap
   - for larger projects they request contact; project status has had slower update cadence.
4. Overpass API:
   - great for bounded feature queries (peaks/water/trails by bbox)
   - public-instance guidance suggests keeping volume modest; planet-scale extraction should use PBF/extract pipelines instead.
5. GNIS (US only):
   - official US names data is downloadable and public domain via USGS/The National Map.
6. Self-host route:
   - OpenMapTiles schema/tooling is available for self-hosted vector tiles
   - gives full control and avoids third-party tile policy constraints.
7. Hosted commercial options (if needed):
   - MapTiler, Thunderforest, Stadia Maps all provide managed basemap APIs with published quotas/pricing.

## Practical Feasibility Check (Overpass, 2026-03-03)
Test query: `node["natural"="peak"](bbox); out body;`

1. US CO (Twin Lakes area): `71` peaks, `71` named, `70` with `ele`, ~`1.2s`.
2. US WA border sample: `15` peaks, `9` named, `15` with `ele`, ~`7.6s`.
3. Alps (Engadin sample): `673` peaks, `556` named, `615` with `ele`, ~`1.3s`.
4. Japan + Norway samples hit `HTTP 429` during test window.

Conclusion: peak coverage is strong enough for a "peaks-only labels" phase, but public Overpass can rate-limit bursty/global traffic, so cache + fallback are mandatory.

## Direct Embedding Options (Leaflet)
1. Basemap labels directly from a standard provider (no custom label stack):
   - add a regular `L.tileLayer(...)` basemap for context mode.
2. Peaks-only overlay from live Overpass:
   - server fetches peak GeoJSON by bbox, frontend renders via `L.geoJSON(...)`.
3. Peaks-only overlay from prebuilt tiles/dataset:
   - serve static GeoJSON/vector tiles (OpenMapTiles/GeoNames/GNIS-derived) and render as a Leaflet overlay.
