#!/usr/bin/env bash
# Build the FabOS OS rootfs image with rootless podman and turn it into an ext4 filesystem (no host root).
# Usage: scripts/build-rootfs.sh [vm|iso]   Env: ROOT_SIZE (default 8G), NO_CACHE=1
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; PROFILE=${1:-vm}; TAG=fabos:$PROFILE
ROOT_SIZE=${ROOT_SIZE:-8G}; mkdir -p build
[ -x build/bin/aios ] || scripts/stage-ai-binaries.sh
# Feedback channel credentials: if the operator staged build/secrets/feedback.env (never committed), bake it in root-only.
mkdir -p image/overlay/$PROFILE/etc/fabos
if [ -f build/secrets/feedback.env ]; then cp build/secrets/feedback.env image/overlay/$PROFILE/etc/fabos/feedback.env; echo "== feedback.env staged into image (root-only)"; else rm -f image/overlay/$PROFILE/etc/fabos/feedback.env; fi
echo "== podman build ($PROFILE)"
tools/rg --profile heavy -- podman build ${NO_CACHE:+--no-cache} --build-arg PROFILE="$PROFILE" --build-arg MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}" --target rootfs -f image/Containerfile -t "$TAG" . 2>&1 | tee build/podman-build-$PROFILE.log | grep -E '^(STEP|COMMIT|Successfully|Error|error|E:)' || true
podman image exists "$TAG" || { echo "build failed; see build/podman-build-$PROFILE.log"; exit 1; }
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
    dpkg-query --admindir="$mnt/var/lib/dpkg" -W -f="${binary:Package}\t${Version}\n" > build/filesystem.manifest 2>/dev/null || cp build/manifest-iso.txt build/filesystem.manifest
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
