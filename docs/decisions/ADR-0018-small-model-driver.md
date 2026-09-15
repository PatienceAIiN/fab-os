# ADR-0018: A stepwise driver for the built-in small model — measured, not promised

**Status:** accepted (2026-09-15) · extends ADR-0005 (agent architecture) and ADR-0011 (built-in local model).

## Context

The built-in model (Qwen2.5-1.5B-Instruct, Q4_K_M, llama-server, ADR-0011) was run through exactly the same free-form loop
as the cloud providers: a 60-turn conversation in which the model sees the whole history and decides when it is done. On
the graded ladder (tests/agent-ladder-vm.sh, levels 1–2) it scored **2 / 11** (docs/QA.md, v1.0.3): it called one tool
and declared the task finished, mis-computed a CSV total in its head, and never opened Fab Editor to type. The cloud path
scored 21 / 21 on the same checks.

Facts established before deciding (all in the round-3 image `localhost/fabos:vm`, llama.cpp-tools 8681+dfsg-1, the model
file mounted read-only from `build/cache/`, 2026-09-15):

- The tools are identical for every provider; `run_shell` runs in the bubblewrap sandbox **with** network and `web_fetch`
  reaches any host the policy allows. Nothing in the OS blocks the local model's internet; when it said "I have no
  internet" that was the prompt and the loop, not the machine.
- The baseline, re-measured with the new harness (`tests/local-driver-image.sh --label before --agent-src build/baseline
  --llama-start build/baseline/llama-start.sh --extra-args --no-repack`, the daemon and wrapper of the pushed main,
  `build/local-driver-before.{json,log}`, finished 2026-09-15T07:30Z): **L1 2/6, L2 0/5**, the same two passes as in the
  QA record (count the files, open the terminal). Every one of the eleven tasks made exactly **one** tool call and ended
  with a done-message: `list_dir` then "I'm sorry, but I can't create the folder or file for you. I don't have the
  necessary permissions" (l1-a), `open_app kate` then "I have opened Fab Editor for you. Type the word hello into the
  window" (l1-f), `read_file` of one CSV then "Now, I will add the 'amount' column across all three files" (l2-a),
  `web_fetch` then "The JSON body is saved to ~/Ladder/health.json" with no file written (l2-e). Tasks took 6–66 s each;
  llama-server `VmHWM` 1.85 GB at the end.
