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

## Windows (frame)

Source: `brand/gen/aurorae_theme.py` → `/usr/share/aurorae/themes/FabOS` (+ `FabOSLight`), rendered by KWin's Aurorae v2
engine (ADR-0012). Geometry at 1x: title bar 36, **radius 14 on the two top corners** (the same circle as the KWin corner
effect below, ADR-0019; it was 20 until 2026-09-15), square bottom corners in the frame itself, no side or
bottom borders (`BorderSize=None`), 28 px shadow padding (gradients plus one luminance `<mask>` per top corner; no SVG filters), 28 px buttons with 3 px rounded-cap strokes.
Colours: `ColorScheme-HeaderBackground` for the bar, `ColorScheme-Text` for glyphs, `ColorScheme-Highlight` on hover,
`ColorScheme-NegativeText` for the close hover — all rewritten from the active scheme by KSvg, so the same SVGs serve Fab
Dark and Fab Light; only the caption colour is per variant.

How the rounded corner actually appears (verified 2026-09-15 against `aurorae/v2/decoration.cpp` and by rendering the
frame through KSvg in the image, `tests/decoration-render-test.sh`): Aurorae paints the whole frame, offset by the
padding, into the decoration texture and cuts the shadow at the window's bounding box. The pixels between the arc and
the box corner (the *notch*) are therefore shown exactly as the SVG draws them. They must be **fully transparent**; the
first release put the corner shadow gradient there (19-38 % black) and every window looked square-cornered. Rules:

- Corner shadows are L-shaped paths that stop at the window box; nothing is drawn in the notch.
- Each top-corner shadow is multiplied by a luminance `<mask>` (radial: black at the box corner, white from 0.9 R outwards),
  so it fades out along the two notch edges instead of ending in a hard step (a faint square "ghost" corner) and is at full
  strength where the arc meets the straight edge. Measured through KSvg: 0.06 just above the notch vs 0.37 above the top
  edge. QtSvg renders `<mask>` since 6.7 (the image has 6.10); SVG filters are still not used. Mask ids are `taper*`/`tp*`,
  never `mask-*`.
- No `mask-*` elements: in Aurorae they only feed KWin's blur region (`setBlurRegion`), never the window shape, and blur
  behind an opaque title bar is wasted GPU work.
- No opaque overlays over the client area to fake a radius; the client's own top edge sits under the title bar.
- Buttons stay 3 px / rounded caps; hover discs radius 13 in a 28 px box; close hover uses the negative colour.
- After editing the generator: `python3 brand/gen/aurorae_theme.py --out packages/fabos-desktop/usr/share/aurorae/themes`,
  then `tests/decoration-render-test.sh` (notch alpha 0, header alpha 1, corner taper below 40 % of the edge shadow, full
  strength where the arc starts) and commit the SVGs.

## Windows (four rounded corners — the KWin effect)

