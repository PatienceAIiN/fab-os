#!/usr/bin/env python3
"""Application launch latency — runs INSIDE the guest, in the graphical session's environment.

    python3 launch.py "COMMAND" CLASS_SUBSTRING [KILL_PATTERN] [MONITOR_FILE]

Starts COMMAND (detached), then waits for KWin to map a window whose resourceClass (or caption) contains CLASS_SUBSTRING.
KWin's windowAdded signal is turned into a session-bus method call by tests/perf/kwin-events.js, and
`busctl --user monitor --json=short` writes those calls to MONITOR_FILE (default /tmp/fabos-perf-monitor.jsonl), each
carrying KWin's own Date.now() in the payload ("added class=org.kde.konsole caption=... t=1726...").
Latency = that timestamp minus this process's clock right before the exec — both are the guest's realtime clock, so no
host/guest skew is involved. Afterwards the application is killed by process name (pkill -x KILL_NAME, default = the
command's first word; -x so this script's own command line, which carries the name, is never matched) and this waits
until it is gone, so the next run starts from nothing.
One JSON line: {"app", "match", "launch_ms", "timed_out", "class", "caption"}; launch_ms is null on timeout (90 s).
"""
import json
import os
import subprocess
import sys
import time

cmd = sys.argv[1]
match = sys.argv[2].lower()
killpat = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else cmd.split()[0]
mon = sys.argv[4] if len(sys.argv) > 4 else "/tmp/fabos-perf-monitor.jsonl"
TIMEOUT_S = 90


def added_events():
    evs = []
    try:
        with open(mon, errors="replace") as f:
            for line in f:
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if j.get("type") != "method_call" or j.get("member") != "event":
                    continue
                data = ((j.get("payload") or {}).get("data") or [""])[0]
                if not str(data).startswith("added "):
                    continue
                evs.append(dict(kv.split("=", 1) for kv in str(data).split(" ")[1:] if "=" in kv))
    except OSError:
        pass
    return evs


seen = len(added_events())
t0 = time.time() * 1000.0
subprocess.Popen(cmd.split(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
deadline = time.time() + TIMEOUT_S
hit = None
while time.time() < deadline and hit is None:
    for e in added_events()[seen:]:
        if match in e.get("class", "").lower() or match in e.get("caption", "").lower():
            try:
                t = float(e.get("t", "0"))
            except ValueError:
                t = 0.0
            if t >= t0 - 50:          # the map event must postdate our launch (50 ms of clock rounding slack)
                hit = e
                break
    if hit is None:
        time.sleep(0.02)
lat = None if hit is None else round(float(hit["t"]) - t0)
time.sleep(0.5)                       # let the window finish its first paint before it is taken down
subprocess.run(["pkill", "-x", killpat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    if subprocess.run(["pgrep", "-x", killpat], stdout=subprocess.DEVNULL).returncode != 0:
        break
    time.sleep(0.25)
else:
    subprocess.run(["pkill", "-9", "-x", killpat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(json.dumps({"app": cmd, "match": match, "launch_ms": lat, "timed_out": hit is None,
                  "class": (hit or {}).get("class", ""), "caption": (hit or {}).get("caption", "")[:60]}))
