# 058: Path Optimization for Skiable + Safe Routing
**Status:** OPEN
**Priority:** P1

## Problem
Current route optimization in `skitur/optimize.py` is local point perturbation:
- It moves one point at a time to one of 8 nearby neighbors.
- Update order is randomized each iteration.
- It optimizes a narrow local objective and can get stuck in local minima.
- It has weak geometric guardrails, so routes can become jagged/wiggly ("monstrosities").

This is not enough for real ski routing where choices are global (which side of a ridge/basin), direction-dependent (up vs down), and constrained by terrain safety.

## Goals
1. Generate routes that are materially more skiable and safer than straight-line interpolation.
2. Handle directionality explicitly (ascent vs descent can prefer different terrain).
3. Produce smooth, human-plausible tracks (no sawtooth jitter, no loops unless requested).
4. Keep runtime acceptable for interactive use.
5. Be deterministic by default (same input => same output).

## Non-Goals (for this ticket)
1. Full avalanche forecasting (wind slab, recent storm, weak-layer model).
2. Land-use/legal access guarantees (private land, seasonal closures).
3. Replacing user intent; required waypoints remain hard constraints.

## Why the Current Approach Is Risky
1. **Local-only search:** cannot reliably discover large detours around bad terrain.
2. **Randomized update order:** non-reproducible outputs and unstable quality.
3. **No explicit direction model:** ascent/downhill preferences are mixed only indirectly.
4. **No hard safety constraints:** steep/hazard terrain is penalized but not forbidden.
5. **No shape regularization:** can produce high-curvature and self-crossing artifacts.

## Proposed Architecture
### 1) Build a Direction-Aware Cost Field
For each candidate edge/cell transition, compute:
- slope/travel-grade cost (different models uphill vs downhill),
- avalanche proxy cost (strong penalty for 30-45 deg, optional hard cutoff),
- friction/terrain roughness proxy,
- distance/time cost.

Use an anisotropic edge cost: `cost(A->B)` may differ from `cost(B->A)`.

Suggested edge objective (normalized terms):

`edge_cost = w_dist*C_dist + w_grade*C_grade(dir) + w_hazard*C_hazard + w_turn*C_turn + w_rough*C_rough`

Where:
1. `C_grade(dir)` uses ascent/downhill-specific preference curves.
2. `C_hazard` strongly penalizes persistent 30-45 degree exposure.
3. `C_turn` penalizes abrupt heading change and repeated zig-zag.
4. Weights are profile-dependent (`Conservative`, `Efficiency`, `Downhill`).

### 2) Global Search First, Local Refinement Second
1. **Global planner (primary):** graph search on a raster/mesh corridor between required waypoints.
   - Candidate methods: A*, Theta*, or Dijkstra with admissible heuristic.
   - 16-direction connectivity (or triangular mesh) for less axis bias.
   - State includes heading bin, not just `(x, y)`, so turn penalties are explicit.
2. **Alternative candidates:** k-shortest diverse paths (beam or Yen-style variants) to keep multiple plausible corridors.
3. **Refinement pass:** constrained smoothing and local improvement on the best candidates.

### 3) Handle "Two Directions to Go"
For each leg between required waypoints:
1. Solve start->end using direction-aware costs.
2. Also solve end->start then reverse the geometry.
3. Keep both as candidates and score with the intended travel direction profile:
   - ascent-focused objective,
   - descent-focused objective,
   - weighted mixed objective for loop tours.

This prevents accidentally preferring a path that only looks good in reverse.

### 4) Anti-Monstrosity Guardrails (Hard + Soft)
Hard constraints:
1. No self-intersections (unless explicitly allowed).
2. Max local turn angle / minimum effective turn radius.
3. Max lateral deviation corridor from user-intended line or anchors.
4. Optional forbidden masks (cliffs, glacier hazards, no-go polygons).

Soft penalties:
1. Curvature penalty (total variation or second-derivative on heading).
2. Zig-zag penalty (frequent heading sign changes).
3. Length inflation penalty relative to baseline route.

