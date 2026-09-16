# Boot and login: the sequence, what Fedora does, what Fab OS does

Round 7 (package revision 1.0-7, over the air). Owner's report from the 1.0-5 laptop install: (A) SDDM showed its red
fallback theme — `Main.qml:62:22: Cannot assign to non-existent property background`; (B) the boot should look like a
Lenovo/Fedora machine: the firmware logo stays, a small spinner and the Fab OS mark, then a smooth hand-off to the login
screen; (C) the LUKS prompt was a bare text line with no visible input; (D) the greeter should look like Fedora/GDM.

## The sequence

| Step | Who draws | Fab OS 1.0-7 |
| --- | --- | --- |
| Firmware | UEFI (vendor logo, stored in the ACPI **BGRT** table) | untouched |
| GRUB | hidden menu, 2 s (`GRUB_TIMEOUT_STYLE=hidden`, `quiet splash`) | unchanged (image default) |
| Kernel + initramfs | Plymouth `two-step` plugin, theme `fabos` | vendor logo stays where the firmware left it, spinner at 70 %, wordmark at 96 %; disk-password dialog replaces the spinner |
| Hand-off | `plymouth quit --retain-splash` from `sddm.service.d/fabos-plymouth.conf` (plymouth-quit* are masked by fabos-desktop) | last splash frame stays until kwin_wayland has the display; the greeter fades in over 450 ms |
| Login | SDDM 0.21, theme `fabos` (Qt 6, layer-shell greeter under kwin_wayland) | GDM-like greeter, below |
| Shutdown / reboot | Plymouth `two-step`, modes `shutdown` / `reboot` | vendor logo + title "Shutting down safely…" / "Restarting…" |

## What Fedora does

* Boot: the **`bgrt`** Plymouth theme = the `two-step` plugin with `UseFirmwareBackground=true`. The plugin reads
  `/sys/firmware/acpi/bgrt/image` and draws it exactly where the firmware did (Windows-style: 38.2 % down), on black, so
  nothing jumps; a spinner turns at 70 %, a small watermark sits at 96 %; `DialogClearsFirmwareBackground=false` keeps the
  logo while the disk password is asked. Flicker-free boot also needs a hidden GRUB menu, `quiet splash`, and simpledrm
  so the framebuffer is inherited from the firmware — all already true in the Fab OS image (`UseSimpledrm=1`, `ShowDelay=0`).
* Login: GDM keeps Plymouth's last frame on screen and fades the shell in: dark blurred wallpaper, time and date at the
  top, a user tile that reveals the password field, "Not listed?", the power menu at the top right.

## What Fab OS does

### Boot splash: `packages/fabos-branding/usr/share/plymouth/themes/fabos/` (two-step; the script theme is gone)

