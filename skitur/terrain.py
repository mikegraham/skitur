"""Terrain data access using USGS 3DEP (US) and SRTM (global).

3DEP provides 10m resolution for US locations.
SRTM provides 30m resolution globally as fallback.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import srtm

from skitur.geo import METERS_PER_DEG_LAT

# Lazy-load seamless_3dep to avoid slow import at startup
_s3dep = None


def _get_s3dep():
    global _s3dep
    if _s3dep is None:
        import seamless_3dep
        _s3dep = seamless_3dep
    return _s3dep


# EPSG:4326 = WGS84 geographic coordinate system (lat/lon)
CRS_WGS84 = 4326

# Cell size for slope calculations (meters)
CELL_SIZE_3DEP = 10.0  # 3DEP provides 10m resolution in US
CELL_SIZE_SRTM = 30.0  # SRTM provides 30m resolution globally

_srtm_data = srtm.get_data()


def _is_us_coverage(lat: float, lon: float) -> bool:
    """Check if location is within approximate US 3DEP coverage."""
    # CONUS: lat 24-50, lon -125 to -66
    if 24 <= lat <= 50 and -125 <= lon <= -66:
        return True
    # Alaska: lat 51-72, lon -180 to -129
    if 51 <= lat <= 72 and -180 <= lon <= -129:
        return True
    # Hawaii: lat 18-23, lon -161 to -154
    if 18 <= lat <= 23 and -161 <= lon <= -154:
        return True
    return False


@dataclass
class DEMCache:
    """Cached DEM as numpy arrays for fast lookups."""

    x_coords: np.ndarray  # longitude values (sorted ascending)
    y_coords: np.ndarray  # latitude values (sorted ascending)
    data: np.ndarray      # elevation data[y, x]
    cell_size: float
    is_us: bool

    def covers(self, lat_min: float, lat_max: float,
               lon_min: float, lon_max: float) -> bool:
        """Check if this cache already covers the requested bounds."""
        if len(self.x_coords) == 0 or len(self.y_coords) == 0:
            return False
        return (self.y_coords[0] <= lat_min and self.y_coords[-1] >= lat_max and
                self.x_coords[0] <= lon_min and self.x_coords[-1] >= lon_max)

    def get_elevation(self, lat: float, lon: float) -> float | None:
        """Get elevation at a point using bilinear interpolation."""
        from scipy.ndimage import map_coordinates

        x_frac = float(np.interp(lon, self.x_coords, np.arange(len(self.x_coords))))
        y_frac = float(np.interp(lat, self.y_coords, np.arange(len(self.y_coords))))

        val = map_coordinates(
            self.data.astype(np.float64), [[y_frac], [x_frac]],
            order=1, mode='nearest'
        )[0]

        if np.isnan(val):
            return None
        return float(val)

    def get_elevations(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """Get elevations for arrays of points using bilinear interpolation.

        Bilinear interpolation produces smooth elevation values between DEM cells,
        avoiding the stair-step artifacts that nearest-neighbor creates with
        integer-meter SRTM data.
        """
        from scipy.ndimage import map_coordinates

        # Convert lat/lon to fractional indices in the DEM grid
        x_frac = np.interp(lons, self.x_coords, np.arange(len(self.x_coords)))
        y_frac = np.interp(lats, self.y_coords, np.arange(len(self.y_coords)))

        elevs = map_coordinates(
            self.data.astype(np.float64), [y_frac, x_frac],
            order=1, mode='nearest'
        )

        return elevs

    def get_elevation_grid(
        self, lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get elevation grid using bilinear interpolation.

        Returns (lon_mesh, lat_mesh, elev_grid).
        """
        from scipy.ndimage import map_coordinates

        lons = np.linspace(lon_min, lon_max, resolution)
        lats = np.linspace(lat_min, lat_max, resolution)
        lon_mesh, lat_mesh = np.meshgrid(lons, lats)

        x_frac = np.interp(lon_mesh, self.x_coords, np.arange(len(self.x_coords)))
        y_frac = np.interp(lat_mesh, self.y_coords, np.arange(len(self.y_coords)))

        elev_grid = map_coordinates(self.data, [y_frac, x_frac], order=1, mode='nearest')

        return lon_mesh, lat_mesh, elev_grid


