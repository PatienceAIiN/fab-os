#!/bin/bash
# Headless checks for the Fab OS leave screen (look-and-feel contents/logout/Logout.qml), run inside the built image with
# the REAL ksmserver-logout-greeter, the process that shows it in a session. Every run uses QT_QPA_PLATFORM=offscreen
# (kwin step: a virtual kwin_wayland), PLASMA_SESSION_GUI_TEST=1 (libkworkspace's test session backend: maysd/canLogout
# are true without logind, the shutdown/reboot actions print "shutdown"/"reboot" instead of touching the machine) and a
# private session bus that activates nothing (tests/logout-qml-harness/bus.conf), prompted over D-Bus exactly like
# libkworkspace does (org.kde.LogoutPrompt /LogoutPrompt promptShutDown …). The package files are rendered from brand.conf
# the way build-debs.sh renders them (@DISTRO_NAME@ …) and bind-mounted over the image's copies.
#   soak       the four prompts (shut down / restart / log out / all options) with the dark package and a 3 s countdown:
#              the screen must come up in the right mode ("fabos-leave: ready mode=N"), produce no QML diagnostic
#              (file.qml:line, "Trying default theme" = the greeter fell back to Breeze, "No such signal"), and the
#              countdown must fire the real action: the test backend prints "shutdown"/"reboot", the logout goes out as
#              a D-Bus call org.kde.Shutdown.logout (dbus-monitor), the all-options grid never counts down.
#   light      the light package's Logout.qml (a Loader wrapper around the dark one) forwards all 10 greeter signals:
#              "light wrapper forwarding 10 signals", then the restart action reaches the backend through it.
#   kdeglobals no --lookandfeel flag: the greeter picks the package from kdeglobals [KDE] LookAndFeelPackage in a temp
#              XDG_CONFIG_HOME, the way a session selects it.
#   harness    a temp copy of the package (id in.patienceai.fabos.leavetest) whose Logout.qml gets ONE appended Loader
#              line handing the root and its ids to tests/logout-qml-harness/Driver.qml: contract, texts, layout, the
#              unsaved-title heuristic, the systemd-unit fallback through the real session-apps.sh (stub systemctl + stub
#              .desktop entries; offscreen there is no window list), confirmLogoutCountdown=12 read through kreadconfig6,
#              the paused / held countdown states; renders build/logout-shutdown.png and build/logout-unsaved.png.
#   kwin       the same harness as the session of a virtual kwin_wayland with two real Wayland windows (testwin.py:
#              "draft.txt* — Test Editor" carries Qt's modified marker, "notes.txt — Test Editor" is clean) and the
#              package's in.patienceai.fabos.logout-greeter.desktop in the sycoca: TasksModel inside the greeter must
#              list both windows and flag the modified one (PATH windows); renders build/logout-windows.png.
#   tests/logout-screen-test.sh [image] [steps]   (default localhost/fabos:vm; steps = any of soak light kdeglobals harness kwin)
# Unfiltered container logs are kept in build/logs/logout-*.log.
set -u
IMG=${1:-localhost/fabos:vm}; IMG=${IMG:-localhost/fabos:vm}
STEPS=${2:-soak light kdeglobals harness kwin}
want() { case " $STEPS " in *" $1 "*) return 0;; esac; return 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LNF=$ROOT/packages/fabos-desktop/usr/share/plasma/look-and-feel
DARK=in.patienceai.fabos.desktop; LIGHT=in.patienceai.fabos.light.desktop; TESTID=in.patienceai.fabos.leavetest
OUT=$ROOT/build; mkdir -p "$OUT/logs"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H=$T/h; mkdir -p "$H/share/plasma/look-and-feel" "$H/share/applications" "$H/bin" "$H/cfg-soak" "$H/cfg-harness" "$H/cfg-kwin"
fail=0; checks=0
ok() { echo "PASS $1"; checks=$((checks + 1)); }
bad() { echo "FAIL $1"; fail=1; }
expect() { # expect <log> <regex> <label>
  if grep -qE -- "$2" "$1"; then ok "$3"; else bad "$3 (no /$2/ in $(basename "$1"))"; fi; }
