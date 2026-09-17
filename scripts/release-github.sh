#!/usr/bin/env bash
# Publish a Fab OS release on GitHub (PatienceAIiN/fab-os). Two flavours, one tag formula: v<DISTRO_VERSION>.<PKG_REVISION-1>
# (packages 1.0-6 -> v1.0.5, 1.0-7 -> v1.0.6). The "what changed" notes come from docs/CHANGELOG.md through
# scripts/changelog-render.py --notes, so the changelog entry must exist and be finished first (docs/RELEASING.md).
#
#   Image release   scripts/release-github.sh v1.0.5 [--iso build/fabos-1.0-desktop-amd64.iso] [--draft] [--target SHA] [--dry-run]
#                   assets: SHA256SUMS + SHA256SUMS.gpg (Fab OS Archive key), <iso>.sha256, fabos-archive-key.asc, MANIFEST.txt
#                   (every package in the built image), NOTES.md. The ISO is bigger than GitHub's 2 GiB asset cap and splitting
#                   is user-hostile, so the one-click file stays on fabos.patienceai.in (scripts/publish-iso.sh).
#   Over-the-air    scripts/release-github.sh v1.0.6 --ota [--suite loom] [--draft] [--target SHA] [--dry-run]
#                   no image; assets: NOTES.md, Packages (the update channel's index at this revision, from
#                   build/apt-repo/<suite>), InRelease (its signed cover), SHA256SUMS + SHA256SUMS.gpg of the .debs,
#                   fabos-archive-key.asc. Title "Fab OS v1.0.6 — over-the-air update 1.0-7".
#   --dry-run       assembles the asset set in a temp dir, signs nothing, creates nothing; prints what it would upload, the
#                   notes and the exact gh command. --target SHA pins the new tag to a commit (gh otherwise tags the head of
#                   the default branch). --draft saves a GitHub draft instead of publishing.
#   FABOS_BUILD=DIR a worktree pointing at the main checkout's build/ (default ./build); APT_GNUPGHOME overrides the key home
#                   (default $FABOS_BUILD/secrets/gpg-home, the same key scripts/publish-apt.sh signs the channel with).
# Users verify with:  sha256sum -c SHA256SUMS   and   gpg --verify SHA256SUMS.gpg SHA256SUMS  (after importing fabos-archive-key.asc)
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
. brand/brand.conf
TAG=${1:?usage: release-github.sh vX.Y.Z [--ota] [--dry-run] [--draft] [--target SHA] [--iso PATH] [--suite S] [--repo OWNER/REPO]}; shift
REPO=PatienceAIiN/fab-os; BUILD=${FABOS_BUILD:-$HERE/build}; MODE=image; ISO=""; DRAFT=0; TARGET=""; DRY=0; SUITE=$DISTRO_CODENAME
while [ $# -gt 0 ]; do case $1 in
  --repo) REPO=$2; shift;; --iso) ISO=$2; shift;; --draft) DRAFT=1;; --ota) MODE=ota;; --suite) SUITE=$2; shift;;
  --target) TARGET=$2; shift;; --dry-run) DRY=1;; *) echo "release-github: unknown option $1" >&2; exit 2;; esac; shift; done
die() { echo "release-github: $*" >&2; exit 1; }
export APT_GNUPGHOME=${APT_GNUPGHOME:-$BUILD/secrets/gpg-home}

