# Installer testing — how the automated installation works and how to read a failure

Three tests cover the Fab OS installer (Calamares 3.3.14 on the live ISO). None needs a human at the keyboard. The
installer is part of the ISO's **live system**: a change under `image/overlay/iso/etc/calamares/` reaches users with the
next ISO build only — never over the air — which is why the third test injects the working tree's files into a booted ISO.

| Test | What it proves | Needs | Time |
|---|---|---|---|
| `tests/calamares-jobs-test.sh [IMAGE]` | every configuration file is valid for **this** Calamares (schemas from the image, key lists from the 3.3.14 sources) and every job of the exec sequence can run **offline** in the target: apt removal + autoremove, every shellprocess line, every systemctl action, locale-gen, update-initramfs, GRUB/shim/efibootmgr and the effective GRUB defaults, cryptsetup-initramfs, sddm + theme + session, the users.conf groups; plus the live helper's log classifier and serial log transport, tied to the strings in the image's Calamares binaries | podman, `localhost/fabos:iso` | ~3 min |
| `tests/install-vm.sh [luks\|plain\|both]` | the ISO boots, Calamares is driven through all six pages on a blank 24 GB disk, finishes its whole job sequence and reaches its finished page, and the **installed disk boots on its own** (encrypted variant: the driver ticks *Encrypt system* and later types the passphrase at the Plymouth prompt; plain: it makes sure the box is unticked — both read the box's state from the screen first) up to `fabos-firstboot` | qemu-system-x86_64 + KVM, OVMF, podman (OCR), python3, >= 14 GB free under `build/` | ~20 min per variant on 2 vCPUs |
| `tests/installer-ui-vm.sh` | the **working tree's** Calamares configuration on the frozen ISO: boots the ISO, logs in on the live serial getty, screenshots the shipped installer (baseline), injects `branding.desc` + `partition.conf` through fw_cfg, restarts Calamares and proves by OCR that every sidebar step name is readable (welcome, partition, users pages), that *Encrypt system* starts unticked and that one click opts in; both session logs are checked for `Unknown branding *style* entry` | qemu + KVM, OVMF, podman (OCR), python3 + Pillow | ~8 min |

## 1. The offline job audit (`tests/calamares-jobs-test.sh`)

Runs one container from the ISO image with `--network none`, the working tree's `image/overlay/iso/etc/calamares` mounted
at `/cal`, and replays what each exec module does in the target chroot. Output is one `PASS`/`FAIL` line per check
(`build/calamares-jobs-test.out`); the last line is `### jobs-test: pass=N fail=M`. Configuration keys are validated with
`jsonschema` against the module schemas shipped in the image (`/usr/lib/x86_64-linux-gnu/calamares/modules/*/*.schema.yaml`)
when the host has the module, otherwise by a key/required check; C++ modules without an installed schema (partition,
users, welcome, initramfs, machineid, umount, shellprocess, finished, locale, keyboard) are checked against the key lists
read from their `Config.cpp`/`*Job.cpp` in Calamares 3.3.14.

What the audit found and fixed (2026-09-16, image `localhost/fabos:iso`, first run 145/147, third run 147/147; after the
review round — helper classifier, autoremove, effective GRUB values, marker strings — 165/165):

- **No default module configs on Ubuntu.** `calamares` from the Ubuntu archive installs no `*.conf` under
  `/usr/share/calamares/modules` or the module directories, so a module without a file in `/etc/calamares/modules` runs
  with every option off. `mount` therefore mounted no `/dev`, `/proc`, `/sys`, `/run`, efivarfs into the target
  (grub-install/efibootmgr/update-initramfs need them), `machineid` wrote no machine-id. Now shipped: `mount.conf`,
  `machineid.conf`, `umount.conf`.
- **services-systemd** used the pre-3.3 nested `units: {enable: [...], disable: [...]}` form; 3.3 wants one flat list of
  `{name, action, mandatory}` (iterating the map yields the words "enable"/"disable" as unit names). Rewritten.
- **fstab** carried keys that moved to `mount.conf` (`mountOptions`, `ssdExtraMountOptions`) and lacked the required
  `tmpOptions`. Rewritten; the EFI `umask=0077` and `noatime` options now live in `mount.conf`.
- **grubcfg** used `keepDistributor` (ignored; the key is `keep_distributor`) and its `defaults` block was never applied
  because the module only uses it for a *missing* `/etc/default/grub` unless `always_use_defaults: true`. Fixed — but note
  what is **authoritative**: `grub-mkconfig` sources `/etc/default/grub` and *then* `/etc/default/grub.d/*.cfg`, and
  `fabos-branding` ships `/etc/default/grub.d/fabos.cfg` (`GRUB_DISTRIBUTOR="Fab OS"`, `GRUB_CMDLINE_LINUX_DEFAULT="quiet
  splash"`, `GRUB_TIMEOUT_STYLE=hidden`, `GRUB_TIMEOUT=2`). For those four keys the snippet wins over whatever the module
  writes; the module's `cryptdevice=UUID=…:luks-… root=/dev/mapper/…` additions (mkinitcpio parameters, meaningless to
  initramfs-tools, which unlocks from `/etc/crypttab`) are therefore dropped harmlessly, and no `resume=` is ever computed
  because swap is a file. `grubcfg.conf` keeps the same values as the snippet so the two files never contradict each
  other; the audit replays the module's edit on a copy of `/etc/default/grub`, sources both files like `grub-mkconfig` and
  checks the effective result (`GRUB_TIMEOUT=2 … GRUB_CMDLINE_LINUX_DEFAULT=quiet splash`).
