# Dock: clicks and running indicators (`in.patienceai.fabos.dock`, v4)

Companion to the dock section of [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) (geometry, magnify, tiles, spacing). This note is
the behaviour contract after the round-6 device report — *"when any app is open the marker disappears; clicking the dock
icon of an open app opens a fresh instance"* — and how it is proven.

## The root cause (found and reproduced in the VM, round 7)

v3's `TasksModel` had `filterHidden: true`, meant to hide windows that ask to stay off task bars. libtaskmanager's
**"hidden" role is the minimised state** (X11 `_NET_WM_STATE_HIDDEN`, the same role on Wayland), so every window the
user minimised — or that *Peek at the desktop* minimised all at once — dropped out of the model. Its pinned launcher then
had no window to merge with: the marker vanished, and the next click on the pin was `requestActivate` on a launcher row,
which launches a **second copy**. The real dock did exactly that in the VM
(`build/r7-dock-activate/run1-filterHidden-reproduced/03-after-click2-firefox-minimised-dock.png`: Dolphin and Konsole
keep their dots, the just-minimised Firefox has none; click 3 on that pin gave KWin a second Firefox window; the probe's
row table shows `windows 4 -> 3` and the Konsole pin back to `launcher / state none / tap launch`).

The fix is one line per model: `filterHidden: false` (minimised windows stay, drawn as a dimmed dot). What `filterHidden`
was meant to do already happens: libtaskmanager's filter proxy drops skip-taskbar windows by default, and `TasksModel`
exposes no `filterSkipTaskbar` at all (assigning one is a non-existent property — the applet fails to load; run 2 of the VM
proof hit exactly that and was discarded). The rest of v4 (below) is defence in depth around the same promise — a tap on
an app with a window anywhere never launches — and the branding check pins `filterHidden: false` in both models.

## The decision table (`contents/code/dock-logic.js`)

Every decision is plain JavaScript with no QML types, so `node tests/dock-js-test.js` runs each state transition. The
QML (`main.qml` `activate()` / `newInstance()`, `TaskItem.qml`) only reads the TasksModel rows and executes what the
logic returns.

| Row state (libtaskmanager roles)                          | Left click            | Indicator                    |
|-----------------------------------------------------------|-----------------------|------------------------------|
| pinned launcher, **no window anywhere**                   | `launch` (bounce)     | nothing                      |
| `IsStartup` (the app is starting)                         | `none` — ignored      | one dot at 70 %, icon pulses |
| `IsWindow`, not active                                    | `activate`            | one dot                      |
| `IsWindow`, active, not minimised                         | `minimize`            | 24 × 3 bar + 6 % background  |
| `IsWindow`, minimised                                     | `activate` (restore)  | one dot at 45 %, icon 70 %   |
| `IsGroupParent`, no active child                          | `recent` — most recently used child (`LastActivated`) | two dots |
| `IsGroupParent`, active child, 2+ windows                 | `cycle` — the next child                              | bar      |
| `IsGroupParent`, active child, 1 window                   | `minimize` that child |                              |
| launcher whose windows the filters hide (**elsewhere**)   | `elsewhere` — activate that window in the unfiltered model (KWin switches desktop / activity) | dot(s), dimmed if minimised |
| any row, **middle click**                                 | `new` — `requestNewInstance` (bounce) |              |

Rules that follow: a tap on an app that has a window **anywhere** never launches (asserted for every reachable state in
the unit test's simulation group); the marker (`running`) is on for `IsWindow || IsGroupParent || IsStartup` and for a
launcher with windows elsewhere, so it never drops while any window of the app exists — minimised, on another virtual
desktop, activity or screen included.

## Why a second TasksModel (and why the filters default off)

libtaskmanager hides a pinned launcher (`launchInPlace`) only while a window of its app **passes the dock's filters**
(`TasksModel::filterAcceptsRow` compares the launcher against the *filtered* window set). With *only the current
desktop / activity* on, a window elsewhere leaves the pin a bare launcher: no marker, and its `requestActivate` starts a
second copy — the reported chaos. Two layers fix that:

1. **Defaults.** `showOnlyCurrentDesktop` and `showOnlyCurrentActivity` are both off (v3 shipped the activity filter on),
   `filterByScreen` is off too. A window on another virtual desktop, activity or screen is then simply a window row of
   the dock's own model — marker on, tap activates (KWin switches to it). This is the macOS / Windows 11 behaviour: the
   dock is one list of running apps, not a per-desktop list.
2. **`allTasks`.** For a user who turns a filter back on, `main.qml` keeps a second `TasksModel` over the same session's
   windows with no desktop / activity / screen filter and no launchers (the `WindowTasksModel` behind both is one shared
   instance in libtaskmanager, so this is two proxy models, not a second window list). A bare launcher looks its app up
   there (`findElsewhere`); a hit means "running elsewhere": dot(s) on, tap = `elsewhere` (activate that window through
   `allTasks`), tooltip "Open on another desktop". Items re-read it once per change burst (`dock.allRevision`, 80 ms
   debounce). The probe log of the VM proof shows the filtered model dropping a window the unfiltered one still holds
   (`windows 4 / allWindows 5`) and the pin keeping its marker.

### Launcher ↔ window identity (`sameApp`)

libtaskmanager does the merge itself when it can resolve the window's desktop file (Wayland app id `org.kde.konsole` →
`applications:org.kde.konsole.desktop`; `firefox` → `applications:firefox.desktop`; the rebranded "Fab Terminal" /
"Fab Files" overrides in `/usr/local/share/applications` keep the same ids, so nothing in the layout's launcher list
had to change; LibreOffice windows resolve to `applications:libreoffice-writer.desktop` etc.). `sameApp` is the dock's
own comparison for the `elsewhere` lookup: `LauncherUrlWithoutIcon` / `AppId` of both rows, ignoring case, a query
string and `.desktop`, either field of one side against either of the other — plus one last resort for a window
libtaskmanager could **not** resolve (row dump of the first VM run: `file:///usr/bin/konsole`, AppId `konsole`) or an
id in another form (`firefox.desktop` pinned, the window says `org.mozilla.firefox`): a bare name equals the last
segment of a reverse-DNS id. Two dotted ids never match that way (`org.kde.kate` vs `org.kde.konsole`), and
`libreoffice-writer` vs `libreoffice-startcenter` stay different launchers. Such an unresolved window still gets its own
icon (libtaskmanager did not merge it), but the pin shows the marker and a click on it brings that window forward
instead of starting a second copy.

