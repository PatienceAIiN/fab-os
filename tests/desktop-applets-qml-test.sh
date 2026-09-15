#!/bin/bash
# Headless QML checks for the three Fab OS desktop applets (quick settings and the clock in the top bar, the dock), run
# inside the built image with plasmawindowed (QT_QPA_PLATFORM=offscreen), the same recipe as tests/askbar-qml-test.sh:
#   1. loads each real applet for 25 s and fails on any QML diagnostic (file.qml:line) — compile errors, binding loops, …
#   2. builds a temp copy of each plasmoid whose main.qml gets ONE appended Loader line that hands the root item and its
#      ids to tests/{quicksettings,dock,clock}-qml-harness/Driver.qml; the drivers feed status text / drive hover and
#      render build/{light,dark}/quicksettings-{bar,open-mid,open-end,edit,notifications}.png, dock-{rest,hover,uniform}.png
#      and clock-bar.png — each harness runs once per Fab OS colour scheme (FabLight / FabDark copied into
#      ~/.config/kdeglobals in the container). The quick-settings driver samples the dialog window's size, the card's y
#      and the tiles' stagger every 11 ms while the pane opens (480 ms) and closes (the no-blink proof); the dock driver
#      grabs every icon box so tests/dock-qml-harness/measure.py (host python3 + Pillow) can compute the visible extents
#      and the tile factor, prints the gaps between items (DOCK_GAPS) and the indicator geometry (INDICATORS) so the same
#      script measures the running / active indicator pixels and the row's centring in dock-rest.png.
#   3. `tray`: a REAL plasmashell as the session of a virtual kwin_wayland with an empty HOME runs the look-and-feel's
#      layout script (bind-mounted over the package) and the generated plasma-org.kde.plasma.desktop-appletsrc is
#      asserted: the system tray's extraItems without the five replaced applets, knownItems with them, the Fab OS clock
#      in the top panel and no stock digital clock, the zero-width launcher, Firefox pinned.
#   Every run sets QT_QPA_PLATFORMTHEME=kde as a Plasma session does: icons go through KIconLoader (monochrome glyphs are
#   recoloured to the scheme; without it Qt's own icon engine draws them in their file colour and they vanish on dark).
#   tests/desktop-applets-qml-test.sh [image] [steps]   (default localhost/fabos:vm; steps = any of soak-qs harness-qs soak-dock
#     harness-dock soak-clock harness-clock harness-dock-kwin harness-qs-kwin tray shot, default all but shot)
#   shot (opt-in, with tray): the real plasmashell session also sets a flat wallpaper and tries to screenshot itself
#   through KWin's ScreenShot2 D-Bus interface (tests/dock-qml-harness/kwin-shot.py; the session runs with KWin's own
#   KWIN_SCREENSHOT_NO_PERMISSION_CHECKS=1 test switch), falling back to the virtual backend's last composited frame
#   (KWIN_WAYLAND_VIRTUAL_SCREENSHOTS=1) -> build/tray/screen.png; measure.py panel then finds the dock panel and its
#   icons and asserts the row is centred with equal room at both ends (the 1 px launcher anchor + the panel's 4 px
#   spacing allowed). NOT YET PASSING IN THE CONTAINER (2026-09-16): the virtual kwin_wayland answered
#   org.kde.KWin.ScreenShot2.Error.Cancelled to CaptureWorkspace and CaptureActiveScreen under both QPainter (KWIN_COMPOSE=Q)
#   and llvmpipe GL (KWIN_COMPOSE=O) compositing, and the frame dump wrote no screenshot-N.png; the step FAILs honestly
#   then. The proofs of the anchor fix that do stand: the appletsrc the real plasmashell writes (tray) and the offscreen
#   dock render's measured gaps / centring (harness-dock).
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
STEPS=${2:-soak-qs harness-qs soak-dock harness-dock soak-clock harness-clock harness-dock-kwin harness-qs-kwin tray}
SCHEMES=${SCHEMES:-light dark}
want() { case " $STEPS " in *" $1 "*) return 0;; esac; return 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PLASMOIDS=$ROOT/packages/fabos-desktop/usr/share/plasma/plasmoids
LAYOUT=packages/fabos-desktop/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js
OUT=$ROOT/build; mkdir -p "$OUT/logs"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
QS=in.patienceai.fabos.quicksettings; DK=in.patienceai.fabos.dock; CK=in.patienceai.fabos.clock
MOUNTS=(-v "$PLASMOIDS/$QS:/usr/share/plasma/plasmoids/$QS:ro,Z" -v "$PLASMOIDS/$DK:/usr/share/plasma/plasmoids/$DK:ro,Z" -v "$PLASMOIDS/$CK:/usr/share/plasma/plasmoids/$CK:ro,Z")

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
run() {   # $1 label, $2 applet id, $3 timeout seconds, $4 extra XDG_DATA_DIRS prefix, $5 light|dark
  local scheme=${5:-light}; mkdir -p "$OUT/$scheme/measure"
  echo "== $1 [$scheme]"
  podman run --rm -e QT_QPA_PLATFORM=offscreen -e QT_QPA_PLATFORMTHEME=kde -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e XDG_MENU_PREFIX=plasma- -e XDG_DATA_DIRS="$4/usr/local/share:/usr/share" \
    -v "$T:/harness:ro,Z" -v "$OUT/$scheme:/out:Z" "${MOUNTS[@]}" "$IMG" \
    bash -c "mkdir -p /tmp/xdg && chmod 700 /tmp/xdg; $(scheme_setup "$scheme") timeout $3 dbus-run-session plasmawindowed $2 2>&1; echo exit=\$?" > "$T/$2.$scheme.log" 2>&1
  cp "$T/$2.$scheme.log" "$OUT/logs/"
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|fuse|xdg-desktop-portal|applications.menu|^qml: [0-9]+$|^$" "$T/$2.$scheme.log"
}
soak() {   # $1 applet id
  run "$1: real applet loads (25 s soak; any file.qml:line diagnostic fails)" "$1" 25 "" light
  grep -qE "\.(qml|js):[0-9]+" "$T/$1.light.log" && { echo "FAIL: QML diagnostics while loading $1"; fail=1; }
}
harness() {   # $1 applet id — once per colour scheme
  for scheme in $SCHEMES; do
    run "$1: harness (Driver.qml drives the real main.qml)" "${1}test" 75 "/harness/share:" $scheme
    grep -q "HARNESS DONE failures=0" "$T/${1}test.$scheme.log" || { echo "FAIL: $1 harness did not finish clean ($scheme)"; fail=1; }
    grep -qE "\.(qml|js):[0-9]+" "$T/${1}test.$scheme.log" && { echo "FAIL: QML diagnostics in the $1 harness run ($scheme)"; fail=1; }
  done
}

