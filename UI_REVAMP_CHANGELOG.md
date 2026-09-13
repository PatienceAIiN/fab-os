# UI revamp changelog

## 2026-09-13
- Product names now come from generated `en@fabos` translation catalogs (Fab Terminal, Fab Files, Fab Editor,
  Fab Software, Fab Photos, Fab Documents, Fab Calculator, Fab Archives, Fab Screenshot, Fab Search, Fab System
  Info, Fab Monitor, Fab Settings, Fab Wallet, Fab OS Desktop); byte patches reduced to plugin metadata (ADR-0009).
- Discover (Fab Software) repaired: the bare UTF-16 rule that broke its resource table is gone and rejected.
- Light mode: icon theme follows the colour scheme (`FollowsColorScheme`), Plasma theme falls back to the
  scheme-following default theme — tray icons are legible on the light top bar.
- Kickoff/KRunner: KWin and Plasma-shell runners disabled by their real plugin ids (no "KWin" section).
- Desktop Actions keep their own names ("Open a New Window"); shortcut component names, AppStream store names and
  the Software Sources dialog wording rebranded; Ubuntu web shortcut hidden; `ubuntu` account and minimized-MOTD
  removed from the image.
- Rounded window corners: kept KWin 6.6 native Breeze rounding; third-party effect rejected (KWin ABI pin risk).
- Audit follow-up: Konsole byte rules removed (they touched functional bytes), `.mo` patching retired, 20 more catalog
  rules (KIO, Ark, Spectacle, KCalc, Klipper, kaccess…), notification/store/device-action names, MIME descriptions,
  Software Sources hidden (cannot run on Fab OS), KDE donation nag off, wired Ethernet managed by NetworkManager.
- High-definition brand assets: wallpapers at seven sizes (3840x2160 … 1280x800), light and dark, shapes supersampled
  2x + LANCZOS, ±1/255 seeded dither against banding; greeter and KSplash background now the 3840x2160 render,
  cropped (PreserveAspectCrop) never stretched; lock screen and desktop wallpaper set `FillMode=2` explicitly.
- Fab OS mark redrawn as the identity icon: bare ring + bars in accent `#3B6EF5` on a transparent background (no tile),
  `fabos.svg` + PNG 16–1024, plus `fabos-symbolic.svg` (KDE colour-scheme aware). Used by the start button, ask bar,
  About page (`/usr/share/pixmaps/fabos.png`, 1024 px), default avatar (`fabos-face.png`, thin halo), SDDM card and
  KSplash (both load the SVG). App tiles unchanged.
- Plymouth: 36 frames at 256 px (2x), script sizes everything from `Window.GetHeight()` (mark 12 %, wordmark 11 %,
  bar 12.5 % of the width) via `Image.Scale` once at load; timing unchanged. Greeter/KSplash wordmark is live Inter text.
- Icon theme: `scalable/` first in `Directories` (MaxSize 1024), fixed sizes now include 512x512, `FollowsColorScheme=true`.
- Wallpaper package metadata: Name "Fab OS", Author "Patience AI", License Apache-2.0 (`KPackageStructure` set).

## 2026-09-12
- Everything follows the system colour scheme: FabOS Plasma theme uses live scheme colours; ask bar uses
  Kirigami.Theme; Command Center, Updates, Feedback, Welcome use palette roles. Fab Dark / Fab Light schemes added.
- Ask bar rebuilt: centred floating panel, single animated action, state dot, status only when relevant.
- Launcher, dock and search names per spec (Fab Files, Fab Terminal, …); window titles of Settings, Software,
  Monitor, System Info, Terminal patched length-safely; Fab Wallet everywhere incl. module texts.
- Welcome to Fab OS first-run wizard (appearance, privacy, AI, quick links).
- Audited root execution path for the agent (as_root), documented.
- Attribution/licensing documents and THIRD_PARTY_LICENSES added; UI_UX_AUDIT.md written.

## 2026-09-11
- Rounded Plasma theme, FabOS icon theme (89 families), Fab mark on start button, KWin animations, Firefox from
  Mozilla, feedback dialog simplified, KDE user-feedback panel hidden, Fab OS session entry, Budgie greeter removed.