forbid() { # forbid <log> <regex> <label>
  if grep -qE -- "$2" "$1"; then bad "$3: $(grep -E -- "$2" "$1" | head -3 | tr '\n' ' ')"; else ok "$3"; fi; }

# --- render the package files like packages/build-debs.sh does (brand.conf @VARS@) ---------------------------------
# shellcheck disable=SC1091
. "$ROOT/brand/brand.conf"
vars=$(grep -oE '^[A-Z_]+=' "$ROOT/brand/brand.conf" | tr -d '=')
render() { local f=$1 v val; for v in $vars; do val="${!v}"; val="${val//\\/\\\\}"; val="${val//&/\\&}"; val="${val//|/\\|}"; sed -i "s|@$v@|$val|g" "$f"; done; }
cp -r "$LNF/$DARK" "$H/share/plasma/look-and-feel/$DARK"
cp -r "$LNF/$LIGHT" "$H/share/plasma/look-and-feel/$LIGHT"
cp -r "$LNF/$DARK" "$H/share/plasma/look-and-feel/$TESTID"
sed -i "s/\"$DARK\"/\"$TESTID\"/" "$H/share/plasma/look-and-feel/$TESTID/metadata.json"
mkdir -p "$H/share/plasma/look-and-feel/$TESTID/contents/logout/harness"
cp "$ROOT/tests/logout-qml-harness/Driver.qml" "$H/share/plasma/look-and-feel/$TESTID/contents/logout/harness/"
cp "$ROOT/packages/fabos-desktop/usr/share/applications/in.patienceai.fabos.logout-greeter.desktop" "$H/share/applications/"
while IFS= read -r -d '' f; do grep -qI '@[A-Z_]*@' "$f" && render "$f"; done < <(find "$H" -type f -print0)
python3 - "$H/share/plasma/look-and-feel/$TESTID/contents/logout/Logout.qml" <<'EOF'
import sys
p = sys.argv[1]; s = open(p).read().rstrip("\n")
assert s.endswith("}"), "Logout.qml must end with the root's closing brace"
s = s[:-1] + ('    Loader { source: Qt.resolvedUrl("harness/Driver.qml"); onLoaded: { item.root = root; item.tasksModel = tasksModel; item.fallbackModel = fallbackModel; '
              'item.card = card; item.titleLabel = titleLabel; item.subtitleLabel = subtitleLabel; item.countdownLabel = countdownLabel; item.primaryButton = primaryButton; '
              'item.cancelButton = cancelButton; item.appsList = appsList; item.appsHeader = appsHeader; item.emptyLabel = emptyLabel; item.unsavedWarning = unsavedWarning; '
              'item.unavailableLabel = unavailableLabel; item.fallbackNote = fallbackNote; item.allOptions = allOptions; item.actions = actions; item.footnote = footnote } }\n}\n')
open(p, "w").write(s)
EOF
# harness helpers: the runner, the bus, the fallback stubs, the test windows
cp "$ROOT/tests/logout-qml-harness/greeter-run.sh" "$ROOT/tests/logout-qml-harness/bus.conf" "$ROOT/tests/logout-qml-harness/kwin-leave-session.sh" "$ROOT/tests/logout-qml-harness/testwin.py" "$H/"
chmod +x "$H/greeter-run.sh" "$H/kwin-leave-session.sh"
cp "$ROOT/tests/logout-qml-harness/stub-systemctl" "$H/bin/systemctl"; chmod +x "$H/bin/systemctl"
printf '[Desktop Entry]\nType=Application\nName=Test Editor\nIcon=test-editor\nExec=kate\n' > "$H/share/applications/org.kde.kate.desktop"
printf '[Desktop Entry]\nType=Application\nName=Test Browser\nIcon=test-browser\nExec=firefox\n' > "$H/share/applications/firefox.desktop"
printf '[Desktop Entry]\nType=Application\nName=Test Editor\nIcon=accessories-text-editor\nExec=/usr/bin/python3\nNoDisplay=true\n' > "$H/share/applications/fabos-testwin.desktop"
# configs: countdown 3 s for the soak (the action must fire), 12 s for the harness (the driver finishes first); the Fab
# Dark palette as kdeglobals so renders use the real colours; the kdeglobals step selects the package the session way.
printf '[General]\nconfirmLogout=true\nconfirmLogoutCountdown=3\n' > "$H/cfg-soak/ksmserverrc"
printf '[General]\nconfirmLogout=true\nconfirmLogoutCountdown=12\n' > "$H/cfg-harness/ksmserverrc"
cp "$H/cfg-harness/ksmserverrc" "$H/cfg-kwin/ksmserverrc"
printf '[KDE]\nLookAndFeelPackage=%s\n' "$DARK" > "$H/cfg-soak/kdeglobals.select"

