#!/usr/bin/env bash
# Fab OS — desktop responsiveness measurements INSIDE the booted VM (2 GB, 4 vCPUs), driven over SSH like
# tests/agent-live-vm.sh. Every PASS/FAIL below is a number read in the VM; nothing is inferred from the source tree.
#
#   tests/perf-vm.sh [--inject] [--keep] [--idle-wait S] [--sample S]
#     --inject      push the working-tree desktop files (kwinrc, kdeglobals, krunnerrc, lowram-tune.sh, the three plasmoids,
#                   command_center.py, the fabos-voiced unit) into the VM first and restart plasmashell / reconfigure KWin
#     --keep        leave the VM running afterwards; without it a VM that THIS script booted is powered off at the end
#                   (a VM that was already running when the script started is always left as found)
#     --idle-wait   seconds to leave the session alone before sampling (default 60)
#     --sample      length of the idle sample in seconds (default 20)
#   VM_MEM (default 2048) — scripts/boot-vm.sh is started if no VM is running.
# Output: build/perf-vm.out (full log), build/perf-vm.json (the numbers).
#
# What is measured
#   1. configuration in effect: kwinrc AllowTearing=false, kdeglobals AnimationDurationFactor=0.5, krunnerrc baloosearch
#      off, blur off on a < 3.5 GB machine (lowram-tune.sh ran at login), fabos-voiced Nice=15 + IOSchedulingClass=idle
#      (unit AND the running process), no spotter process without a microphone
#   2. idle budget after `--idle-wait` s: CPU of plasmashell + kwin_wayland + fabos-agentd + fabos-voiced (+ pocketsphinx,
#      pw-record) + Fab AI Controls (if open) over `--sample` s, as % of ONE core (utime+stime+cutime+cstime deltas, so the
#      short-lived children of plasmashell's DataSources count too) — PASS < 8 %; task creations per second from
#      /proc/stat `processes` (forks AND threads, system-wide, exact) — PASS < 1/s; plus a 50 ms /proc scan that attributes
#      new PIDs to our components (a lower bound: a process that lives < 50 ms can be missed) and /proc/loadavg
#   3. window-switch latency: three windows (kcalc, konsole, kate); ten times KWin's own Alt+Tab handler is invoked
#      through kglobalaccel (org.kde.kglobalaccel.Component.invokeShortcut "Walk Through Windows" on /component/kwin; with
#      no modifier held KWin one-steps to the next window without showing the switcher) and a KWin script reports each
#      windowActivated as a D-Bus call; `busctl --user monitor --json=short` timestamps both the invocation and the
#      activation on the same clock — median of ten must be < 150 ms. (kdotool is not in the image; KWin's D-Bus interface
#      has no activeWindow getter; print() in KWin scripts is gated by the kwin_scripting log category — hence the
#      monitor. Verified in a virtual kwin_wayland session in the image: script-driven switches reported in 5–7 ms.)
#   4. Fab AI Controls does not freeze while a task streams: the daemon is switched to the scripted provider
#      (FABOS_AGENT_PROVIDER=fake via ~/.config/fabos/agent.env, restored afterwards), a "long sleep please" task runs 45 s,
#      `fabos-command-center --task ID` opens on it, three full-screen spectacle shots 1 s apart must differ and the app
#      must have used CPU meanwhile (it repaints its typing indicator and polls the task on its worker thread).
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
INJECT=0; KEEP=0; IDLE_WAIT=60; SAMPLE=20
while [ $# -gt 0 ]; do case "$1" in --inject) INJECT=1;; --keep) KEEP=1;; --idle-wait) IDLE_WAIT=$2; shift;; --sample) SAMPLE=$2; shift;; -h|--help) sed -n '2,30p' "$0"; exit 0;; *) echo "unknown argument: $1"; exit 3;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222"
mkdir -p build; OUT=build/perf-vm.out; JSON=build/perf-vm.json; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
vm() { $SSH "$@"; }
# the graphical session's environment for anything that must talk to KWin / Wayland / the session bus from the ssh login
SENV='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; export WAYLAND_DISPLAY=$(cd $XDG_RUNTIME_DIR && ls -d wayland-? 2>/dev/null | head -1); export QT_QPA_PLATFORM=wayland XDG_SESSION_TYPE=wayland;'
vms() { vm "$SENV $*"; }
api() { # api METHOD PATH [JSON]
  local body=${3:-}
  vm "curl -s -m 30 -X $1 -H \"Authorization: Bearer \$(cat \$XDG_RUNTIME_DIR/fabos-agent/token)\" -H 'Content-Type: application/json' ${body:+-d $(printf %q "$body")} http://127.0.0.1:8790$2"; }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }
pass=0; fail=0; skip=0
verdict() { case "$1" in PASS) pass=$((pass+1));; FAIL) fail=$((fail+1));; SKIP) skip=$((skip+1));; esac; echo ">>> $1: $2"; }
declare -A R   # numbers for build/perf-vm.json; the JSON-valued entries default to an empty object so the summary always parses
R[idle]='{}'; R[switch]='{}'; R[controls]='{}'; R[streaming]='{}'
BOOTED=0       # 1 when this script started the VM (then it is powered off at the end unless --keep)

echo "### Fab OS perf VM test — $(date -u +%FT%TZ) — idle-wait=${IDLE_WAIT}s sample=${SAMPLE}s inject=$INJECT keep=$KEEP"
pgrep -f qemu-system-x86_64 >/dev/null || { echo "booting VM"; BOOTED=1; (scripts/boot-vm.sh --headless --mem "${VM_MEM:-2048}" --cpus 4 > build/boot-headless.out 2>&1 &); }
for i in $(seq 1 100); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM"; exit 3; }
echo "### waiting for the graphical session (plasmashell + kwin_wayland)"
for i in $(seq 1 120); do vm "pgrep -x plasmashell >/dev/null && pgrep -x kwin_wayland >/dev/null" && break; sleep 3; done
vm "pgrep -x plasmashell >/dev/null" || { echo "no plasmashell in the VM (is the session logged in?)"; exit 3; }
vm "nproc; head -1 /proc/meminfo; uptime -p; cat /etc/fabos-release 2>/dev/null | head -2"

if [ "$INJECT" = 1 ]; then
  echo "### injecting working-tree desktop files"
  D=packages/fabos-desktop; A=packages/fabos-agent
  $SCP -r "$D/etc/xdg/kwinrc" "$D/etc/xdg/kdeglobals" "$D/etc/xdg/krunnerrc" "$D/usr/lib/fabos/lowram-tune.sh" "$D/etc/xdg/plasma-workspace/env/40-fabos-lowram.sh" \
       "$A/usr/lib/fabos/agent/command_center.py" packages/fabos-voice/usr/lib/systemd/user/fabos-voiced.service fabos@127.0.0.1:/tmp/ >/dev/null
  $SCP -r "$D/usr/share/plasma/plasmoids/in.patienceai.fabos.quicksettings" "$D/usr/share/plasma/plasmoids/in.patienceai.fabos.dock" "$A/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar" fabos@127.0.0.1:/tmp/ >/dev/null
  # brand variables the package build would have rendered
  vm "sed -i 's/@UI_FONT@/Inter/g; s/@MONO_FONT@/JetBrains Mono/g' /tmp/kdeglobals; sed -i 's/@DISTRO_NAME@/Fab OS/g; s/@DOCS_URL@/https:\/\/fabos.patienceai.in\/docs\//g' /tmp/fabos-voiced.service"
  vm "echo fabos | sudo -S sh -c 'install -m 644 /tmp/kwinrc /tmp/kdeglobals /tmp/krunnerrc /etc/xdg/ && install -m 755 /tmp/lowram-tune.sh /usr/lib/fabos/lowram-tune.sh && install -D -m 644 /tmp/40-fabos-lowram.sh /etc/xdg/plasma-workspace/env/40-fabos-lowram.sh && install -m 755 /tmp/command_center.py /usr/lib/fabos/agent/command_center.py && install -m 644 /tmp/fabos-voiced.service /usr/lib/systemd/user/fabos-voiced.service && for p in in.patienceai.fabos.quicksettings in.patienceai.fabos.dock in.patienceai.fabos.askbar; do rm -rf /usr/share/plasma/plasmoids/\$p && cp -r /tmp/\$p /usr/share/plasma/plasmoids/\$p; done' 2>/dev/null && echo installed"
  vm "rm -f ~/.config/fabos/lowram-tune-done-v1; sh /usr/lib/fabos/lowram-tune.sh; systemctl --user daemon-reload; systemctl --user restart fabos-voiced; $SENV qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure; systemctl --user restart plasma-plasmashell; sleep 8; pgrep -x plasmashell >/dev/null && echo plasmashell-restarted"