run_kwin() {   # $1 label, $2 applet id, $3 timeout seconds, $4 light|dark, $5 screen height — plasmawindowed as a virtual kwin_wayland session
  local scheme=${4:-light}; mkdir -p "$OUT/$scheme-kwin"
  echo "== $1 [$scheme]"
  cp "$ROOT/tests/dock-qml-harness/kwin-session.sh" "$T/kwin-session.sh"; chmod +x "$T/kwin-session.sh"; cp "$ROOT/tests/dock-qml-harness/kwin-shot.py" "$T/kwin-shot.py"
  podman run --rm -e XDG_DATA_DIRS="/harness/share:/usr/local/share:/usr/share" -e HARNESS_WAYLAND_DEBUG \
    -v "$T:/harness:ro,Z" -v "$OUT/$scheme-kwin:/out:Z" "${MOUNTS[@]}" "$IMG" \
    /harness/kwin-session.sh "$2" "$3" "$scheme" "${5:-400}" > "$T/$2.kwin.$scheme.log" 2>&1
  cp "$T/$2.kwin.$scheme.log" "$OUT/logs/"
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|fuse|xdg-desktop-portal|applications.menu|^qml: [0-9]+$|^$|kwin_|Failed to gain real time|Accepting client connections|qt.qpa|platform plugin|Reinstalling the application|Available platform plugins" "$T/$2.kwin.$scheme.log"
}
harness_kwin() {   # $1 applet id — once per colour scheme
  for scheme in $SCHEMES; do
    run_kwin "$1: harness under a virtual kwin_wayland (real TasksModel backend + real pointer)" "${1}test" 75 $scheme 400
    grep -q "HARNESS DONE failures=0" "$T/${1}test.kwin.$scheme.log" || { echo "FAIL: $1 kwin harness did not finish clean ($scheme)"; fail=1; }
    grep -q "backend=real" "$T/${1}test.kwin.$scheme.log" || { echo "FAIL: $1 kwin harness had no real windowing backend ($scheme)"; fail=1; }
    [ "$(grep -c 'POINTER exit=0' "$T/${1}test.kwin.$scheme.log")" -ge 5 ] || { echo "FAIL: the real pointer was not injected in the $1 kwin harness ($scheme)"; fail=1; }
    grep -qE "\.(qml|js):[0-9]+" "$T/${1}test.kwin.$scheme.log" && { echo "FAIL: QML diagnostics in the $1 kwin harness run ($scheme)"; fail=1; }
  done
}
harness_qs_kwin() {   # quick settings under kwin_wayland with the real pointer, once per colour scheme
  for scheme in $SCHEMES; do
    run_kwin "$QS: harness under a virtual kwin_wayland (real pointer via org_kde_kwin_fake_input)" ${QS}test 100 $scheme 720
    local log=$T/${QS}test.kwin.$scheme.log
    grep -q "HARNESS DONE failures=0" "$log" || { echo "FAIL: quick settings kwin harness did not finish clean ($scheme)"; fail=1; }
    [ "$(grep -c 'POINTER exit=0' "$log")" -ge 5 ] || { echo "FAIL: the real pointer was not injected ($scheme)"; fail=1; }
    grep -qE "\.(qml|js):[0-9]+" "$log" && { echo "FAIL: QML diagnostics in the quick settings kwin harness run ($scheme)"; fail=1; }
  done
}
measure_dock() {   # the icon extents from the light-scheme offscreen grabs (host python3 + Pillow)
  echo "== dock icon extents (tests/dock-qml-harness/measure.py over build/light/measure)"
  if python3 -c "import PIL" 2>/dev/null; then
    python3 "$ROOT/tests/dock-qml-harness/measure.py" "$OUT/light/measure" | tee "$OUT/logs/dock-measure.log" || { echo "FAIL: dock icon extents"; fail=1; }
  else echo "SKIP: python3 Pillow not installed on the host (pip install pillow)"; fi
}
measure_indicators() {   # the running / active indicators + centring, measured in the resting render of each scheme (host python3 + Pillow)
  for scheme in $SCHEMES; do
    local log=$T/${DK}test.$scheme.log geom=$OUT/$scheme/dock-indicators.json
    echo "== dock indicators + spacing in $OUT/$scheme/dock-rest.png (tests/dock-qml-harness/measure.py indicators)"
    grep -m1 '^qml: DOCK_GAPS\|DOCK_GAPS ' "$log" | sed 's/.*DOCK_GAPS //' | tee "$OUT/$scheme/dock-gaps.json"
    grep -m1 'INDICATORS {' "$log" | sed 's/.*INDICATORS //' > "$geom"
    if ! python3 -c "import PIL" 2>/dev/null; then echo "SKIP: python3 Pillow not installed on the host"; continue; fi
    [ -s "$geom" ] || { echo "FAIL: no INDICATORS line in the $scheme dock harness log"; fail=1; continue; }
    python3 "$ROOT/tests/dock-qml-harness/measure.py" indicators "$OUT/$scheme/dock-rest.png" "$geom" | tee "$OUT/logs/dock-indicators.$scheme.log" || { echo "FAIL: dock indicators ($scheme)"; fail=1; }
  done
}

