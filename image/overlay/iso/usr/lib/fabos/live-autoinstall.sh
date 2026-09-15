#!/bin/bash
# Fab OS live session: automated installer run for tests/install-vm.sh (QEMU only).
# Started by live-selftest.sh when the firmware-config entry opt/fabos/autoinstall exists (value: luks | plain). It launches
# Calamares in the live desktop exactly as the desktop icon does (as root, on the live user's display) and reports over
# the serial console (/dev/ttyS0): session markers, every job start, the result, the target disk layout and the whole
# Calamares session log. The KEYSTROKES come from the host (tests/install-vm-driver.py: QEMU QMP send-key and absolute
# pointer events, guided by OCR of screendumps) - KWin 6.6 implements no virtual-keyboard protocol, so wtype cannot type
# into the session (verified: libkwin.so.6 contains no "virtual_keyboard" interface string, wtype exits 1).
# Never runs on real hardware: /sys/firmware/qemu_fw_cfg exists only under QEMU with that entry.
#
# Library mode (tests/calamares-jobs-test.sh): FABOS_AUTOINSTALL_LIB=1 . live-autoinstall.sh  defines the functions below
# (classify_log, dump_log, say) and returns without doing anything; FABOS_AUTOINSTALL_OUT may point the output at a file.
set -u
OUT=${FABOS_AUTOINSTALL_OUT:-/dev/ttyS0}; [ -n "${FABOS_AUTOINSTALL_OUT:-}" ] && : >> "$OUT" 2>/dev/null; [ -w "$OUT" ] || OUT=/dev/console
LOG=/root/.cache/calamares/session.log     # Calamares::appLogDir() = QStandardPaths::CacheLocation of the user running it (root)
CALOUT=/var/log/fabos-autoinstall-calamares.log

# ---- what the session log says (Calamares 3.3.14; every string below exists in the image's binaries - checked by
#      tests/calamares-jobs-test.sh against libcalamaresui.so / the finished module .so, so a Calamares update that
#      rewords them fails the audit instead of silently breaking this test):
#   libcalamares      JobThread::run            'Starting job "..." ( n / m )'  /  'Starting EMERGENCY JOB ...'
#   libcalamaresui    ViewManager::onInstallationFailed   'Installation failed: ...' then '- message: ...'  - logged when a
#                     job fails, BEFORE the failure dialog and before the finished page
#   finished module   Config::doNotify, called from FinishedViewStep::onActivate, i.e. exactly when the FINISHED PAGE is
#                     shown - one of three lines, depending on whether a desktop notification could be sent:
#                       'Sending notification of completion: succeeded|failed'          (org.freedesktop.Notifications reachable)
#                       'Could not get dbus interface for notifications at end of installation.'  (the normal case here:
#                          Calamares runs as root and dbus-daemon rejects uid 0 on the live user's session bus - measured in
#                          the image, the connection fails at once, so nothing blocks)
#                       'Notification not sent; completion: ...'                        (notifyOnFinished: false)
#   Success therefore = the finished page was reached (any doNotify line) with no failure line before it. An earlier
#   version waited for 'completion: succeeded' alone, which can never appear as root, and reported a good install as timeout.
JOB_RX='Starting (job|EMERGENCY JOB)'
FAIL_RX='Installation failed:|- message:'
FINISHED_RX='Sending notification of completion:|Could not get dbus interface for notifications at end of installation|Notification not sent; completion:'

