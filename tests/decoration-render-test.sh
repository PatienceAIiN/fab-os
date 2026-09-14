#!/bin/bash
# Renders the Fab OS Aurorae frame through the REAL KSvg engine (KSvg.FrameSvgItem inside plasmawindowed, QT_QPA_PLATFORM=offscreen,
# inside the built image) and checks the pixels that decide whether the top corners look rounded on the live desktop:
#   Aurorae v2 paints decoration.svg offset by the shadow padding into the decoration texture, so the "notch" between the arc
#   and the window's bounding-box corner is shown exactly as drawn -> it must be fully transparent (alpha 0); the title bar
#   must be opaque (alpha 1) and the shadow outside the window box must still exist (alpha > 0).
# Usage: tests/decoration-render-test.sh [image] [theme-dir]   (defaults: localhost/fabos:vm, the theme in this checkout)
# Output: build/decoration-active.png, build/decoration-inactive.png and the probe table.
set -u
IMG=${1:-localhost/fabos:vm}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
THEME=${2:-$ROOT/packages/fabos-desktop/usr/share/aurorae/themes/FabOS}
OUT=$ROOT/build; mkdir -p "$OUT"
H=$ROOT/tests/decoration-render-harness
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/share/plasma/plasmoids/in.patienceai.fabos.decorender/contents/ui"
cp "$H/metadata.json" "$T/share/plasma/plasmoids/in.patienceai.fabos.decorender/"
cp "$H/main.qml" "$T/share/plasma/plasmoids/in.patienceai.fabos.decorender/contents/ui/"
podman run --rm -e QT_QPA_PLATFORM=offscreen -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e XDG_DATA_DIRS="/harness/share:/usr/local/share:/usr/share" \
  -v "$T:/harness:ro,Z" -v "$THEME:/theme:ro,Z" -v "$H/probe.py:/probe.py:ro,Z" -v "$OUT:/out:Z" "$IMG" \
  bash -c 'mkdir -p /tmp/xdg && chmod 700 /tmp/xdg; timeout 45 dbus-run-session plasmawindowed in.patienceai.fabos.decorender 2>&1 | grep -E "PREFIXES|MARGINS|SAVED|\.qml:[0-9]"; python3 /probe.py /out/decoration-active.png /out/decoration-inactive.png' > "$T/log" 2>&1
cat "$T/log"
fail=0
grep -q "SAVED inactive" "$T/log" || { echo "FAIL: frames were not rendered"; fail=1; }
grep -q "RESULT PASS" "$T/log" || { echo "FAIL: pixel probes"; fail=1; }
grep -qE "\.qml:[0-9]+" "$T/log" && { echo "FAIL: QML diagnostics"; fail=1; }
echo "decoration-render-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  (renders: $OUT/decoration-{active,inactive}.png)"
exit $fail
