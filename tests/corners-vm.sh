#!/usr/bin/env bash
# Rounded window corners AND one soft rounded shadow, checked INSIDE the booted VM over SSH (vm profile: scripts/boot-vm.sh,
# hostfwd 2222->22). ADR-0019 + its 2026-09-16 amendment (the KDE-Rounded-Corners effect draws the only shadow; the Aurorae frame is flat).
#   1. KWin lists the KDE-Rounded-Corners effect (kwin4_effect_shapecorners) in org.kde.kwin.Effects.loadedEffects, and the
#      session's kwinrc says UseNativeDecorationShadows=false (the effect's own shadow, not the decoration's).
#   2. Per colour scheme (FabDark, then FabLight, when plasma-apply-colorscheme exists; otherwise the current scheme once):
#      a reference screenshot of the bare desktop, then Fab Editor (kate) is opened and placed at a known frame geometry by a
#      KWin script (which reports the geometry over the session bus, like tests/perf-vm.sh), `spectacle -b -n -f -o` grabs the
#      screen, and tests/corners-sample.py compares the two screenshots (every value is printed):
#        corners   the four frame-corner pixels vs 14 px inside and the diagonal outside neighbour (a square corner == inside);
#        diagonal  12 px outward from each box corner: weaker than beside the straight edge, clearly so at the corner (a
#                  square-cornered shadow is equal there), soft, and back to the background — no shadow rectangle;
#        along     2 px outside the top/bottom/side edges from each corner past the arc start: one smooth ramp, no step
#                  "before the curve";
#        edges     2..6 px outside each edge midpoint: a soft gradient, no hard step;  and the shadow exists at all.
#      `python3 tests/corners-sample.py --selftest` shows the rules on synthetic screenshots (round+soft passes, the 1.0-5
#      rectangle and a square window fail).
#   Every ssh/scp call is wrapped in `timeout 60` (SSH_TIMEOUT to change): a dying SSH child can never block the script — the
#   round-5 run hung in a bare wait. Overall the script also bounds its own boot wait (100 x 3 s) and session wait (120 x 3 s).
#   tests/corners-vm.sh [--keep] [--radius N]      Output: build/corners-vm.out, build/corners-<scheme>.png, build/corners-<scheme>-ref.png
# Needs on the host: sshpass, python3 with Pillow (PIL) for the pixel sampling.
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
KEEP=0; R=14; SSH_TIMEOUT=${SSH_TIMEOUT:-60}
while [ $# -gt 0 ]; do case "$1" in --keep) KEEP=1;; --radius) R=$2; shift;; -h|--help) sed -n '2,22p' "$0"; exit 0;; *) echo "unknown argument: $1"; exit 3;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -P 2222"
mkdir -p build; OUT=build/corners-vm.out; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
vm() { timeout "$SSH_TIMEOUT" $SSH "$@"; }                  # every remote call is bounded; a hung ssh returns 124
scp_to() { timeout "$SSH_TIMEOUT" $SCP "$1" "fabos@127.0.0.1:$2" >/dev/null; }
scp_from() { timeout "$SSH_TIMEOUT" $SCP "fabos@127.0.0.1:$1" "$2" >/dev/null; }
# the graphical session's environment for anything that must talk to KWin / Wayland / the session bus from the ssh login
SENV='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; export WAYLAND_DISPLAY=$(cd $XDG_RUNTIME_DIR && ls -d wayland-? 2>/dev/null | head -1); export QT_QPA_PLATFORM=wayland XDG_SESSION_TYPE=wayland;'
vms() { vm "$SENV $*"; }
pass=0; fail=0
verdict() { if [ "$1" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); fi; echo ">>> $1: $2"; }
python3 -c "import PIL" 2>/dev/null || { echo "python3-pillow (PIL) is needed on the host for the pixel sampling"; exit 3; }
command -v sshpass >/dev/null || { echo "sshpass is needed on the host"; exit 3; }
test -f tests/corners-sample.py || { echo "tests/corners-sample.py missing"; exit 3; }
BOOTED=0
echo "### Fab OS rounded corners + shadow VM test — $(date -u +%FT%TZ) — radius $R, ssh timeout ${SSH_TIMEOUT}s"
pgrep -f qemu-system-x86_64 >/dev/null || { echo "booting VM"; BOOTED=1; (scripts/boot-vm.sh --headless --mem "${VM_MEM:-2048}" --cpus 4 > build/boot-headless.out 2>&1 &); }
for i in $(seq 1 100); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM"; exit 3; }
for i in $(seq 1 120); do vm "pgrep -x plasmashell >/dev/null && pgrep -x kwin_wayland >/dev/null" 2>/dev/null && break; sleep 3; done
vm "pgrep -x kwin_wayland >/dev/null" || { echo "no kwin_wayland in the VM (is the session logged in?)"; exit 3; }
sleep 5

# ---------- 1. the effect is loaded and draws the shadow itself
echo; echo "=== effect"
vm "dpkg-query -W -f 'package: \${Package} \${Version} \${Status}\n' fabos-rounded-corners 2>&1; grep -n '^kwin4_effect_shapecornersEnabled\|^\[Round-Corners\]\|^Size=\|^InactiveCornerRadius=\|^UseNativeDecorationShadows=\|^ShadowSize=\|^InactiveShadowSize=' /etc/xdg/kwinrc; grep -n '^Padding\|^Border' /usr/share/aurorae/themes/FabOS/FabOSrc | tr '\n' ' '; echo"
loaded=$(vms "qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.loadedEffects" 2>&1 | tr '\n' ' ')
echo "loadedEffects: $loaded"
echo "isEffectSupported: $(vms 'qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.isEffectSupported kwin4_effect_shapecorners' 2>&1)  compositing: $(vms 'qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation' 2>/dev/null | grep -E '^(Compositing Type|OpenGL renderer string|OpenGL version string):' | tr '\n' ' ')"
if echo " $loaded " | grep -q " kwin4_effect_shapecorners "; then verdict PASS "kwin4_effect_shapecorners is in loadedEffects"
else verdict FAIL "kwin4_effect_shapecorners not loaded (journal: $(vm 'journalctl --user -b -o cat 2>/dev/null | grep -i -m3 shapecorners' | tr '\n' '|'))"; fi
native=$(vm "kreadconfig6 --file kwinrc --group Round-Corners --key UseNativeDecorationShadows 2>/dev/null")
if [ "$native" = false ]; then verdict PASS "kwinrc [Round-Corners] UseNativeDecorationShadows=false (the effect's own shadow is the one shadow)"
else verdict FAIL "UseNativeDecorationShadows is '${native:-unset}' (expected false)"; fi
frame_shadow=$(vm "grep -c -E 'linearGradient|radialGradient|<mask|<filter' /usr/share/aurorae/themes/FabOS/decoration.svg 2>/dev/null")
if [ "${frame_shadow:-1}" = 0 ]; then verdict PASS "Aurorae frame is flat: no gradient/mask/filter in decoration.svg"
else verdict FAIL "decoration.svg still has $frame_shadow shadow primitives"; fi

# ---------- 2. pixels, per scheme
schemes=current
vm "command -v plasma-apply-colorscheme >/dev/null && test -f /usr/share/color-schemes/FabDark.colors && test -f /usr/share/color-schemes/FabLight.colors" && schemes="FabDark FabLight"
orig=$(vm "kreadconfig6 --file kdeglobals --group General --key ColorScheme 2>/dev/null"); echo "schemes: $schemes (current: ${orig:-?})"
cat > build/corners-kwin.js <<'JS'
// Fab OS corners probe: place the Fab Editor (kate) window at a known frame geometry and report it over the session bus
// (print() is gated by the kwin_scripting log category; a call to the bus driver under our own interface is visible to
// `busctl --user monitor` on the ssh side). Reports again 1.5 s later, after the Wayland configure round trip.
function report(msg) { callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus", "in.patienceai.fabos.corners", "event", msg); }
function kateWindow() { var l = workspace.windowList().filter(function (w) { return w.normalWindow && /kate/i.test(String(w.resourceClass)); }); return l.length ? l[0] : null; }
function place(tag) {
  var w = kateWindow(); if (!w) { report("nokate " + tag); return; }
  if (w.maximizable) { w.setMaximize(false, false); }
  w.frameGeometry = {x: __X__, y: __Y__, width: __W__, height: __H__};
  workspace.activeWindow = w;
  var g = w.frameGeometry;
  report("geo " + tag + " " + Math.round(g.x) + " " + Math.round(g.y) + " " + Math.round(g.width) + " " + Math.round(g.height) + " class=" + w.resourceClass);
}
function placeTwice() { place("now"); var t = new QTimer(); t.singleShot = true; t.timeout.connect(function () { place("settled"); }); t.start(1500); }
if (kateWindow()) { placeTwice(); } else { workspace.windowAdded.connect(function (w) { if (w.normalWindow && /kate/i.test(String(w.resourceClass))) { var t = new QTimer(); t.singleShot = true; t.timeout.connect(placeTwice); t.start(800); } }); }
JS
shot() { # shot <remote png> <local png>: spectacle full screen, batch, no notification; waits for the file
  vms "rm -f $1; spectacle -b -n -f -o $1 >/dev/null 2>&1" ; for i in $(seq 1 15); do vm "test -s $1" 2>/dev/null && break; sleep 1; done
  vm "test -s $1" || { echo "spectacle produced no $1 (session env: $(vms 'echo $WAYLAND_DISPLAY'))"; return 1; }
  scp_from "$1" "$2"; }
for scheme in $schemes; do
  echo; echo "=== scheme $scheme"
  [ "$scheme" != current ] && { vms "plasma-apply-colorscheme $scheme" >/dev/null 2>&1; sleep 5; }
  vms "pkill -x kate; qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-corners >/dev/null 2>&1; pkill -f 'busctl --user monitor' 2>/dev/null; rm -f /tmp/corners-monitor.jsonl; true"; sleep 1
  shot /tmp/corners-ref.png "build/corners-$scheme-ref.png" || { verdict FAIL "$scheme: no reference screenshot"; continue; }
  read -r SW SH < <(python3 -c "from PIL import Image; im=Image.open('build/corners-$scheme-ref.png'); print(im.size[0], im.size[1])")
  X=$((SW*15/100)); Y=$((SH*18/100)); W=$((SW*55/100)); H=$((SH*50/100)); echo "screen ${SW}x${SH}; target frame ${W}x${H}+${X}+${Y}"
  sed "s/__X__/$X/; s/__Y__/$Y/; s/__W__/$W/; s/__H__/$H/" build/corners-kwin.js > build/corners-kwin-$scheme.js
  scp_to "build/corners-kwin-$scheme.js" /tmp/corners-kwin.js
  vms "setsid -f sh -c 'busctl --user monitor --json=short --match \"interface=in.patienceai.fabos.corners\" > /tmp/corners-monitor.jsonl 2>/dev/null'; sleep 1"
  vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /tmp/corners-kwin.js fabos-corners >/dev/null && qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start" >/dev/null 2>&1
  vms "setsid -f kate -n /tmp/fabos-corners.txt >/dev/null 2>&1"
  geo=""; for i in $(seq 1 30); do geo=$(vm "grep -o 'geo settled [0-9 -]*' /tmp/corners-monitor.jsonl 2>/dev/null | tail -1"); [ -n "$geo" ] && break; sleep 1; done
  echo "kwin script: $(vm "grep -o '\"\(nokate\|geo\) [^\"]*' /tmp/corners-monitor.jsonl 2>/dev/null" | tr '\n' ' ')"
  [ -n "$geo" ] || { verdict FAIL "$scheme: Fab Editor window not placed (no geometry report)"; continue; }
  sleep 2
  shot /tmp/corners.png "build/corners-$scheme.png" || { verdict FAIL "$scheme: no screenshot with the window"; continue; }
  read -r GX GY GW GH < <(echo "$geo" | awk '{print $3, $4, $5, $6}')
  echo "frame geometry ${GW}x${GH}+${GX}+${GY}; sampling corners, diagonals, edges vs ${R} px inside (build/corners-$scheme.png vs -ref.png)"
  if timeout 120 python3 tests/corners-sample.py "build/corners-$scheme.png" "build/corners-$scheme-ref.png" "$GX" "$GY" "$GW" "$GH" "$R"; then
    verdict PASS "$scheme: four rounded corners at radius $R, one soft rounded shadow (no rectangle, no step before the curve)"
  else verdict FAIL "$scheme: corner/shadow sampling (see the values above)"; fi
  vms "pkill -x kate; qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-corners >/dev/null 2>&1; pkill -f 'busctl --user monitor' 2>/dev/null; true"
done
[ "$schemes" != current ] && [ -n "$orig" ] && vms "plasma-apply-colorscheme $orig" >/dev/null 2>&1
vm "rm -f /tmp/corners.png /tmp/corners-ref.png /tmp/corners-kwin.js /tmp/corners-monitor.jsonl /tmp/fabos-corners.txt" 2>/dev/null

echo; echo "### corners-vm: $pass PASS, $fail FAIL  (log: $OUT)"
if [ $BOOTED -eq 1 ] && [ $KEEP -eq 0 ]; then vm "echo fabos | sudo -S poweroff" >/dev/null 2>&1; fi
[ $fail -eq 0 ]
