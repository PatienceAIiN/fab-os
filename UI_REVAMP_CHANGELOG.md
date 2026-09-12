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
