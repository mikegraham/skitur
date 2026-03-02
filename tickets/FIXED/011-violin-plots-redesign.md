# 011: Redesign Distribution Plots
*Status:** FIXED  
**Priority:** P1

## Description
Separate the violin plots. Make them horizontal, more like smoothed histograms. Keep the lines the same color as from slope profile, but shade them with the appropriate colormap scheme. For the track histogram/violin, don't take the absolute value - let there be an uphill side and a downhill side. For the ground angle, have it be one-sided and make it clear there's nothing <0 degrees.

## Notes
- User specifically wants horizontal orientation
- Track distribution: uphill (positive) on one side, downhill (negative) on other
- Ground distribution: one-sided, all positive
- Shade with colormap colors (green->yellow->red matching slope profile)
- Must match colors with slope profile chart
