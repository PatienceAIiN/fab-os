# ADR-0019: Real rounded corners on every window through a KWin effect (KDE-Rounded-Corners), radius 14

**Status:** accepted (2026-09-15) · amends ADR-0012 (the Aurorae frame: its top radius becomes 14) · complements ADR-0004
(Plasma 6 on Wayland) · numbered 0019 because 0018 was taken by a parallel track when this one started.

## Context

Fab OS 1.0.3 rounded windows with the decoration only: the Aurorae v2 frame (ADR-0012) draws a radius-20 title bar with
transparent corner notches, verified pixel by pixel through KSvg (`tests/decoration-render-test.sh`). It was not enough.
A decoration can only shape what it draws — the title bar. The client's own bottom corners were always square, and on the
owner's device the top corners still read as sharp edges. The compositor is the only place where all four corners of a
window, in light and dark, for every application, can be cut.

KWin 6.6 has no built-in "round every window" option (its own rounding is Breeze's, decoration-side, and Fab OS does not
use Breeze). The upstream KWin effect **KDE-Rounded-Corners** (github.com/matinlotfali/KDE-Rounded-Corners, GPL-3.0,
formerly "ShapeCorners") does exactly this in the compositor: an `OffscreenEffect` with a fragment shader that masks the
four corners of each window (frame included), draws an optional outline and either keeps the decoration's shadow bent
around the arc or draws its own. Its latest release, v0.10.0 (2026-08-23), targets Plasma 6 and has a Kubuntu 26.04 CI job.
Ubuntu 26.04 does not package it, so it has to be built from source — and a KWin effect must be built against the exact
KWin it will run in (`KWIN_EFFECT_API_VERSION`, `KWIN_PLUGIN_VERSION_NUM` branches all over its sources).

## Facts verified (2026-09-15)