# Module-level cache for the current region
_dem_cache: DEMCache | None = None


def load_dem_for_bounds(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, padding: float = 0.01
) -> DEMCache:
    """Load DEM for a bounding box, caching for repeated queries.

    Downloads 3DEP data for US locations, uses SRTM for elsewhere.
    Skips the download if the in-memory cache already covers the requested bounds.
    """
    global _dem_cache

    # Add padding
    lat_min -= padding
    lat_max += padding
    lon_min -= padding
    lon_max += padding

    # Skip download if cache already covers this region
    if _dem_cache is not None and _dem_cache.covers(lat_min, lat_max, lon_min, lon_max):
        return _dem_cache

    center_lat = (lat_min + lat_max) / 2
    center_lon = (lon_min + lon_max) / 2
    is_us = _is_us_coverage(center_lat, center_lon)

    if is_us:
        bbox = (lon_min, lat_min, lon_max, lat_max)
        s3dep = _get_s3dep()
        cache_dir = Path.home() / ".cache" / "skitur" / "3dep"
        cache_dir.mkdir(parents=True, exist_ok=True)
        tiff_files = s3dep.get_dem(bbox, cache_dir, res=10)
        dem = s3dep.tiffs_to_da(tiff_files, bbox, crs=CRS_WGS84)

        # Extract numpy arrays for fast access
        x_coords = dem.x.values
        y_coords = dem.y.values
        data = dem.values

        # Ensure coords are sorted ascending (required for searchsorted)
        if x_coords[0] > x_coords[-1]:
            x_coords = x_coords[::-1]
            data = data[:, ::-1]
        if y_coords[0] > y_coords[-1]:
            y_coords = y_coords[::-1]
            data = data[::-1, :]

        cell_size = CELL_SIZE_3DEP
    else:
        # Build an SRTM grid cache so we get bilinear interpolation
        # instead of nearest-neighbor point queries (avoids moire).
        # SRTM is ~30m (1 arc-second ≈ 0.000278°)
        srtm_step = 1 / 3600  # 1 arc-second
        x_coords = np.arange(lon_min, lon_max + srtm_step, srtm_step)
        y_coords = np.arange(lat_min, lat_max + srtm_step, srtm_step)
        data = np.full((len(y_coords), len(x_coords)), np.nan)
        for yi, lat in enumerate(y_coords):
            for xi, lon in enumerate(x_coords):
                e = _srtm_data.get_elevation(float(lat), float(lon))
                if e is not None:
                    data[yi, xi] = float(e)
        cell_size = CELL_SIZE_SRTM

    _dem_cache = DEMCache(
        x_coords=x_coords,
        y_coords=y_coords,
        data=data,
        cell_size=cell_size,
        is_us=is_us,
    )
    return _dem_cache


def get_elevation(lat: float, lon: float) -> float | None:
    """Return elevation in meters at (lat, lon), or None if no data."""
    if _dem_cache is not None and len(_dem_cache.x_coords) > 0:
        return _dem_cache.get_elevation(lat, lon)
    return _srtm_data.get_elevation(lat, lon)


