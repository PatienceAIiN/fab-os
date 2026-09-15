#!/bin/bash
# Fab OS — run tests/local-driver-test.py against the REAL built-in model inside the image (localhost/fabos:vm), with
# llama-server started exactly as fabos-llama.service starts it (packages/fabos-ai/usr/lib/fabos/ai/llama-start.sh) and the
# agent daemon from a source directory of your choice — so a BEFORE (baseline code) and an AFTER (this tree) run are directly
# comparable. No podman build, no QEMU: one `podman run` with the model file mounted read-only.
#
#   tests/local-driver-image.sh --label after                       # this tree's daemon + wrapper
#   tests/local-driver-image.sh --label before --agent-src build/baseline --llama-start build/baseline/llama-start.sh
#   options: --levels 1,2,h (h = the four held-out tasks)  --only l1-a  --driver default|stepwise|freeform  --extra-args "--no-repack"  --image localhost/fabos:vm
#            --model /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf  --timeout-scale 1.0
#   outputs: build/local-driver-<label>.json, build/local-driver-<label>.log (daemon + server logs appended)
#
# Inside the container there is no Wayland seat: applications launch with QT_QPA_PLATFORM=offscreen under dbus-run-session
# (kate and konsole stay running and are found by pgrep, as the ladder checks) and `wtype` is a shim that appends the text
# to $HOME/.local/share/fabos-test/typed.log and exits 0 — the driver/model is what is measured here, the real keyboard path
# is exercised by tests/agent-ladder-vm.sh in the VM.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)

if [ "${1:-}" = "--inner" ]; then
  # ---------------------------------------------------------------- inside the container
  shift
  LABEL=$1; AGENT_SRC=$2; LLAMA_START=$3; LEVELS=$4; ONLY=$5; DRIVER=$6; EXTRA=$7; SCALE=$8
  export HOME=/tmp/ldr/home XDG_RUNTIME_DIR=/tmp/ldr/run XDG_CONFIG_HOME=/tmp/ldr/cfg FABOS_AGENT_DATA=/tmp/ldr/data
  export FABOS_AGENT_PROVIDER=local FABOS_AGENT_PORT=8790 QT_QPA_PLATFORM=offscreen FABOS_POLICY_FILE=/tmp/ldr/no-policy.json
  mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME" "$FABOS_AGENT_DATA" /tmp/ldr/bin "$HOME/.local/share/fabos-test"
  chmod 700 "$XDG_RUNTIME_DIR"
  printf '#!/bin/sh\n# wtype shim for the container harness: no Wayland seat here; log what would have been typed\nif [ "$1" = "-k" ]; then printf "\\n" >> %s; exit 0; fi\nprintf "%%s" "$*" >> %s\nexit 0\n' \
    "$HOME/.local/share/fabos-test/typed.log" "$HOME/.local/share/fabos-test/typed.log" > /tmp/ldr/bin/wtype
  chmod +x /tmp/ldr/bin/wtype
  export PATH=/tmp/ldr/bin:$PATH
  echo "== inner: label=$LABEL agent=$AGENT_SRC llama-start=$LLAMA_START driver=$DRIVER extra='$EXTRA' nproc=$(nproc) mem=$(awk '/MemTotal/ {print $2}' /proc/meminfo)kB"
  echo "== llama-server: $(llama-server --version 2>&1 | grep -m1 version) / $(dpkg-query -W -f='${Version}' llama.cpp-tools 2>/dev/null)"
  sha256sum /usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf | cut -c1-20
  FABOS_LLAMA_EXTRA_ARGS="$EXTRA" "$LLAMA_START" > /tmp/ldr/llama.log 2>&1 &
  SPID=$!
  if ! MAINPID=$SPID "$LLAMA_START" --wait-healthy 120; then echo "llama-server did not become healthy"; cat /tmp/ldr/llama.log; exit 3; fi
  echo "== $(grep -m1 'fabos-llama: serving' /tmp/ldr/llama.log)"
  echo "== llama-server argv: $(tr '\0' ' ' < /proc/$SPID/cmdline)"
  # The executor's system prompt measured with the model's own tokenizer (budget < 900 tokens, ADR-0020); the baseline daemon has no such prompt
  python3 - "$AGENT_SRC" <<'PY' 2>&1 | tail -n 1
import json, sys, urllib.request
sys.path.insert(0, sys.argv[1])
try:
    import fabos_agentd as fa
    p = fa.local_system_prompt("auto", {"online": True})
except (ImportError, AttributeError) as e:
    print("== executor system prompt: not in this daemon (%s)" % e); sys.exit(0)
