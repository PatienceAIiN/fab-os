#!/usr/bin/env bash
# Fab OS — Firefox in the booted VM, driven over SSH like tests/agent-live-vm.sh (vm profile: openssh-server on :2222,
# user fabos / password fabos, autologin into the Plasma session). Every PASS/FAIL below is backed by something read in
# the VM — dpkg, xdg-settings / xdg-mime, KWin's own window list, pgrep, the session bus, a spectacle screenshot — never
# by the source tree. kdotool is not packaged for Ubuntu 26.04 and KWin's D-Bus interface has no window list, so a small
# KWin script reports every window (resourceClass|resourceName|caption) and every windowAdded as a D-Bus call that
# `busctl --user monitor` timestamps. The monitor + parser half is checked without a VM by `--selftest` (a private dbus-daemon
# session, the same monitor line, calls shaped like the probe's); the KWin half (callDBus from the script, windowAdded) is only
# proven by the VM run itself — if the script does not report, the run falls back to Firefox's org.mozilla.firefox bus name.
#
#   tests/browser-vm.sh [--budget S] [--keep] [--skip-agent] [--selftest]
#     --budget S    seconds allowed from the first firefox process to its first window (default 12)
#     --keep        leave Firefox open and a VM this script booted powered on
#     --skip-agent  do not drive the agent (`fabos do "open firefox"`); only the direct and xdg-open launches
#     --selftest    no VM: run the window-probe monitor + parser under a private dbus-daemon session and exit (PASS/FAIL)
#   ANTHROPIC_API_KEY (optional): configures the Claude provider when the agent has no ready provider.
#   VM_MEM (default 2048): scripts/boot-vm.sh is started if no VM is running.
# Output: build/browser-vm.out (log), build/browser-vm.json (numbers), build/browser-firefox.png (screenshot).
#
# What is checked
#   1. firefox is Mozilla's build (dpkg Maintainer), version printed; brave-browser absent; policies.json shipped + valid + SkipTermsOfUse
#   2. `xdg-settings get default-web-browser` == firefox.desktop; `xdg-mime query default x-scheme-handler/https` too
#   3. agent path: `fabos do --mode bypass "open firefox"` -> a Firefox window appears; process -> window < budget
#      (the model's thinking time before the launch is reported, not judged); which tool the agent used is printed
#   4. direct path: Firefox closed, `firefox` launched in the session -> first window < budget; exactly one Firefox
#      window and no first-run caption (Terms of Use / Privacy Notice / Welcome); screenshot saved to build/browser-firefox.png
#   5. `xdg-open https://fabos.patienceai.in/` with Firefox closed -> a Firefox window < budget (the default handler)
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
BUDGET=12; KEEP=0; SKIP_AGENT=0; SELFTEST=0
while [ $# -gt 0 ]; do case "$1" in --budget) BUDGET=$2; shift;; --keep) KEEP=1;; --skip-agent) SKIP_AGENT=1;; --selftest) SELFTEST=1;; -h|--help) sed -n '2,27p' "$0"; exit 0;; *) echo "unknown argument: $1"; exit 3;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222"
mkdir -p build; OUT=build/browser-vm.out; JSON=build/browser-vm.json; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
vm() { $SSH "$@"; }
# the graphical session's environment for anything that must talk to KWin / Wayland / the session bus from the ssh login
SENV='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus; export WAYLAND_DISPLAY=$(cd $XDG_RUNTIME_DIR && ls -d wayland-? 2>/dev/null | head -1); export QT_QPA_PLATFORM=wayland XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=KDE KDE_SESSION_VERSION=6;'
vms() { vm "$SENV $*"; }
api() { vm "curl -s -m 30 -X $1 -H \"Authorization: Bearer \$(cat \$XDG_RUNTIME_DIR/fabos-agent/token)\" http://127.0.0.1:8790$2"; }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }
pass=0; fail=0; skip=0
verdict() { case "$1" in PASS) pass=$((pass+1));; FAIL) fail=$((fail+1));; SKIP) skip=$((skip+1));; esac; echo ">>> $1: $2"; }
declare -A R; R[budget_s]=$BUDGET; BOOTED=0

