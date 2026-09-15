#!/usr/bin/env bash
# The window shadow as a REAL KWin draws it — inside the image, no VM. kwin_wayland 6.6 with its virtual backend composites with
# OpenGL through GBM on the host's DRM render node (podman --device /dev/dri/renderD*), loads the KDE-Rounded-Corners effect
# (kwin4_effect_shapecorners) and decorates a probe window with THIS checkout's Aurorae theme under THIS checkout's kwinrc;
# spectacle grabs the screen and tests/corners-sample.py measures corners, diagonals and edges against a reference grab of the bare
# backdrop — the same sampler tests/corners-vm.sh runs on the booted VM. This is the gate for the chain the ADR-0019 amendment
# relies on (flat Aurorae frame with an alpha-1/255 carrier -> KWin's decoration-shadow texture -> the effect's offscreen texture ->
# shader `if (tex.a == 0.0) return tex;`): with UseNativeDecorationShadows=false the effect's shadow is the only one, so "shadow
# present" here means the whole chain works in a composited KWin, and "no shadow" would mean every window ships without one.
#   tests/corners-live-test.sh [--scheme dark|light] [--set Group/Key=Value]... [--control] [--image IMG] [--radius N]
#     --scheme   one scheme only (default: dark, then light — Fab Dark / Fab Light colour schemes + FabOS / FabOSLight frames)
#     --set      kwinrc override inside the session (kwriteconfig6), e.g. --set Round-Corners/ShadowSize=45 — for experiments
#     --control  negative control: the frame's carrier is set to alpha 0; the run PASSES only if the effect then draws NO shadow
#                (shows the carrier is what gives the effect pixels to paint, not a coincidence); output under build/corners-live-control/
# Needs: podman with localhost/fabos:vm, a DRM render node (/dev/dri/renderD*; without one KWin's virtual backend falls back to
# QPainter and loads no effect — measured 2026-09-16: "Compositing Type: QPainter", shapecorners not loaded), python3 with Pillow.
# Output: build/corners-live.out (everything) and, under build/corners-live/: <scheme>.log (the session), <scheme>-{ref,active,inactive}.png,
#         <scheme>-{active,inactive}.sample (the sampler tables), <scheme>-corners.png (4x corner crops of active / inactive / reference, to look at)
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
IMG=localhost/fabos:vm; SCHEMES="dark light"; CONTROL=0; SETS=""; R=14
while [ $# -gt 0 ]; do case "$1" in
  --scheme) SCHEMES=$2; shift;; --set) SETS="${SETS:+$SETS;}$2"; shift;; --control) CONTROL=1;; --image) IMG=$2; shift;;
  --radius) R=$2; shift;; -h|--help) sed -n '2,19p' "$0"; exit 0;; *) echo "unknown argument: $1"; exit 3;; esac; shift; done
