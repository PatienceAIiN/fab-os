#!/bin/bash
# Inside the image (localhost/fabos:vm): a virtual, software-rendered kwin_wayland whose session is the probe applet
# (ModelProbe.qml = the dock's TasksModel settings + contents/code/dock-logic.js, run by plasmawindowed), REAL apps
# (Dolphin, Konsole, Firefox) launched into it, and probe-driver.py asserting the launcher <-> window merge on the live
# rows: each pin becomes its window / group row (HasLauncher), one row per app, marker on, a tap would activate — never
# launch. The tap transitions themselves are proven in the real VM (vm-dock-activate.sh); PROBE_TAPS=1 runs them here too.
# Host side (from the repo root; absolute paths for the mounts):
#   podman run --rm -v $PWD:/work:Z -v $PWD/build/r7-dock-activate/container:/out:Z localhost/fabos:vm /work/tests/dock-qml-harness/model-probe.sh
# Output: /out/probe.log (ROWS dumps + TAP results), /out/driver-*.log (PASS/FAIL), exit 1 on any FAIL.
set -u
export OUT=${OUT:-/out}; mkdir -p "$OUT"; rm -f "$OUT"/probe.log "$OUT"/kwin.log "$OUT"/driver-*.log   # never read a previous run's log
export HOME=/tmp XDG_RUNTIME_DIR=/tmp/xdg XDG_MENU_PREFIX=plasma- KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software QT_QPA_PLATFORMTHEME=kde
export PROBE_CMD=/tmp/dock-probe-cmd
mkdir -p /tmp/xdg /tmp/.config && chmod 700 /tmp/xdg
cp /usr/share/color-schemes/FabLight.colors /tmp/.config/kdeglobals 2>/dev/null; printf '\n[Icons]\nTheme=FabOS\n' >> /tmp/.config/kdeglobals
export H=/tmp/harness; P=$H/share/plasma/plasmoids/in.patienceai.fabos.dockprobe; rm -rf $H; mkdir -p $P/contents/ui $P/contents/code
cp /work/tests/dock-qml-harness/ModelProbe.qml $P/contents/ui/main.qml
cp /work/packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/code/dock-logic.js $P/contents/code/
cp /work/tests/dock-qml-harness/probe-driver.py $H/
printf '{"KPackageStructure":"Plasma/Applet","KPlugin":{"Id":"in.patienceai.fabos.dockprobe","Name":"Dock model probe","Version":"0"},"X-Plasma-API-Minimum-Version":"6.0"}\n' > $P/metadata.json
export XDG_DATA_DIRS=$H/share:/usr/local/share:/usr/share
kbuildsycoca6 --noincremental >/dev/null 2>&1
cp /usr/bin/kwin_wayland /tmp/kwin_wayland   # file capabilities (cap_sys_nice) cannot be exec'd rootless; a plain copy has none
cat > /tmp/probe-session.sh <<EOF
#!/bin/bash
export QT_QPA_PLATFORM=wayland
plasmawindowed in.patienceai.fabos.dockprobe > $OUT/probe.log 2>&1
echo "probe exit=\$?" >> $OUT/probe.log
EOF
chmod +x /tmp/probe-session.sh
cat > /tmp/inner.sh <<'EOF'
set -u
/tmp/kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --width 1280 --height 800 --exit-with-session /tmp/probe-session.sh > $OUT/kwin.log 2>&1 &
KW=$!
for i in $(seq 1 90); do grep -q READY $OUT/probe.log 2>/dev/null && break; sleep 0.5; done
grep -q READY $OUT/probe.log || { echo "probe never became READY"; cat $OUT/probe.log | head -40; kill $KW; exit 1; }
export WAYLAND_DISPLAY=$(ls /tmp/xdg 2>/dev/null | grep -m1 '^wayland-[0-9]*$'); : "${WAYLAND_DISPLAY:=wayland-0}"; export QT_QPA_PLATFORM=wayland
echo "== kwin up on $WAYLAND_DISPLAY ($(ls /tmp/xdg | tr '\n' ' ')); launching apps"
launch() { echo "  launch: $*"; setsid -f "$@" > /dev/null 2>&1; }
mkdir -p /tmp/ffprofile   # a fresh profile directory: no profile chooser, a real browser window
launch dolphin; launch konsole; launch firefox --new-instance --no-remote --profile /tmp/ffprofile --width 900 --height 600
# software-rendered apps take a while here: wait until the model holds the probe's own window + the three apps (<= 240 s)
n=0; for i in $(seq 1 240); do n=$(grep 'ROWS ' $OUT/probe.log 2>/dev/null | tail -1 | grep -o '"windows":[0-9]*' | grep -o '[0-9]*$'); [ "${n:-0}" -ge 4 ] && break; sleep 1; done
echo "== windows in the model: ${n:-0} after ${i}s (probe + dolphin + konsole + firefox)"
echo "== matching"; python3 $H/probe-driver.py $OUT/probe.log $PROBE_CMD matching dolphin konsole firefox | tee $OUT/driver-matching.log; RC1=${PIPESTATUS[0]}
# The tap transitions are proven in the real VM (vm-dock-activate.py): in this software-rendered container the probe's
# event loop is starved once three apps run (ROWS lines seconds apart, tap replies late), so they are opt-in here.
RC2=0; if [ "${PROBE_TAPS:-0}" = 1 ]; then echo "== taps"; python3 $H/probe-driver.py $OUT/probe.log $PROBE_CMD taps dolphin konsole kate | tee $OUT/driver-taps.log; RC2=${PIPESTATUS[0]}; fi
echo quit > $PROBE_CMD; sleep 2
pkill -x dolphin; pkill -x konsole; pkill -f firefox; pkill -f soffice; pkill -x systemsettings; pkill -x kate
kill $KW 2>/dev/null; wait $KW 2>/dev/null
echo "== results: matching rc=$RC1 taps rc=$RC2"
[ "$RC1" = 0 ] && [ "$RC2" = 0 ]
EOF
chmod +x /tmp/inner.sh
timeout ${PROBE_TIMEOUT:-400} dbus-run-session -- bash /tmp/inner.sh
RC=$?
echo "model-probe: $([ $RC = 0 ] && echo PASS || echo FAIL) (rc=$RC)"
exit $RC