# ---- tray: a real plasmashell runs the layout script; the appletsrc it writes is the proof of what the script did
tray() {
  local log=$T/tray.log; mkdir -p "$OUT/tray"
  echo "== layout.js in a real plasmashell (virtual kwin_wayland, empty HOME): the generated appletsrc"
  cat > "$T/tray-session.sh" <<'EOS'
#!/bin/bash
# inside the image: HOME=/tmp and XDG_RUNTIME_DIR come from podman -e
mkdir -p /tmp/.config /tmp/xdg && chmod 700 /tmp/xdg
cp /usr/share/color-schemes/FabDark.colors /tmp/.config/kdeglobals
printf '\n[Icons]\nTheme=FabOS\n\n[KDE]\nLookAndFeelPackage=in.patienceai.fabos.desktop\n' >> /tmp/.config/kdeglobals
export KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software QT_QPA_PLATFORMTHEME=kde XDG_MENU_PREFIX=plasma-
# the shot step: KWin normally hands org.kde.KWin.ScreenShot2 only to a caller whose .desktop file names the interface;
# this is KWin's own switch for test sessions (harness only, never in the image's session)
export KWIN_SCREENSHOT_NO_PERMISSION_CHECKS=1
# ... and the QPainter-composited virtual session cancels ScreenShot2 captures, so the virtual backend's own frame dump
# (every composited frame as /tmp/<tmpdir>/screenshot-N.png; KWin's autotest switch) is the fallback: the last frame is the screen
[ "${FABOS_SHOT:-0}" = 1 ] && export KWIN_WAYLAND_VIRTUAL_SCREENSHOTS=1
cat > /tmp/session.sh <<'EOF'
#!/bin/bash
export QT_QPA_PLATFORM=wayland
dbus-update-activation-environment --all >/dev/null 2>&1
kactivitymanagerd >/tmp/kamd.log 2>&1 &
sleep 2
timeout 70 plasmashell --no-respawn >/tmp/plasmashell.log 2>&1 &
PS=$!
F=/tmp/.config/plasma-org.kde.plasma.desktop-appletsrc
for i in $(seq 1 70); do sleep 1; if [ $i -ge 15 ] && grep -q "extraItems=" "$F" 2>/dev/null && grep -q "in.patienceai.fabos.quicksettings" "$F" 2>/dev/null; then break; fi; done
echo "waited $i s; plasmashell alive: $(kill -0 $PS 2>/dev/null && echo yes || echo no)"
if [ "${FABOS_SHOT:-0}" = 1 ]; then
  # a flat wallpaper so the panels segment cleanly, then a real screenshot through KWin's ScreenShot2 D-Bus interface
  # (kwin-shot.py; the permission check is off for this test session, see KWIN_SCREENSHOT_NO_PERMISSION_CHECKS above)
  qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript 'var ds = desktops(); for (var i = 0; i < ds.length; i++) { ds[i].wallpaperPlugin = "org.kde.color"; ds[i].currentConfigGroup = ["Wallpaper", "org.kde.color", "General"]; ds[i].writeConfig("Color", "#202020") }' >/dev/null 2>&1
  sleep 5
  rm -f /out/screen.raw /out/screen.json /out/screen.png
  timeout 60 python3 /harness/kwin-shot.py /out/screen.raw /out/screen.json; echo "kwin-shot exit=$?"
  if [ ! -s /out/screen.raw ]; then
    f=$(ls -t /tmp/*/screenshot-*.png 2>/dev/null | head -1)
    [ -n "$f" ] && cp "$f" /out/screen.png && echo "virtual-backend frame: $f ($(ls /tmp/*/screenshot-*.png 2>/dev/null | wc -l) frames composited)"
  fi