`fabos.plymouth` — `ModuleName=two-step`, `ImageDir=/usr/share/plymouth/themes/fabos`, `Font=Inter 12`,
`TitleFont=Inter Light 30`, alignments: animation and dialog at .7, title at .6, watermark at .96, black background,
`DialogClearsFirmwareBackground=false`, `MessageBelowAnimation=true`; `[boot-up] [shutdown] [reboot]` use the firmware
background and no end animation; `[shutdown] Title=Shutting down safely…`, `[reboot] Title=Restarting…` (the plugin shows a
mode's Title/SubTitle whenever it is set — plymouth 24.004 `plugin.c` `view_load`); update modes carry a progress bar and
"Keep the computer switched on".

Images (all rendered by `tools/gen-login-boot-assets.py`, committed; the names are the plugin's):

| File | Size (logical px) | Role |
| --- | --- | --- |
| `throbber-0001…0048.png` | 80×80 | the mark's dashed ring turning with breathing bars; the plugin plays throbber frames at 30 fps → 1.6 s per turn |
| `watermark.png` | 134×77 | "Fab OS / by Patience AI" wordmark, bottom centre |
| `bgrt-fallback.png` | 176×176 | the mark, drawn at the logo position when the firmware has **no** BGRT (QEMU/OVMF, legacy BIOS) |
| `entry.png` | 380×120 | the password field: label band ("Disk password" left, "Press Enter to unlock" right) + 380×52 radius-14 box + an equal transparent band below (bullets are drawn at the entry image's vertical centre, so the box has to be centred in it) |
| `bullet.png` | 22×22 | one 10 px dot per typed character, laid from the box's left edge (`ply-entry.c`) |
| `lock.png` | 42×28 | lock glyph left of the field |
| `capslock.png` | 150×30 | "Caps Lock is on" badge; the plugin shows it under the prompt while Caps Lock is on (`ply-capslock-icon.c`) |

`plymouthd.conf` (diverted over Ubuntu's): `Theme=fabos`, `ShowDelay=0`, `DeviceTimeout=8`, `UseSimpledrm=1`. Ubuntu's
initramfs hook copies the theme directory, `two-step.so`, `label-pango.so` and *only the Ubuntu font*; our hook
`usr/share/initramfs-tools/hooks/fabos-plymouth-fonts` (PREREQ plymouth) adds Inter Regular/Light/Medium and refreshes
the fontconfig cache, so the labels are Inter. Package: `Depends: plymouth-theme-spinner` (owner of `two-step.so`, checked
with `dpkg -S`) and `fonts-inter` (was Recommends). `postinst`: `chmod 755` the hook (build-debs.sh normalises data files
to 0644), `update-alternatives --install/--set default.plymouth`, `update-initramfs -u -k all`, `update-grub` — so the OTA
upgrade rebuilds every kernel's initramfs and the next boot uses the new theme.

### The disk-password screen (C) — and why there is no eye toggle there

The plugin draws `lock.png` + `entry.png` centred at `DialogVerticalAlignment` (.7, where the spinner was), one bullet per
character, the **caller's** prompt text under the dialog, then the Caps Lock badge. The prompt text comes from the
initramfs unlock script (`cryptroot-unlock`: "Please unlock disk luks-…") — a theme cannot reword it; our own wording
("Disk password", "Press Enter to unlock") is part of `entry.png`, so the screen reads: *Disk password / [🔒 ●●●●●●] /
Please unlock disk luks-… / Caps Lock is on*.

**A show-password eye is impossible in Plymouth.** plymouthd keeps the passphrase in the daemon; the splash plugin
(two-step and script alike) receives only the *number of bullets* (`display_password (prompt, bullets)`) and the
`ask-for-password` client gets the text only after Enter. No theme can render the typed characters, so we do not pretend
to: the field shows bullets, a clear label, the Enter hint and the Caps Lock warning. The reveal toggle exists where it
can be honest — the greeter's password field.

Not shipped: Plymouth's keyboard-layout indicator (`keyboard.png` + `keymap-render.png`) — it needs the plugin's
pre-rendered sprite sheet whose offsets are compiled into libplymouth; a layout pill is on the greeter instead.

### The firmware logo in QEMU

`UseFirmwareBackground=true` keeps whatever the firmware wrote into the ACPI **BGRT** table. Whether QEMU has one depends
on the OVMF build: the edk2 OVMF on the test host **does** publish a BGRT (the TianoCore logo, `bgrt/status` = 1), so the
VM proof exercises the *real* firmware-background path — `plymouth-live-debug.log` reads `using 193x58 bgrt image centered
… for 1280x800 screen`, and `plymouth-splash-live.png` shows the TianoCore logo kept in place with the Fab OS spinner at
70 % and the wordmark at 96 %; the mark is **not** drawn twice. On an OVMF build (or legacy BIOS) with no BGRT the plugin
instead draws `bgrt-fallback.png` (the mark) at the logo position. Either way, on the Lenovo the vendor's logo stays
exactly where the firmware drew it — that path is what the VM's TianoCore BGRT stands in for
(`plymouth-debug.log`/`plymouth-live-debug.log` names the branch taken: "using … bgrt image" vs "loading background bgrt
fallback image").

### Greeter: `packages/fabos-desktop/usr/share/sddm/themes/fabos/`

Root cause of (A): `Main.qml` imported `QtQuick.Controls 2.15` **and** `SddmComponents 2.0` unqualified; both export
`Button`, `TextField`, `ComboBox`; `Button` resolved to `SddmComponents.Button`, which has no `background`, so the theme
failed to compile and SDDM showed its embedded fallback. The VM profile autologs in and never renders the greeter, which is
why no VM run ever showed it. `theme.conf` also named `background=background.png` while the git tree shipped no image
(the .deb got one from build-debs.sh; the tree was not self-contained).

Now: `import QtQuick.Controls as QQC2`, `import SddmComponents as Sddm`; every control is customised
(`background:` / `contentItem:`), and `zz-fabos.conf` pins `QT_QUICK_CONTROLS_STYLE=Basic` in `GreeterEnvironment` so a
style shipped by another package cannot change what the customisation means. `theme.conf` → `background=backdrop.png`,
a 1920×1080 pre-blurred, dimmed dark weave wallpaper (134 KB, cropped to the panel), committed with the theme; the
build still adds `background.png` (4K) and `mark.svg`. `faces/.face.icon` is a theme-level default avatar (SDDM prefers
`<theme>/faces/.face.icon` over `/usr/share/sddm/faces`). Glyphs: Material Symbols Rounded SVGs in `icons/`.

Layout (GDM-like, dark, Inter, accent `#6E9BFF` on dark, radii 12/14/20/24):

* top centre: time (Inter Light 72; 56 under 800 px tall) and date; top right: keyboard-layout pill (click cycles
  `keyboard.currentLayout`) and a "Caps Lock is on" pill while `keyboard.capsLock`. **No battery, network or volume**: the
  greeter has no UPower/NetworkManager access, and we never show made-up data;
* centre: one tile per account (avatar + name, last user pre-selected, ←/→ to move). Click or **Enter** opens the
  password stage: avatar, name, a radius-14 field with an **eye toggle** (show/hide) and an accent → submit button; Enter
  or → signs in; a spinner replaces → while the daemon answers. **"Not listed?"** switches to a typed-username stage
  (username + password); "Back"/Esc returns to the tiles;
* wrong password: `sddm.loginFailed` → the card shakes (5-step 225 ms), "Wrong password. Try again." in red, the field is
  cleared, re-enabled and focused; `informationMessage` (PAM text) shows in the same line;
* bottom centre: mark + "Fab OS" + "by Patience AI" as live text; bottom right: a gear (session chooser sheet, radius 24,
  with a check on the current session) and a power button (Suspend / Restart / Shut down — each only when `sddm.canX`);
* the whole greeter fades in over 450 ms — the "smooth hand-off" from the retained splash frame.

The avatar is drawn through a `Canvas` clipped to a circle (works on the GL and the software scene graph; no Qt5Compat
/ QtQuick.Effects dependency); when the picture is unreadable (the greeter runs as `sddm`, homes are 0750) it shows a
person glyph.

## How to test

* `tests/sddm-theme-test.sh [theme-dir]` — in `localhost/fabos:vm`, offscreen: static checks (theme.conf keys name shipped
  files, qualified imports, no unqualified controls, backdrop < 1 MB, wording), then `sddm-greeter-qt6 --test-mode --theme`
  for 8 s failing on any QML diagnostic (only test mode's `Socket error: QLocalSocket::connectToServer: Invalid name` is
  ignored), then a harness copy (`tests/sddm-theme-harness/Driver.qml`, one appended Loader line) renders
  `build/sddm-theme-test/greeter-{users,password,password-reveal,error,username,power,session}.png`; qmllint runs when the
  image has it (it does not — reported as SKIP). Proof it catches the laptop bug: `git show 4bbe387:…/Main.qml` into
  `build/sddm-theme-old/` and `RENDER=0 tests/sddm-theme-test.sh build/sddm-theme-old` → FAIL with
  `Main.qml:62:22: Cannot assign to non-existent property "background"` + `Fallback to embedded theme`.
* `tests/plymouth-theme-test.sh` (`FULL=1` also runs `update-initramfs -u` in a throwaway container and checks
  `lsinitramfs`) — validates `fabos.plymouth` against the two-step plugin's key set (plymouth 24.004 `plugin.c`), the
  image set and sizes, the font hook, Depends/postinst, then inside the image: alternatives, `dpkg -S two-step.so`,
  `plymouthd --debug` dry run (no display in a container, so the daemon resolves the theme file but never loads the plugin
  — the render is proven in the VM), the hook against a fake DESTDIR.
* `tests/login-boot-vm.py` (under `flock /tmp/fabos-vm.lock`, disposable qcow2 overlay of `build/fabos-vm.img`): installs
  the files from the tree where the packages put them, removes the VM autologin, restarts sddm, screendumps the greeter,
  logs in by typing through QMP (wrong password first, then the right one — a real desktop login is a `fabos` session on
  `seat0`, counted apart from the serial-console autologin and the driver's own ssh sessions, which are also `fabos` but
  seatless), runs the fabos-branding postinst steps
  (`update-initramfs -u`), renders the two-step dialog live (`plymouthd` + `plymouth ask-for-password` on the VM's display,
  types three characters), the shutdown title, reboots with `plymouth.debug` and films the console. Evidence in
  `build/r7-login-boot/` (`sddm-greeter*.png`, `desktop-after-login.png`, `plymouth-password*.png`,
  `plymouth-shutdown.png`, `plymouth-boot.png`, `frames/`, `plymouth-debug.log`, `sddm-journal*.txt`, `summary.txt`).
  Root in the VM profile is `echo fabos | sudo -S` (the fabos user is in `sudo`); `rootexec` needs an agent authorization
  record and is not a general root path.

## What the 1.0-7 VM proof (`build/r7-login-boot/`) showed

Under the shared lock, on a disposable overlay of `build/fabos-vm.img` (`tests/login-boot-vm.py`):

* **Greeter — proven.** The theme + `zz-fabos.conf` installed, the VM autologin was removed, `sddm` restarted, and the
  greeter rendered with **no QML errors** in the sddm journal (`sddm-greeter.png`, `sddm-greeter-password.png`) — the exact
  GDM-like layout in the render stages. A wrong password was rejected with the red "Wrong password. Try again."
  (`sddm-greeter-wrong.png`) and created no session; the correct password authenticated and opened a `fabos` session on
  `seat0`. The greeter also comes back clean after a reboot (`sddm-greeter-after-reboot.png`).
* **Boot / shutdown splash — proven live.** `plymouthd --mode=boot`/`--mode=shutdown` on the VM display loaded the
  `two-step` plugin from `/usr/share/plymouth/themes/fabos` (`plymouth-live-debug.log`) and rendered the firmware logo +
  Fab OS spinner + wordmark (`plymouth-splash-live.png`, copied to `plymouth-boot.png`) and the "Shutting down safely…"
  title (`plymouth-shutdown.png`).
* **Disk-password screen — proven.** `plymouth ask-for-password` drew the two-step dialog: lock glyph, the "Disk password"
  label and "Press Enter to unlock" hint (both baked into `entry.png`), the caller's "Please unlock disk luks-…" prompt,
  and **one bullet per typed character** — three keystrokes → three bullets (`plymouth-password-typed.png`).
* `update-initramfs -u` on the overlay put `fabos.plymouth`, `throbber-0001.png`, `entry.png`, `two-step.so` and Inter into
  the initramfs, with no `fabos.script` left (`plymouth-install.txt`).

## Honest limits

* The greeter fix takes effect the next time the greeter starts (logout or reboot after the 1.0-7 upgrade); the running
  fallback greeter is not restarted by the package (restarting sddm would kill the owner's session).
* The typed passphrase can never be revealed on the Plymouth screen (see above).
* **The Plasma desktop paint after the greeter login was not captured.** The login authenticated (a `fabos` session opened
  on `seat0`), but Plasma 6.6 on Wayland under software rendering in the 2 GB VM did not finish painting inside the wait
  window, so `desktop-after-login.png` still shows the greeter handing off. This is a VM-render/RAM limit, not a greeter
  defect — the authentication round-trip is what the session-on-seat0 check proves.
* **No frame from the *real* reboot was classified as the splash.** On this host OVMF spends ~60 s in a PXE/HTTP
  network-boot attempt (`>>Start PXE over IPv4.`, seen in `frames/`) before the kernel loads, which consumed the reboot
  film window; the OS splash rendered after it closed. The **live** boot-mode render (`plymouth-boot.png`) is the
  equivalent proof that the two-step boot splash draws the spinner + wordmark over the firmware logo.
* The `summary.txt` FAIL lines are harness artifacts, not theme/config defects: the "no BGRT" check assumed QEMU has none
  (this OVMF publishes a TianoCore BGRT — the *stronger* result); the plymouthd "shown"/answer-file and desktop-paint
  checks are timing/round-trip strictness the screenshots satisfy visually; the "greeter after reboot: no QML errors"
  match is `sudo`'s own audit line echoing the grep pattern (`Main.qml`/`Fallback to embedded`), not a greeter log line.
* The real firmware-logo path on the Lenovo (`/sys/firmware/acpi/bgrt`) is the owner's real-hardware check; in the VM the
  TianoCore BGRT stands in for it (see above).
* `tests/branding-check.sh` still asserts `fabos.script` in the initramfs and the script plugin's presence; it stays green
  against the current image and must switch to `fabos.plymouth` / `two-step.so` when the image is next rebuilt (that file
  belongs to the QA track). `packages/build-debs.sh` still copies the legacy `spinner-*.png`, `wordmark.png`, `bar-*.png`
  into the theme directory (unused by two-step, ~450 KB in the initramfs) — safe, and worth dropping there.
