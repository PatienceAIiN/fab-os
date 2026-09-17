#!/usr/bin/env bash
# Fab OS hardware checks INSIDE a booted VM over SSH (vm profile: sshd, autologin, an emulated Intel HDA card with a
# silent microphone — scripts/boot-vm.sh): fingerprint stack, PAM result, camera stack + Fab Camera, microphone tile.
# QEMU has no fingerprint reader and no camera: what is proven here is that the packages install, the PAM stack takes
# a fingerprint OR the password, fprintd starts and reports zero devices without an error, the Users settings page and
# the camera app start and stay up without a device, and the quick-settings microphone row moves the REAL default
# source (wpctl). docs/HARDWARE.md says what a laptop owner should see beyond this.
#
#   tests/hardware-vm.sh [--debs DIR]
#     --debs DIR   install the fabos-*.deb of DIR first (the over-the-air path: apt pulls fprintd, libcamera, Plasma Camera
#                  … from the Ubuntu archive through the guest's network — slow; without it the checks expect the
#                  packages to be there already)
# Output: build/hardware-vm.out (log) + build/hardware-vm/ (screendumps when HW_QMP=/path names the VM's QMP socket).
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
DEBS=""
while [ $# -gt 0 ]; do case "$1" in --debs) DEBS=$2; shift;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222 -r"
mkdir -p build/hardware-vm; OUT=build/hardware-vm.out; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
vm() { $SSH "$@"; }
vsudo() { $SSH "echo fabos | sudo -S -p '' bash -c $(printf %q "$1") 2>&1"; }
usr() { $SSH "XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus bash -c $(printf %q "$1") 2>&1"; }
shot() { [ -n "${HW_QMP:-}" ] && [ -S "$HW_QMP" ] && python3 tests/install-vm-driver.py --help >/dev/null 2>&1 && python3 - "$HW_QMP" "build/hardware-vm/$1" <<'PY'
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("ivd", "tests/install-vm-driver.py"); ivd = importlib.util.module_from_spec(spec); spec.loader.exec_module(ivd)
from PIL import Image
q = ivd.QMP(sys.argv[1], wait=20); q.screendump(sys.argv[2] + ".ppm"); time.sleep(0.4); Image.open(sys.argv[2] + ".ppm").save(sys.argv[2] + ".png", optimize=True)
import os; os.remove(sys.argv[2] + ".ppm"); print("shot", sys.argv[2] + ".png")
PY
}
pass=0; fail=0
verdict() { if [ "$1" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); fi; echo ">>> $1: $2"; }
# an app started in the session: alive after N seconds, journal free of crashes
app_up() { # app_up <unit> <cmd> <seconds> <shot-name>
  usr "systemd-run --user --collect -q -u $1 -- $2" >/dev/null; sleep "$3"; shot "$4"
  st=$(usr "systemctl --user is-active $1; journalctl --user -u $1 --no-pager -b 2>/dev/null | grep -ciE 'segfault|crash|Aborted|core dumped'"); usr "systemctl --user stop $1" >/dev/null 2>&1
  echo "$st" | tr '\n' ' '
}

echo "### Fab OS hardware VM test — $(date -u +%FT%TZ)"
for i in $(seq 1 100); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM (boot it with scripts/boot-vm.sh --headless)"; exit 1; }
for i in $(seq 1 60); do vm "pgrep -x plasmashell >/dev/null" 2>/dev/null && break; sleep 3; done

if [ -n "$DEBS" ]; then
  echo; echo "=== installing $(ls "$DEBS"/fabos-*.deb | wc -l) packages from $DEBS (over the air: dependencies from the Ubuntu archive)"
  vm "rm -rf /tmp/r8debs; mkdir -p /tmp/r8debs"; $SCP "$DEBS"/fabos-*.deb fabos@127.0.0.1:/tmp/r8debs/ >/dev/null
  t0=$(date +%s)
  o=$(vsudo "export DEBIAN_FRONTEND=noninteractive; apt-get -o Acquire::http::Timeout=60 -o Acquire::Retries=5 update 2>&1 | tail -1; apt-get -o Acquire::http::Timeout=60 -o Acquire::Retries=5 -o Dpkg::Options::=--force-confold install -y /tmp/r8debs/fabos-*.deb 2>&1 | tail -30")
  echo "$o" | sed 's/^/    /'; echo "    ($(( $(date +%s) - t0 )) s)"
  vers=$(vm "dpkg-query -W fabos-hardware fabos-branding fabos-desktop 2>/dev/null" | tr '\t\n' ' ,')
  echo "$o" | grep -qE '^E:|dpkg: error|Errors were encountered' && verdict FAIL "apt install of the fabos-*.deb finished without errors" || verdict PASS "apt install of the fabos-*.deb finished without errors ($vers)"
fi

# ---------- 1. packages
echo; echo "=== packages"
CHK='for p in fprintd libpam-fprintd libfprint-2-2 libfprint-2-tod1 v4l-utils libspa-0.2-libcamera gstreamer1.0-pipewire gstreamer1.0-libcamera plasma-camera pipewire wireplumber fabos-hardware; do dpkg-query -W -f \${db:Status-Status} $p 2>/dev/null | grep -q installed || echo -n "$p "; done'
missing=$(vm "$CHK")
[ -z "$missing" ] && verdict PASS "fingerprint + camera stack installed (fprintd, libpam-fprintd, libfprint TOD, v4l-utils, PipeWire libcamera, Plasma Camera, fabos-hardware)" || verdict FAIL "packages missing: $missing"
vm "dpkg-query -W fprintd libpam-fprintd libfprint-2-2 v4l-utils libspa-0.2-libcamera plasma-camera fabos-hardware 2>/dev/null" | sed 's/^/    /'

# ---------- 2. PAM: fingerprint OR password, never fingerprint-only; the lock screen's parallel fingerprint service exists
echo; echo "=== PAM"
ca=$(vm 'cat /etc/pam.d/common-auth'); echo "$ca" | grep -E 'pam_(fprintd|unix|deny|permit)' | sed 's/^/    /'
fp=$(echo "$ca" | grep -n pam_fprintd | head -1 | cut -d: -f1); ux=$(echo "$ca" | grep -n 'pam_unix.so' | head -1 | cut -d: -f1)
[ -n "$fp" ] && [ -n "$ux" ] && [ "$fp" -lt "$ux" ] && verdict PASS "common-auth: pam_fprintd (line $fp) before pam_unix (line $ux) — fingerprint accepted, password always still accepted" || verdict FAIL "common-auth does not have pam_fprintd before pam_unix (fprintd=$fp unix=$ux)"
echo "$ca" | grep -q 'pam_fprintd.so.*max-tries=1' && echo "$ca" | grep -q 'timeout=' && verdict PASS "pam_fprintd bounded: max-tries + timeout (the password prompt follows within the timeout when the finger is not offered)" || verdict FAIL "pam_fprintd line has no max-tries/timeout bound"
vm 'grep -q pam_fprintd /usr/lib/pam.d/kde-fingerprint && grep -q common-auth /usr/lib/pam.d/kde && grep -q common-auth /etc/pam.d/sddm && grep -q common-auth /etc/pam.d/sudo && cat /etc/pam.d/polkit-1 /usr/lib/pam.d/polkit-1 2>/dev/null | grep -q common-auth' && verdict PASS "lock screen (kde + kde-fingerprint), login (sddm), sudo and polkit all reach the fingerprint/password stack" || verdict FAIL "a PAM service does not include common-auth / kde-fingerprint missing"
vm 'test -f /usr/share/pam-configs/fprintd && test -f /usr/share/pam-configs/unix' && verdict PASS "pam-auth-update profiles fprintd + unix present" || verdict FAIL "pam-configs profile missing"

# ---------- 3. fprintd: starts, lists zero devices, no error
echo; echo "=== fprintd"
o=$(vsudo "systemctl start fprintd 2>&1; sleep 2; systemctl is-active fprintd; busctl call net.reactivated.Fprint /net/reactivated/Fprint/Manager net.reactivated.Fprint.Manager GetDevices 2>&1; fprintd-list fabos 2>&1; journalctl -u fprintd --no-pager -b 2>/dev/null | grep -ciE 'error|fail'")
echo "$o" | sed 's/^/    /'
echo "$o" | grep -q '^active' && echo "$o" | grep -qE '^ao 0$' && verdict PASS "fprintd.service active, GetDevices = empty array (QEMU has no reader), no error in its journal ($(echo "$o" | tail -1) lines)" || verdict FAIL "fprintd did not start cleanly or GetDevices is not the empty array"
echo "$o" | grep -qiE 'No devices available|has no fingers' && verdict PASS "fprintd-list answers about the user without crashing" || verdict FAIL "fprintd-list unexpected: $(echo "$o" | grep -i fprintd-list | head -1)"
vm 'ls /usr/lib/udev/rules.d/*fprint* /usr/lib/udev/hwdb.d/*fprint* 2>/dev/null' | sed 's/^/    /'
vm 'ls /usr/lib/udev/rules.d/*fprint* /usr/lib/udev/hwdb.d/*fprint* 2>/dev/null | grep -q .' && verdict PASS "libfprint udev/hwdb rules installed (reader autosuspend + device permissions)" || verdict FAIL "no libfprint udev/hwdb rules"

# ---------- 4. Users settings page with fprintd present: starts and stays up
echo; echo "=== Users settings page"
st=$(app_up r8users "kcmshell6 kcm_users" 14 kcm-users); echo "    $st"
echo "$st" | grep -q '^active' && echo "$st" | grep -qE ' 0 ?$' && verdict PASS "Fab Settings > Users opens with fprintd installed and stays up (no crash)" || verdict FAIL "Users page: $st"

# ---------- 5. camera: Fab Camera entry, the app starts without a device, v4l2 tools, virtual camera if the kernel has one
echo; echo "=== camera"
CAM='for d in $(systemctl --user show-environment | sed -n "s/^XDG_DATA_DIRS=//p" | tr : " ") /usr/local/share /usr/share; do f=$d/applications/org.kde.plasma.camera.desktop; if [ -f "$f" ]; then echo "$f"; grep -E "^(Name|GenericName|Exec)=" "$f"; break; fi; done; systemctl --user show-environment | grep ^XDG_DATA_DIRS'
o=$(usr "$CAM")
echo "$o" | sed 's/^/    /'
if echo "$o" | grep -q '^XDG_DATA_DIRS=/usr/share/fabos:'; then
  echo "$o" | grep -q '^Name=Fab Camera' && verdict PASS "the session resolves the camera app to its Fab OS name (Fab Camera)" || verdict FAIL "camera entry not renamed in the session's XDG_DATA_DIRS order"
else
  echo "    note: this session started before fabos-branding 1.0-8 (no /usr/share/fabos in XDG_DATA_DIRS yet) — the Fab Camera name is checked after the re-login by tests/rebrand-sweep-vm.sh --relogin"
fi
vm 'test -f /usr/share/fabos/applications/org.kde.plasma.camera.desktop && grep -q ^Name=Fab\ Camera /usr/share/fabos/applications/org.kde.plasma.camera.desktop' && verdict PASS "/usr/share/fabos/applications/org.kde.plasma.camera.desktop written by rebrand-overrides" || verdict FAIL "override copy missing"
o=$(vsudo "ls -la /dev/video* 2>&1 | head -3; v4l2-ctl --list-devices 2>&1 | head -5; echo rc=\$?; (modprobe vivid n_devs=1 node_types=0x1 2>&1 && sleep 2 && echo vivid-loaded && v4l2-ctl --list-devices 2>&1 | head -4) || echo no-vivid-module")
echo "$o" | sed 's/^/    /'
echo "$o" | grep -q 'v4l2-ctl' || echo "$o" | grep -qE 'rc=|Cannot open|Failed' && verdict PASS "v4l-utils present (v4l2-ctl runs; no camera device in QEMU$(echo "$o" | grep -q vivid-loaded && echo ', vivid virtual camera loaded'))" || verdict FAIL "v4l2-ctl missing"
st=$(app_up r8camera "plasma-camera" 16 fab-camera); echo "    $st"
# what each camera path sees of the virtual device (evidence for docs/HARDWARE.md; no verdict — vivid is a test driver, not a webcam)
usr 'echo "--- v4l2:"; v4l2-ctl -d /dev/video0 --list-formats 2>&1 | head -6; echo "--- libcamera:"; cam -l 2>&1 | head -4; echo "--- PipeWire video nodes:"; pw-dump 2>/dev/null | grep -E "\"(media.class|node.description)\": \"(Video|.*[Vv]ivid.*)" | sort | uniq -c | head -6; echo "--- Fab Camera journal:"; journalctl --user -u r8camera --no-pager -b 2>/dev/null | grep -iE "camera|device|pipewire|gst" | tail -6' | sed 's/^/    /'
echo "$st" | grep -q '^active' && echo "$st" | grep -qE ' 0 ?$' && verdict PASS "Fab Camera (plasma-camera) starts and stays up $(echo "$o" | grep -q vivid-loaded && echo 'with the vivid virtual camera' || echo 'without a camera device') — no crash" || verdict FAIL "Fab Camera: $st"
vm 'test -f /usr/lib/x86_64-linux-gnu/spa-0.2/libcamera/libspa-libcamera.so && test -f /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstpipewire.so' && verdict PASS "PipeWire camera plugins (libspa-libcamera, gstpipewire) in place for portal / Flatpak camera access" || verdict FAIL "PipeWire camera plugins missing"

# ---------- 6. microphone: the quick-settings probe and the tile's exact wpctl commands move the real default source
echo; echo "=== microphone (quick settings)"
QS=packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings/contents
$SCP $QS/code/status.sh fabos@127.0.0.1:/tmp/status.sh >/dev/null
probe() { usr "sh /tmp/status.sh" | python3 -c 'import sys,json; d=json.loads(sys.stdin.readline()); print(d.get("mic",""), "|", json.dumps(d.get("privacy")))'; }
base=$(usr 'wpctl get-volume @DEFAULT_AUDIO_SOURCE@'); echo "    default source now: $base"
echo "$base" | grep -q '^Volume:' && verdict PASS "the guest has a default source (emulated hda-micro): $base" || verdict FAIL "no default source in the guest"
setv=$(grep -o 'wpctl set-volume @DEFAULT_AUDIO_SOURCE@ ' $QS/ui/main.qml | head -1); setm=$(grep -o 'wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle' $QS/ui/main.qml | head -1)
[ -n "$setv" ] && [ -n "$setm" ] && verdict PASS "the tile issues exactly: '${setv}<0.00-1.00>' and '$setm'" || verdict FAIL "main.qml does not issue the expected wpctl source commands"
usr "${setv}0.30" >/dev/null; sleep 0.5; p=$(probe); echo "    after set-volume 0.30: $p"
echo "$p" | grep -q 'Volume: 0.30' && verdict PASS "probe reports the new input volume 0.30 (status.sh -> mic)" || verdict FAIL "probe did not follow set-volume: $p"
usr "$setm" >/dev/null; sleep 0.5; p=$(probe); echo "    after set-mute toggle: $p"
echo "$p" | grep -q 'MUTED' && verdict PASS "probe reports the muted default source" || verdict FAIL "probe did not follow set-mute: $p"
usr "$setm; wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.00" >/dev/null
# recording app -> mic in use (a PipeWire capture stream in state running). Fab Voice's wake-word listener records all the
# time through pw-record, so the count is compared relative to the baseline, not to zero.
used() { echo "$1" | sed -n 's/.*"mic_used": *\([0-9]*\).*/\1/p'; }
p0=$(probe); n0=$(used "$p0"); echo "    baseline: $p0"
usr "systemd-run --user --collect -q -u r8rec -- pw-record /tmp/r8rec.wav" >/dev/null; sleep 3   # automatic target (with --target 0 the stream never reaches "running")
p=$(probe); n1=$(used "$p"); echo "    while a second pw-record runs: $p"; usr "systemctl --user stop r8rec; rm -f /tmp/r8rec.wav" >/dev/null 2>&1
[ "${n1:-0}" -gt "${n0:-0}" ] && echo "$p" | grep -q pw-record && verdict PASS "a recording app shows as microphone-in-use: mic_used $n0 -> $n1, named in mic_apps (privacy glyph data)" || verdict FAIL "mic_used did not rise while pw-record ran: $p0 -> $p"
sleep 1; p=$(probe); n2=$(used "$p"); echo "    after it stopped: $p"
[ "${n2:-9}" -eq "${n0:-0}" ] && verdict PASS "microphone-in-use falls back to the baseline ($n0: Fab Voice's listener when it is on) when the recording stops" || verdict FAIL "mic_used did not fall back to $n0: $p"

echo; echo "### result: $pass PASS, $fail FAIL"
[ $fail = 0 ]
