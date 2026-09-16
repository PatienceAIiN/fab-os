# Leaving Fab OS: shut down, restart, log out

What the user sees before the machine turns off, what Plasma really does after they confirm, and — honestly — what
is and is not guaranteed about open work. Owner's brief (1.0.5 on a Lenovo laptop): *"while shutting down show all the
processes if opened and safely save all things before shutting down — make it safe-proof."*

Ships over the air in `fabos-desktop` 1.0-7; no image rebuild needed. Nothing new runs in the background: the change is
one QML screen, one `.desktop` entry that grants it the window list, and two lines of system configuration.

## 1. The leave screen

`packages/fabos-desktop/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/logout/`

| file | role |
|---|---|
| `Logout.qml` | the screen; Plasma's `ksmserver-logout-greeter` loads it as the look-and-feel package's `logoutmainscript` |
| `LeaveButton.qml` | the 48 px / tile button (control radius 12, accent fill for the one primary action, 2 px focus ring, Enter / Space) |
| `session-apps.sh` | helper run through a Plasma5Support "executable" DataSource: reads the countdown key, lists apps from systemd user units when no window list is available |
| `…/in.patienceai.fabos.light.desktop/contents/logout/Logout.qml` | Fab OS Light: a Loader wrapper that shows the same dark screen (a leave screen is a dark scrim in both schemes, like the lock screen) and forwards the greeter's ten signals |
| `usr/share/applications/in.patienceai.fabos.logout-greeter.desktop` | not a launcher: the entry that lets KWin hand the greeter the Wayland window list (`X-KDE-Wayland-Interfaces=org_kde_plasma_window_management`, `Exec` = the greeter binary, `NoDisplay=true`) |
| `etc/xdg/ksmserverrc` | `confirmLogout=true` (always show the screen), `confirmLogoutCountdown=30` (Fab OS key), `loginMode=emptySession` (low-RAM, unchanged) |

### The greeter's contract (plasma-workspace 6.6.6, read from the binary)

The greeter is D-Bus activated (`org.kde.LogoutPrompt`, `/LogoutPrompt`: `promptShutDown`, `promptReboot`, `promptLogout`,
`promptAll`) by libkworkspace whenever anything in the session asks to leave — launcher, power button, Ctrl+Alt+Del
(`promptAll`), Fab Settings. It creates one `KSMShutdownDlg` per screen, loads the package's `contents/logout/Logout.qml`
with the context properties `sdtype` (KWorkSpace::ShutdownType: −1 every option, 0 log out, 1 restart, 2 shut down),
`maysd`, `canLogout`, `softwareUpdatePending`, `spdMethods` (`StandbyState`/`SuspendState`/`HibernateState`),
`rebootToFirmwareSetup`, `rebootToBootLoaderMenu`, `rebootToBootLoaderEntry`, `screenGeometry`, and connects the root
item's signals `logoutRequested`, `haltRequested`, `haltUpdateRequested`, `suspendRequested(int)`, `rebootRequested`,
`rebootRequested2(int)`, `rebootUpdateRequested`, `cancelRequested`, `lockScreenRequested`,
`cancelSoftwareUpdateRequested`. Outside the greeter's binary those names exist only as UTF-16 literals, so
`Logout.qml` reads them through `typeof` and compares plain numbers; it therefore also loads where none of them is set.
If the file fails to compile the greeter logs `Trying default theme` and shows Breeze's screen — the test fails on
exactly that line (the first draft of this screen did fail that way: `font.pixelSize: 12.5` is not an int).

### What it shows

* **Title by mode** — "Shut down", "Restart", "Log out", "Leave" (all options) — with the line *"Fab OS asks each app to
  save its work before turning off / restarting / you sign out."* This is literally what happens (section 2), no more.
