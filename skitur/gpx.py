from pathlib import Path

from lxml import etree


def load_track(path: str | Path) -> list[tuple[float, float]]:
    """Load a GPX file and return track points as (lat, lon) tuples.

    This parser intentionally extracts only trackpoint coordinates for speed.
    """
    points: list[tuple[float, float]] = []

    context = etree.iterparse(
        str(path),
        events=("end",),
        no_network=True,
        resolve_entities=False,
        huge_tree=False,
        recover=True,  # Best-effort parse for slightly malformed GPX uploads.
        tag="{*}trkpt",
    )

    for _event, elem in context:
        lat_s = elem.get("lat")
        lon_s = elem.get("lon")
        if lat_s is None or lon_s is None:
            elem.clear()
            continue

        try:
            lat = float(lat_s)
            lon = float(lon_s)
        except ValueError:
            elem.clear()
            continue

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            elem.clear()
            continue

        points.append((lat, lon))

        # Free parsed nodes to keep memory bounded for large files.
        elem.clear()
        parent = elem.getparent()
        if parent is not None:
            while parent.getprevious() is not None:
                del parent.getparent()[0]

    return points
