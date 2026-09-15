#!/usr/bin/env bash
# Headless live-ISO smoke test: boots the ISO under UEFI, waits for the live self-test markers, powers off.
# (The full automated installation - Calamares driven end to end on a blank disk, then the installed disk booted - is
#  tests/install-vm.sh; this test only proves the live medium boots and the installer + the autoinstall helper are present.)
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
scripts/boot-iso.sh --headless --autotest --timeout "${BOOT_TIMEOUT:-600}" --mem "${MEM:-2560}" > build/iso-boot-test.out 2>&1
fail=0; for m in FABOS_LIVE_OK "LIVE_USER=" "SDDM=active" "CALAMARES=present" "AUTOINSTALL_HELPER=present"; do grep -q "$m" build/serial-iso.log && echo "PASS  $m" || { echo "FAIL  $m"; fail=1; }; done
sed 's/\x1b\[[0-9;]*m//g' build/serial-iso.log | grep -E '^(PRETTY_NAME|KERNEL|LIVE_USER|SDDM|PLASMA|CALAMARES|INSTALLER_ICON|AGENT|FIRMWARE|MEM_USED|AUTOINSTALL_HELPER)' | sed 's/^/  /'; exit $fail