run() { # run <label> <cfg dir name> <inside command...>   -> $LOG holds the unfiltered container output
  local label=$1 cfg=$2; shift 2
  LOG=$OUT/logs/logout-$label.log
  echo "== $label"
  podman run --rm -e QT_QPA_PLATFORM=offscreen -e QT_QPA_PLATFORMTHEME=kde -e PLASMA_SESSION_GUI_TEST=1 -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg \
    -e XDG_DATA_HOME=/tmp/h/share -e XDG_DATA_DIRS=/tmp/h/share:/usr/local/share:/usr/share -e XDG_CONFIG_HOME=/tmp/h/$cfg \
    -e PATH=/tmp/h/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin -e QT_LOGGING_RULES=kde.logout_greeter=true \
    -v "$T:/harness:ro,Z" -v "$OUT:/out:Z" \
    -v "$H/share/plasma/look-and-feel/$DARK/contents/logout:/usr/share/plasma/look-and-feel/$DARK/contents/logout:ro,Z" \
    -v "$H/share/plasma/look-and-feel/$LIGHT/contents/logout:/usr/share/plasma/look-and-feel/$LIGHT/contents/logout:ro,Z" \
    "$IMG" bash -c "cp -r /harness/h /tmp/h && for c in soak harness kwin; do { cat /usr/share/color-schemes/FabDark.colors; printf '\n[Icons]\nTheme=FabOS\n'; } > /tmp/h/cfg-\$c/kdeglobals; done; cat /tmp/h/cfg-soak/kdeglobals.select >> /tmp/h/cfg-soak/kdeglobals; $*" > "$LOG" 2>&1
  grep -E '^(RUN|LOG|CALL|kwin exit|testwin)' "$LOG" | grep -v '^LOG qml: PASS ' | head -60
}
qml_clean() { forbid "$1" '\.(qml|js):[0-9]+|Trying default theme|No such signal|Component is not ready|Failed to load lookandfeel|Couldn.t find a theme' "$2: no QML diagnostic, no fallback to Breeze"; }

if want soak; then
  run soak-shutdown cfg-soak "/tmp/h/greeter-run.sh promptShutDown $DARK 8 'fabos-leave: action'"
  qml_clean "$LOG" "shut down"
  expect "$LOG" 'LOG qml: fabos-leave: ready mode=2 showAll=false canShutdown=true canLogout=true' "shut down: screen up in mode 2 with the test backend's permissions"
  expect "$LOG" 'LOG qml: fabos-leave: action mode=2' "shut down: the 3 s countdown fired the action"
  expect "$LOG" '^LOG shutdown$' "shut down: haltRequested reached the session backend (test backend printed 'shutdown')"
  expect "$LOG" 'RUN greeter exited rc=0 after [2-6]\.' "shut down: greeter exited cleanly right after the action"
  run soak-reboot cfg-soak "/tmp/h/greeter-run.sh promptReboot $DARK 8 'fabos-leave: action'"
  qml_clean "$LOG" "restart"
  expect "$LOG" 'LOG qml: fabos-leave: ready mode=1 showAll=false' "restart: screen up in mode 1"
  expect "$LOG" 'LOG qml: fabos-leave: action mode=1' "restart: countdown fired"
  expect "$LOG" '^LOG reboot$' "restart: rebootRequested reached the backend"
  run soak-logout cfg-soak "/tmp/h/greeter-run.sh promptLogout $DARK 8 'fabos-leave: action'"
  qml_clean "$LOG" "log out"
  expect "$LOG" 'LOG qml: fabos-leave: ready mode=0 showAll=false' "log out: screen up in mode 0"
  expect "$LOG" 'CALL member=logout destination=org.kde.Shutdown' "log out: logoutRequested became the D-Bus call org.kde.Shutdown.logout (what plasma-shutdown acts on)"
  run soak-all cfg-soak "/tmp/h/greeter-run.sh promptAll $DARK 5 'fabos-leave: action'"
  qml_clean "$LOG" "all options"
  expect "$LOG" 'LOG qml: fabos-leave: ready mode=-1 showAll=true' "all options: tile grid mode"
  forbid "$LOG" 'fabos-leave: action|^LOG (shutdown|reboot)$|CALL member=' "all options: nothing counts down, no action fired in 5 s"
  expect "$LOG" 'RUN greeter still running after 5 s' "all options: greeter waited for the user"
