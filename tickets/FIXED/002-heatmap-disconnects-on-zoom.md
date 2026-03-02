# 002: Heatmap Disconnects from Topo on Browser Zoom
**Status:** FIXED
**Priority:** P0

## Description
When using ctrl+plus to zoom the browser, the slope heatmap disconnects from the topo lines and stays in the upper left corner.

## Root Cause
`SlopeGridLayer._redraw()` created `ImageData` at CSS pixel dimensions (`w × h`) but placed it into a canvas sized at `w*dpr × h*dpr` pixels. `putImageData` doesn't respect canvas transforms, so at high browser zoom (higher dpr), the image only filled `1/dpr` of the canvas in each dimension — appearing stuck in the upper-left corner.

## Fix
Changed to render at full canvas resolution:
- `createImageData(w * dpr, h * dpr)` instead of `(w, h)`
- Iterate over `cw × ch` pixels (canvas resolution)
- Convert back to container coordinates via `px / dpr, py / dpr` for `containerPointToLatLng`
- Removed the unnecessary `ctx.scale(dpr, dpr)` and `ctx.setTransform` reset since we work directly in canvas pixel coordinates

## Files Modified
- `skitur/templates/index.html` — `SlopeGridLayer._redraw()`