def get_elevations(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Return elevations for arrays of (lat, lon). NaN where no data."""
    if _dem_cache is not None and len(_dem_cache.x_coords) > 0:
        return _dem_cache.get_elevations(lats, lons)
    result = np.full(len(lats), np.nan)
    for i in range(len(lats)):
        e = _srtm_data.get_elevation(float(lats[i]), float(lons[i]))
        if e is not None:
            result[i] = e
    return result


def get_elevation_grid(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Get elevation grid for a region. Returns (lon_mesh, lat_mesh, elev_grid_meters)."""
    if _dem_cache is not None and len(_dem_cache.x_coords) > 0:
        return _dem_cache.get_elevation_grid(lat_min, lat_max, lon_min, lon_max, resolution)

    # Fallback to point-by-point SRTM queries (no cache available)
    lons = np.linspace(lon_min, lon_max, resolution)
    lats = np.linspace(lat_min, lat_max, resolution)
    lon_mesh, lat_mesh = np.meshgrid(lons, lats)

    elev_grid = np.zeros_like(lat_mesh)
    for i in range(resolution):
        for j in range(resolution):
            e = _srtm_data.get_elevation(lat_mesh[i, j], lon_mesh[i, j])
            elev_grid[i, j] = e if e is not None else np.nan

    return lon_mesh, lat_mesh, elev_grid


def get_ground_slope(lat: float, lon: float, cell_size_m: float | None = None) -> float | None:
    """Return terrain slope in degrees at (lat, lon) using Horn's method.

    Uses a 3x3 window centered on the query position with Sobel-like weighting.
    Cell size defaults to 10m for US (3DEP) or 30m elsewhere (SRTM).
    """
    if cell_size_m is None:
        if _dem_cache is not None:
            cell_size_m = _dem_cache.cell_size
        else:
            cell_size_m = CELL_SIZE_3DEP if _is_us_coverage(lat, lon) else CELL_SIZE_SRTM

    # Convert cell size to degrees
    dlat = cell_size_m / METERS_PER_DEG_LAT
    dlon = cell_size_m / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))

    # Sample 3x3 window
    a = get_elevation(lat + dlat, lon - dlon)  # NW
    b = get_elevation(lat + dlat, lon)          # N
    c = get_elevation(lat + dlat, lon + dlon)  # NE
    d = get_elevation(lat, lon - dlon)          # W
    f = get_elevation(lat, lon + dlon)          # E
    g = get_elevation(lat - dlat, lon - dlon)  # SW
    h = get_elevation(lat - dlat, lon)          # S
    i = get_elevation(lat - dlat, lon + dlon)  # SE

    if None in (a, b, c, d, f, g, h, i):
        return None
    # mypy can't narrow through `in` checks on tuples
    assert (a is not None and b is not None and c is not None
            and d is not None and f is not None
            and g is not None and h is not None and i is not None)

    # Horn's method (1981) with Sobel-like weighting
    dz_dx = ((c + 2*f + i) - (a + 2*d + g)) / (8 * cell_size_m)
    dz_dy = ((g + 2*h + i) - (a + 2*b + c)) / (8 * cell_size_m)

    slope_rad = math.atan(math.sqrt(dz_dx**2 + dz_dy**2))
    return math.degrees(slope_rad)


def get_ground_slopes(lats: np.ndarray, lons: np.ndarray,
                      cell_size_m: float | None = None) -> np.ndarray:
    """Return terrain slopes in degrees for arrays of (lat, lon).

    Vectorized Horn's method on scattered points. Returns NaN where data missing.
    """
    if cell_size_m is None:
        if _dem_cache is not None:
            cell_size_m = _dem_cache.cell_size
        else:
            cell_size_m = CELL_SIZE_3DEP

    # Convert cell size to degrees (use mean latitude for lon scaling)
    dlat = cell_size_m / METERS_PER_DEG_LAT
    mean_lat = np.mean(lats) if len(lats) > 0 else 45.0
    dlon = cell_size_m / (METERS_PER_DEG_LAT * math.cos(math.radians(float(mean_lat))))

    # Build all 8 neighbor coordinate arrays: NW, N, NE, W, E, SW, S, SE
    offsets = [
        (+dlat, -dlon),  # NW = a
        (+dlat,    0.0),  # N  = b
        (+dlat, +dlon),  # NE = c
        (  0.0, -dlon),  # W  = d
        (  0.0, +dlon),  # E  = f
        (-dlat, -dlon),  # SW = g
        (-dlat,    0.0),  # S  = h
        (-dlat, +dlon),  # SE = i
    ]

    # Stack all neighbor coords into one big array and call get_elevations once
    n = len(lats)
    all_lats = np.empty(8 * n)
    all_lons = np.empty(8 * n)
    for k, (dy, dx) in enumerate(offsets):
        all_lats[k*n:(k+1)*n] = lats + dy
        all_lons[k*n:(k+1)*n] = lons + dx

    all_elevs = get_elevations(all_lats, all_lons)

    # Unpack into individual neighbor arrays
    a = all_elevs[0*n:1*n]  # NW
    b = all_elevs[1*n:2*n]  # N
    c = all_elevs[2*n:3*n]  # NE
    d = all_elevs[3*n:4*n]  # W
    f = all_elevs[4*n:5*n]  # E
    g = all_elevs[5*n:6*n]  # SW
    h = all_elevs[6*n:7*n]  # S
    i = all_elevs[7*n:8*n]  # SE

    # Horn's method (1981) with Sobel-like weighting
    dz_dx = ((c + 2*f + i) - (a + 2*d + g)) / (8 * cell_size_m)
    dz_dy = ((g + 2*h + i) - (a + 2*b + c)) / (8 * cell_size_m)

    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    slopes = np.degrees(slope_rad)

    # Mark NaN where any neighbor was NaN
    any_nan = (np.isnan(a) | np.isnan(b) | np.isnan(c) | np.isnan(d) |
               np.isnan(f) | np.isnan(g) | np.isnan(h) | np.isnan(i))
    slopes[any_nan] = np.nan

    return slopes


