# Fab OS design system

Source of truth: `brand/brand.conf` and the generators in `brand/gen/`. Consumers:

| Surface | How tokens reach it |
|---------|---------------------|
| Plasma shell (panels, popups, tooltips, tray, notifications) | `brand/gen/plasma_theme.py` → FabOS Plasma theme; colours follow the active colour scheme through KSvg `current-color-scheme` classes; radii from `radius.panel/popup` |
| Qt/KDE applications | Fab Dark / Fab Light colour schemes (generated in `fabos-desktop` postinst from Breeze structure + token colours); Breeze widget style; fonts from `/etc/xdg/kdeglobals` |
| Fab OS apps (Fab AI Controls, Updates, Feedback, Welcome) | Qt palette roles (`palette(base)`, `palette(highlight)`, …) so they follow the scheme; radii/spacing from tokens |
| Ask bar plasmoid | `Kirigami.Theme` colours; motion durations from `motion.duration` |
| Icons | FabOS icon theme: Material Symbols glyph on a tile with `radius.iconTile`, tile colours from `color.iconTiles`; falls back to Breeze |
| Greeter, splash, boot | generated PNG/SVG assets from `brand/gen/make_assets.py` |
| Website | CSS variables mirroring the dark semantic set |

Rules: no hard-coded colours in Fab OS code (palette or Kirigami.Theme only); one radius scale; Inter everywhere;
animations from the motion tokens and disabled under Reduce Motion; every state (loading/empty/error/disabled)
must be visible and truthful.

## Voice states (fabos-voice)

Every surface that offers voice (ask-bar microphone, Fab AI Controls composer, notifications from `fabos-voiced`)
shows the same states. Text always accompanies the glyph; nothing is colour-only; copy comes from
`packages/fabos-voice/usr/lib/fabos/voice/phrases.py` (Indian English, one sentence each) — UIs reuse it.

| State | Source of truth (`fabos-voice status`) | Visual | Sound / speech |
|-------|----------------------------------------|--------|----------------|
| Unavailable | binary missing or `stt` = `none` | mic button disabled, tooltip "Voice is not available on this machine" | none |
| Idle, wake on | `wake` true | mic glyph in muted `palette(text)`; optional hint "Say 'Hey Fab'" | none — PocketSphinx listens on-device |
| Listening | `listening` true (after the wake phrase or a mic press) | mic glyph in accent (`#3B6EF5` / `#6E9BFF` dark) with a soft 1.5 s pulse from the motion tokens (static ring under Reduce Motion); notification "Listening…" | 180 ms two-tone chime (880 → 1320 Hz) before recording |
| Transcribing | recording ended, text pending | indeterminate progress in the composer; the text lands in the field | none |
| Speaking | `speaking` true | small speaker glyph on the step row whose narration is being read | the narration sentence (cloud Indian-English voice, else eSpeak NG); wake detection is paused |
| Needs you | task `waiting_approval` / `waiting_user` | the existing approval card / question row, focused | "This needs your permission: … Shall I go ahead?" then a 6 s listen; only a short, answer-shaped yes/no counts (a sentence that merely contains "fine" does not), an as-root or CRITICAL step needs a clear "yes"; unclear → "Okay, I will wait for you to decide on screen." |
| Busy, still listening | `wake` true while a task is followed | mic glyph muted; the running task's row | "Hey Fab" cuts in: "stop" cancels the task, another request starts a new one, silence → "No problem, carrying on with the task." |
| Off | `wake` false and setting `voice.enabled` false | mic button normal (push-to-talk still works); Voice toggle off in Fab AI Controls | none |

Components in use: Plasma/Kirigami controls for the shell, Qt Widgets
(Breeze) for Fab OS apps. A Fab-authored widget kit is a later phase; until then consistency comes from the
shared scheme, fonts, radii and icon language.

## Fab AI Controls (the agent's chat app)

`packages/fabos-agent/usr/lib/fabos/agent/command_center.py` (PyQt6; the executable, desktop-file and icon ids keep the
historical name `fabos-command-center`). It is the reference implementation of the design language in a Fab OS app:

