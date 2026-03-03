# 057: Colormap Legend Bars Disappear Below ~700px
**Status:** OPEN  
**Priority:** P1

## Description
At viewport widths below about `700px` (confirmed in Firefox), the map colormap legend bars disappear entirely. This removes key context for both ground slope and track slope colors.

Legend bars should remain visible and readable at small widths instead of being hidden.

## Acceptance Criteria
1. At widths below `700px`, both legend bars are still rendered (ground + track).
2. Legends remain visually usable on small screens (labels and color ramp are legible).
3. Behavior is verified in Firefox and one Chromium-based browser at multiple widths (for example `375`, `480`, `640`, `700` px).
