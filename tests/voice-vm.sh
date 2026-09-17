#!/usr/bin/env bash
# Voice pipeline checks INSIDE the booted VM, driven over SSH (vm profile ships openssh-server). The VM must have been
# booted with scripts/boot-vm.sh (default: an emulated Intel HDA card with a silent microphone and a speaker on the
# "none" audiodev), so PipeWire in the guest has a default source and sink and every stage can be exercised for real.
#   tests/voice-vm.sh [--inject] [--no-task]        (ANTHROPIC_API_KEY=... optional: configures Claude for the task check)
# 1.0-8 additions: the microphone permission (off -> listen-once / status / the daemon's /speech/transcribe refuse, then back on),
# a spoken sentence (espeak-ng) played into a virtual default source (null sink + remap-source) and recorded through the ask bar's
# EXACT listen command with its progress file (transcript == sentence, states starting -> recording -> transcribing -> done, the level
# meter saw the speech, time to the recording state), and whisper-cli's peak RSS (what MEM_NEEDED_KB is derived from).
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

# ---------- 0b. the microphone permission (1.0-8) must be ON for the pipeline checks (section 6 switches it off and back)
echo; echo "=== microphone permission on"
vm "fabos settings voice.mic_allowed true >/dev/null 2>&1; sleep 1; fabos-voice status"
[ "$(vm 'fabos-voice status' | jget '["mic_allowed"]')" = True ] && verdict PASS "microphone permission on (voice.mic_allowed)" || verdict FAIL "could not turn the microphone permission on"

# ---------- 1. doctor: exit 0, every required stage OK
echo; echo "=== fabos-voice doctor"
doc=$(vm "fabos-voice doctor" 2>&1); rc=$?; echo "$doc"
[ $rc -eq 0 ] && verdict PASS "doctor exit 0" || verdict FAIL "doctor exit $rc"
docj=$(vm "fabos-voice doctor --json --quiet" 2>/dev/null)
for st in audio-session default-source mic-permission capture speech-to-text wake-word default-sink text-to-speech; do
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
# The emulated HDA microphone is not digitally silent (the doctor measured RMS ~270 of noise), so either reason is correct:
# an all-zero stream -> "muted or silent"; a noisy stream with no speech -> "did not catch that".
echo "$res" | grep -q -E 'err=.*(muted or silent|did not catch that)' && verdict PASS "listen-once names a reason ($(echo "$res" | grep -o -E 'muted or silent|did not catch that' | head -1))" || verdict FAIL "listen-once stderr: $(echo "$res" | grep err=)"
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

# ---------- 6. the microphone permission OFF (1.0-8, permissions only enable): listen-once refuses with the reason, status says so,
#              the daemon's /speech/transcribe refuses with 403; then back ON
echo; echo "=== microphone permission off"
vm "fabos settings voice.mic_allowed false >/dev/null"; sleep 1
res=$(vm "fabos-voice listen-once --timeout 2 >/tmp/po.out 2>/tmp/po.err; echo rc=\$?; echo out=\$(cat /tmp/po.out); echo err=\$(cat /tmp/po.err)"); echo "$res"
echo "$res" | grep -q '^rc=4$' && echo "$res" | grep -q 'Microphone is off in Settings' && echo "$res" | grep -q '^out=$' && verdict PASS "permission off: listen-once exit 4 with the reason, nothing on stdout" || verdict FAIL "permission off: $res"
[ "$(vm 'fabos-voice status' | jget '["mic_allowed"]')" = False ] && verdict PASS "status mic_allowed false" || verdict FAIL "status mic_allowed not false"
code=$(vm "curl -s -o /tmp/tr.out -w '%{http_code}' -X POST -H \"Authorization: Bearer \$(cat \$XDG_RUNTIME_DIR/fabos-agent/token)\" -H 'Content-Type: application/json' -d '{\"audio_b64\":\"UklGRg==\",\"format\":\"wav\"}' http://127.0.0.1:8790/speech/transcribe; echo; cat /tmp/tr.out"); echo "$code"
echo "$code" | head -1 | grep -q '^403$' && echo "$code" | grep -q 'Microphone is off in Settings' && verdict PASS "/speech/transcribe refuses with 403 + the reason while off" || verdict FAIL "/speech/transcribe while off: $code"
vm "fabos settings voice.mic_allowed true >/dev/null"; sleep 1
[ "$(vm 'fabos-voice status' | jget '["mic_allowed"]')" = True ] && verdict PASS "permission back on" || verdict FAIL "permission did not come back"

