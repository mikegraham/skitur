"""Non-plot map data helpers for slope/elevation grids and contour intervals."""

from __future__ import annotations

from typing import Any

import numpy as np

from skitur.analyze import TrackPoint
from skitur.terrain import get_elevation_grid, get_slope_grid, load_dem_for_bounds

M_TO_FT = 3.28084


def choose_contour_steps_ft(elev_ft_min: float, elev_ft_max: float) -> tuple[int, int]:
    """Pick standard U.S. contour steps from relief."""
    relief = max(0.0, float(elev_ft_max) - float(elev_ft_min))
    if relief > 5000:
        minor = 80
    elif relief > 700:
        minor = 40
    elif relief > 250:
        minor = 20
    else:
        minor = 10
    return minor, minor * 5


def _sample_elevation_grid(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample elevation data for a region. Returns meshes and elevation grid in feet."""
    load_dem_for_bounds(lat_min, lat_max, lon_min, lon_max)
    lon_mesh, lat_mesh, elev_grid = get_elevation_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )
    elev_grid_ft = np.where(np.isnan(elev_grid), np.nan, elev_grid * M_TO_FT)
    return lon_mesh, lat_mesh, elev_grid_ft


def _sample_slope_grid(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample ground slope data for a region."""
    lon_mesh, lat_mesh, slope_grid = get_slope_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )
    return lon_mesh, lat_mesh, slope_grid


def compute_map_grids(
    points: list[TrackPoint],
    padding: float = 0.003,
    resolution: int = 400,
    contour_resolution: int | None = None,
    bounds: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Precompute elevation and slope grids shared by API/report map rendering."""
    if contour_resolution is None:
        contour_resolution = resolution

    if bounds:
        lat_min, lat_max, lon_min, lon_max = bounds
    else:
        lats = [p.lat for p in points]
        lons = [p.lon for p in points]
        lat_min, lat_max = min(lats) - padding, max(lats) + padding
        lon_min, lon_max = min(lons) - padding, max(lons) + padding

    contour_lon_mesh, contour_lat_mesh, contour_elev_grid_ft = _sample_elevation_grid(
        lat_min, lat_max, lon_min, lon_max, contour_resolution
    )
    lon_mesh, lat_mesh, slope_grid = _sample_slope_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )

    return {
        "lon_mesh": lon_mesh,
        "lat_mesh": lat_mesh,
        "slope_grid": slope_grid,
        "contour_lon_mesh": contour_lon_mesh,
        "contour_lat_mesh": contour_lat_mesh,
        "contour_elev_grid_ft": contour_elev_grid_ft,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
    }
