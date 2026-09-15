#!/bin/bash
# Container entry for tests/corners-live-test.sh (runs INSIDE localhost/fabos:vm):
#   podman run --rm --device /dev/dri/renderD128 -e SCHEME=dark|light -v <checkout>/packages/fabos-desktop:/src:ro -v <harness>:/harness:ro -v <out>:/out
# A REAL KWin 6.6 (kwin_wayland --virtual, OpenGL through GBM on the render node) with THIS checkout's /etc/xdg/kwinrc and Aurorae
# theme placed in the user's config/data dirs — KConfig lets ~/.config/kwinrc override /etc/xdg/kwinrc key by key, and Aurorae v2
# finds aurorae/themes/<name> in ~/.local/share before /usr/share — so the image's own (older) kwinrc and theme are shadowed by
# the files under test. One colour scheme per run; inner.sh runs inside the session (windows, screenshots).
# Env: SCHEME=dark|light; KWINRC_SET="Group/Key=Value;Group/Key=Value" overrides written with kwriteconfig6 (experiments);
#      CARRIER_OFF=1 negative control: the frame's alpha-1/255 carrier set to alpha 0, so the effect has nothing to shade.
S=${SCHEME:-dark}
mkdir -p /tmp/xr /tmp/h/.config /tmp/h/.local/share/aurorae/themes && chmod 700 /tmp/xr
export XDG_RUNTIME_DIR=/tmp/xr HOME=/tmp/h QT_QPA_PLATFORM=wayland SCHEME=$S
# KWin hands org.kde.KWin.ScreenShot2 only to a caller whose executable matches a .desktop file listing the interface in
# X-KDE-DBUS-Restricted-Interfaces (looked up through ksycoca, which this bare session does not have): spectacle would be refused.
# The screenshot plugin's own test knob (the string is in /usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/plugins/screenshot.so) lifts it here.
export KWIN_SCREENSHOT_NO_PERMISSION_CHECKS=1
cp -a /src/usr/share/aurorae/themes/FabOS /src/usr/share/aurorae/themes/FabOSLight /tmp/h/.local/share/aurorae/themes/
sed -i 's/@DISTRO_NAME@/Fab OS/g; s/@VENDOR_NAME@/Patience AI/g; s/@DISTRO_VERSION@/1.0/g; s#@HOME_URL@#https://fabos.patienceai.in/#g' /tmp/h/.local/share/aurorae/themes/*/metadata.desktop
if [ -n "${CARRIER_OFF:-}" ]; then
  sed -i 's/fill-opacity:0.004/fill-opacity:0/g' /tmp/h/.local/share/aurorae/themes/FabOS/decoration.svg   # FabOSLight's SVG is a symlink to this file
  echo "CONTROL: carrier removed (fill-opacity 0.004 -> 0): $(grep -o 'fill-opacity:0"' /tmp/h/.local/share/aurorae/themes/FabOS/decoration.svg | wc -l) rects at alpha 0, $(grep -o 'fill-opacity:0.004' /tmp/h/.local/share/aurorae/themes/FabOS/decoration.svg | wc -l) left"
fi
cp /src/etc/xdg/kwinrc /tmp/h/.config/kwinrc
CS=FabDark
if [ "$S" = light ]; then CS=FabLight; sed -i 's/^theme=__aurorae__svg__FabOS$/theme=__aurorae__svg__FabOSLight/' /tmp/h/.config/kwinrc; fi
{ printf '[General]\nColorScheme=%s\n' "$CS"; cat "/usr/share/color-schemes/$CS.colors"; } > /tmp/h/.config/kdeglobals
if [ -n "${KWINRC_SET:-}" ]; then
  IFS=';' read -ra sets <<< "$KWINRC_SET"
  for kv in "${sets[@]}"; do g=${kv%%/*}; rest=${kv#*/}; k=${rest%%=*}; v=${rest#*=}; kwriteconfig6 --file kwinrc --group "$g" --key "$k" "$v"; echo "override: [$g] $k=$v"; done
fi
echo "config: kwinrc from /src ($(grep -c . /tmp/h/.config/kwinrc) lines, $(grep '^theme=' /tmp/h/.config/kwinrc), library=$(kreadconfig6 --file kwinrc --group org.kde.kdecoration2 --key library)), kdeglobals $CS ($(grep -c '^\[Colors:' /tmp/h/.config/kdeglobals) colour groups)"
echo "theme under test: $(readlink -f /tmp/h/.local/share/aurorae/themes/FabOSLight/decoration.svg); PaddingTop=$(kreadconfig6 --file /tmp/h/.local/share/aurorae/themes/FabOS/FabOSrc --group Layout --key PaddingTop) TitleHeight=$(kreadconfig6 --file /tmp/h/.local/share/aurorae/themes/FabOS/FabOSrc --group Layout --key TitleHeight) carrier rects=$(grep -o 'fill-opacity:0.004' /tmp/h/.local/share/aurorae/themes/FabOS/decoration.svg | wc -l) gradients/masks/filters=$(grep -c -E 'linearGradient|radialGradient|<mask|<filter' /tmp/h/.local/share/aurorae/themes/FabOS/decoration.svg)"
echo "render node: $(ls /dev/dri/ 2>/dev/null | tr '\n' ' ')"
cp /usr/bin/kwin_wayland /tmp/kwin_wayland   # the real binary carries a file capability (cap_sys_nice) a rootless container cannot exec
timeout 150 dbus-run-session -- /tmp/kwin_wayland --virtual --no-lockscreen --no-global-shortcuts --width 1280 --height 800 --exit-with-session /harness/inner.sh 2>&1 \
  | grep -v -E '^$|qt.dbus.integration|applications.menu|PipeWire|real time thread|kwin_screencast|Failed to gain|xdg-desktop-portal|kf.windowsystem|QSocketNotifier|kwin_xkbcommon'
echo "kwin exit=${PIPESTATUS[0]}"