H=$ROOT/tests/corners-live-harness
# its own output directory: podman relabels the mount (:Z) for each container, so it must not be shared with another test's container
NAME=corners-live; [ $CONTROL = 1 ] && NAME=corners-live-control
OUT=$ROOT/build/$NAME; mkdir -p "$OUT"
LOG=$ROOT/build/$NAME.out; : > "$LOG"; exec > >(tee -a "$LOG") 2>&1
command -v podman >/dev/null || { echo "podman is needed"; exit 3; }
python3 -c "import PIL" 2>/dev/null || { echo "python3-pillow (PIL) is needed on the host"; exit 3; }
NODE=$(ls /dev/dri/renderD* 2>/dev/null | head -1)
[ -n "$NODE" ] || { echo "no DRM render node (/dev/dri/renderD*): KWin's virtual backend composites with OpenGL only through GBM on a render node; without one it falls back to QPainter and loads no effect"; exit 3; }
pass=0; fail=0; verdict() { if [ "$1" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); fi; echo ">>> $1: $2"; }
TH=$(sed -n 's/^TitleHeight=//p' packages/fabos-desktop/usr/share/aurorae/themes/FabOS/FabOSrc); TH=${TH:-36}
CARRIER_OFF=""; [ $CONTROL = 1 ] && CARRIER_OFF=1
echo "### Fab OS live shadow test (KWin virtual backend in $IMG) — $(date -u +%FT%TZ) — render node $NODE, radius $R, title bar $TH px${SETS:+, overrides: $SETS}${CARRIER_OFF:+, NEGATIVE CONTROL: carrier off}"
for scheme in $SCHEMES; do
  echo; echo "=== scheme $scheme"
  rm -f "$OUT/$scheme-ref.png" "$OUT/$scheme-active.png" "$OUT/$scheme-inactive.png"
  timeout 240 podman run --rm --device "$NODE" -e SCHEME="$scheme" -e KWINRC_SET="$SETS" -e CARRIER_OFF="$CARRIER_OFF" \
    -v "$ROOT/packages/fabos-desktop:/src:ro,Z" -v "$H:/harness:ro,Z" -v "$OUT:/out:Z" "$IMG" /harness/session.sh > "$OUT/$scheme.log" 2>&1
  echo "podman exit=$?"; sed 's/^/    /' "$OUT/$scheme.log"
  if grep -q 'shapecorners loaded=1' "$OUT/$scheme.log" && grep -q 'Compositing Type: OpenGL' "$OUT/$scheme.log"; then
    verdict PASS "$scheme: KWin composites with OpenGL ($(sed -n 's/.*OpenGL renderer string: \([^;]*\);.*/\1/p' "$OUT/$scheme.log" | head -1)) and kwin4_effect_shapecorners is loaded"
  else verdict FAIL "$scheme: effect not loaded or no OpenGL compositing (see the session log above)"; continue; fi
  if grep -q 'UseNativeDecorationShadows=false' "$OUT/$scheme.log"; then verdict PASS "$scheme: KWin reads UseNativeDecorationShadows=false (the effect's shadow is the one shadow)"
  else verdict FAIL "$scheme: KWin does not see UseNativeDecorationShadows=false"; fi
  missing=0; for f in ref active inactive; do [ -s "$OUT/$scheme-$f.png" ] || { verdict FAIL "$scheme: no $f screenshot"; missing=1; }; done
  [ $missing = 0 ] || continue
  geo=$(python3 "$H/geo.py" "$OUT/$scheme-active.png" "$TH") || { verdict FAIL "$scheme: probe window not found in the screenshot"; continue; }
  echo "frame geometry (x y w h): $geo"
  for state in active inactive; do
    echo "--- $state vs ref (tests/corners-sample.py)"
    # shellcheck disable=SC2086
    if timeout 120 python3 tests/corners-sample.py "$OUT/$scheme-$state.png" "$OUT/$scheme-ref.png" $geo "$R" > "$OUT/$scheme-$state.sample"; then res=PASS; else res=FAIL; fi
    sed 's/^/    /' "$OUT/$scheme-$state.sample"
    present=$(sed -n 's/^shadow present: .*dev = \([0-9]*\) \/ \([0-9]*\).*/\1 \2/p' "$OUT/$scheme-$state.sample")
    if [ $CONTROL = 1 ]; then    # control: with no carrier the effect must have painted nothing outside the frame
      if [ -n "$present" ] && [ "$(echo "$present" | awk '{print ($1 < 6 && $2 < 6) ? "none" : "shadow"}')" = none ]; then
        verdict PASS "$scheme $state: carrier off -> no shadow (2 px outside top/bottom dev $present): the carrier is what the effect paints into"
      else verdict FAIL "$scheme $state: carrier off but a shadow is still there (2 px outside top/bottom dev ${present:-?})"; fi
    elif [ $res = PASS ]; then verdict PASS "$scheme $state: four radius-$R corners, one soft round shadow, no rectangle, no step before the curve (2 px outside top/bottom dev $present)"
    else verdict FAIL "$scheme $state: sampling failed (see the values above)"; fi
  done
  # shellcheck disable=SC2086
  python3 "$H/crops.py" "$OUT" "$scheme" $geo
done
echo; echo "### $NAME: $pass PASS, $fail FAIL  (log: $LOG)"
[ $fail -eq 0 ]
