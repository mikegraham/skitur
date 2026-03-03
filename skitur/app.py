"""Flask application for interactive GPX analysis."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import orjson
from flask import Flask, Response, render_template, request

from skitur.report import build_analysis_payload

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder=Path(__file__).parent / "templates")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit


@app.route("/")
def index():
    return render_template("index.html")


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

    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        gpx_file.save(tmp)
        tmp_path = Path(tmp.name)

    # Reject XML bombs: valid GPX files never contain DOCTYPE or ENTITY declarations.
    raw = tmp_path.read_text(errors="replace")
    if "<!DOCTYPE" in raw or "<!ENTITY" in raw:
        tmp_path.unlink(missing_ok=True)
        return _json_error("Invalid GPX file")

    try:
        data = build_analysis_payload(tmp_path)
        body = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
        return Response(body, content_type="application/json")
    except Exception:
        logger.exception("Analysis failed")
        return _json_error("Analysis failed. Please check your GPX file.", 500)
    finally:
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Run the skitur Flask app")
    parser.add_argument("--port", type=int, default=5000, help="Port for web server")
    args = parser.parse_args()

    app.run(
        debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"),
        port=args.port,
    )