* **Open apps card** — one row per window: app icon and name, the window title underneath, an **Unsaved** chip when the
  title carries a modified marker, a header count, scrolling past five rows. The rows come from libtaskmanager's
  `TasksModel`, the same backend as the dock, inside the greeter process. Under Wayland KWin gives the window list only
  to a client whose `.desktop` entry (found through the sycoca, `Exec` equal to `/proc/<pid>/exe`) lists
  `org_kde_plasma_window_management` in `X-KDE-Wayland-Interfaces`; plasma-workspace ships no such entry for its greeter,
  so `fabos-desktop` does. **This is the real path**: proven inside a virtual `kwin_wayland` in the image
  (`tests/logout-screen-test.sh kwin`: two real windows listed, `draft.txt* — Test Editor` flagged) and in the VM
  (section 4). If the greeter still sees no windows 0.9 s after opening (no grant yet, an X11 session without the
  protocol, a stale sycoca right after the upgrade), `session-apps.sh` lists the running applications from the systemd
  user units Plasma launches them in (`app-<desktop id>@…service`, `app-<id>-<pid>.scope`; names and icons from their
  `.desktop` entries, instance counts, `@autostart` units skipped because they usually have no window) and the card
  says plainly that titles are not visible from there, so unsaved work cannot be flagged.
* **Unsaved work** — a window "looks unsaved" when its title carries the markers editors really produce: Qt's `[*]`
  placeholder rendered as a leading or trailing asterisk (`Untitled* — Kate`, `*notes.txt — Editor`, `main.c*`), KDE's
  `[modified]`, GTK's `(modified)`, the `●` / `•` bullets of VS Code and GNOME apps. Any flagged window shows the
  orange warning *"One window looks unsaved. Save your work first, or the app will ask you when it is closed. Nothing
  happens until you choose."* and **pauses the countdown**: the machine never turns itself off over work that looks
  unsaved. The heuristic is exactly that — a heuristic (section 3).
* **Countdown** — *"Shutting down in 30 s. Press any key to wait."* Default 30 s from `ksmserverrc [General]
  confirmLogoutCountdown` (read with `kreadconfig6`, so a per-user `~/.config/ksmserverrc` overrides the system file);
  `0` waits for a click. It never runs for the all-options grid, never while a window looks unsaved, never when the
  action is not permitted (`maysd`/`canLogout` false → a red line says so and only Cancel remains), and stops **for
  good** on any key press or a hover over the buttons: an approaching hand never loses the race. When it reaches zero it
  emits the same signal the primary button does.
* **Buttons** — Cancel and one big accent-filled primary ("Shut down now" / "Restart now" / "Log out now"; with pending
  offline updates "Update and shut down" plus "Shut down without updating", as Breeze offers). The primary has keyboard
  focus: Enter confirms, Escape cancels (also a click on the scrim outside the card). Ctrl+Alt+Del's all-options screen is
  a tile grid: Sleep / Hibernate (when `spdMethods` allows) / Restart / Shut down / Log out / Cancel, no countdown.
* **Other users signed in**, **restart into firmware / boot menu / entry** — the same hints Breeze shows, from the same
  context properties.
* **Footnote** — *"Ask before closing is on: Fab OS shows this screen every time. Fab Settings › Session › Desktop
  Session."*

Design tokens: card radius 24 with a 48 px shadow, inner apps card 20, controls 12, Inter through the system font,
colours from Kirigami's Complementary set so the accent follows the user's scheme, the scrim is the brand ink `#0E1116` at
86 %, 180/220 ms ease-out on open (Fab OS motion tokens). Real blur behind the card is not available to a look-and-feel
QML file (Breeze does not have it either); the scrim is a flat dark overlay.

### Settings

| key (`[General]` in `ksmserverrc`) | Fab OS default | Fab Settings › Session › Desktop Session |
|---|---|---|
| `confirmLogout` | `true` — Plasma's own key, read by libkworkspace on every request; Plasma also defaults to true, written so the behaviour is explicit | **Confirm logout** ("Ask before closing") |
| `confirmLogoutCountdown` | `30` — Fab OS key, seconds before the chosen action runs by itself; `0` = wait for a click | not in the KCM; `kwriteconfig6 --file ksmserverrc --group General --key confirmLogoutCountdown 0` |
| `loginMode` | `emptySession` — the low-RAM decision (docs/LOW-RAM.md): a 2 GB machine that reopens every app at login lands in swap before it is usable | **When logging in: Start with an empty session** (default) / **Restore previous session** / **Restore manually saved session** |

