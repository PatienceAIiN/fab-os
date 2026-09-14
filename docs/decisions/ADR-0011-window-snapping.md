# ADR-0011: Window snapping like Windows/Fedora — edge tiling, tile-layout zones, Snap Assist

Date: 2026-09-14. Status: accepted.

## Context

Users coming from Windows and from GNOME (Fedora) expect three things from a window manager: dragging a window to an
edge or corner tiles it; while dragging, drop zones for a layout appear; after one window is tiled, the remaining
half offers the other open windows (Windows calls this Snap Assist). KWin 6.6 (kwin-wayland 4:6.6.6) gives the first
two natively; the third does not exist upstream.

## What KWin 6.6 offers, verified in the image

- `kwin.kcfg` has `ElectricBorderTiling` (default true) and `ElectricBorderCornerRatio` (default 0.25) and nothing else
  about tiling: there is no setting to show the tile-layout zones without a modifier, and no `TilingModifier`-like key
  in `libkwin.so.6`, `kwin_wayland` or `kconf_update/kwin.upd`. So the zones stay behind **Shift while dragging**, and
  **Meta+T** opens the tile editor. Both are documented in the README and the public guide.
- The Window View effect registers `/org/kde/KWin/Effect/WindowView1`, interface `org.kde.KWin.Effect.WindowView1`,
  with one method, `activate(as handles)` (window `internalId` UUID strings). It is an OpenGL/QML effect: it does not
  load under QPainter compositing.
- `libkwin` looks for scripts in both `kwin-wayland/scripts/` and `kwin/scripts/`; scripts are switched on through
  kwinrc `[Plugins] <id>Enabled=true`; `QTimer` is available to scripts; `Window.tile.relativeGeometry` tells which
  half a quick-tiled window occupies (`libkwin` exports the `quickTileModeChanged` signal but no `quickTileMode`
  property name to scripts).
- KWin's `callDBus` sends a JS array as D-Bus `av`; the effect's adaptor only accepts `as`, so the call is rejected
  ("No such method 'activate' ... (signature 'av')"). A QStringList that arrives in a D-Bus reply is wrapped as a
  mutable sequence that keeps its type on the way out, so the script borrows one at start-up
  (`org.kde.kglobalaccel /component/kwin shortcutNames`) and refills it with the handles for every offer. If none is
  available it falls back to `invokeShortcut("Expose")`, which shows Window View for the whole desktop. The test
  harness proves the premise against `org.kde.KGlobalAccel.shortcut(as)`: the plain array is refused with
  "(signature 'av')", the borrowed list is accepted.

## Decision

1. Keep KWin's own edge tiling on: `[Windows] ElectricBorderTiling=true`, `ElectricBorderCornerRatio=0.25`,
   `ElectricBorderMaximize=true`, `ElectricBorderDelay=150` in `/etc/xdg/kwinrc`. No effects are disabled.
2. Ship **Fab OS Snap Assist** as a KWin script (`fabos-desktop`, `/usr/share/kwin/scripts/fabos-snap-assist`,
   Apache-2.0, author Patience AI), enabled by `[Plugins] fabos-snap-assistEnabled=true`. When a normal window becomes a
   full-height left or right half and other normal windows exist on the same screen and desktop, it shows those
   windows in Window View; the one the user activates is quick-tiled into the free half. It does nothing when the free
   half is already occupied, when it tiled the window itself, within 3 s of the previous offer, for dialogs, popups,
   transient, minimized or skip-taskbar windows, and it withdraws the offer when Window View closes without a pick
   (or never opens; `[Script-fabos-snap-assist] RequireWindowView=false` keeps the pick open for 8 s regardless).
3. Test it against the real engine: `tests/snap-assist-test.sh` runs `kwin_wayland --virtual` inside the image with the
   source-tree kwinrc and script bind-mounted, opens kcalc/konsole/kate as Wayland clients, tiles one and checks the
   offer, the `as` marshalling and the resulting geometry. Window View itself cannot be shown there (QPainter), so that
   single step is reported as skipped; `tests/snap-assist-unit.js` covers the debounce, dialog and withdraw paths with
   a mocked API (`node tests/snap-assist-unit.js`). `tests/branding-check.sh` checks that the script is shipped and
   enabled, that the edge-tiling keys are set, and that the firewall is on.

## Consequences

- On machines whose compositor cannot load OpenGL effects, Snap Assist silently does nothing (Window View is absent);
  edge tiling keeps working.
- The behaviour lives in one ~170-line script and one kwinrc key; removing it is `fabos-snap-assistEnabled=false`.