say(){ echo "$*" >> "$OUT"; logger -t fabos-autoinstall "$*" 2>/dev/null || true; }
# classify_log FILE -> sets VERDICT = ok | failed | running, JOBS_STARTED (Starting job lines) and JOBS_TOTAL (the m of
# "( n / m )"). Sets variables instead of printing so it must NOT be called in a $(...) subshell.
classify_log(){
  local f=$1
  JOBS_STARTED=$(grep -c -E "$JOB_RX" "$f" 2>/dev/null); JOBS_STARTED=${JOBS_STARTED:-0}
  JOBS_TOTAL=$(grep -E "$JOB_RX" "$f" 2>/dev/null | grep -o -E '\( *[0-9]+ */ *[0-9]+ *\)' | tail -n 1 | tr -dc '0-9/' | cut -d/ -f2); JOBS_TOTAL=${JOBS_TOTAL:-0}
  if grep -q -E "$FAIL_RX" "$f" 2>/dev/null; then VERDICT=failed
  elif grep -q -E "$FINISHED_RX" "$f" 2>/dev/null; then VERDICT=ok
  else VERDICT=running; fi
}
# dump_log FILE LABEL [CAP] -> FILE gzip-compressed and base64-encoded (76-column lines) between LABEL_BEGIN / LABEL_END.
# The BEGIN line carries the sizes and the sha256 of the compressed bytes, so the host (tests/install-vm-driver.py
# decode_log_block) can prove it received the whole log. If the compressed log exceeds CAP bytes (default 800 kB), only the
# newest part of the file is sent (kept=...); the raw file is also copied into the ESP (see below), so nothing is lost.
dump_log(){
  local f=$1 label=$2 cap=${3:-800000} tmp keep
  if [ ! -s "$f" ]; then say "${label}_BEGIN encoding=none bytes=0"; echo "(no log at $f)" >> "$OUT"; say "${label}_END"; return 0; fi
  tmp=$(mktemp); keep=$(stat -c %s "$f"); gzip -9c "$f" > "$tmp"
  while [ "$(stat -c %s "$tmp")" -gt "$cap" ] && [ "$keep" -gt 100000 ]; do keep=$((keep/2)); tail -c "$keep" "$f" | gzip -9c > "$tmp"; done
  say "${label}_BEGIN encoding=gzip+base64 bytes=$(stat -c %s "$f") kept=$keep gz=$(stat -c %s "$tmp") sha256=$(sha256sum "$tmp" | cut -d' ' -f1)"
  base64 -w 76 "$tmp" >> "$OUT"
  say "${label}_END"; rm -f "$tmp"
}
if [ -n "${FABOS_AUTOINSTALL_LIB:-}" ]; then return 0 2>/dev/null || exit 0; fi

VARIANT=${1:-luks}; T0=$(date +%s)
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
# 2. launch Calamares (verbose log, like the upstream desktop entry's -D6). The live user's session-bus address is passed
#    on so the environment matches a desktop launch as closely as possible; as root the connection is refused at once
#    (see above), which only costs the end-of-install desktop notification.
mkdir -p /root/.cache/calamares; rm -f "$LOG"
env -i HOME=/root USER=root LOGNAME=root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=en_US.UTF-8 \
    ${WD:+WAYLAND_DISPLAY=$WD} ${XRD:+XDG_RUNTIME_DIR=$XRD} ${DISP:+DISPLAY=$DISP} ${XA:+XAUTHORITY=$XA} ${DBUS:+DBUS_SESSION_BUS_ADDRESS=$DBUS} \
    QT_QPA_PLATFORM="wayland;xcb" calamares -D6 > "$CALOUT" 2>&1 &
CPID=$!
say "AUTOINSTALL_CALAMARES_PID=$CPID"
for i in $(seq 1 90); do [ -s "$LOG" ] && break; kill -0 $CPID 2>/dev/null || break; sleep 1; done
if ! kill -0 $CPID 2>/dev/null; then
  say "INSTALL_RESULT=failed variant=$VARIANT reason=calamares-exited-early"; say "INSTALL_FAIL_TAIL_BEGIN"; tail -n 40 "$CALOUT" 2>/dev/null >> "$OUT"; say "INSTALL_FAIL_TAIL_END"; finish; fi
