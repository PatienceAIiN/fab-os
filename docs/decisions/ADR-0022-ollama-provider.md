# ADR-0022: Ollama as a provider (small and large local models), and where GPU/NPU acceleration comes from

**Status:** accepted (2026-09-16) · extends ADR-0011 (built-in local model) and ADR-0020 (small-model driver) · complements
ADR-0021 (images)

## Context

The built-in model (Qwen2.5-1.5B, llama-server from the Ubuntu package, ADR-0011) is what ships inside the ISO and what a 4 GB
machine can run. Users with more RAM or a GPU want larger local models — 8B, 14B, 30B — and the owner asked for Ollama, the most
common way people already run them, as a provider "for large and small models". Two facts bound the design:

- **Ollama is not in the Ubuntu 26.04 archive** (`apt-cache search ollama` in a plain `ubuntu:26.04` container on 2026-09-16 lists
  only `python3-ollama`, a client library) and its official installer downloads about 1 GB. Under the owner's rule "nothing the user
  must download" it cannot be a silent dependency; it can only be **the user's own install**, and Fab OS must say so plainly.
- **llama.cpp in the image is CPU-only.** The Ubuntu package `llama.cpp-tools 8681+dfsg-1` depends on `libggml0`, and in the built
  image `ls /usr/lib/x86_64-linux-gnu/ggml/backends0/` holds only `libggml-cpu-*.so`; `llama-server --list-devices` prints no
  device. Checked in a plain `ubuntu:26.04` container the same day (`apt-get update; apt-cache search ggml`): the 26.04.1 archive **does**
  carry a Vulkan backend — the package is named **`libggml0-backend-vulkan`** (the name `libggml-vulkan` in the round brief does not
  exist; `apt-cache policy libggml-vulkan` → `Candidate: (none)`), next to `libggml0-backend-blas` and `libggml0-backend-hip`. It is
  not installed in the image. `ggml` loads its backends dynamically from that directory, so installing the package makes
  llama-server load Vulkan on its own. **Measured on 2026-09-16 in an ephemeral `localhost/fabos:vm` container** (`build/vulkan-probe.sh`
  → `build/vulkan-probe.out`; no podman build; the build host has no GPU and the container's only Vulkan device is Mesa's software
  rasteriser `llvmpipe`, *vulkaninfo: deviceName = llvmpipe (LLVM 21.1.8, 256 bits)*): `apt-get install libggml0-backend-vulkan`
  brings **0.9.11-1, Installed-Size 55 638 kB**, depends on `libvulkan1` (already in the image); afterwards `load_backend: loaded Vulkan
  backend from …/libggml-vulkan.so` then **`ggml_vulkan: No devices found`** — the software rasteriser is *not* used, `llama-server
  --list-devices` stays empty and everything runs on the CPU backend as before. Same model, `-c 2048 -t 6 --no-repack`, two 128-token
  generations each: CPU-only **33.7 / 33.5 tok/s** generation (prompt 90 / 81 tok/s), with the Vulkan backend installed but device-less
  **32.6 / 32.2 tok/s** (prompt 85 / 79), with `--device none` 28.9 / 28.8; peak RSS 1.22 GB → 1.28 GB (the loaded library). So on a
  GPU-less machine the package costs about 56 MB of disk, 56 MB of RSS and about 3 % of generation speed and gains nothing; what it
  gains on a real iGPU (Intel, AMD through Mesa) — and whether an iGPU shared with KWin then stutters — **could not be measured on this
  host**. See Consequences.
- No package in the archive, and nothing Fab OS ships, drives an NPU (Intel NPU, AMD XDNA, Qualcomm Hexagon) for llama.cpp.

## Decision

**1. `PROVIDERS["ollama"]`** — label *"Ollama (on this computer)"*, chat-completions base `http://127.0.0.1:11434/v1` (the same `/v1`
API shape llama-server speaks), **no key** (`no_key`; the optional `ollama_api_key` secret — `fabos set-key ollama` — exists only for a reverse-proxied remote
Ollama), the same provider class the built-in `local` model uses. It appears in the Settings dropdown because the dropdown reads the table. Ollama's own API sits one level
up (`ollama_native_base()`): `GET /api/tags` for the model list, `POST /api/show` for a model's capabilities and size.

**2. The model** is the setting `ollama.model`, else **the largest installed model that fits this machine's RAM**
(`ollama_pick_model`, table `OLLAMA_RAM_TABLE`, `MemTotal` from /proc/meminfo):

