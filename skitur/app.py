"""Flask application for interactive GPX analysis."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import orjson
from cachetools import LRUCache
from dem_stitcher.datasets import get_global_dem_tile_extents
from flask import Flask, Response, render_template, request

from skitur.report import EmptyTrackError, build_analysis_payload
from dem_stitcher.exceptions import NoDEMCoverage
from skitur.terrain import ExtentTooLargeError, TerrainLoader

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder=Path(__file__).parent / "templates")

# Defaults — override via SKITUR_CONFIG env var pointing to a Python config file.
app.config["DEM_CACHE_DIR"] = Path.home() / ".cache" / "skitur" / "dem"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024     # 10 MB
app.config["RESPONSE_CACHE_BYTES"] = 50 * 1024 * 1024   # 50 MB per process

if os.environ.get("SKITUR_CONFIG"):
    app.config.from_envvar("SKITUR_CONFIG")

# In-memory LRU response cache keyed on raw GPX bytes, sized by response bytes.
_cache: LRUCache[bytes, bytes] = LRUCache(
    maxsize=app.config["RESPONSE_CACHE_BYTES"],
    getsizeof=len,
)
_cache_lock = threading.Lock()


def _warmup_dem_indexes() -> None:
    """Preload dem-stitcher tile catalogs to avoid first-request parse cost."""
    datasets = ("3dep", "glo_30", "glo_90_missing")
    t0 = time.perf_counter()
    for dem_name in datasets:
        get_global_dem_tile_extents(dem_name)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    logger.info("Preloaded DEM tile catalogs (%s) in %.1f ms", ", ".join(datasets), dt_ms)


_warmup_dem_indexes()

_terrain_loader = TerrainLoader(cache_dir=app.config["DEM_CACHE_DIR"])


@app.route("/")
def index():
    resp = Response(render_template("index.html"), content_type="text/html")
    resp.cache_control.public = True
    resp.cache_control.max_age = 300  # 5 min browser cache
    return resp


@app.route("/healthz")
def healthz():
    return Response(
        orjson.dumps({"status": "ok"}),
        content_type="application/json",
    )


def _json_error(msg: str, status: int = 400) -> tuple[Response, int]:
    body = orjson.dumps({"error": msg})
    return Response(body, status=status, content_type="application/json"), status


@app.route("/api/analyze", methods=["POST"])
def analyze():
    if "gpx_file" not in request.files:
        return _json_error("No GPX file uploaded")

    gpx_file = request.files["gpx_file"]
    if gpx_file.filename == "":
        return _json_error("No file selected")

    gpx_bytes = gpx_file.read()

    with _cache_lock:
        cached = _cache.get(gpx_bytes)
    if cached is not None:
        logger.info("Cache hit (%d bytes)", len(cached))
        return Response(cached, content_type="application/json")

    # Reject XML bombs: valid GPX files never contain DOCTYPE or ENTITY declarations.
    raw_text = gpx_bytes.decode("utf-8", errors="replace")
    if "<!DOCTYPE" in raw_text or "<!ENTITY" in raw_text:
        return _json_error("Invalid GPX file")

    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        tmp.write(gpx_bytes)
        tmp_path = Path(tmp.name)

    try:
        data = build_analysis_payload(tmp_path, terrain_loader=_terrain_loader)
        body = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
        with _cache_lock:
            _cache[gpx_bytes] = body
        return Response(body, content_type="application/json")
    except EmptyTrackError:
        return _json_error("GPX file contains no usable track points")
    except ExtentTooLargeError as exc:
        return _json_error(str(exc), 422)
    except NoDEMCoverage:
        return _json_error(
            "No elevation data available for this area. Try a different location.", 422
        )
    except Exception:
        logger.exception("Analysis failed")
        return _json_error("Analysis failed. Please try again.", 500)
    finally:
        tmp_path.unlink(missing_ok=True)