fi

# ---------------------------------------------------------------- 1. configuration in effect
echo; echo "=== 1. configuration in effect"
v=$(vm "kreadconfig6 --file kwinrc --group Compositing --key AllowTearing --default unset"); R[allow_tearing]=$v
[ "$v" = false ] && verdict PASS "kwinrc [Compositing] AllowTearing=false" || verdict FAIL "kwinrc AllowTearing=$v"
v=$(vm "kreadconfig6 --file kdeglobals --group KDE --key AnimationDurationFactor --default unset"); R[animation_factor]=$v
[ "$v" = 0.5 ] && verdict PASS "kdeglobals [KDE] AnimationDurationFactor=0.5 (every Plasma/KWin animation at half duration)" || verdict FAIL "AnimationDurationFactor=$v"
v=$(vm "kreadconfig6 --file krunnerrc --group Plugins --key baloosearchEnabled --default unset"); R[baloosearch]=$v
[ "$v" = false ] && verdict PASS "krunnerrc: File Search runner off (no baloorunner activation per query)" || verdict FAIL "baloosearchEnabled=$v"
memkb=$(vm "awk '/^MemTotal/{print \$2}' /proc/meminfo"); R[mem_kb]=$memkb
blur=$(vm "kreadconfig6 --file kwinrc --group Plugins --key blurEnabled --default unset"); mark=$(vm "cat ~/.config/fabos/lowram-tune-done-v1 2>/dev/null"); R[blur]=$blur
if [ "${memkb:-0}" -lt 3500000 ]; then
  [ "$blur" = false ] && [ -n "$mark" ] && verdict PASS "blur off on this $((memkb/1024)) MB machine (lowram-tune.sh at login: $mark)" || verdict FAIL "blur=$blur marker='$mark' on a $((memkb/1024)) MB machine"
else verdict SKIP "blur rule not applicable: $((memkb/1024)) MB machine keeps blur (blur=$blur)"; fi
u=$(vm "systemctl --user show fabos-voiced -p Nice -p IOSchedulingClass 2>/dev/null | tr '\n' ' '"); R[voiced_unit]=$u
echo "$u" | grep -q "Nice=15" && echo "$u" | grep -q "IOSchedulingClass=idle" && verdict PASS "fabos-voiced unit: $u" || verdict FAIL "fabos-voiced unit: $u"
pid=$(vm "pgrep -f fabos_voiced.py | head -1"); ni=$(vm "ps -o ni= -o cls= -p ${pid:-0} 2>/dev/null | tr -s ' '"); R[voiced_ps]=$ni
[ -n "$pid" ] && echo "$ni" | grep -q " 15" && verdict PASS "fabos_voiced.py runs at nice 15 (pid $pid:$ni)" || verdict FAIL "fabos_voiced.py pid=${pid:-none} ni/cls='$ni'"
mics=$(vm "pactl list short sources 2>/dev/null | grep -vc monitor"); sp=$(vm "pgrep -c pocketsphinx"); R[mics]=$mics; R[spotters]=$sp
if [ "${mics:-0}" = 0 ]; then [ "${sp:-0}" = 0 ] && verdict PASS "no microphone -> no spotter process" || verdict FAIL "no microphone but $sp pocketsphinx process(es)"; else echo "    (microphone present: $mics source(s), spotter processes: $sp — the always-on cost is measured below)"; fi