Post-process:
1. Simplify with topology-safe Douglas-Peucker-like pass.
2. Re-sample to stable point spacing and re-validate constraints.

### 5) Multi-Objective Scoring
Use explicit weighted terms with tunable profiles:
- `Touring Conservative`: prioritize avalanche avoidance and smoothness.
- `Efficiency`: tighter distance/time tradeoff.
- `Downhill Quality`: emphasize descent grade bands while keeping hazard limits.

Expose the final score breakdown per route:
- distance/time,
- hazard exposure percent,
- ascent/descent grade quality,
- geometry quality (smoothness).

## Iterative Optimization Strategy
1. Generate N global candidates.
2. Discard invalid ones (hard constraints).
3. Refine survivors locally.
4. Re-score with full objective.
5. Keep Pareto frontier and select default route by profile.
6. Repeat with widened corridor only if candidate quality is below threshold.

This avoids pointwise random drift and supports controlled iteration.

## Recommended v1 Search Loop (Deterministic)
For each required-waypoint leg:
1. Build an initial corridor around the straight/anchor polyline (for example 250-400m).
2. Rasterize corridor to a planning grid (for example 15-30m cells).
3. Precompute cell features once: elevation, slope, aspect, roughness proxy, no-go mask.
4. Run A* with state `(cell_id, heading_bin)` and deterministic tie-breaking.
5. Run reverse-direction solve as a separate optimization problem.
6. Produce top-k diverse paths (reject near-duplicates by edge-overlap threshold).
7. Apply constrained smoothing, then re-score with full objective.
8. If no feasible route or score below quality bar, widen corridor and repeat.

Determinism rules:
1. No random shuffle in optimization loop.
2. Stable node expansion ordering for equal priority.
3. Fixed numeric tolerances and canonical route serialization in tests.

## How to Avoid Route "Monstrosities"
Hard checks during and after search:
1. Self-intersection prohibited.
2. Maximum instantaneous heading change threshold.
3. Maximum local slope threshold when hard safety mode is enabled.
4. No loops/backtracking unless explicitly requested.
5. Corridor-bounds compliance.

Soft regularization:
1. Heading total-variation penalty.
2. Penalty for frequent left-right alternation (sawtooth detector).
3. Penalty for unnecessary length inflation and micro-detours.

Post-refinement validation metrics:
1. Turn events per km.
2. Median and P95 absolute turn angle.
3. Edge revisit rate / self-overlap rate.

Important nuance:
1. Ski switchbacks/zigzags are often valid and necessary on ascent.
2. We should penalize **artifact zigzags** (high-frequency short-amplitude oscillation), not all zigzags.
3. Detect artifacts via scale-aware rules, for example:
   - alternating turn sequence over very short leg lengths,
   - repeated heading flips without net progress,
   - high curvature concentrated at sub-threshold segment scale.
4. Legitimate long switchbacks that reduce effective slope should be allowed or rewarded.

## Geometry Representation: Points vs Splines
Recommendation:
1. Use a point/polyline graph for planning and scoring (primary representation).
2. Resample to consistent spacing for stable cost evaluation.
3. Apply spline/smoothing only as a post-process visualization step, followed by hard re-validation.

Why:
1. Splines can drift into unsafe cells after interpolation unless constrained.
2. Polyline vertices map cleanly to cost-grid/graph states and constraints.
3. Deterministic testing is easier with resampled polylines than free-form splines.

Resampling policy (proposed):
1. Planning resolution: tie to DEM/grid scale (e.g., 15-30m).
2. Scoring resolution: fixed step for exposure metrics (e.g., 10-20m equivalent samples).
3. Output resolution: simplify while preserving anchors/safety-critical bends.

## Candidate Tools and Ecosystem
Potential building blocks (not final selection):
1. In-Python graph/pathfinding:
   - `networkx` for A*/k-shortest prototyping.
   - `scipy.sparse.csgraph` for fast shortest-path on sparse matrices.
   - `skimage.graph.route_through_array` for raster least-cost prototypes.
