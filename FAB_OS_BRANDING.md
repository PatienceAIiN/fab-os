# Fab OS branding

**Identity:** Fab OS — a modern desktop operating system by Patience AI. Written "Fab OS" (two words) in prose,
`fabos` in identifiers. Tagline on login, About and installer: **Fab OS by Patience AI**.

**Mark:** a ring with three woven bars, rotated -24° (`brand/logo/fabos-mark.svg`). The identity icon is the **bare
mark in the accent colour `#3B6EF5` on a transparent background — no tile behind it** — so it sits directly over the
wallpaper, the dock, the greeter card and the About page. It is shipped as a vector (`fabos.svg`) first and as PNGs from
16 to 1024 px; a monochrome `fabos-symbolic.svg` (KDE `ColorScheme-Text`, `currentColor`) exists for panels and for
places where the icon must take the surrounding text colour (e.g. the spinning mark on the ask bar's accent pill).
Only the default avatar (`fabos-face.png`) adds a thin white halo, because it is drawn over arbitrary wallpapers.
The coloured tiles (Material Symbols glyph on a gradient square) are for **applications** — Fab Command Center, Fab
Updates, Fab Feedback and the bundled KDE apps — never for the Fab OS identity. Wordmark set in Inter Bold.

**Where the brand appears (and where it does not):** boot animation, greeter, splash, About, installer, Welcome,
Command Center, Updates, Feedback, launcher start button, ask bar. Ordinary application windows, dialogs and
System Settings pages carry no logo — the interface should feel like an OS, not an advertisement.

**Colour:** ink `#0E1116`, surfaces `#161B22`/`#1E242D`, text `#E6EAF0`, accent `#6E9BFF` (dark) / `#3B6EF5` (light).
Tokens: `fab-ui/design-system/tokens/tokens.json`. Colour schemes: Fab Dark, Fab Light (generated at install).

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

Icon theme `index.theme`: `[Icon Theme]` keeps `FollowsColorScheme=true`; `Directories=` lists `scalable/apps`
(MinSize 16, MaxSize 1024) first, then `16x16 … 512x512`.

**Names for bundled applications** (launcher, dock, search): Fab Files, Fab Terminal, Fab Editor, Fab Software,
Fab Photos, Fab Documents, Fab Calculator, Fab Archives, Fab Screenshot, Fab System Info, Fab Monitor, Fab Search,
Fab Settings, Fab Wallet, Fab Command Center, Fab Updates, Fab Feedback, Welcome to Fab OS. These are display names
set through desktop-entry overrides; the programs remain the upstream projects credited in ATTRIBUTIONS.md.

**What is never rebranded:** copyright and licence notices, in-app About dialogs of upstream programs, package and
binary names, D-Bus names, configuration keys, and Canonical/KDE/Mozilla trademarks used for identification.

**Old names:** "Fabric OS" and "ai-native-os" are retired; the only remaining references are historical
(legal/TRADEMARK-SEARCH.md, ADRs) and the server hostname fabricos.patienceai.in.
