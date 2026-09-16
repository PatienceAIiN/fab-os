#!/usr/bin/env bash
# Stage a LOCAL apt repository for the over-the-air tests, from the already built .debs (build/debs) with the working
# tree's fabos-updates package and the fabos-desktop/fabos-branding maintainer files applied — no rootfs build needed.
#   tests/ota-stage-repo.sh --out DIR [--debs build/debs] [--bump +ota1] [--sign-test-key]
#   --bump SUFFIX      append SUFFIX to every package version (1.0-6 -> 1.0-6+ota1: an installed 1.0-6 sees an upgrade)
#   --sign-test-key    sign Release with a throwaway key (DIR/test-archive-keyring.gpg); the real key lives in
#                      build/secrets and is used by scripts/publish-apt.sh only. Unsigned otherwise (apt refuses that).
# Layout = what publish-apt.sh deploys: DIR/dists/<suite>/{InRelease,Release,Release.gpg,main/binary-*/Packages*}, DIR/pool/<suite>/*.deb
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
OUT=""; DEBS=build/debs; ARGS=(); SIGN=0; SUITE=""
while [ $# -gt 0 ]; do case $1 in --out) OUT=$2; shift;; --debs) DEBS=$2; shift;; --suite) SUITE=$2; ARGS+=("$1" "$2"); shift;; --bump) ARGS+=("$1" "$2"); shift;; --sign-test-key) SIGN=1;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
# shellcheck disable=SC1091
. brand/brand.conf; SUITE=${SUITE:-$DISTRO_CODENAME}
[ -n "$OUT" ] || { echo "--out DIR is required"; exit 2; }
ls "$DEBS"/fabos-updates_*.deb >/dev/null 2>&1 || { echo "no fabos-updates .deb in $DEBS (run scripts/publish-apt.sh --no-deploy first)"; exit 1; }
podman image exists localhost/fabos:pkgs || { echo "podman image localhost/fabos:pkgs missing (built by scripts/publish-apt.sh step 1)"; exit 1; }
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd); DEBS=$(cd "$DEBS" && pwd)
podman run --rm -v "$HERE:/work:Z,ro" -v "$DEBS:/debs:Z,ro" -v "$OUT:/out:Z" localhost/fabos:pkgs /work/tests/ota-stage-repo-inside.sh --src /work --debs /debs --out /out "${ARGS[@]}"
if [ $SIGN = 1 ]; then
  # throwaway key, generated and used on the host (gpg + gpg-agent); nothing from build/secrets is touched
  export GNUPGHOME="$OUT/.gnupg-test"; rm -rf "$GNUPGHOME"; mkdir -m 700 "$GNUPGHOME"
  gpg --batch --quiet --passphrase '' --pinentry-mode loopback --quick-gen-key "Fab OS OTA local test key <ota-test@localhost>" ed25519 sign never
  gpg --batch --yes -abs -o "$OUT/dists/$SUITE/Release.gpg" "$OUT/dists/$SUITE/Release"
  gpg --batch --yes --clearsign -o "$OUT/dists/$SUITE/InRelease" "$OUT/dists/$SUITE/Release"
  gpg --export "ota-test@localhost" > "$OUT/test-archive-keyring.gpg"
  gpgconf --kill gpg-agent 2>/dev/null || true
  chmod a+r "$OUT/dists/$SUITE/InRelease" "$OUT/dists/$SUITE/Release.gpg" "$OUT/test-archive-keyring.gpg"
  echo "signed with a throwaway test key -> $OUT/test-archive-keyring.gpg"
fi