- llama-server 8681 supports what a strict driver needs: `response_format: {"type": "json_schema", ...}` (grammar-
  constrained output — a plan request came back as valid JSON matching the schema on the first try), `tool_choice:
  "required"` (a tool call is forced), `/tokenize` (prompt budgets can be measured with the model's own tokenizer),
  `--cache-reuse` (the second request with the same 439-token prefix processed 6 new tokens and reused 433).

## Decision

**1. Two drivers, chosen per provider.** `Agent.driver_for()` / `driver_name()`: the `local` provider runs the new
**stepwise** driver; every other provider keeps the **free-form** loop unchanged (moved verbatim into
`Agent._run_freeform`). The setting `agent.driver` (`stepwise` | `freeform` | empty) overrides the choice either way, so a
strong local model can use the free-form loop and a weak cloud model the stepwise one. `/status` reports the effective
`driver`.

**2. The stepwise driver (`Agent._run_stepwise`, fabos_agentd.py):**

| Phase | What happens | Model call |
|---|---|---|
| PLAN | One call returns `{"steps": [{"tool", "goal"}]}` (tool from an enum of the tools policy allows plus `reply`; at most two steps more than the request has sentences, 3–8, enforced by the schema — measured: a one-line count came back as a seven-step story). llama-server enforces the schema through `response_format json_schema`; an endpoint that rejects `response_format` gets the schema in the prompt instead and the driver parses (strict JSON, then the first `{…}` block). A plan whose every step reaches for the web while the task names no web page or URL is rejected too (measured: `web_fetch 'https://example.com/api/grand_total'` for three local CSV files). Either way the planner is asked again with the reason shown, three attempts in all. The planner prompt carries one worked plan of the look-then-reply shape ("How big is ~/Pictures? Reply SIZE: <bytes>"). Then `plan_sanity()` applies deterministic repairs, each recorded as a `verify plan` row. First a step that repeats an earlier step's goal word for word (case, spacing and a final full stop aside) is dropped — measured: "copy the folder /tmp/ladder/notes to ~/Ladder/notes-copy" planned as `run_shell` and again as `save_result`, which then wrote 0 bytes into the copied folder three times and failed a task whose work was done. The rest come from the **user's own words**: the request says *type* and the plan opens an app without typing → a `type_text` step follows the `open_app`; the request says *do not create or change any file* → every `write_file`/`save_result` step is dropped (measured: the model planned `save_result` into the very folder it was told to leave alone; the plan is never emptied); the request asks for an answer (*how many*, *end your reply with…*, a question mark) and the plan never replies → a `reply` step is appended; the request says *unchanged / exactly as / verbatim* and a `write_file` follows a `web_fetch`/`read_file` → it becomes `save_result`; the request names no URL or web word → `web_fetch` steps are dropped; it never asks to open or type → `open_app`/`type_text` steps are dropped (none of these ever empties the plan). Recorded as an assistant step "Plan: …". | `complete()`, max 600 tokens |
| EXECUTE | One tool call per turn. The model sees ONLY: a compact system prompt, the task, the plan with the current step marked NOW, the verified results so far (last one clipped to 1 500 chars, earlier ones to 300, rendered as plain text — stdout, fetched text — not a JSON envelope), the previous attempt's error, and "do it now with ONE `<tool>` call". First attempt offers the planned tool plus `run_shell` with `tool_choice: required`; retries offer every tool so the model can change approach — except after a step whose command printed **one short value** (≤ 200 chars, ≤ 3 lines) but did not write the file its goal names: that retry offers **only `save_result`** (the hint names the path), a grammar-style constraint for the measured habit of re-running `find` three times instead of saving what the first run printed. When a retry repeats exactly the failed call, the next error starts with "you sent exactly the same call again and it failed the same way". Extra tool calls in the same answer are ignored and recorded. A `reply` step is a plain completion whose text becomes the task result. **`save_result {path}`** is a driver-only tool (never in the free-form loop's list): the driver writes the *previous tool call's output* — stdout, fetched text, file content, or the text `type_text` just put on screen (so "type it, then save it" saves exactly what was typed) — to that path through the real `write_file` tool (same risk gate, recorded as `write_file`), because a 1.5B model mangles data it has to retype inside JSON arguments (measured: the fetched `{"ok": true, "app": "Fab OS"}` came back as `ok\napp=Fab OS`). Three deterministic guards run before any write and fail the attempt with the reason shown: no earlier output at all, an **empty** earlier output (a `cp` prints nothing — without the guard 0 bytes went into the copied folder), or a path that is a folder. | `step()`, max 700 tokens |
| VERIFY | Deterministic first: `run_shell` exit 0; `write_file` file exists with exactly the written content; `open_app` process running 1 s later (`pgrep`) **and the right application** — when the request names exactly one of the desktop's apps (Fab Terminal = konsole, Fab Editor = kate, Fab Files = dolphin, browser = brave-browser) an `open_app` that starts anything else fails with "the task asks for Fab Terminal, which is konsole — you opened dolphin" (measured); `type_text` characters typed; `web_fetch`/`read_file` non-empty; then **every path named in the step's goal must exist afterwards** (skipped for delete/move/rename steps — `date +%F` exits 0 and writes nothing, which is how l1-d failed). Then a model self-check with a yes/no schema (`{"ok", "reason"}`, 60 tokens), shown the task, the tool result **and what the deterministic check observed afterwards** — exit code, the content of a small file the step just wrote, the names in a folder the step just changed (skipped for `open_app`/`type_text`; setting `agent.stepwise_selfcheck=false` disables it; it can fail a step, never pass a deterministically failed one). A failed step is retried up to **2** times with the error shown; after that the task **fails** with an honest message naming the step, the attempts and what was done before. | `complete()`, 60 tokens |
| OUTCOME | Before finishing: every path the user's request names must exist (deletes/moves excepted). For each missing one (max two) a repair step is appended and executed like any other: a `save_result` step when an earlier step produced output (the driver copies it; the model only confirms the path), else a `write_file` step "Create `<path>` exactly as the task asks, using the results above". | — |
| FINISH | If the plan had a `reply` step its text is the result; otherwise one short summary from the verified results (150 tokens), with a deterministic fallback sentence. | `complete()`, 150 tokens |

