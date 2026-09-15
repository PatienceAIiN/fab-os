#!/bin/bash
# Fab OS — capability + memory probe (ADR-0020; the numbers in docs/LOW-RAM.md come from here) of the built-in model inside the image with repacking ON (the wrapper's own choice at >= 6 GiB):
# tokenizes the driver's prompts with the model's tokenizer, checks response_format json_schema and tool_choice=required against the
# live llama-server, runs one 200-token generation, then reads the server's VmHWM. Writes /out/probe.json.
#   podman run --rm -i -v "$PWD:/src:ro,z" -v "$PWD/build:/out:z" -v "$MODEL:/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf:ro,z" localhost/fabos:vm bash /src/tests/local-model-probe.sh
set -u
LLAMA_START=/src/packages/fabos-ai/usr/lib/fabos/ai/llama-start.sh
"$LLAMA_START" > /tmp/probe-llama.log 2>&1 &
SPID=$!
if ! MAINPID=$SPID "$LLAMA_START" --wait-healthy 120; then cat /tmp/probe-llama.log; exit 3; fi
grep -m1 'fabos-llama: serving' /tmp/probe-llama.log
echo "argv: $(tr '\0' ' ' < /proc/$SPID/cmdline)"
python3 - "$SPID" <<'PY'
import json, sys, urllib.request
sys.path.insert(0, "/src/packages/fabos-agent/usr/lib/fabos/agent")
import fabos_agentd as fa
B = "http://127.0.0.1:8081"


def post(path, body):
    r = urllib.request.urlopen(urllib.request.Request(B + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=300)
    return json.loads(r.read())


def ntok(text):
    return len(post("/tokenize", {"content": text})["tokens"])


out = {}
turn = fa.stepwise_turn_text("Copy the folder /tmp/ladder/notes to ~/Ladder/notes-copy so that it holds the same files.",
                             [{"tool": "run_shell", "goal": "copy the folder /tmp/ladder/notes to ~/Ladder/notes-copy"}], 0, {})
out["tokens"] = {"executor_system": ntok(fa.local_system_prompt("auto", {"online": True})),
                 "planner_system": ntok(fa.PLAN_SYSTEM.format(app=fa.APP, net="online", home=fa.HOME)),
                 "check_system": ntok(fa.CHECK_SYSTEM), "finish_system": ntok(fa.FINISH_SYSTEM.format(app=fa.APP)),
                 "execute_turn_sample": ntok(turn),
                 "tool_schemas_run_shell_write_file": ntok(json.dumps([t for t in fa.TOOLS if t["name"] in ("run_shell", "write_file")]))}
# 1. response_format json_schema: the plan schema must be honoured on the first try
allowed = ["run_shell", "write_file", "reply"]
schema = fa.plan_schema(allowed, 4)
d = post("/v1/chat/completions", {"model": "local", "max_tokens": 300, "temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05,
         "messages": [{"role": "system", "content": fa.PLAN_SYSTEM.format(app=fa.APP, net="online", home="/home/user")},
                      {"role": "user", "content": "Task from the user:\nCreate the folder ~/Ladder/one and, inside it, a file called hello.txt whose entire content is exactly this text: Hello from Fab OS\n\nReturn the JSON plan."}],
         "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}})
text = d["choices"][0]["message"]["content"]
plan, why = fa.parse_plan(text, allowed)
out["json_schema"] = {"valid_plan": plan is not None, "why": why, "steps": plan, "raw": text[:300]}
# 2. tool_choice required: exactly a tool call
tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in fa.TOOLS if t["name"] in ("run_shell", "write_file")]
d = post("/v1/chat/completions", {"model": "local", "max_tokens": 200, "temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05, "tools": tools, "tool_choice": "required",
         "messages": [{"role": "system", "content": fa.local_system_prompt("auto", {"online": True})},
                      {"role": "user", "content": "Task: save the word hi into /home/user/Documents/a.txt\nPlan:\n  1. [write_file] save the word hi into /home/user/Documents/a.txt  (NOW)\nStep 1 of 1 — do it now with ONE write_file call: save the word hi into /home/user/Documents/a.txt"}]})
msg = d["choices"][0]["message"]
calls = msg.get("tool_calls") or []
out["tool_choice_required"] = {"tool_calls": [(c["function"]["name"], c["function"]["arguments"][:200]) for c in calls], "finish_reason": d["choices"][0].get("finish_reason"), "content": (msg.get("content") or "")[:200]}
# 3. a 200-token free generation, then the peak RSS
post("/v1/chat/completions", {"model": "local", "max_tokens": 200, "messages": [{"role": "user", "content": "Describe a desktop computer in a paragraph."}]})
with open("/proc/%s/status" % sys.argv[1]) as f:
    out["vmhwm_kb"] = int([ln for ln in f if ln.startswith("VmHWM:")][0].split()[1])
out["repack"] = "off" if "--no-repack" in open("/proc/%s/cmdline" % sys.argv[1]).read() else "on"
json.dump(out, open("/out/probe.json", "w"), indent=1)
print(json.dumps(out, indent=1))
PY
kill $SPID; wait $SPID 2>/dev/null
grep -E "KV buffer size|CPU_REPACK|n_ctx " /tmp/probe-llama.log | head -5
