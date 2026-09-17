#!/usr/bin/env bash
# Fab OS — desktop performance and power measurements INSIDE a disposable copy of the VM disk (docs/PERFORMANCE.md).
# Every number below is read in the guest; nothing is inferred from the source tree. The base disk is never written:
# the run boots a qcow2 overlay of build/fabos-vm.img, optionally installs .debs into it (the over-the-air path:
# `apt-get install` of local files, so every postinst runs as an UPGRADE), reboots into the resulting desktop, leaves it
# alone, measures, and always quits QEMU and deletes the overlay. The whole session runs under flock /tmp/fabos-vm.lock
# (one VM at a time on this host).
#
#   tests/perf-vm.sh [--debs DIR]... [--baseline] [--baseline-json FILE] [--out DIR] [--build DIR]
#                    [--idle-wait S] [--sample S] [--runs N] [--mem MB] [--cpus N] [--no-reboot] [--no-modes]
#                    [--reboot-check] [--keep]
#   --debs DIR        install DIR/*.deb into the guest before measuring (repeatable, applied in order: e.g. the shipped
#                     1.0-7 pool, then the freshly built 1.0-8 debs = the upgrade a user's machine will make)
#   --baseline        record only: budgets are reported but never fail the run (the "before" measurement)
#   --baseline-json   a previous run's perf-vm.json: launch latencies must not regress past it (see BUDGET_REGRESSION)
#   --out DIR         evidence directory (default <build>/perf-vm/<timestamp>): perf-vm.out (log), perf-vm.json (numbers),
#                     facts.txt, idle.json, launch.jsonl, modes.jsonl, install-*.log, serial.log
#   --build DIR       where build/ lives (default <repo>/build or $FABOS_BUILD; a worktree passes the main checkout's)
#   --idle-wait S     seconds the desktop is left alone before the idle sample (default 180)
#   --sample S        length of the idle sample (default 60)
#   --runs N          launches per application (default 3; run 1 is cold, the median of the rest is "warm")
#   --mem/--cpus      QEMU size (default 2048 MB / 2 vCPUs — the shared 7 GB host allows no more)
#   --no-reboot       measure the session that is already logged in after the installs (default: reboot into a fresh one)
#   --no-modes        skip the Performance-mode round trip (fabos-perf-mode set/status for every mode)
#   --reboot-check    after the modes: set Gaming, reboot, assert it is still in force, set Balanced back (+3 min)
#   --keep            leave QEMU running and keep the overlay (for a look inside); default: quit + delete
#
# What is measured (all saved under --out)
#   facts.txt      configuration and service state in effect: memory (sysctl, zram, oomd, I/O schedulers), power
#                  (power-profiles-daemon, powerdevil keys, RAPL / power_supply presence), KWin (tearing, effects
#                  loaded/active, VRR), indexing (baloo), voice (unit, processes, log), the local model units, the
#                  session's running services and autostarts, systemd-analyze blame/critical-chain (system + user),
#                  journal warnings grouped, top processes by RSS — tests/perf/facts.sh
#   idle.json      a --sample s idle sample: per-process CPU % of one core, wakeups/s (context switches over all threads),
#                  RSS/PSS, system CPU, tasks created/s, interrupts/s — tests/perf/sample.py
#   launch.jsonl   launch-to-window-mapped latency of Fab Terminal (konsole), Fab Files (dolphin), Fab Editor (kate) and
#                  Firefox, --runs times each: KWin's windowAdded (tests/perf/kwin-events.js) timestamped on the guest
#                  clock against the exec time (tests/perf/launch.py); kdotool is not in Ubuntu 26.04
#   modes.jsonl    fabos-perf-mode status --json after `set` of every mode, checked against the mode table below
#
# Budgets (FAIL unless --baseline)
#   BUDGET_VOICED_PCT     fabos-voiced + its recorder + spotter together        < 1.5 % of one core while idle
#   BUDGET_PROC_PCT       any other single process while idle                    < 5 %   (plasmashell, kwin_wayland < BUDGET_SHELL_PCT = 3 %)
#   BUDGET_TASKS_PER_S    system-wide task creations while idle                  < 2 /s
#   BUDGET_LAUNCH_MS      warm launch latency per application (VM: 2 vCPU, llvmpipe) — see the table in the code
#   BUDGET_REGRESSION     warm latency vs --baseline-json                         <= 1.25 x + 200 ms
#   every `apt-get install` of --debs exits 0 (a postinst may never fail an upgrade); no llama-server process after login
#   (the model is loaded on demand only); no baloo_file process (indexing off by default); the mode table holds.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
BUILD=${FABOS_BUILD:-$HERE/build}; OUT=""; DEBS=(); BASELINE=0; BASE_JSON=""; IDLE_WAIT=180; SAMPLE=60; RUNS=3; MEM=2048; CPUS=2
NO_REBOOT=0; MODES=1; REBOOT_CHECK=0; KEEP=0; OVL=/tmp/r8-perf.qcow2; QMP=/tmp/r8-perf.qmp
while [ $# -gt 0 ]; do case $1 in
  --debs) DEBS+=("$2"); shift;; --baseline) BASELINE=1;; --baseline-json) BASE_JSON=$2; shift;; --out) OUT=$2; shift;; --build) BUILD=$2; shift;;
  --idle-wait) IDLE_WAIT=$2; shift;; --sample) SAMPLE=$2; shift;; --runs) RUNS=$2; shift;; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;;
  --no-reboot) NO_REBOOT=1;; --no-modes) MODES=0;; --reboot-check) REBOOT_CHECK=1;; --keep) KEEP=1;; --overlay) OVL=$2; shift;;
  -h|--help) sed -n '2,45p' "$0"; exit 0;; *) echo "unknown argument: $1"; exit 3;; esac; shift; done
