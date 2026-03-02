"""Geographic utilities and constants."""

import math

# Approximate meters per degree of latitude (constant everywhere)
METERS_PER_DEG_LAT = 111_320

# Maximum distance between track points before resampling (meters)
RESAMPLE_THRESHOLD_M = 100


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in meters.

    Uses simple equirectangular approximation (accurate for short distances).
    """
    dlat_m = (lat2 - lat1) * METERS_PER_DEG_LAT
    avg_lat = (lat1 + lat2) / 2
    dlon_m = (lon2 - lon1) * METERS_PER_DEG_LAT * math.cos(math.radians(avg_lat))
    return math.sqrt(dlat_m**2 + dlon_m**2)


def resample_track(
    points: list[tuple],
    max_spacing_m: float = RESAMPLE_THRESHOLD_M,
) -> list[tuple]:
    """Resample track so no two consecutive points are more than max_spacing_m apart.

    Accepts (lat, lon) or (lat, lon, elevation) tuples.
    If a segment is longer than max_spacing_m, it is subdivided into segments
    that are just under max_spacing_m (as few subdivisions as possible).
    Elevation is linearly interpolated for subdivided points.
    """
    if len(points) < 2:
        return list(points)

    has_elev = len(points[0]) >= 3

    result = [points[0]]

    for i in range(1, len(points)):
        prev = result[-1]
        cur = points[i]
        lat1, lon1 = prev[0], prev[1]
        lat2, lon2 = cur[0], cur[1]

        dist = haversine_distance(lat1, lon1, lat2, lon2)

        if dist > max_spacing_m:
            n_segments = math.ceil(dist / max_spacing_m)

            for j in range(1, n_segments):
                t = j / n_segments
                interp_lat = lat1 + t * (lat2 - lat1)
                interp_lon = lon1 + t * (lon2 - lon1)
                if has_elev:
                    e1 = prev[2]
                    e2 = cur[2]
                    interp_elev = None
                    if e1 is not None and e2 is not None:
                        interp_elev = e1 + t * (e2 - e1)
                    result.append((interp_lat, interp_lon, interp_elev))
                else:
                    result.append((interp_lat, interp_lon))

        result.append(cur)

    return result