| Piece | Spec |
|---|---|
| Layout (docs/design/CHAT-UI-BRIEF.md) | two columns: **sidebar 282 px** (padding 20: "New chat" accent pill 242×36 r12 · search r12 · chats grouped Today / Yesterday / Earlier as 48 px r12 rows with a chat glyph, hover fill text @ 6 % · hairline · bottom group of 48 px rows: Clear conversations, Appearance follows system, Settings, About Fab OS) · **main column** (header: title + status line, provider chip with a green/amber dot "Claude · ready / not configured", enlarge, settings, System-Wide AI switch, report; empty state = Fab AI mark + "Fab AI Controls" 32/600 and three columns *Try asking* / *What I can do* / *Keep in mind* of 276×48 cards r8 at text @ 4 %; conversation; **composer** = two-row card r24: text row "Ask me to do anything…" (Enter sends, Shift+Enter newline, grows to 6 lines), then mode chip *Ask / Auto / Bypass* left and mic + filled circular Send/Stop right). Spacing grid 12/16/20 |
| Radii | controls 12 · fields 14 · small cards 8 · cards 20 · confirmation dialogs 20 · pills, composer, edit card and settings popup 24 |
| Colour | the whole stylesheet is generated from the application `QPalette` (`build_style`): window / base / alternate-base surfaces, text, highlight = accent. Dark and light schemes both work, also when the system switches at runtime: Qt delivers `ApplicationPaletteChange` to the window's `event()` (never to `changeEvent()`), which re-runs `apply_style`, guarded by a palette signature so `setStyleSheet`'s own `PaletteChange` cannot loop. Only tiny semantic marks (risk LOW→CRITICAL, provider dot, the check/cross animation: green #34C759 / amber #FF9500 / red) use fixed colours, always next to text |
| Type | Inter (14 px body, 15 px messages, 16/600 titles, 18/600 column titles, 32/600 empty-state title, 12 px muted, 12.5 italic narration); JetBrains Mono for code blocks, on a tinted (`text` at 8 %) background |
| Icons | Material-style 24-grid glyphs as inline SVG, rasterised in the palette colour (`glyph_icon`); action rows use 20 px icons at 60 % opacity, 100 % on hover; every action is an icon button with a tooltip — no verbs as words. The action timeline shows the launched app's own theme icon for `open_app` |
| Chat | a chat = a root task + follow-ups (`parent_id`). User message = pill right (r24, text @ 8 %, ≤ 70 % width) with edit / retry on hover; editing turns the pill into a card with Cancel (outlined) / Send (filled) pills. Agent messages = plain text left (no bubble, sparkle mark), Markdown via `QTextDocument.setMarkdown`, action row copy · good · bad · speak · edit · retry, Stop while running |
| Live action feed | one "Working / Worked: N actions" chip per turn; open by itself while the task runs. Each step is a timeline row: per-tool icon (app icon / keyboard / terminal / file / folder / globe / mail / bell / question / eye), present-tense title while it runs ("Opening Fab Files"), past tense when done, the agent's **narration** in italics underneath (`steps.narration` → `narration_done`), a spinner while the output is empty, an animated green check / amber cross / red cross (denied) when it completes; `type_text` is revealed with a typewriter effect (25 ms/char). Rows are appended and updated in place — never rebuilt; raw commands only when `ui.show_raw` is on |
| Provider settings | ONE dropdown (Anthropic (Claude) · Google Gemini · OpenAI · DeepSeek · Local model), one password key field ("stored" placeholder when a secret exists), model, endpoint only for Local, **Check connection** → `POST /providers/test` off the GUI thread; success = a circle that draws itself then a check mark (~520 ms, `ResultMark`) with "Connected · model · N ms", failure = shake on the field + "Key rejected" / "Cannot reach provider"; Save is disabled until the typed key passed a check or "Require a successful check" is unticked. While Save is blocked the reason is also written inline next to the buttons. Provider names appear only as these dropdown labels and in the API-key help text; the **Model** field and the daemon's speech calls carry the providers' own model identifiers (technical strings, not UI copy) — the only place such vendor wording is allowed. A Voice tab holds voice.enabled / wake word / speak replies / offline only / Test voice |
| Motion | typing indicator (three pulsing dots, 40 ms tick), fade-in (260 ms, OutCubic) for new agent text and new timeline rows (200 ms), switch knob 160 ms, check-mark draw 520 ms OutCubic, shake 420 ms, mic pulse ring 1.1 s loop while listening, toast fade 220 ms; nothing loops except the typing dots / spinners while a task runs and the mic ring while listening |
| Live data | `/tasks` every 4 s for the sidebar, `/tasks/{id}` every 1.5 s for the visible chat only (5 s HTTP timeout; the daemon is local); models are updated in place — sidebar rows are *reconciled* (a row already at its position is kept, a chat that moved up is re-inserted, vanished rows are dropped; the list is never cleared), bubbles by step id — with `setUpdatesEnabled` guards, and the view only auto-scrolls when the reader was already at the bottom |
| Editing | hover Edit puts a request back in the composer; sending marks the old task `(superseded) …` and creates the new one in the *same chat* (also for the chat's root, so follow-ups are kept and no look-alike chat appears). Superseded turns stay, dimmed to 45 %, the sidebar row says "edited", and the daemon leaves them out of the follow-up context |
| Safety | rounded, palette-following confirmations (Cancel + accent Confirm) for delete chat, stop task, Bypass mode, System-Wide AI off, reveal raw output, remove an API key; agent approvals surface as the same dialog (Allow / Deny) with the risk level and a friendly description. The exact tool input sits behind a "Show details" chip like every other raw output (`ui.show_raw` opens it) **except** for HIGH / CRITICAL risk, where it is open by default — nobody should approve a dangerous command unseen. A decision is posted only from the two buttons (Escape = Deny); when an approval is resolved from the notification or the CLI the dialog closes silently. Settings → Save with the daemon offline keeps the dialog open and says so |

Offscreen check: `tests/ai-controls-render.py` builds the window under a Fab Dark and a Fab Light palette against a
scripted daemon, writes `build/ai-controls-{dark,light}.png`, `build/ai-controls-empty-*.png`, `build/provider-{dark,light}.png`
(the AI-provider tab after a successful check), and exercises the behaviour above on the live window: a runtime palette
switch restyles every surface, sidebar rows keep their identity while chats appear / move / vanish, the live timeline
spins for the running step and shows checks + narration for finished ones, the provider check enables / blocks Save,
the approval dialog's details toggle and Deny, a stale approval closing without a POST, in-place editing of the root
message staying in its chat, `--task ID` opening on that chat, and an offline Save keeping Settings open.
