# Fab OS branding

**Identity:** Fab OS — a modern desktop operating system by Patience AI. Written "Fab OS" (two words) in prose,
`fabos` in identifiers. Tagline on login, About and installer: **Fab OS by Patience AI**.

**Mark:** a ring with three woven bars, rotated -24° (`brand/logo/fabos-mark.svg`). The identity icon is the **bare
mark in the accent colour `#3B6EF5` on a transparent background — no tile behind it** — so it sits directly over the
wallpaper, the dock, the greeter card and the About page. It is shipped as a vector (`fabos.svg`) first and as PNGs from
16 to 1024 px; a monochrome `fabos-symbolic.svg` (KDE `ColorScheme-Text`, `currentColor`) exists for panels and for
places where the icon must take the surrounding text colour (e.g. the spinning mark on the ask bar's accent pill).
Only the default avatar (`fabos-face.png`) adds a thin white halo, because it is drawn over arbitrary wallpapers.
The coloured tiles (Material Symbols glyph on a gradient square) are for **applications** — Fab AI Controls, Fab
Updates, Fab Feedback and the bundled KDE apps — never for the Fab OS identity. Wordmark set in Inter Bold.

**Where the brand appears (and where it does not):** boot animation, greeter, splash, About, installer, Welcome,
Fab AI Controls, Updates, Feedback, launcher start button, ask bar. Ordinary application windows, dialogs and
System Settings pages carry no logo — the interface should feel like an OS, not an advertisement.

**Colour:** ink `#0E1116`, surfaces `#161B22`/`#1E242D`, text `#E6EAF0`, accent `#6E9BFF` (dark) / `#3B6EF5` (light).
Tokens are defined in the FabOS colour schemes and Plasma theme. Colour schemes: Fab Dark, Fab Light (generated at install).

**Typography:** Inter (UI), JetBrains Mono (code/terminal). Sizes and weights in the tokens file.

## Asset sizes (rendered by `brand/gen/make_assets.py`, installed by `packages/build-debs.sh`)

Every raster is rendered at or above the largest size it is shown at, and every consumer scales **down**, never up.
Shapes are supersampled (mark 4x, weave 2x) and LANCZOS-downsampled; wallpapers are dithered ±1/255 so their long
gradients do not band on 8-bit panels. Sizes measured on a generator run of 2026-09-13.

| Asset | Generated file(s) | Pixel size | Installed as | Consumer scaling |
|-------|-------------------|------------|--------------|------------------|
| Identity icon (vector) | `icons/fabos.svg`, `icons/fabos-symbolic.svg` | scalable (48-unit viewBox) | `/usr/share/icons/hicolor/scalable/apps/`, `/usr/share/icons/FabOS/scalable/apps/`, SDDM `mark.svg`, KSplash `images/mark.svg` | KIconLoader prefers the SVG (scalable/ is first in `Directories`); QML rasterises it at 2x the shown size |
| Identity icon (PNG) | `icons/fabos-{16,22,24,32,48,64,128,256,512,1024}.png` | 16x16 … 1024x1024, RGBA, transparent | hicolor + FabOS theme `<s>x<s>/apps/fabos.png` (16–512) | exact-size hits; 512 covers 256 @2x |
| About / installer logo | `pixmaps/fabos.png` | 1024x1024 | `/usr/share/pixmaps/fabos.png` (kcm-about-distrorc `LogoPath`, Calamares `logo.png`) | downscaled by the consumer |
| Default avatar | `pixmaps/fabos-face.png` | 512x512, thin white halo | `/usr/share/pixmaps/fabos-face.png` → `/etc/skel/.face.icon`, `/usr/share/sddm/faces/.face.icon` | greeter/user KCM downscale |
| Lockups (mark + wordmark) | `pixmaps/fabos-logo.png`, `fabos-logo-dark.png` | 2x (Inter Bold 144 px; ~180 px tall) | `/usr/share/pixmaps/`, Welcome header, Calamares `wordmark.png` | shown at half size or less |
| Boot animation frames | `plymouth/spinner-00…35.png` | 36 × 256x256 (white) | `/usr/share/plymouth/themes/fabos/` | `fabos.script` scales once to 12 % of `Window.GetHeight()` (130 px @1080p, 173 @1440p, 259 @4K) |
| Boot wordmark | `plymouth/wordmark.png` | 2x (Inter 112 px; ~257 px tall) | same | scaled to 11 % of the screen height, aspect kept |
| Wallpapers, light | `wallpapers/light-<WxH>.png` | 3840x2160, 2560x1440, 2560x1600, 1920x1080, 1920x1200, 1366x768, 1280x800 | `/usr/share/wallpapers/FabOS/contents/images/<WxH>.png` | Plasma picks the closest name, `FillMode=2` (PreserveAspectCrop) |
| Wallpapers, dark | `wallpapers/dark-<WxH>.png` | same seven sizes | `…/contents/images_dark/<WxH>.png` | same |
| Wallpaper preview | `wallpapers/screenshot.png` | 1280x720 | `…/contents/screenshot.png`, look-and-feel `previews/preview.png`, `previews/splash.png` | thumbnails |
| Greeter + KSplash background | `sddm/background.png` (= dark 4K) | 3840x2160 | `/usr/share/sddm/themes/fabos/background.png`, look-and-feel `splash/images/background.png` | QML `Image.PreserveAspectCrop`, mipmapped |
| Greeter + KSplash wordmark | — | live `Text` in Inter | `Main.qml`, `Splash.qml` | vector at any DPI |
| App tiles (icon theme) | `icon-theme/scalable/apps/*.svg` + `<s>x<s>/apps/*.png` | scalable + 16…512 | `/usr/share/icons/FabOS/` | SVG first, PNG for exact sizes |
| 3D mark | `3d/fabos-mark.glb`, `.obj` | mesh | `/usr/share/fabos/3d/` | — |

| Window frame (Aurorae theme) | `brand/gen/aurorae_theme.py` → `packages/fabos-desktop/usr/share/aurorae/themes/FabOS/{decoration,minimize,maximize,restore,close}.svg` (generated offline, committed; the image build does not run the generator) | vector; buttons 28 px, title bar 36 px, 32 px padding with no shadow of its own (the KWin corner effect draws the one shadow; ADR-0019 amendment) | `/usr/share/aurorae/themes/FabOS/` + `FabOSLight/` (rc + metadata.desktop; SVGs are symlinks to FabOS) | Aurorae v2 (`org.kde.kwin.aurorae.v2`) through KSvg FrameSvg; scales with the decoration button-size factor and the output scale |
| Status / tray icons (monochrome) | `icon-theme/scalable/{status,devices,actions,places}/*.svg` (63 glyph files + 339 alias symlinks = 402 names) | 22-unit viewBox, no PNGs | `/usr/share/icons/FabOS/scalable/<context>/` | SVG only, so KIconLoader can recolour it at any size |

Icon theme `index.theme`: `[Icon Theme]` keeps `FollowsColorScheme=true`; `Directories=` lists the monochrome
`scalable/status, scalable/devices, scalable/actions, scalable/places` (Size 22, MinSize 8, MaxSize 512), then
`scalable/apps` (MinSize 16, MaxSize 1024), then `16x16 … 512x512/apps`.

## Desktop chrome (window frame, status icons, sizes)

**Roundness.** One radius scale, applied by three different mechanisms:

| Surface | Radius | Where it comes from |
|---------|--------|---------------------|
| Application windows, all four corners | **14** | KDE-Rounded-Corners KWin effect (`/etc/xdg/kwinrc` `[Round-Corners] Size=14`, package `fabos-rounded-corners`, ADR-0019), light and dark; the Aurorae decoration's own top arc is the same circle (`brand/gen/aurorae_theme.py`, `R = 14`), so the frame is right even where the effect is off (no OpenGL compositing); maximised, full-screen and tiled windows are square |
| Popups, dialogs, notifications, tray popup | **24** | FabOS Plasma theme `dialogs/background.svg` (`brand/gen/plasma_theme.py`, `RADIUS["popup"]`) |
| Panels, cards, widget backgrounds | **20** | Plasma theme `widgets/panel-background.svg`, `widgets/background.svg` (`RADIUS["panel"]`) |
| Tooltips | **14** | Plasma theme `widgets/tooltip.svg` (`RADIUS["tooltip"]`) |
| Controls (buttons, fields) in Fab OS apps | **12** (fields 14) | design tokens used by the Command Center / Welcome / ask-bar QML; the Breeze widget style keeps its own radius for Qt Widgets |

**Window frame.** KWin renders the Fab OS Aurorae SVG theme through the **Aurorae v2 engine**
(`kwinrc [org.kde.kdecoration2] library=org.kde.kwin.aurorae.v2`, `theme=__aurorae__svg__FabOS`, `BorderSize=None`,
`BorderSizeAuto=false`, buttons `M | IAX`: window icon left; minimize, maximize/restore, close right; `fabos-desktop`
depends on `kwin-style-aurorae`). Why v2 (ADR-0010): kwin-style-aurorae 6.6 ships two plugins. v1
(`org.kde.kwin.aurorae`, QML) lists only KPackage QML decorations in Settings > Window Decorations — an SVG theme
selected through it renders but is invisible there, shows no current selection, and once a user picks another
decoration there is no UI path back. v2 (C++ over KSvg) lists every `/usr/share/aurorae/themes/<dir>/metadata.desktop`
by its `Name=`, so **"Fab OS" and "Fab OS Light" appear in the KCM and can be re-selected**; the same list is printed
by `kwin-applywindowdecoration --list-themes` (verified in the image). KWin 6.6 itself rewrites
`library=org.kde.kwin.aurorae` + `__aurorae__svg__*` to v2 on start, so the frame would render either way; we store v2
so the KCM's current-selection lookup matches from the first login. The SVGs are generated offline by
`brand/gen/aurorae_theme.py` and committed; after editing the generator, re-run
`python3 brand/gen/aurorae_theme.py --out packages/fabos-desktop/usr/share/aurorae/themes`.

The buttons are our own glyphs, 3 px rounded strokes in a 28 px box inside a 36 px bar: minimize = thick bar,
maximize = rounded square, restore = two offset rounded squares, close = rounded X. Hover puts a disc behind the glyph —
accent (`ColorScheme-Highlight`) for minimize/maximize/restore, red (`ColorScheme-NegativeText`) with a white X for
close; pressed is a stronger disc; inactive windows dim the glyphs. The title-bar fill is `ColorScheme-HeaderBackground`,
the frame paints no shadow at all (since 2026-09-16 the KDE-Rounded-Corners KWin effect draws the only window shadow; the
32 px padding carries an invisible alpha-1/255 carrier the effect's shader needs, docs/design/DESIGN_SYSTEM.md "Windows"). Every fill is a KSvg `current-color-scheme`
class, so the frame follows the **system** colour scheme (Fab Dark or Fab Light) live.

**Caption colour — known limitation.** The one value Aurorae cannot take from the scheme is the caption colour:
`<theme>rc [General] ActiveTextColor` / `InactiveTextColor` are fixed colours (v2 reads them as plain `QColor`s; there
is no `[WM] activeForeground` lookup). Hence two theme directories that differ only in their rc: `FabOS` (light caption,
chosen by the dark look-and-feel) and `FabOSLight` (dark caption, chosen by the light look-and-feel); the SVGs are
shared through symlinks. **Switch schemes from Settings > Appearance > Global Theme** (Fab OS / Fab OS Light): the
look-and-feel switches the frame together with the colours. Changing only Settings > Colours keeps the previous
caption colour (a light caption on a light header) until the frame is switched — pick "Fab OS Light" (or "Fab OS") in
Settings > Window Decorations, or run `kwin-applywindowdecoration __aurorae__svg__FabOSLight` (writes kwinrc and
signals KWin to reload), or `plasma-apply-lookandfeel -a in.patienceai.fabos.light.desktop`.

**Status and tray icons.** The top bar shows only FabOS monochrome glyphs: `brand/gen/make_assets.py` `MONO_MAP` renders
Material Symbols (Rounded) without a tile into `scalable/status|devices|actions|places/`, each path carrying
`class="ColorScheme-Text"`, `fill:currentColor` and the `<style id="current-color-scheme">` block, which KIconLoader rewrites
to the panel text colour of the active scheme (the mechanism Breeze uses). Names covered: Wi-Fi by strength in both the
`network-wireless-signal-*` and plasma-nm `network-wireless-connected-NN` / `network-wireless-NN(-locked)` families,
`network-wireless-{disconnected,off,acquiring,hotspot}`, `network-wired(-activated/-unavailable/-disconnected)`,
`network-vpn`, `audio-volume-{high,medium,low,muted}`, `audio-input-microphone(-muted)`, `battery-000…100`
(+ `-charging`, + `-profile-*`), `battery-{missing,full-charged,caution,low,empty,good,full}`, the power-profile chooser
`battery-profile-{balanced,performance,powersave}`, `bluetooth` /
`preferences-system-bluetooth(-activated/-inactive)`, `notifications(-disabled)`, display and keyboard brightness,
`input-keyboard`, `user-desktop`, `view-grid`, the tray expander `arrow-{up,down,left,right}`, `plasma-vault`,
`kdeconnect(-tray)`, `printer`, `media-playback-{start,pause,stop}`, `media-skip-{forward,backward}`, plus a `-symbolic`
alias for each. `weather-*` and mobile-broadband names stay with Breeze. A name may live in `ICON_MAP` (tile) **or**
`MONO_MAP` (mono), never both — the generator refuses to build otherwise, because a tile in the tray or a mono glyph
among the Settings tiles would be a bug.

**Sizes.** UI font Inter 11 pt (`font`, `menuFont`, `toolBarFont`; `smallestReadableFont` 9; `fixed` JetBrains Mono 11;
title bars `[WM] activeFont` Inter 600 11). Icon groups: toolbars 24, small 18, dialogs 32, desktop 48, panel 32. The clock
is one bold Inter line at 13 (`ddd d MMM` beside the time). Both look-and-feel packages carry the same font defaults.

**Peek at the desktop.** The show-desktop widget is the last item of the bottom dock (not in the top bar), and the
bottom-right hot corner does the same (`kwinrc [ElectricBorders] BottomRight=ShowDesktop`; the top-left corner opens the Overview).

**Names for bundled applications** (launcher, dock, search): Fab Files, Fab Terminal, Fab Editor, Fab Software,
Fab Photos, Fab Documents, Fab Calculator, Fab Archives, Fab Screenshot, Fab System Info, Fab Monitor, Fab Search,
Fab Settings, Fab AI Controls, Fab Updates, Fab Feedback, Welcome to Fab OS (no wallet application: ADR-0015). These are display names
set through desktop-entry overrides; the programs remain the upstream projects credited in ATTRIBUTIONS.md.

**Author metadata.** Every artefact Fab OS ships names its author: `Patience AI <support@patienceai.in>` and
`HOME_URL` appear in the `Authors`/`Website` fields of every `metadata.json` (plasmoid, both look-and-feel packages,
wallpaper, the generated FabOS Plasma theme), in `Maintainer:` and `Homepage:` of every `DEBIAN/control`, in the SDDM
theme `metadata.desktop` (`Author`, `Email`, `Copyright`, `Website`), in the Plymouth theme descriptor (its format
has no Author key, so `Description`/`Comment` carry it), in the FabOS icon theme `Comment`, in
`/etc/os-release` (`VENDOR_NAME`, `VENDOR_URL`, `HOME_URL`, `SUPPORT_URL`, `BUG_REPORT_URL`) and as an about line
in Welcome, Fab Updates, Fab Feedback and the Command Center settings. `tests/branding-check.sh` checks the shipped copies.

**What is never rebranded:** copyright and licence notices, in-app About dialogs of upstream programs, package and
binary names, D-Bus names, configuration keys, and Canonical/KDE/Mozilla trademarks used for identification.

**Old names:** "Fabric OS" and "ai-native-os" are retired; the only remaining references are historical
(legal/TRADEMARK-SEARCH.md, ADRs) and the server hostname fabricos.patienceai.in.
