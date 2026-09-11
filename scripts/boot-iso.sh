#!/usr/bin/env bash
# Boot the Fab OS ISO in QEMU/KVM with UEFI (OVMF), optionally with a blank target disk for an install test.
# Usage: scripts/boot-iso.sh [--headless] [--autotest] [--target-disk] [--mem MB] [--timeout S]
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; . brand/brand.conf
ISO=build/${DISTRO_ID}-${DISTRO_VERSION}-desktop-amd64.iso; MEM=2560; CPUS=4; HEADLESS=0; AUTOTEST=0; TIMEOUT=0; TARGET=0
while [ $# -gt 0 ]; do case $1 in --headless) HEADLESS=1;; --autotest) AUTOTEST=1;; --target-disk) TARGET=1;; --mem) MEM=$2; shift;; --timeout) TIMEOUT=$2; shift;; *) echo "unknown $1"; exit 2;; esac; shift; done
[ -f "$ISO" ] || { echo "no $ISO — run scripts/build-iso.sh"; exit 1; }
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1); VARS_SRC=$(ls /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd 2>/dev/null | head -1)
cp "$VARS_SRC" build/OVMF_VARS_iso.fd; SERIAL=build/serial-iso.log; : > "$SERIAL"
args=( -name "Fab OS live" -machine q35,accel=kvm -cpu host -smp $CPUS -m "$MEM" -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file=build/OVMF_VARS_iso.fd
  -drive file="$ISO",media=cdrom,if=none,id=cd -device ahci,id=ahci -device ide-cd,drive=cd,bus=ahci.0,bootindex=1
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci -netdev user,id=n0 -device virtio-net-pci,netdev=n0 -serial file:"$SERIAL" -monitor none -rtc base=utc )
if [ $TARGET = 1 ]; then [ -f build/install-target.qcow2 ] || qemu-img create -f qcow2 build/install-target.qcow2 24G >/dev/null; args+=( -drive file=build/install-target.qcow2,if=virtio,format=qcow2 ); fi
if [ $HEADLESS = 1 ]; then args+=( -display none -device virtio-vga ); else args+=( -device virtio-vga-gl -display gtk,gl=on,show-cursor=on ); fi
if [ $AUTOTEST = 1 ]; then args+=( -no-reboot -fw_cfg name=opt/fabos/autotest,string=1 ); fi
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1200))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+900))M}
echo "== booting $ISO mem=${MEM}M headless=$HEADLESS autotest=$AUTOTEST target=$TARGET (serial -> $SERIAL)"
if [ "$TIMEOUT" != 0 ]; then tools/rg --profile vm -- timeout --foreground -k 10 "$TIMEOUT" qemu-system-x86_64 "${args[@]}" || true; else tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}"; fi
echo "== qemu exited"; sed 's/\x1b\[[0-9;]*m//g' "$SERIAL" | grep -E '^(FABOS_LIVE|PRETTY_NAME|KERNEL|LIVE_USER|SDDM|PLASMA|CALAMARES|INSTALLER_ICON|AGENT|FIRMWARE|MEM_USED)' || tail -n 15 "$SERIAL"
