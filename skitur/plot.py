from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

from skitur.analyze import TrackPoint
from skitur.terrain import (
    get_elevation_grid,
    get_slope_grid,
    load_dem_for_bounds,
)
from skitur.geo import haversine_distance

# Unit conversions (applied at plot time)
M_TO_FT = 3.28084
KM_TO_MI = 0.621371

# Contour intervals in feet
CONTOUR_MINOR = 40   # thin lines
CONTOUR_MAJOR = 200  # bold + labeled


def _add_track_slope_colorbar(fig, ax, sm, shrink=0.5, pad=0.02):
    """Add track slope colorbar with ticks every degree, labels at 0,5,10,15,20."""
    from matplotlib.ticker import FixedLocator, FixedFormatter

    cbar = fig.colorbar(sm, ax=ax, shrink=shrink, pad=pad)

    # Major ticks (labeled) at 0, 5, 10, 15, 20
    major_ticks = [0, 5, 10, 15, 20]
    cbar.ax.yaxis.set_major_locator(FixedLocator(major_ticks))
    cbar.ax.yaxis.set_major_formatter(FixedFormatter([str(t) for t in major_ticks]))

    # Minor ticks (unlabeled) at every degree
    minor_ticks = [i for i in range(0, 21) if i not in major_ticks]
    cbar.ax.yaxis.set_minor_locator(FixedLocator(minor_ticks))

    # Keep minor ticks subtle
    cbar.ax.tick_params(axis='y', which='minor', length=2, width=0.5)

    cbar.set_label('Track slope (deg)', fontsize=9)
    return cbar


def _add_ground_slope_colorbar(fig, ax, sm, shrink=0.5, pad=0.02):
    """Add ground slope colorbar with ticks every degree up to 45, labels every 5."""
    from matplotlib.ticker import FixedLocator, FixedFormatter

    cbar = fig.colorbar(sm, ax=ax, shrink=shrink, pad=pad)

    # Major ticks (labeled) every 5 degrees, only up to 45
    major_ticks = list(range(0, 46, 5))
    cbar.ax.yaxis.set_major_locator(FixedLocator(major_ticks))
    cbar.ax.yaxis.set_major_formatter(FixedFormatter([str(t) for t in major_ticks]))

    # Minor ticks (unlabeled) at every degree, only up to 45
    minor_ticks = [i for i in range(0, 46) if i not in major_ticks]
    cbar.ax.yaxis.set_minor_locator(FixedLocator(minor_ticks))

    # Keep minor ticks subtle
    cbar.ax.tick_params(axis='y', which='minor', length=2, width=0.5)

    cbar.set_label('Ground slope (deg)', fontsize=9)
    return cbar


def _add_elevation_colorbar(fig, ax, cf, elev_min: float, elev_max: float,
                            shrink=0.5, pad=0.02):
    """Add elevation colorbar with labels at multiples of 200ft."""
    from matplotlib.ticker import FixedLocator, FixedFormatter

    cbar = fig.colorbar(cf, ax=ax, shrink=shrink, pad=pad)

    # Find multiples of 200 in range (no extremal values)
    first_200 = int(np.ceil(elev_min / 200) * 200)
    last_200 = int(np.floor(elev_max / 200) * 200)
    ticks = list(range(first_200, last_200 + 1, 200))
    labels = [f'{t}' for t in ticks]

    cbar.ax.yaxis.set_major_locator(FixedLocator(ticks))
    cbar.ax.yaxis.set_major_formatter(FixedFormatter(labels))

    cbar.set_label('Elevation (ft)', fontsize=9)
    return cbar


