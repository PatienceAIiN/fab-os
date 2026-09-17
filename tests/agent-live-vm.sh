#!/usr/bin/env bash
# Live end-to-end test of the Fab OS agent INSIDE the booted VM, driven over SSH (vm profile ships openssh-server).
# Real provider (Claude), real desktop session (GUI tasks), real mail through the user's OWN account (ADR-0014) to the address you authorise.
#   ANTHROPIC_API_KEY=... MAIL_ADDRESS=you@gmail.com MAIL_APP_PASSWORD=... MAIL_TO=friend@example.com [MAIL_PROVIDER=gmail] tests/agent-live-vm.sh [--model claude-sonnet-5] [--keep]
# Output: build/agent-live-vm.out (full log) — every PASS/FAIL below is backed by a file/log check run in the VM.
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
MODEL=${MODEL:-claude-opus-5}; HEAVY_MODEL=${HEAVY_MODEL:-$MODEL}; MAIL_TO=${MAIL_TO:-}; KEEP=0
while [ $# -gt 0 ]; do case "$1" in --model) MODEL=$2; shift;; --heavy-model) HEAVY_MODEL=$2; shift;; --keep) KEEP=1;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
OUT=build/agent-live-vm.out; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
vm() { $SSH "$@"; }
api() { # api METHOD PATH [JSON]
  local body=${3:-}
  vm "curl -s -m 30 -X $1 -H \"Authorization: Bearer \$(cat \$XDG_RUNTIME_DIR/fabos-agent/token)\" -H 'Content-Type: application/json' ${body:+-d $(printf %q "$body")} http://127.0.0.1:8790$2"; }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval('d$1') if 'd' in '$1' else d$1)" 2>/dev/null; }
pass=0; fail=0
verdict() { if [ "$1" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); fi; echo ">>> $1: $2"; }

echo "### Fab OS agent live test — $(date -u +%FT%TZ) — model=$MODEL heavy=$HEAVY_MODEL mail_to=${MAIL_TO:-none}"
pgrep -f qemu-system-x86_64 >/dev/null || { echo "booting VM"; (scripts/boot-vm.sh --headless --mem ${VM_MEM:-2048} --cpus 4 > build/boot-headless.out 2>&1 &); }
for i in $(seq 1 100); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM"; exit 1; }
for i in $(seq 1 60); do api GET /health 2>/dev/null | grep -q '"ok"' && break; sleep 3; done
api GET /health | grep -q '"ok"' || { echo "agent daemon not reachable in the session"; exit 1; }

if [ "${INJECT:-0}" = 1 ]; then   # test the working-tree agent code without rebuilding the image
  echo "### injecting working-tree agent files into the VM"
  sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222 packages/fabos-agent/usr/lib/fabos/agent/fabos_agentd.py packages/fabos-agent/usr/lib/fabos/agent/scheduler.py packages/fabos-agent/usr/lib/fabos/agent/command_center.py packages/fabos-agent/usr/bin/fabos fabos@127.0.0.1:/tmp/ >/dev/null
  for f in fabos_agentd.py command_center.py fabos; do local_sz=$(stat -c %s "$(ls packages/fabos-agent/usr/lib/fabos/agent/$f packages/fabos-agent/usr/bin/$f 2>/dev/null | head -1)"); remote_sz=$(vm "stat -c %s /tmp/$f" 2>/dev/null)
    [ "$local_sz" = "$remote_sz" ] || { echo "inject: size mismatch for $f ($local_sz vs $remote_sz) — aborting"; exit 1; }; done
  vm "echo fabos | sudo -S install -m 755 /tmp/fabos_agentd.py /tmp/scheduler.py /tmp/command_center.py /usr/lib/fabos/agent/ 2>/dev/null; echo fabos | sudo -S install -m 755 /tmp/fabos /usr/bin/fabos 2>/dev/null; systemctl --user restart fabos-agent; sleep 4; systemctl --user is-active fabos-agent; test -s /usr/lib/fabos/agent/fabos_agentd.py && echo injected-ok"