# ---------- 7. a spoken sentence reaches the transcript through the ask bar's EXACT listen command (1.0-8): a null sink whose monitor
#              is remapped as the default source; espeak-ng speaks a known sentence into it 1.5 s after the recorder starts
echo; echo "=== spoken sentence -> transcript (virtual source, the ask bar's command)"
SENT="good morning everyone how are you today"
orig_src=$(vm "pactl get-default-source")
vm "pactl load-module module-null-sink sink_name=fabtest sink_properties=device.description=FabTest >/dev/null && pactl load-module module-remap-source master=fabtest.monitor source_name=fabmic source_properties=device.description=FabTestMic >/dev/null && pactl set-default-source fabmic && echo default-source=\$(pactl get-default-source)"
vm "command -v espeak-ng >/dev/null && espeak-ng -v en-gb-x-rp -s 150 -a 175 -w /tmp/say.wav 'good morning everyone, how are you today' && ls -la /tmp/say.wav" || echo "no espeak-ng in the VM"
cat > /tmp/r8-listen-vm.sh <<'EOS'
#!/bin/bash
# runs INSIDE the VM: the ask bar's exact listen command (agent.js listenCommand(10), pinned by tests/askbar-js-test.js) while the
# progress file is sampled every 50 ms (timestamp + raw JSON), and the sentence is played into the virtual source 1.5 s in
F="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/fabos-voice/askbar-listen.json"; rm -f "$F"
( while :; do echo "$(date +%s%N) $(cat "$F" 2>/dev/null)"; sleep 0.05; done ) > /tmp/prog.trace & TR=$!
( sleep 1.5; paplay --device=fabtest /tmp/say.wav ) &
t0=$(date +%s%N)
F="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/fabos-voice/askbar-listen.json"; rm -f "$F"; fabos-voice listen-once --timeout 10 --progress "$F" > /tmp/lo.out 2> /tmp/lo.err; rc=$?
t1=$(date +%s%N); kill $TR 2>/dev/null; wait $TR 2>/dev/null
echo "rc=$rc"; echo "t0=$t0"; echo "ms=$(( (t1 - t0) / 1000000 ))"; echo "out=$(cat /tmp/lo.out)"; echo "err=$(tail -1 /tmp/lo.err)"; echo "final=$(cat "$F")"
echo "trace:"; cat /tmp/prog.trace
EOS
$SCP /tmp/r8-listen-vm.sh fabos@127.0.0.1:/tmp/ >/dev/null
res=$(vm "bash /tmp/r8-listen-vm.sh"); echo "$res" | grep -v '^[0-9]* *$' | head -12
python3 - "$res" "$SENT" <<'EOP'
import json, re, sys
res, sent = sys.argv[1], sys.argv[2]
head, _, trace = res.partition("trace:
")
kv = dict(l.split("=", 1) for l in head.splitlines() if "=" in l)
norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", s.lower())).strip()
t0 = int(kv.get("t0", "0")); states, first_rec, peak = [], None, 0.0
for line in trace.splitlines():
    ts, _, js = line.partition(" ")
    if not js.strip():
        continue
    try:
        d = json.loads(js)
    except ValueError:
        continue                                            # half-written: the writer replaces atomically, cat may still race the rm
    st = d.get("state")
    if not states or states[-1] != st:
        states.append(st)
    if st == "recording" and first_rec is None:
        first_rec = (int(ts) - t0) / 1e6
    peak = max(peak, float(d.get("peak") or 0))
final = {}
try:
    final = json.loads(kv.get("final", "") or "{}")
except ValueError:
    pass
print("heard=%r rc=%s ms=%s states=%s first_recording_ms=%s peak=%.2f final_state=%s" % (kv.get("out", ""), kv.get("rc"), kv.get("ms"), states, None if first_rec is None else int(first_rec), peak, final.get("state")))
open("/tmp/r8-listen-summary", "w").write(json.dumps({"heard": norm(kv.get("out", "")), "rc": kv.get("rc"), "states": states, "first_recording_ms": first_rec, "peak": peak, "final_state": final.get("state"), "err": kv.get("err", "")}))
EOP
summ=$(cat /tmp/r8-listen-summary 2>/dev/null); sget() { echo "$summ" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }
[ "$(sget '["heard"]')" = "$SENT" ] && verdict PASS "transcript equals the spoken sentence: '$(sget '["heard"]')'" || verdict FAIL "transcript '$(sget '["heard"]')' != '$SENT' (rc=$(sget '["rc"]') err=$(sget '["err"]'))"
[ "$(sget '["final_state"]')" = done ] && verdict PASS "progress file ends in done" || verdict FAIL "progress file final state: $(sget '["final_state"]')"
[ "$(sget '["states"][:2]')" = "['starting', 'recording']" ] && echo "$(sget '["states"]')" | grep -q transcribing && verdict PASS "progress states in order: $(sget '["states"]')" || verdict FAIL "progress states: $(sget '["states"]')"
fr=$(sget '["first_recording_ms"]'); python3 -c "import sys; v=sys.argv[1]; sys.exit(0 if v not in ('None','') and float(v) <= 2500 else 1)" "$fr" && verdict PASS "recording state ${fr%.*} ms after the command started (the bar shows 'starting' at once and 'speak now' from here)" || verdict FAIL "recording state late or missing (${fr} ms)"
pk=$(sget '["peak"]'); python3 -c "import sys; sys.exit(0 if float(sys.argv[1] or 0) > 0.15 else 1)" "$pk" && verdict PASS "the level meter saw the speech (peak $pk)" || verdict FAIL "level meter peak $pk"
vm "pactl set-default-source $orig_src; pactl unload-module module-remap-source; pactl unload-module module-null-sink; pactl get-default-source" >/dev/null 2>&1

# ---------- 8. whisper-cli tiny.en peak RSS on the spoken sentence (16 kHz) — MEM_NEEDED_KB in voicelib is this + 20 %
echo; echo "=== whisper-cli peak memory"
rss=$(vm "python3 - <<'EOP'
import array, resource, subprocess, sys, wave
sys.path.insert(0, '/usr/lib/fabos/voice'); import voicelib as V
w = wave.open('/tmp/say.wav'); rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes(); pcm = array.array('h', w.readframes(n)); w.close()
if ch == 2: pcm = pcm[::2]
out = array.array('h', (pcm[min(len(pcm) - 1, int(i * rate / 16000))] for i in range(int(len(pcm) * 16000 / rate))))
V.write_wav('/tmp/say16.wav', out.tobytes())
r = subprocess.run(['whisper-cli', '-m', V.MODEL_PATH, '-l', 'en', '-nt', '-np', '-t', '2', '-f', '/tmp/say16.wav'], capture_output=True, text=True)
print('text=' + ' '.join(r.stdout.split()))
print('maxrss_kb=%d' % resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
print('threshold_kb=%d peak_setting_mb=%d' % (V.MEM_NEEDED_KB, V.WHISPER_PEAK_RSS_MB))
print('memavailable_kb=%s' % V.mem_available_kb())
EOP"); echo "$rss"
mx=$(echo "$rss" | grep -o 'maxrss_kb=[0-9]*' | cut -d= -f2); th=$(echo "$rss" | grep -o 'threshold_kb=[0-9]*' | cut -d= -f2)
[ -n "$mx" ] && [ "$mx" -gt 0 ] && [ "$mx" -lt "${th:-0}" ] && verdict PASS "whisper-cli peak RSS $(( mx / 1024 )) MB is under the threshold $(( th / 1024 )) MB" || verdict FAIL "whisper-cli peak RSS ${mx:-?} KB vs threshold ${th:-?} KB"

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
