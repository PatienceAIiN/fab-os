#!/bin/bash
# Runs INSIDE the virtual kwin_wayland session started by session.sh (--exit-with-session). Waits for KWin's bus name, reports the
# effect and the compositing type, then drives windows.py: a frameless maximised light-gradient "wallpaper" window (the virtual
# backend alone paints black, and a black shadow on black cannot be measured), a 600x400 magenta client with the Fab OS Aurorae
# frame (active), then a small window that takes the focus (the probe window becomes inactive). A full-screen grab after each step
# with the same call tests/corners-vm.sh makes in the VM (spectacle -b -n -f -o). Files: /out/<scheme>-{ref,active,inactive}.png (the host mounts build/corners-live there)
S=${SCHEME:-dark}
for i in $(seq 1 60); do qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.loadedEffects >/dev/null 2>&1 && break; sleep 0.5; done
echo "effects: shapecorners loaded=$(qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.loadedEffects 2>/dev/null | grep -c shapecorners) supported=$(qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.isEffectSupported kwin4_effect_shapecorners 2>/dev/null)"
echo "kwinrc seen by KWin: theme=$(kreadconfig6 --file kwinrc --group org.kde.kdecoration2 --key theme) BorderSize=$(kreadconfig6 --file kwinrc --group org.kde.kdecoration2 --key BorderSize) BorderSizeAuto=$(kreadconfig6 --file kwinrc --group org.kde.kdecoration2 --key BorderSizeAuto) UseNativeDecorationShadows=$(kreadconfig6 --file kwinrc --group Round-Corners --key UseNativeDecorationShadows) ShadowSize=$(kreadconfig6 --file kwinrc --group Round-Corners --key ShadowSize) InactiveShadowSize=$(kreadconfig6 --file kwinrc --group Round-Corners --key InactiveShadowSize) Size=$(kreadconfig6 --file kwinrc --group Round-Corners --key Size)"
qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation 2>/dev/null | grep -E '^(Compositing Type|OpenGL renderer string|OpenGL version string|Geometry:|Scale:)' | tr '\n' ';'; echo
rm -f /tmp/stage-* /tmp/go-*
python3 /harness/windows.py > /tmp/windows.log 2>&1 &
PY=$!
wait_file() { for i in $(seq 1 60); do [ -e "$1" ] && return 0; sleep 0.5; done; echo "timeout waiting for $1"; return 1; }
shot() { # shot <png>: spectacle full screen, batch, no notification, no pointer; waits for the file
  rm -f "$1"; timeout 25 spectacle -b -n -f -o "$1" >/tmp/spectacle.log 2>&1
  for i in $(seq 1 20); do [ -s "$1" ] && break; sleep 0.5; done
  if [ -s "$1" ]; then echo "shot $1 $(stat -c %s "$1") bytes"; else echo "no screenshot $1:"; cat /tmp/spectacle.log; fi; }
wait_file /tmp/stage-bg; sleep 2; shot /tmp/ref.png
touch /tmp/go-fg; wait_file /tmp/stage-fg; sleep 3; shot /tmp/active.png
touch /tmp/go-steal; wait_file /tmp/stage-steal; sleep 3; shot /tmp/inactive.png
echo "windows.py: $(tr '\n' ' ' < /tmp/windows.log)"
kill $PY 2>/dev/null
for f in ref active inactive; do [ -s /tmp/$f.png ] && cp /tmp/$f.png "/out/$S-$f.png"; done
ls -l /out/$S-*.png 2>&1
