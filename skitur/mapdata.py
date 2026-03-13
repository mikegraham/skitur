"""Non-plot map data helpers for slope/elevation grids and contour intervals."""

from __future__ import annotations

from typing import Any

import numpy as np

from skitur import analyze, terrain

M_TO_FT = 3.28084


def choose_contour_steps_ft(elev_ft_min: float, elev_ft_max: float) -> tuple[int, int]:
    """Pick standard U.S. contour steps from relief, preferring 40ft minor."""
    relief = max(0.0, float(elev_ft_max) - float(elev_ft_min))
    if relief > 5000:
        minor = 80
    elif relief > 200:
        minor = 40
    else:
        minor = 20
    return minor, minor * 5


def sample_elevation_grid(
    dem: terrain.Terrain,
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample elevation data for a region. Returns meshes and elevation grid in feet."""
    lon_mesh, lat_mesh, elev_grid = dem.get_elevation_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )
    elev_grid_ft = np.where(np.isnan(elev_grid), np.nan, elev_grid * M_TO_FT)
    return lon_mesh, lat_mesh, elev_grid_ft


def sample_slope_grid(
    dem: terrain.Terrain,
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample ground slope data for a region."""
    lon_mesh, lat_mesh, slope_grid = dem.get_slope_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )
    return lon_mesh, lat_mesh, slope_grid


def compute_map_grids(
    dem: terrain.Terrain,
    points: list[analyze.TrackPoint],
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

    contour_lon_mesh, contour_lat_mesh, contour_elev_grid_ft = sample_elevation_grid(
        dem, lat_min, lat_max, lon_min, lon_max, contour_resolution
    )
    lon_mesh, lat_mesh, slope_grid = sample_slope_grid(
        dem, lat_min, lat_max, lon_min, lon_max, resolution
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
