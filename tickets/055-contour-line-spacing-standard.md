# 055: Standardize Contour Line Spacing
**Status:** OPEN  
**Priority:** P1

## Description
Current web contour extraction uses non-standard adaptive intervals (`100/500` and `200/1000` in some relief ranges), which is confusing and inconsistent with common U.S. topo conventions.

Use standard imperial contour handling:
- Minor intervals selected from standard set: `10`, `20`, `40`, `80` ft
- Index contours every 5th line (`50`, `100`, `200`, `400` ft respectively)

Avoid ad-hoc `100/500` and `200/1000` spacing.

## Why This Matters
1. User expectation: map readers recognize standard USGS-style spacing.
2. Consistency: static plots and web map should not diverge in contour convention.
3. Interpretability: slope/terrain reading is harder when interval rules are non-standard.

## Acceptance Criteria
1. Web contour pipeline no longer emits `100/500` or `200/1000` interval pairs.
2. Interval/index pair always follows standard 5th-line rule.
3. Contour spacing logic is shared or clearly aligned between web and plot code paths.
4. At least one regression test asserts index levels are exactly `minor * 5`.

