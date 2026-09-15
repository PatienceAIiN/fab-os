#!/usr/bin/env bash
# Write SHA256SUMS and a detached OpenPGP signature SHA256SUMS.gpg for release files, signed with the Fab OS Archive key —
# the same key and GNUPGHOME scripts/publish-apt.sh signs the apt repository with (APT_GNUPGHOME, default
# build/secrets/gpg-home; APT_KEY_ID, default "Fab OS Archive"). The public key is exported next to them as
# fabos-archive-key.asc (publish-apt.sh already serves it at https://fabos.patienceai.in/apt/fabos-archive-key.asc).
#   scripts/release-checksums.sh <out-dir> <file>...     -> <out-dir>/SHA256SUMS, SHA256SUMS.gpg, fabos-archive-key.asc
#   FABOS_UNSIGNED=1 scripts/release-checksums.sh ...   -> SHA256SUMS only, with a warning on stderr (never for a release)
# Verify:  sha256sum -c SHA256SUMS   and   gpg --verify SHA256SUMS.gpg SHA256SUMS   (after importing fabos-archive-key.asc)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:?usage: release-checksums.sh <out-dir> <file>...}; shift
[ $# -gt 0 ] || { echo "usage: $0 <out-dir> <file>..." >&2; exit 1; }
GNUPGHOME=${APT_GNUPGHOME:-$HERE/build/secrets/gpg-home}; export GNUPGHOME
KEY=${APT_KEY_ID:-"Fab OS Archive"}
mkdir -p "$OUT"
: > "$OUT/SHA256SUMS"
for f in "$@"; do
  [ -f "$f" ] || { echo "no such file: $f" >&2; exit 1; }
  (cd "$(dirname "$f")" && sha256sum "$(basename "$f")") >> "$OUT/SHA256SUMS"
done
if [ "${FABOS_UNSIGNED:-0}" = 1 ]; then
  rm -f "$OUT/SHA256SUMS.gpg"
  echo "WARNING: FABOS_UNSIGNED=1 — $OUT/SHA256SUMS is NOT signed; do not publish this as a release" >&2
  exit 0
fi
[ -d "$GNUPGHOME" ] || { echo "no GNUPGHOME at $GNUPGHOME (set APT_GNUPGHOME, or FABOS_UNSIGNED=1 for an unsigned local build)" >&2; exit 1; }
gpg --batch --yes --default-key "$KEY" --detach-sign -o "$OUT/SHA256SUMS.gpg" "$OUT/SHA256SUMS"
gpg --export --armor "$KEY" > "$OUT/fabos-archive-key.asc"
gpg --verify "$OUT/SHA256SUMS.gpg" "$OUT/SHA256SUMS" 2>/dev/null || { echo "signature does not verify" >&2; exit 1; }
echo "== signed: $OUT/SHA256SUMS $OUT/SHA256SUMS.gpg (key: $KEY, $(gpg --with-colons --list-keys "$KEY" | awk -F: '/^fpr/ {print $10; exit}'))"