Every step is recorded as before (`tool_call` with narration, approvals through the same `_gate`), plus `verify` rows
(`ok: …` / `failed: …`) the CLI shows and the UIs ignore.

**3. The compact system prompt** (`LOCAL_SYSTEM_PROMPT`, `local_system_prompt()`), written for a 1.5B model: who/where/
when, the **Internet: ONLINE/OFFLINE** line from the probe below, "one step, one tool call", "never compute in your head",
"never say done before it is verified", the show-your-work rule (open_app → type_text → write_file), the tool schemas as
one-line JSON, seven worked one-line examples (write_file; run_shell count; run_shell CSV column sum with `csv.DictReader`;
run_shell extension rename with `${f%.txt}.md`; run_shell largest file by name with `-printf '%s %f'`; open_app then type_text;
save_result of the previous output), the app names. The planner's goals are **intent in words, never a command** — measured
before that rule, the planner wrote `find … -exec mv {} {}.md \\` into the goal and the executor copied it verbatim three times,
so the executor's examples never came into play; the planner also gets no Internet line (with one it invented an example.com
API for a local CSV sum), only the executor does.
Every EXECUTE turn also carries a **"Paths named in the task"** line: each path the request names, `~` expanded, and — refreshed
every turn — which of their folders do not exist yet (a 1.5B model drops directory components when it retypes a path, and it
repeats a failing `>` redirect rather than creating the folder). Measured with the model's own tokenizer (`/tokenize` in the
image, `build/tokens.sh`): **698 tokens / 2 576 chars** (budget < 900; tests/agent-test.py bounds it at 2 600 chars). The
planner prompt is 416 tokens, the self-check 68, the summary 51, an execute turn ~170 plus ~350 for two tool schemas — so a
whole turn is ~1 250 tokens and the 8 192 context is never close.

The examples are general shell idioms a 1.5B model does not reliably know (named-column CSV sums, `${f%.ext}` renames,
`find -printf '%s %f'` for the largest file); they are disclosed here because three ladder tasks exercise exactly those idioms. Likewise the planner is told to count or list
files with `list_dir` (a deterministic total) rather than the shell, after the model produced `find … -print0 | wc -c` — bytes,
not files — and answered "FILE COUNT: 248" for a folder of eight.

**4. Internet made explicit.** `network_status()`: one HTTPS `HEAD /` to the configured provider's host when it is public
(api.anthropic.com, api.openai.com, …) else `1.1.1.1:443`, timeout 2 s, cached 60 s; any HTTP or TLS answer counts as
online, DNS/connect/timeout failures as offline. Exposed as `/status.network = {online, target, checked, age_s}`
(non-blocking there: a stale value is returned and refreshed in the background) and used blocking at the start of every
stepwise task for the prompt's Internet line. Nothing else changed about networking: `web_fetch` and the sandbox were
already open.

**5. Server tuning (`packages/fabos-ai/usr/lib/fabos/ai/llama-start.sh`).** Every flag verified against
`llama-server --help` of llama.cpp-tools 8681+dfsg-1 in the image (the help lines are quoted in the script):

| Flag | Help line (verbatim) | Value and why |
|---|---|---|
| `-c, --ctx-size N` | size of the prompt context (default: 0, 0 = loaded from model) | 8192 (16384 at ≥ 7.5 GiB). KV cache 224 MiB at 8192 (server log). |
| `-t, --threads N` | number of CPU threads to use during generation (default: <num_cpus>) | physical cores, max 8 — **measured** 33.8 tok/s with 6 threads vs 28.6 with all 12 (nproc) on the build host; prompt processing 103 vs 121 tok/s. Generation is what the user waits on, so physical cores stay. |
| `-np, --parallel N` | number of server slots (default: -1, -1 = auto) | 1 |
| `-cram, --cache-ram N` | set the maximum cache size in MiB (default: 8192, …) | 256 |
| `--cache-reuse N` | min chunk size to attempt reusing from the cache via KV shifting, requires prompt caching to be enabled (default: 0) | 256 — measured: second request with the same prefix, 6 new tokens processed, 433 reused |
| `--temp, --temperature N` | temperature (default: 0.80) | 0.2 (server default; the daemon also sends it per request) |
| `--top-p N` | top-p sampling (default: 0.95, 1.0 = disabled) | 0.9 (server default; the local provider also sends it per request — cloud requests keep their previous shape) |
| `--repeat-penalty N` | penalize repeat sequence of tokens (default: 1.00, 1.0 = disabled) | 1.05, Qwen2.5's own `generation_config` value (also sent per request by the local provider). Measured without it: the model looped `. \| . \| . \| …` inside tool arguments until the JSON broke |
| `--jinja, --no-jinja` | whether to use jinja template engine for chat (default: enabled) | explicit: tool calling and `response_format` go through the model's chat template |
| `--webui, --no-webui` | whether to enable the Web UI (default: enabled) | API only |
| `--repack, -nr, --no-repack` | whether to enable weight repacking (default: enabled) | off below 6 GiB (ADR-0011) |

