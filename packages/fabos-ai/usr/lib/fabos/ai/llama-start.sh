#!/bin/sh
# Fab OS built-in AI model: start llama-server sized for this machine, or wait until it is healthy.
#   llama-start.sh                  exec llama-server on 127.0.0.1:8081 with the built-in GGUF (used by fabos-llama.service)
#   llama-start.sh --wait-healthy N poll GET /health for up to N seconds (ExecStartPost readiness gate)
# Environment (from /etc/fabos/ai.env, overridable in ~/.config/fabos/ai.env):
#   FABOS_LLAMA_MODEL / AIOS_LLAMA_MODEL  path of the GGUF to serve (default: the built-in model)
#   FABOS_LLAMA_CTX                        context length in tokens (default: 8192, or 16384 with >= 7.5 GiB RAM)
#   FABOS_LLAMA_THREADS                    generation threads (default: physical cores, at most 8)
#   FABOS_LLAMA_EXTRA_ARGS                 extra llama-server flags (advanced)
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
# Context: KV cache for this model is ~28 KB per token, so 8192 tokens is ~230 MB; allow 16384 on machines with >= 7.5 GiB.
if [ -n "${FABOS_LLAMA_CTX:-}" ]; then CTX=$FABOS_LLAMA_CTX
elif [ "${mem_kb:-0}" -ge 7864320 ]; then CTX=16384
else CTX=8192
fi

# Threads: one per physical core (SMT siblings add little to matrix multiplication), capped at 8 so the desktop stays fluid.
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
# headroom matters more than a few percent on prompt processing.
if [ "${mem_kb:-0}" -ge 6291456 ]; then REPACK=""; else REPACK="--no-repack"; fi

echo "fabos-llama: serving $MODEL on 127.0.0.1:$PORT (ctx=$CTX threads=$THREADS repack=${REPACK:-on} mem=${mem_kb}kB)" >&2
# Flags verified against llama-server --help (Ubuntu package llama.cpp-tools 8681+dfsg-1): --alias, -c, -t, --parallel, --cache-ram (MiB cap on
# the prompt cache, default would be 8192), --jinja (tool calling via the model's chat template), --no-webui (API only), --no-repack.
# shellcheck disable=SC2086
exec /usr/bin/llama-server --host 127.0.0.1 --port "$PORT" -m "$MODEL" --alias local,qwen2.5-1.5b-instruct \
  -c "$CTX" -t "$THREADS" --parallel 1 --cache-ram 256 --jinja --no-webui $REPACK ${FABOS_LLAMA_EXTRA_ARGS:-}