sleep 8; say "AUTOINSTALL_UI_READY"
# 3. follow the session log: every job start is echoed (CALAMARES_JOB:) until the log shows a failure (FAIL_RX) or the
#    finished page (FINISHED_RX), or Calamares exits, or the guest deadline passes.
RESULT=timeout; reported=0; DEADLINE=$((T0 + 45*60)); FINISHED_PAGE=no; JOBS_STARTED=0; JOBS_TOTAL=0; VERDICT=running
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ -s "$LOG" ]; then
    classify_log "$LOG"
    if [ "$JOBS_STARTED" -gt "$reported" ]; then grep -E "$JOB_RX" "$LOG" | tail -n +$((reported+1)) | sed -E "s/^.*$JOB_RX/CALAMARES_JOB:/" >> "$OUT"; reported=$JOBS_STARTED; fi
    case $VERDICT in ok) RESULT=ok; FINISHED_PAGE=yes; break;; failed) RESULT=failed; break;; esac
  fi
  if ! kill -0 $CPID 2>/dev/null; then
    # Calamares is gone: read the log once more. A normal exit after the finished page is a success. If it went away
    # without ever showing the finished page, but after every job had started and no failure was logged, the install
    # itself may well be complete: report ok with finished_page=no so the host's own evidence (ESP listing, stage 2 boot)
    # decides, and its verdict for the finished page fails visibly.
    classify_log "$LOG"
    case $VERDICT in ok) RESULT=ok; FINISHED_PAGE=yes;; failed) RESULT=failed;;
      *) if [ "$JOBS_TOTAL" -gt 0 ] && [ "$JOBS_STARTED" -ge "$JOBS_TOTAL" ]; then RESULT=ok; else RESULT=crashed; fi;; esac
    break
  fi
  sleep 5
done
say "INSTALL_RESULT=$RESULT variant=$VARIANT seconds=$(( $(date +%s) - T0 )) jobs=$JOBS_STARTED finished_page=$FINISHED_PAGE"
say "AUTOINSTALL_JOBS started=$JOBS_STARTED total=$JOBS_TOTAL"
grep -E "$FINISHED_RX" "$LOG" 2>/dev/null | head -n 1 | sed 's/^/AUTOINSTALL_FINISHED_LINE: /' >> "$OUT"
if [ "$RESULT" != ok ]; then say "INSTALL_FAIL_TAIL_BEGIN"; grep -E "$FAIL_RX" "$LOG" 2>/dev/null | head -n 4 >> "$OUT"; tail -n 40 "$LOG" 2>/dev/null >> "$OUT"; tail -n 20 "$CALOUT" 2>/dev/null | sed 's/^/CALSTDERR| /' >> "$OUT"; say "INSTALL_FAIL_TAIL_END"; fi
# 4. evidence about the target disk (Calamares' umount job has released it) and the session log for the host to keep.
#    Kernel messages share ttyS0 with us (console=ttyS0): keep them to emergencies while the encoded log goes out.
sync; sleep 2; dmesg -n 1 2>/dev/null || true
lsblk -o NAME,SIZE,FSTYPE,TYPE,LABEL,MOUNTPOINTS /dev/vda 2>/dev/null | sed 's/^/LSBLK: /' >> "$OUT"
esp=$(lsblk -ln -o NAME,PARTTYPENAME /dev/vda 2>/dev/null | awk '/EFI System/{print $1; exit}')
if [ -n "$esp" ]; then mkdir -p /mnt/fabos-esp
  if mount "/dev/$esp" /mnt/fabos-esp 2>/dev/null; then
    find /mnt/fabos-esp -maxdepth 3 -not -path '/mnt/fabos-esp/fabos-install*' | sort | sed 's|^/mnt/fabos-esp|ESP:|' >> "$OUT"
    # full raw logs onto the installed disk's ESP (readable from the host with --keep-disk, or from the installed system)
    if mkdir -p /mnt/fabos-esp/fabos-install 2>/dev/null && cp "$LOG" /mnt/fabos-esp/fabos-install/session.log 2>/dev/null; then
      cp "$CALOUT" /mnt/fabos-esp/fabos-install/calamares-stderr.log 2>/dev/null; sync
      say "AUTOINSTALL_LOG_COPY esp=$esp path=/fabos-install/session.log bytes=$(stat -c %s /mnt/fabos-esp/fabos-install/session.log)"; fi
    umount /mnt/fabos-esp; fi; fi
for p in $(lsblk -ln -o NAME,FSTYPE /dev/vda 2>/dev/null | awk '$2=="crypto_LUKS"{print $1}'); do cryptsetup luksDump "/dev/$p" 2>/dev/null | grep -E '^Version|PBKDF:|Cipher:|Memory:' | tr -s ' \t' ' ' | sed "s|^|LUKS $p: |" >> "$OUT"; done
dump_log "$LOG" CALAMARES_LOG
finish
