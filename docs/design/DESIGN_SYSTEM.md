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
| Layout | header row (title, enlarge, settings, System-Wide AI switch, report) · sidebar card 296 px (New chat, search, chats grouped Today / Yesterday / Earlier) · main card · composer pill. Spacing grid 12/16 px |
| Radii | controls 12 · fields 14 · cards and bubbles 20 · confirmation dialogs 20 · composer pill and settings popup 24 |
| Colour | the whole stylesheet is generated from the application `QPalette` (`build_style`): window / base / alternate-base surfaces, text, highlight = accent. Dark and light schemes both work, also when the system switches at runtime: Qt delivers `ApplicationPaletteChange` to the window's `event()` (never to `changeEvent()`), which re-runs `apply_style`, guarded by a palette signature so `setStyleSheet`'s own `PaletteChange` cannot loop. Only tiny semantic dots (risk LOW→CRITICAL, status) use fixed colours, always next to text |
| Type | Inter (14 px body, 16/600 titles, 12 px muted); JetBrains Mono for code blocks, on a tinted (`text` at 8 %) background |
| Icons | Material-style 24-grid glyphs as inline SVG, rasterised in the palette colour (`glyph_icon`); every action is an icon button with a tooltip — no verbs as words |
| Chat | a chat = a root task + follow-ups (`parent_id`). User bubble right / accent; agent bubbles left / surface, Markdown via `QTextDocument.setMarkdown`; tool steps folded into one "Worked: N actions" chip per turn, friendly labels only unless `ui.show_raw` is on |
| Motion | typing indicator (three pulsing dots, 40 ms tick), fade-in (260 ms, OutCubic) for new agent text, switch knob 160 ms; nothing loops except the typing dots while a task runs |
| Live data | `/tasks` every 4 s for the sidebar, `/tasks/{id}` every 1.5 s for the visible chat only (5 s HTTP timeout; the daemon is local); models are updated in place — sidebar rows are *reconciled* (a row already at its position is kept, a chat that moved up is re-inserted, vanished rows are dropped; the list is never cleared), bubbles by step id — with `setUpdatesEnabled` guards, and the view only auto-scrolls when the reader was already at the bottom |
| Editing | hover Edit puts a request back in the composer; sending marks the old task `(superseded) …` and creates the new one in the *same chat* (also for the chat's root, so follow-ups are kept and no look-alike chat appears). Superseded turns stay, dimmed to 45 %, the sidebar row says "edited", and the daemon leaves them out of the follow-up context |
| Safety | rounded, palette-following confirmations (Cancel + accent Confirm) for delete chat, stop task, Bypass mode, System-Wide AI off, reveal raw output, remove an API key; agent approvals surface as the same dialog (Allow / Deny) with the risk level and a friendly description. The exact tool input sits behind a "Show details" chip like every other raw output (`ui.show_raw` opens it) **except** for HIGH / CRITICAL risk, where it is open by default — nobody should approve a dangerous command unseen. A decision is posted only from the two buttons (Escape = Deny); when an approval is resolved from the notification or the CLI the dialog closes silently. Settings → Save with the daemon offline keeps the dialog open and says so |

Offscreen check: `tests/ai-controls-render.py` builds the window under a Fab Dark and a Fab Light palette against a
scripted daemon, writes `build/ai-controls-{dark,light}.png`, and exercises the behaviour above on the live window: a
runtime palette switch restyles every surface, sidebar rows keep their identity while chats appear / move / vanish, the
approval dialog's details toggle and Deny, a stale approval closing without a POST, root-message editing staying in its
chat, and an offline Save keeping Settings open.
