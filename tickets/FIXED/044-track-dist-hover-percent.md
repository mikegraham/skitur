# 044: Track Distribution Hover Text and Y-axis Improvements

**Status:** FIXED
**Priority:** P2

## Description
In the Track Slope Distribution plot:
1. Show bin range "3-4 deg" instead of "3 deg" on hover
2. Show percent instead of count (for uphill, percent of uphill; for downhill, percent of downhill)
3. Remove y-axis labels and lines entirely

## Additional Request
Also add vertical gridlines at the x-axis label positions (every 5 degrees) for the Track Slope Distribution plot.

## Resolution
All requirements verified in index.html renderTrackDistribution function:
- Percent calculation: lines 986, 1001 `(b.count / Total) * 100`
- Bin range hover text: lines 995, 1011 `${Math.floor(b.mid)}-${Math.ceil(b.mid)}&deg;`
- No y-axis labels: line 1021 `showticklabels: false, showgrid: false`
- Vertical gridlines: lines 1017-1018 `showgrid: true, dtick: 5`