`loginMode` is a per-user choice like any other: the first time a user picks *Restore previous session* their
`~/.config/ksmserverrc` wins over `/etc/xdg/ksmserverrc`, and Fab OS never resets it.

## 2. What Plasma does after you confirm (the chain, verified)

1. **The greeter** emits the signal → libkworkspace calls **`org.kde.Shutdown`** (`plasma-shutdown`, D-Bus activated):
   `logoutAndShutdown`, `logoutAndReboot` or `logout`. Seen with `dbus-monitor` in `tests/logout-screen-test.sh`
   (`CALL member=logout destination=org.kde.Shutdown`); with libkworkspace's test backend the shutdown/reboot variants
   print `shutdown` / `reboot` instead.
2. **`plasma-shutdown`** asks **ksmserver** to `closeSession` (`org.kde.KSMServerInterface`). ksmserver is the X
   Session Management server (XSMP): X11 / XWayland clients that registered with it get `SaveYourself` with
   interaction allowed — an app may put up its own "save?" dialog and **cancel the whole logout** (ksmserver's
   *"Logout canceled by '%1'"*). Legacy X apps without XSMP get `WM_SAVE_YOURSELF`. Clients that never answer are
   given up on after ksmserver's timeouts (`clientShutdownTimeoutSecs`, `legacySaveTimeoutSecs`, `SmsDie timeout`).
3. **Wayland windows** are not XSMP clients. ksmserver asks KWin (`org.kde.KWin.Session`: `aboutToSaveSession`,
   `closeWaylandWindows`, `finishSaveSession`) to **close every Wayland window**: KWin sends each toplevel a close
   request, which is what makes Kate, Firefox, LibreOffice show their own "Save changes?" dialogs. KWin carries a
   `cancellogout` / *"Cancel Logout"* notification event for a window that refuses; a window that never responds gets
   KWin's *"Final ping timeout on a close attempt, asking to kill"* dialog. **What an app does with the close request
   is the app's business** — this is the only place where "save its work" actually happens, and it happens inside the
   app.
4. **Session save** (only with `loginMode=restorePreviousLogout`): ksmserver stores XSMP clients' restart commands; for
   Wayland `plasma-shutdown` runs `plasma-fallback-session-save`, which records the window list through
   `WindowTasksModel` (the same grant mechanism as our screen, via `org.kde.plasma-fallback-session-save.desktop`) so
   `plasma-fallback-session-restore` can **relaunch the `.desktop` entries** at the next login. It relaunches
   applications; it does not restore their documents — apps that restore their own state (Kate's sessions, Firefox's
   tabs) do that themselves. Fab OS defaults to `emptySession`, so none of this runs unless the user turns it on.
5. **`plasma-shutdown`** then stops the systemd user targets (`graphical-session.target`, `plasma-workspace.target`)
   and asks **systemd-logind** to power off / reboot. logind emits `PrepareForShutdown`; *delay* inhibitors (e.g.
   unattended-upgrades finishing a package install) may hold it for at most **`InhibitDelayMaxSec=30`** — already set
   in the image by `/usr/lib/systemd/logind.conf.d/unattended-upgrades-logind-maxdelay.conf`, so nothing to add. systemd
   then stops services, kills what is left, **syncs and unmounts the filesystems** (`systemd-shutdown` does this
   unconditionally: no "sync before shutdown" unit is needed — a manual `sync` would only duplicate it).

No shutdown inhibitor, autosave flusher or extra unit was added: everything above already exists, and a Fab OS process
cannot save an application's document for it. The one real gap — the user not knowing what is open — is what the leave
screen closes.

## 3. Guarantees and their limits (the honest list)

**Guaranteed by this change**

