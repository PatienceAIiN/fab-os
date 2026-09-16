#!/bin/bash
# Boot a DISPOSABLE overlay of the Fab OS VM disk, run tests/dock-qml-harness/vm-dock-activate.py against it, quit QEMU,
# delete the overlay. Always run under the shared VM lock (one VM at a time on this host), the whole session as ONE command:
#   flock -w 5400 /tmp/fabos-vm.lock -c 'tests/dock-qml-harness/vm-dock-activate.sh [out dir]'
# Env: FABOS_MAIN (checkout whose build/fabos-vm.img + scripts/boot-vm.sh boot the VM; default: this checkout, or the main
#      checkout when this one is a git worktree without a build/ disk), FABOS_VM_IMG (base disk, default $FABOS_MAIN/build/fabos-vm.img).
# QEMU is killed and the overlay deleted on ANY exit (trap), so an interrupted run never leaves a VM behind.
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
MAIN=${FABOS_MAIN:-$ROOT}
if [ ! -f "$MAIN/build/fabos-vm.img" ] && [ -f "$ROOT/.git" ]; then   # a worktree: .git is a file pointing into the main checkout
  COMMON=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); [ -n "$COMMON" ] && MAIN=$(dirname "$COMMON")
fi
BASE=${FABOS_VM_IMG:-$MAIN/build/fabos-vm.img}
OUT=${1:-$ROOT/build/r7-dock-activate}; mkdir -p "$OUT"
OVL=/tmp/r7-dock.qcow2; QMP=/tmp/r7-dock.qmp
[ -f "$BASE" ] || { echo "no base disk $BASE"; exit 2; }
cleanup() {
  echo "== quitting QEMU"
  pkill -f "qemu-system-x86_64.*$OVL" 2>/dev/null; sleep 4
  pgrep -f "qemu-system-x86_64.*$OVL" >/dev/null && { pkill -9 -f "qemu-system-x86_64.*$OVL"; sleep 2; }
  rm -f "$OVL" "$QMP" /tmp/r7-dock-shot.ppm /tmp/r7-dock-kwin.js /tmp/r7-sess.py
}
trap cleanup EXIT INT TERM
rm -f "$OVL" "$QMP"
qemu-img create -f qcow2 -b "$BASE" -F raw "$OVL" > /dev/null || exit 2
echo "== booting overlay $OVL (base $BASE) from $MAIN; evidence -> $OUT"
(cd "$MAIN" && scripts/boot-vm.sh --headless --mem 2048 --cpus 2 --disk "$OVL" --qmp "$QMP" > "$OUT/boot.log" 2>&1 &)
sleep 3
python3 "$ROOT/tests/dock-qml-harness/vm-dock-activate.py" --qmp "$QMP" --out "$OUT" --repo "$ROOT" 2>&1 | tee "$OUT/vm-dock-activate.out"
RC=${PIPESTATUS[0]}
echo "vm-dock-activate: $([ "$RC" = 0 ] && echo PASS || echo FAIL) (rc=$RC) — evidence in $OUT"
exit "$RC"
