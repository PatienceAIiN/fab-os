#!/usr/bin/env bash
# Boot the Fab OS ISO in QEMU/KVM with UEFI (OVMF), optionally with a blank target disk for an install test.
# Usage: scripts/boot-iso.sh [--headless] [--autotest] [--target-disk] [--mem MB] [--cpus N] [--timeout S]
#        [--install-disk PATH]      attach PATH as a virtio disk (created sparse, 24 GB raw, when missing; *.qcow2 stays qcow2)
#        [--autoinstall luks|plain] fw_cfg entry opt/fabos/autoinstall: the live session runs /usr/lib/fabos/live-autoinstall.sh
#                                   (tests/install-vm.sh drives the installer from the host over the QMP socket)
#        [--qmp PATH]               QMP control socket (send-key, input-send-event, screendump) for tests/install-vm-driver.py
#        [--vars PATH]              OVMF variable store to use AND keep (default: a fresh copy at build/OVMF_VARS_iso.fd);
#                                   the install test hands the same file to the second boot so the firmware boot entry survives
#        [--serial PATH]            serial console log (default build/serial-iso.log)
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; . brand/brand.conf
ISO=${ISO:-build/${DISTRO_ID}-${DISTRO_VERSION}-desktop-amd64.iso}; MEM=2560; CPUS=4; HEADLESS=0; AUTOTEST=0; TIMEOUT=0; TARGET=0
IDISK=""; AUTOINSTALL=""; QMP=""; VARS=""; SERIAL=build/serial-iso.log
while [ $# -gt 0 ]; do case $1 in
  --headless) HEADLESS=1;; --autotest) AUTOTEST=1;; --target-disk) TARGET=1;; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;; --timeout) TIMEOUT=$2; shift;;
  --install-disk) IDISK=$2; shift;; --autoinstall) AUTOINSTALL=$2; shift;; --qmp) QMP=$2; shift;; --vars) VARS=$2; shift;; --serial) SERIAL=$2; shift;;
  *) echo "unknown $1"; exit 2;; esac; shift; done
[ -f "$ISO" ] || { echo "no $ISO — run scripts/build-iso.sh"; exit 1; }
case "$AUTOINSTALL" in ""|luks|plain) ;; *) echo "--autoinstall takes luks or plain"; exit 2;; esac
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1); VARS_SRC=$(ls /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd 2>/dev/null | head -1)
if [ -z "$VARS" ]; then VARS=build/OVMF_VARS_iso.fd; cp "$VARS_SRC" "$VARS"; elif [ ! -f "$VARS" ]; then cp "$VARS_SRC" "$VARS"; fi
: > "$SERIAL"
args=( -name "Fab OS live" -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$MEM" -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$VARS"
  -drive file="$ISO",media=cdrom,if=none,id=cd -device ahci,id=ahci -device ide-cd,drive=cd,bus=ahci.0,bootindex=1
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci -netdev user,id=n0 -device virtio-net-pci,netdev=n0 -serial file:"$SERIAL" -monitor none -rtc base=utc )
if [ -n "$QMP" ]; then rm -f "$QMP"; args+=( -qmp unix:"$QMP",server,nowait ); fi
if [ $TARGET = 1 ]; then [ -f build/install-target.qcow2 ] || qemu-img create -f qcow2 build/install-target.qcow2 24G >/dev/null; args+=( -drive file=build/install-target.qcow2,if=virtio,format=qcow2 ); fi
if [ -n "$IDISK" ]; then fmt=raw; case "$IDISK" in *.qcow2) fmt=qcow2;; esac
  if [ ! -f "$IDISK" ]; then if [ $fmt = qcow2 ]; then qemu-img create -f qcow2 "$IDISK" 24G >/dev/null; else truncate -s 24G "$IDISK"; fi; fi
  args+=( -drive file="$IDISK",if=virtio,format=$fmt,cache=writeback,discard=unmap ); fi
if [ $HEADLESS = 1 ]; then args+=( -display none -device virtio-vga ); else args+=( -device virtio-vga-gl -display gtk,gl=on,show-cursor=on ); fi
if [ $AUTOTEST = 1 ] || [ -n "$AUTOINSTALL" ]; then args+=( -no-reboot ); fi
if [ $AUTOTEST = 1 ]; then args+=( -fw_cfg name=opt/fabos/autotest,string=1 ); fi
if [ -n "$AUTOINSTALL" ]; then args+=( -fw_cfg name=opt/fabos/autoinstall,string="$AUTOINSTALL" ); fi
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1200))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+900))M}
echo "== booting $ISO mem=${MEM}M cpus=$CPUS headless=$HEADLESS autotest=$AUTOTEST target=$TARGET install-disk=${IDISK:-none} autoinstall=${AUTOINSTALL:-none} (serial -> $SERIAL)"
if [ "$TIMEOUT" != 0 ]; then tools/rg --profile vm -- timeout --foreground -k 10 "$TIMEOUT" qemu-system-x86_64 "${args[@]}" || true; else tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}"; fi
echo "== qemu exited"; sed 's/\x1b\[[0-9;]*m//g' "$SERIAL" | grep -E '^(FABOS_LIVE|PRETTY_NAME|KERNEL|LIVE_USER|SDDM|PLASMA|CALAMARES|INSTALLER_ICON|AGENT|FIRMWARE|MEM_USED|AUTOINSTALL|INSTALL_RESULT)' || tail -n 15 "$SERIAL"
