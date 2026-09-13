#!/usr/bin/env bash
# Boot the FabOS OS disk in QEMU/KVM with UEFI (OVMF), under the resource guard so the host never hangs.
# Usage: scripts/boot-vm.sh [--headless] [--autotest] [--mem MB] [--cpus N] [--timeout S] [--fresh-vars] [--heads N]  (N virtual displays for multi-monitor tests)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
MEM=2048; CPUS=4; HEADLESS=0; AUTOTEST=0; TIMEOUT=0; GL=0; HEADS=1; DISK=build/fabos-vm.img
while [ $# -gt 0 ]; do case $1 in
  --headless) HEADLESS=1;; --autotest) AUTOTEST=1;; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;;
  --timeout) TIMEOUT=$2; shift;; --gl) GL=1;; --heads) HEADS=$2; shift;; --fresh-vars) rm -f build/OVMF_VARS.fd;; --disk) DISK=$2; shift;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
[ -f "$DISK" ] || { echo "no $DISK — run scripts/make-disk.sh"; exit 1; }
[ -e /dev/kvm ] || echo "WARNING: /dev/kvm missing, falling back to TCG (slow)"
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1)
VARS_SRC=$(ls /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd 2>/dev/null | head -1)
[ -f build/OVMF_VARS.fd ] || cp "$VARS_SRC" build/OVMF_VARS.fd
ACCEL=$([ -e /dev/kvm ] && echo kvm || echo tcg); CPU=$([ -e /dev/kvm ] && echo host || echo max)
SERIAL=build/serial-vm.log; : > "$SERIAL"
args=( -name "Fab OS" -machine q35,accel=$ACCEL -cpu $CPU -smp "$CPUS" -m "$MEM"
  -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file=build/OVMF_VARS.fd
  -drive file="$DISK",format=raw,if=virtio,cache=writeback,discard=unmap
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 -device virtio-net-pci,netdev=n0
  -serial file:"$SERIAL" -monitor unix:build/qemu-monitor.sock,server,nowait -rtc base=utc )
if [ $HEADLESS = 1 ]; then args+=( -display none -device virtio-vga,id=vga0,max_outputs=$HEADS ); elif [ $GL = 1 ]; then args+=( -device virtio-vga-gl,id=vga0,max_outputs=$HEADS -display gtk,gl=on,show-cursor=on ); else args+=( -device virtio-vga,id=vga0,max_outputs=$HEADS -display gtk,show-cursor=on,zoom-to-fit=on ); fi
if [ $AUTOTEST = 1 ]; then args+=( -no-reboot -smbios type=11,value=io.systemd.boot.kernel-cmdline-extra=fabos.autopoweroff\ console=ttyS0 ); fi
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1100))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+800))M}
echo "== booting $DISK  mem=${MEM}M cpus=$CPUS accel=$ACCEL headless=$HEADLESS autotest=$AUTOTEST  (serial -> $SERIAL)"
if [ "$TIMEOUT" != 0 ]; then tools/rg --profile vm -- timeout --foreground -k 10 "$TIMEOUT" qemu-system-x86_64 "${args[@]}" || true
else tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}"; fi
echo "== qemu exited; last serial lines:"; tail -n 25 "$SERIAL"