## Proofs

- `node tests/dock-js-test.js` — 25 groups: running state / indicator kind / dots for every row kind, every tap
  transition, identity matching (including the reverse-DNS last resort), and `readRow` / `findElsewhere` / `perform` /
  `tap` against a fake model that records the exact requests (no `requestNewInstance`, no `requestActivate` on a launcher
  row while a window exists; the unresolved-window row from the VM dump is activated, not launched).
- `tests/branding-check.sh vm` — the "dock v4" source-tree check pins `filterHidden: false` in both models, no
  `filterSkipTaskbar`, the decision module, the unfiltered model, both filter defaults off, and runs the node test.
- `flock -w 5400 /tmp/fabos-vm.lock -c 'FABOS_MAIN=<main checkout> tests/dock-qml-harness/vm-dock-activate.sh <out dir>'`
  — the real VM (a disposable qcow2 overlay of `build/fabos-vm.img`, QEMU quit and the overlay deleted by the script's
  trap): the plasmoid installed under `/usr/share/plasma/plasmoids`, plasmashell restarted, Firefox + Fab Terminal +
  Fab Files opened from their desktop files **in plasmashell's own environment** (`/tmp/sess` copies its `/proc/PID/environ`;
  an ssh login lacks `XDG_MENU_PREFIX=plasma-`, so KService resolves nothing there and every pin looks unmerged — that
  was the first run's false alarm), the **real dock clicked with QEMU's pointer** (activate → minimise → restore; KWin's own
  window list: 1 Firefox window throughout), Konsole minimised through KWin (dimmed dot; `IsHidden` true, `SkipTaskbar`
  false), **v3 replayed** on the same rows (`filterHidden: true`: 4 → 3 windows, the Konsole pin a bare launcher whose tap
  would launch), the view from a second virtual desktop (three markers), the tap table driven on the live rows through the
  probe applet (`ModelProbe.qml` + `probe-driver.py`, the dock's `dock-logic.js` inside the session: activate / minimise /
  restore / launch Fab Editor / middle click → Konsole group of 2 → cycle; KWin: Firefox 1, Dolphin 1 unchanged), Firefox
  on desktop 2 with the default filters (window row, marker) and with "only the current desktop" written into the real
  dock's config (bare launcher + `elsewhere`, marker kept, tap = `elsewhere`, still 1 Firefox window), LibreOffice
  resolving to `applications:libreoffice-startcenter.desktop`, and no QML / JS diagnostics from the dock in plasmashell's
  journal. Round 7, 2026-09-16: `VM DRIVER DONE failures=0` (102 PASS lines); evidence under `build/r7-dock-activate/`
  (`NN-*.png` full frames + `-dock.png` bands, `vm-driver.log`, `probe.log`, `driver-*.log`), the bug reproduction under
  `run1-filterHidden-reproduced/` and the first, blind run under `previous-run-1302-ssh-env/`.
- `tests/desktop-applets-qml-test.sh localhost/fabos:vm harness-dock` — the offscreen render harness over the same
  `main.qml` (both schemes): indicator kinds (window / 3-window group / minimised at 45 % / active bar), 12 px gaps,
  centring, tile extents — `harness-dock rc=0` this round (`build/r7-dock-activate/desktop-applets-harness-dock.log`).
  `harness-dock-kwin` (virtual `kwin_wayland` + real pointer) was not re-run.

## Shipping note

The plasmoid ships in `fabos-desktop`; a running plasmashell keeps the old QML in memory until it restarts, so the fix is
live at the next login or `systemctl --user restart plasma-plasmashell`.
