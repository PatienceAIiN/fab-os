#!/bin/bash
# Headless QML checks for the two Fab OS desktop applets (quick settings in the top bar, the dock), run inside the built
# image with plasmawindowed (QT_QPA_PLATFORM=offscreen), the same recipe as tests/askbar-qml-test.sh:
#   1. loads each real applet for 25 s and fails on any QML diagnostic (file.qml:line) — compile errors, binding loops, …
#   2. builds a temp copy of each plasmoid whose main.qml gets ONE appended Loader line that hands the root item and its
#      ids to tests/{quicksettings,dock}-qml-harness/Driver.qml; the drivers feed status text / drive hover and render
#      build/{light,dark}/quicksettings-{bar,pane,notifications}.png and build/{light,dark}/dock-{idle,hover}.png — each
#      harness runs once per Fab OS colour scheme (FabLight / FabDark copied into ~/.config/kdeglobals in the container).
#   Every run sets QT_QPA_PLATFORMTHEME=kde as a Plasma session does: icons go through KIconLoader (monochrome glyphs are
#   recoloured to the scheme; without it Qt's own icon engine draws them in their file colour and they vanish on dark).
#   tests/desktop-applets-qml-test.sh [image] [steps]   (default localhost/fabos:vm; steps = any of soak-qs harness-qs soak-dock harness-dock harness-dock-kwin harness-qs-kwin, default all)
#   SCHEMES="light" (default "light dark") limits the colour schemes; the unfiltered container logs are kept in build/logs/.
#   harness-dock-kwin runs the dock harness as the session of a virtual kwin_wayland (tests/dock-qml-harness/kwin-session.sh) so
#   libtaskmanager has a real windowing backend and the launchers really come out of TasksModel; offscreen they cannot.
#   harness-qs-kwin runs the quick-settings harness the same way and drives a REAL pointer through KWin's fake-input
#   protocol (tests/quicksettings-qml-harness/fakeinput.py): hover on an indicator (glyph-only magnify, rendered to
#   quicksettings-bar-hover.png) and hover + click on a notification row's dismiss cross.
# The plasmoid sources are bind-mounted over /usr/share/plasma/plasmoids/<id>, so this also runs against an image built
# before the applets existed.
set -u
IMG=${1:-localhost/fabos:vm}; IMG=${IMG:-localhost/fabos:vm}
STEPS=${2:-soak-qs harness-qs soak-dock harness-dock harness-dock-kwin harness-qs-kwin}
SCHEMES=${SCHEMES:-light dark}
want() { case " $STEPS " in *" $1 "*) return 0;; esac; return 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PLASMOIDS=$ROOT/packages/fabos-desktop/usr/share/plasma/plasmoids
OUT=$ROOT/build; mkdir -p "$OUT/logs"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0

make_harness() {   # $1 applet id, $2 harness dir, $3 loader line
  local id=$1 H=$T/share/plasma/plasmoids/${1}test
  mkdir -p "$H/contents/harness"
  sed "s/\"$id\"/\"${id}test\"/" "$PLASMOIDS/$id/metadata.json" > "$H/metadata.json"
  cp -r "$PLASMOIDS/$id/contents/"* "$H/contents/"
  cp "$ROOT/tests/$2/Driver.qml" "$H/contents/harness/"
  cp "$ROOT/tests/quicksettings-qml-harness/fakeinput.py" "$H/contents/harness/"
  python3 - "$H/contents/ui/main.qml" "$3" <<'EOF'
import sys
p, line = sys.argv[1], sys.argv[2]; s = open(p).read().rstrip("\n")
assert s.endswith("}"), "main.qml must end with the root's closing brace"
open(p, "w").write(s[:-1] + "    " + line + "\n}\n")
EOF
}
scheme_setup() {   # $1 light|dark -> shell snippet that installs the Fab OS colour scheme as ~/.config/kdeglobals in the container
  local f=FabLight; [ "$1" = dark ] && f=FabDark
  echo "mkdir -p /tmp/.config && cp /usr/share/color-schemes/$f.colors /tmp/.config/kdeglobals && printf '\\n[Icons]\\nTheme=FabOS\\n' >> /tmp/.config/kdeglobals;"
}
run() {   # $1 label, $2 applet id, $3 timeout seconds, $4 extra XDG_DATA_DIRS prefix, $5 real plasmoid id to mount, $6 light|dark
  local scheme=${6:-light}; mkdir -p "$OUT/$scheme"
  echo "== $1 [$scheme]"
  podman run --rm -e QT_QPA_PLATFORM=offscreen -e QT_QPA_PLATFORMTHEME=kde -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e XDG_MENU_PREFIX=plasma- -e XDG_DATA_DIRS="$4/usr/local/share:/usr/share" \
    -v "$T:/harness:ro,Z" -v "$OUT/$scheme:/out:Z" -v "$PLASMOIDS/$5:/usr/share/plasma/plasmoids/$5:ro,Z" "$IMG" \
    bash -c "mkdir -p /tmp/xdg && chmod 700 /tmp/xdg; $(scheme_setup "$scheme") timeout $3 dbus-run-session plasmawindowed $2 2>&1; echo exit=\$?" > "$T/$2.$scheme.log" 2>&1
  cp "$T/$2.$scheme.log" "$OUT/logs/"
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|fuse|xdg-desktop-portal|applications.menu|^qml: [0-9]+$|^$" "$T/$2.$scheme.log"
}
soak() {   # $1 applet id
  run "$1: real applet loads (25 s soak; any file.qml:line diagnostic fails)" "$1" 25 "" "$1" light
  grep -qE "\.(qml|js):[0-9]+" "$T/$1.light.log" && { echo "FAIL: QML diagnostics while loading $1"; fail=1; }
}
harness() {   # $1 applet id — once per colour scheme
  for scheme in $SCHEMES; do
    run "$1: harness (Driver.qml drives the real main.qml)" "${1}test" 60 "/harness/share:" "$1" $scheme
    grep -q "HARNESS DONE failures=0" "$T/${1}test.$scheme.log" || { echo "FAIL: $1 harness did not finish clean ($scheme)"; fail=1; }
    grep -qE "\.(qml|js):[0-9]+" "$T/${1}test.$scheme.log" && { echo "FAIL: QML diagnostics in the $1 harness run ($scheme)"; fail=1; }
  done
}

