from pathlib import Path

import gpxpy


def load_track(path: str | Path) -> list[tuple[float, float, float | None]]:
    """Load a GPX file and return list of (lat, lon, elevation) tuples.

    Elevation comes from the GPX file (GPS/barometric altimeter).
    Returns None for elevation if the GPX point has no elevation data.
    """
    with open(path) as f:
        gpx = gpxpy.parse(f)

    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((point.latitude, point.longitude, point.elevation))
    return points
