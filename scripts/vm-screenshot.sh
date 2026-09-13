#!/usr/bin/env bash
# Take a screenshot of the running FabOS OS VM via the QEMU monitor socket. Usage: scripts/vm-screenshot.sh [name] [head]  (head: 0 = first display)
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
name=${1:-shot-$(date +%H%M%S)}; head=${2:-}; mkdir -p build/screenshots; ppm=build/screenshots/$name.ppm; png=build/screenshots/$name.png
python3 - "$ppm" "$head" <<'PY'
import socket, sys, time
s=socket.socket(socket.AF_UNIX); s.connect("build/qemu-monitor.sock"); s.settimeout(3)
try: s.recv(4096)
except Exception: pass
s.sendall(("screendump %s%s\n" % (sys.argv[1], (" vga0 %s" % sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else "")).encode()); time.sleep(1.5)
try: s.recv(4096)
except Exception: pass
s.close()
PY
for i in 1 2 3 4 5 6; do [ -s "$ppm" ] && break; sleep 1; done
convert "$ppm" "$png" && rm -f "$ppm" && echo "$png"
