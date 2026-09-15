# ADR-0021: Image generation as a first-class agent capability (`generate_image`)

**Status:** accepted (2026-09-16) · extends ADR-0005 (agent architecture) and ADR-0020 (small-model driver) · privacy note in
legal/PRIVACY.md

## Context

"Draw me a logo", "make a wallpaper of the Western Ghats", "a picture of a blue circle" — the agent had no way to do it. Asked
anyway, a cloud model reached for ImageMagick or wrote an SVG by hand; the built-in 1.5B model planned `convert` commands that do
not exist in the image. The owner wants pictures to be a first-class tool: made by the provider the user already pays for, saved
where the user looks for pictures, shown in the chat like any other step.

Two of the four cloud providers Fab OS supports have an image API behind the same key the user already pasted (the `openai` and
`gemini` providers); two do not (Anthropic, DeepSeek); the built-in llama-server serves a text model only. Nothing may be downloaded on the
user's behalf (owner's rule 1), so a local diffusion model is **not** bundled: the smallest usable ones are 2–4 GB on disk and
need more RAM than the 4 GB machine the built-in text model already fills (ADR-0011).

## Decision

**1. One tool, `generate_image(prompt, size="1024x1024", n=1)`**, offered to every provider with the rest of `TOOLS`
(`fabos_agentd.py`). It returns `{path, width, height, provider, model, prompt, count, folder}` (`paths` when n > 1). Risk class
**MEDIUM** — "creates an image file" — so Ask mode asks first and Auto mode runs it (like `write_file`). Narration: *"Generating
the image now."* → *"Done, the image is saved in Pictures."*; the approval line: *"This needs your permission: generate an image
and save it in Pictures. Shall I go ahead?"*.

**2. Which provider draws (`image_provider()`, no network):**

| Situation | Provider used |
|---|---|
| setting `images.provider` = `openai` / `gemini` / `local` | that one (an error names the missing key or endpoint) |
| `images.provider` empty ("automatic") and the active provider is `openai` or `gemini` with its key stored | the active provider, same key |
| active provider is the built-in model or Ollama and `images.local_endpoint` is set | the local image endpoint |
| otherwise | the first configured of: `openai` key, `gemini` key, `images.local_endpoint` |
| nothing configured | the tool fails with exactly the sentence `IMAGE_NO_PROVIDER` in fabos_agentd.py: *"This provider cannot generate images; add an … or Gemini key in Settings, or a local image endpoint"* (the first provider is named by its label) |
| the test provider (`FABOS_AGENT_PROVIDER=fake`) | a real PNG drawn offline by the daemon (`fake_images`: a filled disc, colour from the prompt) — the ask-bar fixture and the ladder's FakeProvider run get a genuine file without any network |

A managed computer's policy applies as everywhere: `cloud_allowed=false` leaves only the local endpoint, `hosts_allowed` binds
every image endpoint, `tools_denied: ["generate_image"]` removes the tool. `/status` carries `images: {provider, ready, detail}`
so UIs and the ladder can tell in advance whether a picture can be made.

**3. The REST shapes, as documented by the providers (all through `urllib`, monkeypatched in tests/agent-test.py):**

- **`openai`** — `POST {openai.base_url}/images/generations` with `{"model": "gpt-image-1", "prompt", "n", "size"}`; `gpt-image-1`
  answers with base64 PNG data in `data[].b64_json`. Sizes are mapped to the three the model accepts by orientation (square →
  `1024x1024`, landscape → `1536x1024`, portrait → `1024x1536`). When the account cannot use `gpt-image-1` (a 400/403/404 whose
  message names the model or a verification), the call is retried once with the older model `dall-e-3` (`response_format:
  "b64_json"`, `n=1` per request, sizes `1024x1024` / `1792x1024` / `1024x1792`). A 401 is reported as a rejected key and is never
  retried on another model. Setting `images.openai_model` overrides the first model.
- **Gemini** — the native API (`gemini_native_base()`, one level above the `/openai` shim the chat uses), key in the
  `x-goog-api-key` header (never in the URL): `POST models/{model}:generateContent` with
  `{"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig":
  {"aspectRatio": <the documented ratio nearest to the requested size: 1:1, 3:2, 2:3, 4:3, 3:4, 5:4, 4:5, 16:9, 9:16, 21:9>}}}`;
  the picture comes back as `candidates[].content.parts[].inlineData.data`
  (base64). Default model `gemini-2.5-flash-image` (setting `images.gemini_model`); a model name starting with `imagen` takes the
  Imagen shape instead — `POST models/{model}:predict` with `{"instances": [{"prompt"}], "parameters": {"sampleCount",
  "aspectRatio"}}`, pictures in `predictions[].bytesBase64Encoded`. An answer without an image (a safety block) is a plain error
  that quotes `promptFeedback.blockReason`.
- **Local** — any server speaking `/v1/images/generations` on this computer or the LAN (stable-diffusion.cpp's server, LocalAI, an
  Automatic1111 with the `/v1` extension): setting `images.local_endpoint` is the `/v1` base or the full URL,
  `images.local_model` the optional model name, the stored `local_api_key` is sent as the bearer. `data[].b64_json` is read; a server
  that only returns `url`s has them fetched (same host policy).

**4. Files.** Every image goes to **`~/Pictures/Fab OS/<yyyy-mm-dd>-<slug>-<n>.png`** (the folder is created; the slug is the
prompt lower-cased, non-alphanumerics to `-`, at most 40 chars; a name already taken gets `-2`, `-3`… — nothing is ever
overwritten; a non-PNG answer keeps its real extension). Width and height are read from the PNG's own IHDR, not from the request.
The activity log records `image_generated` with provider, model, size and the path — whose **file name is derived from the first 40
characters of the prompt** (the slug above), so that much of the prompt is in the log; the prompt itself and the key never are.