# ---------------------------------------------------------------- 2. idle budget
echo; echo "=== 2. idle budget (leaving the session alone for ${IDLE_WAIT}s, then a ${SAMPLE}s sample)"
cat > build/perf-sample.py <<'PY'
import os, sys, time, json
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
CLK = os.sysconf("SC_CLK_TCK")
OURS = ["plasmashell", "kwin_wayland", "fabos_agentd.py", "fabos_voiced.py", "command_center.py", "pocketsphinx", "pw-record", "fabos-voice"]

def read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return b""

def procs():
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        st = read("/proc/%s/stat" % d)
        if not st:
            continue
        i, j = st.find(b"("), st.rfind(b")")
        comm = st[i + 1:j].decode(errors="replace")
        f = st[j + 2:].split()
        try:
            ppid = int(f[1]); ticks = int(f[11]) + int(f[12]) + int(f[13]) + int(f[14])   # utime stime cutime cstime
        except (IndexError, ValueError):
            continue
        cmd = read("/proc/%s/cmdline" % d).replace(b"\0", b" ").decode(errors="replace")
        out[int(d)] = (comm, cmd, ppid, ticks)
    return out

def component(pid, table):
    seen = set()
    while pid > 1 and pid in table and pid not in seen:
        seen.add(pid)
        comm, cmd, ppid, _ = table[pid]
        for n in OURS:
            if n in cmd.split(" ")[0:3] or comm == n or (n.endswith(".py") and n in cmd):
                return n
        pid = ppid
    return None

def forks():
    for line in read("/proc/stat").splitlines():
        if line.startswith(b"processes"):
            return int(line.split()[1])
    return 0

t0 = time.monotonic(); p0 = procs(); f0 = forks()
known = set(p0); spawns = {}; samples = 0
while time.monotonic() - t0 < DUR:
    time.sleep(0.05)
    cur = procs(); samples += 1
    for pid in cur:
        if pid not in known:
            known.add(pid)
            c = component(pid, cur) or component(cur[pid][2], cur) or "other"
            spawns[c] = spawns.get(c, 0) + 1
t1 = time.monotonic(); p1 = procs(); f1 = forks()
el = t1 - t0
per = {}
for pid, (comm, cmd, ppid, ticks) in p1.items():
    c = component(pid, p1)
    if c is None:
        continue
    d = ticks - (p0[pid][3] if pid in p0 else 0)
    per[c] = per.get(c, 0.0) + d
per_pct = {k: round(v / CLK / el * 100, 2) for k, v in per.items()}
print(json.dumps({"elapsed_s": round(el, 1), "clk_tck": CLK, "ncpu": os.cpu_count(), "total_pct_one_core": round(sum(per_pct.values()), 2), "per_process_pct": per_pct,
                  "tasks_created": f1 - f0, "tasks_per_s": round((f1 - f0) / el, 2), "spawns_attributed_50ms": spawns, "scans": samples,
                  "loadavg": read("/proc/loadavg").decode().split()[:3]}))
