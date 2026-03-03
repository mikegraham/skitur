# 037: Topo Line Labeling Improvements
**Status:** OPEN
**Priority:** P2

## Description
Multiple labels for long contour lines, text halos instead of line gaps. Research showed pros use Mapnik-style text halos. Possible MapLibre GL JS migration.

## Additional Notes
- Not enough topo labels currently — long contour lines may have zero or one label when they need several
- Investigate standard libraries/examples that could improve topo rendering

## Research (ChatGPT analysis, March 2026)

### Key Findings

The limiting factor for topo quality isn't line drawing — it's **label placement** (curved text on lines, repetition rules, collision avoidance). Leaflet's core rendering (SVG/Canvas) doesn't include a modern symbol/label engine.

### Recommended Approaches (ranked)

**Option A (best quality): Leaflet + MapLibre GL JS hybrid**
- Render basemap/overlays in Leaflet, contour layer in MapLibre via Leaflet binding
- MapLibre style spec supports: line-placed labels, repeated placement spacing, text halos, collision avoidance
- `symbol-placement: "line"`, `symbol-spacing`, `text-halo-color/width`, `text-keep-upright`
- This is the most straightforward path to "professional" curved contour labels

**Option B: Pure Leaflet with precomputed label points**
- Contour lines via Leaflet.VectorGrid (MVT/Canvas)
- Separate label-point layer with precomputed anchors
- Declutter with Leaflet.LabelTextCollision or labelgun
- More engineering work, less elegant than MapLibre

**Option C: OpenLayers (non-WebGL line labels)**
- OpenLayers supports `placement: 'line'` and `repeat` in Text style API
- Built-in decluttering without WebGL
- Would require switching from Leaflet entirely

### Our Current Approach (gap + rotated divIcon)
- One label per contour level (longest line only)
- Line gap at midpoint with rotated `<span>` in a divIcon
- No collision avoidance, no halos, no repeated labels
- Adequate for now but clearly inferior to MapLibre-style labels

### Relevant Libraries
- **MapLibre GL JS** - BSD-3-Clause, best-in-class line labels
- **Leaflet.VectorGrid** - MVT rendering in Leaflet
- **Leaflet.TextPath** - SVG textPath (fragile on mobile)
- **Leaflet.LabelTextCollision** - hide overlapping labels
- **labelgun** - library-agnostic label decluttering