fi
if want light; then
  run light cfg-soak "/tmp/h/greeter-run.sh promptReboot $LIGHT 8 'fabos-leave: action'"
  qml_clean "$LOG" "light package"
  expect "$LOG" 'LOG qml: fabos-leave: light wrapper forwarding 10 signals' "light: wrapper loaded the shared screen and connected the 10 signals"
  expect "$LOG" 'LOG qml: fabos-leave: ready mode=1' "light: restart mode"
  expect "$LOG" '^LOG reboot$' "light: the action went through the wrapper to the backend"
fi
if want kdeglobals; then
  run kdeglobals cfg-soak "/tmp/h/greeter-run.sh promptLogout - 8 'fabos-leave: action'"
  qml_clean "$LOG" "kdeglobals selection"
  expect "$LOG" 'LOG qml: fabos-leave: ready mode=0' "kdeglobals [KDE] LookAndFeelPackage=$DARK selects the Fab OS screen without a flag"
  expect "$LOG" 'CALL member=logout destination=org.kde.Shutdown' "kdeglobals: logout action reached D-Bus"
fi
if want harness; then
  run harness cfg-harness "/tmp/h/greeter-run.sh promptShutDown $TESTID 30 'HARNESS DONE'"
  qml_clean "$LOG" "harness"
  expect "$LOG" 'HARNESS DONE failures=0' "harness: every driver check passed"
  expect "$LOG" '^LOG qml: PATH apps$' "harness: offscreen = systemd-unit fallback path"
  n=$(grep -cE '^LOG qml: PASS ' "$LOG"); checks=$((checks + n)); echo "   ($n driver checks)"
  for p in shutdown unsaved; do [ -s "$OUT/logout-$p.png" ] && ok "render build/logout-$p.png" || bad "render build/logout-$p.png missing"; done
fi
if want kwin; then
  run kwin cfg-kwin "/tmp/h/kwin-leave-session.sh /tmp/h 40"
  qml_clean "$LOG" "kwin"
  expect "$LOG" 'HARNESS DONE failures=0' "kwin: every driver check passed"
  expect "$LOG" '^LOG qml: PATH windows$' "kwin: the greeter got the real window list through its X-KDE-Wayland-Interfaces entry"
  expect "$LOG" 'WIN draft.txt\* — Test Editor \| unsaved=true' "kwin: the modified window is listed and flagged"
  expect "$LOG" 'WIN notes.txt — Test Editor \| unsaved=false' "kwin: the clean window is listed and not flagged"
  n=$(grep -cE '^LOG qml: PASS ' "$LOG"); checks=$((checks + n)); echo "   ($n driver checks)"
  [ -s "$OUT/logout-windows.png" ] && ok "render build/logout-windows.png" || bad "render build/logout-windows.png missing"
fi
echo "logout-screen-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  checks=$checks  (renders: $OUT/logout-{shutdown,unsaved,windows}.png, logs: $OUT/logs/logout-*.log)"
exit $fail