# ---- tag -> package revision, and the changelog entry that carries the notes ------------------------------------------
[[ $TAG =~ ^v${DISTRO_VERSION//./\\.}\.([0-9]+)$ ]] || die "tag $TAG must read v$DISTRO_VERSION.<n> with n = PKG_REVISION - 1"
REV=$(( ${BASH_REMATCH[1]} + 1 )); PKGVER="$DISTRO_VERSION-$REV"
eval "$(python3 scripts/changelog-render.py --info "$REV")"          # CL_VERSION CL_TAG CL_KIND CL_DATE CL_STATUS CL_FILE
[ "$CL_TAG" = "$TAG" ] || die "docs/CHANGELOG.md gives $PKGVER the tag $CL_TAG, not $TAG"
[ "$CL_KIND" = "$MODE" ] || die "docs/CHANGELOG.md says $PKGVER is kind=$CL_KIND, but this run publishes an $MODE release"
if [ "$CL_STATUS" != released ]; then
  if [ $DRY = 1 ]; then echo "WARNING: the changelog entry for $PKGVER is still a draft — finish it before the real run (docs/RELEASING.md)" >&2
  else die "the changelog entry for $PKGVER is still a draft — finish it first (docs/RELEASING.md)"; fi
fi
if [ $DRY = 1 ]; then OUT=$(mktemp -d "/tmp/fabos-release-$TAG.XXXXXX"); else OUT=$BUILD/release/$TAG; rm -rf "$OUT"; mkdir -p "$OUT"; fi
sign() {   # sign <files...>  -> $OUT/SHA256SUMS, SHA256SUMS.gpg, fabos-archive-key.asc. A dry run never opens the key.
  if [ $DRY = 1 ]; then
    FABOS_UNSIGNED=1 scripts/release-checksums.sh "$OUT" "$@" 2>/dev/null
    echo "   (dry run: SHA256SUMS left unsigned; the real run signs it with the Fab OS Archive key from $APT_GNUPGHOME)"
  else scripts/release-checksums.sh "$OUT" "$@"; fi
  [ -f "$OUT/fabos-archive-key.asc" ] || cp "$BUILD/apt-repo/$SUITE/fabos-archive-key.asc" "$OUT/" 2>/dev/null || true
}
LEGAL='## Legal
Fab OS is an independent project. Ubuntu is a trademark of Canonical Ltd.; KDE and Plasma are trademarks of KDE e.V.
Fab OS is not endorsed by either. Licences: LICENSING.md, ATTRIBUTIONS.md, THIRD_PARTY_LICENSES/, legal/.
Corresponding source for the shipped GPL packages: legal/SOURCE-OFFER.md.'

if [ $MODE = image ]; then
  # ---- image release --------------------------------------------------------------------------------------------------
  [ -n "$ISO" ] || ISO=$(ls "$BUILD"/fabos-*-desktop-amd64.iso 2>/dev/null | head -1 || true)
  [ -n "$ISO" ] && [ -f "$ISO" ] || die "no ISO under $BUILD (or --iso PATH) — run scripts/build-rootfs.sh iso && scripts/build-iso.sh"
  base=$(basename "$ISO"); size=$(stat -c %s "$ISO")
  echo "== image $base ($((size/1048576)) MB), package revision $PKGVER"
  (cd "$(dirname "$ISO")" && sha256sum "$base") > "$OUT/$base.sha256"
  sign "$ISO"
  limit=$((1900*1024*1024))
  if [ "$size" -le "$limit" ]; then
    if [ $DRY = 1 ]; then ln -s "$(readlink -f "$ISO")" "$OUT/$base"; else cp "$ISO" "$OUT/"; fi
  else echo "== the ISO exceeds GitHub's 2 GiB asset cap — GitHub carries checksum, manifest and notes; the file is served from fabos.patienceai.in"; fi
  if command -v podman >/dev/null && podman image exists localhost/fabos:iso 2>/dev/null; then
    podman run --rm localhost/fabos:iso cat /usr/share/fabos/manifest.txt > "$OUT/MANIFEST.txt" 2>/dev/null || echo "(manifest unavailable)" > "$OUT/MANIFEST.txt"
  else echo "(manifest unavailable: no localhost/fabos:iso image on this host)" > "$OUT/MANIFEST.txt"; fi
  cat > "$OUT/NOTES.md" <<MD
# Fab OS $TAG (pre-release) — image, package revision $PKGVER

Ubuntu 26.04 LTS based desktop by Patience AI with KDE Plasma 6, the Fab OS look, and the built-in Fab OS agent
(Anthropic Claude / Google Gemini / OpenAI / DeepSeek / Ollama / the built-in offline model, with a real connection check; ask / auto / bypass permission modes; System-Wide AI switch).

## Get the image
One file, one click: **https://fabos.patienceai.in/download/$base**  (about $(awk -v s="$size" 'BEGIN{printf "%.1f GB", s/1e9}')).
Write it to an 8 GB+ USB stick with [Balena Etcher](https://etcher.balena.io/), restart, pick the USB stick.
Try it live, then double-click **Install Fab OS** on the desktop. Verify with \`sha256sum -c SHA256SUMS\` and
\`gpg --verify SHA256SUMS.gpg SHA256SUMS\` (Fab OS Archive key: fabos-archive-key.asc, also at https://fabos.patienceai.in/apt/fabos-archive-key.asc).

$(python3 scripts/changelog-render.py --notes "$REV")

## What is inside
See MANIFEST.txt (every package and version). Bundled: Firefox (Mozilla's official build), LibreOffice, VLC, KWeather, Fab Terminal,
Fab Files, Fab Editor, Fab Software, Fab Photos, Fab Documents, Fab AI Controls, Fab Updates, Fab Feedback.
Installed systems receive these packages ($PKGVER) through Fab Updates without reinstalling.

$LEGAL
MD
  TITLE="Fab OS $TAG"
else
  # ---- over-the-air release: the signed channel already carries the packages; GitHub gets the proof and the notes ------
  REPODIR=$BUILD/apt-repo/$SUITE; POOL=$REPODIR/pool/$SUITE; DIST=$REPODIR/dists/$SUITE; INDEX=$DIST/main/binary-amd64/Packages
  [ -s "$INDEX" ] && [ -s "$DIST/InRelease" ] || die "no signed repository at $REPODIR — run scripts/publish-apt.sh first (FABOS_BUILD points a worktree at the main build/)"
  mapfile -t DEBS < <(ls "$POOL"/*_"$PKGVER"_*.deb 2>/dev/null); [ ${#DEBS[@]} -gt 0 ] || die "no packages at $PKGVER in $POOL"
  # the index must describe exactly this revision — a repository already rebuilt for the next one is the wrong snapshot
  others=$(grep -E '^Version: ' "$INDEX" | grep -v -x "Version: $PKGVER" || true)
  [ -z "$others" ] || die "the $SUITE index carries versions other than $PKGVER: $(echo "$others" | tr '\n' ' ')"
  n_index=$(grep -c '^Package:' "$INDEX"); [ "$n_index" -eq ${#DEBS[@]} ] || die "the index lists $n_index packages, the pool has ${#DEBS[@]} at $PKGVER"
  echo "== over-the-air $PKGVER: ${#DEBS[@]} packages in suite $SUITE"; printf '   %s\n' "${DEBS[@]##*/}"
  cp "$INDEX" "$OUT/Packages"; cp "$DIST/InRelease" "$OUT/InRelease"
  sign "${DEBS[@]}"
  cat > "$OUT/NOTES.md" <<MD
# Fab OS $TAG — over-the-air update $PKGVER

Ubuntu 26.04 LTS based desktop by Patience AI with KDE Plasma 6 and the built-in Fab OS agent. This release carries no disc
image: it is package revision **$PKGVER**, delivered to every installed Fab OS system on its own through **Fab Updates**
(the signed update channel, suite \`$SUITE\`). Open Fab Updates to check now; afterwards it says exactly one thing —
nothing to do, log out and back in, or restart.

$(python3 scripts/changelog-render.py --notes "$REV")

## What is attached
- **Packages** — the update channel's index at this revision: name, version, size and checksum of every Fab OS package.
- **InRelease** — the signed cover of that index (Fab OS Archive key): \`gpg --verify InRelease\` after importing the key.
- **SHA256SUMS** / **SHA256SUMS.gpg** — checksums of the ${#DEBS[@]} packages, signed with the same key: \`gpg --verify SHA256SUMS.gpg SHA256SUMS\`.
- **fabos-archive-key.asc** — the public key, also at https://fabos.patienceai.in/apt/fabos-archive-key.asc.

The packages themselves are served by the update channel (https://fabos.patienceai.in/apt/). A new installation starts
from the current image (https://fabos.patienceai.in/#status) and updates itself to $PKGVER on its first check.

$LEGAL
MD
  TITLE="Fab OS $TAG — over-the-air update $PKGVER"
fi

echo "== assets"; (cd "$OUT" && stat -L -c '   %10s  %n' *)
CMD=(gh release create "$TAG" -R "$REPO" --prerelease --title "$TITLE" --notes-file "$OUT/NOTES.md")
[ $DRAFT = 1 ] && CMD+=(--draft); [ -n "$TARGET" ] && CMD+=(--target "$TARGET"); CMD+=("$OUT"/*)
if [ $DRY = 1 ]; then
  echo "== DRY RUN — nothing created, nothing signed. Would run:"; printf '  '; printf ' %q' "${CMD[@]}"; echo
  echo "== NOTES.md:"; sed 's/^/   | /' "$OUT/NOTES.md"
  echo "== assets kept in $OUT for inspection (remove when done)"; exit 0
fi
echo "== creating release $TAG on $REPO"; "${CMD[@]}"
echo "== done: https://github.com/$REPO/releases/tag/$TAG"
