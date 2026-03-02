# 033: Track Segment Coloring Method
**Status:** OPEN  
**Priority:** P2

## Description
How does the code decide how to color a segment of track? It looks like one color now. Is that for the middle? the start? the end? Should it be a gradient?

## Notes
- Currently uses pts[i+1].slope for segment color (end point)
- Could use gradient between start and end colors
- Would need canvas gradient per segment
