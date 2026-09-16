#!/bin/bash
# Inside the image: the leave screen with a REAL window list. A virtual, software-rendered kwin_wayland runs a session
# script that opens testwin.py's two Wayland windows (one with Qt's modified marker in its title) and then runs the
# REAL greeter through greeter-run.sh (harness copy of the package, Driver.qml attached) prompted for a shutdown.
# KWin hands org_kde_plasma_window_management only to a client whose .desktop entry (found through the sycoca, Exec ==
# /proc/<pid>/exe) lists it in X-KDE-Wayland-Interfaces; the harness puts fabos-desktop's
# in.patienceai.fabos.logout-greeter.desktop into XDG_DATA_HOME/applications and rebuilds the sycoca, exactly what the
# package install does on a real system. No entry -> TasksModel stays empty -> the driver reports PATH apps -> the host FAILs.
#   kwin-leave-session.sh <harness root, e.g. /tmp/h> <seconds for the greeter>
H=$1 SECS=${2:-40}
export HOME=/tmp XDG_RUNTIME_DIR=/tmp/xdg XDG_MENU_PREFIX=plasma- XDG_DATA_HOME=$H/share XDG_CONFIG_HOME=$H/cfg-kwin
export XDG_DATA_DIRS=$H/share:/usr/local/share:/usr/share
mkdir -p /tmp/xdg /tmp/.X11-unix && chmod 700 /tmp/xdg && chmod 1777 /tmp/.X11-unix
kbuildsycoca6 --noincremental >/dev/null 2>&1
export KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software QT_QPA_PLATFORMTHEME=kde PLASMA_SESSION_GUI_TEST=1
cat > /tmp/leave-session.sh <<EOF
#!/bin/bash
export QT_QPA_PLATFORM=wayland
python3 $H/testwin.py &
for i in \$(seq 1 40); do [ -e "\$XDG_RUNTIME_DIR/\$WAYLAND_DISPLAY" ] && break; sleep 0.25; done
sleep 4
$H/greeter-run.sh promptShutDown in.patienceai.fabos.leavetest $SECS "HARNESS DONE"
kill %1 2>/dev/null
EOF
chmod +x /tmp/leave-session.sh
# /usr/bin/kwin_wayland carries file capabilities (cap_sys_nice) that a rootless container cannot exec; a plain copy has none.
cp /usr/bin/kwin_wayland /tmp/kwin_wayland
timeout $(( SECS + 40 )) dbus-run-session -- /tmp/kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --width 1280 --height 800 --exit-with-session /tmp/leave-session.sh 2>&1 \
  | grep -vE '^$|kwin_core: Failed to|kwin_scene_opengl|kwin_wayland_drm|QThreadStorage: entry|does not support grabbing|not in X-KDE-Wayland-Interfaces of "/usr/bin/python'
echo "kwin exit=${PIPESTATUS[0]}"