PY
$SCP build/perf-sample.py fabos@127.0.0.1:/tmp/perf-sample.py >/dev/null
[ "$IDLE_WAIT" -gt 0 ] && sleep "$IDLE_WAIT"
idle=$(vm "python3 /tmp/perf-sample.py $SAMPLE"); echo "    $idle"
R[idle]=$idle
cpu=$(echo "$idle" | jget '["total_pct_one_core"]'); tps=$(echo "$idle" | jget '["tasks_per_s"]')
python3 -c "import sys; sys.exit(0 if float('${cpu:-999}') < 8 else 1)" && verdict PASS "idle CPU of our components ${cpu}% of one core over ${SAMPLE}s (< 8)" || verdict FAIL "idle CPU ${cpu}% of one core (limit 8)"
python3 -c "import sys; sys.exit(0 if float('${tps:-999}') < 1 else 1)" && verdict PASS "task creations ${tps}/s system-wide while idle (< 1)" || verdict FAIL "task creations ${tps}/s system-wide (limit 1); attribution: $(echo "$idle" | jget '["spawns_attributed_50ms"]')"

# ---------------------------------------------------------------- 3. window-switch latency
echo; echo "=== 3. window-switch latency (10x KWin's Alt+Tab handler via kglobalaccel, activations timestamped on the session bus)"
cat > build/perf-kwin.js <<'JS'
// Fab OS perf probe: report window activations as D-Bus calls (print() is gated by the kwin_scripting log category).
// The call goes to the bus driver under our own interface: nobody answers it (UnknownMethod, ignored) but
// `busctl --user monitor --match "interface='in.patienceai.fabos.perf'"` sees every call with a microsecond timestamp.
function report(msg) { callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus", "in.patienceai.fabos.perf", "event", msg + " t=" + Date.now()); }
report("loaded windows=" + workspace.windowList().filter(function (w) { return w.normalWindow; }).length);
workspace.windowActivated.connect(function (w) { report("activated caption=" + (w ? String(w.caption).replace(/\s+/g, "_") : "-")); });
workspace.windowAdded.connect(function (w) { report("added caption=" + String(w.caption).replace(/\s+/g, "_")); });
JS
$SCP build/perf-kwin.js fabos@127.0.0.1:/tmp/perf-kwin.js >/dev/null
vms "pkill -f 'busctl --user monitor' 2>/dev/null; rm -f /tmp/perf-monitor.jsonl; setsid -f sh -c 'busctl --user monitor --json=short --match \"interface=in.patienceai.fabos.perf\" --match \"interface=org.kde.kglobalaccel.Component,member=invokeShortcut\" > /tmp/perf-monitor.jsonl 2>/tmp/perf-monitor.err'; sleep 1"
vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-perf >/dev/null 2>&1; qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /tmp/perf-kwin.js fabos-perf && qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start" >/dev/null
vms "setsid -f kcalc >/dev/null 2>&1; setsid -f konsole >/dev/null 2>&1; setsid -f kate -n >/dev/null 2>&1"
for i in $(seq 1 30); do [ "$(vm "grep -c '\"added caption=' /tmp/perf-monitor.jsonl 2>/dev/null")" -ge 3 ] && break; sleep 1; done
vm "grep -c 'added caption=' /tmp/perf-monitor.jsonl" | sed 's/^/    windows opened: /'
sleep 2
for i in $(seq 1 10); do vms "busctl --user call org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component invokeShortcut s 'Walk Through Windows'" >/dev/null 2>&1; sleep 0.7; done
sleep 1
if vm "command -v wtype >/dev/null"; then   # informative: a real Alt+Tab key chord through the virtual-keyboard protocol
  for i in 1 2 3; do vms "wtype -M alt -k Tab -m alt" 2>/dev/null; sleep 0.7; done
fi
sleep 1
lat=$(vm "python3 - <<'PY'
import json
inv = []; act = []; wt = []
for line in open('/tmp/perf-monitor.jsonl'):
    try: j = json.loads(line)
    except Exception: continue
    if j.get('type') != 'method_call': continue
    ts = j.get('timestamp-realtime') or j.get('timestamp') or 0
    if j.get('interface') == 'org.kde.kglobalaccel.Component': inv.append(ts)
    elif j.get('member') == 'event':
        data = ((j.get('payload') or {}).get('data') or [''])[0]
        if data.startswith('activated'): act.append((ts, data.split(' ')[1]))
acts = sorted(act); lats = []; captions = set()
for t in inv:
    nxt = [a for a in acts if a[0] > t and a[0] - t < 2000000]
    if nxt: lats.append((nxt[0][0] - t) / 1000.0); captions.add(nxt[0][1])
lats.sort()
med = lats[len(lats)//2] if lats else None
print(json.dumps({'invocations': len(inv), 'activations': len(acts), 'latencies_ms': [round(x, 1) for x in lats], 'median_ms': None if med is None else round(med, 1), 'distinct_windows': len(captions)}))
PY"); echo "    $lat"; R[switch]=$lat
med=$(echo "$lat" | jget '["median_ms"]'); ninv=$(echo "$lat" | jget '["invocations"]'); nlat=$(echo "$lat" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["latencies_ms"]))' 2>/dev/null)
if [ "${ninv:-0}" -lt 10 ]; then verdict FAIL "only ${ninv:-0} invokeShortcut calls seen on the bus (kglobalaccel /component/kwin reachable?)"
elif [ "${nlat:-0}" -lt 8 ]; then verdict FAIL "only ${nlat:-0} of $ninv switches produced a windowActivated within 2 s"
else python3 -c "import sys; sys.exit(0 if float('${med:-999}') < 150 else 1)" && verdict PASS "window switch median ${med} ms over $nlat switches (< 150; ${lat})" || verdict FAIL "window switch median ${med} ms (limit 150)"; fi
vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-perf >/dev/null 2>&1; pkill -x kcalc; pkill -x konsole; pkill -x kate" 2>/dev/null

# ---------------------------------------------------------------- 4. Fab AI Controls keeps repainting while a task streams
echo; echo "=== 4. Fab AI Controls under a streaming task (scripted provider, restored afterwards)"
vm "mkdir -p ~/.config/fabos; cp -f ~/.config/fabos/agent.env /tmp/agent.env.bak 2>/dev/null; grep -v FABOS_AGENT_PROVIDER ~/.config/fabos/agent.env 2>/dev/null > /tmp/agent.env.new; echo FABOS_AGENT_PROVIDER=fake >> /tmp/agent.env.new; cp /tmp/agent.env.new ~/.config/fabos/agent.env; systemctl --user restart fabos-agent"
for i in $(seq 1 30); do api GET /health 2>/dev/null | grep -q '"ok"' && break; sleep 1; done
TASK=$(api POST /tasks '{"request":"long sleep please","mode":"bypass"}' | jget '["id"]')
for i in $(seq 1 20); do [ "$(api GET "/tasks/${TASK:-0}" | jget '["status"]')" = running ] && break; sleep 0.5; done
echo "    task #$TASK status=$(api GET "/tasks/${TASK:-0}" | jget '["status"]')"
vms "pkill -f command_center.py 2>/dev/null; setsid -f fabos-command-center --task ${TASK:-1} >/tmp/perf-cc.log 2>&1"
sleep 5
ccpid=$(vm "pgrep -f command_center.py | head -1"); t_a=$(vm "awk '{print \$14+\$15}' /proc/${ccpid:-1}/stat 2>/dev/null")
shots=0; diffs=0; prev=""
for i in 1 2 3; do
  vms "spectacle -b -n -f -o /tmp/perf-shot$i.png >/dev/null 2>&1"; sleep 1
  sum=$(vm "md5sum /tmp/perf-shot$i.png 2>/dev/null | cut -c1-32")
  [ -n "$sum" ] && shots=$((shots+1)); [ -n "$prev" ] && [ -n "$sum" ] && [ "$sum" != "$prev" ] && diffs=$((diffs+1)); prev=$sum
done
t_b=$(vm "awk '{print \$14+\$15}' /proc/${ccpid:-1}/stat 2>/dev/null"); cc_ticks=$(( ${t_b:-0} - ${t_a:-0} ))
cc_state=$(vm "ps -o stat= -p ${ccpid:-0} 2>/dev/null | tr -d ' '")
echo "    Fab AI Controls pid=${ccpid:-none} state=$cc_state cpu_ticks_during_shots=$cc_ticks shots=$shots differing_pairs=$diffs"
R[controls]="{\"pid\":\"${ccpid:-}\",\"shots\":$shots,\"differing_pairs\":$diffs,\"cpu_ticks\":$cc_ticks}"
if [ -z "$ccpid" ]; then verdict FAIL "Fab AI Controls did not start ($(vm 'tail -3 /tmp/perf-cc.log' | tr '\n' '|'))"
elif [ "$shots" -lt 2 ]; then verdict FAIL "spectacle produced $shots screenshot(s) (needs the session env: $(vm 'echo $WAYLAND_DISPLAY'))"
elif [ "$diffs" -ge 1 ] && [ "$cc_ticks" -gt 0 ]; then verdict PASS "window kept repainting while the task streamed ($diffs of $((shots-1)) consecutive screenshots differ; $cc_ticks CPU ticks used)"
else verdict FAIL "screen identical across $shots shots or no CPU used (diffs=$diffs ticks=$cc_ticks): the window looks frozen"; fi
[ -n "$ccpid" ] && [ "$IDLE_WAIT" -gt 0 ] && { s2=$(vm "python3 /tmp/perf-sample.py 10"); echo "    10 s sample with Fab AI Controls open on a streaming task: $s2"; R[streaming]=$s2; }
vms "pkill -f command_center.py" 2>/dev/null
api POST "/tasks/${TASK:-0}/cancel" >/dev/null 2>&1
vm "if [ -f /tmp/agent.env.bak ]; then cp /tmp/agent.env.bak ~/.config/fabos/agent.env; else rm -f ~/.config/fabos/agent.env; fi; systemctl --user restart fabos-agent"

# ---------------------------------------------------------------- summary
# (the JSON-valued entries were pre-set to '{}' above: `${R[x]:-{}}` would append a stray '}' whenever the entry IS set)
python3 - "$JSON" "$pass" "$fail" "$skip" "${R[idle]}" "${R[switch]}" "${R[controls]}" "${R[streaming]}" "${R[allow_tearing]:-}" "${R[animation_factor]:-}" "${R[blur]:-}" "${R[mem_kb]:-0}" "${R[voiced_unit]:-}" <<'PY'
import json, sys
a = sys.argv
def j(s):
    try: return json.loads(s)
    except Exception: return s
out = {"pass": int(a[2]), "fail": int(a[3]), "skip": int(a[4]), "idle": j(a[5]), "switch": j(a[6]), "controls": j(a[7]), "streaming": j(a[8]),
       "allow_tearing": a[9], "animation_factor": a[10], "blur": a[11], "mem_kb": int(a[12] or 0), "voiced_unit": a[13]}
open(a[1], "w").write(json.dumps(out, indent=1))
PY
echo; echo "### SUMMARY: $pass PASS / $fail FAIL / $skip SKIP  (numbers: $JSON, log: $OUT)"
if [ $KEEP -eq 1 ]; then echo "(--keep: VM left running for inspection; use scripts/boot-vm.sh monitor to quit)"
elif [ $BOOTED -eq 1 ]; then
  echo "powering the VM off (it was booted by this run; pass --keep to leave it running)"
  vm "echo fabos | sudo -S poweroff" >/dev/null 2>&1
  for i in $(seq 1 60); do pgrep -f qemu-system-x86_64 >/dev/null || break; sleep 2; done
  pgrep -f qemu-system-x86_64 >/dev/null && echo "(VM still running after 120 s; use scripts/boot-vm.sh monitor to quit)" || echo "VM powered off"
else echo "(VM was already running before this run; left as found)"; fi
exit $fail