| RAM (MemTotal) | largest model chosen |
|---|---|
| up to 4.5 GiB | ≤ 3B parameters |
| up to 8.5 GiB | ≤ 8B |
| up to 16.5 GiB | ≤ 14B |
| up to 32.5 GiB | ≤ 34B |
| more | ≤ 72B |

The parameter count comes from Ollama's own `details.parameter_size` ("7.6B"); each row names a size *class* — Ollama reports
`qwen2.5:3b` as 3.1B, `llama3.2:3b` as 3.2B, `qwen3:14b` as 14.8B — so a 10 % allowance (`OLLAMA_CLASS_SLACK`) is applied to the
bound. A model without a parameter count counts as fitting. When
nothing fits, the smallest installed model is used anyway (Ollama will page; `fabos ollama status` shows the bound so the user
knows why it is slow). With no model installed the agent fails with the pull command to run (`ollama pull qwen2.5:3b`).

**3. Tool calling and the driver.** `POST /api/show` lists a model's `capabilities`. With `"tools"` present the provider sends the
tools API as for every other endpoint. Without it, the provider class (`text_tools=True`) runs the **JSON-in-text protocol**: the
tools are described in the system prompt (`text_tool_protocol()`: one line per tool with its argument names and types, and the
answer shape `{"tool": "<name>", "args": {...}}`), earlier calls and results travel as plain assistant/user text, and the reply is
parsed by `parse_text_tool_call()` — the first JSON object naming a known tool, also accepting `name`/`function`/`tool_name` for the
tool and `arguments`/`input`/`parameters` (a dict or a JSON string) for the arguments, the chat-completions nesting
`{"function": {"name", "arguments"}}`, flat arguments beside the tool key, and a ```json fence or prose around the object.
Unparseable arguments come back as `{"_raw": …}` so the driver's existing "arguments were not valid JSON" retry handles them;
plain text without a tool call ends the turn. An older Ollama whose `/api/show` has no `capabilities` list is treated as tool-capable
and the server's own error stands. The **driver** (`driver_name()`): `agent.driver` wins; else Ollama runs the **stepwise** driver
of ADR-0020 when the model has no tools API **or** fewer than `OLLAMA_FREEFORM_MIN_B` = 7B parameters (the measured reason small
models need it), and the free-form loop for a tool-capable 7B+ model. Small models get the local result limit (8 000 chars), large
ones the cloud limit.

**4. API and CLI.** `GET /providers/ollama/models` → `[{name, size, parameter_size, quantization, family, modified_at,
parameter_b}]` (largest first; 30 s cache; `?refresh=1`; 503 with the reason when Ollama is down). `GET /providers/ollama/status` →
`{installed, running, models, model, model_source, ram_gib, max_parameters_b, install_command, bundled: false}`. `POST
/providers/test {"provider": "ollama"}` works without a key and tells *"Connected, but no models are installed — pull one first"*
apart from *"cannot reach Ollama"*. `POST /providers/ollama/install {"confirm": true}` runs the **official installer** — `curl -fsSL
https://ollama.com/install.sh | sh` — as root through the polkit path of ADR-0017 (`Tools.run_as_root`: a single-use authorisation
record, `pkexec rootexec`, the user's own password in the system dialog), never silently, and records `ollama_install_requested` /
`_done` / `_failed` in the audit log. On a managed computer (ADR-0017 policy.json) the installer — a root download from
`ollama.com` — goes through the same gate as every other outbound endpoint: `POLICY.require_host("ollama.com")` against
`hosts_allowed`, and nothing is downloaded at all when `cloud_allowed` is false; the daemon answers 403 *"Managed by your
organisation …"* and records `ollama_install_refused`. **CLI:** `fabos ollama status | models | install [--yes] [--force]` — `install` without `--yes`
only prints the command; `fabos check ollama`; `fabos settings provider ollama`, `fabos settings ollama.model llama3.1:8b`.
`/status` for the Ollama provider reports `provider_ready: true` (no key), the effective model from the cache (never a network
call on a poll) and the driver.

**5. Not bundled, said plainly.** README, `fabos ollama status` (`bundled: false`) and `fabos ollama install` all say Ollama is not
part of Fab OS and what its installer downloads. Ollama binds `127.0.0.1:11434` by default; with it selected, prompts and tool
results stay on the machine like the built-in model's (legal/PRIVACY.md).

