#!/usr/bin/env bash
# Record the GPL source offer for an image: package manifest, source-package URIs, copyright inventory.
# Usage: scripts/source-offer.sh [vm|iso] [--download]     -> legal/source-offer/<BUILD_ID>/
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; PROFILE=${1:-vm}; DL=${2:-}
TAG=fabos:$PROFILE; ID=$(cat build/BUILD_ID 2>/dev/null || echo "unreleased-$PROFILE"); OUT=legal/source-offer/$ID; mkdir -p "$OUT"
podman run --rm "$TAG" bash -c 'dpkg-query -W -f="${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"' | sort > "$OUT/manifest.txt"
podman run --rm "$TAG" bash -c 'find /usr/share/doc -name copyright | sort' > "$OUT/copyrights.txt"
echo "== resolving source URIs for $(wc -l < "$OUT/manifest.txt") binary packages (apt-get source --print-uris)"
cut -f3,4 "$OUT/manifest.txt" | sort -u | awk -F'\t' '$1!=""{print $1"="$2}' > build/srcpkgs.txt
podman run --rm -v "$HERE/build/srcpkgs.txt:/srcpkgs.txt:ro" "$TAG" bash -c '
  apt-get update -qq >/dev/null 2>&1; cd /tmp
  xargs -a /srcpkgs.txt -n 40 apt-get source --print-uris -qq 2>/dev/null | grep -oE "https?://[^ ]+ [^ ]+ [0-9]+ [A-Za-z0-9]+:[0-9a-f]+" ' | sort -u > "$OUT/source-uris.txt" || true
printf 'Image: %s\nProfile: %s\nGenerated: %s\nBinary packages: %s\nSource files: %s\nOffer: see legal/SOURCE-OFFER.md\n' "$ID" "$PROFILE" "$(date -u +%FT%TZ)" "$(wc -l < "$OUT/manifest.txt")" "$(wc -l < "$OUT/source-uris.txt")" > "$OUT/README.txt"
cat "$OUT/README.txt"
if [ "$DL" = --download ]; then mkdir -p "build/sources-$ID"; awk '{print $1}' "$OUT/source-uris.txt" | (cd "build/sources-$ID" && xargs -n1 -P4 curl -sSfLO); echo "sources mirrored to build/sources-$ID"; fi
