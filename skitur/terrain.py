"""Terrain data access with US high-resolution DEM + global fallback.

Resolution cascade:
- US 3DEP: 10m via USGS
- Copernicus GLO-30: 30m globally

All accessed via dem-stitcher.
"""

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from dem_stitcher import stitch_dem

from skitur.geo import METERS_PER_DEG_LAT

logger = logging.getLogger(__name__)


class ExtentTooLargeError(Exception):
    """Raised when the requested DEM extent exceeds the allowed limit."""

# EPSG:4326 = WGS84 geographic coordinate system (lat/lon)
CRS_WGS84 = 4326

# Persistent tile cache for dem-stitcher (it has no built-in cache)
_TILE_CACHE_DIR = Path.home() / ".cache" / "skitur" / "dem"


@dataclass(frozen=True)
class _DEMSource:
    name: str
    cell_size: float  # meters
    resolution: float  # degrees per pixel


_3DEP = _DEMSource("3dep", cell_size=10.0, resolution=1 / 3600 / 3)
_GLO30 = _DEMSource("glo_30", cell_size=30.0, resolution=1 / 3600)

# US bounding boxes where 3DEP (10m) is available: (lat_min, lat_max, lon_min, lon_max)
_US_BOUNDS = [
    (24, 50, -125, -66),    # CONUS
    (51, 72, -180, -129),   # Alaska
    (18, 23, -161, -154),   # Hawaii
]


def _choose_dem_source(lat: float, lon: float) -> _DEMSource:
    """Pick the best DEM source for a location (3DEP in the US, GLO-30 elsewhere)."""
    for lat_min, lat_max, lon_min, lon_max in _US_BOUNDS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return _3DEP
    return _GLO30


