#!/bin/bash
# Renders the Fab OS Aurorae frame through the REAL KSvg engine (KSvg.FrameSvgItem inside plasmawindowed, QT_QPA_PLATFORM=offscreen,
# inside the built image), once per colour scheme (Fab Dark, Fab Light: kdeglobals ColorScheme= in a throw-away HOME, which is what
# rewrites the SVG's current-color-scheme stylesheet), and checks the pixels that decide how the frame looks on the live desktop:
#   Aurorae v2 paints decoration.svg offset by the padding into the decoration texture and cuts the shadow at the window box, so
#   the padding ring and the corner "notches" are shown exactly as drawn. Since 2026-09-16 (ADR-0019 amendment) the frame is FLAT:
#   the shadow comes from the KDE-Rounded-Corners effect alone, and the frame's padding + notches must be one uniform alpha-1/255
#   carrier (the effect shades only pixels with alpha > 0) — no gradient, no step, no hole; the title bar opaque; the client area empty.
#   tests/decoration-render-harness/probe.py holds the probes (scans every padding and notch pixel).
# Usage: tests/decoration-render-test.sh [image] [theme-dir]   (defaults: localhost/fabos:vm, the theme in this checkout)
# Output: build/decoration-{dark,light}-{active,inactive}.png (the raw frames, transparent) and
#         build/decoration-preview-{dark,light}.png (the same frames composited over a wallpaper-like backdrop, to LOOK at) + the probe table.
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
for s in FabDark FabLight; do printf '[General]\nColorScheme=%s\n' "$s" > "$T/kdeglobals-$s"; done
fail=0; pngs=""
for scheme in dark light; do
  cs=FabDark; [ $scheme = light ] && cs=FabLight
  rm -f "$OUT/decoration-$scheme-active.png" "$OUT/decoration-$scheme-inactive.png"
  timeout 180 podman run --rm -e QT_QPA_PLATFORM=offscreen -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e XDG_DATA_DIRS="/harness/share:/usr/local/share:/usr/share" \
    -e SCHEME="$cs" -e SCH="$scheme" -v "$T:/harness:ro,Z" -v "$THEME:/theme:ro,Z" -v "$H/probe.py:/probe.py:ro,Z" -v "$OUT:/out:Z" "$IMG" \
    bash -c 'mkdir -p /tmp/xdg /tmp/.config && chmod 700 /tmp/xdg && cat /harness/kdeglobals-$SCHEME /usr/share/color-schemes/$SCHEME.colors > /tmp/.config/kdeglobals;
             echo "kdeglobals: ColorScheme=$SCHEME + $(grep -c "^\[Colors:" /tmp/.config/kdeglobals) colour groups from /usr/share/color-schemes/$SCHEME.colors (KColorScheme reads the groups, not the name)";
             timeout 45 dbus-run-session plasmawindowed in.patienceai.fabos.decorender 2>&1 | grep -E "PREFIXES|MARGINS|SAVED|\.qml:[0-9]";
             mv /out/decoration-active.png /out/decoration-$SCH-active.png && mv /out/decoration-inactive.png /out/decoration-$SCH-inactive.png;
             python3 /probe.py /out/decoration-$SCH-active.png /out/decoration-$SCH-inactive.png' > "$T/log-$scheme" 2>&1
  echo "=== scheme $scheme ($cs)"; cat "$T/log-$scheme"
  grep -q "SAVED inactive" "$T/log-$scheme" || { echo "FAIL: $scheme frames were not rendered"; fail=1; }
  grep -q "RESULT PASS" "$T/log-$scheme" || { echo "FAIL: $scheme pixel probes"; fail=1; }
  grep -qE "\.qml:[0-9]+" "$T/log-$scheme" && { echo "FAIL: $scheme QML diagnostics"; fail=1; }
  pngs="$pngs $OUT/decoration-$scheme-active.png $OUT/decoration-$scheme-inactive.png"
done
# Preview: the transparent frames over a two-tone backdrop (a "wallpaper" band and a plain band), active left, inactive right, 2x —
# the picture a reviewer looks at; the alpha numbers above are the test.
timeout 120 podman run --rm -i -v "$OUT:/out:Z" "$IMG" python3 - <<'PY' 2>&1 | grep -v "^$"
from PyQt6.QtGui import QImage, QPainter, QColor, QLinearGradient
from PyQt6.QtCore import QRectF, Qt
for scheme, bg1, bg2 in (("dark", "#243147", "#0b0f18"), ("light", "#dfe6f3", "#f7f8fb")):
    frames = [QImage("/out/decoration-%s-%s.png" % (scheme, st)) for st in ("active", "inactive")]
    if any(f.isNull() for f in frames):
        print("preview %s: frames missing" % scheme); continue
    W, H = frames[0].width(), frames[0].height()
    out = QImage(2 * (W + 20) + 20, H + 40, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    g = QLinearGradient(0, 0, out.width(), out.height()); g.setColorAt(0, QColor(bg1)); g.setColorAt(1, QColor(bg2))
    p.fillRect(out.rect(), g)
    for i, f in enumerate(frames):
        p.drawImage(20 + i * (W + 20), 20, f)
    p.end()
    out = out.scaled(out.width() * 2, out.height() * 2, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    out.save("/out/decoration-preview-%s.png" % scheme)
    print("preview %s: /out/decoration-preview-%s.png (%dx%d, 2x)" % (scheme, scheme, out.width(), out.height()))
PY
echo "decoration-render-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  (renders:$pngs; previews: $OUT/decoration-preview-{dark,light}.png)"
exit $fail
