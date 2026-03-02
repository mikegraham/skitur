# 035: Score Bar Display for >100 and Negative
*Status:** FIXED  
**Priority:** P2

## Description
Score bars use Math.min(value, 100) for width and show value/100. Now that components can exceed 100 or go negative, the display needs updating.

## Notes
- Could show a "bonus" segment extending past 100% mark
- Negative could show red bar growing from left
- Or just cap display at 0-100 with the number showing actual value