def _fractional_axis_coords(values: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Map coordinates to fractional indices on a uniformly spaced axis."""
    if len(axis) <= 1:
        return np.zeros_like(values, dtype=float)
    step = float(axis[1] - axis[0])
    if step == 0.0:
        return np.zeros_like(values, dtype=float)
    return (values - float(axis[0])) / step


class Terrain:
    """DEM data and terrain analysis methods.

    Holds elevation grid data and provides all terrain queries:
    elevation lookups, slope computation, gradient analysis, etc.
    """

    def __init__(self, x_coords: np.ndarray, y_coords: np.ndarray,
                 data: np.ndarray, cell_size: float) -> None:
        self.x_coords = x_coords    # longitude values (sorted ascending)
        self.y_coords = y_coords    # latitude values (sorted ascending)
        self.data = data            # elevation data[y, x]
        self.cell_size = cell_size
        self._grad_dz_dx: np.ndarray | None = None
        self._grad_dz_dy: np.ndarray | None = None

    @property
    def native_max_dimension(self) -> int:
        """Max(native_rows, native_cols)."""
        return int(max(self.data.shape))

    def covers(self, lat_min: float, lat_max: float,
               lon_min: float, lon_max: float) -> bool:
        """Check if this terrain data covers the requested bounds."""
        if len(self.x_coords) == 0 or len(self.y_coords) == 0:
            return False
        return (self.y_coords[0] <= lat_min and self.y_coords[-1] >= lat_max and
                self.x_coords[0] <= lon_min and self.x_coords[-1] >= lon_max)

    def _ensure_gradient_grids(self) -> None:
        """Compute Horn's method gradient grids on native DEM if not already done."""
        if self._grad_dz_dx is not None:
            return

        elev = self.data
        if elev.shape[0] < 3 or elev.shape[1] < 3:
            self._grad_dz_dx = np.full_like(elev, np.nan)
            self._grad_dz_dy = np.full_like(elev, np.nan)
            return

        # Horn's method on the full grid. Output is 2 cells smaller in each
        # dimension; pad with NaN to keep the same shape/indexing.
        dz_dx_core = (
            (elev[:-2, 2:] + 2*elev[1:-1, 2:] + elev[2:, 2:]) -
            (elev[:-2, :-2] + 2*elev[1:-1, :-2] + elev[2:, :-2])
        ) / (8 * self.cell_size)
        dz_dy_core = (
            (elev[2:, :-2] + 2*elev[2:, 1:-1] + elev[2:, 2:]) -
            (elev[:-2, :-2] + 2*elev[:-2, 1:-1] + elev[:-2, 2:])
        ) / (8 * self.cell_size)

        rows, cols = elev.shape
        self._grad_dz_dx = np.full((rows, cols), np.nan, dtype=np.float32)
        self._grad_dz_dy = np.full((rows, cols), np.nan, dtype=np.float32)
        self._grad_dz_dx[1:-1, 1:-1] = dz_dx_core
        self._grad_dz_dy[1:-1, 1:-1] = dz_dy_core

    # -- Elevation lookups --

    def get_elevation(self, lat: float, lon: float) -> float | None:
        """Get elevation at a point using bilinear interpolation."""
        from scipy.ndimage import map_coordinates

        x_frac = float(_fractional_axis_coords(np.array([lon], dtype=float), self.x_coords)[0])
        y_frac = float(_fractional_axis_coords(np.array([lat], dtype=float), self.y_coords)[0])

        val = map_coordinates(
            self.data, [[y_frac], [x_frac]],
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

        x_frac = _fractional_axis_coords(lons, self.x_coords)
        y_frac = _fractional_axis_coords(lats, self.y_coords)

        return map_coordinates(
            self.data, [y_frac, x_frac],
            order=1, mode='nearest'
        )

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

        x_frac = _fractional_axis_coords(lon_mesh, self.x_coords)
        y_frac = _fractional_axis_coords(lat_mesh, self.y_coords)

        elev_grid = map_coordinates(self.data, [y_frac, x_frac], order=1, mode='nearest')

        return lon_mesh, lat_mesh, elev_grid

    # -- Gradient / slope methods --

    def _horn_gradients_grid(self, lats: np.ndarray, lons: np.ndarray
                              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Look up precomputed Horn gradients at arbitrary (lat, lon) points.

        Returns (dz_dx, dz_dy, invalid) where invalid is True for points
        where any neighbor was NaN (border cells or missing data).
        """
        from scipy.ndimage import map_coordinates

        self._ensure_gradient_grids()
        x_frac = _fractional_axis_coords(lons, self.x_coords)
        y_frac = _fractional_axis_coords(lats, self.y_coords)

        dz_dx = map_coordinates(self._grad_dz_dx, [y_frac, x_frac],
                                order=1, mode='nearest')
        dz_dy = map_coordinates(self._grad_dz_dy, [y_frac, x_frac],
                                order=1, mode='nearest')
        invalid = np.isnan(dz_dx) | np.isnan(dz_dy)
        return dz_dx, dz_dy, invalid

    def horn_gradients(self, lats: np.ndarray, lons: np.ndarray
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Look up Horn's method dz/dx, dz/dy gradients at (lat, lon) points.

        Always uses precomputed native-resolution gradient grids so that all
        callers (track analysis, scoring, map rendering) get consistent slope
        values -- differentiate-then-interpolate, not the reverse.

        Returns (dz_dx, dz_dy, invalid).
        """
        return self._horn_gradients_grid(lats, lons)

    def get_ground_slope(self, lat: float, lon: float) -> float | None:
        """Return terrain slope in degrees at (lat, lon).

        Uses precomputed native-resolution gradient grids (differentiate-then-
        interpolate) for accuracy. Returns None if gradient data is invalid.
        """
        dz_dx, dz_dy, invalid = self.horn_gradients(
            np.array([lat], dtype=float),
            np.array([lon], dtype=float),
        )
        if invalid[0]:
            return None
        slope_rad = math.atan(math.hypot(float(dz_dx[0]), float(dz_dy[0])))
        return math.degrees(slope_rad)

    def get_ground_slopes(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """Return terrain slopes in degrees for arrays of (lat, lon).

        Uses precomputed native-resolution gradient grids (differentiate-then-
        interpolate) so that track analysis agrees with the rendered slope map.
        Returns NaN where data is missing.
        """
        dz_dx, dz_dy, invalid = self.horn_gradients(lats, lons)

        slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
        slopes = np.degrees(slope_rad)
        slopes[invalid] = np.nan

        return slopes

    def get_ground_aspects(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """Return terrain aspect in compass degrees for arrays of (lat, lon).

        0=N, 90=E, 180=S, 270=W. Returns NaN where data missing.
        Uses the same precomputed gradient grids as slope computation.
        """
        dz_dx, dz_dy, invalid = self.horn_gradients(lats, lons)

        # atan2(-dz_dy, dz_dx) gives angle from east, counter-clockwise.
        # Convert to compass bearing: 0=N, 90=E, 180=S, 270=W.
        math_angle = np.degrees(np.arctan2(-dz_dy, dz_dx))
        aspect = (90.0 - math_angle) % 360.0

        aspect[invalid] = np.nan

        return aspect

    def get_slope_grid(
        self, lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get ground slope grid using vectorized Horn's method.

        Computes slopes at the native DEM resolution (where data is most accurate),
        then bilinearly resamples to the requested display resolution. This avoids
        the blocky artifacts that occur when computing slopes from interpolated
        elevation data (which creates piecewise-constant slopes within each DEM cell).

        Returns (lon_mesh, lat_mesh, slope_grid_degrees).
        """
        import cv2

        if len(self.x_coords) == 0:
            # Fallback: compute at display resolution from interpolated elevations
            return self._slope_grid_interpolated(lat_min, lat_max, lon_min, lon_max, resolution)

        # Find the native DEM cells covering the requested bounds (with 1-cell buffer for Horn's)
        xi_min = max(0, int(np.searchsorted(self.x_coords, lon_min)) - 2)
        xi_max = min(len(self.x_coords) - 1, int(np.searchsorted(self.x_coords, lon_max)) + 2)
        yi_min = max(0, int(np.searchsorted(self.y_coords, lat_min)) - 2)
        yi_max = min(len(self.y_coords) - 1, int(np.searchsorted(self.y_coords, lat_max)) + 2)

        # Need at least 3 cells in each direction for Horn's method
        if xi_max - xi_min < 2 or yi_max - yi_min < 2:
            return self._slope_grid_interpolated(lat_min, lat_max, lon_min, lon_max, resolution)

        # Extract the native elevation sub-grid
        elev_sub = self.data[yi_min:yi_max+1, xi_min:xi_max+1]

        # Compute slopes at native resolution using Horn's method
        center_lat = (lat_min + lat_max) / 2
        cell_size_y = self.cell_size  # meters
        cell_size_x = self.cell_size * math.cos(math.radians(center_lat))

        dz_dx = (
            (elev_sub[:-2, 2:] + 2*elev_sub[1:-1, 2:] + elev_sub[2:, 2:]) -
            (elev_sub[:-2, :-2] + 2*elev_sub[1:-1, :-2] + elev_sub[2:, :-2])
        ) / (8 * cell_size_x)
        dz_dy = (
            (elev_sub[2:, :-2] + 2*elev_sub[2:, 1:-1] + elev_sub[2:, 2:]) -
            (elev_sub[:-2, :-2] + 2*elev_sub[:-2, 1:-1] + elev_sub[:-2, 2:])
        ) / (8 * cell_size_y)

        slope_native = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))

        # -- Anti-aliasing before downsampling (moire prevention) --
        # Apply a NaN-safe normalized Gaussian blur, then bilinearly remap to
        # the display lattice so high-frequency slope texture is suppressed while
        # preserving exact output bounds.
        downsample_ratio = max(
            slope_native.shape[0] / resolution,
            slope_native.shape[1] / resolution,
        )
        # sigma = downsample_ratio / 2 is the textbook minimum for anti-aliasing.
        # Skip blur entirely when ratio < 1.2 (negligible downsampling).
        smooth_sigma = downsample_ratio / 2 if downsample_ratio > 1.2 else 0
        # Prepare NaN-safe arrays once, then run a normalized blur/remap pipeline.
        nan_mask = np.isnan(slope_native)
        slope_filled = np.where(nan_mask, 0.0, slope_native).astype(np.float32)
        weights = (~nan_mask).astype(np.float32)

        if smooth_sigma > 0:
            # OpenCV ksize must be odd and positive.
            blur_ksize = max(3, int(round(smooth_sigma * 6)) | 1)
            slope_filled = np.asarray(
                cv2.GaussianBlur(
                    slope_filled,
                    (blur_ksize, blur_ksize),
                    sigmaX=smooth_sigma,
                    sigmaY=smooth_sigma,
                    borderType=cv2.BORDER_REPLICATE,
                ),
                dtype=np.float32,
            )
            weights = np.asarray(
                cv2.GaussianBlur(
                    weights,
                    (blur_ksize, blur_ksize),
                    sigmaX=smooth_sigma,
                    sigmaY=smooth_sigma,
                    borderType=cv2.BORDER_REPLICATE,
                ),
                dtype=np.float32,
            )

        # The native slope grid covers y_coords[yi_min+1:yi_max], x_coords[xi_min+1:xi_max]
        # (Horn's method trims 1 cell on each side)
        native_lats = self.y_coords[yi_min+1:yi_max]
        native_lons = self.x_coords[xi_min+1:xi_max]

        # Build the display grid.
        disp_lons = np.linspace(lon_min, lon_max, resolution)
        disp_lats = np.linspace(lat_min, lat_max, resolution)
        lon_mesh, lat_mesh = np.meshgrid(disp_lons, disp_lats)

        # Convert display lon/lat axes into native slope-grid index space, then
        # expand to full 2D fractional index grids for map_coordinates.
        col_coords = _fractional_axis_coords(disp_lons, native_lons).astype(np.float32)
        row_coords = _fractional_axis_coords(disp_lats, native_lats).astype(np.float32)
        col_map, row_map = np.meshgrid(col_coords, row_coords)

        # Bilinear remap on blurred values and blurred validity weights.
        remapped_sum = cv2.remap(
            slope_filled,
            col_map,
            row_map,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        remapped_w = cv2.remap(
            weights,
            col_map,
            row_map,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )

        slope_display = np.full((resolution, resolution), np.nan, dtype=float)
        valid = remapped_w > 1e-6
        slope_display[valid] = (remapped_sum[valid] / remapped_w[valid]).astype(float)

        return lon_mesh, lat_mesh, slope_display

    def _slope_grid_interpolated(
        self, lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Fallback: compute slopes from interpolated elevations."""
        dlat = (lat_max - lat_min) / resolution
        dlon = (lon_max - lon_min) / resolution

        lon_mesh, lat_mesh, elev = self.get_elevation_grid(
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

        slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
        slope_deg = np.degrees(slope_rad)

        return lon_mesh[1:-1, 1:-1], lat_mesh[1:-1, 1:-1], slope_deg

    def get_path_slope(self, lat1: float, lon1: float,
                       lat2: float, lon2: float) -> float | None:
        """Return slope in degrees from point 1 to point 2.

        Positive = uphill, negative = downhill.
        """
        z1 = self.get_elevation(lat1, lon1)
        z2 = self.get_elevation(lat2, lon2)
        if z1 is None or z2 is None:
            return None

        dlat_m = (lat2 - lat1) * METERS_PER_DEG_LAT
        avg_lat = (lat1 + lat2) / 2
        dlon_m = (lon2 - lon1) * METERS_PER_DEG_LAT * math.cos(math.radians(avg_lat))
        dist_m = math.hypot(dlat_m, dlon_m)

        if dist_m == 0:
            return 0.0

        slope_rad = math.atan2(z2 - z1, dist_m)
        return math.degrees(slope_rad)


# -- Module-level DEM loading --

_dem_lock = threading.Lock()


def _coords_from_profile(
    profile: dict, data: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Extract lon/lat coordinate arrays from a rasterio Affine profile.

    dem-stitcher returns (data, profile) where profile contains an Affine
    transform. This converts it to the x_coords/y_coords arrays that
    Terrain expects (pixel centers).
    """
    transform = profile["transform"]
    rows, cols = data.shape[-2], data.shape[-1]
    # Pixel centers: transform maps (col, row) -> (x, y)
    x_coords = transform.c + (np.arange(cols) + 0.5) * transform.a
    y_coords = transform.f + (np.arange(rows) + 0.5) * transform.e
    return x_coords, y_coords


def _stitch_dem_fast(stitch_fn, **kwargs):
    """Call stitch_dem with rasterio.open patched to use /vsicurl/.

    dem-stitcher opens S3-hosted COG tiles via rasterio.open("https://...s3...").
    rasterio detects the S3 domain and invokes botocore to resolve AWS credentials,
    which takes ~2s per call -- even though these tiles are on PUBLIC buckets that
    need no authentication.

    By prefixing URLs with /vsicurl/, GDAL uses its curl driver instead of the
    S3 driver, bypassing botocore entirely. This saves ~2s on every cold request.
    Safe because _dem_lock serializes all callers.
    """
    import rasterio
    _orig_open = rasterio.open

    def _vsicurl_open(fp, *args, **kw):
        if isinstance(fp, str) and '.s3.amazonaws.com/' in fp:
            fp = f'/vsicurl/{fp}'
        return _orig_open(fp, *args, **kw)

    rasterio.open = _vsicurl_open
    try:
        return stitch_fn(**kwargs)
    finally:
        rasterio.open = _orig_open


def load_dem_for_bounds(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, padding: float = 0.01
) -> Terrain:
    """Load DEM for a bounding box, returning a Terrain object.

    Resolution cascade: US 3DEP (10m) > GLO-30 (30m).
    Tiles are cached on disk so repeat calls read local files instead of S3.
    Thread-safe: lock serializes stitch_dem calls.
    """
    MAX_EXTENT_DEG = 1.0
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
    if lat_span > MAX_EXTENT_DEG or lon_span > MAX_EXTENT_DEG:
        raise ExtentTooLargeError(
            f"Route covers too large an area ({lat_span:.2f} x {lon_span:.2f} deg, "
            f"max {MAX_EXTENT_DEG} x {MAX_EXTENT_DEG} deg)"
        )

    # Add padding
    lat_min -= padding
    lat_max += padding
    lon_min -= padding
    lon_max += padding

    with _dem_lock:
        center_lat = (lat_min + lat_max) / 2
        center_lon = (lon_min + lon_max) / 2
        source = _choose_dem_source(center_lat, center_lon)

        _TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Expand bounds by one pixel so that pixel-center coordinates
        # (from _coords_from_profile) fully cover the requested extent.
        # Without this, the outermost pixel centers fall half a pixel
        # inside the tile edge, causing covers() to fail on repeat calls.
        fetch_bounds = [lon_min - source.resolution, lat_min - source.resolution,
                        lon_max + source.resolution, lat_max + source.resolution]

        logger.info("Fetching DEM (%s) for bounds [%.4f, %.4f, %.4f, %.4f]",
                     source.name, *fetch_bounds)

        tile_cache = _TILE_CACHE_DIR / source.name
        tile_cache.mkdir(parents=True, exist_ok=True)
        stitch_kwargs = dict(
            bounds=fetch_bounds,
            dem_name=source.name,
            dst_ellipsoidal_height=False,  # orthometric heights
            dst_area_or_point="Point",
            dst_resolution=source.resolution,
            dst_tile_dir=tile_cache,
        )
        data, profile = _stitch_dem_fast(stitch_dem, **stitch_kwargs)

        # Squeeze band dimension if present (stitch_dem returns 3D: [1, rows, cols])
        if data.ndim == 3:
            data = data[0]

        x_coords, y_coords = _coords_from_profile(profile, data)

        # Ensure coords are sorted ascending (required for searchsorted)
        if len(x_coords) > 1 and x_coords[0] > x_coords[-1]:
            x_coords = x_coords[::-1]
            data = data[:, ::-1]
        if len(y_coords) > 1 and y_coords[0] > y_coords[-1]:
            y_coords = y_coords[::-1]
            data = data[::-1, :]

        if not np.issubdtype(data.dtype, np.floating):
            raise TypeError(
                f"DEM data has non-float dtype {data.dtype}; expected float32 or float64"
            )

        terrain = Terrain(
            x_coords=x_coords,
            y_coords=y_coords,
            data=data,
            cell_size=source.cell_size,
        )
        return terrain
