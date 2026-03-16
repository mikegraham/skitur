#!/usr/bin/env bash
# Set up a Cloudflare Cache Rule to cache the landing page HTML.
#
# Cloudflare does NOT cache HTML by default. This rule makes / eligible,
# then the CDN-Cache-Control header from the app controls the edge TTL.
#
# Required env vars:
#   CLOUDFLARE_ZONE_ID    - Zone ID for fjell.ski
#   CLOUDFLARE_API_TOKEN  - API token with Zone > Cache Rules > Edit
#
# Usage:
#   CLOUDFLARE_ZONE_ID=... CLOUDFLARE_API_TOKEN=... bash scripts/setup_cloudflare_cache.sh
set -euo pipefail

: "${CLOUDFLARE_ZONE_ID:?Set CLOUDFLARE_ZONE_ID}"
: "${CLOUDFLARE_API_TOKEN:?Set CLOUDFLARE_API_TOKEN}"

API="https://api.cloudflare.com/client/v4/zones/$CLOUDFLARE_ZONE_ID/rulesets/phases/http_request_cache_settings/entrypoint"
AUTH="Authorization: Bearer $CLOUDFLARE_API_TOKEN"

echo "Checking existing cache rules..."
EXISTING=$(curl -s "$API" -H "$AUTH")
echo "$EXISTING" | python3 -m json.tool 2>/dev/null || echo "$EXISTING"

echo ""
echo "Creating cache rule for landing page..."
RESULT=$(curl -s -X PUT "$API" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {
        "expression": "(http.request.uri.path eq \"/\")",
        "description": "Cache HTML landing page",
        "action": "set_cache_settings",
        "action_parameters": {
          "cache": true
        }
      }
    ]
  }')

SUCCESS=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('success', False))" 2>/dev/null || echo "unknown")

if [ "$SUCCESS" = "True" ]; then
    echo "Cache rule created successfully."
    echo ""
    echo "Verify with: curl -sI https://fjell.ski/ | grep cf-cache-status"
    echo "  DYNAMIC = not cached (something went wrong)"
    echo "  MISS    = eligible, first request"
    echo "  HIT     = served from edge cache"
else
    echo "Failed to create cache rule:"
    echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
    exit 1
fi