**5. The model is told.** The free-form SYSTEM_PROMPT gets one rule: an image/picture/logo/poster/wallpaper/icon request →
`generate_image`, tell the user the returned path, and when the tool says the provider cannot draw, say exactly that and stop —
never paint with shell tools. The stepwise driver (ADR-0020): `generate_image` is in `STEP_TOOLS`, the planner's tool list and the
executor prompt (one worked example, `draw a blue circle -> generate_image {"prompt": "a blue circle"}`); `plan_sanity()` turns
an image request (`IMAGE_RE`: a drawing verb, or generate/make/create/design/render/produce whose **direct object** is an image noun —
the verb, an optional "me", an article or count, at most three plain modifier words, then the noun; no free gap, so "create a folder
named pictures", "make a list of the images", "give me the number of pictures", "generate a report of the photos", "create an
icon-sized thumbnail" are not image requests (a review of 2026-09-16 found the earlier 40-character gap matched all of them and put
a `generate_image` step in front of the user's real task); "open the picture folder", "take a screenshot", "copy the image files",
"make the logo bigger" are not either) whose plan has no `generate_image` step into one — drawing commands and files are dropped,
a `generate_image` step is put first, the plan is never emptied. Belt and braces: `plan_sanity(request, plan, images_ready)` gets
`image_capability(store).ready`; with **no image provider configured** the step is only put in when the plan itself tries to draw
(the model and the word list agree), otherwise the plan is left as planned and a note says so — a word-list misfire cannot sink an
unrelated task. And when a `generate_image` step fails on **configuration** (no provider, no key, `images.provider` naming a missing
key, a rejected key, a managed-policy refusal — `image_config_error`) the driver ends the task with that sentence at once instead
of spending its `STEP_RETRIES` on a failure the model cannot change. `step_check` passes the step only
when the returned file exists; `render_result` shows the model *"image saved to <path> (WxH, made by <provider>)"*, which is what
the reply step quotes back. The executor prompt was re-measured with the model's own tokenizer after these additions: **849 tokens
/ 3 104 chars** in the harness (`tests/local-driver-image.sh`, 2026-09-16), under the 900-token budget of ADR-0020.

**6. Privacy.** The prompt — and nothing else — is sent to the one image provider chosen by the rules above; the picture is saved
locally and is never uploaded anywhere. When the automatic rule picks a *different* provider than the active chat provider (a
Claude user with an `openai` key), the tool result says so (`provider`) and `/status.images.detail` explains it in advance.
Recorded in legal/PRIVACY.md.

## Measured / tested

- `python3 tests/agent-test.py` (class `Images`): the `openai` path writes a **real PNG** from a tiny base64 payload and reports the
  IHDR size (2×3) rather than the requested one; the `dall-e-3` fallback happens on a model refusal and not on a rejected key; the
  Gemini path (native URL, header key, `responseModalities`, `imageConfig.aspectRatio`, `inlineData`) and the Imagen `predict` path;
  the friendly error for Claude / DeepSeek / the built-in model / Ollama with no key or endpoint (and no network call at all); the
  automatic choice; the local endpoint (bare `/v1` or full URL, optional model); folder creation and the `-2` suffix; the request
  classifier, narrations, `IMAGE_RE`, `plan_sanity`, `step_check`, `render_result`.
- Through the running daemon with the scripted provider (`Daemon.test_34/35`): **"draw a cat"** → one `generate_image` step,
  MEDIUM, auto-approved in Auto and waiting for approval in Ask, a PNG under `~/Pictures/Fab OS`, the path in the closing line; the
  same on the stepwise driver, and `tests/ladder/checks.py l2g` accepts the file.
- The ladder (`tests/agent-ladder-vm.sh`) has **L2-g** — *"Draw a simple picture of a blue circle and tell me where you saved it."* —
  asserting a `generate_image` step and a PNG under `~/Pictures/Fab OS/` (`checks.py l2g` reads the signature and the IHDR and
  requires the `<yyyy-mm-dd>-<slug>-<n>.png` name). It is an optional SKIP when `/status.images.ready` is false; with
  `OPENAI_API_KEY` exported the ladder sets `images.provider=openai` for that one task and resets it afterwards. **Not run in a VM
  in this round** (no key on the build host; the QEMU ladder is the orchestrator's).
- The FakeProvider scenario is the ask-bar harness fixture: `POST /tasks {"request": "draw a cat"}` on a `FABOS_AGENT_PROVIDER=fake`
  daemon yields a task with a `generate_image` tool step whose output carries `path`, `width`, `height`.

## Consequences

- Fab AI Controls does not yet preview the picture in the chat; the step shows the path and the narration. A later UI track can
  render `output.path` as a thumbnail with an "Open in Fab Photos" (gwenview) action — the data is already in the step record.
- No image editing, variations or upscaling: one tool, one job. Cost is the provider's own; the agent never loops on it (n ≤ 4).
- `IMAGE_RE` is a word list, not language understanding: "I want a poster for the fest" is an image request, "make an image viewer"
  is not; the free-form models decide for themselves, the regex only repairs the small model's plans. Its unit test keeps a list of
  ordinary file tasks that must NOT match (folders, lists, counts, reports, thumbnails, backups named after pictures) — add to it
  before widening the pattern.
- `/status.images` and `GET /settings` only ask whether a key **exists** (`has_secret`, a stat); the key is decrypted once per
  picture in `generate_images`. The ask bar polls `/status` every 2–8 s, so the probe must never spawn `systemd-creds`.
- The model identifier `dall-e-3` appears only as the API string the fallback sends; Fab OS's own words for it are "the provider's
  older image model", per the naming rule in tests/branding-check.sh (vendor names only as provider labels, in allowlisted files).
