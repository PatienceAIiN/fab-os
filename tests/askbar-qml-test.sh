#!/bin/bash
# Headless QML check for the ask bar plasmoid, run inside the built image with plasmawindowed (QT_QPA_PLATFORM=offscreen):
#   1. loads the real applet for 25 s and fails on any QML diagnostic (file.qml:line) — compile errors, binding loops, …
#   2. builds a temp copy of the plasmoid whose main.qml gets ONE appended Loader line that hands the root item and its
#      ids to tests/askbar-qml-harness/Driver.qml; the driver feeds main.qml daemon-shaped JSON, checks the rows it
#      builds and the in-applet response panel (geometry flush under the card, growth / fold animation, no
#      PlasmaCore.Dialog on the desktop, no pointer handler on the transparent strip, the containment hit mask so a
#      right-click on the empty strip is the desktop's), the remembered conversation incl. the service-not-up retry, runs a real
#      `fabos-voice listen-once` for the mic feedback, instantiates every ConvoDelegate kind / AiMark state and renders
#      build/askbar-{bar,panel,feed}.png (bar = the whole strip: card + panel stack), feeds a 40-row conversation with
#      long lines and one-line code to check the scroll geometry (panel grows to its max with the bottom anchored, then
#      the 6 px overlay bar sits in the 14 px right gutter with no row under it, the header row stays fixed at y = 0,
#      the view follows new rows only while at the end; renders build/askbar-scroll-{bottom,top}.png), then shrinks the
#      window to a panel thickness to exercise the compact form for real (PlasmaCore.Dialog appears, the mic hint moves
#      into the placeholder, the cloud hint chip becomes the popup's first header row — the 36 px card has no room;
#      renders build/askbar-compact-hint.png), and
#      finally the polish stages: Do it disabled on an empty / whitespace field (40 %, no
#      hover, Enter ignored, mic still live), the cloud hint chip for the built-in model (dismissal remembered, never with
#      a cloud provider), and the image cards — a generate_image step pointing at a REAL PNG written inside the image by
#      mkpng.py (no python3-pil there) renders a card once the `[ -f ]` check answered, a missing file renders none, the
#      final text mentioning the same file adds no second card, a tap opens the 80 % viewer with its control row
#      (Copy image disabled with a reason: no wl-copy in the image; wallpaper / Fab Photos enabled: their binaries are
#      there), Regenerate posts the follow-up (and while that POST is in flight a second Regenerate closes the viewer and
#      says so — never a silent no-op), Save as falls back to a real copy in ~/Pictures; renders
#      build/askbar-image-{card,viewer}.png.
#   tests/askbar-qml-test.sh [image]        (default localhost/fabos:vm)
set -u
IMG=${1:-localhost/fabos:vm}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PKG=$ROOT/packages/fabos-agent/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar
OUT=$ROOT/build; mkdir -p "$OUT"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H=$T/share/plasma/plasmoids/in.patienceai.fabos.askbartest
mkdir -p "$H/contents/harness"
sed 's/"in.patienceai.fabos.askbar"/"in.patienceai.fabos.askbartest"/' "$PKG/metadata.json" > "$H/metadata.json"
cp -r "$PKG/contents/ui" "$H/contents/ui"
cp -r "$PKG/contents/config" "$H/contents/config"
cp "$ROOT/tests/askbar-qml-harness/Driver.qml" "$H/contents/harness/"
cp "$ROOT/tests/askbar-qml-harness/mkpng.py" "$T/mkpng.py"
python3 - "$H/contents/ui/main.qml" <<'EOF'
import sys
p = sys.argv[1]; s = open(p).read().rstrip("\n")
assert s.endswith("}"), "main.qml must end with the root's closing brace"
s = s[:-1] + ('    Loader { source: Qt.resolvedUrl("../harness/Driver.qml"); onLoaded: { item.bar = root; item.convo = convo; item.card = card; item.panel = panel; '
              'item.panelMain = panelMain; item.popup = popupLoader; item.statusText = statusText; item.field = field; item.vbar = vbar; item.panelHeader = panelHeader; item.panelFoot = panelFoot; '
              'item.go = go; item.goArea = goArea; item.micButton = micButton; item.cloudHint = cloudHint; item.cloudHintText = cloudHint.label; item.cloudChoose = cloudHint.chooseItem; item.compactHint = compactHint; item.viewer = viewerLoader; item.list = list } }\n}\n')
