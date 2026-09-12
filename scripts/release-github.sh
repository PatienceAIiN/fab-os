#!/usr/bin/env bash
# Publish a Fab OS release on GitHub: ISO (split into <2 GiB parts, GitHub's per-asset limit), checksums, manifest, notes.
# Usage: scripts/release-github.sh vX.Y.Z [--repo PatienceAIiN/fab-os] [--iso build/fabos-1.0-desktop-amd64.iso] [--draft]
# Reassemble: cat fabos-*.iso.part-* > fabos-<ver>-desktop-amd64.iso && sha256sum -c fabos-<ver>-desktop-amd64.iso.sha256
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
TAG=${1:?tag like v1.0.0}; shift; REPO=PatienceAIiN/fab-os; ISO=$(ls build/fabos-*-desktop-amd64.iso 2>/dev/null | head -1); DRAFT=""
while [ $# -gt 0 ]; do case $1 in --repo) REPO=$2; shift;; --iso) ISO=$2; shift;; --draft) DRAFT="--draft";; esac; shift; done
[ -f "$ISO" ] || { echo "no ISO at $ISO — run scripts/build-rootfs.sh iso && scripts/build-iso.sh"; exit 1; }
OUT=build/release/$TAG; rm -rf "$OUT"; mkdir -p "$OUT"; base=$(basename "$ISO")
echo "== checksums"; (cd "$(dirname "$ISO")" && sha256sum "$base") > "$OUT/$base.sha256"
size=$(stat -c %s "$ISO"); limit=$((1900*1024*1024))
if [ "$size" -gt "$limit" ]; then echo "== splitting $((size/1048576)) MB ISO into parts"; split -b 1900M -d -a 2 "$ISO" "$OUT/$base.part-"; (cd "$OUT" && sha256sum "$base".part-* > "$base.parts.sha256")
else cp "$ISO" "$OUT/"; fi
cp /dev/null "$OUT/MANIFEST.txt"; podman run --rm localhost/fabos:iso cat /usr/share/fabos/manifest.txt > "$OUT/MANIFEST.txt" 2>/dev/null || echo "(manifest unavailable)" > "$OUT/MANIFEST.txt"
cat > "$OUT/NOTES.md" <<MD
# Fab OS $TAG (pre-release)

Ubuntu 26.04 LTS based desktop by Patience AI with KDE Plasma 6, the Fab OS look, and the built-in Fab OS agent
(Claude / OpenAI / Gemini / local models; ask / auto / bypass permission modes; System-Wide AI switch).

## Download
GitHub limits each asset to 2 GiB, so the ISO is split. Download all parts and reassemble:

    cat $base.part-* > $base
    sha256sum -c $base.sha256

Write it to a USB stick (e.g. \`dd if=$base of=/dev/sdX bs=4M status=progress oflag=sync\`) or boot it in a VM with UEFI.
Live user: \`fabos\` (no password). The installer is on the live desktop.

## What is inside
See MANIFEST.txt (every package and version). Bundled: Firefox (Mozilla build), LibreOffice, VLC, KWeather, Fab Terminal,
Fab Files, Fab Editor, Fab Software, Fab Photos, Fab Documents, Fab Command Center, Fab Updates, Fab Feedback.

## Legal
Fab OS is an independent project. Ubuntu is a trademark of Canonical Ltd.; KDE and Plasma are trademarks of KDE e.V.
Fab OS is not endorsed by either. Licences: LICENSING.md, ATTRIBUTIONS.md, THIRD_PARTY_LICENSES/, legal/.
Corresponding source for the shipped GPL packages: legal/SOURCE-OFFER.md.
MD
echo "== creating release $TAG on $REPO"
gh release create "$TAG" -R "$REPO" $DRAFT --prerelease --title "Fab OS $TAG" --notes-file "$OUT/NOTES.md" "$OUT"/* 
echo "== done: https://github.com/$REPO/releases/tag/$TAG"