run_kwin() {   # $1 label, $2 applet id, $3 timeout seconds, $4 real plasmoid id to mount, $5 light|dark, $6 screen height — plasmawindowed as a virtual kwin_wayland session
  local scheme=${5:-light}; mkdir -p "$OUT/$scheme-kwin"
  echo "== $1 [$scheme]"
  cp "$ROOT/tests/dock-qml-harness/kwin-session.sh" "$T/kwin-session.sh"; chmod +x "$T/kwin-session.sh"
  podman run --rm -e XDG_DATA_DIRS="/harness/share:/usr/local/share:/usr/share" -e HARNESS_WAYLAND_DEBUG \
    -v "$T:/harness:ro,Z" -v "$OUT/$scheme-kwin:/out:Z" -v "$PLASMOIDS/$4:/usr/share/plasma/plasmoids/$4:ro,Z" "$IMG" \
    /harness/kwin-session.sh "$2" "$3" "$scheme" "${6:-400}" > "$T/$2.kwin.$scheme.log" 2>&1
  cp "$T/$2.kwin.$scheme.log" "$OUT/logs/"
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|fuse|xdg-desktop-portal|applications.menu|^qml: [0-9]+$|^$|kwin_|Failed to gain real time|Accepting client connections|qt.qpa|platform plugin|Reinstalling the application|Available platform plugins" "$T/$2.kwin.$scheme.log"
}
harness_kwin() {   # $1 applet id — once per colour scheme
  for scheme in $SCHEMES; do
    run_kwin "$1: harness under a virtual kwin_wayland (real TasksModel backend + real pointer)" "${1}test" 75 "$1" $scheme 400
    grep -q "HARNESS DONE failures=0" "$T/${1}test.kwin.$scheme.log" || { echo "FAIL: $1 kwin harness did not finish clean ($scheme)"; fail=1; }
    grep -q "backend=real" "$T/${1}test.kwin.$scheme.log" || { echo "FAIL: $1 kwin harness had no real windowing backend ($scheme)"; fail=1; }
    [ "$(grep -c 'POINTER exit=0' "$T/${1}test.kwin.$scheme.log")" -ge 5 ] || { echo "FAIL: the real pointer was not injected in the $1 kwin harness ($scheme)"; fail=1; }
    grep -qE "\.(qml|js):[0-9]+" "$T/${1}test.kwin.$scheme.log" && { echo "FAIL: QML diagnostics in the $1 kwin harness run ($scheme)"; fail=1; }
  done
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
harness_qs_kwin() {   # quick settings under kwin_wayland with the real pointer, once per colour scheme
  for scheme in $SCHEMES; do
    run_kwin "in.patienceai.fabos.quicksettings: harness under a virtual kwin_wayland (real pointer via org_kde_kwin_fake_input)" in.patienceai.fabos.quicksettingstest 90 in.patienceai.fabos.quicksettings $scheme 720
    local log=$T/in.patienceai.fabos.quicksettingstest.kwin.$scheme.log
    grep -q "HARNESS DONE failures=0" "$log" || { echo "FAIL: quick settings kwin harness did not finish clean ($scheme)"; fail=1; }
    [ "$(grep -c 'POINTER exit=0' "$log")" -ge 5 ] || { echo "FAIL: the real pointer was not injected ($scheme)"; fail=1; }
    grep -qE "\.(qml|js):[0-9]+" "$log" && { echo "FAIL: QML diagnostics in the quick settings kwin harness run ($scheme)"; fail=1; }
  done
}
want harness-qs-kwin && harness_qs_kwin
echo "desktop-applets-qml-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  (renders: $OUT/{light,dark}/quicksettings-{bar,pane,notifications}.png $OUT/{light,dark}/dock-{idle,hover}.png $OUT/{light,dark}-kwin/dock-{idle,hover}.png $OUT/{light,dark}-kwin/quicksettings-bar-hover.png)"
exit $fail