req = urllib.request.Request("http://127.0.0.1:8081/tokenize", data=json.dumps({"content": p}).encode(), headers={"Content-Type": "application/json"})
n = len(json.loads(urllib.request.urlopen(req, timeout=30).read())["tokens"])
print("== executor system prompt: %d tokens, %d chars (budget < 900 tokens)" % (n, len(p)))
PY
  dbus-run-session -- python3 "$AGENT_SRC/fabos_agentd.py" > /tmp/ldr/agent.log 2>&1 &
  DPID=$!
  for i in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8790/health 2>/dev/null && break; sleep 0.5; done
  curl -fsS http://127.0.0.1:8790/health >/dev/null || { echo "daemon did not start"; cat /tmp/ldr/agent.log; exit 3; }
  ARGS=(--home "$HOME" --label "$LABEL" --levels "$LEVELS" --driver "$DRIVER" --server-pid "$SPID" --out "/out/local-driver-$LABEL.json" --timeout-scale "$SCALE")
  [ -n "$ONLY" ] && ARGS+=(--only "$ONLY")
  python3 /src/tests/local-driver-test.py "${ARGS[@]}"; rc=$?
  echo "== llama-server VmHWM: $(grep VmHWM /proc/$SPID/status)"
  echo "== typed.log (wtype shim): $(head -c 300 "$HOME/.local/share/fabos-test/typed.log" 2>/dev/null | tr '\n' '|')"
  pkill kate; pkill -x konsole
  kill $DPID 2>/dev/null; kill $SPID 2>/dev/null; wait $DPID 2>/dev/null; wait $SPID 2>/dev/null
  echo "== agent log (tail)"; tail -n 60 /tmp/ldr/agent.log
  echo "== llama log (grep)"; grep -E "fabos-llama|n_ctx|threads|error|Error|cache" /tmp/ldr/llama.log | head -20
  exit $rc
fi

# ---------------------------------------------------------------- host side
LABEL=run; AGENT_SRC=$ROOT/packages/fabos-agent/usr/lib/fabos/agent; LLAMA_START=$ROOT/packages/fabos-ai/usr/lib/fabos/ai/llama-start.sh
LEVELS=1,2,h; ONLY=""; DRIVER=default; EXTRA=""; IMG=localhost/fabos:vm; SCALE=1.0
MODEL=${MODEL:-$ROOT/build/cache/qwen2.5-1.5b-instruct-q4_k_m.gguf}
[ -f "$MODEL" ] || MODEL=/home/harsh/Downloads/fabric-os/build/cache/qwen2.5-1.5b-instruct-q4_k_m.gguf
while [ $# -gt 0 ]; do
  case "$1" in
    --label) LABEL=$2; shift;;
    --agent-src) AGENT_SRC=$(cd "$2" && pwd); shift;;
    --llama-start) LLAMA_START=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); shift;;
    --levels) LEVELS=$2; shift;;
    --only) ONLY=$2; shift;;
    --driver) DRIVER=$2; shift;;
    --extra-args) EXTRA=$2; shift;;
    --image) IMG=$2; shift;;
    --model) MODEL=$2; shift;;
    --timeout-scale) SCALE=$2; shift;;
    *) echo "unknown option $1"; exit 3;;
  esac; shift
done
[ -f "$MODEL" ] || { echo "model file not found: $MODEL (set --model)"; exit 3; }
mkdir -p "$ROOT/build"
# paths are rewritten to the container's mounts: the repo at /src, extra dirs bound explicitly
map() { case "$1" in "$ROOT"/*) echo "/src${1#"$ROOT"}";; *) echo "$1";; esac; }
VOLS=(-v "$ROOT:/src:ro,z" -v "$ROOT/build:/out:z" -v "$MODEL:/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf:ro,z")
case "$AGENT_SRC" in "$ROOT"/*) ;; *) VOLS+=(-v "$AGENT_SRC:/agent-src:ro,z"); AGENT_SRC=/agent-src;; esac
case "$LLAMA_START" in "$ROOT"/*) ;; *) VOLS+=(-v "$(dirname "$LLAMA_START"):/llama-start-dir:ro,z"); LLAMA_START=/llama-start-dir/$(basename "$LLAMA_START");; esac
echo "== local-driver-image: label=$LABEL image=$IMG agent=$AGENT_SRC llama-start=$LLAMA_START levels=$LEVELS driver=$DRIVER extra='$EXTRA'"
podman run --rm -i "${VOLS[@]}" "$IMG" bash /src/tests/local-driver-image.sh --inner "$LABEL" "$(map "$AGENT_SRC")" "$(map "$LLAMA_START")" "$LEVELS" "$ONLY" "$DRIVER" "$EXTRA" "$SCALE" 2>&1 | tee "$ROOT/build/local-driver-$LABEL.log"
exit "${PIPESTATUS[0]}"