open(p, "w").write(s)
EOF
fail=0
run() {   # $1 label, $2 applet id, $3 timeout seconds, $4 extra XDG_DATA_DIRS prefix
  echo "== $1"
  podman run --rm -e QT_QPA_PLATFORM=offscreen -e HOME=/tmp -e XDG_RUNTIME_DIR=/tmp/xdg -e XDG_DATA_DIRS="$4/usr/local/share:/usr/share" \
    -v "$T:/harness:ro,Z" -v "$OUT:/out:Z" -v "$PKG/contents:/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents:ro,Z" "$IMG" \
    bash -c "mkdir -p /tmp/xdg && chmod 700 /tmp/xdg; mkdir -p '/tmp/Pictures/Fab OS' && python3 /harness/mkpng.py '/tmp/Pictures/Fab OS/askbar-test.png' >/dev/null; timeout $3 dbus-run-session plasmawindowed $2 2>&1; echo exit=\$?" > "$T/$2.log" 2>&1
  grep -vE "dbus-daemon|kglobalaccel|kf.windowsystem|propagateSizeHints|KWindowShadow|QProcess: Destroyed|Loading default layout|^qml: (396|700)$|^$" "$T/$2.log"
}
run "real applet loads (25 s soak; any file.qml:line diagnostic fails)" in.patienceai.fabos.askbar 25 ""
grep -qE "\.(qml|js):[0-9]+" "$T/in.patienceai.fabos.askbar.log" && { echo "FAIL: QML diagnostics while loading the applet"; fail=1; }
run "harness (Driver.qml drives the real main.qml)" in.patienceai.fabos.askbartest 60 "/harness/share:"
grep -q "HARNESS DONE failures=0" "$T/in.patienceai.fabos.askbartest.log" || { echo "FAIL: harness did not finish clean"; fail=1; }
grep -qE "\.(qml|js):[0-9]+" "$T/in.patienceai.fabos.askbartest.log" && { echo "FAIL: QML diagnostics in the harness run"; fail=1; }
checks=$(grep -cE "^qml: (PASS|FAIL) " "$T/in.patienceai.fabos.askbartest.log")
# the thumbnail really painted, and upright: sample the render where the harness said the thumbnail sits (sky above, hill below)
thumb=$(grep -E "^qml: THUMB " "$T/in.patienceai.fabos.askbartest.log" | tail -1 | cut -d' ' -f3-)
if [ -n "$thumb" ] && python3 "$ROOT/tests/askbar-qml-harness/pngcheck.py" "$OUT/askbar-image-card.png" $thumb; then echo "PASS thumbnail pixels: blue sky on top, green hill below (rect $thumb)"; checks=$((checks + 1))
else echo "FAIL: thumbnail pixels not as expected (rect '$thumb')"; fail=1; fi
python3 - "$OUT" <<'EOF'
import struct, sys, os
for n in ("bar", "panel", "feed", "scroll-bottom", "scroll-top", "compact-hint", "image-card", "image-viewer"):   # PNG IHDR: width, height right after the 8-byte signature + 8-byte chunk header
    p = os.path.join(sys.argv[1], "askbar-%s.png" % n)
    try:
        with open(p, "rb") as f: w, h = struct.unpack(">II", f.read(24)[16:24])
        print("render askbar-%s.png %dx%d" % (n, w, h))
    except OSError: print("render askbar-%s.png MISSING" % n)
EOF
echo "askbar-qml-test: $([ $fail = 0 ] && echo PASS || echo FAIL)  checks=$checks  (renders: $OUT/askbar-{bar,panel,feed,scroll-bottom,scroll-top,compact-hint,image-card,image-viewer}.png)"
exit $fail
