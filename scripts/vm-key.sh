#!/usr/bin/env bash
# Send keys / type text into the running VM through the QEMU monitor. Usage: vm-key.sh key meta-space | vm-key.sh type "hello"
set -euo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
python3 - "$@" <<'PY'
import socket, sys, time
s=socket.socket(socket.AF_UNIX); s.connect("build/qemu-monitor.sock"); s.settimeout(2)
def cmd(c):
    s.sendall((c+"\n").encode()); time.sleep(0.15)
    try: s.recv(65536)
    except Exception: pass
try: s.recv(65536)
except Exception: pass
mode=sys.argv[1]
if mode=="key":
    for k in sys.argv[2:]: cmd("sendkey "+k); time.sleep(0.25)
elif mode=="type":
    keymap={' ':'spc','.':'dot',',':'comma','-':'minus','/':'slash',':':'shift-semicolon','@':'shift-2','?':'shift-slash','!':'shift-1',"'":'apostrophe','_':'shift-minus'}
    for ch in sys.argv[2]:
        if ch.isupper(): cmd("sendkey shift-"+ch.lower())
        elif ch.isalnum(): cmd("sendkey "+ch)
        else: cmd("sendkey "+keymap.get(ch,'spc'))
        time.sleep(0.05)
s.close()
PY