fi
echo "===== plasmashell log (errors only) ====="
grep -iE "error|warning: .*layout|Could not|failed to load" /tmp/plasmashell.log | grep -vE "qml: |kf.plasma.quick|qt.svg|QQmlComponent|kf.windowsystem|Binding loop|xdg-desktop-portal|fuse|TypeError|ReferenceError: (Plasmoid|root)" | head -30
echo "===== appletsrc ====="
cat "$F" 2>/dev/null || echo "(no appletsrc written)"
echo "===== end appletsrc ====="
kill $PS 2>/dev/null; sleep 1
EOF
chmod +x /tmp/session.sh
cp /usr/bin/kwin_wayland /tmp/kwin_wayland   # the file capability on the real binary cannot be exec'd rootless
timeout 110 dbus-run-session -- /tmp/kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --width 1280 --height 720 --exit-with-session /tmp/session.sh 2>&1 | grep -vE "kwin_(core|scene|wayland|xkb|libinput|screencast)|Failed to gain real time|qt.qpa|^$|Accepting client"
echo "kwin exit=${PIPESTATUS[0]}"
EOS
  chmod +x "$T/tray-session.sh"; cp "$ROOT/tests/dock-qml-harness/kwin-shot.py" "$T/kwin-shot.py"
  local shot=0; want shot && shot=1
  podman run --rm -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e FABOS_SHOT=$shot -v "$T:/harness:ro,Z" -v "$OUT/tray:/out:Z" "${MOUNTS[@]}" \
    -v "$ROOT/$LAYOUT:/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js:ro,Z" \
    "$IMG" /harness/tray-session.sh > "$log" 2>&1
  cp "$log" "$OUT/logs/tray-plasmashell.log"
  sed -n '/===== appletsrc =====/,/===== end appletsrc =====/p' "$log" > "$OUT/tray/appletsrc"
  grep -E "waited|alive|kwin exit" "$log"
  local rc=$OUT/tray/appletsrc
  tchk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
  tchk "appletsrc written with the tray's General group" "grep -q '^extraItems=' $rc && grep -q '^knownItems=' $rc"
  local extra known
  extra=$(grep -m1 '^extraItems=' "$rc" | cut -d= -f2); known=$(grep -m1 '^knownItems=' "$rc" | cut -d= -f2)
  echo "      extraItems=$extra"; echo "      knownItems=$known"
  for id in org.kde.plasma.networkmanagement org.kde.plasma.bluetooth org.kde.plasma.volume org.kde.plasma.battery org.kde.plasma.brightness; do
    tchk "tray does not load $id (not in extraItems)" "! echo ',$extra,' | grep -q ',$id,'"
    tchk "tray knows $id (knownItems: the registry will not re-add it)" "echo ',$known,' | grep -q ',$id,'"
  done
  tchk "notifications applet loaded (popups) but hidden" "echo ',$extra,' | grep -q ',org.kde.plasma.notifications,' && grep -q '^hiddenItems=.*org.kde.plasma.notifications' $rc"
  tchk "no stock tray applet was instantiated for the five (no [Applets] plugin= lines)" "! grep -E '^plugin=org.kde.plasma.(networkmanagement|bluetooth|volume|battery|brightness)$' $rc"
  tchk "top panel: Fab OS clock present, stock digital clock absent" "grep -q '^plugin=in.patienceai.fabos.clock$' $rc && ! grep -q 'plugin=org.kde.plasma.digitalclock' $rc"
  tchk "top panel: quick settings + system tray present" "grep -q '^plugin=in.patienceai.fabos.quicksettings$' $rc && grep -q '^plugin=org.kde.plasma.systemtray$' $rc"
  tchk "clock and quick settings share barSize=medium" "[ \$(grep -c '^barSize=medium' $rc) -ge 2 ]"
  tchk "dock: Fab OS dock with start + peek, launcher menu 1 px (icon = the dock's launcher-anchor.png, menuLabel=)" "grep -q '^plugin=in.patienceai.fabos.dock$' $rc && grep -q '^showStart=true' $rc && grep -q '^showPeek=true' $rc && grep -q '^icon=/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/images/launcher-anchor.png$' $rc && grep -q '^menuLabel=$' $rc"
  tchk "dock: Firefox pinned, no Brave, no icontasks / showdesktop applets" "grep -q 'applications:firefox.desktop' $rc && ! grep -qi brave $rc && ! grep -qE 'plugin=org.kde.plasma.(icontasks|showdesktop)' $rc"
  tchk "speed always on: showSpeed=true written" "grep -q '^showSpeed=true' $rc"
  if [ "$shot" = 1 ]; then   # the real panels as KWin composited them: is the dock one evenly spaced, centred row (no blank before the Fab OS button)?
    grep -E "kwin-shot|virtual-backend frame" "$log"
    echo "== real dock panel in $OUT/tray/screen.png (tests/dock-qml-harness/measure.py panel)"
    [ -s "$OUT/tray/screen.raw" ] && python3 "$ROOT/tests/dock-qml-harness/measure.py" raw2png "$OUT/tray/screen.raw" "$OUT/tray/screen.json" "$OUT/tray/screen.png"
    if [ -s "$OUT/tray/screen.png" ] && python3 -c "import PIL" 2>/dev/null; then
      python3 "$ROOT/tests/dock-qml-harness/measure.py" panel "$OUT/tray/screen.png" "$OUT/tray/dock-real.png" | tee "$OUT/logs/dock-panel.log" || { echo "FAIL: real dock panel geometry"; fail=1; }
    else echo "FAIL: no screenshot (kwin-shot.py) or no Pillow on the host"; fail=1; fi
  fi
}

