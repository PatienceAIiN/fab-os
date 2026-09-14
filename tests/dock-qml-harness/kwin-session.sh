#!/bin/bash
# Inside the image: run `plasmawindowed <applet>` as the session of a virtual, software-rendered kwin_wayland, so
# libtaskmanager's WindowTasksModel has a real windowing backend (offscreen it has none: zero columns, and the task
# filter proxy then rejects every row, launchers included). Used by tests/desktop-applets-qml-test.sh for the dock.
#   kwin-session.sh <applet id> <seconds>
export HOME=/tmp XDG_RUNTIME_DIR=/tmp/xdg XDG_MENU_PREFIX=plasma-
export KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software
export QT_QPA_PLATFORMTHEME=kde   # as in a Plasma session: QIcon::fromTheme goes through KIconLoader, so FabOS-only app icons resolve
mkdir -p /tmp/xdg /tmp/.X11-unix && chmod 700 /tmp/xdg && chmod 1777 /tmp/.X11-unix
cat > /tmp/session.sh <<EOF
#!/bin/bash
export QT_QPA_PLATFORM=wayland
timeout $2 plasmawindowed $1 2>&1
echo "plasmawindowed exit=\$?"
EOF
chmod +x /tmp/session.sh
# /usr/bin/kwin_wayland carries file capabilities (cap_sys_nice) that a rootless container cannot exec; a plain copy has none.
cp /usr/bin/kwin_wayland /tmp/kwin_wayland
timeout $(( $2 + 25 )) dbus-run-session -- /tmp/kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --width 1280 --height 400 --exit-with-session /tmp/session.sh 2>&1
echo "kwin exit=$?"