def _make_ground_cmap():
    """Milky teal->green->yellow 0-25, muted orange 25-30, bold orange->red 30-45, gray >45.

    The 30-degree boundary has a dramatic saturation shift (faint -> vivid)
    to make the avalanche-terrain threshold unmistakable.
    """
    from matplotlib.colors import LinearSegmentedColormap

    colors = []
    # Proportional to 0-47 deg range
    n_total = 250
    n_safe = round(n_total * 25 / 47)       # 0-25 degrees (milky teal/green/yellow)
    n_warning = round(n_total * 5 / 47)     # 25-30 degrees (muted orange)
    n_danger = round(n_total * 15 / 47)     # 30-45 degrees (bold orange/red)
    n_gray = n_total - n_safe - n_warning - n_danger  # 45-47 degrees

    # 0-25: milky/faint teal -> green -> yellow (very desaturated, pastel)
    for i in range(n_safe):
        t = i / n_safe
        if t < 0.5:
            # faint teal to faint green
            s = t / 0.5
            r = 0.70 + 0.05 * s
            g = 0.78 + 0.07 * s
            b = 0.72 - 0.12 * s
        else:
            # faint green to faint yellow
            s = (t - 0.5) / 0.5
            r = 0.75 + 0.10 * s
            g = 0.85 + 0.02 * s
            b = 0.60 - 0.22 * s
        colors.append((r, g, b))

    # 25-30: muted orange warning zone (still faint, but clearly orange)
    for i in range(n_warning):
        t = i / max(n_warning, 1)
        r = 0.85 + 0.03 * t
        g = 0.72 - 0.12 * t
        b = 0.38 - 0.05 * t
        colors.append((r, g, b))

    # 30-45: HARD transition -- bold, saturated orange -> red -> dark red
    # Saturation jumps dramatically at 30 deg: faint -> vivid
    for i in range(n_danger):
        t = i / max(n_danger, 1)
        if t < 0.3:
            # Bold orange (30-34.5 deg) — G starts at 0.25 for sharp contrast
            s = t / 0.3
            r = 0.92 - 0.04 * s
            g = 0.25 - 0.05 * s
            b = 0.05 + 0.02 * s
        elif t < 0.65:
            # Bold red (34.5-39.75 deg)
            s = (t - 0.3) / 0.35
            r = 0.88 - 0.10 * s
            g = 0.20 - 0.05 * s
            b = 0.07 - 0.02 * s
        else:
            # Dark red (39.75-45 deg)
            s = (t - 0.65) / 0.35
            r = 0.78 - 0.23 * s
            g = 0.15 - 0.05 * s
            b = 0.08 - 0.02 * s
        colors.append((r, g, b))

    # >45: gray (too steep to hold snow)
    for i in range(n_gray):
        colors.append((0.5, 0.5, 0.5))

    cmap = LinearSegmentedColormap.from_list('ground', colors, N=n_total)
    norm = mcolors.Normalize(vmin=0, vmax=47)
    return cmap, norm


def _make_elevation_cmap():
    """Custom elevation colormap: tan -> green -> brown -> white (no blue)."""
    from matplotlib.colors import LinearSegmentedColormap

    # Hypsometric tinting: low=tan, mid=green, high=brown/gray/white
    colors = [
        (0.76, 0.70, 0.55),  # tan/beige at lowest
        (0.68, 0.78, 0.55),  # yellow-green
        (0.45, 0.68, 0.40),  # green
        (0.35, 0.55, 0.35),  # darker green
        (0.55, 0.50, 0.40),  # brown-green transition
        (0.65, 0.55, 0.45),  # light brown
        (0.75, 0.70, 0.65),  # tan/gray
        (0.90, 0.88, 0.85),  # near white at highest
    ]

    return LinearSegmentedColormap.from_list('elevation', colors, N=256)