- **initramfs** had `initramfsName` (a dracut key); the module reads `kernel` and `be_unsafe`. Fixed.
- **bootloader**: `efiBootloaderId: fabos` -> `ubuntu`. Ubuntu's Secure-Boot-signed `grubx64.efi` carries the fixed
  prefix `/EFI/ubuntu` and no `$cmdpath` logic (both facts checked by reading the binary in the test), so it only finds
  its `grub.cfg` there. Deprecated `kernel/img/fallback/timeout` keys dropped.
- **partition**: the signed GRUB contains `cryptodisk` and `luks2` but **no `argon2`** module, while cryptsetup 2.8's
  default for LUKS2 is argon2id; an encrypted `/boot` (Calamares' default one-partition layout) would have produced a
  system GRUB cannot open. The layout is now ESP + `/boot` (2 GiB ext4, `noEncrypt: true`) + `/` (LUKS2 when encryption
  is ticked). Calamares then skips the boot keyfile (`luksbootkeyfile`: "/boot partition is not encrypted"), installs
  `encrypt_hook_nokey`, writes `none` as the crypttab key and leaves `GRUB_ENABLE_CRYPTODISK` unset (all from the 3.3.14
  sources). `initialPartitioningChoice: erase`; `preCheckEncryption: false` since 2026-09-17 (owner's decision: encryption
  is the user's choice — the box and its passphrase fields are offered, `enableLuksAutomatedPartitioning: true`, but start
  unticked); `xfs` removed from the offered filesystems (no `mkfs.xfs` in the image). See ADR-0021 and its 2026-09-17
  amendment.
- **branding** (owner's report, 2026-09-17: black sidebar, no step names): Calamares 3.3 resolves the `style:` keys of
  `branding.desc` by the name of its `Branding::StyleEntry` enum, so only `SidebarBackground`, `SidebarText`,
  `SidebarTextCurrent` and `SidebarBackgroundCurrent` exist. Ours had the 3.2 spellings (`sidebarText`,
  `sidebarTextSelect`, `sidebarTextHighlight`, `sidebarBackground`), and every 2026-09-16 session log says
  `WARNING: Unknown branding *style* entry` for all four; the colours then fell back to an invalid `QColor` = black — only
  the current step's background falls back to the window palette, which is why that one name was readable. Renamed. The
  audit checks the four names against the strings of the image's `libcalamaresui`, that the old names are unknown to it
  (and the warning text is in it), and that the image's default `branding.desc` spells them the same way;
  `tests/installer-ui-vm.sh` (section 3) proves the result on a live VM.
- **welcome**: `requiredRam: 4` contradicted the README's 2 GB minimum (a 2 GB machine would have been refused); now
  1.5 GiB. The internet check URL still pointed at the old `fabricos.` host; it is informative only and now the apt archive.
- **shellprocess**: every line guarded (`...; true`) and logging; the live-only `serial-getty@ttyS0` and
  `fabos-live-selftest` units are disabled; a second instance `shellprocess@efifallback` runs after `bootloader` and makes
  `EFI/boot/bootx64.efi` the signed shim (with `grubx64.efi` next to it) so firmware that ignores NVRAM entries still boots
  with Secure Boot on. Calamares runs each line through `/bin/sh -c`, so the guards work as written.
- **packages** stays `try_remove: [casper, calamares]` (the only live-only packages). The apt backend runs `apt-get
  --purge -q -y autoremove` right after the removal (`packages/main.py`, `PMApt.remove`), which also purges what only
  those two pulled in — in this image `calamares-data`, `libcalamares3.3`, `libcalamaresui3.3`, `libyaml-cpp0.8`,
  `libboost-python1.90.0`, `squashfs-tools`, `finalrd`, `user-setup`, `localechooser-data`, `lzma`. The audit replays both
  steps, prints that list, and checks that every name is `apt-mark auto` and none is something the installed system
  needs (cryptsetup, GRUB/shim, efibootmgr, plymouth, sddm, NetworkManager, fabos-*, kernel, initramfs-tools, systemd,
  Plasma/KWin — all `apt-mark manual` in the image).
- **live-autoinstall.sh** (found in review, 2026-09-16): the helper waited for `completion: succeeded` in the session
  log. The finished module (`Config::doNotify`) only logs that when it can reach `org.freedesktop.Notifications`; Calamares
  runs as root on the live user's session bus, and dbus-daemon refuses uid 0 there (replayed in the image: the bus started
  as `fabos` answers `fabos`, refuses root at once), so it always logs `Could not get dbus interface for notifications at
  end of installation.` instead — a good install would have been reported as `timeout` after 45 minutes. The helper now
  treats **any** of the three `doNotify` lines as "finished page reached" and a `ViewManager::onInstallationFailed` line
  (`Installation failed:` / `- message:`) before it as failure; the audit checks every one of those strings against the
  image's binaries and runs the classifier on synthetic logs.

**Rule learned on 2026-09-16 (ADR-0021 amendment):** a `shellprocess` line is expanded by Calamares itself (`$name`, `${name}` are Calamares variables), so the two lines are only the paths of shipped scripts under `/usr/lib/fabos/`; the audit fails on any `$` in such a line and replays the scripts, not a `sh -c` approximation of them.

## 2. The automated installation (`tests/install-vm.sh`)

```
tests/install-vm.sh luks          # encrypted (default)      tests/install-vm.sh plain     tests/install-vm.sh both
ISO=/path/to/fabos-1.0-desktop-amd64.iso tests/install-vm.sh both --cpus 2 --mem 2560 [--keep-disk]
```

### Stage 1 — live ISO, Calamares driven from the host

`scripts/boot-iso.sh --headless --autoinstall <variant> --install-disk build/install-target-<variant>.img --qmp ... --vars ...`
boots the ISO with a blank sparse 24 GB raw virtio disk and the firmware-config entry `opt/fabos/autoinstall` (`luks` or
`plain`). In the live session `live-selftest.sh` prints its usual markers and, because that entry exists, hands over to
**`/usr/lib/fabos/live-autoinstall.sh`**, which

1. waits for the live user's Plasma session, reads `WAYLAND_DISPLAY`/`DISPLAY`/`XAUTHORITY`/`XDG_RUNTIME_DIR` from
   `plasmashell`'s environment and prints `AUTOINSTALL_SESSION`, `AUTOINSTALL_DISK` (`vda 24G disk`) and
   `AUTOINSTALL_SQUASHFS` on the serial console;
2. launches `calamares -D6` as root on that display (what the desktop icon does through pkexec; the live user's
   `DBUS_SESSION_BUS_ADDRESS` is passed on too — root is refused there at once, which costs only the end-of-install
   desktop notification), prints `AUTOINSTALL_UI_READY` once `/root/.cache/calamares/session.log` exists (the log path is
   `Calamares::appLogDir()` = the running user's cache dir, verified in `libcalamares/utils/Logger.cpp`);
3. follows the session log: each `Starting job "..." ( n / m )` line is echoed as `CALAMARES_JOB: ...`; the run ends
   when the log shows **failure** — `ViewManager::onInstallationFailed` logs `Installation failed:` and `- message:`
   (checked first; they precede the failure dialog and the finished page) — or the **finished page**: any of the three
   lines `Config::doNotify` writes when `FinishedViewStep::onActivate` runs (`Sending notification of completion: …` when
   a notification daemon is reachable; `Could not get dbus interface for notifications at end of installation.` — the
   line root always gets here; `Notification not sent; completion: …` with `notifyOnFinished: false`), or when Calamares
   exits, or after 45 minutes. If Calamares exits without ever showing the finished page but after every job had started
   and nothing failed, the result is still `ok` with `finished_page=no`, so stage 2 runs and the finished-page verdict
   fails visibly instead of hiding a complete installation;
4. prints `INSTALL_RESULT=ok|failed|crashed|timeout variant=... seconds=... jobs=... finished_page=yes|no`,
   `AUTOINSTALL_JOBS started=n total=m` and `AUTOINSTALL_FINISHED_LINE: <the doNotify line>` (on failure also the failure
   lines and the last 40 log lines between `INSTALL_FAIL_TAIL_BEGIN/END`), then `LSBLK:` (partition table), `ESP:`
   (listing of the EFI system partition), copies the raw session log + Calamares' stderr into the ESP under
   `/fabos-install/` (`AUTOINSTALL_LOG_COPY`; readable from the host with `--keep-disk`), `LUKS <part>:` (`cryptsetup
   luksDump` header lines), then the session log **gzip-compressed and base64-encoded** between `CALAMARES_LOG_BEGIN
   encoding=gzip+base64 bytes=… gz=… sha256=…` and `CALAMARES_LOG_END` (a -D6 log of a few MB compresses to a few hundred
   kB; a compressed log above 800 kB is cut to its newest part, `kept=…`; the kernel's console loglevel is lowered to
   emergencies first so no printk lands inside the stream), `AUTOINSTALL_END`, and powers off. The driver decodes the
   block, verifies the sha256 and writes `session.log`; `guest-evidence.txt` records `SESSION_LOG_DECODE=ok|…`.

The **keystrokes come from the host**, not from `wtype` in the guest: KWin 6.6 implements no virtual-keyboard Wayland
protocol (`libkwin.so.6` contains no `virtual_keyboard` interface string; `wtype` exits 1 with "Compositor does not
support the virtual keyboard protocol"). `tests/install-vm-driver.py install` therefore talks to QEMU's QMP socket:

- `screendump` every few seconds -> OCR with **tesseract inside the ISO image** (`podman exec`, the host needs no OCR
  software) -> the page is recognised from its body text in Calamares 3.3.14 (`src/modules/*`), never from the sidebar:

  | page | text looked for | action |
  |---|---|---|
  | Welcome | `Welcome to the Fab OS ... installer` | `Alt+N` (`&Next`) |
  | Location | `Region:` / `Zone:` | `Alt+N` |
  | Keyboard | `Keyboard Model` | `Alt+N` |
  | Partitions | `Select storage device:` (+ `Erase disk` / `Encrypt system`) | first **read the state** of *Encrypt system* from the screendump (`encrypt_state()`: the `Passphrase` placeholder seen by OCR = ticked; else the pixels of the line-edit row ~210 px right of the label differ from the window background 45 px above it = ticked, identical = unticked; the indicator's dark check mark as a third signal); luks: click the label if unticked, then click the **Passphrase** field (OCR word box of the `EncryptWidget.ui` placeholder, excluding the `Confirm passphrase` one, else the row geometry), type `fabos-test`, `Tab`, type again; plain: click the label if ticked; re-read the state after each click (at most two); then `Alt+N`. This is what lets one driver install from the 1.0 ISO (box pre-ticked) and from the next one (opt-in) |
  | Users | `What is your name?` | focus is already in the full-name field (`UsersPage::onActivate` calls `textBoxFullName->setFocus()`); type `Fab Tester`, `Tab`, `fabtest`, `Tab` (hostname, auto-filled, kept), `Tab`, `fabos-test`, `Tab`, `fabos-test`; `Alt+N` |
  | Summary | `This is an overview of what will happen` | `Alt+I` (`&Install`) |
  | prompt | `Continue with Installation?` | `Alt+I` (`&Install Now`) |

  Field order is the widget order of `page_usersetup.ui` (textBoxFullName, textBoxLoginName, textBoxHostname,
  textBoxUserPassword, textBoxUserVerifiedPassword; the file has no `<tabstops>`, so Qt uses creation order) and of
  `EncryptWidget.ui` (m_encryptCheckBox, m_passphraseLineEdit, m_confirmLineEdit). `Alt+C` is **not** used for the checkbox:
  `En&crypt system` and the navigation bar's `&Cancel` share the mnemonic, so Qt would only move focus. Mouse clicks are
  QMP `input-send-event` absolute pointer events on the emulated USB tablet, at the coordinates OCR reported.
- Once `CALAMARES_JOB:` lines arrive, the driver stops touching the UI and waits for `INSTALL_RESULT=`, taking a screenshot
  every four minutes. Every screenshot (`NNN-<page>.png`, or `.ppm` without Pillow) and its OCR text (`NNN-<page>.txt`)
  is kept in `build/install-vm-<variant>/`; `session.log` there is the Calamares log copied out of the guest and
  `guest-evidence.txt` the partition table / ESP listing / LUKS header.

### Stage 2 — the installed disk boots alone

`scripts/boot-vm.sh --headless --install-disk build/install-target-<variant>.img --vars <the same OVMF store> --no-net ...`
boots the disk Calamares wrote, without the ISO and without any network device (so `fabos-firstboot.sh` gives up on
`nm-online` after 180 s and finishes offline, deterministically). The same OVMF variable store is reused so the firmware
boot entry `grub-install` registered is present (the `EFI/boot/bootx64.efi` fallback is there too). For the **luks**
variant the driver watches the screen for the Plymouth prompt (`Please unlock disk luks-...:`, drawn by the Fab OS theme's
`SetDisplayPasswordFunction`) and types the passphrase + Enter; if OCR recognised no prompt after 110 s it types blind
(the prompt accepts retries, and stray keystrokes into the login screen are harmless), at most four times. The stage
passes when the installed system prints **`FABOS_INSTALLED_OK`** on `ttyS0` — `fabos-firstboot.service` has
`ExecStartPost=/bin/sh -c "echo FABOS_INSTALLED_OK >/dev/ttyS0 2>/dev/null || true"`, a no-op on hardware without a serial
port — after which the VM is powered down over QMP.

### Reading a failure

`build/install-vm-<variant>.log` has the `PASS`/`FAIL` lines and the driver's timeline (`[  12.3s] page: users ...`).

| symptom | where to look |
|---|---|
| `FAIL install-x: live ISO booted` | `build/install-vm-x-serial-1.log` has no `FABOS_LIVE_OK`: the live medium itself did not come up (see `tests/iso-boot-test.sh`); `build/install-vm-x/boot-iso.out` for QEMU errors |
| `no AUTOINSTALL_UI_READY` | Calamares did not start: `INSTALL_FAIL_TAIL_*` in the serial log holds its stderr (`CALSTDERR|`), e.g. no display; `AUTOINSTALL_SESSION` shows which display variables were found |
| `stuck on page ... for 150 s` / `... not found by OCR` | the driver logs the OCR words it saw; open the last `NNN-poll.png` in `build/install-vm-x/` — the page is visible there |
| `INSTALL_RESULT=failed` | `guest-evidence.txt` ends with the failure lines and the last 40 session-log lines; `session.log` has the full `-D6` log — search for `- message:` (the dialog text) and the last `Starting job` line to see which module failed |
| `INSTALL_RESULT=timeout` | the guest deadline (45 min) passed without a failure or finished-page line; the last `CALAMARES_JOB:` line names the running job (unpackfs of the 8.8 GB rootfs is the long one); if `AUTOINSTALL_JOBS started=m total=m` shows every job started, look at the end of `session.log` for what the finished module did |
| `INSTALL_RESULT=ok … finished_page=no` | Calamares went away after the last job without showing its finished page; the disk evidence and stage 2 still run — read the end of `session.log` and `CALSTDERR|` lines for the crash |
| `FAIL … session log not received intact` | `SESSION_LOG_DECODE=` in `guest-evidence.txt` says why (`sha256-mismatch`, `gunzip-error`: something else wrote to ttyS0 during the transfer — `session.log.raw-serial` keeps the received block; `missing`: the guest never reached the dump). The raw log is also on the target disk's ESP under `/fabos-install/` (`--keep-disk`, then `guestmount`/`mount -o loop,offset=…`) |
| `FAIL install-x: installed disk boots` | `build/install-vm-x/*boot*.png` show what the screen looked like (GRUB shell, kernel panic, unlock prompt still waiting); `build/install-vm-x-serial-2.log` has kernel messages only if the installed system logs to ttyS0 (it does not by default), so the screenshots are the evidence |

### Budget and space

An install writes the whole 8.8 GB rootfs plus a 512 MiB swap file into the sparse disk image; the preflight refuses to
start with less than 14 GB free under `build/` and the image is deleted after each variant unless `--keep-disk`. On 2 vCPUs
expect ~15 min for stage 1 (unpackfs dominates) and ~5 min for stage 2 (180 s of that is the offline first-boot wait).
The session-log transfer over the emulated serial port is a few hundred kB (compressed) rather than the raw multi-MB log,
so it adds seconds, not minutes; the driver allows 600 s for it.

**OCR software.** `tesseract` 5.5.0 + the English data are Ubuntu packages (`tesseract-ocr`, `tesseract-ocr-eng`,
Apache-2.0) that are in the image as dependencies of `kde-spectacle` (its screenshot text recognition) — not a Fab OS
addition and nothing the user downloads; the test merely runs them inside the image with `podman exec`. The preflight
fails early if the image lacks them.

## 3. The installer UI proof (`tests/installer-ui-vm.sh`)

```
tests/installer-ui-vm.sh [--out DIR] [--image localhost/fabos:iso] [--mem MB] [--cpus N]     ISO=path/to/x.iso overrides the ISO
```

The ISO is frozen, so a change to `image/overlay/iso/etc/calamares/` cannot be seen by simply booting it — this test injects
the working tree's files into a running live session. One QEMU on the VM lock (`flock /tmp/fabos-vm.lock`), headless, with
a blank sparse 24 GB target disk (so the partition page has a device; deleted at the end) and **no** `--autoinstall`, so
nothing in the guest starts Calamares by itself:

1. waits for `FABOS_LIVE_OK` on the serial console, then logs in on the live ISO's **serial getty** (`console=ttyS0` is on
   the ISO's kernel line, so `serial-getty@ttyS0` runs there; the live user `fabos` has an empty password — `pam_unix
   nullok` — and NOPASSWD sudo). The serial port is a unix socket on the host side, so commands are typed and their output
   read there; the emulated keyboard and tablet only ever touch Calamares' own pages;
2. installs a guest script delivered through fw_cfg (`opt/fabos/ui.sh`) and runs its `baseline` phase: Calamares **as
   shipped in the ISO** is launched exactly as `live-autoinstall.sh` does it (root, on the live user's Wayland display,
   `-D6`). The host drives to the welcome and partition pages (`Alt+N`), screenshots them and OCRs the **sidebar region**
   (left ~15 % of the frame, upscaled 3x, in both polarities) for the eight step names — on the 1.0 ISO at most two are
   readable (the current step; the rest is black on black) and the *Encrypt system* box is ticked: the owner's report,
   reproduced. One click on the label must untick it (what the plain install driver does on that ISO);
3. `inject`: kills Calamares, copies the working tree's `branding.desc` and `partition.conf` (fw_cfg; sha256 compared with
   the local files) over `/etc/calamares` in the live overlay and starts Calamares again. Now every step name must be
   readable on the welcome, partition and users pages; *Encrypt system* must start unticked; one click must tick it and
   show the *Passphrase* / *Confirm passphrase* fields (row OCR); a second click must hide them again — and the install
   driver's `encrypt_state()` must agree with what OCR sees at each of these steps;
4. `stop`: the baseline session log must contain `Unknown branding *style* entry` lines, the fixed one none.

Evidence in `<out>/`: `NNN-baseline-*.png` and `NNN-fixed-*.png` (+ `.txt` OCR), `region-*-sidebar-*.png` (the sidebar crops
as OCR saw them, normal and inverted), `region-*-row-*.png` (the checkbox row), `guest.txt` (the guest script's output with
the warning lines), `serial.log`, `driver.log`. One `PASS`/`FAIL` line per check; exit 0 only when all passed.

## 4. What the tests deliberately do not cover

- Real hardware quirks (firmware that ignores NVRAM boot entries, NVMe namespaces, Optane RST): the layout follows
  Ubuntu's, the shim fallback is written, and `INSTALL_RESULT` + the ESP listing are the evidence to compare against.
- Manual partitioning, *Replace* and *Alongside*: those paths reuse the same modules; only *Erase disk* is automated.
- Typing into the live session with `wtype`: impossible on KWin 6.6 (above). If a future KWin adds the protocol, the
  driver can be switched to in-guest typing by teaching `live-autoinstall.sh` the same sequence; the page texts and field
  order documented above stay valid.
- Stage 2 has **screen evidence only**: the installed system's kernel command line carries no `console=ttyS0` (GRUB, not
  systemd-boot, so `boot-vm.sh --autotest`'s SMBIOS `kernel-cmdline-extra` string is not read), so the Plymouth unlock
  prompt never appears on the serial log and the passphrase is typed through QEMU's keyboard, guided by OCR of screendumps
  — blind after 110 s if the prompt was not recognised, retried every 90 s (at most four times) until the login screen shows
  the test user; stray keystrokes into the login screen are harmless. `FABOS_INSTALLED_OK` does arrive on the serial log
  (`/dev/ttyS0` exists without `console=`). If the blind typing ever proves unreliable, the alternative is to add
  `console=ttyS0` to the installed system's GRUB command line from the helper (it would change the system under test) and
  answer the prompt over a bidirectional serial chardev.
- The desktop notification at the end of the install (`notifyOnFinished`) is not observable in the test: Calamares runs
  as root and the live user's session bus refuses it. A `pkexec calamares` launch on real hardware has no session bus
  either — pkexec passes only a short whitelist of variables (the pkexec 127 binary in the image names `DISPLAY`,
  `XAUTHORITY`, `LANGUAGE`, `LINGUAS`, `COLORTERM`, `SHELL`… and contains neither `DBUS_SESSION_BUS_ADDRESS` nor
  `WAYLAND_DISPLAY`). The finished page itself, and its log line, are what the test checks.
