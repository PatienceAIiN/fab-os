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
# GitHub caps one asset at 2 GiB; the ISO is bigger and splitting is user-hostile. The full one-click download is
# hosted on fabos.patienceai.in (scripts/publish-iso.sh). GitHub carries only the checksum, manifest and notes.
if [ "$size" -le "$limit" ]; then cp "$ISO" "$OUT/"; else echo "== ISO is $((size/1048576)) MB (> GitHub 2 GiB limit) — publishing checksum only; full download is on fabos.patienceai.in/download/"; fi
cp /dev/null "$OUT/MANIFEST.txt"; podman run --rm localhost/fabos:iso cat /usr/share/fabos/manifest.txt > "$OUT/MANIFEST.txt" 2>/dev/null || echo "(manifest unavailable)" > "$OUT/MANIFEST.txt"
cat > "$OUT/NOTES.md" <<MD
# Fab OS $TAG (pre-release)

Ubuntu 26.04 LTS based desktop by Patience AI with KDE Plasma 6, the Fab OS look, and the built-in Fab OS agent
(Anthropic Claude / Google Gemini / OpenAI / DeepSeek / local models with a real connection check; ask / auto / bypass permission modes; System-Wide AI switch).

## Download
One file, one click: **https://fabos.patienceai.in/download/$base**  (about 2.3 GB).
Write it to an 8 GB+ USB stick with [Balena Etcher](https://etcher.balena.io/), restart, pick the USB stick.
Try it live, then double-click **Install Fab OS** on the desktop. Verify with \`sha256sum -c $base.sha256\`.

## What is inside
See MANIFEST.txt (every package and version). Bundled: Brave Browser (Brave's official build), LibreOffice, VLC, KWeather, Fab Terminal,
Fab Files, Fab Editor, Fab Software, Fab Photos, Fab Documents, Fab AI Controls, Fab Updates, Fab Feedback.

## Legal
Fab OS is an independent project. Ubuntu is a trademark of Canonical Ltd.; KDE and Plasma are trademarks of KDE e.V.
Fab OS is not endorsed by either. Licences: LICENSING.md, ATTRIBUTIONS.md, THIRD_PARTY_LICENSES/, legal/.
Corresponding source for the shipped GPL packages: legal/SOURCE-OFFER.md.
MD
echo "== creating release $TAG on $REPO"
gh release create "$TAG" -R "$REPO" $DRAFT --prerelease --title "Fab OS $TAG" --notes-file "$OUT/NOTES.md" "$OUT"/* 
echo "== done: https://github.com/$REPO/releases/tag/$TAG"