def _make_track_cmap():
    """Green -> yellow -> orange -> red -> black over 0-20 degrees."""
    from matplotlib.colors import LinearSegmentedColormap

    # Anchor colors at specific degree positions. Saturated enough to pop
    # on the map while still transitioning smoothly.
    anchors = [
        (0 / 20,    (0.15, 0.75, 0.15)),  # vivid green
        (2 / 20,    (0.15, 0.75, 0.15)),  # still green
        (4.5 / 20,  (1.0,  0.92, 0.0)),   # pure yellow
        (6 / 20,    (1.0,  0.88, 0.0)),   # 6deg: still yellow
        (8 / 20,    (1.0,  0.45, 0.0)),   # 8deg: orange
        (11 / 20,   (0.9,  0.10, 0.0)),   # 11deg:  vivid red
        (13 / 20,   (0.5,  0.04, 0.0)),   # 13deg:  dark red (still clearly red)
        (15 / 20,   (0.1,  0.0,  0.0)),   # 15deg:  near-black but reddish
        (17 / 20,   (0.05, 0.0,  0.0)),   # 17deg:  very dark red
        (1.0,       (0.0,  0.0,  0.0)),   # 20deg:  black
    ]

    positions, colors = zip(*anchors)
    cmap = LinearSegmentedColormap.from_list('track', list(zip(positions, colors)))
    norm = mcolors.Normalize(vmin=0, vmax=20)
    return cmap, norm


def _make_contour_levels(elev_ft_min: float, elev_ft_max: float) -> tuple[list[int], list[int]]:
    """Generate contour levels: every 40ft, with 200ft as major."""
    # Round to nearest 40ft below/above
    start = int(np.floor(elev_ft_min / CONTOUR_MINOR) * CONTOUR_MINOR)
    end = int(np.ceil(elev_ft_max / CONTOUR_MINOR) * CONTOUR_MINOR)

    minor = list(range(start, end + 1, CONTOUR_MINOR))
    major = [lvl for lvl in minor if lvl % CONTOUR_MAJOR == 0]
    return minor, major


def plot_slopes(points: list[TrackPoint], output: str | Path) -> None:
    """Plot track and ground slopes vs distance."""
    distances = [p.distance / 1000 * KM_TO_MI for p in points]  # miles
    track_slopes = [p.track_slope for p in points]
    ground_slopes = [p.ground_slope for p in points]

    fig, ax = plt.subplots(figsize=(10, 5))

    track_abs = [abs(s) if s is not None else None for s in track_slopes]
    # Ground first (behind), then track on top so it's always visible
    ax.plot(distances, ground_slopes, 'r-', linewidth=1, label='Ground', alpha=0.7)
    ax.plot(distances, track_abs, 'b-', linewidth=1, label='Track')

    ax.set_xlabel('Distance (mi)')
    ax.set_ylabel('Slope (deg)')
    ax.set_title('Slope Profile')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)

    fig.tight_layout()
    fig.savefig(output, dpi=300)
    plt.close(fig)


def plot_elevation_profile(points: list[TrackPoint], output: str | Path) -> None:
    """Plot elevation profile, colored by track slope."""
    distances = np.array([p.distance / 1000 * KM_TO_MI for p in points])
    elevations = np.array([p.elevation * M_TO_FT if p.elevation is not None else 0 for p in points])
    slopes = np.array([abs(p.track_slope) if p.track_slope is not None else 0 for p in points])

    fig, ax = plt.subplots(figsize=(10, 4))

    cmap, norm = _make_track_cmap()

    _draw_gradient_line(ax, distances, elevations, slopes, cmap, norm,
                        linewidth=3, border_width=5, smooth_path=True)
    ax.autoscale_view()

    valid_elevs = elevations[elevations > 0]
    min_elev = float(valid_elevs.min())
    max_elev = float(valid_elevs.max())
    padding = (max_elev - min_elev) * 0.05

    ax.set_xlabel('Distance (mi)')
    ax.set_ylabel('Elevation (ft)')
    ax.set_title('Elevation Profile')
    ax.set_xlim(0, float(distances.max()))
    ax.set_ylim(min_elev, max_elev + padding)
    ax.grid(True, alpha=0.3)

    # Colorbar for track slope
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    _add_track_slope_colorbar(fig, ax, sm, shrink=0.8, pad=0.02)

    fig.tight_layout()
    fig.savefig(output, dpi=300)
    plt.close(fig)