`--wait-healthy` and the systemd units are unchanged. `FABOS_LLAMA_EXTRA_ARGS` is appended last, so a user override such
as `--temp 0.3` wins.

**Memory (VmHWM of llama-server, `/proc/<pid>/status`, ctx 8192, 6 threads, the model mounted read-only):** 2.03 GB with
repacking on (the wrapper's choice at ≥ 6 GiB) after the probe requests; **1.85 GB with `--no-repack`** (the 4 GB-machine
configuration) after the full BEFORE ladder run; 1.53–1.65 GB with `--no-repack` after the stepwise runs (shorter prompts
touch fewer KV cells). All inside the unit's `MemoryHigh=2200M` / `MemoryMax=3G`.

**6. Measurement harness.** `tests/local-driver-test.py` runs the ladder's L1 and L2 task texts (parsed out of
`tests/agent-ladder-vm.sh`, so they cannot drift) against a running daemon and grades them with `tests/ladder/checks.py`
imported as a module and the committed answer key — PASS is decided by the checker, never by the agent's words.
`tests/local-driver-image.sh` runs it inside the image: llama-server started through the shipped wrapper, the daemon from
a chosen source directory (so BEFORE = the HEAD daemon and AFTER = this tree are directly comparable), applications
launched with `QT_QPA_PLATFORM=offscreen` under `dbus-run-session` (kate and konsole stay running and are found by
`pgrep`, as the ladder checks), and `wtype` replaced by a shim that logs the text (no Wayland seat in a container; the
real keyboard path is exercised by the ladder in the VM). The harness mirrors the ladder script's own preconditions and
nothing more: `mkdir -p ~/Ladder` before the first task (the ladder's "fresh scratch space" step) and seeding
`~/Ladder/notes-copy` before l2-b when l1-e left none (the ladder's `test -d … || cp -r` line). Each task's tool inputs and
verify lines are printed, so a FAIL is diagnosable from the log alone. `tests/local-driver-table.py` renders the
BEFORE/AFTER table below from the two JSON reports, so no number in it is typed by hand.

## Results (real model, in the image, 2026-09-15)

See the table in docs/LOW-RAM.md ("The built-in model") — it is the record; the numbers below are copied from
`build/local-driver-{before,after}.log`.

RESULTS_TABLE_PLACEHOLDER

## Consequences

- The cloud path is unchanged: same loop, same request shape (`temperature 0.2`, no `top_p`, no `tool_choice`), covered
  by the same tests. `FakeProvider` scripts the stepwise phases too, so tests/agent-test.py covers plan parsing and
  repair, one tool per turn, deterministic and self-check retries, the honest give-up, the outcome repair step,
  `save_result` (verbatim copy; refused when there is no output, an empty output or a folder path), the duplicate-step
  drop, the driver selection and the network probe without a model.
- A failed step now fails the task with the step named instead of a cheerful "done". That is the intended trade: the
  user sees what did not work.
- Each stepwise task costs 3 + 2·steps model calls; with the compact prompts and `--cache-reuse` the local model is
  faster per task than before (see the table), not slower.
- The self-check is the weakest part: on the CSV and rename tasks the model approved wrong results even when shown the
  folder's new names. It never passes a step the deterministic checks failed, and it can be switched off
  (`agent.stepwise_selfcheck=false`).
- `save_result` is the one place the driver does work the model would otherwise do: it copies bytes. The model still
  chooses the plan, the command and the path; what it can no longer do is corrupt data on the way to the file.
- Not done here: the free-form loop for the local model is still reachable (`agent.driver=freeform`) and still bad; a
  future track may retire it. The mail task (L2-f) needs the user's own account and was not measured here.
