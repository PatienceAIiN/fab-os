#!/usr/bin/env bash
# Live test of the Fab OS Snap Assist KWin script against the real KWin shipped in the image.
# Starts kwin_wayland --virtual (software rendering, no GPU, no seat) inside the fabos container with the SOURCE-TREE
# /etc/xdg/kwinrc and /usr/share/kwin/scripts/fabos-snap-assist bind-mounted read-only, opens three real Wayland client
# windows (kcalc, konsole, kate), quick-tiles one to the left and checks that the script offers the others to
# org.kde.KWin.Effect.WindowView1.activate with a proper "as" payload and tiles the picked one into the right half.
# The container composites with QPainter, so the Window View effect itself (OpenGL/QML) cannot load here: the offer is
# checked at the D-Bus level (no marshalling error; the only tolerated error is the effect's object being absent) and
# RequireWindowView=false is set in the test user's kwinrc so the pick is honoured without the effect. Nothing is built
# or booted. Usage: tests/snap-assist-test.sh [image-tag]        (default fabos:vm; about a minute)
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); TAG=${1:-fabos:vm}; fail=0
SCRIPT=$ROOT/packages/fabos-desktop/usr/share/kwin/scripts/fabos-snap-assist
LOG=${TMPDIR:-/tmp}/snap-assist-kwin.log
podman run --rm --user 1000 --cap-add=SYS_NICE --security-opt label=disable \
  -v "$SCRIPT:/usr/share/kwin/scripts/fabos-snap-assist:ro" \
  -v "$ROOT/packages/fabos-desktop/etc/xdg/kwinrc:/etc/xdg/kwinrc:ro" \
  -v "$ROOT/tests/snap-assist:/src/tests/snap-assist:ro" \
  -e HOME=/tmp/home -e XDG_RUNTIME_DIR=/tmp/xdg -e QT_LOGGING_RULES='kwin_scripting*=true' \
  "$TAG" bash -c 'mkdir -p -m700 /tmp/xdg /tmp/home/.config && printf "[Script-fabos-snap-assist]\nRequireWindowView=false\n" > /tmp/home/.config/kwinrc && timeout 150 dbus-run-session -- kwin_wayland --virtual --no-lockscreen --no-kactivities --exit-with-session=/src/tests/snap-assist/session.sh 2>&1' > "$LOG"
grep -E "^(session|js: (harness|fabos-snap-assist)):|Received D-Bus message is error" "$LOG" | cut -c1-230 | sed 's/^/  | /'
chk() { if grep -qE "$2" "$LOG"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
chk "kwin started; script discovered in /usr/share/kwin/scripts and enabled by kwinrc" "fabos-snap-assist loaded=true"
chk "three client windows appeared"                        "harness: windows=3"
chk "w1 was tiled to the left half"                        "harness: w1 tile=0,0,0\.5,1"
chk "script offered the two other windows via activate"    "fabos-snap-assist: offering 2 window\(s\) for the right half via activate"
chk "picked window was tiled to the right half"            "fabos-snap-assist: tiled the picked window to the right"
chk "picked window really occupies the right half"         "harness: w2 tile=0\.5,0,0\.5,1"
chk "no offer when the pair is already complete"           "harness: DONE"
if grep -q "'activate'.*signature 'av'" "$LOG"; then echo "FAIL  activate handles were sent as 'av' (must be 'as')"; fail=1; else echo "PASS  no D-Bus signature mismatch on activate"; fi
chk "premise: a plain JS array reaches D-Bus as 'av' (rejected by an 'as' method)" "No such method 'shortcut'.*signature 'av'"
chk "premise: a borrowed QStringList is accepted by the same 'as' method"        "harness: probe-list reply=\\["
if [ "$(grep -c 'fabos-snap-assist: offering' "$LOG")" = 1 ]; then echo "PASS  exactly one offer"; else echo "FAIL  exactly one offer expected"; fail=1; fi
if grep -q "windowview loaded=true" "$LOG"; then chk "Window View effect reported shown" "fabos-snap-assist: window view shown";
else echo "SKIP  Window View effect is not loadable in this container (QPainter compositing); activate() reached the bus, object absent: $(grep -c "No such object path '/org/kde/KWin/Effect/WindowView1'" "$LOG") error(s)"; fi
echo "log: $LOG"
exit $fail