def get_slope_grid(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Get ground slope grid using vectorized Horn's method.

    Computes slopes at the native DEM resolution (where data is most accurate),
    then bilinearly resamples to the requested display resolution. This avoids
    the blocky artifacts that occur when computing slopes from interpolated
    elevation data (which creates piecewise-constant slopes within each DEM cell).

    Returns (lon_mesh, lat_mesh, slope_grid_degrees).
    """
    from scipy.ndimage import map_coordinates

    if _dem_cache is None or len(_dem_cache.x_coords) == 0:
        # Fallback: compute at display resolution from interpolated elevations
        return _slope_grid_interpolated(lat_min, lat_max, lon_min, lon_max, resolution)

    # Find the native DEM cells covering the requested bounds (with 1-cell buffer for Horn's)
    x_coords = _dem_cache.x_coords
    y_coords = _dem_cache.y_coords
    elev_native = _dem_cache.data
    cell_size = _dem_cache.cell_size

    # Index range in the native DEM that covers our bounds
    xi_min = max(0, np.searchsorted(x_coords, lon_min) - 2)
    xi_max = min(len(x_coords) - 1, np.searchsorted(x_coords, lon_max) + 2)
    yi_min = max(0, np.searchsorted(y_coords, lat_min) - 2)
    yi_max = min(len(y_coords) - 1, np.searchsorted(y_coords, lat_max) + 2)

    # Need at least 3 cells in each direction for Horn's method
    if xi_max - xi_min < 2 or yi_max - yi_min < 2:
        return _slope_grid_interpolated(lat_min, lat_max, lon_min, lon_max, resolution)

    # Extract the native elevation sub-grid
    elev_sub = elev_native[yi_min:yi_max+1, xi_min:xi_max+1].astype(np.float64)

    # Compute slopes at native resolution using Horn's method
    center_lat = (lat_min + lat_max) / 2
    cell_size_y = cell_size  # meters
    cell_size_x = cell_size * math.cos(math.radians(center_lat))

    dz_dx = (
        (elev_sub[:-2, 2:] + 2*elev_sub[1:-1, 2:] + elev_sub[2:, 2:]) -
        (elev_sub[:-2, :-2] + 2*elev_sub[1:-1, :-2] + elev_sub[2:, :-2])
    ) / (8 * cell_size_x)
    dz_dy = (
        (elev_sub[2:, :-2] + 2*elev_sub[2:, 1:-1] + elev_sub[2:, 2:]) -
        (elev_sub[:-2, :-2] + 2*elev_sub[:-2, 1:-1] + elev_sub[:-2, 2:])
    ) / (8 * cell_size_y)

    slope_native = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))

    # ── Anti-aliasing Gaussian blur (moire prevention) ──
    #
    # When the display grid is smaller than the native DEM, downsampling
    # aliases high-frequency slope variations into speckled moire patterns.
    # A Gaussian blur before downsampling acts as a low-pass filter to
    # suppress frequencies above the display grid's Nyquist limit.
    #
    # When the display grid matches the native DEM (ratio ≈ 1), no blur
    # is needed — the data is shown at its natural resolution.
    from scipy.ndimage import gaussian_filter
    downsample_ratio = max(
        slope_native.shape[0] / resolution,
        slope_native.shape[1] / resolution,
    )
    # sigma = downsample_ratio / 2 is the textbook minimum for anti-aliasing.
    # Skip blur entirely when ratio < 1.2 (negligible downsampling).
    smooth_sigma = downsample_ratio / 2 if downsample_ratio > 1.2 else 0
    if smooth_sigma > 0:
        nan_mask = np.isnan(slope_native)
        if np.any(nan_mask):
            # NaN-safe smoothing via normalized convolution
            slope_filled = np.where(nan_mask, 0.0, slope_native)
            weights = (~nan_mask).astype(np.float64)
            smoothed_num = gaussian_filter(slope_filled, sigma=smooth_sigma)
            smoothed_den = gaussian_filter(weights, sigma=smooth_sigma)
            slope_native = np.where(smoothed_den > 0.01, smoothed_num / smoothed_den, np.nan)
        else:
            slope_native = gaussian_filter(slope_native, sigma=smooth_sigma)

    # The native slope grid covers y_coords[yi_min+1:yi_max], x_coords[xi_min+1:xi_max]
    # (Horn's method trims 1 cell on each side)
    native_lats = y_coords[yi_min+1:yi_max]
    native_lons = x_coords[xi_min+1:xi_max]

    # Build the display grid
    disp_lons = np.linspace(lon_min, lon_max, resolution)
    disp_lats = np.linspace(lat_min, lat_max, resolution)
    lon_mesh, lat_mesh = np.meshgrid(disp_lons, disp_lats)

    # Map display coordinates to fractional indices in the native slope grid
    col_frac = np.interp(lon_mesh.ravel(), native_lons, np.arange(len(native_lons)))
    row_frac = np.interp(lat_mesh.ravel(), native_lats, np.arange(len(native_lats)))

    # Replace NaN with 0 for interpolation (NaN causes map_coordinates issues)
    nan_mask = np.isnan(slope_native)
    slope_clean = np.where(nan_mask, 0.0, slope_native)

    # Bilinear resample native slopes to display resolution
    slope_display = map_coordinates(slope_clean, [row_frac, col_frac],
                                     order=1, mode='nearest')
    slope_display = slope_display.reshape(resolution, resolution)

    # Restore NaN where native data was missing (check nearest native cell)
    if np.any(nan_mask):
        nan_check = map_coordinates(nan_mask.astype(np.float64),
                                     [row_frac, col_frac],
                                     order=0, mode='nearest')
        slope_display[nan_check.reshape(resolution, resolution) > 0.5] = np.nan

    return lon_mesh, lat_mesh, slope_display


def _slope_grid_interpolated(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fallback: compute slopes from interpolated elevations (used when no DEM cache)."""
    dlat = (lat_max - lat_min) / resolution
    dlon = (lon_max - lon_min) / resolution

    lon_mesh, lat_mesh, elev = get_elevation_grid(
        lat_min - dlat, lat_max + dlat,
        lon_min - dlon, lon_max + dlon,
        resolution + 2
    )

    center_lat = (lat_min + lat_max) / 2
    cell_size_y = dlat * METERS_PER_DEG_LAT
    cell_size_x = dlon * METERS_PER_DEG_LAT * math.cos(math.radians(center_lat))

    dz_dx = (
        (elev[:-2, 2:] + 2*elev[1:-1, 2:] + elev[2:, 2:]) -
        (elev[:-2, :-2] + 2*elev[1:-1, :-2] + elev[2:, :-2])
    ) / (8 * cell_size_x)
    dz_dy = (
        (elev[2:, :-2] + 2*elev[2:, 1:-1] + elev[2:, 2:]) -
        (elev[:-2, :-2] + 2*elev[:-2, 1:-1] + elev[:-2, 2:])
    ) / (8 * cell_size_y)

    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    slope_deg = np.degrees(slope_rad)

    return lon_mesh[1:-1, 1:-1], lat_mesh[1:-1, 1:-1], slope_deg


def get_path_slope(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    """Return slope in degrees from point 1 to point 2.

    Positive = uphill, negative = downhill.
    """
    z1 = get_elevation(lat1, lon1)
    z2 = get_elevation(lat2, lon2)
    if z1 is None or z2 is None:
        return None

    dlat_m = (lat2 - lat1) * METERS_PER_DEG_LAT
    avg_lat = (lat1 + lat2) / 2
    dlon_m = (lon2 - lon1) * METERS_PER_DEG_LAT * math.cos(math.radians(avg_lat))
    dist_m = math.sqrt(dlat_m**2 + dlon_m**2)

    if dist_m == 0:
        return 0.0

    slope_rad = math.atan2(z2 - z1, dist_m)
    return math.degrees(slope_rad)
