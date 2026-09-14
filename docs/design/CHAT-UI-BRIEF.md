# Fab AI Controls — chat UI brief (from the ChatGPT UI Kit reference in Figma)

Source: Figma file `U8et3QcXI42VtSdIsx15ty` ("ChatGPT UI Kit, AI Chat (Community)", SnowUI).
Extracted 2026-09-14 via the Figma REST API. Reference renders are kept OUTSIDE the repository (they are screenshots of a third-party product) on the build host at build/figma/:

| PNG | What it shows |
|---|---|
| `681-3246.png` | New chat, dark — sidebar + empty state (Examples / Capabilities / Limitations) + composer |
| `681-2796.png` | Same, light |
| `chat-676-5605.png` | Conversation (light): user pill, plain assistant text, per-message action icons, edit-message card with Cancel/Send, composer with mic + voice button |
| `chat-676-4554.png`, `chat-676-4874.png` | Authenticated start-chat flow (model dropdown, Share, avatar) |
| `chat-676-2591/2644/2743/2792.png` | Guest chat states (empty, typing, streaming, long answer) |
| `chat-676-3190.png`, `chat-676-3233.png` | "Write or code" answers with code blocks |
| `695-809.png` | SnowUI design system colour sheet |
| `webclient-overview.png` | Whole web-client page (all flows) |

## Layout (measured from the frames, 1440×1024)

- **Window**: 1440×1024, outer radius 24. Two columns: sidebar 282 px (UI Kit) / 304 px (web client), main column fills the rest.
- **Sidebar** (vertical, gap 20, padding 20): 
  - "New chat" pill button: 242×36, radius 12, accent fill, padding 16/8, plus glyph + label.
  - Conversation rows: 242×48, radius 12, padding 12, gap 8, chat-bubble glyph + title (Inter 400 14). Hover/selected = white @ 4 %.
  - Bottom group (separated by a 1 px hairline, white @ 15 %): Clear conversations, Light/Dark mode, My account, Updates & FAQ, Log out — each 48 high, icon 20 + label.
  - Web client adds: Search chats, Library, a "Chats" section label (12 px, 60 % opacity), and a footer card (sparkle icon + two-line text).
- **Top bar** (web client): model dropdown left (title + chevron), Share pill + kebab + avatar right, 60 px tall, no border.
- **Empty state**: product mark + name (Inter 600 32) with a small "Plus" tag (accent fill, radius 8, 12 px); three columns (gap 40) each with a 24 px glyph, a title (Inter 600 18) and three cards 276×48 (radius 8, white @ 4 %, Inter 400 14).
- **Messages** (web client):
  - User message: right-aligned pill, fill light grey (#f4f4f4 light / white @ 8 % dark), radius 24, padding 12/20, max width ~70 %.
  - Assistant message: NO bubble; plain text at 16/24 with bold headings, paragraphs, lists; beneath it an action row of 20 px icons: copy, thumbs up, thumbs down, speak (speaker), edit, regenerate (+ chevron). Icons 60 % opacity, 100 % on hover.
  - Edit-message state: the user text sits inside a card (radius 24, grey fill) with two pills bottom-right: "Cancel" (outlined) and "Send" (filled black/accent).
  - Follow-up suggestion pill ("Tell me more") right-aligned, radius 24.
- **Composer** (bottom, sticky): card 760×56 in the kit (radius 16, padding 20/16, fill white @ 4 %, hairline stroke white @ 20 % at 0.5 px), or 1040×100 two-row card in the web client (radius 28): row 1 = "Ask anything" placeholder; row 2 = "+" and "Tools" chips left, **microphone** icon (outlined circle) and a filled black circular **voice** button (waveform glyph) right. Below: one-line disclaimer (12 px, 60 %).
- **Background blur** 40 on the floating composer strip.

## Tokens

| Token | Figma value | Fab OS mapping |
|---|---|---|
| Page background (dark) | #333333 (kit) · #212121 (web client) | `palette().window()` — follow system scheme, never hard-coded |
| Sidebar background | same as page, hairline separator white @ 15 % | `palette().window()` + `palette().mid()` @ 25 % |
| Surface (cards, inputs) | white @ 4 % on dark, black @ 4 % on light (#f9f9fa) | `palette().alternateBase()` or window text @ 4 % |
| Input stroke | white @ 20 %, 0.5–1 px | `palette().mid()` |
| Accent | #adadfb (lavender) | **#3B6EF5** Fab OS accent (`Kirigami.Theme.highlightColor` / `palette().highlight()`) |
| Text primary | #ffffff / #000000 | `palette().windowText()` |
| Text secondary / placeholder | 60 % / 20 % | windowText @ 60 % / 40 % |
| Success / Warning | #34c759 / #ff9500 (SnowUI) | keep |
| Radii | 24 window/pills · 16 composer · 12 buttons+rows · 8 small cards/tags · 4 code | Fab OS: control 12, field 14–16, card 20, popup 24 |
| Type | Inter 400 14 body · 600 18 section titles · 400 12 captions · 400 16/24 message body | Inter (already the system UI font) |
| Icon size | 20 (rows), 24 (empty-state glyphs), 20 (action row) | Material Symbols from the FabOS icon theme |
| Spacing | sidebar padding 20, row gap 4, group gap 20, column gap 40, composer padding 20/16 | same |
| Motion | (static kit) | Fab OS: 160–320 ms OutCubic; message fade+rise 12 px; typing dots pulse |

## What Fab AI Controls must take from this

1. Two-column shell: 282 px sidebar (New chat pill, conversation list, bottom settings group) + main chat column; window radius 24, no visible title-bar noise inside the content.
2. Empty state with the Fab AI mark + "Fab AI Controls" and three columns: **Try asking** (examples of OS tasks), **What I can do** (tools), **Keep in mind** (limits / permission modes).
3. Messages per the web client: user pill right, assistant plain text left with the action row (copy, good, bad, speak, edit, retry) — Fab OS adds **Stop** while running and the live action timeline under the assistant turn.
4. Composer per the web client: two-row rounded card at the bottom, "Ask me to do anything…" placeholder, "+" attach and a mode chip left, **mic** and **voice** buttons right; Enter sends, Shift+Enter newline; follow-ups attach to the same conversation.
5. Palette-driven colours (system light/dark), accent #3B6EF5, Inter, Material Symbols icons. Nothing from OpenAI/ChatGPT branding (no logo, no name) — layout only; SnowUI kit is CC BY 4.0 community, we reuse structure and measurements, not assets.
