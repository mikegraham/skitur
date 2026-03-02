# 026: Distance Display Too Many Sigfigs
**Status: FIXED
**Priority:** P2

## Description
Don't display the distance with 3-4 sigfigs. Have at most one digit after the decimal point.

## Fix
Changed `toFixed(2)` to `toFixed(1)` for both miles and km distance display in `index.html`.
