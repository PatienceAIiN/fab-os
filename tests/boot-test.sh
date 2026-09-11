#!/usr/bin/env bash
# Headless boot smoke test: boots the VM image, waits for the self-test markers on the serial console.
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
scripts/boot-vm.sh --headless --autotest --timeout "${BOOT_TIMEOUT:-300}" --mem "${MEM:-2048}" >/dev/null 2>&1
log=build/serial-vm.log; fail=0
for m in FABOS_BOOT_OK "ID=fabos" "SDDM=active" ; do grep -q "$m" "$log" && echo "PASS  $m" || { echo "FAIL  $m"; fail=1; }; done
grep -E 'PRETTY_NAME|KERNEL|SDDM|PLASMA|KWIN|PLYMOUTH|AIOS|LLAMA|SNAPD|MEM_USED|BOOT_TIME' "$log" | sed 's/^/  /'
exit $fail