fi
echo "### configure provider + mail"
if [ "${AGENT_PROVIDER:-claude}" = fake ]; then   # the scripted provider (no network): the concurrency proof can run offline
  vm "fabos settings provider fake >/dev/null; fabos settings ai.enabled true >/dev/null; fabos mode auto >/dev/null"
else
  vm "fabos settings provider claude >/dev/null; fabos settings claude.model $MODEL >/dev/null; fabos settings ai.enabled true >/dev/null; fabos mode auto >/dev/null; printf '%s\n' '$ANTHROPIC_API_KEY' | fabos set-key claude >/dev/null 2>&1"
fi
if [ -n "${MAIL_APP_PASSWORD:-}" ] && [ -n "${MAIL_ADDRESS:-}" ] && [ -n "$MAIL_TO" ]; then
  vm "fabos settings mail.provider ${MAIL_PROVIDER:-gmail} >/dev/null; fabos settings mail.address $MAIL_ADDRESS >/dev/null; fabos settings mail.from_name 'Fab OS agent' >/dev/null; printf '%s\n' '$MAIL_APP_PASSWORD' | fabos set-key mail >/dev/null 2>&1; fabos mail-check"; fi
vm "fabos status"

approve_pending() { # approve everything pending, print what was approved
  local ids; ids=$(api GET /approvals/pending | python3 -c 'import sys,json; d=json.load(sys.stdin); print(" ".join(str(a["id"]) for a in (d if isinstance(d,list) else d.get("approvals",[]))))' 2>/dev/null)
  for a in $ids; do api POST "/approvals/$a" '{"decision":"approved"}' >/dev/null; echo "    approved approval #$a"; APPROVED=$((APPROVED+1)); done; }
run_task() { # run_task NAME TIMEOUT_S MODE MODEL TEXT   -> sets TASK_ID TASK_STATUS TASK_RESULT
  local name=$1 to=$2 mode=$3 model=$4 text=$5 start=$(date +%s); APPROVED=0
  vm "fabos settings claude.model $model >/dev/null"
  echo; echo "=== $name [$mode/$model]: $text"
  local resp; resp=$(api POST /tasks "$(python3 -c 'import json,sys; print(json.dumps({"request": sys.argv[1], "mode": sys.argv[2]}))' "$text" "$mode")")
  TASK_ID=$(echo "$resp" | jget '["id"]'); [ -n "$TASK_ID" ] || { echo "    create failed: $resp"; TASK_STATUS=create_failed; return; }
  while :; do
    local t; t=$(api GET "/tasks/$TASK_ID"); TASK_STATUS=$(echo "$t" | jget '["status"]')
    case "$TASK_STATUS" in done|failed|cancelled) break;; esac
    approve_pending
    local q; q=$(echo "$t" | python3 -c 'import sys,json; d=json.load(sys.stdin); qs=[x for x in d.get("questions",[]) if not x.get("answer")]; print(qs[-1]["question"][:200] if qs else "")' 2>/dev/null)
    if [ -n "$q" ]; then echo "    agent asked: $q -> answering 'yes, go ahead'"; api POST "/tasks/$TASK_ID/answer" '{"text":"yes, go ahead"}' >/dev/null; fi
    [ $(( $(date +%s) - start )) -gt "$to" ] && { echo "    timeout after ${to}s — cancelling"; api POST "/tasks/$TASK_ID/cancel" >/dev/null; TASK_STATUS=timeout; break; }
    sleep 5
  done
  local t; t=$(api GET "/tasks/$TASK_ID")
  TASK_RESULT=$(echo "$t" | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("result") or d.get("error") or "")[:600].replace("\n"," "))' 2>/dev/null)
  local tools; tools=$(echo "$t" | python3 -c '
import sys,json,collections; d=json.load(sys.stdin); steps=d.get("steps") or d.get("log") or []
c=collections.Counter((s.get("tool") or s.get("kind") or s.get("type") or "?") for s in steps if isinstance(s,dict)); print(len(steps), dict(c))' 2>/dev/null)
  echo "    -> status=$TASK_STATUS in $(( $(date +%s) - start ))s approvals=$APPROVED steps=$tools"; echo "    result: $TASK_RESULT"
}
check() { # check DESC COMMAND(in VM, must print something / exit 0)
  local desc=$1; shift; local out; out=$(vm "$@" 2>&1); local rc=$?
  echo "    check: $desc -> rc=$rc $(echo "$out" | head -c 300 | tr '\n' '|')"; [ $rc -eq 0 ] && [ -n "$out" ]; }

