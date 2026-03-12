#!/usr/bin/env bash
# Regenerate debug.html by starting the Flask server, uploading Twin_Lakes.gpx,
# and saving the rendered result.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GPX_FILE="${1:-$REPO_DIR/tests/data/Twin_Lakes.gpx}"
OUT_FILE="${2:-$REPO_DIR/debug.html}"
PORT=5199

cd "$REPO_DIR"

# Start Flask server in background
.venv/bin/python3 -m skitur.app --port "$PORT" &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for server to be ready
for i in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$PORT/" > /dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Upload GPX and save the JSON response to a temp file
TMPJSON=$(mktemp)
trap "kill $SERVER_PID 2>/dev/null; rm -f $TMPJSON" EXIT
curl -s -X POST "http://127.0.0.1:$PORT/api/analyze" \
    -F "gpx_file=@$GPX_FILE" > "$TMPJSON"

# Get the template HTML
TEMPLATE=$(curl -s "http://127.0.0.1:$PORT/")

# Inject data into the template to create a self-contained debug page
.venv/bin/python3 -c "
import json, sys
from skitur.report import build_embedded_report_html

template = sys.stdin.read()
with open(sys.argv[1]) as f:
    data = json.load(f)

html = build_embedded_report_html(
    template_html=template,
    data=data,
    filename=sys.argv[2],
    hide_upload_section=True,
    hide_new_upload_button=False,
)
print(html)
" "$TMPJSON" "$(basename "$GPX_FILE")" <<< "$TEMPLATE" > "$OUT_FILE"

echo "Generated $OUT_FILE ($(wc -c < "$OUT_FILE") bytes)"
