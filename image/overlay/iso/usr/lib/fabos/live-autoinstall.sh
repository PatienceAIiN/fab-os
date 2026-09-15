#!/bin/bash
# Fab OS live session: automated installer run for tests/install-vm.sh (QEMU only).
# Started by live-selftest.sh when the firmware-config entry opt/fabos/autoinstall exists (value: luks | plain). It launches
# Calamares in the live desktop exactly as the desktop icon does (as root, on the live user's display) and reports over
# the serial console (/dev/ttyS0): session markers, every job start, the result, the target disk layout and the whole
# Calamares session log. The KEYSTROKES come from the host (tests/install-vm-driver.py: QEMU QMP send-key and absolute
# pointer events, guided by OCR of screendumps) - KWin 6.6 implements no virtual-keyboard protocol, so wtype cannot type
# into the session (verified: libkwin.so.6 contains no "virtual_keyboard" interface string, wtype exits 1).
# Never runs on real hardware: /sys/firmware/qemu_fw_cfg exists only under QEMU with that entry.
set -u
VARIANT=${1:-luks}; OUT=/dev/ttyS0; [ -w $OUT ] || OUT=/dev/console
LOG=/root/.cache/calamares/session.log     # Calamares::appLogDir() = QStandardPaths::CacheLocation of the user running it (root)
CALOUT=/var/log/fabos-autoinstall-calamares.log; T0=$(date +%s)
say(){ echo "$*" > $OUT; logger -t fabos-autoinstall "$*" 2>/dev/null || true; }
finish(){ say "AUTOINSTALL_END"; sync; sleep 3; systemctl poweroff; exit 0; }
say "AUTOINSTALL_BEGIN variant=$VARIANT"
# 1. the live desktop of the fabos user must be up; borrow its display environment for Calamares
pid=""; for i in $(seq 1 120); do pid=$(pgrep -u fabos -n plasmashell 2>/dev/null); [ -n "$pid" ] && break; sleep 1; done
[ -n "$pid" ] || { say "INSTALL_RESULT=failed variant=$VARIANT reason=no-plasma-session"; finish; }
envof(){ tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n "s/^$1=//p" | head -1; }
WD=$(envof WAYLAND_DISPLAY); XRD=$(envof XDG_RUNTIME_DIR); DISP=$(envof DISPLAY); XA=$(envof XAUTHORITY); DBUS=$(envof DBUS_SESSION_BUS_ADDRESS)
say "AUTOINSTALL_SESSION wayland=${WD:-none} x11=${DISP:-none} runtime=${XRD:-none}"
say "AUTOINSTALL_DISK $(lsblk -dn -o NAME,SIZE,TYPE /dev/vda 2>/dev/null | tr -s ' ' || echo none)"
say "AUTOINSTALL_SQUASHFS $(ls -la /cdrom/casper/filesystem.squashfs 2>/dev/null || echo missing)"
say "AUTOINSTALL_MEM_MB=$(free -m | awk '/Mem:/{print $2}')"
sleep 5
# 2. launch Calamares (verbose log, like the upstream desktop entry's -D6)
mkdir -p /root/.cache/calamares; rm -f "$LOG"
env -i HOME=/root USER=root LOGNAME=root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=en_US.UTF-8 \
    ${WD:+WAYLAND_DISPLAY=$WD} ${XRD:+XDG_RUNTIME_DIR=$XRD} ${DISP:+DISPLAY=$DISP} ${XA:+XAUTHORITY=$XA} ${DBUS:+DBUS_SESSION_BUS_ADDRESS=$DBUS} \
    QT_QPA_PLATFORM="wayland;xcb" calamares -D6 > "$CALOUT" 2>&1 &
CPID=$!
say "AUTOINSTALL_CALAMARES_PID=$CPID"
for i in $(seq 1 90); do [ -s "$LOG" ] && break; kill -0 $CPID 2>/dev/null || break; sleep 1; done
if ! kill -0 $CPID 2>/dev/null; then
  say "INSTALL_RESULT=failed variant=$VARIANT reason=calamares-exited-early"; say "INSTALL_FAIL_TAIL_BEGIN"; tail -n 40 "$CALOUT" 2>/dev/null > $OUT; say "INSTALL_FAIL_TAIL_END"; finish; fi
sleep 8; say "AUTOINSTALL_UI_READY"
# 3. follow the session log: every job start is echoed (CALAMARES_JOB:), until the finished page reports the completion
#    ("Sending notification of completion: succeeded|failed" from the finished module, notifyOnFinished: true) or the
#    failure dialog is logged by ViewManager::onInstallationFailed ("- message:"), or Calamares exits.
RESULT=timeout; reported=0; DEADLINE=$((T0 + 45*60))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ -s "$LOG" ]; then
    n=$(grep -c -E 'Starting (job|EMERGENCY JOB)' "$LOG" 2>/dev/null || true); n=${n:-0}
    if [ "$n" -gt "$reported" ]; then grep -E 'Starting (job|EMERGENCY JOB)' "$LOG" | tail -n +$((reported+1)) | sed -E 's/^.*Starting (job|EMERGENCY JOB)/CALAMARES_JOB:/' > $OUT; reported=$n; fi
    if grep -q 'completion: succeeded' "$LOG"; then RESULT=ok; break; fi
    if grep -q -E 'completion: failed|^.*- message:' "$LOG"; then RESULT=failed; break; fi
  fi
  kill -0 $CPID 2>/dev/null || { RESULT=crashed; break; }
  sleep 5
done
say "INSTALL_RESULT=$RESULT variant=$VARIANT seconds=$(( $(date +%s) - T0 )) jobs=$reported"
if [ "$RESULT" != ok ]; then say "INSTALL_FAIL_TAIL_BEGIN"; tail -n 40 "$LOG" 2>/dev/null > $OUT; tail -n 20 "$CALOUT" 2>/dev/null | sed 's/^/CALSTDERR| /' > $OUT; say "INSTALL_FAIL_TAIL_END"; fi
# 4. evidence about the target disk (Calamares' umount job has released it) and the full session log for the host to keep
sync; sleep 2
lsblk -o NAME,SIZE,FSTYPE,TYPE,LABEL,MOUNTPOINTS /dev/vda 2>/dev/null | sed 's/^/LSBLK: /' > $OUT
esp=$(lsblk -ln -o NAME,PARTTYPENAME /dev/vda 2>/dev/null | awk '/EFI System/{print $1; exit}')
if [ -n "$esp" ]; then mkdir -p /mnt/fabos-esp; if mount -o ro "/dev/$esp" /mnt/fabos-esp 2>/dev/null; then find /mnt/fabos-esp -maxdepth 3 | sort | sed 's|^/mnt/fabos-esp|ESP:|' > $OUT; umount /mnt/fabos-esp; fi; fi
for p in $(lsblk -ln -o NAME,FSTYPE /dev/vda 2>/dev/null | awk '$2=="crypto_LUKS"{print $1}'); do cryptsetup luksDump "/dev/$p" 2>/dev/null | grep -E '^Version|PBKDF:|Cipher:|Memory:' | tr -s ' \t' ' ' | sed "s|^|LUKS $p: |" > $OUT; done
say "CALAMARES_LOG_BEGIN"; if [ -s "$LOG" ]; then tail -c 3000000 "$LOG" > $OUT; else echo "(no session log at $LOG)" > $OUT; fi; say "CALAMARES_LOG_END"
finish
