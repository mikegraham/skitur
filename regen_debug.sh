#!/usr/bin/env bash
# Regenerate debug.html by starting the Flask server, uploading Twin_Lakes.gpx,
# and saving the rendered result.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GPX_FILE="${1:-$SCRIPT_DIR/Twin_Lakes.gpx}"
OUT_FILE="${2:-$SCRIPT_DIR/debug.html}"
PORT=5199

cd "$SCRIPT_DIR"

# Start Flask server in background
.venv/bin/python3 -m skitur.web --port "$PORT" &
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

template = sys.stdin.read()
with open('$TMPJSON') as f:
    data = json.load(f)

data_json = json.dumps(data, separators=(',', ':'))

inject = '<script>\n'
inject += 'document.addEventListener(\"DOMContentLoaded\", function() {\n'
inject += '    const data = ' + data_json + ';\n'
inject += '    trackData = data;\n'
inject += '    document.getElementById(\"upload-section\").style.display = \"none\";\n'
inject += '    renderResults(data, \"' + '$(basename "$GPX_FILE")' + '\");\n'
inject += '});\n'
inject += '</script>'

html = template.replace('</body>', inject + '</body>')
print(html)
" <<< "$TEMPLATE" > "$OUT_FILE"

echo "Generated $OUT_FILE ($(wc -c < "$OUT_FILE") bytes)"
