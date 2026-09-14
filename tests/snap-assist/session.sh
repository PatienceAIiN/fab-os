#!/bin/bash
# Runs INSIDE the kwin_wayland --virtual session (kwin --exit-with-session=<this>): waits for KWin's D-Bus, opens three
# real Wayland client windows, loads tests/snap-assist/harness.js and gives it time to run. Output goes to KWin's log.
Q=/usr/lib/qt6/bin/qdbus
for i in $(seq 1 60); do "$Q" org.kde.KWin /Scripting >/dev/null 2>&1 && break; sleep 0.5; done
echo "session: kwin dbus up after $i tries"
echo "session: fabos-snap-assist loaded=$("$Q" org.kde.KWin /Scripting org.kde.kwin.Scripting.isScriptLoaded fabos-snap-assist)"
echo "session: compositing=$("$Q" org.kde.KWin /Compositor org.kde.kwin.Compositing.compositingType 2>&1) windowview loaded=$("$Q" org.kde.KWin /Effects org.kde.kwin.Effects.isEffectLoaded windowview 2>&1)"
export QT_QPA_PLATFORM=wayland XDG_SESSION_TYPE=wayland
kcalc >/dev/null 2>&1 &
konsole >/dev/null 2>&1 &
kate -n >/dev/null 2>&1 &
id=$("$Q" org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /src/tests/snap-assist/harness.js snap-harness)
echo "session: harness id=$id"
"$Q" org.kde.KWin /Scripting org.kde.kwin.Scripting.start
sleep 45
echo "session: end"
