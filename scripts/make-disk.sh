#!/usr/bin/env bash
# Assemble a UEFI-bootable GPT disk: ESP (systemd-boot + kernel + initrd) + FabOS root. Rootless.
# Usage: scripts/make-disk.sh [vm]    Env: ESP_MB (512)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; PROFILE=${1:-vm}; ESP_MB=${ESP_MB:-512}
ROOT=build/fabos-root-$PROFILE.ext4; DISK=build/fabos-$PROFILE.img; ESP=build/esp-$PROFILE.img
for t in sfdisk mkfs.vfat mmd mcopy; do command -v $t >/dev/null || { echo "missing host tool: $t (dnf install util-linux dosfstools mtools)"; exit 1; }; done
[ -f "$ROOT" ] || { echo "run scripts/build-rootfs.sh $PROFILE first"; exit 1; }
. brand/brand.conf
root_bytes=$(stat -c %s "$ROOT"); root_mb=$(( (root_bytes + 1048575) / 1048576 ))
# --- ESP ---
rm -f "$ESP"; mkfs.vfat -C -F 32 -n FABOS-ESP "$ESP" $((ESP_MB*1024)) >/dev/null
export MTOOLS_SKIP_CHECK=1
mmd -i "$ESP" ::/EFI ::/EFI/BOOT ::/loader ::/loader/entries ::/fabos
mcopy -i "$ESP" build/systemd-bootx64.efi ::/EFI/BOOT/BOOTX64.EFI
mcopy -i "$ESP" build/vmlinuz ::/fabos/vmlinuz
mcopy -i "$ESP" build/initrd.img ::/fabos/initrd.img
T=$(mktemp -d)
printf 'default fabos.conf\ntimeout 0\nconsole-mode max\neditor no\n' > "$T/loader.conf"
CMDLINE="root=LABEL=fabos-root rw quiet splash loglevel=3 systemd.show_status=false vt.global_cursor_default=0"
printf 'title   %s\nversion %s\nlinux   /fabos/vmlinuz\ninitrd  /fabos/initrd.img\noptions %s\n' "$DISTRO_PRETTY_NAME" "$DISTRO_VERSION" "$CMDLINE" > "$T/fabos.conf"
printf 'title   %s (verbose)\nlinux   /fabos/vmlinuz\ninitrd  /fabos/initrd.img\noptions root=LABEL=fabos-root rw console=ttyS0,115200 console=tty1\n' "$DISTRO_PRETTY_NAME" > "$T/fabos-verbose.conf"
mcopy -i "$ESP" "$T/loader.conf" ::/loader/loader.conf
mcopy -i "$ESP" "$T/fabos.conf" ::/loader/entries/fabos.conf
mcopy -i "$ESP" "$T/fabos-verbose.conf" ::/loader/entries/fabos-verbose.conf
rm -rf "$T"
# --- GPT disk ---
rm -f "$DISK"; total_mb=$((1 + ESP_MB + root_mb + 2)); truncate -s ${total_mb}M "$DISK"
sfdisk -q "$DISK" <<SF
label: gpt
start=1MiB, size=${ESP_MB}MiB, type=uefi, name="FABOS-ESP", bootable
start=$((1+ESP_MB))MiB, size=${root_mb}MiB, type=linux, name="fabos-root"
SF
dd if="$ESP"  of="$DISK" bs=1M seek=1 conv=notrunc,sparse status=none
dd if="$ROOT" of="$DISK" bs=1M seek=$((1+ESP_MB)) conv=notrunc,sparse status=none
sfdisk -l "$DISK" | tail -4; echo "== disk: $DISK ($(du -h --apparent-size "$DISK" | cut -f1) apparent, $(du -h "$DISK" | cut -f1) on disk)"