Build (all commands run; logs kept with the round's report):

- Release tarball `https://github.com/matinlotfali/KDE-Rounded-Corners/archive/refs/tags/v0.10.0.tar.gz`, 92 243 bytes,
  sha256 `f3f03d96e17ae4b7dcee6347a01c75de6f90ed19e070e98ae8bf2dd71ae276db` (computed on the host, pinned in
  `image/rounded-corners-build.sh` and in the package's copyright file).
- In a plain `ubuntu:26.04` container on the NITC mirror, with the upstream Kubuntu 26.04 CI package list
  (`cmake g++ gettext extra-cmake-modules qt6-base-private-dev qt6-base-dev-tools libkf6configwidgets-dev
  libkf6kcmutils-dev kwin-dev libdrm-dev` + `dpkg-dev`): cmake 4.2.3, ECM 6.24.0, KF 6.24.0, Qt 6.10.2, kwin-dev
  4:6.6.6-0ubuntu0.1 → "Found KWin Version: 6.6.6", "Found KWinEffect API Version: 237", configure 3.6 s, **15 translation
  units compiled with 0 warnings** in 39 s at -j4, Release. Output: `usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/effects/plugins/
  kwin4_effect_shapecorners.so` (165 160 bytes), `…/kwin/effects/configs/kwin_shapecorners_config.so` (289 400 bytes, the
  System Settings module), `usr/share/kwin/shaders/shapecorners.frag` + `shapecorners_core.frag` (22.8 kB each), six
  `kcmcorners.mo` catalogues. The same build succeeds inside the round-3 Fab OS image (`localhost/fabos:vm`).
- Plugin id: KWin's `PluginEffectLoader` names an effect after its plugin file, so the id is **`kwin4_effect_shapecorners`**
  (kwinrc key `kwin4_effect_shapecornersEnabled`); the embedded KPlugin metadata (`src/metadata.json`) says
  `ServiceTypes: KWin/Effect`, `Name: Rounded Corners (formerly ShapeCorners)`, `X-KDE-ConfigModule:
  kwin_shapecorners_config`, `License: GPL`; the KCM metadata lists `X-KDE-ParentComponents: kwin4_effect_shapecorners`.
- Configuration (`src/kcm/options.kcfg`): file `kwinrc`, one group **`Round-Corners`**, 90 keys. The ones Fab OS sets are
  listed in the Decision; every key name in `/etc/xdg/kwinrc` was checked against the kcfg by a script (29/29 present, types
  and defaults printed).
- Behaviour read in the sources: `Effect::supported()` = `isOpenGLCompositing()`; `Window::hasEffect()` = normal windows
  (`IncludeNormalWindows`) or dialogs (`IncludeDialogs`) or an `Inclusions` match, minus `Exclusions`; `hasRoundCorners()`
  is false for full-screen / maximised / tiled windows when the respective `DisableRound*` is true (maximised = frame equals
  the maximize area ±1 px; tiled = `TileChecker` finds windows that together span the screen); KWin itself, the lock screen,
  krunner, ksmserver and ksplash are hard exceptions; docks are used only for the maximised check. Palette colours:
  `resolveColor()` reads `QWidget::palette()` with the configured `QPalette::ColorRole` index. Shadows: with
  `UseNativeDecorationShadows` the shader keeps the decoration's shadow texture and interpolates it around the corner
  (`getNativeShadow`); `ShadowSize`/`ShadowColor` are used only by the effect's own shadow (`getCustomShadow`). Side effect:
  while loaded, the effect writes `breezerc [Common] OutlineIntensity=OutlineOff, RoundedCorners=false, OutlineEnabled=false`
  (and restores the defaults on unload) — meant for Breeze-decoration users; Fab OS uses Aurorae, so it is inert here.
- Packaging as a `.deb` (`fabos-rounded-corners_0.10.0-0fabos1_amd64.deb`, 135 kB, Installed-Size 592 kB): `Depends`
  from `dpkg-shlibdeps` (`libkwin6 (>= 4:6.6.6)`, KF6 config/coreaddons/i18n/kcmutils/widgetsaddons, Qt6 core/dbus/gui/
  widgets, libepoxy0, libc6, libstdc++6) plus an explicit ABI window `libkwin6 (>= 4:6.6), libkwin6 (<< 4:6.7)` and
  `kwin-wayland`. Installing it into the round-3 image needs no other package ("606 kB of additional disk space"); `ldd`
  resolves every library.
- **The purge hazard.** A dry run in the finished image of the obvious clean-up, `apt-get purge <the dev list>` followed
  by `apt-get autoremove --purge`, removed 30 + 215 packages including plasma-workspace, plasma-desktop, kwin-common,
  fabos-desktop and fabos-desktop-meta: `qt6-base-dev-tools` was already installed as a dependency of the desktop, purging it
  by name cascaded through its reverse dependencies, and autoremove then swept the orphans. The build script therefore
  purges exactly the set of packages it installed (dpkg list before vs after, `comm`), never a name list, never autoremove,
  and fails if any pre-existing package is missing afterwards or anything but `fabos-rounded-corners` was added.
- Size, measured by running the exact step (`image/rounded-corners-build.sh`) in the round-3 image (final run 2026-09-15,
  exit 0): the build tools are **+917 MB** while present (166 packages; 167 archives = apt's "Need to get 138 MB", 132 MiB
  on disk — the 167th is an upgrade of the pre-existing `perl-base` 5.40.1-7ubuntu0.1 → 0.3 that `perl` required; it stays
  upgraded and the script prints every such version change, since a name-list `comm` cannot see them). A first version
  that only purged the packages still left **+133 MB**, because apt keeps every downloaded `.deb` in
  `/var/cache/apt/archives` — the rootfs stage removes Ubuntu's `docker-clean` hook and nothing runs `apt-get clean`. A
  second version deleted the archives with `find -newer <marker>` and deleted **nothing** (review finding): apt stamps each
  downloaded `.deb` with the server's Last-Modified time, which is older than any marker written during the build. The
  step now lists the archive directory before and after, deletes exactly the difference (167 files, 132 MiB) and asserts
  that none of them is left; measured result: **7187 → 7188 MB, net +1 MB** (`du -sxm /usr /var /etc`; the package's
  Installed-Size is 592 kB), 916 MB of build tools removed, the 1656 older archives (1406 MB) untouched. Side-finding, not
  acted on here: the shipped round-3 image already carries **1 406 MB of cached `.deb` files
  (1 656 archives) from the earlier layers**; a single `apt-get clean` in the final configuration layer would drop them
  (whiteouts in the OCI layers, real savings in the exported rootfs / ISO). That is a separate decision.
- Headless load check (2026-09-15, `kwin_wayland --virtual` inside the image with the package installed and this kwinrc as
  `~/.config/kwinrc`): KWin's `org.kde.kwin.Effects.listOfEffects` lists `kwin4_effect_shapecorners` (the loader finds and
  parses the plugin), but the container's virtual backend only offers QPainter compositing (`KWIN_COMPOSE=O` → "Could not
  fulfill the requested compositing mode"), so `isEffectSupported` is false there, exactly as `Effect::supported()` says.
  Loading under OpenGL is therefore verified only in the booted VM by `tests/corners-vm.sh` (the Plasma session in QEMU
  composites with OpenGL through llvmpipe, which blur already relies on).
- Headless scripting check (2026-09-15, same virtual KWin, one PyQt6 window on its Wayland socket): the KWin script lines
  `tests/corners-vm.sh` uses — `w.frameGeometry = {x, y, width, height}` on a plain JS object, `workspace.activeWindow = w`,
  `callDBus("org.freedesktop.DBus", …, "in.patienceai.fabos.corners", "event", msg)` — are accepted by KWin 6.6.6's
  scripting engine: the report right after the assignment still shows the old geometry (490 282 300 236 → Wayland
  configure round trip), the one 1.5 s later shows exactly 200 120 700 420. That is why the test waits for the
  `geo settled` report, not the immediate one.
- Radius geometry: the effect masks the frame **including the decoration**, so the visible top corner is the intersection
  of the effect's arc and the Aurorae arc. If the two differ, either the effect's 1 px outline runs through the frame's
  transparent notch or the frame's arc shows inside the effect's — both visible as a sliver. The two must be the same
  circle: same radius, circular (the effect's default squircle would not match an SVG arc). The Aurorae generator was
  moved to `R = 14`, regenerated (only `decoration.svg` changed: `LW` 42, block 92 px) and re-verified through KSvg in the
  image (`tests/decoration-render-test.sh`: notch alpha 0.00, header 1.00, taper 0.11 vs edge 0.37, all 19 probes × 2
  frames pass with the probe's radius set to 14).

## Decision

1. **Ship the KDE-Rounded-Corners effect, built from its v0.10.0 release inside the image**, as the Debian package
   `fabos-rounded-corners` (`image/rounded-corners-build.sh`, run by `image/Containerfile` through a bind mount right before
   the Fab OS packages so the model/voice layers stay cached). The step installs the dev packages, fetches and verifies the
   pinned tarball, builds Release against the image's kwin-dev, packages with `dpkg-shlibdeps`-derived dependencies and a
   `libkwin6 (>= 4:6.6), (<< 4:6.7)` window, installs the package, then purges exactly what it added and asserts the result
   (plugin, KCM, shaders present; no build tool left; `ldd` clean; copyright names the sha; nothing pre-existing removed).
2. **One radius, 14, on all four corners, in every scheme.** `/etc/xdg/kwinrc` (fabos-desktop): `[Plugins]
   kwin4_effect_shapecornersEnabled=true`; `[Round-Corners] Size=14 InactiveCornerRadius=14 UseSquircleShape=false
   AnimationDuration=160 UseNativeDecorationShadows=true ShadowSize=24 InactiveShadowSize=16 OutlineThickness=1
   InactiveOutlineThickness=1 ActiveOutlineUsePalette=true ActiveOutlineUseCustom=false ActiveOutlinePalette=0
   ActiveOutlineAlpha=56 InactiveOutlineUsePalette=true InactiveOutlineUseCustom=false InactiveOutlinePalette=0
   InactiveOutlineAlpha=36 SecondOutlineThickness=0 InactiveSecondOutlineThickness=0 OuterOutlineThickness=0
   InactiveOuterOutlineThickness=0 IncludeNormalWindows=true IncludeDialogs=true DisableRoundMaximize=true
   DisableOutlineMaximize=true DisableRoundFullScreen=true DisableOutlineFullScreen=true DisableRoundTile=true
   DisableOutlineTile=true`. In words: radius 14 (the "field" step of the radius scale, below the 24 of Plasma popups, which
   are other window types and untouched), circular, a single 1 px hairline in the scheme's window-text colour at 22 % active
   / 14 % inactive, the Aurorae shadow kept and bent around the arc, 160 ms fades, square when maximised / full-screen /
   snapped. The Aurorae frame's own top radius is 14 as well (`brand/gen/aurorae_theme.py`), so the frame is right on its
   own where the effect cannot run.
3. **Legal:** GPL-3.0 text already in `THIRD_PARTY_LICENSES/GPL-3.0.txt` (index row extended); `ATTRIBUTIONS.md` and
   `legal/THIRD-PARTY.md` rows; the tarball URL + sha256 recorded in `legal/SOURCE-OFFER.md` as a non-archive component
   whose corresponding source is that tarball (`scripts/source-offer.sh` writes the URL + hash into every image's
   `legal/source-offer/<id>/README.txt` and `--download` mirrors the tarball, hash-verified, next to the Ubuntu sources),
   and repeated in `/usr/share/doc/fabos-rounded-corners/copyright` with the upstream `LICENSE` and README beside it. No
   modification to the upstream source. Licence as the tree states it (read 2026-09-15): the `LICENSE` file is GPL
   version 3; the only per-file notices are the headers of `src/Effect.cpp` and `src/Effect.h` — "Copyright 2015 Robert
   Metsäranta … either version 2 of the License, or (at your option) any later version", i.e. GPL-2.0-or-later, which
   permits distribution under GPL-3.0 with the rest; no other file carries a copyright line, and `src/metadata.json` names
   the authors Rob and Matin Lotfaliei. The copyright file therefore has a `Files: *` GPL-3.0 stanza attributed to "the
   KDE-Rounded-Corners authors" and a `Files: src/Effect.cpp src/Effect.h` GPL-2.0-or-later stanza — no invented years.
4. **Tests:** `tests/branding-check.sh` asserts the plugin, its KCM and shaders, the package (dpkg, manifest, `dpkg -S`),
   the copyright hash, the absence of build tools, the kwinrc switch and keys, the frame's radius-14 arcs in the rendered
   SVG, the generator and probe constants, and the legal/doc files; the pre-existing notch check accepts the radius-20 or
   the radius-14 geometry (append-only in substance: nothing removed or weakened; the strict radius-14 assertion is a new
   check). New `tests/corners-vm.sh` (SSH into the booted VM): `qdbus6 org.kde.KWin /Effects loadedEffects` must list
   `kwin4_effect_shapecorners`; Fab Editor is placed at a known frame geometry by a KWin script that reports it over the
   session bus, `spectacle -b -n -f -o` grabs the screen, and each of the four frame-corner pixels is compared with the pixel
   14 px inside and with its diagonal outside neighbour, in Fab Dark and Fab Light; every value is printed with PASS/FAIL.
   The corner-vs-inside distance threshold is 8 (a noise floor: a square corner gives exactly the window colour), not a
   contrast requirement, because the shadowed dark wallpaper next to a Fab Dark window can be within ~30 RGB units of it.
   The script has not yet been run in a booted VM (that is the orchestrator's VM step); the KWin-script placement and spectacle-over-ssh
   follow `tests/perf-vm.sh`, which is in the same state.

## Consequences

- Every normal window and dialog has four rounded corners of radius 14 in both schemes, cut by the compositor; the
  decoration's radius is consistent with it. Maximised, full-screen and tiled windows are square by design.
- The image grows by about 1 MB (measured net +1 MB, Installed-Size 592 kB); the build grows by one compile (~2 minutes on
  4 cores, 138 MB of dev downloads that are removed again). One Ubuntu package (`perl-base`) ends up at the archive's
  current revision instead of the rootfs stage's, which is what an `apt-get upgrade` would do anyway. A KWin update within 6.6.x keeps working (same effect API); a KWin 6.7 would make apt drop
  `fabos-rounded-corners` (its `<< 4:6.7` dependency) — then the frame's own radius-14 top corners remain and the package
  has to be rebuilt against the new headers (same script). The package lives in the image only; it is not in the Fab OS
  apt repository (adding it to `scripts/publish-apt.sh` from a built image is future work).
- Requires OpenGL compositing (KWin's default; the VM uses llvmpipe). Under the QPainter fallback the effect is simply
  not loaded (`isEffectSupported` false) and nothing else changes.
- The effect writes three keys into `breezerc [Common]` while loaded — irrelevant to Fab OS's Aurorae frame, but a user
  who switches to the Breeze decoration would see Breeze's own rounding and outline turned off, which is the effect's
  intent (it replaces them).
- The outline colour follows the scheme only as far as KWin's `QPalette` does (KDE platform theme in a Plasma session);
  should it not, the hairline is a 22 % `WindowText` of the default palette — dark, visible on Fab Light and nearly
  invisible on Fab Dark, never wrong. `tests/corners-vm.sh` prints the sampled values so this is observable.
- Plasma's own surfaces (panels, popups, notifications, the ask bar) keep their radius 24 from the Plasma theme.
