#!/usr/bin/env bash
# Build the FabOS OS rootfs image with rootless podman and turn it into an ext4 filesystem (no host root).
# Usage: scripts/build-rootfs.sh [vm|iso]   Env: ROOT_SIZE (default 14G — the vm test disk must hold the built-in model, Firefox and voice with room to run), NO_CACHE=1
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; PROFILE=${1:-vm}; TAG=fabos:$PROFILE
ROOT_SIZE=${ROOT_SIZE:-14G}; mkdir -p build
[ -x build/bin/aios ] || scripts/stage-ai-binaries.sh
# Feedback channel credentials: if the operator staged build/secrets/feedback.env (never committed), bake it in root-only.
mkdir -p image/overlay/$PROFILE/etc/fabos
if [ -f build/secrets/feedback.env ]; then cp build/secrets/feedback.env image/overlay/$PROFILE/etc/fabos/feedback.env; echo "== feedback.env staged into image (root-only)"; else rm -f image/overlay/$PROFILE/etc/fabos/feedback.env; fi
# Google OAuth desktop client for the Gmail sign-in (build/secrets/google-oauth.env, never committed). World-readable in the
# image on purpose: the agent daemon runs as the user, and Google treats desktop-app client secrets as non-confidential.
if [ -f build/secrets/google-oauth.env ]; then install -m 0644 build/secrets/google-oauth.env image/overlay/$PROFILE/etc/fabos/google-oauth.env; echo "== google-oauth.env staged into image"; else rm -f image/overlay/$PROFILE/etc/fabos/google-oauth.env; fi
# Mozilla's apt signing key is fetched HERE on the host (never inside podman build: under the resource guard the build
# container could not reach packages.mozilla.org tonight, twice) and copied into the pkgs stage, which verifies its
# fingerprint offline. A previously fetched copy is reused when the network is down.
if curl -fsSL -4 -m 60 -o build/moz-key.asc.new https://packages.mozilla.org/apt/repo-signing-key.gpg 2>/dev/null; then mv build/moz-key.asc.new build/moz-key.asc; else rm -f build/moz-key.asc.new; [ -s build/moz-key.asc ] || { echo "ERROR: cannot fetch Mozilla's signing key and no cached build/moz-key.asc"; exit 1; }; fi
gpg --show-keys --with-fingerprint --with-colons build/moz-key.asc 2>/dev/null | grep -q '^fpr:::::::::35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3:' || { echo "ERROR: Mozilla key fingerprint mismatch in build/moz-key.asc"; exit 1; }
echo "== Mozilla signing key staged (fingerprint verified)"
echo "== podman build ($PROFILE)"
PREV_ID=$(podman image inspect --format '{{.Id}}' "$TAG" 2>/dev/null || echo none)
tools/rg --profile heavy -- podman build ${NO_CACHE:+--no-cache} --build-arg PROFILE="$PROFILE" --build-arg MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}" --target rootfs -f image/Containerfile -t "$TAG" . 2>&1 | tee build/podman-build-$PROFILE.log | grep -E '^(STEP|COMMIT|Successfully|Error|error|E:)' || true
BUILD_RC=${PIPESTATUS[0]}
[ "$BUILD_RC" = 0 ] || { echo "ERROR: podman build exited $BUILD_RC (killed or failed) — NOT exporting a stale image. See build/podman-build-$PROFILE.log"; exit 1; }
grep -qE '^(Successfully tagged|COMMIT)' build/podman-build-$PROFILE.log || { echo "ERROR: podman build did not reach a final commit — NOT exporting. See build/podman-build-$PROFILE.log"; exit 1; }
NEW_ID=$(podman image inspect --format '{{.Id}}' "$TAG")
echo "== image $TAG: ${PREV_ID#sha256:} -> ${NEW_ID#sha256:}"
echo "== export -> ext4 (inside podman unshare, so ownership is preserved without root)"
CTR=$(podman create --name fabos-export-$$ "$TAG")
BUILD_ID="$(date -u +%Y%m%dT%H%M%SZ)-$PROFILE"; echo "$BUILD_ID" > build/BUILD_ID
podman unshare bash -euo pipefail -c '
  mnt=$(podman mount '"$CTR"')
  printf "FABOS_IMAGE_PROFILE=%s\nFABOS_BUILD_ID=%s\n" "'"$PROFILE"'" "'"$BUILD_ID"'" > "$mnt/etc/fabos/release"
  cp "$mnt"/boot/vmlinuz-* build/vmlinuz; cp "$mnt"/boot/initrd.img-* build/initrd.img
  cp "$mnt"/usr/lib/systemd/boot/efi/systemd-bootx64.efi build/systemd-bootx64.efi 2>/dev/null || true
  cp "$mnt"/usr/share/fabos/manifest.txt build/manifest-'"$PROFILE"'.txt
  chmod 644 build/vmlinuz build/initrd.img build/systemd-bootx64.efi
  if [ "'"$PROFILE"'" = iso ]; then
    cp "$mnt"/usr/lib/shim/shimx64.efi.signed.latest build/shimx64.efi; cp "$mnt"/usr/lib/shim/mmx64.efi build/mmx64.efi 2>/dev/null || true
    cp "$mnt"/usr/lib/grub/x86_64-efi-signed/gcdx64.efi.signed build/gcdx64.efi
    cp build/manifest-iso.txt build/filesystem.manifest
    du -sx --block-size=1 "$mnt" | cut -f1 > build/filesystem.size
    rm -f build/filesystem.squashfs
    tools/rg --profile heavy -- mksquashfs "$mnt" build/filesystem.squashfs -comp zstd -Xcompression-level 15 -b 1M -noappend -wildcards -e "proc/*" "sys/*" "dev/*" "run/*" "tmp/*" "var/lib/apt/lists/*" "var/cache/apt/archives/*.deb"
  else
    rm -f build/fabos-root-'"$PROFILE"'.ext4
    tools/rg --profile build -- mkfs.ext4 -q -F -L fabos-root -E lazy_itable_init=1,lazy_journal_init=1 -d "$mnt" build/fabos-root-'"$PROFILE"'.ext4 '"$ROOT_SIZE"'
  fi
  podman umount '"$CTR"' >/dev/null
'
podman rm "$CTR" >/dev/null
ls -la build/vmlinuz build/initrd.img build/fabos-root-$PROFILE.ext4 build/filesystem.squashfs 2>/dev/null || ls -la build/
echo "== done: build/fabos-root-$PROFILE.ext4 (BUILD_ID $BUILD_ID)"
