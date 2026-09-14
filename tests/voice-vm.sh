#!/usr/bin/env bash
# Voice pipeline checks INSIDE the booted VM, driven over SSH (vm profile ships openssh-server). The VM must have been
# booted with scripts/boot-vm.sh (default: an emulated Intel HDA card with a silent microphone and a speaker on the
# "none" audiodev), so PipeWire in the guest has a default source and sink and every stage can be exercised for real.
#   tests/voice-vm.sh [--inject] [--no-task]        (ANTHROPIC_API_KEY=... optional: configures Claude for the task check)
# Output: build/voice-vm.out (full log) — every PASS/FAIL below is backed by a command run in the VM.
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
INJECT=0; TASK=1
while [ $# -gt 0 ]; do case "$1" in --inject) INJECT=1;; --no-task) TASK=0;; esac; shift; done
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222"
mkdir -p build; OUT=build/voice-vm.out; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
vm() { $SSH "$@"; }
api() { # api METHOD PATH [JSON]
  local body=${3:-}
  vm "curl -s -m 30 -X $1 -H \"Authorization: Bearer \$(cat \$XDG_RUNTIME_DIR/fabos-agent/token)\" -H 'Content-Type: application/json' ${body:+-d $(printf %q "$body")} http://127.0.0.1:8790$2"; }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }
pass=0; fail=0
verdict() { if [ "$1" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); fi; echo ">>> $1: $2"; }

echo "### Fab OS voice VM test — $(date -u +%FT%TZ)"
pgrep -f qemu-system-x86_64 >/dev/null || { echo "booting VM"; (scripts/boot-vm.sh --headless --mem ${VM_MEM:-2048} --cpus 4 > build/boot-headless.out 2>&1 &); }
for i in $(seq 1 100); do vm true 2>/dev/null && break; sleep 3; done; vm true || { echo "no ssh to the VM"; exit 1; }
for i in $(seq 1 60); do api GET /health 2>/dev/null | grep -q '"ok"' && break; sleep 3; done
api GET /health | grep -q '"ok"' || echo "note: agent daemon not reachable in the session (doctor's optional stages will FAIL, required ones are unaffected)"

if [ "$INJECT" = 1 ]; then   # test the working-tree voice code without rebuilding the image
  echo "### injecting working-tree voice files into the VM"
  $SCP packages/fabos-voice/usr/bin/fabos-voice packages/fabos-voice/usr/lib/fabos/voice/voicelib.py packages/fabos-voice/usr/lib/fabos/voice/phrases.py packages/fabos-voice/usr/lib/fabos/voice/fabos_voiced.py fabos@127.0.0.1:/tmp/ >/dev/null
  vm "echo fabos | sudo -S install -m 755 /tmp/fabos-voice /usr/bin/fabos-voice 2>/dev/null; echo fabos | sudo -S install -m 644 /tmp/voicelib.py /tmp/phrases.py /usr/lib/fabos/voice/ 2>/dev/null; echo fabos | sudo -S install -m 755 /tmp/fabos_voiced.py /usr/lib/fabos/voice/ 2>/dev/null; systemctl --user restart fabos-voiced; sleep 3; systemctl --user is-active fabos-voiced && echo injected-ok"
fi

# ---------- 0. the emulated sound card is visible to the guest and to PipeWire
echo; echo "=== sound card"
vm "ls /dev/snd/; pactl info 2>/dev/null | grep -E 'Server Name|Default Source|Default Sink'"
vm "test -e /dev/snd/pcmC0D0c && pactl get-default-source | grep -qv monitor" && verdict PASS "guest has a capture device and a default source" || verdict FAIL "no capture device / default source in the guest (boot with scripts/boot-vm.sh, which adds ich9-intel-hda + hda-micro)"

# ---------- 1. doctor: exit 0, every required stage OK
echo; echo "=== fabos-voice doctor"
doc=$(vm "fabos-voice doctor" 2>&1); rc=$?; echo "$doc"
[ $rc -eq 0 ] && verdict PASS "doctor exit 0" || verdict FAIL "doctor exit $rc"
docj=$(vm "fabos-voice doctor --json --quiet" 2>/dev/null)
for st in audio-session default-source capture speech-to-text wake-word default-sink text-to-speech; do
  ok=$(echo "$docj" | python3 -c "import sys,json; d=json.load(sys.stdin); print([s['ok'] for s in d['stages'] if s['stage']=='$st'][0])" 2>/dev/null)
  [ "$ok" = True ] && verdict PASS "doctor stage $st OK" || verdict FAIL "doctor stage $st: $(echo "$docj" | python3 -c "import sys,json; d=json.load(sys.stdin); print([s['detail'] for s in d['stages'] if s['stage']=='$st'][0])" 2>/dev/null)"
done

# ---------- 2. status: mic true, reasons empty for what works
echo; echo "=== fabos-voice status"
st=$(vm "fabos-voice -v status"); echo "$st"
[ "$(echo "$st" | jget '["mic"]')" = True ] && verdict PASS "status mic true" || verdict FAIL "status mic: $(echo "$st" | jget '["mic"]') reason: $(echo "$st" | jget '["mic_reason"]')"
[ "$(echo "$st" | jget '["stt"]')" != none ] && verdict PASS "status stt $(echo "$st" | jget '["stt"]')" || verdict FAIL "status stt none: $(echo "$st" | jget '["stt_reason"]')"
[ "$(echo "$st" | jget '["tts"]')" != none ] && verdict PASS "status tts $(echo "$st" | jget '["tts"]')" || verdict FAIL "status tts none: $(echo "$st" | jget '["tts_reason"]')"
[ "$(echo "$st" | jget '["wake"]')" = True ] && verdict PASS "wake listener running (spotter on the emulated microphone)" || verdict FAIL "wake false: $(vm 'journalctl --user -u fabos-voiced --no-pager -n 5 -o cat' | tr '\n' '|')"

