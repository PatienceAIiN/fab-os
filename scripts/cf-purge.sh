#!/usr/bin/env bash
# Purge the Cloudflare cache for fabos.patienceai.in after a deploy (the zone caches HTML, so stale pages/ISO responses
# otherwise persist for hours). Reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ZONE_ID from the environment or build/secrets/cloudflare.env.
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
[ -f build/secrets/cloudflare.env ] && . build/secrets/cloudflare.env
: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ZONE_ID:?}"
curl -s -m 30 -X POST "https://api.cloudflare.com/client/v4/zones/$CLOUDFLARE_ZONE_ID/purge_cache" -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" --data '{"purge_everything":true}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print("cloudflare cache purged" if d.get("success") else "purge FAILED: "+json.dumps(d.get("errors"))[:300]); sys.exit(0 if d.get("success") else 1)'
