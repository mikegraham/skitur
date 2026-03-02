# 015: Refactor Shared Code Between Matplotlib and Web
**Status:** OPEN  
**Priority:** P1

## Description
Look for code duplication between the static matplotlib version (plot.py) and the web/HTML version (index.html). Refactor to use shared stuff when reasonable.

## Notes
- Colormaps defined in both places
- Score computation shared but display logic differs
- Consider ifchange-thenchange pattern if sharing isn't practical
- Don't force sharing where it doesn't make sense
