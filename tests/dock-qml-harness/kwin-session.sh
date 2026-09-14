#!/bin/bash
# Inside the image: run `plasmawindowed <applet>` as the session of a virtual, software-rendered kwin_wayland, so
# libtaskmanager's WindowTasksModel has a real windowing backend (offscreen it has none: zero columns, and the task
# filter proxy then rejects every row, launchers included) and so a REAL pointer can be driven: the harness runs
# tests/quicksettings-qml-harness/fakeinput.py, a raw Wayland client of KWin's org_kde_kwin_fake_input. KWin hands that
# global only to a trusted client: the FIRST .desktop file whose Exec is the client's /proc/pid/exe must list the interface
# in X-KDE-Wayland-Interfaces. The image's python3.14.desktop has Exec=/usr/bin/python3.14 too and would win at random, so
# the injector runs from a private copy of the interpreter (/tmp/fabos-pointer, PYTHONHOME=/usr) that only our file names.
#   kwin-session.sh <applet id> <seconds> [light|dark] [screen height]   (colour scheme: FabLight / FabDark copied into ~/.config/kdeglobals)
export HOME=/tmp XDG_RUNTIME_DIR=/tmp/xdg XDG_MENU_PREFIX=plasma-
SCREEN_H=${4:-400}
cp "$(readlink -f /usr/bin/python3)" /tmp/fabos-pointer
mkdir -p /tmp/.local/share/applications
printf '[Desktop Entry]\nType=Application\nName=Fab OS applet harness pointer\nExec=/tmp/fabos-pointer\nX-KDE-Wayland-Interfaces=org_kde_kwin_fake_input\n' > /tmp/.local/share/applications/fabos-harness-pointer.desktop
kbuildsycoca6 --noincremental >/dev/null 2>&1
mkdir -p /tmp/.config && cp "/usr/share/color-schemes/$([ "${3:-light}" = dark ] && echo FabDark || echo FabLight).colors" /tmp/.config/kdeglobals && printf '\n[Icons]\nTheme=FabOS\n' >> /tmp/.config/kdeglobals
export KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software
export QT_QPA_PLATFORMTHEME=kde   # as in a Plasma session: QIcon::fromTheme goes through KIconLoader, so FabOS-only app icons resolve
mkdir -p /tmp/xdg /tmp/.X11-unix && chmod 700 /tmp/xdg && chmod 1777 /tmp/.X11-unix
cat > /tmp/session.sh <<EOF
#!/bin/bash
export QT_QPA_PLATFORM=wayland
${HARNESS_WAYLAND_DEBUG:+export WAYLAND_DEBUG=1}   # HARNESS_WAYLAND_DEBUG=1: libwayland-client traces every event plasmawindowed receives
timeout $2 plasmawindowed $1 2>&1
echo "plasmawindowed exit=\$?"
EOF
chmod +x /tmp/session.sh
# /usr/bin/kwin_wayland carries file capabilities (cap_sys_nice) that a rootless container cannot exec; a plain copy has none.
cp /usr/bin/kwin_wayland /tmp/kwin_wayland
timeout $(( $2 + 25 )) dbus-run-session -- /tmp/kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --width 1280 --height "$SCREEN_H" --exit-with-session /tmp/session.sh 2>&1
echo "kwin exit=$?"