### Styling Guidelines (cartographic convention)
- Index contours (every 5th): thicker, darker, always labeled
- Intermediate contours: thin, lighter, minimal labeling
- Label spacing: 300-800px depending on zoom (pixel-based, not meters)
- Text halos: white/off-white, 0.8-1.5px width
- Color: brown family (#8b6b3e range)

### Decision
If we pursue this, the MapLibre hybrid approach (Option A) gives the best quality-to-effort ratio. Our contours are already computed server-side, so we'd need to either:
1. Serve them as MVT tiles (more infrastructure)
2. Pass them as GeoJSON to a MapLibre layer (simpler, works with current architecture)

---

## Extended Research Notes (March 2026)

### Executive Summary

A Leaflet-first contour map can reach "publication-like" quality, but the limiting factor is almost never line drawing—it is **label placement** (curved text on lines, repetition rules, and collision avoidance) and **multi-scale generalization**. Leaflet's core rendering stack (SVG/Canvas) does **not** include a modern symbol/label engine comparable to WebGL vector-tile renderers, so you must either (a) bring a label engine, or (b) bake labels into tiles.

### Detailed Approach Comparison

| Candidate | Labeling | Pros | Cons | License |
|---|---|---|---|---|
| **Leaflet** | Tooltips/popups only; no curved line labels | Simple API; huge plugin ecosystem | No modern symbol engine | BSD-2 |
| **Leaflet.VectorGrid** | None built-in | Efficient tiled MVT rendering | Labeling must be separate | Beer-Ware |
| **MapLibre GL JS** | Best-in-class: line placement, halos, collisions | WebGL performance; style-spec ecosystem | More complex than Leaflet | BSD-3 |
| **OpenLayers** | Strong Canvas: `placement: 'line'` + `repeat` | Built-in line label + declutter | Would require switching from Leaflet | BSD-2 |
| **Tangram** | Curved labels supported | WebGL cartography | Smaller ecosystem | MIT |
| **deck.gl** | Text at points only | Very high-performance | No text-along-polyline | MIT |
| **protomaps-leaflet** | Canvas + web fonts | Lightweight vector + labeling in Leaflet | In maintenance mode | - |
| **Leaflet.TextPath** | True text-along-line in SVG | Simple curved labels | Mobile edge cases; no collision mgmt | MIT |

### MapLibre Style Spec for Contour Labels

Key capabilities:
- `symbol-placement: "line"` / `"line-center"` — place labels along line geometry
- `symbol-spacing` — distance between repeated label anchors (pixel-based, default 250px)
- `text-halo-color` / `text-halo-width` — readability halos (max halo = 1/4 font-size)
- `text-keep-upright: true` — keep text readable
- Global viewport-based collision detection with spatial indexing

```json
{
  "id": "contour-labels",
  "type": "symbol",
  "source": "contours",
  "source-layer": "contour",
  "layout": {
    "symbol-placement": "line",
    "symbol-spacing": ["interpolate", ["linear"], ["zoom"], 10, 900, 14, 450],
    "text-field": ["to-string", ["get", "ele"]],
    "text-size": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 14],
    "text-rotation-alignment": "map",
    "text-keep-upright": true
  },
  "paint": {
    "text-color": "#6b4f2a",
    "text-halo-color": "rgba(255,255,255,0.85)",
    "text-halo-width": 1.2
  }
}
```

### Pure-Leaflet Label Placement Algorithm (Pseudocode)

For repeated labels along polylines with collision avoidance:

1. Convert polyline to screen coords, compute cumulative length
2. Place candidate anchors every `minPixelSpacing` pixels along line
3. At each anchor: compute tangent angle, skip high-curvature segments
4. Estimate rotated label bounding box, test against spatial index (rbush/grid)
5. If no collision, accept placement and insert bbox into index
6. Process higher-priority contours (index lines) first

Key functions needed: `interpolateAlong()`, `isLocallyStraight()`, spatial index (rbush), `rotatedBBox()`

### Styling Rules (Cartographic Convention)

**Line styling by zoom:**
- z <= 10: minor 0.3-0.5px, index 0.6-0.9px
- z 11-13: minor 0.5-0.9px, index 1.0-1.6px
- z >= 14: minor 0.9-1.4px, index 1.6-2.4px
- Color: brown (#8b6b3e), round joins/caps

**Label frequency (index contours):**
- z 10-11: 800-1200px spacing
- z 12-13: 500-800px
- z 14-15: 300-500px
- z >= 16: 250-400px

**Zoom-dependent label density:**
- z <= 10: index contours only (or none)
- z 11-13: index contours consistently, skip intermediates
- z >= 14: option to label some intermediates

### Contour Data Sources

- **MapTiler Contours** — dedicated contour vector tileset (10m / 20ft intervals)
- **Mapbox Terrain v2** — `contour` source layer with `ele` and `index` fields (10m increments)
- **USGS National Map Contours** — authoritative US contours via ArcGIS services, updated quarterly
- **OpenTopoMap** — raster tiles derived from OSM + SRTM (labels baked in, not restyleable)
- **Self-hosted pipeline** — generate contours from DEM via GDAL → simplify with Mapshaper → tile with Tippecanoe → serve as MVT/PMTiles

### Performance Notes

- Prefer MVT/Protobuf tiles over GeoJSON for anything beyond small AOIs
- Canvas renderer for large vector counts
- Separate label layer from line layer for independent density control
- Per-zoom simplification (Douglas-Peucker / Visvalingam-Whyatt) to keep vertex counts bounded
- PMTiles for serverless single-file hosting

### Implementation Path for Our Architecture

**Short-term (pure Leaflet, no new dependencies):**
- Multiple labels per long contour line (repeat every ~400px screen distance)
- Text halos via CSS `text-shadow` on existing divIcon labels
- Simple collision avoidance: skip label if too close to an existing one

**Medium-term (MapLibre hybrid):**
- Use `@maplibre/maplibre-gl-leaflet` binding
- Pass contours as GeoJSON to MapLibre layer (works with current backend)
- Style with MapLibre's symbol layer for professional labels
- Keep all other layers (slope overlay, track, markers) in Leaflet

**Long-term (full MapLibre migration):**
- Move entire map rendering to MapLibre GL JS
- Serve contours + slope grid as vector tiles
- Full collision avoidance, curved labels, zoom-dependent density
