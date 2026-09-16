#!/bin/bash
# Inside the image: run the REAL ksmserver-logout-greeter, prompt ONE mode over D-Bus exactly as libkworkspace does
# (org.kde.LogoutPrompt /LogoutPrompt promptShutDown|promptReboot|promptLogout|promptAll), wait until the greeter exits,
# a marker line shows up in its log or the time is up, then print everything the host script asserts on:
#   RUN  ...            how it ended (exit code, seconds, "still running")
#   LOG  <line>         the greeter's stdout/stderr (QML diagnostics, our fabos-leave: lines, the test backend's "shutdown")
#   CALL member=… destination=…   every non-bus method call the greeter made (dbus-monitor): the logout path goes to org.kde.Shutdown
# Without a session bus in the environment it re-runs itself under dbus-run-session with bus.conf, a bus that ACTIVATES
# NOTHING: with the stock session.conf the first qdbus call would start a second greeter from
# /usr/share/dbus-1/services/org.kde.LogoutPrompt.service and that one would answer the prompt with the image's package.
# Inside a virtual kwin_wayland session (kwin-leave-session.sh) the bus already exists and may activate; the name is then
# awaited by LISTING names (never by addressing the service), so no second instance can be started either.
#   greeter-run.sh <method|windowed> <lookandfeel id | -> <max seconds> [marker]
# QT_QPA_PLATFORM, PLASMA_SESSION_GUI_TEST (maysd=true without logind), XDG_* and the look-and-feel search path come from the caller.
METHOD=$1 LNF=$2 SECS=$3 MARKER=${4:-}
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then exec dbus-run-session --config-file="$HERE/bus.conf" -- "$0" "$@"; fi
G=/usr/lib/x86_64-linux-gnu/libexec/ksmserver-logout-greeter
export HOME=${HOME:-/tmp} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/xdg} QT_LOGGING_RULES=${QT_LOGGING_RULES:-kde.logout_greeter=true}
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
LOG=/tmp/greeter-$METHOD.log MON=/tmp/monitor-$METHOD.log
dbus-monitor --session "type=method_call" > "$MON" 2>&1 &
MP=$!
args=(); [ "$LNF" != - ] && args+=(--lookandfeel "$LNF"); [ "$METHOD" = windowed ] && args+=(--windowed)
"$G" "${args[@]}" > "$LOG" 2>&1 &
GP=$!
has_name() { dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null | grep -q '"org.kde.LogoutPrompt"'; }
for _ in $(seq 1 60); do has_name && break; kill -0 $GP 2>/dev/null || break; sleep 0.25; done
has_name && echo "RUN greeter owns org.kde.LogoutPrompt" || echo "RUN greeter never registered org.kde.LogoutPrompt"
T0=$(date +%s.%N)
if [ "$METHOD" != windowed ]; then qdbus6 org.kde.LogoutPrompt /LogoutPrompt "$METHOD" >/dev/null 2>&1; echo "RUN prompt $METHOD rc=$?"; fi
ticks=$(( SECS * 4 )); ended=""
for _ in $(seq 1 "$ticks"); do
  if ! kill -0 $GP 2>/dev/null; then ended=exited; break; fi
  if [ -n "$MARKER" ] && grep -q -- "$MARKER" "$LOG" 2>/dev/null; then ended=marker; break; fi
  sleep 0.25
done
T1=$(date +%s.%N)
if [ "$ended" = exited ]; then wait $GP; echo "RUN greeter exited rc=$? after $(echo "$T1 - $T0" | bc) s"
else
  [ "$ended" = marker ] && echo "RUN marker '$MARKER' seen after $(echo "$T1 - $T0" | bc) s" || echo "RUN greeter still running after $SECS s"
  kill $GP 2>/dev/null; wait $GP 2>/dev/null; echo "RUN greeter killed rc=$?"
fi
sleep 0.3; kill $MP 2>/dev/null; wait $MP 2>/dev/null
grep -vE '^$|QtQuick software backend|gui autotesting only|Could not find any platform plugin|does not support grabbing|compositingActive|QThreadStorage: entry' "$LOG" | sed 's/^/LOG /'
grep -E '^method call' "$MON" | grep -vE 'org\.freedesktop\.DBus|org\.freedesktop\.portal|org\.kde\.LogoutPrompt|org\.kde\.KIconLoader|org\.kde\.kconfig' \
  | sed -E 's/.*destination=([^ ;]+).*member=([A-Za-z0-9_]+).*/CALL member=\2 destination=\1/' | sort -u
exit 0
