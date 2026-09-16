#!/usr/bin/env bash
# Render the Fab OS Updates window offscreen inside the image with a pending post-upgrade state and prove the banner
# appears with the right wording (docs/UPDATES.md §4): "Restart to finish" (fabos-branding changed), "Log out and back in
# to finish" (fabos-desktop changed), and no banner when nothing is pending. The package tree is rendered from
# brand/brand.conf like packages/build-debs.sh does, so the strings are the shipped ones.
#   tests/updates-banner-render.sh [--out DIR]     PNGs: DIR/updates-banner-{restart,logout,none}.png (default build/)
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
OUT=$HERE/build; while [ $# -gt 0 ]; do case $1 in --out) OUT=$2; shift;; esac; shift; done
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
podman image exists localhost/fabos:vm || { echo "podman image localhost/fabos:vm missing"; exit 2; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
cat > "$W/inner.sh" <<'EOF'
#!/bin/bash
set -u
# shellcheck disable=SC1091
. /work/brand/brand.conf
mkdir -p /tmp/u /tmp/bin /tmp/state; cp /work/packages/fabos-updates/usr/lib/fabos/updates/*.py /tmp/u/
for f in /tmp/u/*.py; do for v in DISTRO_NAME VENDOR_NAME DISTRO_CODENAME DOCS_URL; do sed -i "s|@$v@|${!v}|g" "$f"; done; done
grep -q '@[A-Z_]*@' /tmp/u/*.py && { echo "FAIL  unrendered placeholder in the python sources"; grep -n '@[A-Z_]*@' /tmp/u/*.py; exit 1; }
# the container runs the host's kernel: make the kernel check a no-op so only the journal decides
printf '#!/bin/sh\nuname -r\n' > /tmp/bin/linux-version; chmod +x /tmp/bin/linux-version
export PATH=/tmp/bin:$PATH QT_QPA_PLATFORM=offscreen FABOS_UPDATES_STATE_DIR=/tmp/state HOME=/tmp
rm -f /run/reboot-required   # baked into the image's /run by a kernel postinst at build time; a booted system starts with an empty /run
boot=$(cat /proc/sys/kernel/random/boot_id); mono=$(python3 -c 'import time; print("%.3f" % (time.clock_gettime(time.CLOCK_MONOTONIC)))')
fail=0
render() { # render NAME CLASSES PACKAGES
  rm -f /tmp/state/journal; [ -n "$2" ] && echo "time=$(date +%s) mono=$mono boot=$boot version=1.0-7 packages=$3 classes=$2" > /tmp/state/journal
  timeout 90 python3 /tmp/u/fabos_updates.py --screenshot "/out/updates-banner-$1.png" >/dev/null 2>&1; rc=$?
  st=$(python3 /tmp/u/state.py state --no-apt 2>/dev/null | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["banner"])')
  echo "state[$1]: banner='$st' (render exit $rc)"
  # banner border colour #E0A64B (224,166,75): a 1 px frame around the banner is ~1400 px; none when hidden
  n=$(python3 - "/out/updates-banner-$1.png" <<'PY'
import sys; from PyQt6.QtGui import QImage
im = QImage(sys.argv[1]); n = 0
for y in range(im.height()):
    for x in range(im.width()):
        c = im.pixelColor(x, y)
        if abs(c.red() - 224) < 14 and abs(c.green() - 166) < 14 and abs(c.blue() - 75) < 14: n += 1
print("%d %dx%d" % (n, im.width(), im.height()))
PY
); size=${n#* }; n=${n%% *}
  echo "banner-colour pixels[$1]: $n  ($size)"
  echo "$st|$n"
}
r=$(render restart "reboot,session,agent" "fabos-agent,fabos-branding,fabos-desktop,fabos-updates"); echo "$r" | head -2; st=${r##*$'\n'}; n=${st##*|}; st=${st%|*}
if [ "$st" = "$DISTRO_NAME 1.0-7 is installed. Restart to finish." ] && [ "$n" -gt 300 ]; then echo "PASS  restart banner rendered: '$st'"; else echo "FAIL  restart banner ('$st', $n px)"; fail=1; fi
r=$(render logout "session" "fabos-desktop"); echo "$r" | head -2; st=${r##*$'\n'}; n=${st##*|}; st=${st%|*}
if [ "$st" = "$DISTRO_NAME 1.0-7 is installed. Log out and back in to finish." ] && [ "$n" -gt 300 ]; then echo "PASS  log-out banner rendered: '$st'"; else echo "FAIL  log-out banner ('$st', $n px)"; fail=1; fi
r=$(render none "" ""); echo "$r" | head -2; st=${r##*$'\n'}; n=${st##*|}; st=${st%|*}
if [ -z "$st" ] && [ "$n" -lt 40 ]; then echo "PASS  no banner when nothing is pending ($n px)"; else echo "FAIL  banner shown with nothing pending ('$st', $n px)"; fail=1; fi
# the apt configuration fragment must parse: a syntax error in /etc/apt/apt.conf.d breaks EVERY apt call on the installed system
cp /work/packages/fabos-updates/etc/apt/apt.conf.d/52fabos-unattended /tmp/52fabos-unattended
DISTRO_ID_TITLE="$(echo "${DISTRO_ID:0:1}" | tr a-z A-Z)${DISTRO_ID:1}"
for v in DISTRO_NAME VENDOR_NAME DISTRO_CODENAME DISTRO_ID_TITLE BASE_CODENAME; do sed -i "s|@$v@|${!v}|g" /tmp/52fabos-unattended; done
if grep -q '@[A-Z_]*@' /tmp/52fabos-unattended; then echo "FAIL  unrendered placeholder in 52fabos-unattended"; fail=1; fi
dump=$(apt-config -c /tmp/52fabos-unattended dump Unattended-Upgrade 2>/tmp/apt-config.err)
if echo "$dump" | grep -q "Allowed-Origins:: \"Ubuntu:$BASE_CODENAME-security\"" && echo "$dump" | grep -q 'Origins-Pattern:: "site=packages.mozilla.org"' && echo "$dump" | grep -q "Allowed-Origins:: \"$VENDOR_NAME:$DISTRO_CODENAME\"" && [ ! -s /tmp/apt-config.err ]; then
  echo "PASS  52fabos-unattended parses (apt-config) and names $VENDOR_NAME:$DISTRO_CODENAME, Ubuntu:$BASE_CODENAME-security, site=packages.mozilla.org"
else echo "FAIL  52fabos-unattended does not parse or lacks the origins: $(cat /tmp/apt-config.err)"; echo "$dump" | head -20; fail=1; fi
# wording rules (owner): no forbidden product names, no "download" aimed at users in the update UI strings
if grep -nE 'ChatGPT|OpenAI|GPT|SnowUI|Sora|DALL|[Dd]ownload' /tmp/u/*.py /work/packages/fabos-updates/usr/lib/fabos/updates/helper.sh /work/packages/fabos-updates/usr/lib/systemd/*/* | grep -v 'APT::Periodic::Download' ; then echo "FAIL  forbidden wording in fabos-updates"; fail=1; else echo "PASS  no forbidden wording in fabos-updates"; fi
exit $fail
EOF
chmod +x "$W/inner.sh"
podman run --rm -v "$HERE:/work:Z,ro" -v "$OUT:/out:Z" -v "$W/inner.sh:/inner.sh:Z" -e QT_QPA_PLATFORM=offscreen localhost/fabos:vm /inner.sh; rc=$?
echo "UPDATES BANNER RENDER: $([ $rc = 0 ] && echo PASS || echo FAIL)  ($OUT/updates-banner-{restart,logout,none}.png)"; exit $rc
