# Research · Computer use — the two per-chat switches

Package 1.0-8 (over the air). Owner's request: "toggle animated switch for research, computer use — in the command bar aligned
in home and in the AI command center inside the app — matching, animated, smooth transition, and working effectively."

Two capabilities of the agent become two switches the user sees in both places they talk to it, and the daemon honours them
for real: **off means the tools are absent from the model's list**, the prompt says so, and a call that still names one is
refused deterministically. Ask / auto / bypass permission modes are untouched by either switch.

| Switch | Setting (default for new chats) | On | Off |
|---|---|---|---|
| **Research** | `agent.research` = `true` | the planner may use `web_fetch` and browse several pages; research style: *gather, then answer with the facts and end with a `Sources:` list of the URLs read* | `web_fetch` is not offered, **and `run_shell` may not fetch from the network either**: a command that does (curl / wget / aria2c, pip / apt / npm / cargo installs, git clone / fetch / pull, a script's HTTP library) is refused with the same decision — a local service on this computer (localhost, 127.x) still passes; the prompt says so; a request that needs the web gets the agreed sentence naming the switch |
| **Computer use** | `agent.computer_use` = `true` | the GUI tools `open_app` and `type_text` are offered ("show your work" stays as it is) | those tools are not offered, **and `run_shell` may not reach the screen either**: keystroke / pointer injection (wtype, ydotool, xdotool, kdotool), the session bus (qdbus6, dbus-send --session, gdbus, busctl --user), launchers (kstart, xdg-open, kde-open, gtk-launch, gio open, kioclient exec, kdialog, flatpak run), screen capture (spectacle, grim, …) and the executables of the installed graphical applications (the .desktop registry; `--headless` / `--convert-to` / `--version` / `--help` runs pass) are refused with the same decision; the planner is told to work with commands and files or to answer directly; a request to open or type into an application gets the agreed sentence naming the switch |

The agreed sentences live in one place, `fabos_agentd.CAPABILITY_OFF_REPLY`; the prompt quotes them and the scripted test provider
says them verbatim, so what the user reads is the same whichever model is behind the bar.

## What the switches are — and are not

A switch removes a capability from the **model**: its tools are gone from the list, the prompt says so, and the daemon refuses a call that
still names one — or a `run_shell` command that does the same thing by hand (`fabos_agentd.capability_shell_block`, the table above). The
shell has to stay: it is how files and commands get done, and it keeps the session's Wayland socket and D-Bus like any program the user
starts, which is exactly why the shell rule exists. Honest limits:

* **A classifier of the known ways, not a sandbox.** The shell rule names the input-injection tools, the session-bus clients, the
  launchers, the screen-capture tools and the installed graphical applications' executables; the network rule names the HTTP clients,
  the package managers' install verbs, remote git and the common HTTP libraries in a one-liner. A model that writes a novel program —
  its own Wayland client, its own socket code in a file it then runs — is not caught by either. Segment by segment, so `curl x; ls --help`
  gets no pass from the `ls`; `--help` / `--version` runs of the named tools pass on purpose.
* **Research is about reading the web**, not about all networking: `ssh`, `rsync`, `ping` and the mail tools keep their own risk class and
  the user's permission mode; loopback targets pass because a local service is not the web.
* **Computer use is about the user's screen**: what opens, types, drives or captures it. `notify_user` (a notification) and the terminal
  launchers of the .desktop registry (`Terminal=true`) are not part of it.
* **The hard controls are the administrator's**: `tools_denied` removes a tool for good, `sandbox_network: false` takes the network out of
  `run_shell` with a network namespace (`bwrap --unshare-net`). The switches are the user's per-chat choice on top of that, not a
  replacement for it.
* **Nothing else changes**: Ask / auto / bypass permission modes, the risk classes and the approval flow are untouched by either switch.

## Where the choice is kept

* **Per chat.** A chat remembers its own choice on its **root task** (`tasks.research`, `tasks.computer_use`; `NULL` = not stated).
  Every follow-up of the chat inherits it — the effective value is *task → root → default*.
* **Defaults for new chats.** The settings `agent.research` / `agent.computer_use` (`fabos settings agent.research false`, or the
  strip with no chat open). Changing a default never touches a chat that has its own choice.
* **API** (`fabos_agentd.py`):
  `GET /status` → `capabilities: {research, computer_use}` (the defaults) ·
  `POST /tasks {research?, computer_use?}` starts a chat with its own choice (a follow-up writes them to its chat's root) ·
  `PATCH /tasks/{id} {research | computer_use: bool}` changes the whole chat (any task of the chat may be named; the reply carries
  the effective values and `root_id`) ·
  `GET /tasks/{id}` → `research`, `computer_use` (effective) and `capabilities_source` (`chat` | `global`) ·
  `PUT /settings {agent.research: "false"}` (any of true/false/on/off/1/0; anything else is a 400).
* **History.** Every task that ran with a switch off has a `verify · capabilities` row ("off for this chat: Computer use (tools not
  offered: open_app, type_text)"); a tool call the model still made is recorded with decision `off-for-this-chat` and the
  narration "Sorry, computer use is off for this chat." — a `run_shell` command the switch refused the same way, its activity row
  `tool_off_for_chat` carrying the reason (`run_shell: starts dolphin on the screen`, `run_shell: fetches from the network (curl)`); the
  model's tool result names the switch and the sentence to say; each change is an activity row `chat_capability`.

## The control (one design, two renderers)

An iOS-style pill switch with a small icon and a label, in a strip of two: `[globe] Research (switch) · [keyboard] Computer use (switch)`.

Every number and word comes from **one file**, `packages/fabos-agent/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/code/modes.js`
(`TOKENS`, strict JSON between the `MODES-TOKENS` markers). The ask bar's QML imports it; Fab AI Controls (`command_center.py`,
`load_mode_tokens`) reads the same block from the installed file (`/usr/lib/fabos/agent → /usr/share/plasma/plasmoids/…`, the same
relative walk in the repository) and keeps a fallback copy that `tests/askbar-js-test.js` and `tests/ai-controls-render.py` require to
be identical. The two strips therefore cannot drift.

| Token | Value |
|---|---|
| track | 40 × 22, radius 11 (pill) |
| knob | 16, 3 px inset, travels 3 → 21 px (`knobX(p) = pad + p·(width − knob − 2·pad)`) |
| colours | off: text @ 28 %; on: the accent (`Kirigami.Theme.highlightColor` / `palette(highlight)`); knob: highlighted-text on the accent, the surface colour on the grey track; hover lifts the track 8 % |
| motion | knob slide **200 ms OutCubic**, colour **200 ms**; the strip's reveal in the bar **180 ms**; a switch never loops |
| strip | height 30, gap 16 between the two, 8 between icon · label · switch, icon 14, label Inter 12 medium (90 % on / 60 % off), radius family 12 (control) |
| focus | Tab focus; a 1.5 px accent ring 3 px outside the track; **Space / Enter toggle**; the label is part of the control (click toggles) |
| tooltip | what the switch does now + where the choice is kept: "… Remembered for this chat" / "… The default for new chats" (Space toggles) |
| confirmation | one line for 2.4 s: **"Research off for this chat"** / "Computer use on for new chats" |

**Home bar** (`contents/ui/ModeSwitch.qml`, `ModeStrip.qml`, wired in `main.qml`): the strip is part of the bar's *awake* state — it
unfolds under the field (180 ms, the card grows with it) while the bar is awake (interacted with in the last 30 s), the field has focus
or a conversation is open, and folds away otherwise; it sits in its own row aligned with the field's left edge, so it never overlaps the
microphone or **Do it**. It yields to the one notice that makes it pointless — the agent service is down (a switch could not be kept
then); with no provider or System-Wide AI off the strip stays, dimmed. In the compact (panel) form the 36 px card has no room: the strip
is the popup's first header row. The strips are Loaders that exist only while shown, so a folded bar has no pointer area under the field. A toggle is
applied at once (both knobs slide), persisted with ONE request — `PATCH /tasks/{root}` while a chat is open, `PUT /settings` otherwise —
and confirmed in one line **beside the switches, in the strip's own row** (a new row would recentre the card's rows and move the field —
seen on the VM, so the note never adds a row); while the request is in flight no snapshot may write an older value back
(`modeInflight`); a failure is a sentence in the status line and a re-sync. A new chat is posted with the strip's state
(`Modes.taskFields`), so it remembers them.

