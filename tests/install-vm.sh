#!/usr/bin/env bash
# Automated end-to-end installation test of the Fab OS ISO in QEMU/KVM (headless):
#   stage 1  boot the ISO with a blank 24 GB virtio disk + fw_cfg opt/fabos/autoinstall=<variant>; the live session starts
#            Calamares (image/overlay/iso/usr/lib/fabos/live-autoinstall.sh) and this host drives every page over QMP
#            (tests/install-vm-driver.py: screendump -> OCR -> send-key / pointer clicks) until Calamares reaches its
#            finished page (any Config::doNotify line of the finished module in the session log - as root the desktop
#            notification itself is never sent - with no ViewManager::onInstallationFailed line before it); the guest
#            prints INSTALL_RESULT=... finished_page=yes|no, AUTOINSTALL_JOBS started=n total=m, the partition table, the
#            ESP listing, the LUKS header and the whole session log (gzip+base64, sha256) on the serial console and powers off
#   stage 2  boot the INSTALLED disk alone (no ISO, no network) with the same OVMF variable store; for the luks variant the
#            driver types the passphrase at the Plymouth prompt; wait for FABOS_INSTALLED_OK (fabos-firstboot.service
#            ExecStartPost on the installed system), then power the VM down
# Usage: tests/install-vm.sh [luks|plain|both] [--mem MB] [--cpus N] [--keep-disk] [--image localhost/fabos:iso]
#   ISO=path/to/x.iso overrides the ISO (default build/fabos-1.0-desktop-amd64.iso, see scripts/boot-iso.sh)
# Output: build/install-vm-<variant>.log (this log), build/install-vm-<variant>/ (screenshots + OCR text, session.log =
#   the Calamares session log copied out of the guest, guest-evidence.txt = partition table / ESP listing / LUKS header),
#   build/install-vm-<variant>-serial-{1,2}.log (serial consoles). PASS/FAIL per stage; exit 1 on any FAIL.
# Needs: qemu-system-x86_64 + /dev/kvm, OVMF, podman with the ISO image (OCR runs tesseract inside it; the host needs no
#   tesseract), python3 (Pillow optional: PNG screenshots + 2x upscaling for OCR), >= 14 GB free under build/ per variant
#   (unpackfs writes the 8.8 GB rootfs + a 512 MiB swap file + /boot into the sparse disk image).
#   tesseract is not a Fab OS addition: Ubuntu's tesseract-ocr 5.5.0 + tesseract-ocr-eng (Apache-2.0) are in the image as
#   dependencies of kde-spectacle (its screenshot OCR). This test only borrows them; nothing is downloaded.
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; . brand/brand.conf
VARIANTS="luks"; MEM=2560; CPUS=2; KEEP=0; IMAGE=localhost/fabos:iso
while [ $# -gt 0 ]; do case "$1" in luks|plain) VARIANTS=$1;; both) VARIANTS="luks plain";; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;; --keep-disk) KEEP=1;; --image) IMAGE=$2; shift;; *) echo "unknown $1"; exit 2;; esac; shift; done
ISO=${ISO:-build/${DISTRO_ID}-${DISTRO_VERSION}-desktop-amd64.iso}; export ISO
mkdir -p build
verdict(){ if [ "$1" = PASS ]; then echo "PASS  $2"; else echo "FAIL  $2"; fi; }   # (counted from the logs at the end: the loop body runs in a tee pipeline)
wait_qemu(){ # wait_qemu PID SECONDS PATTERN — wait for the boot script to end; afterwards stop the qemu whose command line matches PATTERN
  local pid=$1 t=$2 pat=$3; while kill -0 "$pid" 2>/dev/null && [ $t -gt 0 ]; do sleep 2; t=$((t-2)); done
  if kill -0 "$pid" 2>/dev/null; then echo "  (qemu still running after the driver finished; stopping it)"; pkill -TERM -f -- "$pat" 2>/dev/null; sleep 8; pkill -KILL -f -- "$pat" 2>/dev/null; fi
  wait "$pid" 2>/dev/null || true; }

# ---- preflight (each a PASS/FAIL line; a failed preflight stops before any VM starts)
pre_fail=0
p(){ if eval "$2" >/dev/null 2>&1; then echo "PASS  preflight: $1"; else echo "FAIL  preflight: $1"; pre_fail=1; fi; }
p "ISO present ($ISO)" "test -f '$ISO'"
p "KVM available (/dev/kvm)" "test -e /dev/kvm"
p "qemu-system-x86_64 + OVMF present" "command -v qemu-system-x86_64 && ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | grep -q ."
p "podman image $IMAGE present (OCR: tesseract runs inside it)" "podman image exists $IMAGE"
p "tesseract usable inside the image" "podman run --rm --network none $IMAGE tesseract --version"
p "python3 present" "command -v python3"
free_gb=$(df -BG --output=avail build | tail -1 | tr -dc 0-9); need=14
p "free space under build/: ${free_gb} GB (need >= ${need} GB per variant: unpackfs writes the 8.8 GB rootfs into the sparse disk)" "[ ${free_gb:-0} -ge $need ]"
if [ $pre_fail = 1 ]; then echo "### install-vm: preflight failed (free space hint: old images under build/ such as *-old.img can be deleted)"; exit 1; fi

for V in $VARIANTS; do
  LOG=build/install-vm-$V.log; OUT=build/install-vm-$V; DISK=build/install-target-$V.img; VARS=build/OVMF_VARS_install-$V.fd
  rm -rf "$OUT" "$DISK" "$VARS" build/install-vm-$V.qmp build/install-vm-$V-boot.qmp; mkdir -p "$OUT"; : > "$LOG"
  {
  echo "### install-vm variant=$V — $(date -u +%FT%TZ) — iso=$ISO mem=${MEM}M cpus=$CPUS image=$IMAGE"
  T1=$(date +%s)
  # ---- stage 1: live ISO + Calamares
  scripts/boot-iso.sh --headless --autoinstall "$V" --install-disk "$DISK" --qmp build/install-vm-$V.qmp --vars "$VARS" --serial build/install-vm-$V-serial-1.log --mem "$MEM" --cpus "$CPUS" --timeout 3300 > "$OUT/boot-iso.out" 2>&1 &
  QPID=$!
  python3 tests/install-vm-driver.py install --qmp build/install-vm-$V.qmp --serial build/install-vm-$V-serial-1.log --variant "$V" --outdir "$OUT" --image "$IMAGE"; rc1=$?
  wait_qemu $QPID 120 "qmp unix:build/install-vm-$V.qmp"
  T2=$(date +%s); inst_s=$(sed -n 's/.*INSTALL_RESULT=[a-z]* variant=[a-z]* seconds=\([0-9]*\).*/\1/p' build/install-vm-$V-serial-1.log | tail -1)
  grep -q 'FABOS_LIVE_OK' build/install-vm-$V-serial-1.log && v1=PASS || v1=FAIL; verdict $v1 "install-$V: live ISO booted (FABOS_LIVE_OK) and the autoinstall helper started ($(grep -c '^AUTOINSTALL' build/install-vm-$V-serial-1.log) AUTOINSTALL lines)"
  if grep -q 'INSTALL_RESULT=ok' build/install-vm-$V-serial-1.log && [ $rc1 = 0 ]; then v2=PASS; else v2=FAIL; fi
  verdict $v2 "install-$V: Calamares finished the whole job sequence (INSTALL_RESULT=ok; guest time ${inst_s:-?} s, stage wall $((T2-T1)) s, $(grep -c '^CALAMARES_JOB:' build/install-vm-$V-serial-1.log) jobs)"
  grep -q 'INSTALL_RESULT=ok .*finished_page=yes' build/install-vm-$V-serial-1.log && v2b=PASS || v2b=FAIL
  verdict $v2b "install-$V: Calamares reached its finished page (finished module's doNotify line: $(grep -m1 '^AUTOINSTALL_FINISHED_LINE' build/install-vm-$V-serial-1.log | cut -c27-140 | tr -s ' ' || echo none))"
  jobs_line=$(grep -m1 '^AUTOINSTALL_JOBS' build/install-vm-$V-serial-1.log); js=$(sed -n 's/.*started=\([0-9]*\).*/\1/p' <<<"$jobs_line"); jt=$(sed -n 's/.*total=\([0-9]*\).*/\1/p' <<<"$jobs_line")
  if [ -n "$jt" ] && [ "$jt" -gt 0 ] && [ "$js" = "$jt" ]; then v2c=PASS; else v2c=FAIL; fi
  verdict $v2c "install-$V: every job of the sequence started (${jobs_line:-no AUTOINSTALL_JOBS line})"
  if [ -s "$OUT/session.log" ] && grep -q '^SESSION_LOG_DECODE=ok' "$OUT/guest-evidence.txt" 2>/dev/null; then verdict PASS "install-$V: Calamares session log received intact ($OUT/session.log, $(wc -l < "$OUT/session.log") lines; $(grep -m1 '^SESSION_LOG_DECODE' "$OUT/guest-evidence.txt"))"
  else verdict FAIL "install-$V: Calamares session log not received intact from the guest ($(grep -m1 '^SESSION_LOG_DECODE' "$OUT/guest-evidence.txt" 2>/dev/null || echo 'no decode record'))"; fi
  if [ "$V" = luks ]; then grep -q '^LUKS .*Version:.*2' build/install-vm-$V-serial-1.log && v3=PASS || v3=FAIL; verdict $v3 "install-$V: target disk shows a LUKS2 container (luksDump on the root partition)"; fi
  grep -q '^ESP:/EFI/ubuntu/grubx64.efi' build/install-vm-$V-serial-1.log && grep -q '^ESP:/EFI/boot/bootx64.efi' build/install-vm-$V-serial-1.log && v4=PASS || v4=FAIL
  verdict $v4 "install-$V: EFI system partition holds EFI/ubuntu/grubx64.efi + EFI/boot/bootx64.efi (shim fallback)"
  # ---- stage 2: boot the installed disk alone
  if [ $v2 = PASS ]; then
    T3=$(date +%s)
    scripts/boot-vm.sh --headless --install-disk "$DISK" --vars "$VARS" --qmp build/install-vm-$V-boot.qmp --monitor build/install-vm-$V.hmp --serial build/install-vm-$V-serial-2.log --no-net --no-audio --mem 2048 --cpus "$CPUS" --timeout 1000 > "$OUT/boot-vm.out" 2>&1 &
    QPID=$!
    python3 tests/install-vm-driver.py boot --qmp build/install-vm-$V-boot.qmp --serial build/install-vm-$V-serial-2.log --variant "$V" --outdir "$OUT" --image "$IMAGE"; rc2=$?
    wait_qemu $QPID 120 "qmp unix:build/install-vm-$V-boot.qmp"
    T4=$(date +%s)
    if grep -q FABOS_INSTALLED_OK build/install-vm-$V-serial-2.log && [ $rc2 = 0 ]; then v5=PASS; else v5=FAIL; fi
    verdict $v5 "install-$V: installed disk boots on its own to FABOS_INSTALLED_OK ($((T4-T3)) s incl. the 180 s offline first-boot wait$( [ "$V" = luks ] && echo '; LUKS passphrase typed at the Plymouth prompt' ))"
  else
    verdict FAIL "install-$V: installed disk boot skipped (installation did not finish)"
  fi
  [ $KEEP = 1 ] || rm -f "$DISK"
  echo "### install-vm variant=$V done — evidence: $OUT/ (screenshots, OCR text, session.log, guest-evidence.txt), serial: build/install-vm-$V-serial-{1,2}.log"
  } 2>&1 | tee -a "$LOG"
done
total_fail=0; for V in $VARIANTS; do total_fail=$((total_fail + $(grep -c '^FAIL  ' "build/install-vm-$V.log"))); done
echo "### install-vm: $total_fail FAIL line(s) across: $VARIANTS"; [ $total_fail = 0 ]
