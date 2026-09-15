#!/usr/bin/env bash
# Record the GPL source offer for an image: package manifest, source-package URIs, copyright inventory.
# Usage: [BUILD_ID=<id>] scripts/source-offer.sh [vm|iso] [--download]     -> legal/source-offer/<BUILD_ID>/
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; PROFILE=${1:-vm}; DL=${2:-}
TAG=fabos:$PROFILE; ID=${BUILD_ID:-$(cat build/BUILD_ID 2>/dev/null || echo "unreleased-$PROFILE")}; OUT=legal/source-offer/$ID; mkdir -p "$OUT"   # BUILD_ID=<id> overrides build/BUILD_ID
# NOTE: the format string must reach dpkg-query literally (no shell expansion of ${binary:Package} on the way in).
podman run --rm "$TAG" dpkg-query -W -f '${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n' | sort > "$OUT/manifest.txt"
awk -F'\t' '$1==""{bad++} END{if(bad>0){print "ERROR: manifest has " bad " empty package names"; exit 1}}' "$OUT/manifest.txt"
podman run --rm "$TAG" bash -c 'find /usr/share/doc -name copyright | sort' > "$OUT/copyrights.txt"
echo "== resolving source URIs for $(wc -l < "$OUT/manifest.txt") binary packages (apt-get source --print-uris)"
cut -f3,4 "$OUT/manifest.txt" | sort -u | awk -F'\t' '$1!=""{print $1"="$2}' > build/srcpkgs.txt
# The shipped image has no deb-src entries (and its apt lists are dropped), so enable deb-src on the official archive
# inside the throw-away container, refresh, then resolve every source package's .dsc/.orig/.debian URIs.
# The package list goes in on STDIN (a bind mount is unreadable under rootless podman + SELinux without relabelling).
podman run --rm -i "$TAG" bash -c '
  sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/ubuntu.sources
  apt-get update -qq >/dev/null 2>&1 || apt-get update 2>&1 | grep -E "^(E:|Err)" | head -3 >&2; cd /tmp
  xargs -n 1 apt-get source --print-uris -qq 2>/dev/null | grep -oE "^.?https?://[^ ]+ [^ ]+ [0-9]+ [A-Za-z0-9]+:[0-9a-f]+" | sed -E "s/^.?(https?:)/\\1/; s/'"'"' / /" ' < build/srcpkgs.txt | sort -u > "$OUT/source-uris.txt" || true
# coverage: requested source packages that got no URI (own fabos-* packages = this repo; Brave-repo brave-browser/brave-keyring; versions superseded in the archive)
awk '{print $2}' "$OUT/source-uris.txt" | sed -E 's/_[^_]+$//' | sort -u > build/resolved-src.txt
awk -F= 'NR==FNR{r[$1]=1; next} !($1 in r)' build/resolved-src.txt build/srcpkgs.txt > "$OUT/unresolved-sources.txt"
[ -s "$OUT/source-uris.txt" ] || echo "WARNING: no source URIs resolved — check deb-src availability / network" >&2
printf 'Image: %s\nProfile: %s\nGenerated: %s\nBinary packages: %s\nSource files: %s\nUnresolved source packages: %s (see unresolved-sources.txt)\nOffer: see legal/SOURCE-OFFER.md\n' "$ID" "$PROFILE" "$(date -u +%FT%TZ)" "$(wc -l < "$OUT/manifest.txt")" "$(wc -l < "$OUT/source-uris.txt")" "$(wc -l < "$OUT/unresolved-sources.txt")" > "$OUT/README.txt"
# Components that are not Ubuntu archive packages (SOURCE-OFFER.md item 4): the image build compiles fabos-rounded-corners from a
# sha256-pinned upstream tarball (image/rounded-corners-build.sh, ADR-0019); that tarball is its corresponding source — record it,
# and mirror it with --download next to the Ubuntu sources (hash verified).
RC_URL=$(sed -n 's/^URL=//p' image/rounded-corners-build.sh); RC_SHA=$(sed -n 's/^SHA=//p' image/rounded-corners-build.sh)
[ -n "$RC_URL" ] && [ -n "$RC_SHA" ] && printf 'Non-archive component source (fabos-rounded-corners): %s sha256 %s\n' "$RC_URL" "$RC_SHA" >> "$OUT/README.txt"
cat "$OUT/README.txt"
if [ "$DL" = --download ]; then mkdir -p "build/sources-$ID"; awk '{print $1}' "$OUT/source-uris.txt" | (cd "build/sources-$ID" && xargs -n1 -P4 curl -sSfLO)
  if [ -n "$RC_URL" ]; then curl -sSfL -o "build/sources-$ID/KDE-Rounded-Corners-$(basename "$RC_URL")" "$RC_URL" && echo "$RC_SHA  build/sources-$ID/KDE-Rounded-Corners-$(basename "$RC_URL")" | sha256sum -c -; fi
  echo "sources mirrored to build/sources-$ID"; fi