BUILD=$(cd "$BUILD" && pwd) || { echo "no build dir $BUILD"; exit 3; }
DISK=$BUILD/fabos-vm.img; [ -f "$DISK" ] || { echo "no VM disk $DISK (scripts/make-disk.sh)"; exit 3; }
OUT=${OUT:-$BUILD/perf-vm/$(date +%Y%m%d-%H%M%S)}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
for d in "${DEBS[@]}"; do ls "$d"/*.deb >/dev/null 2>&1 || { echo "no .deb in $d"; exit 3; }; done
if [ -z "${PERF_INNER:-}" ]; then
  echo "== waiting for the VM lock (/tmp/fabos-vm.lock, up to 90 min)"
  args=(--out "$OUT" --build "$BUILD" --idle-wait "$IDLE_WAIT" --sample "$SAMPLE" --runs "$RUNS" --mem "$MEM" --cpus "$CPUS" --overlay "$OVL")
  for d in "${DEBS[@]}"; do args+=(--debs "$d"); done
  [ $BASELINE = 1 ] && args+=(--baseline); [ -n "$BASE_JSON" ] && args+=(--baseline-json "$BASE_JSON")
  [ $NO_REBOOT = 1 ] && args+=(--no-reboot); [ $MODES = 0 ] && args+=(--no-modes); [ $REBOOT_CHECK = 1 ] && args+=(--reboot-check); [ $KEEP = 1 ] && args+=(--keep)
  PERF_INNER=1 exec flock -w 5400 /tmp/fabos-vm.lock "$0" "${args[@]}"
fi

# ---------------------------------------------------------------- budgets
BUDGET_VOICED_PCT=1.5; BUDGET_PROC_PCT=5; BUDGET_SHELL_PCT=3; BUDGET_TASKS_PER_S=2.0   # tasks: system-wide (systemd, journald and the probes of the bar included); 1.0-7 measured 1.5/s
declare -A BUDGET_LAUNCH_MS=([konsole]=4000 [dolphin]=6000 [kate]=6000 [firefox]=30000)   # warm, in THIS VM (2 vCPU, llvmpipe); the regression check against --baseline-json is the sharp guard
BUDGET_REGRESSION_FACTOR=1.25; BUDGET_REGRESSION_SLACK_MS=200

LOG=$OUT/perf-vm.out; JSON=$OUT/perf-vm.json; : > "$LOG"; exec > >(tee -a "$LOG") 2>&1
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222"
vm() { $SSH "$@"; }
SENV='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; export WAYLAND_DISPLAY=$(cd $XDG_RUNTIME_DIR && ls -d wayland-? 2>/dev/null | head -1); export QT_QPA_PLATFORM=wayland XDG_SESSION_TYPE=wayland;'
vms() { vm "$SENV $*"; }
# Run a command as the user's systemd manager would (a transient unit, stdout piped back): an ssh login is an inactive,
# remote logind session and polkit denies it power-profiles-daemon's switch-profile and the fabos-perf-mode@ start rule,
# while a process under the user manager is attributed to the user's active seat session — the same context the quick
# settings card (plasmashell, itself a user unit) runs `fabos-perf-mode set` from. So this is the realistic path.
vmu() { vm "systemd-run --user --quiet --wait --pipe --collect -E DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/\$(id -u)/bus -E XDG_RUNTIME_DIR=/run/user/\$(id -u) $*"; }
root() { vm "echo fabos | sudo -S -p '' sh -c $(printf %q "$*") 2>&1"; }
pass=0; fail=0; skip=0
verdict() { case "$1" in PASS) pass=$((pass+1));; FAIL) fail=$((fail+1));; SKIP) skip=$((skip+1));; esac; echo ">>> $1: $2"; }
budget() { # budget "what" "value < limit" -> PASS/FAIL (FAIL becomes NOTE with --baseline)
  if python3 -c "import sys; sys.exit(0 if ($2) else 1)" 2>/dev/null; then verdict PASS "$1"; elif [ $BASELINE = 1 ]; then echo ">>> NOTE (baseline, not enforced): $1"; else verdict FAIL "$1"; fi; }
jq_() { python3 -c "import sys,json; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))" "$1" "$2" 2>/dev/null; }

cleanup() {
  if [ $KEEP = 1 ]; then echo "== --keep: QEMU left running on $OVL (kill qemu-system-x86_64 and delete it when done)"; return; fi
  echo "== quitting QEMU and deleting the overlay"
  vm "echo fabos | sudo -S -p '' poweroff" >/dev/null 2>&1
  for i in $(seq 1 30); do pgrep -f "qemu-system-x86_64.*$(basename "$OVL")" >/dev/null || break; sleep 2; done
  pkill -f "qemu-system-x86_64.*$(basename "$OVL")" 2>/dev/null; sleep 1; pkill -9 -f "qemu-system-x86_64.*$(basename "$OVL")" 2>/dev/null
  rm -f "$OVL" "$QMP" "$OUT/OVMF_VARS.fd" /tmp/r8-perf.mon
}
trap cleanup EXIT

wait_ssh() { for i in $(seq 1 "${1:-120}"); do vm true 2>/dev/null && return 0; sleep 3; done; return 1; }
wait_session() { for i in $(seq 1 "${1:-120}"); do vm "pgrep -x plasmashell >/dev/null && pgrep -x kwin_wayland >/dev/null" 2>/dev/null && return 0; sleep 3; done; return 1; }
reboot_guest() {
  echo "== rebooting the guest"; vm "echo fabos | sudo -S -p '' systemctl reboot" >/dev/null 2>&1
  for i in $(seq 1 40); do vm true 2>/dev/null || break; sleep 2; done        # wait for ssh to go away
  sleep 5; wait_ssh 150 || { echo "guest did not come back after the reboot"; exit 3; }
  wait_session 120 || { echo "no graphical session after the reboot"; exit 3; }
  vm "loginctl list-sessions --no-legend 2>/dev/null | head -3; uptime -p"
}

echo "### Fab OS perf VM test — $(date -u +%FT%TZ) — mem=${MEM}M cpus=$CPUS idle-wait=${IDLE_WAIT}s sample=${SAMPLE}s runs=$RUNS baseline=$BASELINE debs=${DEBS[*]:-none} out=$OUT"
pgrep -f qemu-system-x86_64 >/dev/null && { echo "another QEMU is running on this host; refusing (one VM at a time)"; exit 3; }
rm -f "$OVL"; qemu-img create -q -f qcow2 -b "$DISK" -F raw "$OVL" || { echo "qemu-img failed"; exit 3; }
cp "$BUILD/OVMF_VARS.fd" "$OUT/OVMF_VARS.fd" 2>/dev/null || cp /usr/share/OVMF/OVMF_VARS.fd "$OUT/OVMF_VARS.fd"
( cd "$(dirname "$BUILD")" && scripts/boot-vm.sh --headless --mem "$MEM" --cpus "$CPUS" --disk "$OVL" --qmp "$QMP" --vars "$OUT/OVMF_VARS.fd" --serial "$OUT/serial.log" --monitor /tmp/r8-perf.mon > "$OUT/boot.out" 2>&1 & )
echo "== booting a disposable overlay of $DISK"
wait_ssh 150 || { echo "no ssh to the VM ($(tail -3 "$OUT/boot.out" | tr '\n' '|'))"; exit 3; }
wait_session 120 || { echo "no graphical session (plasmashell + kwin_wayland) in the VM"; exit 3; }
vm "nproc; head -1 /proc/meminfo; uptime -p; head -2 /etc/fabos-release 2>/dev/null; dpkg-query -W -f '\${Package} \${Version}\n' 'fabos-*' | tr '\n' ' '"; echo

# ---------------------------------------------------------------- 0. install the packages (the over-the-air path)
n=0
for d in "${DEBS[@]}"; do
  n=$((n+1)); echo; echo "=== 0.$n installing $(ls "$d"/*.deb | wc -l) .deb(s) from $d"
  vm "rm -rf /tmp/perf-debs-$n; mkdir -p /tmp/perf-debs-$n"; $SCP "$d"/*.deb "fabos@127.0.0.1:/tmp/perf-debs-$n/" >/dev/null
  root "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --allow-downgrades -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold /tmp/perf-debs-$n/*.deb" > "$OUT/install-$n.log"; rc=$?
  tail -4 "$OUT/install-$n.log" | sed 's/^/    /'
  vm "dpkg-query -W -f '\${Package} \${Version} \${Status}\n' 'fabos-*'" | sed 's/^/    /'
  if [ $rc = 0 ] && ! grep -q -E '^dpkg: error|subprocess .* returned error|^E: ' "$OUT/install-$n.log"; then verdict PASS "install $n: apt-get exit 0, no dpkg/postinst error ($d)"; else verdict FAIL "install $n: apt-get exit $rc — see $OUT/install-$n.log"; fi
done
if [ ${#DEBS[@]} -gt 0 ] && [ $NO_REBOOT = 0 ]; then reboot_guest; fi

# ---------------------------------------------------------------- 1. leave it alone, then facts + idle sample
echo; echo "=== 1. idle: leaving the session alone for ${IDLE_WAIT}s"
vm "rm -rf /tmp/perf; mkdir -p /tmp/perf"; $SCP "$HERE"/tests/perf/sample.py "$HERE"/tests/perf/launch.py "$HERE"/tests/perf/facts.sh "$HERE"/tests/perf/kwin-events.js fabos@127.0.0.1:/tmp/perf/ >/dev/null
[ "$IDLE_WAIT" -gt 0 ] && sleep "$IDLE_WAIT"
echo "=== 1a. facts in effect -> $OUT/facts.txt"
vms "sh /tmp/perf/facts.sh" > "$OUT/facts.txt" 2>&1
sed -n '/^### system/,/^### power/p' "$OUT/facts.txt" | sed 's/^/    /'
grep -E 'ppd active|swappiness|AllowTearing|Indexing-Enabled|fabos-voiced:|llama-server|fabos-llama.socket|scheduler|governor|power_supply|powercap' "$OUT/facts.txt" | sed 's/^/    /'
echo "=== 1b. ${SAMPLE}s idle sample -> $OUT/idle.json"
vm "python3 /tmp/perf/sample.py $SAMPLE" > "$OUT/idle.json"
python3 - "$OUT/idle.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("    system: cpu busy %.1f%% of one core (%s vCPU), tasks %.2f/s, ctxt %.0f/s, irq %.0f/s (timer %.0f/s), load %s, MemAvailable %d MB of %d, swap used %d MB" % (
    d["system_cpu_busy_pct_one_core"], d["ncpu"], d["tasks_per_s"], d["ctxt_switches_per_s"], d["interrupts_per_s"], d["timer_interrupts_per_s"], " ".join(d["loadavg"]),
    d["meminfo_kb"]["MemAvailable"] // 1024, d["meminfo_kb"]["MemTotal"] // 1024, (d["meminfo_kb"]["SwapTotal"] - d["meminfo_kb"]["SwapFree"]) // 1024))
print("    %-7s %-6s %-9s %-8s %-8s %s" % ("pid", "cpu%", "wakeups/s", "rss_MB", "pss_MB", "command"))
for p in d["top15_cpu"]:
    print("    %-7d %-6.2f %-9.1f %-8.1f %-8.1f %s" % (p["pid"], p["cpu_pct"], p["wakeups_per_s"], p["rss_kb"] / 1024, p["pss_kb"] / 1024, (p["cmd"] or p["comm"])[:70]))
print("    top PSS: " + ", ".join("%s %.0f MB" % (p["comm"], p["pss_kb"] / 1024) for p in d["top15_pss"][:10]))
PY
voiced=$(jq_ "$OUT/idle.json" 'round(sum(p["cpu_pct"] for p in d["procs"] if "fabos_voiced" in p["cmd"] or p["comm"] in ("pw-record","pocketsphinx")), 2)')
voiced_w=$(jq_ "$OUT/idle.json" 'round(sum(p["wakeups_per_s"] for p in d["procs"] if "fabos_voiced" in p["cmd"] or p["comm"] in ("pw-record","pocketsphinx")), 1)')
shell=$(jq_ "$OUT/idle.json" 'round(sum(p["cpu_pct"] for p in d["procs"] if p["comm"]=="plasmashell"), 2)')
kwin=$(jq_ "$OUT/idle.json" 'round(sum(p["cpu_pct"] for p in d["procs"] if p["comm"]=="kwin_wayland"), 2)')
tps=$(jq_ "$OUT/idle.json" 'd["tasks_per_s"]'); syscpu=$(jq_ "$OUT/idle.json" 'd["system_cpu_busy_pct_one_core"]')
hogs=$(jq_ "$OUT/idle.json" '", ".join("%s %.1f%%" % (p["comm"], p["cpu_pct"]) for p in d["procs"] if p["cpu_pct"] >= 5 and p["comm"] not in ("plasmashell","kwin_wayland","sshd","bash","sh"))')
echo "    voice pipeline (fabos_voiced + pw-record + pocketsphinx): ${voiced}% of one core, ${voiced_w} wakeups/s; plasmashell ${shell}%; kwin_wayland ${kwin}%; other hogs >= 5%: ${hogs:-none}"
budget "idle CPU of the voice pipeline ${voiced}% < ${BUDGET_VOICED_PCT}%" "${voiced:-999} < $BUDGET_VOICED_PCT"
budget "idle plasmashell ${shell}% and kwin_wayland ${kwin}% < ${BUDGET_SHELL_PCT}%" "${shell:-999} < $BUDGET_SHELL_PCT and ${kwin:-999} < $BUDGET_SHELL_PCT"
budget "no other process >= ${BUDGET_PROC_PCT}% while idle (${hogs:-none})" "'${hogs}' == ''"
budget "task creations ${tps}/s system-wide while idle < ${BUDGET_TASKS_PER_S}" "${tps:-999} < $BUDGET_TASKS_PER_S"
llama_procs=$(grep -c 'llama process:' "$OUT/facts.txt"); baloo_procs=$(grep -c 'baloo process:' "$OUT/facts.txt")
[ "$llama_procs" = 0 ] && verdict PASS "no llama-server process after login (loaded on demand only; $(grep -o 'ConditionResult=[a-z]*' "$OUT/facts.txt" | head -1) at ${MEM} MB)" || verdict FAIL "llama-server running after login: $(grep 'llama process:' "$OUT/facts.txt")"
grep -q 'exit-idle-time=' "$OUT/facts.txt" && verdict PASS "fabos-llama-proxy carries --$(grep -o 'exit-idle-time=[0-9a-z]*' "$OUT/facts.txt" | head -1) and fabos-llama.service $(grep -o 'StopWhenUnneeded=[a-z]*' "$OUT/facts.txt" | head -1)" || verdict FAIL "fabos-llama-proxy has no --exit-idle-time"
[ "$baloo_procs" = 0 ] && verdict PASS "no baloo_file process ($(grep -o 'Indexing-Enabled=[a-z]*' "$OUT/facts.txt" | head -1))" || verdict FAIL "baloo running: $(grep 'baloo process:' "$OUT/facts.txt")"

# ---------------------------------------------------------------- 2. application launch latency
echo; echo "=== 2. launch latency: $RUNS x konsole, dolphin, kate, firefox (KWin windowAdded on the guest clock)"
vms "pkill -f 'busctl --user [m]onitor' 2>/dev/null; rm -f /tmp/fabos-perf-monitor.jsonl; setsid -f sh -c 'busctl --user monitor --json=short --match \"interface=in.patienceai.fabos.perf\" > /tmp/fabos-perf-monitor.jsonl 2>/tmp/fabos-perf-monitor.err'; sleep 1"
vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-perf >/dev/null 2>&1; qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /tmp/perf/kwin-events.js fabos-perf && qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start" >/dev/null
sleep 2; vm "grep -c 'loaded windows' /tmp/fabos-perf-monitor.jsonl" | sed 's/^/    KWin probe loaded (events seen): /'
: > "$OUT/launch.jsonl"
for spec in "konsole|konsole|konsole" "dolphin|dolphin|dolphin" "kate -n|kate|kate" "firefox|firefox|firefox"; do
  IFS='|' read -r cmd match kill <<< "$spec"
  for r in $(seq 1 "$RUNS"); do
    line=$(vms "python3 /tmp/perf/launch.py '$cmd' $match $kill"); echo "$line" >> "$OUT/launch.jsonl"
    echo "    $match run $r: $(echo "$line" | python3 -c 'import sys,json; j=json.load(sys.stdin); print("%s ms (%s)" % (j["launch_ms"], j["class"] or "timeout"))' 2>/dev/null)"
    sleep 2
  done
done
vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-perf >/dev/null 2>&1; pkill -f 'busctl --user [m]onitor'" 2>/dev/null
python3 - "$OUT/launch.jsonl" "$OUT/launch-summary.json" <<'PY'
import json, sys, statistics
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
out = {}
for app in ("konsole", "dolphin", "kate", "firefox"):
    xs = [r["launch_ms"] for r in rows if r["match"] == app]
    good = [x for x in xs if x is not None]
    warm = [x for x in xs[1:] if x is not None]
    out[app] = {"runs_ms": xs, "cold_ms": xs[0] if xs else None, "warm_median_ms": round(statistics.median(warm)) if warm else None, "timeouts": len(xs) - len(good)}
    print("    %-8s cold %s ms, warm median %s ms, runs %s%s" % (app, out[app]["cold_ms"], out[app]["warm_median_ms"], xs, " TIMEOUTS=%d" % out[app]["timeouts"] if out[app]["timeouts"] else ""))
json.dump(out, open(sys.argv[2], "w"), indent=1)
PY
for app in konsole dolphin kate firefox; do
  warm=$(jq_ "$OUT/launch-summary.json" "d['$app']['warm_median_ms']"); to=$(jq_ "$OUT/launch-summary.json" "d['$app']['timeouts']")
  [ "${to:-1}" = 0 ] && [ "$warm" != None ] || { verdict FAIL "$app: ${to:-?} launch(es) never mapped a window within 90 s"; continue; }
  budget "$app warm launch ${warm} ms < ${BUDGET_LAUNCH_MS[$app]} ms" "$warm < ${BUDGET_LAUNCH_MS[$app]}"
  if [ -n "$BASE_JSON" ] && [ -f "$BASE_JSON" ]; then
    base=$(jq_ "$BASE_JSON" "d['launch']['$app']['warm_median_ms']")
    [ -n "$base" ] && [ "$base" != None ] && budget "$app warm ${warm} ms vs baseline ${base} ms (limit ${BUDGET_REGRESSION_FACTOR}x + ${BUDGET_REGRESSION_SLACK_MS})" "$warm <= $base * $BUDGET_REGRESSION_FACTOR + $BUDGET_REGRESSION_SLACK_MS"
  fi
done

# ---------------------------------------------------------------- 3. performance modes (only when the package is installed)
: > "$OUT/modes.jsonl"; modes_ok=skip
if [ $MODES = 1 ] && vm "command -v fabos-perf-mode >/dev/null"; then
  echo; echo "=== 3. performance modes: set -> read back (fabos-perf-mode status --json)"
  before_mode=$(vms "fabos-perf-mode get 2>/dev/null"); echo "    mode before: ${before_mode:-unset}"
  for m in power-saver performance gaming server balanced; do
    vmu "fabos-perf-mode set $m" > "$OUT/mode-set-$m.log" 2>&1; rc=$?; sleep 2
    js=$(vms "fabos-perf-mode status --json 2>/dev/null"); echo "$js" >> "$OUT/modes.jsonl"
    echo "    set $m (rc=$rc): $(echo "$js" | python3 -c 'import sys,json; j=json.load(sys.stdin); print("mode=%s ppd=%s tearing=%s vrr=%s blur=%s translucency=%s spotter=%s display_off=%s autosuspend=%s governor=%s" % (j.get("mode"), j.get("ppd"), j.get("tearing"), j.get("vrr"), j.get("effects",{}).get("blur"), j.get("effects",{}).get("translucency"), j.get("spotter"), j.get("display",{}).get("turn_off_when_idle"), j.get("display",{}).get("autosuspend"), j.get("governor")))' 2>/dev/null || echo "$js")"
    [ $rc = 0 ] || echo "    $(tail -3 "$OUT/mode-set-$m.log" | tr '\n' '|')"
  done
  python3 - "$OUT/modes.jsonl" <<'PY' && modes_ok=yes || modes_ok=no
import json, sys
# the mode table of docs/PERFORMANCE.md: what `status --json` must read back after `set <mode>`
want = {"power-saver": dict(ppd="power-saver", tearing=False, spotter="battery-off", blur=None, translucency=None, display_off=None, autosuspend=None),
        "performance": dict(ppd="performance", tearing=False, spotter="on", blur=None, translucency=None, display_off=None, autosuspend=None),
        "gaming":      dict(ppd="performance", tearing=True, spotter="on", blur=False, translucency=False, display_off=None, autosuspend=None),
        "server":      dict(ppd="balanced", tearing=False, spotter="off", blur=False, translucency=False, display_off=False, autosuspend=0),
        "balanced":    dict(ppd="balanced", tearing=False, spotter="on", blur=None, translucency=None, display_off=None, autosuspend=None)}
ok = True
for line in open(sys.argv[1]):
    if not line.strip(): continue
    j = json.loads(line); m = j.get("mode"); w = want.get(m)
    if not w: print("    unexpected mode in status:", m); ok = False; continue
    got = dict(ppd=j.get("ppd"), tearing=j.get("tearing"), spotter=j.get("spotter"), blur=j.get("effects", {}).get("blur"), translucency=j.get("effects", {}).get("translucency"),
               display_off=j.get("display", {}).get("turn_off_when_idle"), autosuspend=j.get("display", {}).get("autosuspend"))
    for k, v in w.items():
        if v is None: continue           # "whatever the user had" (restored from the snapshot) — not asserted
        if got[k] != v: print("    %s: %s = %r, expected %r" % (m, k, got[k], v)); ok = False
    if j.get("persisted") is not True: print("    %s: not persisted" % m); ok = False
    if j.get("ppd_available") is False: print("    %s: power-profiles-daemon not reachable" % m); ok = False
print("    mode table %s" % ("holds" if ok else "VIOLATED"))
sys.exit(0 if ok else 1)
PY
  [ "$modes_ok" = yes ] && verdict PASS "performance modes read back as the mode table says (5 modes)" || verdict FAIL "performance modes: table violated (see $OUT/modes.jsonl)"
  if [ $REBOOT_CHECK = 1 ]; then
    echo "    persistence: set gaming, reboot, read back"
    vmu "fabos-perf-mode set gaming" >/dev/null 2>&1; reboot_guest; sleep 20
    js=$(vms "fabos-perf-mode status --json 2>/dev/null"); echo "$js" > "$OUT/mode-after-reboot.json"; echo "    after reboot: $js"
    echo "$js" | python3 -c 'import sys,json; j=json.load(sys.stdin); sys.exit(0 if j.get("mode")=="gaming" and j.get("ppd")=="performance" and j.get("tearing") is True else 1)' && verdict PASS "gaming mode survived the reboot (mode, ppd, tearing)" || verdict FAIL "gaming mode did not survive the reboot: $js"
    vmu "fabos-perf-mode set balanced" >/dev/null 2>&1
  fi
  vmu "fabos-perf-mode set ${before_mode:-balanced}" >/dev/null 2>&1
elif [ $MODES = 1 ]; then echo; echo "=== 3. performance modes: fabos-perf-mode not installed in this guest (1.0-7 or older) — skipped"; verdict SKIP "performance modes (no fabos-perf-mode)"; fi

# ---------------------------------------------------------------- 4. voice spotter switch (only when the daemon knows voice.spotter)
if vm "grep -q 'voice.spotter' /usr/lib/fabos/voice/fabos_voiced.py 2>/dev/null"; then
  echo; echo "=== 4. voice.spotter setting: off -> no spotter process; on -> back (the daemon re-reads settings and policy every 30 s; up to ~60 s for the return)"
  t4=$(date +%s); vm "fabos settings voice.spotter off >/dev/null 2>&1"; for i in $(seq 1 75); do [ "$(vm 'pgrep -c pocketsphinx')" = 0 ] && break; sleep 1; done
  sp_off=$(vm "pgrep -c pocketsphinx"); st_off=$(vm "fabos-voice status 2>/dev/null"); t_off=$(( $(date +%s) - t4 ))
  t4=$(date +%s); vm "fabos settings voice.spotter on >/dev/null 2>&1"; for i in $(seq 1 90); do [ "$(vm 'pgrep -c pocketsphinx')" != 0 ] && break; sleep 1; done; t_on=$(( $(date +%s) - t4 ))
  echo "    spotter gone after ${t_off}s, back after ${t_on}s"
  sp_on=$(vm "pgrep -c pocketsphinx"); st_on=$(vm "fabos-voice status 2>/dev/null")
  echo "    off: pocketsphinx=$sp_off $st_off"; echo "    on:  pocketsphinx=$sp_on $st_on"
  [ "$sp_off" = 0 ] && [ "$sp_on" != 0 ] && verdict PASS "voice.spotter off stops the spotter, on brings it back" || verdict FAIL "voice.spotter: off->$sp_off process(es), on->$sp_on"
fi

# ---------------------------------------------------------------- summary
python3 - "$JSON" "$pass" "$fail" "$skip" "$OUT/idle.json" "$OUT/launch-summary.json" "$OUT/modes.jsonl" "$voiced" "$voiced_w" "$shell" "$kwin" "$tps" "$syscpu" "$MEM" "$CPUS" "$BASELINE" <<'PY'
import json, sys, os
a = sys.argv
def load(p, default):
    try: return json.load(open(p))
    except Exception: return default
idle = load(a[5], {})
modes = [json.loads(l) for l in open(a[7]) if l.strip()] if os.path.exists(a[7]) else []
out = {"pass": int(a[2]), "fail": int(a[3]), "skip": int(a[4]), "mem_mb": int(a[14]), "cpus": int(a[15]), "baseline": a[16] == "1",
       "voice_pipeline_pct": float(a[8] or 0), "voice_pipeline_wakeups_per_s": float(a[9] or 0), "plasmashell_pct": float(a[10] or 0), "kwin_pct": float(a[11] or 0),
       "tasks_per_s": float(a[12] or 0), "system_cpu_pct_one_core": float(a[13] or 0),
       "idle": {k: idle.get(k) for k in ("elapsed_s", "ncpu", "system_cpu_busy_pct_one_core", "tasks_per_s", "ctxt_switches_per_s", "interrupts_per_s", "timer_interrupts_per_s", "loadavg", "meminfo_kb", "top15_cpu", "top15_pss")},
       "launch": load(a[6], {}), "modes": modes}
json.dump(out, open(a[1], "w"), indent=1)
PY
echo; echo "### SUMMARY: $pass PASS / $fail FAIL / $skip SKIP  (numbers: $JSON, log: $LOG, facts: $OUT/facts.txt)"
[ $BASELINE = 1 ] && exit 0
exit $fail
