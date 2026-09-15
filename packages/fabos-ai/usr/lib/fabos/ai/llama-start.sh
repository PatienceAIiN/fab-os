#!/bin/sh
# Fab OS built-in AI model: start llama-server sized for this machine, or wait until it is healthy.
#   llama-start.sh                  exec llama-server on 127.0.0.1:8081 with the built-in GGUF (used by fabos-llama.service)
#   llama-start.sh --wait-healthy N poll GET /health for up to N seconds (ExecStartPost readiness gate)
# Environment (from /etc/fabos/ai.env, overridable in ~/.config/fabos/ai.env):
#   FABOS_LLAMA_MODEL / AIOS_LLAMA_MODEL  path of the GGUF to serve (default: the built-in model)
#   FABOS_LLAMA_CTX                        context length in tokens (default: 8192, or 16384 with >= 7.5 GiB RAM)
#   FABOS_LLAMA_THREADS                    generation threads (default: physical cores, at most 8)
#   FABOS_LLAMA_EXTRA_ARGS                 extra llama-server flags (advanced; appended last, so they override the defaults below)
set -eu
PORT=8081
DEFAULT_MODEL=/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf

if [ "${1:-}" = "--wait-healthy" ]; then
  limit=${2:-90}; i=0
  while [ "$i" -lt "$limit" ]; do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null; then exit 0; fi
    # systemd hands ExecStartPost the main PID ($MAINPID, systemd.exec(5)); stop waiting as soon as llama-server itself is gone.
    if [ -n "${MAINPID:-}" ] && ! kill -0 "$MAINPID" 2>/dev/null; then
      echo "fabos-llama: llama-server (pid $MAINPID) exited before answering /health" >&2; exit 1
    fi
    sleep 1; i=$((i + 1))
  done
  echo "fabos-llama: llama-server did not answer /health within ${limit}s" >&2
  exit 1
fi

MODEL=${FABOS_LLAMA_MODEL:-${AIOS_LLAMA_MODEL:-$DEFAULT_MODEL}}
if [ ! -r "$MODEL" ]; then
  echo "fabos-llama: model file not found or not readable: $MODEL" >&2
  exit 4
fi

mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
# Context: the KV cache for this model is 224 MiB at 8192 tokens (llama-server's own line: "CPU KV buffer size = 224.00 MiB",
# 28 layers, f16 K and V), so 8192 fits the 4 GB machine next to the desktop; allow 16384 on machines with >= 7.5 GiB.
# The stepwise driver (ADR-0018) never needs more than ~1.5k tokens per turn, so 8192 is also plenty of room for a long free-form chat.
if [ -n "${FABOS_LLAMA_CTX:-}" ]; then CTX=$FABOS_LLAMA_CTX
elif [ "${mem_kb:-0}" -ge 7864320 ]; then CTX=16384
else CTX=8192
fi

# Threads: one per physical core, capped at 8 so the desktop stays fluid. Measured in the Fab OS image on 2026-09-15 (6 physical
# cores / 12 threads, ctx 8192): generation 33.8 tok/s with 6 threads against 28.6 tok/s with all 12 (SMT siblings compete for the
# same vector units), prompt processing 103 vs 121 tok/s — the generation rate is what the user waits on, so physical cores win.
if [ -n "${FABOS_LLAMA_THREADS:-}" ]; then THREADS=$FABOS_LLAMA_THREADS
else
  cores=$(awk -F': *' '/^physical id/ {p=$2} /^core id/ {c[p ":" $2]=1} END {n=0; for (k in c) n++; print n}' /proc/cpuinfo 2>/dev/null || echo 0)
  if [ "${cores:-0}" -lt 1 ]; then cores=$(nproc 2>/dev/null || echo 1); fi
  THREADS=$cores
  [ "$THREADS" -le 8 ] || THREADS=8
  [ "$THREADS" -ge 1 ] || THREADS=1
fi

# Weight repacking (llama.cpp CPU backend) keeps a second, SIMD-friendly copy of the Q4 tensors in anonymous memory (596 MiB
# 'CPU_REPACK' in llama-server's own memory breakdown). Measured in the Fab OS image, 2026-09-14, 6 threads: peak RSS 2.06 GB with
# it vs 1.45 GB without; prompt processing 98-103 vs 91-94 tok/s; generation 28-33 tok/s either way. Below 6 GiB the 0.6 GB of
# headroom matters more than a few percent on prompt processing. Over a full L1+L2 ladder run (2026-09-15, --no-repack, ctx 8192)
# the peak grew to 1.86 GB as the KV cache and compute buffers were actually used — still inside the unit's MemoryHigh=2200M.
if [ "${mem_kb:-0}" -ge 6291456 ]; then REPACK=""; else REPACK="--no-repack"; fi

echo "fabos-llama: serving $MODEL on 127.0.0.1:$PORT (ctx=$CTX threads=$THREADS repack=${REPACK:-on} mem=${mem_kb}kB)" >&2
# Every flag below was checked against `llama-server --help` of the installed Ubuntu package llama.cpp-tools 8681+dfsg-1
# (2026-09-15, in the image); the help line each one comes from:
#   -a,   --alias STRING          set model name aliases, comma-separated (to be used by API)
#   -c,   --ctx-size N            size of the prompt context (default: 0, 0 = loaded from model)
#   -t,   --threads N             number of CPU threads to use during generation (default: <num_cpus>)
#   -np,  --parallel N            number of server slots (default: -1, -1 = auto)          -> 1: one user, one slot, one KV cache
#   -cram, --cache-ram N          set the maximum cache size in MiB (default: 8192, -1 - no limit, 0 - disable)   -> 256 MiB prompt cache
#   --cache-reuse N               min chunk size to attempt reusing from the cache via KV shifting, requires prompt caching to be
#                                 enabled (default: 0)   -> 256: the stepwise driver sends the same ~700-token system prompt every
#                                 turn; measured 2026-09-15: second request with the same prefix processed 6 new tokens, 433 reused
#   --temp, --temperature N       temperature (default: 0.80)    -> 0.2: server default for tool turns (the daemon also sends it per request)
#   --top-p N                     top-p sampling (default: 0.95, 1.0 = disabled)   -> 0.9 (same: server default + per request)
#   --repeat-penalty N            penalize repeat sequence of tokens (default: 1.00, 1.0 = disabled)   -> 1.05, Qwen2.5's own
#                                 generation_config value; measured 2026-09-15 without it: the model looped ". | . | . | ..." inside
#                                 tool arguments until the JSON broke
#   --jinja, --no-jinja           whether to use jinja template engine for chat (default: enabled)   -> explicit: tool calling
#                                 and response_format json_schema go through the model's own chat template
#   --webui, --no-webui           whether to enable the Web UI (default: enabled)   -> API only
#   --repack, -nr, --no-repack    whether to enable weight repacking (default: enabled)
# shellcheck disable=SC2086
exec /usr/bin/llama-server --host 127.0.0.1 --port "$PORT" -m "$MODEL" --alias local,qwen2.5-1.5b-instruct \
  -c "$CTX" -t "$THREADS" --parallel 1 --cache-ram 256 --cache-reuse 256 --temp 0.2 --top-p 0.9 --repeat-penalty 1.05 --jinja --no-webui $REPACK ${FABOS_LLAMA_EXTRA_ARGS:-}
