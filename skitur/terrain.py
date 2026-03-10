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

from skitur.geo import METERS_PER_DEG_LAT

logger = logging.getLogger(__name__)


class ExtentTooLargeError(Exception):
    """Raised when the requested DEM extent exceeds the allowed limit."""

# EPSG:4326 = WGS84 geographic coordinate system (lat/lon)
CRS_WGS84 = 4326

# Cell size for slope calculations (meters), matching the display resolution
# we request from each DEM source.
CELL_SIZE_3DEP = 10.0    # 3DEP provides 10m resolution in US
CELL_SIZE_GLO30 = 30.0   # Copernicus GLO-30 provides 30m resolution globally

# Persistent tile cache for dem-stitcher (it has no built-in cache)
_TILE_CACHE_DIR = Path.home() / ".cache" / "skitur" / "dem"


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

    def _horn_gradients_pointwise(self, lats: np.ndarray, lons: np.ndarray,
                                   cell_size_m: float | None = None
                                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Compute Horn's method dz/dx, dz/dy gradients for arrays of (lat, lon).

        Per-point 8-neighbor method. Slower than precomputed grids but works
        for any point set without upfront cost.

        Returns (dz_dx, dz_dy, any_nan).
        """
        if cell_size_m is None:
            cell_size_m = self.cell_size

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

        all_elevs = self.get_elevations(all_lats, all_lons)

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

        # Mask where any neighbor was NaN
        any_nan = (np.isnan(a) | np.isnan(b) | np.isnan(c) | np.isnan(d) |
                   np.isnan(f) | np.isnan(g) | np.isnan(h) | np.isnan(i))

        return dz_dx, dz_dy, any_nan

    def horn_gradients(self, lats: np.ndarray, lons: np.ndarray
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Compute Horn's method dz/dx, dz/dy gradients at (lat, lon) points.

        Uses precomputed gradient grids when available (fast path for small DEMs).
        Falls back to per-point 8-neighbor method for large DEMs where
        precomputation is too expensive.

        Returns (dz_dx, dz_dy, invalid).
        """
        if len(self.x_coords) > 0:
            # Only use precomputed grids if they're already computed or cheap to compute.
            # For large DEMs (>10M pixels) the 0.7s+ grid computation isn't worth it
            # for routes with few points.
            if self._grad_dz_dx is not None:
                return self._horn_gradients_grid(lats, lons)
            if self.data.size <= 10_000_000:
                return self._horn_gradients_grid(lats, lons)
        return self._horn_gradients_pointwise(lats, lons)

    def get_ground_slope(self, lat: float, lon: float,
                         cell_size_m: float | None = None) -> float | None:
        """Return terrain slope in degrees at (lat, lon) using Horn's method.

        Uses a 3x3 window centered on the query position with Sobel-like weighting.
        """
        if cell_size_m is None:
            cell_size_m = self.cell_size

        # Convert cell size to degrees
        dlat = cell_size_m / METERS_PER_DEG_LAT
        dlon = cell_size_m / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))

        # Sample 3x3 window
        a = self.get_elevation(lat + dlat, lon - dlon)  # NW
        b = self.get_elevation(lat + dlat, lon)          # N
        c = self.get_elevation(lat + dlat, lon + dlon)  # NE
        d = self.get_elevation(lat, lon - dlon)          # W
        f = self.get_elevation(lat, lon + dlon)          # E
        g = self.get_elevation(lat - dlat, lon - dlon)  # SW
        h = self.get_elevation(lat - dlat, lon)          # S
        i = self.get_elevation(lat - dlat, lon + dlon)  # SE

        if None in (a, b, c, d, f, g, h, i):
            return None
        # mypy can't narrow through `in` checks on tuples
        assert (a is not None and b is not None and c is not None
                and d is not None and f is not None
                and g is not None and h is not None and i is not None)

        # Horn's method (1981) with Sobel-like weighting
        dz_dx = ((c + 2*f + i) - (a + 2*d + g)) / (8 * cell_size_m)
        dz_dy = ((g + 2*h + i) - (a + 2*b + c)) / (8 * cell_size_m)

        slope_rad = math.atan(math.hypot(dz_dx, dz_dy))
        return math.degrees(slope_rad)

    def get_ground_slopes(self, lats: np.ndarray, lons: np.ndarray,
                          cell_size_m: float | None = None) -> np.ndarray:
        """Return terrain slopes in degrees for arrays of (lat, lon).

        Vectorized Horn's method on scattered points. Returns NaN where data missing.
        """
        dz_dx, dz_dy, any_nan = self._horn_gradients_pointwise(lats, lons, cell_size_m)

        slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
        slopes = np.degrees(slope_rad)
        slopes[any_nan] = np.nan

        return slopes

    def get_ground_aspects(self, lats: np.ndarray, lons: np.ndarray,
                           cell_size_m: float | None = None) -> np.ndarray:
        """Return terrain aspect in compass degrees for arrays of (lat, lon).

        0=N, 90=E, 180=S, 270=W. Returns NaN where data missing.
        Uses the same Horn's method gradients as slope computation.
        """
        dz_dx, dz_dy, any_nan = self._horn_gradients_pointwise(lats, lons, cell_size_m)

        # atan2(-dz_dy, dz_dx) gives angle from east, counter-clockwise.
        # Convert to compass bearing: 0=N, 90=E, 180=S, 270=W.
        # Compass bearing = 90 - math_angle (in degrees), then mod 360.
        math_angle = np.degrees(np.arctan2(-dz_dy, dz_dx))
        aspect = (90.0 - math_angle) % 360.0

        aspect[any_nan] = np.nan

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
        from dem_stitcher import stitch_dem
        center_lat = (lat_min + lat_max) / 2
        center_lon = (lon_min + lon_max) / 2
        is_us = _is_us_coverage(center_lat, center_lon)
        if is_us:
            dem_name = "3dep"
            cell_size = CELL_SIZE_3DEP
            res = 1 / 3600 / 3
        else:
            dem_name = "glo_30"
            cell_size = CELL_SIZE_GLO30
            res = 1 / 3600

        _TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Expand bounds by one pixel so that pixel-center coordinates
        # (from _coords_from_profile) fully cover the requested extent.
        # Without this, the outermost pixel centers fall half a pixel
        # inside the tile edge, causing covers() to fail on repeat calls.
        fetch_bounds = [lon_min - res, lat_min - res,
                        lon_max + res, lat_max + res]

        logger.info("Fetching DEM (%s) for bounds [%.4f, %.4f, %.4f, %.4f]",
                     dem_name, *fetch_bounds)

        tile_cache = _TILE_CACHE_DIR / dem_name
        tile_cache.mkdir(parents=True, exist_ok=True)
        stitch_kwargs = dict(
            bounds=fetch_bounds,
            dem_name=dem_name,
            dst_ellipsoidal_height=False,  # orthometric heights
            dst_area_or_point="Point",
            dst_resolution=res,
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

        # Ensure float dtype for scipy.map_coordinates
        if not np.issubdtype(data.dtype, np.floating):
            data = data.astype(np.float32)

        terrain = Terrain(
            x_coords=x_coords,
            y_coords=y_coords,
            data=data,
            cell_size=cell_size,
        )
        return terrain