# ---------- 1.0-8 concurrency proof (CONCURRENCY_ONLY=1 runs just this and exits): chat A waits for an approval while chat B
# starts, runs and finishes; a follow-up in chat A queues behind A and says why; after the approval A and its follow-up finish;
# no slot or queue entry is left. Every verdict is a daemon answer over curl (GET /tasks/{id}, GET /status parallel).
if [ "${CONCURRENCY_ONLY:-0}" = 1 ]; then
  APPROVED=0
  if [ "${AGENT_PROVIDER:-claude}" = fake ]; then REQ_A="show me the system"; REQ_B="say hi"; CHECK_B="true"
  else REQ_A="Delete the file ~/r8-approval-test.txt"; REQ_B="Run the shell command: echo r8-ok > ~/r8-b.txt   and then confirm the file exists."; CHECK_B="grep -qx r8-ok ~/r8-b.txt"; fi
  echo; echo "=== concurrency: an approval-waiting chat does not block a new chat (provider ${AGENT_PROVIDER:-claude})"
  vm "rm -f ~/r8-b.txt ~/r8-approval-test.txt; touch ~/r8-approval-test.txt"
  A=$(api POST /tasks "$(python3 -c 'import json,sys; print(json.dumps({"request": sys.argv[1], "mode": "ask"}))' "$REQ_A")" | jget '["id"]'); echo "    chat A = task #$A (ask mode: $REQ_A)"
  st=""; for i in $(seq 1 40); do st=$(api GET "/tasks/$A" | jget '["status"]'); [ "$st" = waiting_approval ] && break; case "$st" in done|failed|cancelled) break;; esac; sleep 3; done
  echo "    A status: $st"; [ "$st" = waiting_approval ] && verdict PASS "chat A waits for approval" || verdict FAIL "chat A did not reach waiting_approval ($st: $(api GET "/tasks/$A" | jget '.get("error")'))"
  par=$(api GET /status | python3 -c 'import sys,json; p=json.load(sys.stdin)["parallel"]; print(p["busy"], p["max"])'); echo "    slots busy/max while A waits: $par"
  [ "${par%% *}" = 0 ] && verdict PASS "the waiting chat holds no slot (busy 0 of ${par##* })" || verdict FAIL "slots busy while A waits: $par"
  B=$(api POST /tasks "$(python3 -c 'import json,sys; print(json.dumps({"request": sys.argv[1], "mode": "auto"}))' "$REQ_B")" | jget '["id"]'); echo "    chat B = task #$B (auto mode: $REQ_B)"
  t0=$(date +%s); sb=""; for i in $(seq 1 60); do sb=$(api GET "/tasks/$B" | jget '["status"]'); case "$sb" in done|failed|cancelled) break;; esac; sleep 3; done
  echo "    B status: $sb after $(( $(date +%s) - t0 ))s; A still: $(api GET "/tasks/$A" | jget '["status"]')"
  [ "$sb" = done ] && vm "$CHECK_B" && verdict PASS "chat B ran and finished while A waited" || verdict FAIL "chat B: $sb ($(api GET "/tasks/$B" | jget '.get("error")'))"
  [ "$(api GET "/tasks/$A" | jget '["status"]')" = waiting_approval ] && verdict PASS "chat A is still waiting for approval" || verdict FAIL "A changed state"
  FA=$(api POST /tasks "{\"request\":\"say hi\",\"parent_id\":$A,\"mode\":\"auto\"}" | jget '["id"]'); sleep 3
  q=$(api GET "/tasks/$FA" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["status"], (d.get("queue") or {}).get("text",""))'); echo "    follow-up #$FA in chat A: $q"
  [ "$q" = "queued waiting for the previous step in this chat" ] && verdict PASS "a follow-up in the waiting chat is queued with the reason" || verdict FAIL "follow-up in A: $q"
  lst=$(api GET /tasks | python3 -c 'import sys,json; d=json.load(sys.stdin); t=[x for x in d if x["id"]=='"$FA"']; print((t[0].get("queue") or {}).get("why") if t else "missing")'); [ "$lst" = chat ] && verdict PASS "GET /tasks carries queue.why=chat for it" || verdict FAIL "GET /tasks queue: $lst"
  approve_pending
  sa=""; for i in $(seq 1 40); do sa=$(api GET "/tasks/$A" | jget '["status"]'); case "$sa" in done|failed|cancelled) break;; esac; approve_pending; sleep 3; done
  sf=""; for i in $(seq 1 40); do sf=$(api GET "/tasks/$FA" | jget '["status"]'); case "$sf" in done|failed|cancelled) break;; esac; approve_pending; sleep 3; done
  echo "    A: $sa · follow-up: $sf"; [ "$sa" = done ] && [ "$sf" = done ] && verdict PASS "after the approval A finished and its follow-up ran" || verdict FAIL "A=$sa follow-up=$sf"
  par=$(api GET /status | python3 -c 'import sys,json; p=json.load(sys.stdin)["parallel"]; print(p["busy"], len(p["waiting"]))'); [ "$par" = "0 0" ] && verdict PASS "no slot and no queue entry left" || verdict FAIL "slots after: $par"
  echo; echo "### SUMMARY: $pass PASS / $fail FAIL"; vm "fabos tasks --limit 6"; exit $fail
