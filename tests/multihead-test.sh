#!/usr/bin/env bash
# Multi-monitor check: boot the VM with two virtual displays, wait for the desktop, capture both heads.
# PASS = head 1 shows a desktop too (Plasma extended the workspace onto the second display, KScreen default).
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
pgrep -f qemu-system-x86_64 >/dev/null && { echo "a VM is already running; stop it first"; exit 1; }
(scripts/boot-vm.sh --headless --heads 2 --mem 2048 --cpus 4 > build/boot-multihead.out 2>&1 &)
for i in $(seq 1 60); do [ -S build/qemu-monitor.sock ] && break; sleep 2; done; sleep 90
scripts/vm-screenshot.sh multihead-0 0 >/dev/null 2>&1 && echo "  shot head 0"
scripts/vm-screenshot.sh multihead-1 1 >/dev/null 2>&1 && echo "  shot head 1"
scripts/vm-key.sh key alt-f2; sleep 1.5; scripts/vm-key.sh type "kscreen-doctor -o"; sleep 1; scripts/vm-key.sh key esc
python3 - <<'PY'
from PIL import Image, ImageStat
import os, sys
ok=True
for h in (0,1):
    p="build/screenshots/multihead-%d.png"%h
    if not os.path.exists(p): print("head %d: no frame"%h); ok=False; continue
    im=Image.open(p).convert("RGB"); st=ImageStat.Stat(im); mean=sum(st.mean)/3; var=sum(st.var)/3
    print("head %d: %dx%d mean=%.0f var=%.0f"%(h, im.width, im.height, mean, var))
    if var < 50: print("  looks blank"); ok=False
print("MULTIHEAD", "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
PY
rc=$?
python3 -c "
import socket; s=socket.socket(socket.AF_UNIX); s.connect('build/qemu-monitor.sock'); s.sendall(b'quit\n'); s.close()" 2>/dev/null
exit $rc
