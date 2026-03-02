# 013: Ground Colormap Needs Orange Zone
**Status:** FIXED
**Priority:** P1

## Description
The ground colormap should have more orange zone, not going straight to red. The yellow and orange must be extremely distinguishable. The saturation should change sharply at 30 degrees, going from faint milky colors to bold ones.

## Notes
- Current: 0-30 pastel teal->green->yellow, 30-45 muted red->dark red
- Need: 0-30 milky/faint colors, with orange appearing near 25-30
- At 30: sharp saturation change to bold colors
- 30-45: bold orange->red->dark red
- The color change at 30 should signal both hue AND saturation shift

## Fix Details
Updated both `skitur/templates/index.html` (GROUND_CMAP lookup table) and
`skitur/plot.py` (`_make_ground_cmap` function) with matching colormap changes.

**New zone structure:**
- **0-25 deg:** Milky/faint teal -> green -> yellow (very desaturated, pastel).
  Same washed-out palette as before, but compressed into 0-25 range.
- **25-30 deg:** Muted orange warning zone. Still faint/desaturated but clearly
  orange (r=0.85-0.88, g=0.72-0.60, b=0.38-0.33), distinct from yellow below.
- **30 deg:** HARD saturation + hue shift. Jumps from faint muted orange to bold
  vivid orange (r=0.90, g=0.45, b=0.08). Unmistakable boundary.
- **30-45 deg:** Bold orange -> red -> dark red (three sub-zones: bold orange
  30-34.5, bold red 34.5-39.75, dark red 39.75-45). High saturation throughout.
- **45+ deg:** Gray (unchanged, rock/ice too steep for snow).

Both JS and Python colormaps use identical formulas for pixel-perfect match.
