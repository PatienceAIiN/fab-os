#!/usr/bin/env bash
# Fab OS rebrand sweep, run against a REAL booted VM over SSH: lists every product string the logged-in user can still
# see that names KDE / Plasma / Kubuntu / Ubuntu / a K-prefixed or renamed upstream app (tests/rebrand-sweep/inner.py
# enumerates launcher + hidden desktop entries, notifyrc app names, login-screen sessions, System Settings module
# metadata, background services, search plugins, widgets, KWin plugins, themes, "Get New…" titles, identity files and
# the user's own ~/.config pins), then applies tests/rebrand-sweep/allowlist.txt (legally required credits and
# third-party apps we do not rename, one reason each) and FAILS if anything else remains.
#
#   tests/rebrand-sweep-vm.sh [--boot] [--inject] [--relogin] [--frames] [--keep]
#     (no flag)  a VM is already up on ssh 127.0.0.1:2222 (scripts/boot-vm.sh); sweep it as it is
#     --boot     boot a DISPOSABLE qcow2 overlay of build/fabos-vm.img headless (QMP at /tmp/r8-sweep.qmp) and delete it at
#                the end. Take the VM lock first: flock -w 5400 /tmp/fabos-vm.lock -c 'tests/rebrand-sweep-vm.sh --boot --inject --frames'
#     --inject   install THIS tree's branding files into the guest exactly where the packages put them (fabos-branding:
#                plasma-workspace/env scripts, rebrand-overrides + overrides.d rules, apt hook; fabos-desktop: /etc/xdg
#                files) with @VARS@ rendered from brand/brand.conf, run the generator, pin a Breeze splash / global theme
#                in the user's config the way a 1.0-5/1.0-6 home directory can carry them (implies --relogin). The sweep
#                BEFORE the injection is kept as build/rebrand-sweep/sweep-before.tsv for the before/after count. Not
#                needed when the tree's .debs are already installed in the guest (tests/hardware-vm.sh --debs).
#     --relogin  pin the Breeze splash + global theme in the user's config, restart the login manager so the session
#                starts again through the (new) env scripts (autologin in the vm profile) and check the migration stamp.
#     --frames   (with --relogin; needs the QMP socket: --boot, or SWEEP_QMP=/path for a VM booted elsewhere with --qmp)
#                screendump the display every second from the restart of the login manager until the desktop is up
#                (frames/), OCR every frame (tests/rebrand-sweep/ocr-frames.sh: tesseract inside localhost/fabos:iso) and
#                FAIL if a frame reads "KDE" or "Plasma" — the proof that neither the Breeze start-up splash nor a
#                KDE-named session nor SDDM's embedded fallback theme shows up between the greeter and the desktop.
#     --keep     leave the VM running (default with --boot: power off + delete the overlay)
# Output: build/rebrand-sweep/{sweep-before.tsv,sweep.tsv,remaining.tsv,frames/,frames-ocr.txt,summary.txt}; exit 0 only
# when remaining.tsv is empty (and, with --frames, no frame shows KDE/Plasma text).
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
BOOT=0; INJECT=0; RELOGIN=0; FRAMES=0; KEEP=0
while [ $# -gt 0 ]; do case "$1" in --boot) BOOT=1;; --inject) INJECT=1; RELOGIN=1;; --relogin) RELOGIN=1;; --frames) FRAMES=1;; --keep) KEEP=1;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222 -r"
OUT=build/rebrand-sweep; mkdir -p "$OUT/frames"; : > "$OUT/summary.txt"
vm() { $SSH "$@"; }
vsudo() { $SSH "echo fabos | sudo -S -p '' bash -c $(printf %q "$1") 2>&1"; }
usr() { $SSH "XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus bash -c $(printf %q "$1") 2>&1"; }
say() { echo "$*" | tee -a "$OUT/summary.txt"; }
fail=0
verdict() { if [ "$1" = PASS ]; then say "PASS  $2"; else fail=1; say "FAIL  $2"; fi; }

OVL=/tmp/r8-sweep.qcow2; QMP=${SWEEP_QMP:-/tmp/r8-sweep.qmp}; VARS=/tmp/r8-sweep-vars.fd; MON=/tmp/r8-sweep.mon
cleanup() {
  if [ $BOOT = 1 ] && [ $KEEP = 0 ]; then
    vsudo "systemctl poweroff" >/dev/null 2>&1; for i in $(seq 1 20); do pgrep -f "qemu-system-x86_64.*$OVL" >/dev/null || break; sleep 2; done
    pkill -f "qemu-system-x86_64.*$OVL" 2>/dev/null; rm -f "$OVL" "$QMP" "$VARS" "$MON"; say "VM powered off, overlay removed"
  fi
}
trap cleanup EXIT
if [ $BOOT = 1 ]; then
  pgrep -f qemu-system-x86_64 >/dev/null && { echo "a VM is already running; stop it first (one VM at a time)"; exit 1; }
  rm -f "$OVL" "$QMP" "$VARS"; qemu-img create -f qcow2 -b "$HERE/build/fabos-vm.img" -F raw "$OVL" >/dev/null; cp build/OVMF_VARS.fd "$VARS"
  (scripts/boot-vm.sh --headless --mem 2048 --cpus 2 --disk "$OVL" --qmp "$QMP" --vars "$VARS" --serial "$OUT/serial.log" --monitor "$MON" > "$OUT/boot-vm.out" 2>&1 &)
fi
for i in $(seq 1 140); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM"; exit 1; }
for i in $(seq 1 60); do vm "pgrep -x plasmashell >/dev/null" 2>/dev/null && break; sleep 3; done
say "### Fab OS rebrand sweep — $(date -u +%FT%TZ) — guest: $(vm 'dpkg-query -W -f "\${Package} \${Version}, " fabos-branding fabos-desktop 2>/dev/null')"

$SCP tests/rebrand-sweep/inner.py fabos@127.0.0.1:/tmp/inner.py >/dev/null
sweep() {   # the enumerator must run to completion: a crash (Traceback) is a FAIL, never a vacuous "0 hits"
  usr "python3 /tmp/inner.py 2>/tmp/inner.err" | grep -v '^#' > "$1"; err=$(usr "cat /tmp/inner.err"); echo "$err" | sed 's/^/    /'
  echo "$err" | grep -q '# hits=' && ! echo "$err" | grep -q Traceback && verdict PASS "sweep enumerator ran to completion ($(echo "$err" | sed -n 's/.*# hits=//p') hits)" || verdict FAIL "sweep enumerator did not finish: $(echo "$err" | tail -1)"
}
apply_allowlist() { # apply_allowlist <sweep.tsv> <remaining.tsv>
  : > "$2"
  while IFS= read -r line; do
    [ -n "$line" ] || continue; ok=0
    while IFS= read -r rule; do
      rule=${rule%%	# *}; rule=${rule%%  #*}
      case "$rule" in ''|'#'*) continue;; esac
      if printf '%s\n' "$line" | grep -qE -- "$rule"; then ok=1; break; fi
    done < tests/rebrand-sweep/allowlist.txt
    [ $ok = 1 ] || printf '%s\n' "$line" >> "$2"
  done < "$1"
}

# ---------- the sweep as the machine is now (the BEFORE of an --inject run)
sweep "$OUT/sweep-before.tsv"; apply_allowlist "$OUT/sweep-before.tsv" "$OUT/remaining-before.tsv"
say "before: $(grep -c . "$OUT/sweep-before.tsv") visible KDE/Plasma/K-named strings, $(grep -c . "$OUT/remaining-before.tsv") not on the allowlist"
cut -f1 "$OUT/remaining-before.tsv" | sort | uniq -c | sed 's/^/    /' | tee -a "$OUT/summary.txt"

if [ $INJECT = 1 ]; then
  # render this tree's files the way packages/build-debs.sh does and copy them where the packages install them
  STAGE=$(mktemp -d); mkdir -p "$STAGE/env" "$STAGE/overrides.d" "$STAGE/xdg"
  cp packages/fabos-branding/etc/xdg/plasma-workspace/env/*.sh "$STAGE/env/"
  cp packages/fabos-branding/usr/lib/fabos/rebrand-overrides "$STAGE/"
  cp packages/fabos-branding/usr/lib/fabos/overrides.d/*.rules "$STAGE/overrides.d/"
  cp packages/fabos-hardware/usr/lib/fabos/overrides.d/*.rules "$STAGE/overrides.d/" 2>/dev/null
  cp packages/fabos-branding/etc/apt/apt.conf.d/91fabos-rebrand-overrides "$STAGE/"
  cp packages/fabos-desktop/etc/xdg/ksplashrc packages/fabos-desktop/etc/xdg/kdeglobals packages/fabos-desktop/etc/xdg/kscreenlockerrc "$STAGE/xdg/"
  # shellcheck disable=SC1091
  . brand/brand.conf
  for f in $(find "$STAGE" -type f); do
    for v in $(grep -oE '^[A-Z_]+=' brand/brand.conf | tr -d =); do val=${!v}; sed -i "s|@$v@|${val//|/\\|}|g" "$f"; done
  done
  $SCP "$STAGE" fabos@127.0.0.1:/tmp/r8stage >/dev/null; rm -rf "$STAGE"
  o=$(vsudo "set -e; install -d /etc/xdg/plasma-workspace/env /usr/lib/fabos/overrides.d
    install -m644 /tmp/r8stage/env/*.sh /etc/xdg/plasma-workspace/env/
    install -m755 /tmp/r8stage/rebrand-overrides /usr/lib/fabos/rebrand-overrides
    install -m644 /tmp/r8stage/overrides.d/*.rules /usr/lib/fabos/overrides.d/
    install -m644 /tmp/r8stage/91fabos-rebrand-overrides /etc/apt/apt.conf.d/
    install -m644 /tmp/r8stage/xdg/* /etc/xdg/
    /usr/lib/fabos/rebrand-overrides; ls /usr/share/fabos/applications | wc -l; ls /usr/share/fabos/kf6/searchproviders | wc -l; cat /var/lib/fabos/overrides.list | wc -l")
  verdict "$( [ -n "$o" ] && ! echo "$o" | grep -qiE 'error|No such' && echo PASS || echo FAIL)" "injected this tree's branding files; generator wrote overrides ($(echo "$o" | tr '\n' ' '))"
fi
if [ $RELOGIN = 1 ]; then
  # a home directory as 1.0-5 / 1.0-6 could leave it: Breeze splash + Breeze global theme pinned by the user
  usr "kwriteconfig6 --file ksplashrc --group KSplash --key Theme org.kde.breeze.desktop; kwriteconfig6 --file kdeglobals --group KDE --key LookAndFeelPackage org.kde.breezedark.desktop; rm -f ~/.config/fabos/defaults-migrated-v1; cat ~/.config/ksplashrc" | sed 's/^/    /'
  since=$(vm 'date -u +%FT%TZ')
  vsudo "systemctl restart sddm" >/dev/null
  if [ $FRAMES = 1 ] && [ -S "$QMP" ]; then
    python3 - "$QMP" "$HERE/$OUT/frames" <<'PY'   # QEMU writes the screendump relative to ITS cwd: pass an absolute path
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("ivd", "tests/install-vm-driver.py"); ivd = importlib.util.module_from_spec(spec); spec.loader.exec_module(ivd)
from PIL import Image
q = ivd.QMP(sys.argv[1], wait=30); d = sys.argv[2]
for f in os.listdir(d): os.remove(os.path.join(d, f))
end = time.time() + 75; n = 0
while time.time() < end:
    ppm = os.path.join(d, "f%03d.ppm" % n)
    try:
        q.screendump(ppm); time.sleep(0.25); Image.open(ppm).save(ppm[:-4] + ".png", optimize=True); os.remove(ppm)
    except Exception as e:
        print("frame", n, "failed:", e)
    n += 1; time.sleep(0.7)
print("frames:", n)
PY
  else
    sleep 60
  fi
  for i in $(seq 1 60); do vm "pgrep -x plasmashell >/dev/null" 2>/dev/null && break; sleep 3; done; sleep 8
  o=$(usr "cat ~/.config/fabos/defaults-migrated-v1; kreadconfig6 --file ksplashrc --group KSplash --key Theme; kreadconfig6 --file kdeglobals --group KDE --key LookAndFeelPackage; systemctl --user show-environment | grep ^XDG_DATA_DIRS")
  echo "$o" | sed 's/^/    /' | tee -a "$OUT/summary.txt"
  verdict "$(echo "$o" | grep -q 'migrated: ksplash lookandfeel' && echo "$o" | grep -q '^Theme=in.patienceai.fabos.desktop\|^in.patienceai.fabos.desktop$' && echo PASS || echo FAIL)" "session-start migration rewrote the user's Breeze splash + global-theme pins to Fab OS once (stamp written)"
  verdict "$(echo "$o" | grep -q '^XDG_DATA_DIRS=/usr/share/fabos:' && echo PASS || echo FAIL)" "the new session has /usr/share/fabos first in XDG_DATA_DIRS"
  o=$(usr "journalctl --user --no-pager --since '$since' 2>/dev/null | grep -iE 'ksplash|Splash.qml' | tail -5"); echo "$o" | sed 's/^/    /'
  if [ $FRAMES = 1 ] && [ -S "$QMP" ]; then
    : > "$OUT/frames-ocr.txt"
    tests/rebrand-sweep/ocr-frames.sh "$OUT/frames" > "$OUT/frames-ocr.txt" 2>/dev/null
    bad=$(grep -iE '\bKDE\b|Plasma|Kubuntu' "$OUT/frames-ocr.txt" | cut -f1 | tr '\n' ' ')
    verdict "$( [ -z "$bad" ] && [ -s "$OUT/frames-ocr.txt" ] && echo PASS || echo FAIL)" "no frame from the login manager's restart to the desktop reads KDE / Plasma / Kubuntu (OCR of $(grep -c . "$OUT/frames-ocr.txt") frames)${bad:+ — offending: $bad}"
    fab=$(grep -ciE 'Fab ?OS|Patience' "$OUT/frames-ocr.txt")
    verdict "$( [ "$fab" -gt 0 ] && echo PASS || echo FAIL)" "the Fab OS name is read in $fab of the frames (greeter and/or splash)"
  fi
fi

# ---------- the sweep now
sweep "$OUT/sweep.tsv"; apply_allowlist "$OUT/sweep.tsv" "$OUT/remaining.tsv"
say "after: $(grep -c . "$OUT/sweep.tsv") visible KDE/Plasma/K-named strings, $(grep -c . "$OUT/remaining.tsv") not on the allowlist"
if [ -s "$OUT/remaining.tsv" ]; then
  say "remaining (not on tests/rebrand-sweep/allowlist.txt):"; cut -c1-200 "$OUT/remaining.tsv" | sed 's/^/    /' | tee -a "$OUT/summary.txt"
  verdict FAIL "every visible KDE/Plasma/K-named string is either renamed or on the allowlist with a reason"
else
  verdict PASS "every visible KDE/Plasma/K-named string is either renamed or on the allowlist with a reason ($(grep -c . "$OUT/sweep.tsv") allowlisted credits/components)"
fi
say "### result: $([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
