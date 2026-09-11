#!/usr/bin/env bash
# Build the Fabric OS rootfs image with rootless podman and turn it into an ext4 filesystem (no host root).
# Usage: scripts/build-rootfs.sh [vm|iso]   Env: ROOT_SIZE (default 8G), NO_CACHE=1
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; PROFILE=${1:-vm}; TAG=fabric-os:$PROFILE
ROOT_SIZE=${ROOT_SIZE:-8G}; mkdir -p build
[ -x build/bin/aios ] || scripts/stage-ai-binaries.sh
# Feedback channel credentials: if the operator staged build/secrets/feedback.env (never committed), bake it in root-only.
mkdir -p image/overlay/$PROFILE/etc/fabric
if [ -f build/secrets/feedback.env ]; then cp build/secrets/feedback.env image/overlay/$PROFILE/etc/fabric/feedback.env; echo "== feedback.env staged into image (root-only)"; else rm -f image/overlay/$PROFILE/etc/fabric/feedback.env; fi
echo "== podman build ($PROFILE)"
tools/rg --profile heavy -- podman build ${NO_CACHE:+--no-cache} --build-arg PROFILE="$PROFILE" --build-arg MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}" --target rootfs -f image/Containerfile -t "$TAG" . 2>&1 | tee build/podman-build-$PROFILE.log | grep -E '^(STEP|COMMIT|Successfully|Error|error|E:)' || true
podman image exists "$TAG" || { echo "build failed; see build/podman-build-$PROFILE.log"; exit 1; }
echo "== export -> ext4 (inside podman unshare, so ownership is preserved without root)"
CTR=$(podman create --name fabric-export-$$ "$TAG")
BUILD_ID="$(date -u +%Y%m%dT%H%M%SZ)-$PROFILE"; echo "$BUILD_ID" > build/BUILD_ID
podman unshare bash -euo pipefail -c '
  mnt=$(podman mount '"$CTR"')
  printf "FABRIC_IMAGE_PROFILE=%s\nFABRIC_BUILD_ID=%s\n" "'"$PROFILE"'" "'"$BUILD_ID"'" > "$mnt/etc/fabric/release"
  cp "$mnt"/boot/vmlinuz-* build/vmlinuz; cp "$mnt"/boot/initrd.img-* build/initrd.img
  cp "$mnt"/usr/lib/systemd/boot/efi/systemd-bootx64.efi build/systemd-bootx64.efi
  cp "$mnt"/usr/share/fabric/manifest.txt build/manifest-'"$PROFILE"'.txt
  chmod 644 build/vmlinuz build/initrd.img build/systemd-bootx64.efi
  rm -f build/fabric-root-'"$PROFILE"'.ext4
  tools/rg --profile build -- mkfs.ext4 -q -F -L fabric-root -E lazy_itable_init=1,lazy_journal_init=1 -d "$mnt" build/fabric-root-'"$PROFILE"'.ext4 '"$ROOT_SIZE"'
  podman umount '"$CTR"' >/dev/null
'
podman rm "$CTR" >/dev/null
ls -la build/vmlinuz build/initrd.img build/fabric-root-$PROFILE.ext4 2>/dev/null || ls -la build/
echo "== done: build/fabric-root-$PROFILE.ext4 (BUILD_ID $BUILD_ID)"
