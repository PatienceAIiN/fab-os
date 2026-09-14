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

## Ask bar (home screen)

The bar is the agent's front door and answers **inline** — nothing else opens unless the user clicks
"Open in Fab AI Controls". Source: `packages/fabos-agent/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/`
(`main.qml` state machine + `agent.js` pure helpers, `ConvoDelegate.qml`, `AiMark.qml`, `IconButton.qml`, `Spinner.qml`,
`TypingDots.qml`). Layout follows the chat brief in `CHAT-UI-BRIEF.md` (composer card; user pill right, assistant plain
text with an action row) with Fab OS tokens.

- **Composer** (the card): radius 24, `Kirigami.Theme.backgroundColor` @ 92 % with a 12 % hairline. Left: the animated
  **Fab AI mark** (`AiMark.qml`, drawn in QML — orb, ring of three arcs, breathing glow; idle breathes over 4 s, working
  orbits at 1.2 s and pulses, listening pulses at 0.9 s with a red dot, done flashes one expanding ring, error / not
  configured tints amber via `neutralTextColor`; every loop stops after 30 s idle; static frame `brand/logo/fabos-ai-mark.svg`).
  Middle: the field (pill, `alternateBackgroundColor`, accent border on focus). Right: the round **mic** button (20 px
  glyph, 60 % → 100 % on hover; records only on click through `fabos-voice listen-once --timeout 10`; disabled with the
  tooltip "Voice is not available on this machine" when `fabos-voice status` reports no speech backend or the binary is
  missing; the transcript is typed into the field at ~25 ms/char and submitted) and the accent **Do it** pill (reads
  "Send" while a conversation is open).
- **Response panel**: a `PlasmaCore.Dialog` (type AppletPopup, FabOS Plasma dialog background, radius 24) anchored
  below the card (`visualParent` = card, location TopEdge) at the card's width. It grows downward — height animates
  from 0 to its content (280 ms OutCubic) while the content fades in; content growth animates at 260 ms, capped at
  62 % of the screen height. Header: status line left, icon controls right with tooltips (Stop · Retry · Edit prompt ·
  Copy result · Minimize · Open in Fab AI Controls = `fabos-command-center --task ID`). Minimize collapses it to a
  one-line status pill that reopens on click; Edit prompt puts the request back into the bar and closes the panel — the
  next Do it starts a fresh task even inside the 300 ms shrink (it cancels the close and re-opens the panel). Dismissing
  the panel forgets the task in the bar (the mark returns to idle; the task itself carries on in the daemon and the
  status line counts it).
- **Conversation** (`ConvoDelegate.qml`): user request as a right-aligned pill (radius 20, tinted, ≤ 72 % wide);
  assistant text plain (Inter 15/1.25) rendered from Markdown-lite (bold, italics, inline code, links, lists,
  headings) with fenced code as monospace cards (JetBrains Mono 13 on a text-colour @ 8 % tint, radius 12) and an
  action row (Copy · Try again · Open) under the final answer; new rows fade in and rise 12 px (260 ms). Typing
  indicator: three pulsing dots while the task is queued / running / waiting.
- **Live action feed**: one card per tool step (radius 14) appended the moment the daemon inserts the step row, with
  the app's own icon for `open_app` (scale-in), a keyboard glyph and a typewriter reveal (~25 ms/char) for `type_text`,
  terminal / file / globe / mail / bell / question / eye glyphs for the other tools; friendly labels only (raw commands
  and paths appear only when the daemon setting `ui.show_raw` is true); each step's `narration` (when the daemon
  provides one) in italics under the title; spinner while running, green check when done, amber cross on error / denied; the current step carries an
  accent border. When the task ends its steps fold once into a "Worked: N actions" chip that expands on click (a later
  poll or a later task finishing never re-folds a group the user opened). Approvals render inline with the risk level and Allow / Deny icon buttons
  (`POST /approvals/{id}`); questions render an inline answer field (`POST /tasks/{id}/answer`; the answer is shown at
  once as a user pill and bound to the daemon's `answer` step — whose text is in `input` — when the next poll returns it,
  so it is never duplicated; answers given elsewhere appear from that step).
- **Polling**: `GET /tasks/{id}` every 1.5 s through the executable DataSource (curl; port from
  `$XDG_RUNTIME_DIR/fabos-agent/port`; the bearer token is handed to curl as one config line on stdin — `printf … | curl
  -K -`, the shell's builtin printf — so it is never on a command line / in `/proc/*/cmdline`), only while the panel is
  open and the task is active, plus two trailing polls after it stops; rows are appended and updated in place, never
  rebuilt. `curl` is a declared dependency of `fabos-agent`. `GET /status` every 4 s drives the mark and the
  status line; `GET /settings` is read when the panel opens (`ui.show_raw`).
- **Follow-ups**: with the panel open, typing and pressing Send posts a new task with `parent_id` = the root task and
  appends it to the same conversation. Retry of a follow-up keeps the root; retry of the root re-roots the chat (the
  daemon copies `parent_id`, which is null for a root).
- **Layout**: the applet is a full-width transparent strip (150 px) placed by the look-and-feel layout script; the
  card centres on the physical screen width. In a panel (compact) the same code collapses to one row and the
  response panel opens from the panel edge.
- **Checks**: `node tests/askbar-js-test.js` (pure helpers) and `tests/askbar-qml-test.sh` (loads the applet headless
  with `plasmawindowed` inside the image, drives the state machine with daemon-shaped JSON, renders
  `build/askbar-{bar,panel,feed}.png`).