make_harness $QS quicksettings-qml-harness \
  'Loader { source: Qt.resolvedUrl("../harness/Driver.qml"); onLoaded: { item.root = root; item.pane = pane; item.bar = bar; item.history = history; item.settingsPane = settingsPane; item.notifPane = notifPane; item.tilesArea = tilesArea; item.tileRepeater = tileRepeater; item.batInd = batInd; item.netInd = netInd; item.volInd = volInd; item.bellInd = bellInd; item.notifList = notifList; item.poll = poll; item.probe = probe } }'
make_harness $DK dock-qml-harness \
  'Loader { source: Qt.resolvedUrl("../harness/Driver.qml"); onLoaded: { item.dock = dock; item.row = row; item.tasksModel = tasksModel; item.repeater = repeater; item.startItem = startItem; item.peekItem = peekItem } }'
make_harness $CK clock-qml-harness \
  'Loader { source: Qt.resolvedUrl("../harness/Driver.qml"); onLoaded: { item.root = root; item.label = label; item.popup = popup; item.minuteTimer = minuteTimer } }'

want soak-qs && soak $QS
want harness-qs && harness $QS
want soak-dock && soak $DK
want harness-dock && { harness $DK; measure_dock; measure_indicators; }
want soak-clock && soak $CK
want harness-clock && harness $CK
want harness-dock-kwin && harness_kwin $DK
want harness-qs-kwin && harness_qs_kwin
want tray && tray
echo "desktop-applets-qml-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  (renders: $OUT/{light,dark}/quicksettings-{bar,open-mid,open-end,edit,notifications}.png $OUT/{light,dark}/dock-{rest,hover,uniform}.png $OUT/{light,dark}/clock-bar.png $OUT/{light,dark}-kwin/dock-{rest,hover}.png $OUT/{light,dark}-kwin/quicksettings-bar-hover.png; tray proof: $OUT/tray/appletsrc)"
exit $fail