**Fab AI Controls** (`ModeSwitch`, `ModeStrip` in `command_center.py`): the same strip in the chat header (hidden while the main column
is under 1110 px — a 1280 px window with the sidebar shows the composer's copy alone; a 1400 px window or the enlarged layout shows both) and in the composer's second row beside the permission-mode chip; both are one state (`set_modes`) and move
together. With a chat open the strip shows that chat's effective values and a toggle PATCHes its root; on **New chat** it shows the
defaults and a toggle PUTs the setting; the toast confirms either. Programmatic changes (switching chats) animate too.

## Proof

* `python3 tests/agent-test.py Daemon.test_38_capabilities_settings_defaults_and_per_chat_choice Daemon.test_39_computer_use_off_means_no_open_app_step_and_a_helpful_message Daemon.test_40_research_off_means_no_web_fetch_and_on_means_a_cited_fetch`
  — defaults, per-chat storage and inheritance, validation; **computer use off → "open the files app" runs no `open_app` step and
  answers with the sentence** (on → one `open_app` step, Fab Files); the tool list and prompt the model got; a call the model still
  makes is refused with decision `off-for-this-chat`; **research off → no `web_fetch`, nothing requested from the stub page; on → the
  page is fetched and the answer carries the fact and `Sources:`**; a default of off inherited by a new chat, switched back on for
  that chat through PATCH, makes the next follow-up fetch again.
* `python3 tests/agent-test.py Daemon.test_41_a_switched_off_capability_binds_run_shell_too` — the shell rule: with Computer use off,
  `wtype`, `setsid -f dolphin`, `qdbus6 … KWin`, `kstart6`, `xdg-open`, `kdialog`, `spectacle`, `flatpak run` are refused and `ls`,
  `libreoffice --headless --convert-to`, `dolphin --version`, `dbus-send --system` pass; with Research off, `curl`, `wget`, `pip install`,
  `apt-get install`, `git clone` / `pull`, a python `urllib` one-liner, `npm install`, `$(curl …)` are refused and `curl http://127.0.0.1…`,
  `git status`, `pip list`, `echo see http://…`, `ssh`, `ping` pass; both on: nothing refused. Through `_gate` the step carries
  `off-for-this-chat` and the reason; **end to end, the scripted provider asked to "open the files app from the shell" has its
  `xdg-open` refused and answers with the sentence**.
* `node tests/askbar-js-test.js` — the TOKENS block parses, equals the live object and the Python fallback; readers for
  /settings, /status and /tasks; the toggle state machine (PATCH in a chat with a boolean, PUT the default otherwise, messages);
  persistence per thread; tooltips, failure lines, the visibility rule; the knob formula; no banned words.
* `podman run --rm -v $PWD:/work:Z -e QT_QPA_PLATFORM=offscreen localhost/fabos:vm python3 /work/tests/ai-controls-render.py /work/build`
  — tokens loaded from modes.js; both strips 40 × 22 + ring margin, 200 ms; a real click on the composer switch moves both knobs
  (the header knob is mid-slide right after the click, settled at off within 350 ms), toasts "Research off for this chat" and the daemon
  has it on the chat (the follow-up inherits); Space on the focused header switch does the same for Computer use; New chat shows the
  defaults, reopening the chat restores its choice; with no chat open a toggle PUTs the default and the next new chat POSTs the strip's
  state. Renders `build/ai-controls-modes-{on,research-off,both-off}-{dark,light}.png` and `build/ai-controls-modes-{dark,light}.png`.
* `tests/askbar-qml-test.sh` — the real applet loads with the strip (no QML diagnostics); the harness's geometry checks still hold
  (no pointer area outside the card / panel: the strips are Loaders that exist only while shown).
* `tests/modes-vm.py` (under the VM lock, disposable overlay) — the booted OS with these files installed: the home bar's strip
  photographed with both on and with Research off after a **real pointer click through QMP**, `fabos settings agent.research` reading
  `false` afterwards; the Fab AI Controls window with the same strip, toggled the same way. Evidence: `build/r8-modes/`.