* The screen appears before every shut down / restart / log out (`confirmLogout=true` system default; a user can turn it
  off in Fab Settings and Fab OS respects that).
* Nothing happens for 30 s unless the user clicks or presses Enter; any key or a hover stops the clock for good.
* The countdown never runs while any window title carries a modified marker; the warning says what to do.
* The open windows are listed with icons, names and titles whenever KWin grants the window list (verified in the image
  and in the VM); otherwise the running applications are listed by name and the screen says titles are not visible.
* When the user confirms, every app is asked to close and may show its own save dialog (Plasma's existing behaviour).
* Disks are synced by systemd before power-off (existing behaviour).

**Not guaranteed — and why the text says "looks unsaved"**

* Apps that do not mark their titles are not flagged: browsers with a half-written form or an upload in progress,
  terminals with a running job, chat apps with a draft, Electron apps that use a colour instead of `●`, games, most
  media players. The heuristic can also miss a translated marker.
* The list shows windows. Background work without a window (a running `rsync` in a systemd unit, a transfer in a
  headless process, a build in a terminal that was minimised — the terminal *is* listed, the job is not) is invisible.
* "Fab OS asks each app to save its work" means Plasma sends the close request and the app decides. An app that ignores
  it is killed by KWin / ksmserver after their timeouts; an app that crashes on the request loses whatever it had not
  autosaved. Nothing in Fab OS can write an application's document for it.
* The all-options grid (Ctrl+Alt+Del) has no countdown by design, as in Breeze.
* Session restore is off by default on Fab OS (low RAM). Users who want their apps back at login choose *Restore previous
  session*; even then apps come back, documents only if the app restores them.
* The greeter's window-list grant depends on the sycoca knowing the new `.desktop` entry. KSycoca rebuilds itself when
  `/usr/share/applications` changes (that is how a newly installed app shows up in the launcher without logging out), so
  the first prompt after the 1.0-7 upgrade may still show the fallback list once; the VM proof rebuilt the cache
  explicitly (`kbuildsycoca6`) rather than waiting for that.
* Other users' sessions (the "another user is signed in" hint) get the same close requests from their own ksmserver only
  if their session is active; a switched-away session is killed by logind at power-off with no dialog anyone sees.

## 4. Verification

* **`tests/logout-screen-test.sh`** (host, podman, `localhost/fabos:vm`) — runs the REAL greeter offscreen on a private
  session bus that activates nothing, with libkworkspace's `PLASMA_SESSION_GUI_TEST` backend (`maysd=true` without
  logind), prompted over D-Bus like a session does. Steps: `soak` (four modes, no QML diagnostic / no Breeze fallback,
  3 s countdown fires the real action: `shutdown`/`reboot` at the backend, `org.kde.Shutdown.logout` on the bus, the
  all-options grid never counts down), `light` (wrapper forwards the 10 signals and the action), `kdeglobals`
  (package selected through `[KDE] LookAndFeelPackage`), `harness` (Driver.qml inside the greeter: contract, texts,
  layout, the unsaved heuristic on 15 titles, the systemd fallback through the real helper with a stub `systemctl`,
  `confirmLogoutCountdown=12` through `kreadconfig6`, paused / held states; renders `build/logout-shutdown.png`,
  `build/logout-unsaved.png`), `kwin` (the same harness as the session of a virtual `kwin_wayland` with two real
  windows and the package's `.desktop` grant: both listed, the modified one flagged; renders `build/logout-windows.png`).
  2026-09-16: PASS, 56 + 27 checks.
* **VM proof** (`build/r7-shutdown-safety/`, disposable overlay of `build/fabos-vm.img`, real autologin Plasma Wayland
  session): see the report for the run of 2026-09-16 — `logout-screen.png` (Firefox + Kate with an unsaved document
  listed by the real greeter), `logout-screen-light.png`, and the logout-cancel experiment (`logout-in-progress.png`,
  `desktop-after-app-cancel.png`, `journal-logout-cancel.log`).