2. Geospatial least-cost tooling:
   - GRASS GIS (`r.cost` + `r.path`) for mature raster least-cost workflows.
   - WhiteboxTools cost-distance/pathway tools for alternate raster pipeline.
3. Routing engines / graph infra:
   - OSRM, Valhalla, GraphHopper for large-scale graph routing patterns and infra ideas.
   - pgRouting for SQL/PostGIS-native A* and shortest-path operations.
4. Optimization frameworks:
   - OR-Tools / Pyomo class tooling if we move toward constrained multi-objective search with richer solver control.

## Testing Plan
### Unit Tests
1. Cost components are monotonic in expected directions (e.g., steeper 35 deg > 20 deg hazard cost).
2. Directional cost asymmetry works (`A->B` != `B->A` where expected).
3. Guardrails reject loops/self-crossings/high-curvature routes.

### Synthetic Terrain Tests
Create small synthetic DEMs where optimal behavior is known:
1. Valley with two ridge bypass options (left/right branch choice).
2. Bowl with hazardous center (should route around center).
3. Long mellow detour vs short dangerous direct line (tradeoff check).

### Regression/Golden Tests
1. Fixed seed + fixed DEM extract => deterministic route geometry.
2. Compare against baseline on known GPX examples:
   - lower hazard exposure,
   - acceptable distance inflation cap,
   - smoother geometry metrics.

### Property Tests
1. Route always passes required waypoints in order.
2. No NaN/invalid coordinates.
3. No segment exceeds max gradient threshold when hard cutoff enabled.

### Performance Benchmarks
1. P50/P95 runtime on small/medium/large corridors.
2. Candidate count vs quality gain curve.
3. Memory bounds for interactive server use.

## Acceptance Criteria (v1)
1. Deterministic output: same inputs produce byte-identical route geometry.
2. Safety improvement: hazard-exposure distance is reduced versus baseline interpolation on benchmark routes.
3. Quality guardrails:
   - no self-crossings,
   - turn-event density below configured threshold,
   - no pathological zig-zag patterns.
4. Practicality:
   - distance inflation bounded by profile (for example conservative profile may allow more detour than efficiency profile),
   - required waypoints preserved in order.
5. Runtime target: interactive planning latency on representative route sizes (define dataset + threshold in benchmark harness).

## Suggested Implementation Milestones
1. **M1: Deterministic Baseline**
   - Remove randomized update order dependence.
   - Add geometry guardrails and objective breakdown output.
   - Add benchmark fixtures + deterministic golden tests.
2. **M2: Global Planner per Leg**
   - Implement corridor graph + A* (`(cell, heading)` state) with anisotropic cost.
   - Return top-k alternatives.
3. **M3: Bidirectional Candidate Evaluation**
   - Evaluate forward and reverse plans for each leg.
   - Add ascent/descent profile selection.
4. **M4: Refinement + Smoothing**
   - Constrained local smoothing and simplification.
5. **M5: Test + Benchmark Harness**
   - Synthetic fixtures, regression suite, and performance gates.

## Open Questions
1. Should hazard constraints default to hard cutoffs (e.g., forbid >40 deg) or soft penalties?
2. Do we optimize a single canonical route or expose 2-3 alternatives by default?
3. For loops/out-and-back tours, do we optimize ascent and descent legs separately?
4. What max distance inflation is acceptable for safety improvements (e.g., +10%, +25%)?

## Tooling Landscape (External References)
### Closest Ski/Backcountry Product Analogs
1. **Skitourenguru**: publicly describes route generation/adjustment as Dijkstra over cost surfaces, with tuning knobs and sensitivity controls.
   - ISSW 2024 paper record: https://arc.lib.montana.edu/snow-science/item/3341
   - Route sensitivity doc (DE): https://wiki.skitourenguru.com/de/articles/a0064.html
