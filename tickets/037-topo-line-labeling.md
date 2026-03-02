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
