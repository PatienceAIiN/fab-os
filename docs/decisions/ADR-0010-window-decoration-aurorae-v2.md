# ADR-0010: Window frame as an Aurorae SVG theme, rendered by the Aurorae v2 engine

**Status:** accepted (2026-09-14) · complements ADR-0004 (Plasma 6 on Wayland)

## Context
Fab OS needs its own window frame: radius-20 title bar, bold 3 px minimize / maximize / restore / close glyphs, surfaces
that follow the **system** colour scheme (Fab Dark and Fab Light), no KDE glyphs. Breeze's decoration cannot be reshaped
that far through its KCM options, and a compiled KDecoration3 plugin would tie every Plasma update to a rebuild. KWin's
Aurorae engine renders a frame from SVGs (`decoration.svg` + one SVG per button) and is packaged by Ubuntu as
`kwin-style-aurorae`; its SVGs go through KSvg, whose `current-color-scheme` stylesheet classes are rewritten from the
active KColorScheme at render time, which gives scheme-following for free.

## Facts verified (Plasma 6.6.6 image, sources at invent.kde.org tag v6.6.5)
- `kwin-style-aurorae` 6.6.6 installs **two** KDecoration3 plugins: `org.kde.kwin.aurorae` (v1, QML,
  `/usr/share/kwin/aurorae/aurorae.qml`) and `org.kde.kwin.aurorae.v2` (C++/QPainter over `KSvg::FrameSvg`,
  `aurorae/v2/*.cpp`). Both declare `"themes": true` / `themeListKeyword: themes` in their plugin JSON, so the Window
  Decorations KCM asks each one for a theme list (`kwin/src/kcms/decoration/decorationmodel.cpp` `DecorationsModel::init`).
- v1's `ThemeProvider::init()` only calls `findAllQmlThemes()` (KPackage `KWin/Decoration` packages under
  `kwin/decorations/`). An SVG theme selected through v1 renders, but it is **not listed** in System Settings, the KCM
  shows no current selection, and a user who picks another decoration has no UI path back.
- v2's `DecorationThemeProvider` iterates every `aurorae/themes/<dir>/` that contains a `metadata.desktop`, reads its
  `Name=` with `KDesktopFile::readName()` and publishes the theme as `__aurorae__svg__<dir>` with configuration module
  `kcm_auroraedecoration` (`v2/decorationthemeprovider.cpp`). Directories without `metadata.desktop` are skipped with
  "has no metadata.desktop file". In the image, with the Fab OS theme directories mounted,
  `kwin-applywindowdecoration --list-themes` (which builds the same `DecorationsModel`) lists
  `Fab OS (theme name: __aurorae__svg__FabOS)` and `Fab OS Light (theme name: __aurorae__svg__FabOSLight)`.
- v2 reads the same `<theme>rc` keys as v1 (`[General]` ActiveTextColor, InactiveTextColor, TitleAlignment, Animation;
  `[Layout]` Border*, TitleEdge*, TitleBorder*, TitleHeight, Button*, Padding*) and the same SVG element prefixes
  (`decoration`, `decoration-inactive`, `decoration-maximized(-inactive)`, `innerborder`, `mask`; buttons `active`,
  `inactive`, `hover(-inactive)`, `pressed(-inactive)`, `deactivated(-inactive)`), so one theme serves both engines.
- KWin 6.6's `DecorationBridge` constructor runs `migrateAuroraeTheme()`: if `theme` starts with `__aurorae__svg__` and
  `library` is `org.kde.kwin.aurorae`, it rewrites `library` to `org.kde.kwin.aurorae.v2` in the user's kwinrc
  (`kwin/src/decorations/decorationbridge.cpp`). SVG themes are therefore always rendered by v2 at runtime; what
  differs is whether the *stored* plugin id matches what the KCM lists (it matches only for v2).
- Caption colour: v2's `DecorationTheme` reads `ActiveTextColor` / `InactiveTextColor` as fixed `QColor`s
  (`v2/decorationtheme.cpp`) and paints the caption with them; there is no `[WM] activeForeground` lookup. This is the
  one value an Aurorae theme cannot take from the colour scheme.
- `BorderSizeAuto` defaults to true in `kwindecorationsettings.kcfg`; neither Aurorae plugin declares a
  `recommendedBorderSize`, so without `BorderSizeAuto=false` KWin would apply `Normal` side borders.

## Decision
1. Ship the Fab OS frame as an Aurorae SVG theme at `/usr/share/aurorae/themes/FabOS/` (generated offline by
   `brand/gen/aurorae_theme.py` and committed; the image build does not run the generator) with a `metadata.desktop`
   whose `Name=` is the KCM entry.
2. Select the **v2** engine everywhere the frame is configured: `/etc/xdg/kwinrc` and both look-and-feel `defaults`
   carry `library=org.kde.kwin.aurorae.v2`, `theme=__aurorae__svg__FabOS` (dark) / `__aurorae__svg__FabOSLight`
   (light), `BorderSize=None`, `BorderSizeAuto=false`, `ButtonsOnLeft=M`, `ButtonsOnRight=IAX`.
3. `fabos-desktop` depends on `kwin-style-aurorae` explicitly (it was only present transitively via `kwin-wayland`).
4. Caption colour: two theme directories that differ only in their rc (`FabOS`: light caption; `FabOSLight`: dark
   caption; SVGs shared through symlinks). The supported way to change scheme is Settings > Appearance > Global Theme
   (the look-and-feel switches the frame with the colours). Recovery after changing only Settings > Colours: pick
   "Fab OS" / "Fab OS Light" in Settings > Window Decorations, or run
   `kwin-applywindowdecoration __aurorae__svg__FabOSLight` (saves kwinrc and signals KWin to reload), or
   `plasma-apply-lookandfeel -a in.patienceai.fabos.light.desktop`.
5. `tests/branding-check.sh` asserts the theme directories, the `Name=` entries, the v2 plugin id in kwinrc and both
   look-and-feel defaults, the v2 plugin file, the package dependency, and that `kwin-applywindowdecoration
   --list-themes` lists "Fab OS" in the image.

## Consequences
- The frame appears in System Settings under its own name and can be re-selected there; the "no way back" problem
  of v1-selected SVG themes does not exist.
- One set of SVGs follows Fab Dark / Fab Light live; only the caption colour is per-variant (documented limitation,
  with three recovery paths). A caption that follows `Kirigami.Theme.textColor` would need a KPackage QML decoration
  or a KDecoration3 plugin — deferred; revisit if Aurorae v2 grows a scheme-derived caption colour.
- The generator is not part of the image build; edits to `aurorae_theme.py` must be followed by re-running it and
  committing the SVGs (`python3 brand/gen/aurorae_theme.py --out packages/fabos-desktop/usr/share/aurorae/themes`).