# ---------------------------------------------------------------- window probe (KWin script + session-bus monitor)
cat > build/browser-kwin.js <<'JS'
// Fab OS browser probe: KWin reports every window now and each windowAdded / windowRemoved as a D-Bus call to the bus
// driver under our own interface. Nobody answers it (UnknownMethod, ignored) but `busctl --user monitor` sees every call
// with a microsecond timestamp; t= is KWin's own clock (ms since the epoch) for the time-to-window measurement.
function wl(w) { return String(w.resourceClass) + "|" + String(w.resourceName) + "|" + String(w.caption).replace(/[\s|]+/g, "_"); }
function report(msg) { callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus", "in.patienceai.fabos.browsertest", "event", msg + " t=" + Date.now()); }
var ws = workspace.windowList();
for (var i = 0; i < ws.length; i++) { report("existing " + wl(ws[i])); }
report("loaded windows=" + ws.length);
workspace.windowAdded.connect(function (w) { report("added " + wl(w)); });
workspace.windowRemoved.connect(function (w) { report("removed " + wl(w)); });
JS
cat > build/browser-ffwin.py <<'PY'
#!/usr/bin/env python3
# Reads /tmp/browser-monitor.jsonl (busctl --user monitor --json=short of the in.patienceai.fabos.browsertest calls made by
# the Fab OS KWin probe) and prints one JSON line about Firefox windows: how many were added after AFTER_MS (KWin clock),
# the first such time, the current Firefox window count, and the last captions seen.
import json, sys
after = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
added, captions, cur = [], [], 0
try:
    lines = open("/tmp/browser-monitor.jsonl", encoding="utf-8", errors="replace").read().splitlines()
except FileNotFoundError:
    lines = []
for line in lines:
    try:
        j = json.loads(line)
    except Exception:
        continue
    if j.get("type") != "method_call" or j.get("member") != "event":
        continue
    data = ((j.get("payload") or {}).get("data") or [""])[0]
    parts = data.split(" ")
    if len(parts) < 2 or not parts[-1].startswith("t="):
        continue
    kind, win = parts[0], " ".join(parts[1:-1])
    try:
        t = float(parts[-1][2:])
    except ValueError:
        t = 0.0
    fields = win.split("|")
    if "firefox" not in (fields[0] + "|" + (fields[1] if len(fields) > 1 else "")).lower():
        continue
    if kind in ("existing", "added"):
        cur += 1; captions.append(fields[-1])
    elif kind == "removed":
        cur = max(0, cur - 1)
    if kind == "added" and t >= after:
        added.append(t)
print(json.dumps({"added": len(added), "first_t": int(min(added)) if added else 0, "current": cur, "captions": captions[-3:]}))
PY

if [ $SELFTEST = 1 ]; then
  # --selftest: the monitor + parser half without a VM. A private dbus-daemon session, the same `busctl --user monitor` line the
  # VM run uses, five calls shaped like the KWin probe's (nobody answers them — the bus driver returns an error — but the monitor
  # sees every method_call), then the parser with AFTER=1500: expect added=2, first_t=2000, current=1 (one added, one removed).
  # The KWin half (callDBus from the script, windowAdded) is only proven by the VM run; its fallback is Firefox's bus name.
  rm -f /tmp/browser-monitor.jsonl
  if command -v dbus-run-session >/dev/null 2>&1 && command -v busctl >/dev/null 2>&1; then
    src="dbus-daemon session + busctl monitor"
    dbus-run-session -- bash -c 'busctl --user monitor --json=short --match "interface=in.patienceai.fabos.browsertest" > /tmp/browser-monitor.jsonl 2>/dev/null & m=$!; sleep 1
      for e in "existing konsole|konsole|Fab_Terminal t=1000" "loaded windows=1 t=1001" "added firefox|firefox|Mozilla_Firefox t=2000" "added firefox|firefox|Fab_OS_Mozilla_Firefox t=2600" "removed firefox|firefox|Mozilla_Firefox t=2900"; do
        busctl --user call org.freedesktop.DBus /org/freedesktop/DBus in.patienceai.fabos.browsertest event s "$e" >/dev/null 2>&1; done
      sleep 1; kill $m 2>/dev/null; wait $m 2>/dev/null; true'
    echo "    monitor lines: $(grep -c method_call /tmp/browser-monitor.jsonl 2>/dev/null || echo 0) method_call(s) captured"
  else
    src="synthetic monitor log (no dbus-run-session/busctl on this host)"
    python3 - <<'PY2'
import json
rows = [("existing konsole|konsole|Fab_Terminal t=1000"), ("loaded windows=1 t=1001"), ("added firefox|firefox|Mozilla_Firefox t=2000"),
        ("added firefox|firefox|Fab_OS_Mozilla_Firefox t=2600"), ("removed firefox|firefox|Mozilla_Firefox t=2900")]
with open("/tmp/browser-monitor.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps({"type": "method_call", "member": "event", "interface": "in.patienceai.fabos.browsertest", "payload": {"type": "s", "data": [r]}}) + "\n")
PY2
  fi
  j=$(python3 build/browser-ffwin.py 1500); echo "    parser ($src): $j"
  if [ "$(echo "$j" | jget '["added"]')" = 2 ] && [ "$(echo "$j" | jget '["first_t"]')" = 2000 ] && [ "$(echo "$j" | jget '["current"]')" = 1 ]; then
    verdict PASS "window probe monitor + parser: added=2 first_t=2000 current=1 ($src)"
  else verdict FAIL "window probe monitor + parser: unexpected $j ($src)"; fi
  echo "### browser-vm --selftest: $pass PASS / $fail FAIL — log build/browser-vm.out"; [ $fail -eq 0 ]; exit $?
fi

echo "### Fab OS browser VM test — $(date -u +%FT%TZ) — budget=${BUDGET}s keep=$KEEP skip_agent=$SKIP_AGENT"
pgrep -f qemu-system-x86_64 >/dev/null || { echo "booting VM"; BOOTED=1; (scripts/boot-vm.sh --headless --mem "${VM_MEM:-2048}" --cpus 4 > build/boot-headless.out 2>&1 &); }
for i in $(seq 1 100); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM"; exit 3; }
echo "### waiting for the graphical session (plasmashell + kwin_wayland)"
for i in $(seq 1 120); do vm "pgrep -x plasmashell >/dev/null && pgrep -x kwin_wayland >/dev/null" && break; sleep 3; done
vm "pgrep -x plasmashell >/dev/null" || { echo "no plasmashell in the VM (is the session logged in?)"; exit 3; }
vm "nproc; head -1 /proc/meminfo; uptime -p; grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release"

# ---------------------------------------------------------------- 1. package
echo; echo "=== 1. package"
FFV=$(vm "dpkg-query -W -f '\${Version}' firefox 2>/dev/null"); FFM=$(vm "dpkg-query -W -f '\${Maintainer}' firefox 2>/dev/null")
R[firefox_version]=${FFV:-absent}; R[firefox_maintainer]=${FFM:-absent}
if echo "$FFM" | grep -q '^Mozilla'; then verdict PASS "firefox $FFV is Mozilla's build ($FFM)"; else verdict FAIL "firefox is not Mozilla's build (version='${FFV:-absent}' maintainer='${FFM:-absent}')"; fi
if vm "dpkg -s brave-browser >/dev/null 2>&1"; then verdict FAIL "brave-browser is still installed"; else verdict PASS "brave-browser absent"; fi
if vm "test -f /usr/lib/firefox/distribution/policies.json && python3 -m json.tool /usr/lib/firefox/distribution/policies.json >/dev/null 2>&1 && grep -q '\"SkipTermsOfUse\": true' /usr/lib/firefox/distribution/policies.json"; then verdict PASS "policies.json shipped, valid, SkipTermsOfUse on ($(vm "grep -c '\"' /usr/lib/firefox/distribution/policies.json") quoted lines)"; else verdict FAIL "policies.json missing, invalid or without SkipTermsOfUse at /usr/lib/firefox/distribution/policies.json"; fi
vm "test -f /etc/apparmor.d/firefox && aa-status 2>/dev/null | grep -qE '^ +firefox$'" && echo "    apparmor: firefox profile (userns) loaded" || echo "    apparmor: firefox profile not reported by aa-status (needs sudo, or not loaded)"

# ---------------------------------------------------------------- 2. default browser
echo; echo "=== 2. default browser"
DB=$(vms "xdg-settings get default-web-browser 2>/dev/null" | tail -1); MM=$(vms "xdg-mime query default x-scheme-handler/https 2>/dev/null" | tail -1)
R[default_browser]=${DB:-none}; R[mime_https]=${MM:-none}
[ "$DB" = firefox.desktop ] && verdict PASS "xdg-settings get default-web-browser = firefox.desktop" || verdict FAIL "xdg-settings get default-web-browser = '${DB:-}' (want firefox.desktop)"
[ "$MM" = firefox.desktop ] && verdict PASS "xdg-mime query default x-scheme-handler/https = firefox.desktop" || verdict FAIL "xdg-mime query default x-scheme-handler/https = '${MM:-}' (want firefox.desktop)"

# ---------------------------------------------------------------- window probe: install in the VM (files written above)
$SCP build/browser-kwin.js build/browser-ffwin.py fabos@127.0.0.1:/tmp/ >/dev/null
vms "pkill -f 'busctl --user monitor' 2>/dev/null; rm -f /tmp/browser-monitor.jsonl; setsid -f sh -c 'busctl --user monitor --json=short --match \"interface=in.patienceai.fabos.browsertest\" > /tmp/browser-monitor.jsonl 2>&1'"
sleep 1
vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-browsertest >/dev/null 2>&1; qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /tmp/browser-kwin.js fabos-browsertest && qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start" >/dev/null 2>&1
sleep 1
if [ "$(vm "grep -c 'loaded windows=' /tmp/browser-monitor.jsonl 2>/dev/null")" -ge 1 ] 2>/dev/null; then WINPROBE=kwin; echo "    window probe: KWin script + bus monitor active ($(vm "grep -o 'loaded windows=[0-9]*' /tmp/browser-monitor.jsonl | tail -1"))"
else WINPROBE=none; echo "    window probe: the KWin script did not report — falling back to the firefox process + its org.mozilla.firefox bus name"; fi
R[window_probe]=$WINPROBE

ffwin() { vm "python3 /tmp/browser-ffwin.py $1"; }                     # -> JSON: added first_t current captions
ffkill() { vm "pkill -x 'firefox|firefox-bin' 2>/dev/null; true"; for i in $(seq 1 20); do vm "pgrep -x 'firefox|firefox-bin' >/dev/null" || return 0; sleep 0.5; done; vm "pkill -9 -x 'firefox|firefox-bin' 2>/dev/null; true"; sleep 1; }
procwatch() { vm "rm -f /tmp/browser-procstart; setsid -f sh -c 'while ! pgrep -x \"firefox|firefox-bin\" >/dev/null; do sleep 0.05; done; date +%s%3N > /tmp/browser-procstart'"; }
ms() { python3 -c "print(round(($1 - $2) / 1000.0, 2))" 2>/dev/null || echo "?"; }
# wait_window T0_MS MAX_S -> sets W_ADDED W_FIRST W_CUR W_CAPTIONS T_PROC T_WINDOW T_TOTAL (seconds; "?" when unknown)
wait_window() {
  local t0=$1 max=$2 j; W_ADDED=0; W_FIRST=0; W_CUR=0; W_CAPTIONS=""; T_PROC=?; T_WINDOW=?; T_TOTAL=?
  for i in $(seq 1 $((max * 2))); do
    if [ "$WINPROBE" = kwin ]; then
      j=$(ffwin "$t0"); W_ADDED=$(echo "$j" | jget '["added"]'); W_FIRST=$(echo "$j" | jget '["first_t"]'); W_CUR=$(echo "$j" | jget '["current"]'); W_CAPTIONS=$(echo "$j" | jget '["captions"]')
      [ "${W_ADDED:-0}" -ge 1 ] 2>/dev/null && break
    else   # fallback: Firefox registers org.mozilla.firefox.<profile> on the session bus once its first window is up
      if vms "busctl --user list --no-legend 2>/dev/null | grep -q org.mozilla.firefox"; then W_ADDED=1; W_FIRST=$(vm "date +%s%3N"); W_CUR=$(vm "pgrep -x 'firefox|firefox-bin' | wc -l"); W_CAPTIONS="(bus name org.mozilla.firefox.*)"; break; fi
    fi
    sleep 0.5
  done
  local tp; tp=$(vm "cat /tmp/browser-procstart 2>/dev/null")
  if [ "${W_ADDED:-0}" -ge 1 ] 2>/dev/null; then
    T_TOTAL=$(ms "$W_FIRST" "$t0"); [ -n "$tp" ] && { T_PROC=$(ms "$tp" "$t0"); T_WINDOW=$(ms "$W_FIRST" "$tp"); } || T_WINDOW=$T_TOTAL
  fi
}
budget_ok() { python3 -c "import sys; sys.exit(0 if float('$1') < float('$BUDGET') else 1)" 2>/dev/null; }

# ---------------------------------------------------------------- 3. agent path
echo; echo "=== 3. agent path: fabos do --mode bypass \"open firefox\""
if [ $SKIP_AGENT = 1 ]; then verdict SKIP "agent path skipped (--skip-agent)"
else
  for i in $(seq 1 30); do api GET /health 2>/dev/null | grep -q '"ok"' && break; sleep 2; done
  ready=$(vm "fabos --json status 2>/dev/null" | jget '["provider_ready"]')
  if [ "$ready" != True ] && [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    vm "fabos settings provider claude >/dev/null; fabos settings ai.enabled true >/dev/null; printf '%s\n' '$ANTHROPIC_API_KEY' | fabos set-key claude >/dev/null 2>&1"
    ready=$(vm "fabos --json status 2>/dev/null" | jget '["provider_ready"]')
  fi
  if [ "$ready" != True ]; then verdict SKIP "agent path: no AI provider is ready in the VM (set ANTHROPIC_API_KEY or configure one); the direct launches below still run"
  else
    ffkill; procwatch; t0=$(vm "date +%s%3N")
    TASK=$(vm "fabos --json do --mode bypass 'open firefox' 2>/dev/null" | jget '["id"]'); echo "    task #${TASK:-?} created"
    status=""; tool=""
    for i in $(seq 1 90); do
      t=$(vm "fabos --json show ${TASK:-0} 2>/dev/null"); status=$(echo "$t" | jget '["status"]')
      tool=$(echo "$t" | python3 -c 'import sys,json; d=json.load(sys.stdin); s=[x for x in d.get("steps",[]) if (x.get("name") in ("open_app","run_shell")) and "firefox" in json.dumps(x.get("input","")).lower()]; print(s[0]["name"] if s else "")' 2>/dev/null)
      vm "pgrep -x 'firefox|firefox-bin' >/dev/null" && break
      case "$status" in done|failed|cancelled) break;; esac
      sleep 1
    done
    wait_window "$t0" $((BUDGET + 10))
    echo "    task status=$status tool=${tool:-none} thinking+launch=${T_PROC}s process->window=${T_WINDOW}s total=${T_TOTAL}s windows=${W_CUR} captions=${W_CAPTIONS}"
    R[agent_status]=$status; R[agent_tool]=${tool:-none}; R[agent_process_to_window_s]=$T_WINDOW; R[agent_total_s]=$T_TOTAL; R[agent_think_s]=$T_PROC
    if [ "${W_ADDED:-0}" -ge 1 ] 2>/dev/null; then
      if budget_ok "$T_WINDOW"; then verdict PASS "agent opened Firefox with ${tool:-?}: process->window ${T_WINDOW}s (< ${BUDGET}s), task->window ${T_TOTAL}s"; else verdict FAIL "agent opened Firefox but process->window took ${T_WINDOW}s (limit ${BUDGET}s)"; fi
    else verdict FAIL "agent path: no Firefox window after the task (status=$status tool=${tool:-none}); $(echo "$t" | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("result") or d.get("error") or "")[:200])' 2>/dev/null)"; fi
    [ "${tool:-}" = open_app ] && echo "    show-your-work: the agent used open_app (visible launch), as the system prompt asks" || echo "    note: the agent did not use open_app (tool=${tool:-none})"
  fi
fi

# ---------------------------------------------------------------- 4. direct launch + screenshot
echo; echo "=== 4. direct launch: firefox from the session (first window within ${BUDGET}s)"
ffkill; procwatch; t0=$(vm "date +%s%3N")
vms "setsid -f firefox >/dev/null 2>&1"
wait_window "$t0" $((BUDGET + 10))
echo "    launch->process=${T_PROC}s process->window=${T_WINDOW}s launch->window=${T_TOTAL}s windows=${W_CUR} captions=${W_CAPTIONS}"
R[direct_process_to_window_s]=$T_WINDOW; R[direct_launch_to_window_s]=$T_TOTAL; R[direct_windows]=${W_CUR:-0}
if [ "${W_ADDED:-0}" -ge 1 ] 2>/dev/null; then
  if budget_ok "$T_TOTAL"; then verdict PASS "Firefox window ${T_TOTAL}s after launch (process->window ${T_WINDOW}s; limit ${BUDGET}s)"; else verdict FAIL "Firefox window took ${T_TOTAL}s after launch (limit ${BUDGET}s)"; fi
else verdict FAIL "no Firefox window within $((BUDGET + 10))s of launch (processes: $(vm "pgrep -x 'firefox|firefox-bin' | wc -l"))"; fi
sleep 3
if [ "$WINPROBE" = kwin ]; then
  j=$(ffwin 0); W_CUR=$(echo "$j" | jget '["current"]')
  [ "${W_CUR:-0}" = 1 ] && verdict PASS "exactly one Firefox window after first launch (no first-run extras; policies.json)" || verdict FAIL "Firefox window count after first launch = ${W_CUR:-?} (want 1): $(echo "$j" | jget '["captions"]')"
  caps=$(echo "$j" | jget '["captions"]')
  if echo "$caps" | grep -qiE 'terms.of.use|privacy.notice|welcome|choose.what.to.import'; then verdict FAIL "a first-run screen is showing (caption): $caps"; else verdict PASS "no first-run caption (Terms of Use / Privacy Notice / Welcome): captions=$caps"; fi
else verdict SKIP "window count not measurable without the KWin probe"; fi
echo "    bus names: $(vms "busctl --user list --no-legend 2>/dev/null | grep -o 'org.mozilla.firefox[^ ]*' | head -2 | tr '\n' ' '")"
vms "rm -f /tmp/browser-firefox.png; spectacle -b -n -f -o /tmp/browser-firefox.png >/dev/null 2>&1"
for i in $(seq 1 10); do vm "test -s /tmp/browser-firefox.png" && break; sleep 1; done
if vm "test -s /tmp/browser-firefox.png" && $SCP fabos@127.0.0.1:/tmp/browser-firefox.png build/browser-firefox.png >/dev/null 2>&1; then verdict PASS "screenshot saved: build/browser-firefox.png ($(stat -c %s build/browser-firefox.png) bytes)"
elif [ -S build/qemu-monitor.sock ] && scripts/vm-screenshot.sh browser-firefox >/dev/null 2>&1 && cp build/screenshots/browser-firefox.png build/browser-firefox.png; then verdict PASS "screenshot saved through the QEMU monitor: build/browser-firefox.png"
else verdict FAIL "no screenshot (spectacle in the session and the QEMU monitor both failed)"; fi

# ---------------------------------------------------------------- 5. xdg-open (the default handler)
echo; echo "=== 5. xdg-open https://fabos.patienceai.in/ with Firefox closed"
ffkill; procwatch; t0=$(vm "date +%s%3N")
vms "setsid -f xdg-open https://fabos.patienceai.in/ >/dev/null 2>&1"
wait_window "$t0" $((BUDGET + 10))
echo "    launch->process=${T_PROC}s process->window=${T_WINDOW}s launch->window=${T_TOTAL}s captions=${W_CAPTIONS}"
R[xdg_open_launch_to_window_s]=$T_TOTAL
if [ "${W_ADDED:-0}" -ge 1 ] 2>/dev/null; then
  if budget_ok "$T_TOTAL"; then verdict PASS "xdg-open spawned Firefox: window ${T_TOTAL}s after the call (limit ${BUDGET}s)"; else verdict FAIL "xdg-open spawned Firefox but the window took ${T_TOTAL}s (limit ${BUDGET}s)"; fi
else verdict FAIL "xdg-open did not produce a Firefox window (handler: $(vms "xdg-mime query default x-scheme-handler/https 2>/dev/null" | tail -1); processes: $(vm "pgrep -x 'firefox|firefox-bin' | wc -l"))"; fi
sleep 4; [ "$WINPROBE" = kwin ] && echo "    page caption after 4 s: $(ffwin 0 | jget '["captions"]')"

# ---------------------------------------------------------------- summary
vms "qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript fabos-browsertest >/dev/null 2>&1; pkill -f 'busctl --user monitor' 2>/dev/null; true"
[ $KEEP = 1 ] || ffkill
python3 - "$JSON" "$pass" "$fail" "$skip" "${!R[@]}" <<'PY' -- "${R[@]}"
import json, sys
args = sys.argv[1:]; out, p, f, s = args[0], int(args[1]), int(args[2]), int(args[3])
rest = args[4:]; sep = rest.index("--"); keys, vals = rest[:sep], rest[sep + 1:]
d = dict(zip(keys, vals)); d.update({"pass": p, "fail": f, "skip": s})
json.dump(d, open(out, "w"), indent=1, sort_keys=True); print("    numbers: " + out)
PY
echo; echo "### browser-vm: $pass PASS / $fail FAIL / $skip SKIP — log build/browser-vm.out"
if [ $KEEP = 1 ]; then echo "    --keep: VM and Firefox left running"
elif [ $BOOTED -eq 1 ]; then echo "    powering off the VM this script booted"; vm "echo fabos | sudo -S poweroff" >/dev/null 2>&1; fi
[ $fail -eq 0 ]
