#!/bin/bash
# Fab OS — speed and cache-reuse probe (ADR-0020; the tok/s and cache_n figures in llama-start.sh and docs/LOW-RAM.md come from here) in the image: generation tok/s with 6 threads (the wrapper's choice: physical cores) against all
# 12 (nproc), and the prompt tokens a second request sharing the first one's prefix still has to process (--cache-reuse 256).
# llama-server's own "timings" object in the /v1/chat/completions answer is the source. Writes /out/probe2.json.
#   podman run --rm -i -v "$PWD:/src:ro,z" -v "$PWD/build:/out:z" -v "$MODEL:/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf:ro,z" localhost/fabos:vm bash /src/tests/local-model-speed.sh
set -u
LLAMA_START=/src/packages/fabos-ai/usr/lib/fabos/ai/llama-start.sh
run_one() {  # $1 = threads
  FABOS_LLAMA_THREADS="$1" "$LLAMA_START" > "/tmp/p2-$1.log" 2>&1 &
  SPID=$!
  if ! MAINPID=$SPID "$LLAMA_START" --wait-healthy 120; then cat "/tmp/p2-$1.log"; exit 3; fi
  grep -m1 'fabos-llama: serving' "/tmp/p2-$1.log"
  python3 - "$1" <<'PY'
import json, sys, urllib.request
sys.path.insert(0, "/src/packages/fabos-agent/usr/lib/fabos/agent")
import fabos_agentd as fa
B = "http://127.0.0.1:8081"


def post(body):
    r = urllib.request.urlopen(urllib.request.Request(B + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=600)
    return json.loads(r.read())


def pick(t):
    return {k: t.get(k) for k in ("prompt_n", "prompt_per_second", "predicted_n", "predicted_per_second", "cache_n")}


sysp = fa.local_system_prompt("auto", {"online": True})
turn = "Task: %s\nPlan:\n  1. [run_shell] %s  (NOW)\nStep 1 of 1 — do it now with ONE run_shell call: %s"
# 1st request: the driver's system prompt + one turn; 2nd: the same system prompt + a different turn — the shared prefix should be reused
r1 = post({"model": "local", "max_tokens": 120, "temperature": 0.2, "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": turn % (("count the regular files in /tmp/x",) * 3)}]})
r2 = post({"model": "local", "max_tokens": 120, "temperature": 0.2, "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": turn % (("print today's date in ISO form",) * 3)}]})
# a longer free generation for the tok/s figure
r3 = post({"model": "local", "max_tokens": 200, "temperature": 0.7, "messages": [{"role": "user", "content": "Describe a desktop computer in a paragraph of about 150 words."}]})
out = {"threads": int(sys.argv[1]), "first": pick(r1.get("timings", {})), "second_same_prefix": pick(r2.get("timings", {})), "generation_200": pick(r3.get("timings", {}))}
json.dump(out, open("/out/probe2-%s.json" % sys.argv[1], "w"), indent=1)
print(json.dumps(out))
PY
  grep -E "prompt eval time|eval time|reus" "/tmp/p2-$1.log" | tail -n 6
  kill $SPID; wait $SPID 2>/dev/null
}
run_one 6
run_one 12
python3 - <<'PY'
import json
d = [json.load(open("/out/probe2-%d.json" % n)) for n in (6, 12)]
json.dump(d, open("/out/probe2.json", "w"), indent=1)
print(json.dumps(d, indent=1))
PY
