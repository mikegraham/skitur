"""Route optimization for ski tours.

Finds optimal routes between waypoints by minimizing a cost function
that balances slope quality and avalanche safety.
"""

import math
import random
from dataclasses import dataclass

from skitur.geo import haversine_distance, METERS_PER_DEG_LAT
from skitur.terrain import get_elevation, get_ground_slope, load_dem_for_bounds
from skitur.score import _avy_slope_danger, _downhill_segment_score, _uphill_segment_score

# Optimization parameters
POINT_SPACING_M = 100.0      # Target spacing between route points
PERTURBATION_M = 30.0        # How far to nudge points during optimization
NUM_ITERATIONS = 50          # Gradient descent iterations
NUM_NEIGHBORS = 8            # Directions to sample for gradient


@dataclass
class Waypoint:
    """A point on the route."""
    lat: float
    lon: float
    required: bool = False   # If True, point cannot be moved
    name: str | None = None


@dataclass
class OptimizationResult:
    """Result of route optimization."""
    route: list[tuple[float, float]]  # Optimized (lat, lon) points
    cost: float                        # Final cost (lower is better)
    iterations: int                    # Number of iterations run


def _segment_cost(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute cost for traveling between two points.

    Lower cost = better route. Combines:
    - Slope quality (prefer moderate grades)
    - Avalanche danger (avoid 30-45 degree terrain)
    - Distance (shorter is better, but not dominant)
    """
    elev1 = get_elevation(lat1, lon1)
    elev2 = get_elevation(lat2, lon2)

    if elev1 is None or elev2 is None:
        return 1000.0  # Penalty for missing data

    dist = haversine_distance(lat1, lon1, lat2, lon2)
    if dist < 1.0:
        return 0.0

    # Calculate slope
    slope_deg = math.degrees(math.atan2(elev2 - elev1, dist))

    # Slope cost: invert the segment scores (higher score = lower cost)
    if slope_deg > 0.5:  # Uphill
        slope_cost = (100 - _uphill_segment_score(slope_deg)) / 100
    elif slope_deg < -0.5:  # Downhill
        slope_cost = (100 - _downhill_segment_score(abs(slope_deg))) / 100
    else:  # Flat
        slope_cost = 0.3  # Flat is okay but not ideal

    # Avalanche cost at midpoint
    mid_lat = (lat1 + lat2) / 2
    mid_lon = (lon1 + lon2) / 2
    ground_slope = get_ground_slope(mid_lat, mid_lon)
    avy_cost = _avy_slope_danger(ground_slope) if ground_slope else 0.0

    # Distance cost (normalized, minor factor)
    dist_cost = dist / 1000.0  # 1km = 1.0 cost

    # Weighted combination
    return (
        slope_cost * 0.4 +
        avy_cost * 0.5 +
        dist_cost * 0.1
    )


def _route_cost(points: list[tuple[float, float]]) -> float:
    """Compute total cost for a route."""
    if len(points) < 2:
        return 0.0

    total = 0.0
    for i in range(len(points) - 1):
        lat1, lon1 = points[i]
        lat2, lon2 = points[i + 1]
        total += _segment_cost(lat1, lon1, lat2, lon2)

    return total


def _interpolate_points(
    start: tuple[float, float],
    end: tuple[float, float],
    spacing_m: float = POINT_SPACING_M,
) -> list[tuple[float, float]]:
    """Generate evenly-spaced points between start and end."""
    dist = haversine_distance(start[0], start[1], end[0], end[1])
    if dist < spacing_m:
        return [start, end]

    n_points = max(2, int(dist / spacing_m) + 1)
    points = []

    for i in range(n_points):
        t = i / (n_points - 1)
        lat = start[0] + t * (end[0] - start[0])
        lon = start[1] + t * (end[1] - start[1])
        points.append((lat, lon))

    return points


def _get_neighbors(
    lat: float, lon: float, radius_m: float = PERTURBATION_M
) -> list[tuple[float, float]]:
    """Get neighboring points in cardinal and diagonal directions."""
    dlat = radius_m / METERS_PER_DEG_LAT
    dlon = radius_m / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))

    neighbors = []
    for dlat_sign in [-1, 0, 1]:
        for dlon_sign in [-1, 0, 1]:
            if dlat_sign == 0 and dlon_sign == 0:
                continue
            neighbors.append((
                lat + dlat_sign * dlat,
                lon + dlon_sign * dlon,
            ))

    return neighbors


def _optimize_point(
    idx: int,
    points: list[tuple[float, float]],
    required_indices: set[int],
) -> tuple[float, float]:
    """Find the best position for a single point using local search."""
    if idx in required_indices:
        return points[idx]

    if idx == 0 or idx == len(points) - 1:
        return points[idx]

    current = points[idx]
    prev_point = points[idx - 1]
    next_point = points[idx + 1]

    # Current cost for segments involving this point
    current_cost = (
        _segment_cost(prev_point[0], prev_point[1], current[0], current[1]) +
        _segment_cost(current[0], current[1], next_point[0], next_point[1])
    )

    best_pos = current
    best_cost = current_cost

    # Try neighbors
    for neighbor in _get_neighbors(current[0], current[1]):
        cost = (
            _segment_cost(prev_point[0], prev_point[1], neighbor[0], neighbor[1]) +
            _segment_cost(neighbor[0], neighbor[1], next_point[0], next_point[1])
        )
        if cost < best_cost:
            best_cost = cost
            best_pos = neighbor

    return best_pos


def optimize_route(
    waypoints: list[Waypoint],
    num_iterations: int = NUM_ITERATIONS,
) -> OptimizationResult:
    """Optimize a route through the given waypoints.

    Args:
        waypoints: List of waypoints. Required waypoints are fixed.
        num_iterations: Number of optimization iterations.

    Returns:
        OptimizationResult with optimized route and final cost.
    """
    if len(waypoints) < 2:
        raise ValueError("Need at least 2 waypoints")

    # Load DEM for the area
    lats = [w.lat for w in waypoints]
    lons = [w.lon for w in waypoints]
    load_dem_for_bounds(min(lats), max(lats), min(lons), max(lons), padding=0.02)

    # Build initial route by interpolating between waypoints
    points: list[tuple[float, float]] = []
    required_indices: set[int] = set()

    for i, wp in enumerate(waypoints):
        if i > 0:
            # Interpolate from previous waypoint
            prev = waypoints[i - 1]
            interp = _interpolate_points((prev.lat, prev.lon), (wp.lat, wp.lon))
            # Skip first point (already added) except for first segment
            points.extend(interp[1:] if points else interp)

        # Mark required waypoint index
        if wp.required:
            required_indices.add(len(points) - 1)

    print(f"Initial route: {len(points)} points, cost={_route_cost(points):.2f}")

    # Optimization loop
    for iteration in range(num_iterations):
        improved = False

        # Optimize each movable point
        indices = list(range(1, len(points) - 1))
        random.shuffle(indices)  # Random order to avoid bias

        for idx in indices:
            if idx in required_indices:
                continue

            old_pos = points[idx]
            new_pos = _optimize_point(idx, points, required_indices)

            if new_pos != old_pos:
                points[idx] = new_pos
                improved = True

        if iteration % 10 == 0 or iteration == num_iterations - 1:
            cost = _route_cost(points)
            print(f"  Iteration {iteration + 1}: cost={cost:.2f}")

        # Early termination if no improvement
        if not improved:
            print(f"  Converged at iteration {iteration + 1}")
            break

    final_cost = _route_cost(points)

    return OptimizationResult(
        route=points,
        cost=final_cost,
        iterations=iteration + 1,
    )


def optimize_hood_example():
    """Example: optimize a route on Mt Hood."""
    # Polallie Ridge trailhead to Tilly Jane
    waypoints = [
        Waypoint(lat=45.3973, lon=-121.6517, required=True, name="Trailhead"),
        Waypoint(lat=45.4050, lon=-121.6400, required=False, name="Ridge"),
        Waypoint(lat=45.4150, lon=-121.6250, required=False, name="Mid"),
        Waypoint(lat=45.4230, lon=-121.6050, required=True, name="Tilly Jane"),
    ]

    print("Optimizing Mt Hood route...")
    print(f"Waypoints: {[w.name for w in waypoints]}")

    # Get initial route for comparison
    initial_points: list[tuple[float, float]] = []
    for i, wp in enumerate(waypoints):
        if i > 0:
            prev = waypoints[i - 1]
            interp = _interpolate_points((prev.lat, prev.lon), (wp.lat, wp.lon))
            initial_points.extend(interp[1:] if initial_points else interp)

    result = optimize_route(waypoints, num_iterations=30)

    print(f"\nResult: {len(result.route)} points, cost={result.cost:.2f}")
    print(f"Iterations: {result.iterations}")

    # Visualization helper intentionally removed from core package to keep
    # runtime dependencies lightweight (no matplotlib requirement).
    print("Route comparison plotting is disabled in this build.")

    return result


if __name__ == "__main__":
    optimize_hood_example()