A decoration can only shape what it draws: the title bar. The client's own bottom corners stayed square, and on the owner's
device the top ones read as sharp too. Since 2026-09-15 (ADR-0019) the **compositor** rounds every window: the
KDE-Rounded-Corners KWin effect (GPL-3.0 per its LICENSE, two source headers GPL-2.0-or-later; upstream id
`kwin4_effect_shapecorners`), compiled from its v0.10.0 release
inside the image against the exact KWin (`image/rounded-corners-build.sh` → package `fabos-rounded-corners`), enabled and
configured in `/etc/xdg/kwinrc` (`[Plugins] kwin4_effect_shapecornersEnabled=true`, group `[Round-Corners]`; every key is
from the effect's `src/kcm/options.kcfg`).

- **Radius 14, circular** (`Size=14`, `InactiveCornerRadius=14`, `UseSquircleShape=false`) — the "field" step of the radius
  scale (docs/design/BRANDING.md). The effect masks the whole frame, decoration included, so the visible corner is the
  intersection of its arc and the Aurorae arc: both must be the same circle, hence `R = 14` in `aurorae_theme.py` and no
  squircle. Plasma popups, panels, menus, tooltips and notifications are other window types and keep their radius 24.
- **Where it applies:** normal windows and dialogs (`IncludeNormalWindows`, `IncludeDialogs`), light and dark alike (the
  mask is geometry; colours are the window's own). **Square** when maximised, full-screen or snapped/tiled
  (`DisableRound{Maximize,FullScreen,Tile}=true`): those windows abut the screen edge or each other.
- **Outline:** one 1 px hairline in `QPalette::WindowText` at 22 % active / 14 % inactive (`ActiveOutlineUsePalette=true`,
  `ActiveOutlinePalette=0`, `ActiveOutlineAlpha=56`; inactive 36) — dark on Fab Light, light on Fab Dark. Second and outer
  outlines off.
- **Shadow:** the Aurorae shadow is kept (`UseNativeDecorationShadows=true`; the shader bends it around the arc).
  `ShadowSize=24` / `InactiveShadowSize=16` only apply if a user turns native shadows off in the effect's settings page.
- **Motion:** `AnimationDuration=160` (the effect's own active/inactive fade; it does not read `AnimationDurationFactor`).
- **Requirement:** OpenGL compositing (`Effect::supported()`); under KWin's QPainter fallback the frame's own radius-14
  top corners remain. The effect also writes `breezerc [Common] OutlineIntensity=OutlineOff, RoundedCorners=false,
  OutlineEnabled=false` while loaded (upstream behaviour for Breeze users; Fab OS uses Aurorae, so it is inert here).
- **Tests:** `tests/branding-check.sh` (plugin, KCM, shaders, package, kwinrc keys, radius 14 in the frame, no build tools
  left); `tests/corners-vm.sh` (in the VM over SSH: `loadedEffects` lists the effect; Fab Editor placed at a known geometry,
  `spectacle -b -n -f -o`, the four corner pixels vs 14 px inside, in Fab Dark and Fab Light).

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
| Muted / silent microphone | `mic` true with a non-empty `mic_reason`; `listen-once` exit 3 with "The microphone is muted or silent…" on stderr (the first second of the stream was exactly zero) | mic button enabled; the reason shown as the inline note under the composer / next to "microphone: yes" in Settings › Voice; never a bare "nothing heard" | none — no chime replay, no retry loop |
| No audio session | `mic` false, `tts` `none`, `mic_reason` / `tts_reason` = "No audio session: PipeWire is not running…"; `listen-once` and `say` exit 4 | mic and Speak disabled; the reason as the tooltip and inline note, with "Run `fabos-voice doctor`" as the action | none |
| Queued | another Fab process holds `speaking` (Speak button vs `fabos-voiced`) | the speaker glyph of the waiting message stays in its idle state until its turn | the new utterance starts only after the current one ends (machine-wide lock, at most 20 s wait); two utterances never overlap and no sentence of a task is spoken twice |

Rules shared by every voice surface: text explains every disabled control (`*_reason` from `fabos-voice status`, never
a generic "unavailable" when a reason exists); "Run `fabos-voice doctor`" is the single troubleshooting action offered
in copy; the wake listener is paused (fed nothing) while anything speaks, so no surface needs its own echo guard.

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
| Live action feed | one "Working / Worked: N actions" chip per turn; open by itself while the task runs. Each step is a timeline row: per-tool icon (app icon / keyboard / terminal / file / folder / globe / mail / bell / question / eye / picture for `generate_image`), present-tense title while it runs ("Opening Fab Files", "Creating an image"), past tense when done, the agent's **narration** in italics underneath (`steps.narration` → `narration_done`), a spinner while the output is empty, an animated green check / amber cross / red cross (denied) when it completes; `type_text` is revealed with a typewriter effect (25 ms/char). Rows are appended and updated in place — never rebuilt; raw commands only when `ui.show_raw` is on |
| Image cards | a finished `generate_image` step (output `{"path": "/home/<user>/Pictures/Fab OS/<name>.png", "width", "height", "provider", "prompt"}`) — or a `~/Pictures/Fab OS/*.png|jpg` path named in the final text (`~`, `$HOME`, `/home/<user>`, `file://` forms) — becomes an **image card** (`ImageCard`, `QFrame#imageCard`: radius 16, alternate-base surface, hairline; hover = accent border) for every file that **exists**, once per file however often it is mentioned; the thumbnail is the picture with radius-12 corners (`rounded_pixmap`), fitted to the agent's text width and never taller than 320 px, decoded at most 1024 px wide (`load_image`), fading in like a message; the card is exactly as wide as its thumbnail (+ 16 px padding, `set_max_width` caps `maximumWidth` at the scaled picture) so the caption = the prompt (13 px) and the small line "Provider · W × H" (11.5 px muted) sit under the picture's left edge, not beside empty card. Cards sit after the turn's messages, before its status row |
| Image viewer | a click on a card opens `ImageViewer` (`QDialog#imageViewer`, non-modal, **80 % of the screen**, one per card): a dark scrim (#0A0D14 — a photo viewer's scrim is dark in both schemes, every word on it white), prompt + "Provider · W × H · file" above, the picture fitted (re-scaled on resize, ≤ 4096 px decode), an outcome line, and the control row of radius-12 pills (white @ 10 %, 18 % on hover; one row, or two rows of three when the viewer is narrow): **Save as** (`QFileDialog` suggesting `~/Pictures/<name>`; if the dialog cannot be shown, a copy into `~/Pictures` — never overwriting — and the outcome line says where) · **Copy image** (`QClipboard.setImage`) · **Open in Fab Photos** (`gwenview`, else `xdg-open`, else disabled with "Fab Photos (gwenview) is not installed") · **Set as wallpaper** (`plasma-apply-wallpaperimage` through a `QProcess`; its exit code decides "Wallpaper set" / "Could not set the wallpaper"; disabled with the binary's name when absent) · **Regenerate** (POST `/tasks` `{"request": "regenerate the image with the same prompt", "parent_id": <chat root>}` — the daemon has the prompt in the thread — then the viewer closes) · **Close** (Esc). A disabled control is 40 % and its tooltip names what is missing; nothing fails silently. Regenerate is wired **once per card**: `ImageCard.regenerate_requested` is connected to the viewer when the viewer is created and the `Turn` listens to the card, so clicking the card again while its viewer is open only raises the viewer and ONE Regenerate click posts ONE follow-up (`tests/ai-controls-render.py` asserts this after three card clicks) |
| Composer state | **Send is disabled while the box is empty or whitespace** (the accent disc and its glyph at 40 % — its own style token `senddim`, so the disabled primary Save in Settings keeps its 35 % `hidim`; arrow cursor, no hover fill; tooltip "Type a request first"; Enter posts nothing) and live the moment there is text; the mic is independent of the text. While a task runs the same button is **Stop** and stays live |
| Cloud hint | with the built-in model (`/status` `provider == "local"`) a chip (`QFrame#cloudHint`: radius 12, alternate-base, hairline; info glyph in the accent) sits **above the composer**: "Using the built-in model. For the best results use a cloud model" · **Choose** (accent text button → Settings › AI provider) · dismiss (×). Dismissal lasts the session (`_cloud_hint_dismissed`, not a setting); the chip never shows with a cloud provider or while the service is offline |
| Settings (compact) | `SettingsDialog`, 620 px wide, radius 24, **four tabs whose first level holds only what most people touch**; every tab has a collapsed **Advanced** expander (`Disclosure`: a chevron row, no height animation, the dialog re-fits). **General**: Permission mode · System-Wide AI switch — Advanced: persona, raw responses, max steps, tool-result limit. **AI provider**: ONE dropdown (five providers), one password key field ("stored" placeholder when a secret exists), **Check connection** → `POST /providers/test` off the GUI thread; success = a circle that draws itself then a check mark (~520 ms, `ResultMark`) with "Connected · model · N ms", failure = shake on the field + "Key rejected" / "Cannot reach provider" — Advanced: model, endpoint (opened automatically for Local), "Require a successful check", the systemd-creds note. **Voice**: Listen for "Hey Fab" · Speak replies · **Voice check** (`fabos-voice doctor`, its lines in a monospace `#raw` box; an older CLI without `doctor` falls back to `status` and says so) · **Test voice** (`fabos-voice say --test`, plain `say` fallback) — Advanced: wake-word text, offline only, cloud voice name. **Mail**: provider dropdown (Gmail first), address, one **Sign in** — with Google OAuth available it runs the browser flow and shows the animated check ("Signed in with Google as …"); otherwise it reveals, in reading order, the app-password field, the provider's 3-step hint (plus the preset's note — Outlook: IMAP password sign-in is switched off) and then the **Check connection** row *under* the field it checks → `POST /mail/test` with the same check / shake pattern ("Signed in · SMTP ✓ · IMAP ✓ · N ms"; a sending-only account gets "Signed in · SMTP ✓ · IMAP off · N ms" with the reason on a second line and Save enabled; "Wrong password — Gmail needs an app password…" / "…closed the connection at sign-in…" in red shakes the password field; "Cannot reach host:port" in amber shakes the address). Once a check passed, the 3-step hint is hidden (it returns when the form changes). The "no Google sign-in on this build" reason is one short line in the result column; the daemon's full sentence is its tooltip. Advanced: sender name, SMTP / IMAP servers auto-filled by the preset and stored only when they differ (`none` in IMAP server = sending only). Save is disabled until a typed key or changed mail account passed its check (unless the requirement is unticked); the reason is written inline next to the buttons. The dialog is as tall as the **current** tab (`TabPage` reports no size while hidden; `RoundedDialog.refit()` invalidates + activates the layout before `adjustSize`, then grows to the layout's height-for-width so nothing is clipped), and every wrapped hint / result label is a `WrapLabel` — a word-wrapped QLabel whose sizeHint is its height at the width it actually has, because `QFormLayout` (Qt 6) reserves a wrapped field's guessed-width sizeHint height, never its height-for-width at the real column width (measured: 176 px reserved for 112 px of text; a squeezed result label clipped). Measured offscreen at Medium font: General 307 · AI provider 380 · Voice 326 · Mail 357 px collapsed; Mail states — app-password path 470 · check passed 415 · wrong password 470 · Outlook hint + note 533 · Outlook sending-only 463; budget ≤ 560, no clipped rows (`tests/ai-controls-render.py --settings` asserts both). Provider names appear only as dropdown labels and in the API-key help text; the **Model** field and the daemon's speech calls carry the providers' own model identifiers (technical strings, not UI copy) — the only place such vendor wording is allowed |
| Motion | typing indicator (three pulsing dots, 40 ms tick), fade-in (260 ms, OutCubic) for new agent text and new timeline rows (200 ms), switch knob 160 ms, check-mark draw 520 ms OutCubic, shake 420 ms, mic pulse ring 1.1 s loop while listening, toast fade 220 ms; nothing loops except the typing dots / spinners while a task runs and the mic ring while listening |
| Live data | `/tasks` every 4 s for the sidebar, `/tasks/{id}` every 1.5 s for the visible chat only (5 s HTTP timeout; the daemon is local); models are updated in place — sidebar rows are *reconciled* (a row already at its position is kept, a chat that moved up is re-inserted, vanished rows are dropped; the list is never cleared), bubbles by step id — with `setUpdatesEnabled` guards, and the view only auto-scrolls when the reader was already at the bottom |
| Editing | hover Edit puts a request back in the composer; sending marks the old task `(superseded) …` and creates the new one in the *same chat* (also for the chat's root, so follow-ups are kept and no look-alike chat appears). Superseded turns stay, dimmed to 45 %, the sidebar row says "edited", and the daemon leaves them out of the follow-up context |
| Safety | rounded, palette-following confirmations (Cancel + accent Confirm) for delete chat, stop task, Bypass mode, System-Wide AI off, reveal raw output, remove an API key; agent approvals surface as the same dialog (Allow / Deny) with the risk level and a friendly description. The exact tool input sits behind a "Show details" chip like every other raw output (`ui.show_raw` opens it) **except** for HIGH / CRITICAL risk, where it is open by default — nobody should approve a dangerous command unseen. A decision is posted only from the two buttons (Escape = Deny); when an approval is resolved from the notification or the CLI the dialog closes silently. Settings → Save with the daemon offline keeps the dialog open and says so |

Offscreen check: `tests/ai-controls-render.py --settings --welcome` (fast) renders every Settings tab in dark and light
(`build/settings-{general,provider,voice,mail}-*.png`, `settings-mail-{ok,fail,outlook}-*.png`, `settings-voice-*.png`, plus the welcome
wizard's Mail page `welcome-mail-*.png`), prints the per-tab heights against the 560 px budget, and drives the Mail sign-in →
app-password → Check connection → Save gating, the Voice check box (fake `fabos-voice` with and without `doctor`) and the
composer mic toast reasons (exit 3 / 4 / missing binary). The full run, `tests/ai-controls-render.py`, builds the window under a Fab Dark and a Fab Light palette against a
scripted daemon, writes `build/ai-controls-{dark,light}.png`, `build/ai-controls-empty-*.png`, `build/provider-{dark,light}.png`
(the AI-provider tab after a successful check), and exercises the behaviour above on the live window: a runtime palette
switch restyles every surface, sidebar rows keep their identity while chats appear / move / vanish, the live timeline
spins for the running step and shows checks + narration for finished ones, the provider check enables / blocks Save,
the approval dialog's details toggle and Deny, a stale approval closing without a POST, in-place editing of the root
message staying in its chat, `--task ID` opening on that chat, and an offline Save keeping Settings open. It also seeds a
`generate_image` step naming a **real PNG** (written by `tests/askbar-qml-harness/mkpng.py` — standard library only; the
image has no python3-pil), a second step naming a file never written, and a final text naming the first file again, and
asserts: exactly one image card (caption = prompt, "Test provider · 640 × 400", thumbnail ≤ 320 px with the aspect ratio
kept), the viewer at 80 % of the screen with all six controls, Copy image putting a 640 × 400 picture on the clipboard,
Save as copying through a patched `QFileDialog` and falling back to `~/Pictures` when the dialog raises, Open launching
`gwenview` / `xdg-open` with the path (patched `Popen`), Set as wallpaper running `plasma-apply-wallpaperimage` for real and
reporting its outcome, Regenerate posting `{"request": "regenerate the image with the same prompt", "parent_id": <root>}`
and closing the viewer (renders `build/ai-controls-image-{card,viewer}.png`, `-light` variants); the composer's Send
disabled on an empty / whitespace box with Enter posting nothing and live with text; the cloud hint chip only for
`provider == "local"`, Choose → `open_settings("provider")`, dismissal remembered, never for another provider
(`build/ai-controls-cloud-hint-{dark,light}.png`).

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
  glyph, 60 % → 100 % on hover; records only on click through `fabos-voice listen-once --timeout 10`; dimmed — never
  dead — with a tooltip naming the reason when `fabos-voice status` reports no microphone or no speech backend or the
  binary is missing; that status is cached for **30 s only** (a microphone can be plugged in later), so hovering the
  mic or focusing the field asks again; a tap always tries. While recording the status line under the field shows a
  red dot + "Listening…" and the mark pulses red. When the CLI exits non-zero — or prints nothing — a reason is shown
  in the status line for 6 s: for exit 4 (no microphone / no speech backend) and any other failure it is the CLI's own
  last stderr line ("No microphone found on this computer.", "Speech recognition is not available: no offline model
  and no cloud provider key."); exit 3 (nothing heard) and exit 127 (binary missing, the shell's code) get a fixed
  sentence from the bar ("I did not catch that. Tap the mic and try again.", "Voice is not installed on this machine
  (fabos-voice is missing)"). A failed tap is never a silent no-op — in the compact (panel) form, where the status
  line is hidden, the same reason goes into the field's placeholder and the card's hover tooltip. The transcript is
  typed into the field at ~25 ms/char and submitted) and the accent **Do it** pill (reads "Send" while a
  conversation is open). **Do it / Send is disabled while the field is empty or whitespace**: 40 % opacity, no hover
  lift or tint, arrow cursor, a click does nothing and Enter is ignored (`submit()` trims first); a tooltip says "Type
  or speak a request first"; it is live the moment there is text. The mic beside it is never affected. The card is
  6.2 grid units tall, or taller when its rows need it (`cardCol.implicitHeight` + margins): under the field and the
  status line a **cloud hint chip** (radius 12, `alternateBackgroundColor`, hairline, info glyph in the accent) appears
  while `/status` reports `provider == "local"` — "Using the built-in model. For the best results use a cloud model" ·
  **Choose** (accent text → `fabos-command-center --settings provider`) · × dismiss. The dismissal lasts the session (a
  plain property, not `Plasmoid.configuration`); the chip never shows with a cloud provider or while unconfigured. It is
  ONE component (`CloudHintChip.qml`) in two places: under the field inside the card on the desktop, and — because the
  36 px compact card has no room for it — as the **first row of the popup's header** in the compact (panel) form
  (`compactHint`, hidden and 0 px tall on the desktop; the popup's `wanted` height includes it), so the hint shows
  wherever `/status` reports `provider == "local"`.
- **Response panel**: an `Item` **inside the applet** — there is no `PlasmaCore.Dialog` (no separate popup window) on
  the desktop. It sits flush under the card: `y = card.y + card.height + 8`, the card's x and width, radius 24 like
  the card so the pair reads as one stack, `Kirigami.Theme.backgroundColor` @ 96 % with the 12 % hairline. Because
  the applet lives in the **desktop layer**, the panel is always behind every application window (the editor the
  agent opens covers it, it never paints over another app) and is back the moment those windows are minimised or
  closed. It grows downward — height animates from 0 to its content (220 ms OutCubic) while the content fades in.
  **Growth and scrolling**: below its maximum (the strip's remaining height) the panel grows with its rows (200 ms
  OutCubic) and shows **no scrollbar**; at the maximum the list scrolls inside and a **6 px overlay scrollbar**
  appears. The bar (`QtQuick.Templates` ScrollBar on the ListView, own rounded handle, text colour @ 28 % → 50 % on
  hover) lives in a **14 px right gutter**: every row is `list.width − 14` wide, so no text, pill, icon button or step
  card is ever under it. The transition from growing to scrolling never jumps — while the user is at the bottom the
  newest row stays anchored to the bottom edge in every frame of the height animation (`positionViewAtEnd` on the
  list's height change), and once at the maximum the view auto-follows new rows **only while the user was at the
  bottom** (a drag, flick, wheel or handle drag upward stops following until they are back at the end). Wheel and touch
  are `StopAtBounds` with a moderate `maximumFlickVelocity` (2000). Three fixed bands: the **header** (status line
  left, icon controls right with tooltips: Stop · Retry · Edit prompt · Copy result · Minimize · Open in Fab AI
  Controls = `fabos-command-center --task ID`) sits above the scroll area and never scrolls or meets the bar; the
  **list** is the only scroll area; the **foot** (typing dots while the task works, 26 px, animates away when it ends)
  is fixed under the list, clear of the gutter. Minimize collapses it to a one-line status pill under
  the card (still inside the applet) that reopens on click; **Escape** in the bar folds it, a **click on the mark**
  folds and unfolds it. Edit prompt puts the request back into the bar and closes the panel — the next Do it starts a fresh
  task even inside the 300 ms shrink (it cancels the close and re-opens the panel). Dismissing the panel forgets the
  task in the bar (the mark returns to idle; the task itself carries on in the daemon and the status line counts it).
  Only when the plasmoid sits in a **panel** (compact form) is a `PlasmaCore.Dialog` created — a popup is the only
  place the conversation can go there — and the same conversation item moves into it.
- **Conversation** (`ConvoDelegate.qml`): user request as a right-aligned pill (radius 20, tinted, ≤ 72 % wide);
  assistant text plain (Inter 15/1.25) rendered from Markdown-lite (bold, italics, inline code, links, lists,
  headings), wrapping inside the row width (list width minus the 14 px gutter); fenced code as monospace cards
  (JetBrains Mono 13 on a text-colour @ 8 % tint, radius 12) that **keep their lines**: a long line scrolls sideways
  inside its own card (horizontal Flickable, `StopAtBounds`, a 6 px overlay bar under the last line that appears only
  when the code is wider than the card) and never widens the row or the list; a card that fits is not interactive, so
  the wheel over it still scrolls the chat; an action row (Copy · Try again · Open) under the final answer; new rows
  fade in and rise 12 px (200 ms). Typing indicator: three pulsing dots in the fixed foot while the task is queued /
  running / waiting.
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
- **Image cards** (`ConvoDelegate` kind `image`): a finished `generate_image` step — output `{"path": "/home/<user>/Pictures/Fab
  OS/<name>.png", "width", "height", "provider", "prompt"}`, shown in the feed as "Creating / Created an image" with the
  prompt and a picture glyph — or a `~/Pictures/Fab OS/*.png|jpg` path named in the final text (`~`, `$HOME`,
  `/home/<user>`, `file://` forms; `Agent.imagePathsInText`) **offers** an image; the bar asks the shell whether the file
  is there (`Agent.imageCheckCommand`: `[ -f ]` with `~`/`$HOME` expanded by the shell, printing the absolute path) and
  only then appends **one card per file**, however often and in whatever form it was mentioned. The card: radius 16,
  `alternateBackgroundColor`, hairline (accent on hover), the picture fitted to the row's width and never taller than
  320 px with its own radius-12 corners — painted once by a `Canvas` (QPainter, clipped to a rounded rectangle; a
  hidden `Image` decodes the file and its implicit size drives the box — deterministic in every scene-graph backend,
  where a ShaderEffectSource into `Kirigami.ShadowedTexture` came out upside-down and a MultiEffect mask blank in the
  headless image) — fading in when painted, a hover badge "enlarge", caption = the prompt (13 px), a small "Built-in model ·
  W × H" line (11 px; a cloud provider by the id the daemon reports — no vendor names live in the bar). A file that
  vanished later shows a muted "This image is no longer at …" line. The card is a tap target.
- **Image viewer** (`ImageViewer.qml` in a `PlasmaCore.Dialog`, `viewerLoader` in main.qml — the only window the desktop
  form ever opens, and only on a tap): **80 % of the screen**, frameless, a dark scrim (radius 24 — a photo viewer's
  scrim is dark in both schemes; every word on it is white), the prompt and "Built-in model · W × H · file" above, the
  picture fitted (decoded at the file's size — a `sourceSize` cap would scale a small file UP), an outcome line, and one
  control row of radius-12 pills (white @ 10 %, 18 % on hover; `Flow`, so a narrow screen wraps): **Save as**
  (`QtQuick.Dialogs` FileDialog loaded through a `Loader` from `SaveDialog.qml` — when the module is missing, a copy
  into `~/Pictures` that never overwrites, and the outcome line says where; the same fallback command is
  `Agent.saveCopyCommand`) · **Copy image** (`wl-copy --type image/png|jpeg < file`; disabled with "Copying needs
  wl-clipboard (wl-copy), which is not installed" when the probe finds none) · **Open in Fab Photos** (`gwenview`, else
  `xdg-open`, else disabled with "Fab Photos (gwenview) is not installed") · **Set as wallpaper**
  (`plasma-apply-wallpaperimage file`; disabled with the binary's name when absent) · **Regenerate** (a follow-up in the
  conversation: `POST /tasks {"request": "regenerate the image with the same prompt", "parent_id": root}`; while the
  previous POST is still in flight the viewer closes and the status line says "Still sending your last request — try
  Regenerate again in a moment" — never a silent no-op — the daemon
  has the prompt in the thread — then the viewer closes and the request row appears like any submit) · **Close**
  (Esc, the × in the header, or a click on the scrim). The binaries are probed once per opening with one `sh`
  (`Agent.binsCommand`: `command -v` for wl-copy, gwenview, xdg-open, plasma-apply-wallpaperimage); until it answers the
  dependent controls read "Checking what this machine can do…". Every control that depends on a binary degrades to a
  disabled 40 % pill with the reason in its tooltip — nothing fails silently.
- **Polling**: one curl snapshot (`/status` + `/tasks/{id}`) every 2 s while a task is followed, 8 s while awake and idle, a 60 s heartbeat while asleep, nothing while closed — through the executable DataSource (curl; port from
  `$XDG_RUNTIME_DIR/fabos-agent/port`; the bearer token is handed to curl as one config line on stdin — `printf … | curl
  -K -`, the shell's builtin printf — so it is never on a command line / in `/proc/*/cmdline`), only while the panel is
  open and the task is active, plus two trailing polls after it stops; rows are appended and updated in place, never
  rebuilt. `curl` is a declared dependency of `fabos-agent`. the same snapshot drives the mark and the
  status line; `GET /settings` is read when the panel opens (`ui.show_raw`).
- **Follow-ups**: with the panel open, typing and pressing Send posts a new task with `parent_id` = the root task and
  appends it to the same conversation. Retry of a follow-up keeps the root; retry of the root re-roots the chat (the
  daemon copies `parent_id`, which is null for a root).
- **Layout**: the applet is a tall, full-width transparent strip placed by the look-and-feel layout script from 24 %
  of the screen height down to the dock (`sh * 0.66` tall); the card sits at the strip's top and centres on the
  physical screen width, the panel unfolds in the space below it. `Plasmoid.backgroundHints` is NoBackground and the
  transparent rest of the strip has **no pointer handler**. plasmashell decides whose context menu a click gets by
  asking the applet item `contains(point)` — by default its whole bounding box, i.e. the entire strip — so the root
  carries a **containment hit mask** (`containmentMask`, an invisible Item spanning the card + panel stack, the
  card alone when the panel is folded): `contains()` is false on the transparent rest, and a right-click there is the
  desktop's own menu, not the applet's. (`containmentMask` is a revisioned QQuickItem property the
  `org.kde.plasma.plasmoid` module does not expose declaratively, so main.qml assigns it from JavaScript on load and
  whenever the form changes.) The open conversation is remembered in `Plasmoid.configuration` (`rootTaskId`,
  `taskId`; cleared on dismiss) and, after a desktop re-layout, the applet asks `GET /tasks/{id}` on load and reopens
  the panel while the task is still active; if the agent service is not up yet (plasmashell usually starts first at
  login) the ids are kept and the request is repeated when `GET /status` first answers — only a daemon answer
  (404 `{error}` or a finished task) forgets them. In a panel (compact) the same code collapses to one row and the
  response panel opens as a popup from the panel edge.
- **Checks**: `node tests/askbar-js-test.js` (pure helpers, incl. the voice-failure messages) and
  `tests/askbar-qml-test.sh` (loads the applet headless with `plasmawindowed` inside the image, drives the state
  machine with daemon-shaped JSON, asserts the panel's geometry under the card, its growth and fold animation, that no
  `PlasmaCore.Dialog` exists on the desktop, that no MouseArea lies outside card/panel, that `root.contains()` is
  false on the transparent strip and true over the card + panel stack (the hit mask), runs a real
  `fabos-voice listen-once` for the mic feedback, exercises the remembered conversation (incl. the service-not-up
  retry), feeds a 40-row conversation with long lines and one-line code and asserts the scroll geometry (the panel
  grows to its maximum with the bottom anchored and no bar below it; at the maximum the 6 px bar's x ≥ the rows' right
  edge inside the 14 px gutter, no live delegate wider than `list.width − gutter` or under the bar, the header row at
  y = 0 after following to the end, the fixed foot under the list, long text wrapped inside the row, code overflowing
  sideways inside its card, a new row not yanking a view the user scrolled up, following again once back at the end),
  shrinks the window to a panel thickness so the compact form runs for real (Dialog appears, the mic reason moves into
  the placeholder, the cloud hint becomes the popup's first header row above `panelHeader` with the popup grown to hold
  it, its Choose and × work there, and no chip anywhere with a cloud provider; renders `build/askbar-compact-hint.png`), and renders `build/askbar-{bar,panel,feed,scroll-bottom,scroll-top}.png` — bar = the whole strip
  with the card + panel stack, scroll-bottom/top = the 40-row panel at its maximum, followed to the end and scrolled
  to the top). Its polish stages then assert the disabled Do it (empty and whitespace field: `canSend` false, no hover,
  arrow cursor, `submit()` posts nothing, opacity 0.40 after the animation, the mic still `active`; live at 1.0 with
  text), the cloud hint chip (shown for `provider == "local"` under the field inside the card with the panel still 8 px
  under the card, Choose → `--settings provider`, dismissed and remembered across the next `/status`, never for a cloud
  provider), and the image cards for real: `mkpng.py` writes a 640 × 400 PNG inside the image (HOME=/tmp) before
  plasmawindowed starts; a task with two `generate_image` steps (the real file via `~/…`, a file never written) and a
  final text naming both (`$HOME/…`, `~/…`) yields four `[ -f ]` checks and **exactly one** card (absolute path,
  caption = prompt, "640 × 400"), the Canvas thumbnail ≤ 320 px with the aspect ratio kept and painted, and
  `pngcheck.py` samples the render itself — blue sky at the top of the thumbnail, green hill at the bottom — so the
  picture is proven painted and upright, not eyeballed; a tap opens the viewer (80 % of the screen, path / prompt /
  provider carried), the binary probe answers (gwenview and plasma-apply-wallpaperimage present → enabled; no wl-copy in
  the image → Copy image disabled at 40 % with the wl-clipboard reason), the FileDialog module loads, the viewer holds
  focus, the Save as fallback really copies into `~/Pictures` and says where, Regenerate closes the viewer and posts the
  follow-up under the root task (a second Regenerate while that POST is in flight closes the viewer, posts nothing and
  puts the reason in the status line), Close / Esc closes it; renders `build/askbar-image-{card,viewer}.png`.

## Top bar & dock

Three Fab OS plasmoids in `packages/fabos-desktop/usr/share/plasma/plasmoids/` — quick settings, clock, dock — replace
the stock status icons, the stock digital clock and the icons-only task manager; the look-and-feel layout
(`…/contents/layouts/org.kde.plasma.desktop-layout.js`) places them. All follow the system colour scheme through
`Kirigami.Theme` (no literal colours), use Inter, the FabOS monochrome status glyphs (`brand/gen/make_assets.py`
MONO_MAP, recoloured by the icon loader) and the motion tokens (160–320 ms OutCubic; every animation is a `Behavior` or
a bounded run that idles at rest — no timers driving frames, no shaders).

### One size for everything in the bar

`barSize` (small / medium / large) is **one setting owned by the quick-settings applet** ("Bar" page): glyphs 16 / 18 /
22 px, indicator text 11 / 12 / 13 px, battery percentage and the clock 12 / 13 / 15 px (Inter 700). The layout writes
the same value into both applets at first login (`quick.writeConfig("barSize", …)`, `clock.writeConfig("barSize", …)`).
Because one applet cannot write another's `Plasmoid.configuration` from QML, a later change runs plasmashell's own
scripting API over D-Bus — `qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '…'`
(`status.js: syncScript`: `panels()[i].widgets("in.patienceai.fabos.clock")[j].writeConfig("barSize", …)`, plus the
dock's `magnify`) — the mechanism actually implemented; there is no shared file. The clock's own page only reports the
size and points to the quick-settings page. (An `evaluateScript` write lands in the applet's config and its
`Plasmoid.configuration` binding updates live; the harness asserts the queued command, the VM run the effect.)

### Clock (`in.patienceai.fabos.clock`, top bar, centre)

One `Text`: `Qt.formatDate(now, "ddd d MMM") + " · " + time` → "Mon 15 Sep · 10:47", Inter 700 at the bar size's px,
`Text.NativeRendering`. The time is the region's short format **without seconds** (the C locale's short format carries
`:ss`; `autoTimeFormat` strips it), or 24 h / 12 h from the clock's page; the date format is a Qt format string
(default `ddd d MMM`, off = time only). It ticks once per minute *on the minute* (the timer is re-armed to the next
boundary; no 1 Hz work). Click opens a month view (`org.kde.plasma.workspace.calendar` MonthView, the stock clock's own
component) in a 22 × 21 gridUnit dialog; the tooltip is the long date and time.

### Quick settings (`in.patienceai.fabos.quicksettings`, top bar, right corner)

- **Indicator group** (one applet, one hit area): network glyph by signal level, the live rate beside it
  (`↓ 1.2 MB/s  ↑ 80 kB/s`; **always shown while a link is up, `0 kB/s` when idle, hidden only without a link** — kB/s
  is the floor unit, never `B/s` jitter), Bluetooth glyph while the adapter is on, volume glyph while muted or for 3 s
  after a change (wheel over it changes the volume), battery glyph **with the percentage in the clock's size**, and the
  bell with an unread badge (crossed while Do Not Disturb). Hovering an indicator scales its **glyph** to 1.25 (160 ms
  OutCubic) when "Magnify on hover" is on — the text and the indicator's layout width stay put, so the bar never
  re-flows. Tooltip: network · battery · volume lines.
- **Accurate Wi-Fi**: the glyph follows NetworkManager's own reading, not the kernel's link quality:
  `nmcli -t -f ACTIVE,SIGNAL dev wifi list --rescan no` (the `yes:` line; 14–17 ms measured on the dev host, so it
  runs with every 5 s light probe while a wireless interface is up — `--rescan no`, never a scan) and
  `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status` for the connection type. Signal 0–100 → five glyphs:
  `excellent ≥ 80`, `good ≥ 55`, `ok ≥ 30`, `weak ≥ 5`, else `none` (`-locked` with security); `network-wired` when
  only Ethernet is connected; `network-wireless-off` when every Wi-Fi device reads `unavailable` (radio off);
  `network-wireless-connected` while associated but not yet read. Recorded nmcli output drives
  `tests/quicksettings-js-test.js`. `/proc/net/wireless`, where a driver exposes it, is only the fallback for a script
  older than the applet.
- **No-blink slide-down**: the `PlasmaCore.Dialog` (type AppletPopup) is **transparent** (`backgroundHints:
  NoBackground`) and **opens at its final size at once** — `mainItem` is bound to the card's size, which is arithmetic
  (tile layout height + header + footer; rows × 56 for the history) and does not change while anything moves. Only the
  **card** inside animates: `y` from −height to 0 and opacity 0 → 1 in 220 ms OutCubic; closing reverses and hides the
  window when the animation has ended (a window whose size changes per frame is re-rasterised by the compositor every
  frame — the "blink" the old height animation caused). The card is drawn by the applet: `Kirigami.Theme.backgroundColor`
  @ 96 %, a 1 px hairline (text colour @ 12 %), radius 24 at the bottom corners (square at the top, against the bar), and
  a soft shadow from a second translucent rectangle (black @ 16 %, +3 px, radius 26). Switching settings ↔ notifications
  while open is one instant resize plus a 160 ms cross-fade; the content the card holds is kept while it closes, so
  the window keeps one size from the first frame of the slide to the last. Proof: the harness samples
  `Dialog.width/height` and `card.y` every 11 ms while opening and closing (24 samples each) and asserts one width, one
  height, and a monotonic `y` through ≥ 8 distinct values. Click-away closes it (`hideOnWindowDeactivate`).
- **Tiles you arrange** (`Plasmoid.configuration.tilesJson`, a JSON array of `{id, size, enabled}` in display order;
  `status.js TILES / parseTiles / layoutTiles`): Wi-Fi, Bluetooth, Volume, Brightness, Battery, Network speed,
  Notifications, Do Not Disturb, Power profile, Night light, Screenshot, Settings. `size` is **small** (half a row) or
  **wide** (a full row); a two-column layout, small tiles pair up left to right, a wide tile takes its own row, the row
  height is the tallest tile in it (`layoutTiles`, pure, unit-tested for geometry and non-overlap). Tiles that need
  hardware or a daemon hide while it is absent (brightness without a backlight, battery without one, power profile
  without power-profiles-daemon, night light unless KWin offers it) — edit mode shows them dimmed so they can still be
  arranged. The **pencil** in the header toggles edit mode: every tile gets an accent frame, a grab handle, a size
  toggle and a remove cross; its own controls go inert; a `DragHandler` moves it and `tileAt` (the slot under the tile's
  centre, or the nearest within a tile height) reorders the model live so the others glide (160 ms) into their new
  slots; the order is persisted at release. Removed tiles come back as `+ name` chips under the grid; a reset arrow
  restores the shipped layout. The **Tiles** settings page edits the same model (checkbox, Small / Wide, up / down,
  Reset to default); the **Bar** page holds the bar size, magnify, "show speed" and the probe cadence.
  - Tile contents: Wi-Fi (on/off + SSID · signal %, chevron → stock network applet), Bluetooth (on/off + connected
    count, chevron), Volume (mute · slider · % · chevron; the chevron hides when small), Brightness (powerdevil's
    `org.kde.ScreenBrightness` through `BrightnessBridge.qml`; disabled with a tooltip when the service is absent),
    Battery (percentage · time, power-profile chips when wide, chevron), Network speed (interface · IP · ↓ ↑),
    Notifications (count → the history pane), Do Not Disturb (toggle), Power profile (click cycles power-saver →
    balanced → performance; chevron → stock battery applet), Night light (toggles `kwinrc NightColor/Active` +
    `qdbus6 org.kde.KWin /KWin reconfigure` — KWin's `inhibit()` is tied to the caller's bus connection and would end
    with the one-shot process; chevron → `kcmshell6 kcm_nightlight`), Screenshot (`spectacle`), Settings
    (`systemsettings`). Footer: System Settings, "Bar: Medium" and a chevron to the applet's pages.
- **Notifications pane** (the bell): **28 gridUnits wide**, header (count, Do Not Disturb, a labelled **Clear all**
  pill, settings → `kcmshell6 kcm_notifications`), a banner while Do Not Disturb holds popups, the history as rows of
  **56 px minimum**: 32 px app icon, 13 px summary and body (two lines), app · time ago; click runs the default action;
  jobs show their percentage. The list grows with its content up to **60 % of the screen**, then scrolls (a vertical
  scroll bar shows when it overflows). The dismiss cross stays reachable: the row's hover area spans the whole row and
  the cross is shown while *either* the row or the cross itself is hovered (`SmallButton.hovered`), because a hovered
  `MouseArea` takes the hover from the row area beneath it; the kwin_wayland harness clicks it with a real pointer.
  Opening the pane marks everything read.
- **Data**: `contents/code/status.sh` (one JSON line) through the Plasma5Support executable engine — the `--light`
  probe (kernel counters, battery, backlight + the one nmcli ACTIVE,SIGNAL call while a wireless link is up) every 5 s
  while closed, the full probe (nmcli dev status, BlueZ over busctl, wpctl, power-profiles-daemon, KWin night light)
  every 30 s while closed, on a link change, every 2 s while the pane is open and 400 ms after each action. Parsing is
  `contents/ui/status.js` (pure; `node tests/quicksettings-js-test.js`).
- **Actions**: `nmcli radio wifi on|off`, `bluetoothctl power on|off`, `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.NN`
  (slider drags coalesced to one call per 120 ms), `wpctl set-mute … toggle`, `powerprofilesctl set …`, brightness
  through powerdevil's D-Bus service, night light through `kwriteconfig6`. Notifications and Do Not Disturb use
  `org.kde.notificationmanager` — the same model and `Settings` the stock history uses, in the same plasmashell
  process; Do Not Disturb writes `notificationsInhibitedUntil` (one year = "until turned off", as the stock applet
  does).
- **No duplicate tray icons — the real mechanism**: Plasma 6.6's system tray is its own containment and its settings
  live under the applet in `plasma-org.kde.plasma.desktop-appletsrc`
  (`[Containments][panel][Applets][tray][General]`). At start its PlasmoidRegistry adds every `EnabledByDefault`
  status applet it does not yet **know** (`knownItems`) to `extraItems`, and `extraItems` is the list it *loads*.
  `hiddenItems` only folds a loaded applet into the tray's popup — and Plasma re-shows an "active" one on the bar,
  which is why the stock network / Bluetooth / volume / battery icons still appeared beside ours. `layout.js
  configureTray()` therefore writes, on the tray widget returned by `top.addWidget("org.kde.plasma.systemtray")`
  with `currentConfigGroup = ["General"]`: `knownItems` = everything the registry knew ∪ the five replaced ids, and
  `extraItems` = that list **without** `org.kde.plasma.networkmanagement`, `bluetooth`, `volume`, `battery`,
  `brightness` (a recorded default list is the fallback when the registry has not filled `extraItems` yet);
  `org.kde.plasma.notifications` stays loaded (it is the notification server that draws popups) but in `hiddenItems`.
  Volume keys and their OSD live in plasma-pa's kded module, brightness keys in powerdevil — not in the removed
  applets. Proven, not assumed: `tests/desktop-applets-qml-test.sh tray` runs a **real plasmashell** (as a virtual
  kwin_wayland session with an empty HOME, the layout bind-mounted over the package) and asserts the appletsrc it
  writes: `extraItems` without the five, `knownItems` with them, no `plugin=org.kde.plasma.{networkmanagement,…}`
  applet instantiated, the Fab OS clock present and `org.kde.plasma.digitalclock` absent. The chevrons still open the
  stock applets in a window (`plasmawindowed <id>`).
- **One owner per shared setting**: the quick settings own the bar size and the "Magnify on hover" switch (pushed to
  the clock / dock as above); the dock owns its magnification strength (Subtle / Normal / Strong) and, when the switch
  is flipped on the dock's page, writes it back through the same scripting call (`dock main.qml: syncScript`). A write
  of an unchanged value emits no change on either side, so there is no ping-pong.

### Dock (`in.patienceai.fabos.dock`, bottom floating panel)

- **One applet, one size**: the row is `start button · tasks · peek` (`ExtraItem.qml` for the two ends, `TaskItem.qml`
  for the tasks) — no separate kickoff / showdesktop applets, whose icons filled the panel (64 px) or sat at a fixed
  32 px beside 40 px tasks (the owner's "Fab icon very big, others smaller"). The stock launcher applet stays in the
  panel **zero-width** (`icon=` and `menuLabel=` empty: plasma-desktop 6.6's compact representation then has no size),
  so the Meta key and `plasmashell activateLauncherMenu` still open it at the dock's left end; the start button calls
  `org.kde.PlasmaShell.activateLauncherMenu` over D-Bus, peek invokes KWin's *Show Desktop* shortcut.
- **Uniform visible extent** (measured, not guessed): the FabOS app icons are full-bleed rounded tiles, Breeze app
  icons keep a margin inside their box. Two facts from the offscreen render drive the code. (1) KIconLoader never
  scales a fixed-size PNG: at the dock's 40 px resting box it centred the theme's **32 px PNG** (the FabOS scalable
  directory's nominal `Size=64` loses the best-match to `32x32`), so tiles read 0.80 of the box at rest and would step
  32 → 48 → 64 while magnifying. Tiles are therefore drawn **from their SVG at exactly the box size**
  (`dock.tileSource(name)` → `file:///usr/share/icons/FabOS/scalable/apps/<name>.svg`; a task is a tile when its
  launcher's desktop file names a tile icon or its own icon name is one — the probe `ls`es the theme's `scalable/apps`
  and reads every desktop file's `Icon=` once at start). Every other icon is requested at Breeze's native scalable size
  (`stdIconSize` 64, inside `apps/48`'s 48–256 range) and scaled to the box by the item, so it magnifies smoothly and
  keeps its own glyph margin. (2) Breeze's glyph extent at that size is the target: `tests/dock-qml-harness/measure.py`
  reads the alpha bounding boxes of nine Breeze-only app icons and of every dock item (raw at `tileScale` 1.0, then at
  the shipped value) and prints the factor `mean Breeze extent / tile extent`; the shipped `tileScale` default in
  `config/main.xml` must match it within 0.02 and all items must lie within ±6 % of the mean. Measured 2026-09-15 in
  the image (40 px resting slot): Breeze glyphs 54 of 64 px = **0.847** (nine icons, 0.844–0.859), tiles 1.000 →
  factor **0.8472**, shipped `tileScale` **0.85**; normalised, every item — start tile, eight app tiles, a Breeze-icon
  window, peek — is **34 px of the 40 px slot**, spread ±0.0 %. The start button is the theme's launcher tile (`start-here`, the
  Fab OS mark on a dark tile); peek is a neutral tile (text colour @ 14 %) behind the monochrome desktop glyph — so
  every item in the row is a tile of one extent. `dock-uniform.png` is the normalised idle dock.
- **Backend**: libtaskmanager's `TasksModel` exactly as the stock task manager configures it — launchers from
  `Plasmoid.configuration.launchers` (written back on change), `GroupApplications`, `SortManual`, launch-in-place,
  activity / virtual-desktop filters from the settings. Default pins: Overview, Fab AI Controls, Files, Terminal, Editor,
  **Firefox**, Settings, Software.
- **Magnify**: the hovered icon scales to 1.6, neighbours 1.3 / 1.1 (Subtle 1.3 / 1.15 / 1.05, Strong 1.9 / 1.45 /
  1.15), 160 ms OutCubic; the start button and peek magnify like any other slot. Each item's width follows its own
  scale, so the row re-flows and icons never overlap. Because a panel clips its applets, the **resting** size is
  `floor(available height / peak)`, capped at 48 px: in the 4-gridUnit dock icons rest at about 40 px and the hovered
  one fills the panel at 64 px. The **applet's width is constant while the pointer moves**: resting row + the growth of
  one magnified group (`reserve`), so the floating "fit" panel never resizes per frame and an interior hovered icon
  keeps its centre where it rested. `hoveredIndex` is set and cleared by each item's own `HoverHandler` only (an item
  is a `ToolTipArea` and takes the hover away from the root beneath it).
- **Behaviour**: left click launches (with a 300 ms bounce 1.0 → 1.15 → 1.0), activates, minimises the active window or
  cycles a group's windows; middle click opens a new instance; right click opens an own `PlasmaExtras.Menu` (New Window,
  Minimise / Restore, Pin to Dock / Unpin, Close, Configure Dock…). Tooltips: window title, app name / window count.
  Running indicator: a 3 px pill under the icon (accent while active, wider for a group, attention colour when
  demanding attention); minimised windows at 60 %; a bounded pulse while an app is starting.
- **Settings**: Magnify on hover (the switch shared with the top bar), Magnification (the dock's own), largest resting
  icon, grouping, desktop / activity filters, start / peek items, `tileScale`.

### Checks

`node tests/quicksettings-js-test.js` (parsers, recorded nmcli → glyphs, rates and the 0 kB/s floor, the tile model:
order / size / enabled / geometry / drag helpers, the night-light command, the sync script, the main.qml invariants),
`node tests/layout-js-dry-run.js` (executes the layout script against a stub of the 6.6 shell API and asserts the bar
and dock contents and the tray's `extraItems` / `knownItems` / `hiddenItems`), and `tests/desktop-applets-qml-test.sh`
(inside the image with `plasmawindowed`, offscreen, once per Fab OS colour scheme with `QT_QPA_PLATFORMTHEME=kde`):
a 25 s soak of each applet, then the harnesses — quick settings: fed status and `/proc/net` samples (moving, idle →
`0 kB/s` still shown, moving again), the pane opened while the window size and the card's `y` are sampled every 11 ms,
the tile edit mode (programmatic drag, size toggle, remove / add back, reset → `tilesJson`), a real
`org.freedesktop.Notifications.Notify` into the 28-gridUnit history (row ≥ 56, icon 32, body 13), Do Not Disturb, the
size change queuing the sync script, the close sampled the same way; dock: the unified row, hover scales and re-flow,
constant applet width, bounce, menu, magnify off / strong, the write-back, then the icon-extent grabs for
`measure.py`; clock: text, px per size, no seconds, formats, the month popup. Renders
`build/{light,dark}/quicksettings-{bar,pane,edit}.png`, `notifications-pane.png`, `dock-{idle,hover,uniform}.png`,
`clock-bar.png`. Under a virtual `kwin_wayland` (`tests/dock-qml-harness/kwin-session.sh`) the dock gets the real
`TasksModel` and both harnesses drive a **real pointer** through `tests/quicksettings-qml-harness/fakeinput.py`
(KWin's `org_kde_kwin_fake_input`): hover on the network indicator (glyph-only magnify), the bell, a history row and
its dismiss cross; icons in the dock and a click on the Overview launcher. `tray` runs the real plasmashell and
asserts the generated appletsrc (above).
