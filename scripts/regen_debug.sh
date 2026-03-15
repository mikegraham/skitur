#!/usr/bin/env bash
# Regenerate debug.html by starting the Flask server, uploading a GPX file,
# and saving the server-rendered report page.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GPX_FILE="${1:-$REPO_DIR/tests/data/Twin_Lakes.gpx}"
OUT_FILE="${2:-$REPO_DIR/debug.html}"
PORT=5199

cd "$REPO_DIR"

# Start Flask server in background
.venv/bin/flask --app skitur.app run --port "$PORT" 2>/dev/null &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for server to be ready
for i in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$PORT/" > /dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Upload GPX and save the full HTML report directly
curl -s --max-time 120 -X POST "http://127.0.0.1:$PORT/analyze" \
    -F "gpx_file=@$GPX_FILE" > "$OUT_FILE"

echo "Generated $OUT_FILE ($(wc -c < "$OUT_FILE") bytes)"
