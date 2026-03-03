# 056: Track Stats Gap and Score Card Width
**Status:** OPEN  
**Priority:** P3

## Description
At some viewport widths, the Track Statistics card can show an overly large horizontal gap between label/value columns, which hurts scanability.

Improve the layout so:
- Track Statistics label/value pairs stay visually close and readable.
- Tour Quality Score card can widen when there is extra horizontal room in the row.
- Layout is checked at multiple viewport sizes to avoid regressions.

## Acceptance Criteria
1. Track Statistics rows do not show a large empty gutter between the two data columns at common desktop/tablet widths.
2. The info row (`Tour Quality Score` + `Track Statistics`) uses responsive sizing that allows the score card to grow when spare room exists.
3. Mobile behavior remains clean (single-column stack unchanged or improved).
4. Visual checks are performed at multiple widths (for example: ~375, ~768, ~1024, ~1440 px) and artifacts/screenshots are captured or updated.