**6. GPU / NPU — the honest statement.** The built-in path (llama.cpp from the Ubuntu package) runs on the **CPU only** in every Fab
OS image built so far. **Ollama is the path to GPU acceleration** on this release: its installer brings its own CUDA/ROCm runtimes
and Vulkan support for the hardware it detects, and Fab OS then simply talks to `127.0.0.1:11434`. Fab OS ships no NPU runtime and
promises none; whether an NPU is used depends entirely on the user's Ollama build and their device, per Ollama's documentation.

## The local model's web and multi-step path — measured, before and after (owner: "local model unable to perform web fetching and multipart")

Two held-out tasks were added to `tests/local-driver-test.py` (level `h`, names in no prompt) and run in the image with the real
built-in model through `tests/local-driver-image.sh --levels h --only h-e,h-f --extra-args --no-repack` (2026-09-16; `build/
local-driver-before-heldout.json`, `build/local-driver-after-heldout.json`):

| Task | before (pushed main's daemon, `git show HEAD:…fabos_agentd.py`) | after (this tree) |
|---|---|---|
| **h-e** *fetch http://127.0.0.1:8790/health and save it to ~/Ladder/h.json* — the check requires the file to parse with `ok: true` **and** a `web_fetch` step to have run | **PASS** 48.1 s · `web_fetch:1 write_file:1` (web_fetch → save_result) | **PASS** 62.1 s · `web_fetch:1 write_file:2` |
| **h-f** *Create the folder ~/Ladder/pack, write two files inside it — a.txt containing the word apple and b.txt containing the word banana — then list the names of the files in that folder into ~/Ladder/pack/index.txt, one per line.* | **FAIL** 50.1 s · `run_shell:1 write_file:3` · index.txt held `a.txt:apple` / `b.txt:banana` | **PASS** 64.2 s · `run_shell:4` · a.txt=apple, b.txt=banana, index.txt lists a.txt and b.txt |

So **web_fetch was chosen by the unchanged daemon**: the owner's "unable to fetch" was not the tool choice (the executor prompt's
web_fetch line and the planner's wording are therefore unchanged in this round — no fetch example was added, and the
measurement is of the tool list as it was). The multi-step failure had a driver cause: `plan_max_steps()` allowed "two more steps
than the request has sentences", and h-f is **one** sentence — the schema capped the plan at three steps (mkdir, a.txt, b.txt),
the index step was squeezed out and the outcome repair of ADR-0020 wrote the model's own idea of an index. `plan_max_steps` now also
counts the request's explicit clause breaks (an em dash, a semicolon, "then", "finally": `PLAN_CLAUSE_RE`), so h-f gets five steps and
the same request planned `mkdir`, two `echo >` steps and `ls -1 … > index.txt`, all verified. Unit-tested in
`StepwiseUnits` (a listed one-sentence request → 5; twelve sentences still cap at 8). Executor prompt after the round's additions:
849 tokens / 3 104 chars in the harness (budget 900); planner prompt 637 tokens (`build/tokens.sh`, llama-server `/tokenize`).
Peak llama-server RSS over the two tasks: 1.45 GB before, 1.65 GB after (`VmHWM`, `--no-repack`). One run each is a sample, not a
guarantee (ADR-0020).

## Consequences

- A Fab OS user with 16 GB and an Ollama install picks *Ollama* in the dropdown and gets a 14B model with the cloud-style free-form
  loop; a 4 GB user gets a 3B model on the stepwise driver — with no change anywhere else in the OS.
- The JSON-in-text protocol is weaker than a native tools API (no grammar, no `tool_choice: required`); it exists so that a model
  Ollama marks tool-less still works through the stepwise driver, whose retries already handle a missing or malformed call.
- **Vulkan backend for the built-in model — measured, deliberately not shipped in this round.** The package exists
  (`libggml0-backend-vulkan`), the image's ggml loads it automatically, and on a GPU-less machine it is harmless (no device found, the
  CPU path runs; ~3 % slower, +56 MB) — the numbers are in Context. It is **not** added to the Containerfile here because (a) the
  gain on real iGPU hardware is unmeasured on the build host, (b) llama-server would offload every layer to any Vulkan device it finds,
  and a weak iGPU shared with the compositor can be slower than the CPU and make the desktop stutter — the low-RAM promise of
  docs/LOW-RAM.md, and (c) no image is rebuilt in this round to verify it. The next round can add the one package line and measure
  on an Intel/AMD laptop: `llama-server --list-devices`, tokens/s against the CPU path, KWin frame times while generating; and
  `llama-start.sh` gets a `--device none` switch (`FABOS_LLAMA_DEVICE`) if a machine needs to opt out. Until then Ollama is the GPU path.
- `docs/QA.md` is not updated by this round (another track owns the release record); the numbers above are the record for now.