# ---------- 3. listen-once on the silent emulated microphone: exit 3 within 8 s, reason on stderr, nothing on stdout
echo; echo "=== fabos-voice listen-once --timeout 4"
t0=$(date +%s); res=$(vm "fabos-voice listen-once --timeout 4 >/tmp/lo.out 2>/tmp/lo.err; echo rc=\$?; echo out=\$(cat /tmp/lo.out); echo err=\$(cat /tmp/lo.err)"); el=$(( $(date +%s) - t0 )); echo "$res (${el}s)"
echo "$res" | grep -q '^rc=3$' && [ $el -le 8 ] && verdict PASS "listen-once exit 3 in ${el}s" || verdict FAIL "listen-once: $res in ${el}s"
echo "$res" | grep -q 'err=.*muted or silent' && verdict PASS "listen-once names the reason (microphone muted or silent)" || verdict FAIL "listen-once stderr: $(echo "$res" | grep err=)"
echo "$res" | grep -q '^out=$' && verdict PASS "listen-once stdout empty" || verdict FAIL "listen-once printed on stdout"

# ---------- 4. say --test: exit 0, backend + sink reported
echo; echo "=== fabos-voice say --test"
say=$(vm "fabos-voice say --test" 2>&1); rc=$?; echo "$say"
[ $rc -eq 0 ] && echo "$say" | grep -q 'backend: ' && echo "$say" | grep -q 'sink: ' && verdict PASS "say --test exit 0 ($(echo "$say" | tail -1))" || verdict FAIL "say --test rc=$rc: $say"
# two utterances started together must play one after the other (the playback lock), never both at once
echo; echo "=== two concurrent say calls are serialised"
t0=$(date +%s%N); vm "fabos-voice say 'First sentence for the queue test.' & fabos-voice say 'Second sentence for the queue test.' & wait"; el=$(( ($(date +%s%N) - t0) / 1000000 ))
one=$(vm "python3 -c \"import sys; sys.path.insert(0,'/usr/lib/fabos/voice'); import voicelib as V, os; p=os.path.join(V.ensure_run_dir(),'q.wav'); V.render_espeak('First sentence for the queue test.', p); print(int(V.wav_seconds(p)*1000))\"")
echo "two sentences took ${el} ms; one rendered sentence lasts ${one:-?} ms"
[ -n "$one" ] && [ "$el" -ge $(( one * 2 * 8 / 10 )) ] && verdict PASS "concurrent say calls played one after the other" || verdict FAIL "concurrent say calls overlapped (${el} ms for two ${one:-?} ms sentences)"

# ---------- 5. a task through the daemon with speak_replies on: no spoken line repeats
if [ "$TASK" = 1 ]; then
  echo; echo "=== task narration through fabos_voiced (speak_replies on)"
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then vm "fabos settings provider claude >/dev/null; fabos settings ai.enabled true >/dev/null; printf '%s\n' '$ANTHROPIC_API_KEY' | fabos set-key claude >/dev/null 2>&1"; fi
  vm "fabos settings voice.speak_replies true >/dev/null; fabos mode auto >/dev/null; fabos status | head -5"
  vm "mkdir -p ~/.local/state/fabos-voice; : > ~/.local/state/fabos-voice/spoken.log"
  before_j=$(vm "journalctl --user -u fabos-voiced --no-pager -o cat 2>/dev/null | wc -l")
  t0=$(date +%s)
  vm "timeout 420 python3 /usr/lib/fabos/voice/fabos_voiced.py --handle 'Create the file ~/voice-test.txt containing the word hello, then tell me it is done' 2>&1 | tail -30"; rc=$?
  echo "    --handle finished rc=$rc in $(( $(date +%s) - t0 ))s"
  spoken=$(vm "cat ~/.local/state/fabos-voice/spoken.log"); echo "--- spoken.log:"; echo "$spoken"
  n=$(echo "$spoken" | grep -c .); dups=$(echo "$spoken" | cut -f3 | sort | uniq -d)
  [ "$n" -ge 2 ] && verdict PASS "daemon spoke $n lines for the task" || verdict FAIL "daemon spoke only $n lines (task status: $(api GET /tasks | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["status"] if d else "none")' 2>/dev/null))"
  [ -z "$dups" ] && verdict PASS "no spoken line repeats" || verdict FAIL "repeated lines: $(echo "$dups" | tr '\n' '|')"
  vm "test -f ~/voice-test.txt && grep -qi hello ~/voice-test.txt" && verdict PASS "the task itself was carried out (~/voice-test.txt)" || verdict FAIL "task did not create ~/voice-test.txt"
  echo "--- fabos-voiced journal (new lines):"; vm "journalctl --user -u fabos-voiced --no-pager -o cat 2>/dev/null | tail -n +$((before_j+1)) | tail -20"
  jd=$(vm "journalctl --user -u fabos-voiced --no-pager -o cat 2>/dev/null | grep -E '^[0-9:]+ voiced: spoke ' | sed -E 's/^[0-9:]+ voiced: spoke \[[a-z]+\] \([^)]*\): //' | sort | uniq -d")
  [ -z "$jd" ] && verdict PASS "no repeated 'spoke' line in the fabos-voiced journal" || verdict FAIL "journal repeats: $(echo "$jd" | tr '\n' '|')"
fi

echo; echo "### SUMMARY: $pass PASS / $fail FAIL"
exit $fail