fi

# ---------- EASY
run_task easy-1 300 auto "$MODEL" "Create the file ~/notes/hello.txt containing exactly the text: hello from fab os"
check "file content" "grep -x 'hello from fab os' ~/notes/hello.txt" && verdict PASS "easy-1 file created" || verdict FAIL "easy-1 ($TASK_STATUS)"
run_task easy-2 300 auto "$MODEL" "Tell me the kernel version and the free memory of this computer in one line. Do not create files."
echo "$TASK_RESULT" | grep -qi "7\.0\." && verdict PASS "easy-2 answered with kernel version" || verdict FAIL "easy-2 ($TASK_STATUS)"
# ---------- MEDIUM
run_task medium-1 480 auto "$MODEL" "Write a Python script ~/work/primes.py that prints the first 50 prime numbers one per line, run it, and save its output to ~/work/primes.txt"
check "50 primes, last 229" "test \$(wc -l < ~/work/primes.txt) -eq 50 && tail -1 ~/work/primes.txt | grep -x 229" && verdict PASS "medium-1 primes" || verdict FAIL "medium-1 ($TASK_STATUS)"
vm "mkdir -p ~/work && printf 'def total(n):\n    s = 0\n    for i in range(1, n):\n        s += i\n    return s\nprint(totl(100))\n' > ~/work/buggy.py"
run_task medium-2 480 auto "$MODEL" "The script ~/work/buggy.py should print 5050 but it is broken. Find and fix all bugs in place and run it to prove it prints 5050."
check "prints 5050" "python3 ~/work/buggy.py | grep -x 5050" && verdict PASS "medium-2 debugging" || verdict FAIL "medium-2 ($TASK_STATUS)"
run_task medium-3 300 auto "$MODEL" "Fetch https://example.com and save only the page title text to ~/work/title.txt"
check "title" "grep -i 'example domain' ~/work/title.txt" && verdict PASS "medium-3 web fetch" || verdict FAIL "medium-3 ($TASK_STATUS)"
# ---------- GUI (real desktop session)
run_task gui-1 420 auto "$MODEL" "Open the text editor application, type the text 'hi from the Fab OS agent' into it, and save the document as ~/Documents/hi.txt. Then close nothing, just report when done."
check "hi.txt saved" "grep -i 'hi from the fab os agent' ~/Documents/hi.txt" && verdict PASS "gui-1 editor typing" || verdict FAIL "gui-1 ($TASK_STATUS)"
scripts/vm-screenshot.sh live-gui-1 >/dev/null 2>&1 && echo "    screenshot: build/screenshots/live-gui-1.png"
# ---------- ROOT + APPROVAL
run_task root-1 480 auto "$MODEL" "Install the package 'sl' with apt and confirm the command exists afterwards."
check "sl installed" "command -v sl" && verdict PASS "root-1 as_root install (approvals=$APPROVED)" || verdict FAIL "root-1 ($TASK_STATUS)"
before=$(api GET /activity | python3 -c 'import sys,json; d=json.load(sys.stdin); print(sum(1 for a in (d if isinstance(d,list) else d.get("activity",[])) if "approval" in json.dumps(a)))' 2>/dev/null)
run_task ask-1 300 ask "$MODEL" "Delete the file ~/notes/hello.txt"
after=$(api GET /activity | python3 -c 'import sys,json; d=json.load(sys.stdin); print(sum(1 for a in (d if isinstance(d,list) else d.get("activity",[])) if "approval" in json.dumps(a)))' 2>/dev/null)
if [ "$APPROVED" -ge 1 ] && ! vm "test -e ~/notes/hello.txt"; then verdict PASS "ask-1 approval requested (${before:-?}->${after:-?}) then executed"; else verdict FAIL "ask-1 approvals=$APPROVED status=$TASK_STATUS"; fi
# ---------- MAIL (only to the address you authorised)
if [ -n "$MAIL_TO" ] && [ -n "${MAIL_APP_PASSWORD:-}" ] && [ -n "${MAIL_ADDRESS:-}" ]; then
  run_task mail-1 300 auto "$MODEL" "Send an email to $MAIL_TO with the subject 'Fab OS agent test' and a short body that reports this machine's hostname and disk usage of /."
  check "email_sent logged" "fabos log --limit 60 | grep -i 'email_sent'" && verdict PASS "mail-1 sent from the user's own account to $MAIL_TO (check the inbox)" || verdict FAIL "mail-1 ($TASK_STATUS)"
