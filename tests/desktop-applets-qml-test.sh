#!/bin/bash
# Headless QML checks for the two Fab OS desktop applets (quick settings in the top bar, the dock), run inside the built
# image with plasmawindowed (QT_QPA_PLATFORM=offscreen), the same recipe as tests/askbar-qml-test.sh:
#   1. loads each real applet for 25 s and fails on any QML diagnostic (file.qml:line) — compile errors, binding loops, …
#   2. builds a temp copy of each plasmoid whose main.qml gets ONE appended Loader line that hands the root item and its
#      ids to tests/{quicksettings,dock}-qml-harness/Driver.qml; the drivers feed status text / drive hover and render
#      build/quicksettings-{bar,pane,notifications}.png and build/dock-{idle,hover}.png.
#   tests/desktop-applets-qml-test.sh [image] [steps]   (default localhost/fabos:vm; steps = any of soak-qs harness-qs soak-dock harness-dock harness-dock-kwin, default all)
#   harness-dock-kwin runs the dock harness as the session of a virtual kwin_wayland (tests/dock-qml-harness/kwin-session.sh) so
#   libtaskmanager has a real windowing backend and the launchers really come out of TasksModel; offscreen they cannot.
# The plasmoid sources are bind-mounted over /usr/share/plasma/plasmoids/<id>, so this also runs against an image built
# before the applets existed.
set -u
IMG=${1:-localhost/fabos:vm}; IMG=${IMG:-localhost/fabos:vm}
STEPS=${2:-soak-qs harness-qs soak-dock harness-dock harness-dock-kwin}
want() { case " $STEPS " in *" $1 "*) return 0;; esac; return 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PLASMOIDS=$ROOT/packages/fabos-desktop/usr/share/plasma/plasmoids
OUT=$ROOT/build; mkdir -p "$OUT"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0

make_harness() {   # $1 applet id, $2 harness dir, $3 loader line
  local id=$1 H=$T/share/plasma/plasmoids/${1}test
  mkdir -p "$H/contents/harness"
  sed "s/\"$id\"/\"${id}test\"/" "$PLASMOIDS/$id/metadata.json" > "$H/metadata.json"
  cp -r "$PLASMOIDS/$id/contents/"* "$H/contents/"
  cp "$ROOT/tests/$2/Driver.qml" "$H/contents/harness/"
  python3 - "$H/contents/ui/main.qml" "$3" <<'EOF'
import sys
p, line = sys.argv[1], sys.argv[2]; s = open(p).read().rstrip("\n")
assert s.endswith("}"), "main.qml must end with the root's closing brace"
open(p, "w").write(s[:-1] + "    " + line + "\n}\n")
EOF
}
run() {   # $1 label, $2 applet id, $3 timeout seconds, $4 extra XDG_DATA_DIRS prefix, $5 real plasmoid id to mount
  echo "== $1"
  podman run --rm -e QT_QPA_PLATFORM=offscreen -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e XDG_MENU_PREFIX=plasma- -e XDG_DATA_DIRS="$4/usr/local/share:/usr/share" \
    -v "$T:/harness:ro,Z" -v "$OUT:/out:Z" -v "$PLASMOIDS/$5:/usr/share/plasma/plasmoids/$5:ro,Z" "$IMG" \
    bash -c "mkdir -p /tmp/xdg && chmod 700 /tmp/xdg; timeout $3 dbus-run-session plasmawindowed $2 2>&1; echo exit=\$?" > "$T/$2.log" 2>&1
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|fuse|xdg-desktop-portal|applications.menu|^qml: [0-9]+$|^$" "$T/$2.log"
}
soak() {   # $1 applet id
  run "$1: real applet loads (25 s soak; any file.qml:line diagnostic fails)" "$1" 25 "" "$1"
  grep -qE "\.(qml|js):[0-9]+" "$T/$1.log" && { echo "FAIL: QML diagnostics while loading $1"; fail=1; }
}
harness() {   # $1 applet id
  run "$1: harness (Driver.qml drives the real main.qml)" "${1}test" 60 "/harness/share:" "$1"
  grep -q "HARNESS DONE failures=0" "$T/${1}test.log" || { echo "FAIL: $1 harness did not finish clean"; fail=1; }
  grep -qE "\.(qml|js):[0-9]+" "$T/${1}test.log" && { echo "FAIL: QML diagnostics in the $1 harness run"; fail=1; }
}

run_kwin() {   # $1 label, $2 applet id, $3 timeout seconds, $4 real plasmoid id to mount — plasmawindowed as a virtual kwin_wayland session
  echo "== $1"
  cp "$ROOT/tests/dock-qml-harness/kwin-session.sh" "$T/kwin-session.sh"; chmod +x "$T/kwin-session.sh"
  podman run --rm -e XDG_DATA_DIRS="/harness/share:/usr/local/share:/usr/share" \
    -v "$T:/harness:ro,Z" -v "$OUT:/out:Z" -v "$PLASMOIDS/$4:/usr/share/plasma/plasmoids/$4:ro,Z" "$IMG" \
    /harness/kwin-session.sh "$2" "$3" > "$T/$2.kwin.log" 2>&1
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|fuse|xdg-desktop-portal|applications.menu|^qml: [0-9]+$|^$|kwin_|Failed to gain real time|Accepting client connections|qt.qpa|platform plugin|Reinstalling the application|Available platform plugins" "$T/$2.kwin.log"
}
harness_kwin() {   # $1 applet id
  run_kwin "$1: harness under a virtual kwin_wayland (real TasksModel backend)" "${1}test" 60 "$1"
  grep -q "HARNESS DONE failures=0" "$T/${1}test.kwin.log" || { echo "FAIL: $1 kwin harness did not finish clean"; fail=1; }
  grep -q "backend=real" "$T/${1}test.kwin.log" || { echo "FAIL: $1 kwin harness had no real windowing backend"; fail=1; }
  grep -qE "\.(qml|js):[0-9]+" "$T/${1}test.kwin.log" && { echo "FAIL: QML diagnostics in the $1 kwin harness run"; fail=1; }
}

make_harness in.patienceai.fabos.quicksettings quicksettings-qml-harness \
  'Loader { source: Qt.resolvedUrl("../harness/Driver.qml"); onLoaded: { item.root = root; item.pane = pane; item.bar = bar; item.history = history; item.settingsPane = settingsPane; item.notifPane = notifPane; item.batInd = batInd; item.netInd = netInd; item.volInd = volInd; item.bellInd = bellInd; item.notifList = notifList; item.poll = poll; item.netPoll = netPoll } }'
make_harness in.patienceai.fabos.dock dock-qml-harness \
  'Loader { source: Qt.resolvedUrl("../harness/Driver.qml"); onLoaded: { item.dock = dock; item.row = row; item.tasksModel = tasksModel; item.repeater = repeater } }'

want soak-qs && soak in.patienceai.fabos.quicksettings
want harness-qs && harness in.patienceai.fabos.quicksettings
want soak-dock && soak in.patienceai.fabos.dock
want harness-dock && harness in.patienceai.fabos.dock
want harness-dock-kwin && harness_kwin in.patienceai.fabos.dock
echo "desktop-applets-qml-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  (renders: $OUT/quicksettings-{bar,pane,notifications}.png $OUT/dock-{idle,hover}.png)"
exit $fail
