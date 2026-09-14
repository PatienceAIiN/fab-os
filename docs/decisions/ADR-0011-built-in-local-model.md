# ADR-0011: A built-in, fully offline AI model ships inside the image

Date: 2026-09-14. Status: accepted.

## Problem
The agent (ADR-0005) is the centre of Fab OS, yet until now it did nothing until the user created an account with a
cloud provider and pasted a key. The owner's rule is the opposite: **nothing the user must download** — every model,
font and app is inside the ISO — and the agent must work out of the box, offline, with no account. The image already
contains llama.cpp (Ubuntu metapackage `llama.cpp` 8681+dfsg-1; `/usr/bin/llama-server` is in `llama.cpp-tools`) and the
daemon already has a `local` provider that speaks the chat-completions HTTP API to `http://127.0.0.1:8080/v1` with tool
calling and needs no key (`PROVIDERS["local"]`, `RESULT_LIMIT_LOCAL`). What was missing: a model file, a way to serve it
that costs no RAM while idle, and a guard for small machines.

## Selected

**Model: Qwen2.5-1.5B-Instruct, GGUF Q4_K_M** (`qwen2.5-1.5b-instruct-q4_k_m.gguf`, 1 117 320 736 bytes,
sha256 `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`, from
`https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf`).

- **Licence: Apache-2.0**, "Copyright 2024 Alibaba Cloud". Checked on 2026-09-14: the Hugging Face model API reports
  `license: apache-2.0`, and the repository's `LICENSE` file (fetched from `.../raw/main/LICENSE`, 11 343 bytes) is the
  Apache-2.0 text with that copyright line; it is stored verbatim as `THIRD_PARTY_LICENSES/Qwen2.5-LICENSE.txt`.
  Redistributable without a non-commercial or acceptable-use clause, which rules out several otherwise attractive small
  models (Llama, Gemma and Phi variants with custom terms). Recorded in ATTRIBUTIONS.md, NOTICE, legal/THIRD-PARTY.md,
  THIRD_PARTY_LICENSES/README.md and on the system as `/usr/share/fabos/models/LICENSE-qwen2.5` + `README`.
- **Size / RAM (measured, not estimated).** 1.1 GB on disk. In the Fab OS image (`localhost/fabos:vm`), started through the
  shipped wrapper with the exact unit command line (`-c 8192 -t 6 --parallel 1 --cache-ram 256 --jinja --no-webui`, model
  file mounted read-only, warm disk cache):

  | Configuration | `/health` = 200 after | Peak RSS (`VmHWM`) after two tool-calling requests | Prompt / generation speed |
  |---|---|---|---|
  | default (weight repacking on; the wrapper picks this at >= 6 GiB RAM) | 3.09 s | **2 059 144 kB = 2.06 GB** | 98–103 tok/s / 28–33 tok/s |
  | `--no-repack` (the wrapper picks this below 6 GiB RAM) | 1.02 s | **1 448 076 kB = 1.45 GB** | 91–94 tok/s / ~33 tok/s |

  llama-server's own breakdown: host memory 1611 MiB = 1059 (weights) + 224 (context) + 327 (compute), plus 596 MiB
  `CPU_REPACK` when repacking is on. Load times are from a warm page cache (the file had just been hashed); a cold first
  load after boot must read 1.1 GB from disk and was not measured.
- **Tool calling works** with the daemon's request shape (`model: "local"`, `temperature: 0.2`, function `tools`,
  `Authorization: Bearer none`). The user message "Create a file named hello.txt containing hi" returned
  `finish_reason: "tool_calls"` with `write_file {"path": "hello.txt", "content": "hi"}` in both memory configurations, and
  again when `run_shell` and `open_app` were offered alongside `write_file` (231 / 365 prompt tokens, 27–30 completion
  tokens, 3.3–4.1 s end to end). The path varied between runs (`hello.txt`, `/home/user/hello.txt`, `home/hello.txt`):
  a 1.5B model needs the daemon's system prompt to state the working directory, which it does.
- Alternatives: Qwen2.5-0.5B and Qwen2.5-3B from the same Apache-2.0 family. 1.5B is the size that fits a 4 GB machine;
  the 3B Q4_K_M file is about 2 GB and would not leave room for the desktop. Llama 3.x and Gemma small models are under
  custom licences with use restrictions and are excluded by the open-source hygiene rule (only Apache-2.0, MIT, BSD or
  GPL with source offer may be bundled).

**Delivery: baked into the image, hash-pinned.** One `RUN` layer in `image/Containerfile` (right after the bundled-apps
layer, before the FabOS packages so package rebuilds never refetch 1.1 GB) downloads with `curl -fL --retry 3` (fails on
any non-2xx status), checks the byte count and the sha256 — the build fails otherwise — and writes
`/usr/share/fabos/models/{README,LICENSE-qwen2.5}` (the licence text comes from `/usr/share/common-licenses/Apache-2.0`
plus the Alibaba copyright line). The same name/size/hash/URL live in `/usr/share/fabos/ai-models.json` (package
fabos-ai) so UIs and `fabos-local-model` read one manifest. Both profiles (vm, iso) get the model. No "download on first
use" anywhere.

