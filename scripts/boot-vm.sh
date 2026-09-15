#!/usr/bin/env bash
# Boot the FabOS OS disk in QEMU/KVM with UEFI (OVMF), under the resource guard so the host never hangs.
# Usage: scripts/boot-vm.sh [--headless] [--autotest] [--mem MB] [--cpus N] [--timeout S] [--fresh-vars] [--heads N]  (N virtual displays for multi-monitor tests)
#        [--no-audio]  (default: an emulated Intel HDA card with speaker + microphone on the silent "none" backend, so PipeWire
#                       in the guest has a default source and sink and tests/voice-vm.sh can exercise the whole voice pipeline)
#        [--disk PATH | --install-disk PATH]  boot PATH instead of build/fabos-vm.img (raw; *.qcow2 is opened as qcow2) - the
#                       install test boots the disk Calamares just wrote, alone, without the ISO
#        [--vars PATH]    OVMF variable store (default build/OVMF_VARS.fd); the install test reuses the file of the ISO boot so
#                         the firmware boot entry written by grub-install is still there
#        [--serial PATH]  serial log (default build/serial-vm.log)   [--monitor PATH] HMP socket (default build/qemu-monitor.sock)
#        [--qmp PATH]     QMP control socket (send-key / screendump; tests/install-vm-driver.py types the LUKS passphrase with it)
#        [--no-net]       no network device at all (an installed system then finishes fabos-firstboot offline in 3 minutes)
#        [--fw-cfg NAME=VALUE]  extra fw_cfg string entry (repeatable)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
MEM=2048; CPUS=4; HEADLESS=0; AUTOTEST=0; TIMEOUT=0; GL=0; HEADS=1; AUDIO=${VM_AUDIO:-1}; DISK=build/fabos-vm.img
VARS=build/OVMF_VARS.fd; SERIAL=build/serial-vm.log; MON=build/qemu-monitor.sock; QMP=""; NET=1; FWCFG=()
while [ $# -gt 0 ]; do case $1 in
  --headless) HEADLESS=1;; --autotest) AUTOTEST=1;; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;;
  --timeout) TIMEOUT=$2; shift;; --gl) GL=1;; --heads) HEADS=$2; shift;; --fresh-vars) rm -f build/OVMF_VARS.fd;; --disk|--install-disk) DISK=$2; shift;;
  --vars) VARS=$2; shift;; --serial) SERIAL=$2; shift;; --monitor) MON=$2; shift;; --qmp) QMP=$2; shift;; --no-net) NET=0;; --fw-cfg) FWCFG+=( "$2" ); shift;;
  --no-audio) AUDIO=0;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
[ -f "$DISK" ] || { echo "no $DISK — run scripts/make-disk.sh"; exit 1; }
[ -e /dev/kvm ] || echo "WARNING: /dev/kvm missing, falling back to TCG (slow)"
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1)
VARS_SRC=$(ls /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd 2>/dev/null | head -1)
[ -f "$VARS" ] || cp "$VARS_SRC" "$VARS"
ACCEL=$([ -e /dev/kvm ] && echo kvm || echo tcg); CPU=$([ -e /dev/kvm ] && echo host || echo max)
FMT=raw; case "$DISK" in *.qcow2) FMT=qcow2;; esac
: > "$SERIAL"
args=( -name "Fab OS" -machine q35,accel=$ACCEL -cpu $CPU -smp "$CPUS" -m "$MEM"
  -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$VARS"
  -drive file="$DISK",format=$FMT,if=virtio,cache=writeback,discard=unmap
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci
  -serial file:"$SERIAL" -monitor unix:"$MON",server,nowait -rtc base=utc )
if [ $NET = 1 ]; then args+=( -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 -device virtio-net-pci,netdev=n0 ); else args+=( -nic none ); fi
if [ -n "$QMP" ]; then rm -f "$QMP"; args+=( -qmp unix:"$QMP",server,nowait ); fi
for e in "${FWCFG[@]}"; do args+=( -fw_cfg name="${e%%=*}",string="${e#*=}" ); done
if [ $HEADLESS = 1 ]; then args+=( -display none -device virtio-vga,id=vga0,max_outputs=$HEADS ); elif [ $GL = 1 ]; then args+=( -device virtio-vga-gl,id=vga0,max_outputs=$HEADS -display gtk,gl=on,show-cursor=on ); else args+=( -device virtio-vga,id=vga0,max_outputs=$HEADS -display gtk,show-cursor=on,zoom-to-fit=on ); fi
if [ $AUTOTEST = 1 ]; then args+=( -no-reboot -smbios type=11,value=io.systemd.boot.kernel-cmdline-extra=fabos.autopoweroff\ console=ttyS0 ); fi
# Sound: ich9-intel-hda controller + hda-micro codec (speaker + microphone) on the "none" audiodev — the guest sees a real
# capture and playback device (the microphone delivers silence, the speaker discards). Names verified with
# `qemu-system-x86_64 -device help | grep -i hda` and `-audiodev help` (QEMU 10.2).
if [ "$AUDIO" = 1 ]; then args+=( -audiodev none,id=snd0 -device ich9-intel-hda -device hda-micro,audiodev=snd0 ); fi
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1100))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+800))M}
echo "== booting $DISK ($FMT)  mem=${MEM}M cpus=$CPUS accel=$ACCEL headless=$HEADLESS autotest=$AUTOTEST net=$NET vars=$VARS  (serial -> $SERIAL)"
if [ "$TIMEOUT" != 0 ]; then tools/rg --profile vm -- timeout --foreground -k 10 "$TIMEOUT" qemu-system-x86_64 "${args[@]}" || true
else tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}"; fi
echo "== qemu exited; last serial lines:"; tail -n 25 "$SERIAL"