def _sample_elevation_grid(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, resolution: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample elevation data for a region. Returns meshes and elevation grid in feet."""
    load_dem_for_bounds(lat_min, lat_max, lon_min, lon_max)
    lon_mesh, lat_mesh, elev_grid = get_elevation_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )
    # Convert to feet, preserving NaN
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


def _find_round_ticks(vmin: float, vmax: float, min_ticks: int = 2,
                      max_ticks: int = 5) -> list[float]:
    """Find round tick values in range, between min_ticks and max_ticks.

    Prefers the coarsest interval that gives at least min_ticks.
    """
    intervals = [0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001, 0.0005, 0.0001]
    best = None

    for interval in intervals:
        first = np.ceil(vmin / interval) * interval
        ticks = []
        tick = first
        while tick <= vmax + interval * 1e-9:
            ticks.append(round(tick, 6))
            tick += interval
        if len(ticks) >= min_ticks:
            if len(ticks) <= max_ticks:
                return ticks
            # Too many ticks at this interval -- remember it as fallback
            if best is None:
                best = ticks
    if best is not None:
        # Thin the best candidate to max_ticks by taking evenly-spaced subset
        indices = np.round(np.linspace(0, len(best) - 1, max_ticks)).astype(int)
        return [best[i] for i in indices]

    return [vmin, (vmin + vmax) / 2, vmax]


def _format_coord(v: float) -> str:
    """Format coordinate with exactly 5 decimal places, always showing trailing zeros."""
    # Format with 5 decimals, which always shows trailing zeros
    return f'{v:.5f}'


def _set_map_ticks(ax: plt.Axes, lat_min: float, lat_max: float,
                   lon_min: float, lon_max: float) -> None:
    """Set lat/lon ticks with 5 decimal places, using round numbers."""
    lon_ticks = _find_round_ticks(lon_min, lon_max, max_ticks=3)
    lat_ticks = _find_round_ticks(lat_min, lat_max, max_ticks=4)

    ax.set_xticks(lon_ticks)
    ax.set_xticklabels([_format_coord(v) for v in lon_ticks])
    ax.set_yticks(lat_ticks)
    ax.set_yticklabels([_format_coord(v) for v in lat_ticks])


def _set_metric_aspect(ax: plt.Axes, center_lat: float) -> None:
    """Set aspect ratio so map is undistorted (equal in meters, not degrees).

    At higher latitudes, 1 degree of longitude is shorter than 1 degree of latitude.
    This corrects for that so the map looks correct.
    """
    import math
    # 1 deg lat is always ~111km, 1 deg lon is ~111*cos(lat) km
    # To make them visually equal in real distance, set aspect = 1/cos(lat)
    aspect = 1.0 / math.cos(math.radians(center_lat))
    ax.set_aspect(aspect)


def _get_track_slopes(points: list[TrackPoint]) -> list[float]:
    """Extract absolute track slopes, skipping None values."""
    return [abs(p.track_slope) for p in points if p.track_slope is not None]


def _interpolate_track(x, y, values, n_points=2000):
    """Interpolate a track to n_points for smooth gradient rendering.

    Returns (x_interp, y_interp, values_interp) arrays with n_points entries.
    """
    # Cumulative arc length as the interpolation parameter
    dx = np.diff(x)
    dy = np.diff(y)
    ds = np.sqrt(dx**2 + dy**2)
    s = np.concatenate([[0], np.cumsum(ds)])

    s_interp = np.linspace(0, s[-1], n_points)
    x_interp = np.interp(s_interp, s, x)
    y_interp = np.interp(s_interp, s, y)
    v_interp = np.interp(s_interp, s, values)
    return x_interp, y_interp, v_interp


def _smooth(values, window=51):
    """Smooth values with a rolling average, preserving array length."""
    if len(values) < window:
        return values
    kernel = np.ones(window) / window
    # Pad edges to avoid shrinkage
    pad = window // 2
    padded = np.concatenate([
        np.full(pad, values[0]),
        values,
        np.full(pad, values[-1]),
    ])
    smoothed = np.convolve(padded, kernel, mode='valid')
    return smoothed[:len(values)]


def _draw_gradient_line(ax, x, y, values, cmap, norm, linewidth=3,
                        border_width=5, n_points=2000, zorder_base=4,
                        smooth_path=False):
    """Draw a smooth gradient-colored line with a solid border underneath.

    LineCollection colors each segment independently, which looks like a
    caterpillar when segments are short and colors change rapidly. Two fixes:
      1. Smooth the color values so adjacent segments don't flicker.
      2. Interpolate to many evenly-spaced points so each color step is sub-pixel.

    The border is drawn as a single ax.plot() line (proper joins, no artifacts).
    Round caps ensure no gaps. Smoothing + interpolation means adjacent segments
    have nearly identical colors, so overlapping caps blend invisibly.

    smooth_path: also smooth the y coordinates of the rendered path. Useful for
    elevation profiles where tiny DEM bumps create direction changes that make
    LineCollection edges look scalloped. Doesn't affect the data, just rendering.
    """
    from matplotlib.collections import LineCollection

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    values = np.asarray(values, dtype=float)

    # Smooth values so color transitions are gradual, not noisy
    smooth_window = max(3, len(values) // 40)
    if smooth_window % 2 == 0:
        smooth_window += 1  # keep odd for symmetric kernel
    values = _smooth(values, smooth_window)

    # Ensure enough points for smooth rendering
    n_points = max(n_points, len(x))

    # Interpolate to many points for smooth gradient
    xi, yi, vi = _interpolate_track(x, y, values, n_points)

    if smooth_path:
        # Smooth the rendered path shape to eliminate tiny direction changes
        # from DEM noise that cause scalloped edges on the LineCollection.
        # Aggressive window (~1% of points) removes sub-pixel bumps.
        path_window = max(3, n_points // 50)
        if path_window % 2 == 0:
            path_window += 1
        yi = _smooth(yi, path_window)

    # Build segments
    points_arr = np.column_stack([xi, yi]).reshape(-1, 1, 2)
    segments = np.concatenate([points_arr[:-1], points_arr[1:]], axis=1)

    # Border: single continuous line -- ax.plot() handles joins properly,
    # unlike LineCollection which draws each segment independently
    ax.plot(xi, yi, color='black', linewidth=border_width,
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.7, zorder=zorder_base)

    # Thin lines (<=2pt): butt caps -- no blob artifacts, border covers gaps.
    # Wider lines: round caps -- need overlap to avoid gaps showing through.
    cap = 'butt' if linewidth <= 2 else 'round'
    lc = LineCollection(segments, cmap=cmap, norm=norm,
                        linewidths=linewidth,
                        capstyle=cap, joinstyle='round',
                        zorder=zorder_base + 1)
    lc.set_array(vi[1:])
    ax.add_collection(lc)

    return vi


def _draw_track_colored_by_slope(
    ax: plt.Axes, points: list[TrackPoint]
) -> tuple[plt.cm.ScalarMappable, float]:
    """Draw track colored by track slope. Returns (ScalarMappable, max_slope)."""
    lats = np.array([p.lat for p in points])
    lons = np.array([p.lon for p in points])
    slopes = np.array([abs(p.track_slope) if p.track_slope is not None else 0 for p in points])

    cmap, norm = _make_track_cmap()
    max_slope = float(slopes.max())

    _draw_gradient_line(ax, lons, lats, slopes, cmap, norm,
                        linewidth=3, border_width=5)
    ax.autoscale_view()

    # Check if it's a loop (start and end within 200m)
    is_loop = haversine_distance(lats[0], lons[0], lats[-1], lons[-1]) < 200

    if is_loop:
        # Single marker for loop: white square with black outline
        ax.plot(lons[0], lats[0], 'ws', markersize=12, markeredgecolor='black',
                markeredgewidth=2, label='Start/End', zorder=10)
    else:
        # Separate markers for point-to-point
        ax.plot(lons[0], lats[0], 'go', markersize=10, label='Start', zorder=10)
        ax.plot(lons[-1], lats[-1], 'ws', markersize=10, markeredgecolor='black',
                label='End', zorder=10)

    return plt.cm.ScalarMappable(norm=norm, cmap=cmap), max_slope


def _draw_topo_contours(
    ax: plt.Axes, lon_mesh: np.ndarray, lat_mesh: np.ndarray, elev_grid_ft: np.ndarray
) -> list[int] | None:
    """Draw topo contours: 40ft minor, 200ft major with labels."""
    valid = elev_grid_ft[~np.isnan(elev_grid_ft)]
    if len(valid) == 0:
        return None

    minor_levels, major_levels = _make_contour_levels(valid.min(), valid.max())

    # Minor contours (thin)
    ax.contour(lon_mesh, lat_mesh, elev_grid_ft, levels=minor_levels,
               colors='#333333', linewidths=0.4, alpha=0.6)

    # Major contours (bold + labeled)
    cs = ax.contour(lon_mesh, lat_mesh, elev_grid_ft, levels=major_levels,
                    colors='black', linewidths=1.2)
    ax.clabel(cs, inline=True, fontsize=8, fmt='%d')

    return minor_levels


def compute_map_grids(
    points: list[TrackPoint],
    padding: float = 0.003,
    resolution: int = 400,
    bounds: tuple[float, float, float, float] | None = None,
) -> dict:
    """Precompute elevation and slope grids shared by topo and slope maps.

    Call once and pass to both plot_topo_map and plot_slope_map to avoid
    redundant DEM downloads and grid interpolation.

    If bounds is given as (lat_min, lat_max, lon_min, lon_max), use those
    instead of computing from points + padding.
    """
    if bounds:
        lat_min, lat_max, lon_min, lon_max = bounds
    else:
        lats = [p.lat for p in points]
        lons = [p.lon for p in points]
        lat_min, lat_max = min(lats) - padding, max(lats) + padding
        lon_min, lon_max = min(lons) - padding, max(lons) + padding

    lon_mesh, lat_mesh, elev_grid_ft = _sample_elevation_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )
    _, _, slope_grid = _sample_slope_grid(
        lat_min, lat_max, lon_min, lon_max, resolution
    )

    return {
        'lon_mesh': lon_mesh, 'lat_mesh': lat_mesh,
        'elev_grid_ft': elev_grid_ft, 'slope_grid': slope_grid,
        'lat_min': lat_min, 'lat_max': lat_max,
        'lon_min': lon_min, 'lon_max': lon_max,
    }


def plot_topo_map(
    points: list[TrackPoint],
    output: str | Path,
    padding: float = 0.003,
    resolution: int = 400,
    grids: dict | None = None,
) -> None:
    """Plot topo map with track."""
    if grids is None:
        grids = compute_map_grids(points, padding, resolution)

    lat_min, lat_max = grids['lat_min'], grids['lat_max']
    lon_min, lon_max = grids['lon_min'], grids['lon_max']
    lon_mesh = grids['lon_mesh']
    lat_mesh = grids['lat_mesh']
    elev_grid_ft = grids['elev_grid_ft']

    fig, ax = plt.subplots(figsize=(12, 9))

    # Continuous elevation fill (custom colormap, no blue)
    valid = elev_grid_ft[~np.isnan(elev_grid_ft)]
    elev_norm = mcolors.Normalize(vmin=valid.min(), vmax=valid.max())
    elev_cmap = _make_elevation_cmap()
    cf = ax.pcolormesh(lon_mesh, lat_mesh, elev_grid_ft, cmap=elev_cmap, norm=elev_norm)

    # Topo contours
    _draw_topo_contours(ax, lon_mesh, lat_mesh, elev_grid_ft)

    # Track
    sm, max_slope = _draw_track_colored_by_slope(ax, points)

    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')

    ax.legend(loc='upper right')

    _set_map_ticks(ax, lat_min, lat_max, lon_min, lon_max)

    _add_elevation_colorbar(fig, ax, cf, valid.min(), valid.max(), shrink=0.5, pad=0.01)

    _add_track_slope_colorbar(fig, ax, sm, shrink=0.5, pad=0.04)

    # Set proper aspect ratio (correct in meters, not just degrees)
    center_lat = (lat_min + lat_max) / 2
    _set_metric_aspect(ax, center_lat)

    fig.savefig(output, dpi=300, bbox_inches='tight', pad_inches=0.1)
    plt.close(fig)


def plot_slope_map(
    points: list[TrackPoint],
    output: str | Path,
    padding: float = 0.003,
    resolution: int = 400,
    grids: dict | None = None,
) -> None:
    """Plot avalanche terrain map."""
    if grids is None:
        grids = compute_map_grids(points, padding, resolution)

    lat_min, lat_max = grids['lat_min'], grids['lat_max']
    lon_min, lon_max = grids['lon_min'], grids['lon_max']
    lon_mesh = grids['lon_mesh']
    lat_mesh = grids['lat_mesh']
    elev_grid_ft = grids['elev_grid_ft']
    slope_grid = grids['slope_grid']

    fig, ax = plt.subplots(figsize=(12, 9))

    # Slope coloring
    ground_cmap, ground_norm = _make_ground_cmap()
    cf = ax.pcolormesh(lon_mesh, lat_mesh, slope_grid, cmap=ground_cmap, norm=ground_norm)

    # Topo contours
    _draw_topo_contours(ax, lon_mesh, lat_mesh, elev_grid_ft)

    # Track
    sm, max_slope = _draw_track_colored_by_slope(ax, points)

    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')

    ax.legend(loc='upper right')

    _set_map_ticks(ax, lat_min, lat_max, lon_min, lon_max)

    _add_ground_slope_colorbar(fig, ax, cf, shrink=0.5, pad=0.01)

    _add_track_slope_colorbar(fig, ax, sm, shrink=0.5, pad=0.04)

    # Set proper aspect ratio (correct in meters, not just degrees)
    center_lat = (lat_min + lat_max) / 2
    _set_metric_aspect(ax, center_lat)

    fig.savefig(output, dpi=300, bbox_inches='tight', pad_inches=0.1)
    plt.close(fig)


def plot_slope_histogram(points: list[TrackPoint], output: str | Path) -> None:
    """Plot histogram of track slopes (absolute value)."""
    slopes = _get_track_slopes(points)
    if not slopes:
        return

    max_slope = max(slopes)
    # Fit x-axis to data with some padding
    x_max = min(50, int(max_slope / 5 + 1) * 5 + 5)

    fig, ax = plt.subplots(figsize=(6, 4))

    bins = np.arange(0, x_max + 2, 2)
    cmap, norm = _make_track_cmap()

    counts, _, patches = ax.hist(slopes, bins=bins, edgecolor='#333333', linewidth=0.8)
    for patch, left_edge in zip(patches, bins[:-1]):
        # For >= 20 degrees, use lighter gray for readability against dark border
        if left_edge >= 20:
            patch.set_facecolor('#666666')
        else:
            color = cmap(norm(left_edge + 1))
            patch.set_facecolor(color)

    ax.set_xlabel('Track slope (degrees)')
    ax.set_ylabel('Count')
    ax.set_title('Track Slope Distribution')
    ax.set_xlim(0, x_max)

    fig.tight_layout()
    fig.savefig(output, dpi=300)
    plt.close(fig)
