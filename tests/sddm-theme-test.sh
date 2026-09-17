#!/usr/bin/env bash
# SDDM greeter theme check, run inside the built image (sddm-greeter-qt6, QT_QPA_PLATFORM=offscreen):
#   1. static: theme.conf keys name files that ship in the theme, metadata MainScript exists, QML imports are QUALIFIED
#      (an unqualified QtQuick.Controls + SddmComponents pair is what made `Button` resolve to SddmComponents.Button and
#      the 1.0-5 laptop greeter fall back to SDDM's red embedded theme), no user-facing forbidden words;
#   2. loads the theme for SECS seconds in `sddm-greeter-qt6 --test-mode --theme <dir>` and FAILS on any QML diagnostic
#      (Cannot assign | is not a type | Type … unavailable | Error | error | TypeError | ReferenceError | Fallback to
#      embedded theme …). Test mode has no daemon socket, so exactly one known line is ignored:
#      `Socket error:  "QLocalSocket::connectToServer: Invalid name"`;
#   3. renders the greeter stages through tests/sddm-theme-harness/Driver.qml (a temp copy of the theme with one Loader
#      line appended) to build/sddm-theme-test/greeter-*.png at 1366x768 — users tile, password (hidden / revealed),
#      wrong-password feedback, power menu, session chooser, "Not listed?" username stage — and checks each PNG exists;
#   4. qmllint, when the image has it (Ubuntu's qt6-declarative-dev-tools is not in the image: SKIP is reported, not PASS).
# Usage: tests/sddm-theme-test.sh [theme-dir]        (default packages/fabos-desktop/usr/share/sddm/themes/fabos)
# Env:   IMAGE=localhost/fabos:vm  SECS=8  RENDER=1 (0 skips the render stage)
# The old Main.qml (git 4bbe387) fails stage 2 with the exact laptop error; the current one passes — see docs/design/LOGIN-BOOT.md.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
THEME=${1:-packages/fabos-desktop/usr/share/sddm/themes/fabos}; THEME=${THEME%/}
IMAGE=${IMAGE:-localhost/fabos:vm}; SECS=${SECS:-8}; RENDER=${RENDER:-1}
OUT=build/sddm-theme-test; mkdir -p "$OUT"; rm -f "$OUT"/greeter-*.png "$OUT"/*.log
fail=0
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
case "$THEME" in /*) echo "theme dir must be inside the repository (relative path)"; exit 2;; esac
[ -f "$THEME/Main.qml" ] || { echo "FAIL  no $THEME/Main.qml"; exit 1; }

echo "== 1. static checks on $THEME"
main=$(sed -n 's/^MainScript=//p' "$THEME/metadata.desktop" 2>/dev/null); main=${main:-Main.qml}
chk "metadata MainScript ($main) exists"        "test -f '$THEME/$main'"
missing=""
while IFS='=' read -r k v; do
  case "$k" in ""|"#"*|"["*) continue;; esac
  case "$v" in *.png|*.svg|*.jpg|*.qml) [ -f "$THEME/$v" ] || [ -f "$v" ] || missing="$missing $k=$v";; esac
done < <(sed 's/[[:space:]]*$//' "$THEME/theme.conf" 2>/dev/null)
chk "theme.conf file keys all shipped${missing:+ (missing:$missing)}" "test -z '$missing'"
chk "QtQuick.Controls imported qualified"      "grep -qE '^import QtQuick.Controls( [0-9.]+)? as [A-Z]' '$THEME/Main.qml'"
chk "SddmComponents imported qualified"        "! grep -qE '^import SddmComponents( [0-9.]+)?\$' '$THEME/Main.qml'"
chk "no unqualified Button/TextField/ComboBox" "! grep -qE '^[[:space:]]*(Button|TextField|ComboBox|PasswordBox|TextBox) *\{' '$THEME/Main.qml'"
chk "background under 1 MB"                    "[ \$(stat -c %s '$THEME/backdrop.png' 2>/dev/null || echo 0) -lt 1000000 ]"
chk "no forbidden wording"                     "! grep -rniE 'chatgpt|openai|gpt|snowui|sora|dall|download' '$THEME' --include=*.qml --include=*.conf --include=*.desktop"

echo "== 2. load in sddm-greeter-qt6 --test-mode for ${SECS}s (offscreen)"
LOG=$OUT/greeter-test-mode.log
podman run --rm --network none -v "$ROOT:/work:Z" -e QT_QPA_PLATFORM=offscreen -e HOME=/tmp -w /tmp "$IMAGE" \
  bash -c "timeout $SECS sddm-greeter-qt6 --test-mode --theme /work/$THEME 2>&1; echo \"exit=\$?\"" > "$LOG" 2>&1
ALLOW='^Socket error:  "QLocalSocket::connectToServer: Invalid name"'
BAD='\.qml:[0-9]+:[0-9]+:|Cannot assign|is not a type|unavailable|Error|error|TypeError|ReferenceError|is not defined|is not a function|Fallback to embedded theme|Unable to assign|Binding loop|Could not|Failed|warning'
bad=$(grep -vE "$ALLOW" "$LOG" | grep -E "$BAD" || true)
chk "greeter loaded ($LOG)"                    "grep -q '^Loading file:///work/$THEME/$main' '$LOG' && grep -q '^Adding view for' '$LOG'"
chk "no QML errors or warnings"                "test -z \"\$bad\""
[ -n "$bad" ] && echo "$bad" | sed 's/^/      /' | head -20
chk "greeter ran until the timeout (no crash)" "grep -q '^exit=124\$' '$LOG'"

if [ "$RENDER" = 1 ]; then
  echo "== 3. render the stages (harness copy of the theme)"
  H=build/sddm-theme-test/theme; rm -rf "$H"; mkdir -p "$H/harness"; cp -a "$THEME"/. "$H/"; cp tests/sddm-theme-harness/Driver.qml "$H/harness/"
  # the copy gets the brand names/font filled in like build-debs.sh does, so the renders show the shipped wording
  ( . brand/brand.conf; sed -i -e "s|@DISTRO_NAME@|$DISTRO_NAME|g" -e "s|@VENDOR_NAME@|$VENDOR_NAME|g" -e "s|@UI_FONT@|$UI_FONT|g" -e "s|@HOME_URL@|$HOME_URL|g" "$H/$main" "$H/metadata.desktop" )
  # append ONE Loader line inside the root item (the last line of Main.qml is the root's closing brace)
  python3 - "$H/$main" <<'PY'
import sys; p = sys.argv[1]; s = open(p).read().rstrip()
assert s.endswith("}"), "Main.qml must end with the root item's closing brace"
line = ('    Loader { source: "harness/Driver.qml"; onLoaded: { item.rootItem = root; item.passwordField = password; '
        'item.errorLabel = errorText; item.shakeAnim = shake; item.sessionSheet = sessionPopup; item.powerSheet = powerPopup } }\n')
open(p, "w").write(s[:-1] + line + "}\n")
PY
  RLOG=$OUT/greeter-render.log
  podman run --rm --network none -v "$ROOT:/work:Z" -e QT_QPA_PLATFORM=offscreen -e HOME=/tmp -w /tmp "$IMAGE" \
    bash -c "timeout 60 sddm-greeter-qt6 --test-mode --theme /work/$H 2>&1; echo \"exit=\$?\"" > "$RLOG" 2>&1
  chk "harness finished"                       "grep -q 'HARNESS done' '$RLOG'"
  rbad=$(grep -vE "$ALLOW" "$RLOG" | grep -E "$BAD" | grep -v '^qml: HARNESS' || true)
  chk "no QML errors while rendering"          "test -z \"\$rbad\""
  [ -n "$rbad" ] && echo "$rbad" | sed 's/^/      /' | head -20
  for s in users password password-reveal error info power session username; do
    chk "render greeter-$s.png"                "[ \$(stat -c %s '$OUT/greeter-$s.png' 2>/dev/null || echo 0) -gt 20000 ]"
  done
  grep '^qml: HARNESS users=' "$RLOG" | sed 's/^/      /'
fi

echo "== 4. qmllint"
if podman run --rm --network none "$IMAGE" bash -c 'command -v qmllint >/dev/null 2>&1'; then
  podman run --rm --network none -v "$ROOT:/work:Z" "$IMAGE" bash -c "qmllint -I /usr/lib/x86_64-linux-gnu/qt6/qml /work/$THEME/$main" > "$OUT/qmllint.log" 2>&1
  chk "qmllint clean" "! grep -qiE 'error|warning' '$OUT/qmllint.log'"
else
  echo "SKIP  qmllint (not in $IMAGE; qt6-declarative-dev-tools is not installed there)"
fi
exit $fail
