# 059: Ground Legend Breakpoints Must Be Hard at 30° and 45°
**Status:** OPEN  
**Priority:** P2

## Description
The ground-angle colormap legend bar currently looks smooth and diffuse around the `30°` and `45°` thresholds.

Those two transitions are semantically important boundaries in the visual design and should render as crisp, abrupt breaks in the legend (and corresponding map shading), not as blended ramps.

## Acceptance Criteria
1. The ground legend shows a visibly hard transition at `30°`.
2. The ground legend shows a visibly hard transition at `45°`.
3. There is no anti-aliased/interpolated-looking fade zone at those breakpoints.
4. The legend appearance matches the underlying map colormap behavior at those same thresholds.
