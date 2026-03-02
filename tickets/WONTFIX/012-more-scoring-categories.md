# 012: Think Up More Scoring Categories
**Status:** RESEARCHED
**Priority:** P1

## Description
Brainstorm and implement additional scoring categories for tour quality beyond downhill fun, uphill doability, and avy exposure.

## Ideas to Explore
- Aspect-based snow quality (north vs south facing)
- Steepness consistency (reward consistent grades, penalize wild variation)
- Bang-for-buck / effort ratio (vertical per distance)
- Terrain variety (does the tour visit different terrain types?)
- Access difficulty
- Route complexity / navigation

## Questions for User
- What aspects of a tour do YOU find most important beyond slope quality and avy?

## Research Findings

### Top Priority Categories (Ordered by Impact & Feasibility)

1. **Elevation Gain Efficiency** ("Earned Turns Ratio")
   - Formula: `net_gain / total_ascent`
   - Measures wasted climbing on non-productive sections
   - Data required: GPX elevation profile (already computed)
   - Impact: High | Feasibility: High

2. **Approach-to-Skiing Ratio**
   - Formula: `flat_distance / total_distance`
   - Penalizes boring flat approaches and traverses
   - Data required: GPX elevation profile (already computed)
   - Impact: High | Feasibility: High

3. **Transition Penalty** (Choppy Skin/Ski Changes)
   - Metric: Count mode changes (skin → ski → skin patterns)
   - Choppiness index: `total_elevation_change / (2 * net_elevation_change)`
   - Rewards flowing routes with fewer transitions
   - Data required: GPX elevation profile (already computed)
   - Impact: Medium-High | Feasibility: High

4. **Snow Quality Potential**
   - Model: Aspect-elevation-solar exposure (north-facing + high elevation = best snow preservation)
   - Considers seasonal sun angle and wind-loading factors
   - Data required: DEM aspect/curvature extension
   - Impact: High | Feasibility: Medium (needs DEM data)

5. **Scenic / Viewshed Quality**
   - Metric: TPI (Topographic Position Index) or viewshed analysis
   - Identifies panoramic openness and scenic diversity
   - Data required: DEM aspect/curvature extension
   - Impact: Medium | Feasibility: Medium (needs DEM analysis)

### Additional Categories Identified
- Terrain variety (different terrain types visited)
- Wind exposure / shelter (aspect-based windloading)
- Route-finding difficulty (navigation complexity)
- Time estimates (Munter speed model)
- Descent steepness consistency (reward smooth grades, penalize wild variation)
- Commitment / escape difficulty (bail-out options)
- Aspect diversity of descent (multi-aspect exposure)
- Fall-line alignment (skiing efficiency)
- Elevation profile aesthetics (reward interesting profiles)
- Forest/treeline analysis (exposure variety)

### Implementation Strategy
- **Phase 1:** Implement categories 1-3 (GPX-based, no additional data needed)
- **Phase 2:** Extend DEM processing for categories 4-5 (aspect, curvature, solar exposure)
- **Phase 3:** Evaluate additional categories based on user feedback

### Notes
- Categories 1-3 leverage existing elevation profile computation
- Categories 4-5 require DEM aspect/curvature extension as prerequisite
- User validation recommended before full implementation