**Serving: on demand, zero RAM while idle.** Three systemd *user* units in fabos-ai (all pass `systemd-analyze verify
--user` on systemd 259 in the image):

| Unit | Role |
|------|------|
| `fabos-llama.socket` | `ListenStream=127.0.0.1:8080`, `Accept=no`, `Service=fabos-llama-proxy.service`; `WantedBy=sockets.target`, enabled globally by the package postinst |
| `fabos-llama-proxy.service` | `systemd-socket-proxyd --exit-idle-time=10min 127.0.0.1:8081` (`Type=notify` — the binary sends `READY=1`); `Requires/After=fabos-llama.service` |
| `fabos-llama.service` | `/usr/lib/fabos/ai/llama-start.sh` execs `llama-server --host 127.0.0.1 --port 8081 -m <gguf> --alias local,qwen2.5-1.5b-instruct -c <8192|16384> -t <physical cores, max 8> --parallel 1 --cache-ram 256 --jinja --no-webui [--no-repack]`; `ExecStartPost` polls `/health` up to 90 s and gives up early if `$MAINPID` is gone; `StopWhenUnneeded=yes`, `Nice=5`, `OOMScoreAdjust=500`, `MemoryHigh=2200M`, `MemoryMax=3G` |

The first connection to 8080 starts the proxy, which pulls in the server and waits until the model is loaded; ten
minutes after the last connection closes the proxy exits, nothing needs the server any more and systemd stops it. The
daemon's existing `base_url` is untouched. Every flag was checked against `llama-server --help` of the installed
`llama.cpp-tools 8681+dfsg-1` and `--exit-idle-time` against `systemd-socket-proxyd --help` (systemd 259.5). The wrapper
sizes threads from `/proc/cpuinfo` (physical cores), context from `MemTotal` (16384 tokens only with >= 7.5 GiB) and turns
repacking off below 6 GiB. `MemoryHigh`/`MemoryMax` work in user units because `user@.service` carries
`Delegate=pids memory cpu` in this image.

The socket is enabled in the **package postinst** (`systemctl --global enable fabos-llama.socket`), not in the image
layer that fetches the model: that layer runs before the fabos-ai .deb is installed and `systemctl --global enable`
refuses a unit file that does not exist yet (checked: "Failed to enable unit: Unit ... does not exist", rc 1). `prerm`
disables it again on removal.

**RAM guard.** `ConditionMemory=>3G` on all three units: on a 2 GB machine the socket is not even listening, the daemon
gets an immediate connection error, and the UIs explain via `fabos-local-model status` (JSON: `installed`, `bytes`,
`sha256_ok` — computed only with `--verify` —, `ram_ok` = MemTotal >= 3.5 GiB, `server_active`, `ready`, `message`).
The honest requirement in user-facing text is "needs 4 GB RAM": 1.45 GB for the model next to a 2 GB desktop.

**Sandboxing.** Process-level hardening only (`NoNewPrivileges`, `LockPersonality`, `RestrictRealtime`,
`RestrictNamespaces`, `SystemCallArchitectures=native`, `UMask=0077`) plus the cgroup memory limits. No
`ProtectSystem`/`PrivateTmp`: in user units those need unprivileged user namespaces, which the image restricts
(`kernel.apparmor_restrict_unprivileged_userns = 1` in `/usr/lib/sysctl.d/10-apparmor.conf`), and a unit that fails with
`NAMESPACE` would silently take the offline model away. Binds 127.0.0.1 only, runs as the user.

## Tradeoffs / risks
- A 1.5B model is good at short concrete tasks and tool calls, weak at long reasoning and at facts. The system prompt
  and the cloud providers cover that; the UI copy must not oversell it.
- The ISO grows by about 1.1 GB (the GGUF does not compress): the last build was 2.67 GB, so expect about 3.8 GB. The
  VM ext4 image (`ROOT_SIZE` 8G) must still have room; check on the next build.
- The daemon's default provider is still `claude`; making `local` the default when no key is configured is a change in
  fabos_agentd.py / the welcome wizard (owned by other tracks), tracked in the merge notes.
- Repacking policy and the 6 GiB / 7.5 GiB thresholds are heuristics from one machine; they are overridable per user in
  `~/.config/fabos/ai.env` (`FABOS_LLAMA_CTX`, `FABOS_LLAMA_THREADS`, `FABOS_LLAMA_EXTRA_ARGS`).
- The socket -> proxy -> server -> `StopWhenUnneeded` chain was verified with `systemd-analyze verify --user` and by
  running the exact server command line in the image; the runtime chain under a real user manager (idle unload after
  10 min, `ConditionMemory` on a 2 GB guest) is left to the QEMU boot tests.