2. **White Risk**: route-planning support with automatic crux detection and avalanche-terrain overlays.
   - Product page: https://whiterisk.ch/en/
   - Automatic cruxes: https://content.whiterisk.ch/en/help/tour-planning/automatic-crux-detection
   - Avalanche terrain maps: https://content.whiterisk.ch/en/help/maps/avalanche-terrain-maps
3. **Slope-layer planning tools** (strong context, limited autonomous optimization): CalTopo, Gaia GPS, onX Backcountry.
   - CalTopo overlays: https://training.caltopo.com/all_users/overlays/overlay-desc
   - Gaia slope/avy layer: https://www.gaiagps.com/maps/source/slope-avy/
   - onX slope + ATES/Terrain-X: https://onxbackcountry.zendesk.com/hc/en-us/articles/360055773352-Viewing-Slope-Angles
   - onX terrain analysis: https://onxbackcountry.zendesk.com/hc/en-us/articles/22727054986125

### Proven Geospatial Optimization Engines
1. **GRASS GIS** least-cost stack:
   - `r.walk`: https://grass.osgeo.org/grass-stable/manuals/r.walk.html
   - `r.path`: https://grass.osgeo.org/grass82/manuals/r.path.html
2. **ArcGIS Spatial Analyst** least-cost path:
   - https://pro.arcgis.com/en/pro-app/3.3/tool-reference/spatial-analyst/creating-the-least-cost-path.htm
3. **pgRouting** graph search in PostGIS:
   - A*: https://docs.pgrouting.org/dev/en/pgr_aStar.html

### Core Algorithm References for Implementation
1. **Any-angle path planning** (Theta* family):
   - https://s.aaai.org/Library/JAIR/Vol39/jair39-012.php
2. **k-shortest simple paths** (Yen in NetworkX):
   - https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.simple_paths.shortest_simple_paths.html
3. **Ski-resort route optimization research** (preference-aware routing):
   - https://arxiv.org/abs/2307.08570

### Notes for This Ticket
1. Inference: the most transferable approach remains terrain cost-surface routing + constrained graph search + deterministic refinement.
2. Product claims (apps) are useful directional signals, but algorithm details are often proprietary and should not be treated as validated benchmarks.

## Research Synthesis (2026-03-04)
This section folds in a longer terrain-aware routing write-up and maps it directly to implementation decisions.

### Terrain-Aware Cost Modeling
1. Treat the route plan as a least-cost path problem over DEM-derived terrain cells/edges.
2. Include slope, aspect-derived directionality, roughness/friction, hazard exposure, and movement distance/time.
3. Keep avalanche hazard modeling explicit in the objective, with sustained 30-45 degree exposure heavily penalized.
4. Use direction-dependent edge costs so uphill and downhill behavior are intentionally different.

### Algorithm Strategy
1. Use deterministic graph search (A* or Dijkstra) as the primary production path.
2. Preserve Theta* as an optional any-angle variant for smoother geometry, with strict post-checking against safety constraints.
3. Generate alternates via k-shortest/diversity strategies (Yen-style or overlap-pruned repeated solves).
4. Keep PDE/continuous anisotropic solvers (for example Ordered Upwind style methods) as research references, not initial production scope.

### Deterministic Heading-State Prototype
1. Baseline prototype should operate on `(cell, heading_bin)` states with fixed tie-breaking.
2. Edge score should include distance + direction-aware grade + hazard + turn friction.
3. Solve each leg both forward and reverse (then reverse geometry) before final ranking.
4. Validate on synthetic DEM scenes before large real-route rollout.

### Guardrails and Evaluation
1. Hard constraints: no self-intersection, no forbidden zones, optional slope cutoff, and bounded turning behavior.
2. Soft geometry quality: penalize artifact zigzags and high-frequency heading flips while allowing long useful switchbacks.
3. Primary comparison metrics versus baseline interpolation:
   - hazard-exposed distance,
   - distance/time inflation,
   - turn density and high-angle turn tails,
   - waypoint/order compliance.
4. Smoothing/simplification is allowed only with full post-smoothing revalidation.