elif [ -n "$MAIL_TO" ]; then
  # No user mail account was supplied (MAIL_ADDRESS + MAIL_APP_PASSWORD): the agent has no Brevo path any more, so the
  # right behaviour is to ask the user to sign in — verify exactly that instead of pretending to send.
  run_task mail-1 120 auto "$MODEL" "Send an email to $MAIL_TO with the subject 'Fab OS agent test' and a short body that reports this machine's hostname."
  if echo "$TASK_RESULT" | grep -q -i -E 'sign in|not configured|Settings.*Mail|mail account'; then echo ">>> SKIP: mail-1 no mail account supplied; the agent correctly asked the user to sign in (Settings > Mail)"; else verdict FAIL "mail-1 without an account: agent neither sent nor asked to sign in ($TASK_STATUS)"; fi
fi
# ---------- WATCH
run_task watch-1 240 auto "$MODEL" "Every 20 seconds check whether the file ~/work/flag.txt exists; when it appears, notify me and stop checking."
sleep 30; vm "touch ~/work/flag.txt"; sleep 75
check "watch fired" "fabos watches | grep -E 'hits=[1-9]|done|fired' ; fabos log --limit 80 | grep -iE 'watch|notify' | tail -3" && verdict PASS "watch-1 watch fired after flag" || verdict FAIL "watch-1 ($TASK_STATUS)"
# ---------- HARD
run_task hard-1 900 auto "$HEAVY_MODEL" "Create a Python project in ~/work/todo: a CLI todo.py with add/list/done commands storing items in todo.json, plus unittest tests in test_todo.py covering all three commands. Run the tests and make them pass. Finish by printing the test summary."
check "tests pass" "cd ~/work/todo && python3 -m unittest -q 2>&1 | tail -1 | grep -x OK" && verdict PASS "hard-1 project + tests" || verdict FAIL "hard-1 ($TASK_STATUS)"
# ---------- SUPER HEAVY
run_task heavy-1 1500 auto "$HEAVY_MODEL" "Build a static site generator in Python under ~/site: read Markdown files from ~/site/content, convert them to HTML with a simple template (title, nav, body) into ~/site/out, and generate an index page linking all posts. Create three sample posts, build the site, start 'python3 -m http.server 8123' in the background serving ~/site/out, and fetch http://127.0.0.1:8123/ to confirm the index lists the three posts."
check "site served" "curl -s http://127.0.0.1:8123/ | grep -ci '<a ' | awk '\$1>=3'" && verdict PASS "heavy-1 static site built and served" || verdict FAIL "heavy-1 ($TASK_STATUS)"
# ---------- CONTROLS: cancel / retry / delete / ai off / bypass
run_task ctl-cancel 20 auto "$MODEL" "Run the shell command: sleep 240 && echo finished. Wait for it to complete, then report its output."
sleep 4; st=$(api GET "/tasks/$TASK_ID" | jget '["status"]'); child=$(vm "pgrep -f '^sleep 240\$' | wc -l")   # anchored so the remote shell's own command line does not count as a survivor
[ "$st" = cancelled ] && [ "${child:-1}" = 0 ] && verdict PASS "ctl cancel: task cancelled and its shell child killed" || verdict FAIL "ctl cancel status=$st children_left=$child"
newid=$(api POST "/tasks/$TASK_ID/retry" | jget '["id"]'); sleep 6; st=$(api GET "/tasks/${newid:-$TASK_ID}" | jget '["status"]'); api POST "/tasks/${newid:-$TASK_ID}/cancel" >/dev/null; sleep 3
{ [ "$st" = running ] || [ "$st" = queued ]; } && verdict PASS "ctl retry restarts (new task #$newid)" || verdict FAIL "ctl retry status=$st"
api DELETE "/tasks/$TASK_ID" >/dev/null; api GET "/tasks/$TASK_ID" | grep -q '"id"' && verdict FAIL "ctl delete" || verdict PASS "ctl delete removes history"
vm "fabos settings ai.enabled false >/dev/null"; r=$(api POST /tasks '{"request":"say hi"}'); echo "$r" | grep -qi "disabled\|off\|error" && verdict PASS "ai.enabled=false refuses tasks" || verdict FAIL "ai off accepted: $r"; vm "fabos settings ai.enabled true >/dev/null"
run_task bypass-1 300 bypass "$MODEL" "Create ~/work/bypass.txt containing the word ok using a shell command."
[ "$APPROVED" -eq 0 ] && check "bypass file" "grep -x ok ~/work/bypass.txt" && verdict PASS "bypass mode: no approvals asked" || verdict FAIL "bypass approvals=$APPROVED status=$TASK_STATUS"

echo; echo "### SUMMARY: $pass PASS / $fail FAIL"; vm "fabos tasks --limit 30"; vm "fabos log --limit 40" | tail -40
[ $KEEP -eq 1 ] || echo "(VM left running for inspection; use scripts/boot-vm.sh monitor to quit)"
exit $fail
