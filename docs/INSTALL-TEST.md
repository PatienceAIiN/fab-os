# Installer testing — how the automated installation works and how to read a failure

Two tests cover the Fab OS installer (Calamares 3.3.14 on the live ISO). Neither needs a human at the keyboard.

| Test | What it proves | Needs | Time |
|---|---|---|---|
| `tests/calamares-jobs-test.sh [IMAGE]` | every configuration file is valid for **this** Calamares (schemas from the image, key lists from the 3.3.14 sources) and every job of the exec sequence can run **offline** in the target: apt removals, every shellprocess line, every systemctl action, locale-gen, update-initramfs, GRUB/shim/efibootmgr, cryptsetup-initramfs, sddm + theme + session, the users.conf groups | podman, `localhost/fabos:iso` | ~3 min |
| `tests/install-vm.sh [luks\|plain\|both]` | the ISO boots, Calamares is driven through all six pages on a blank 24 GB disk, finishes its whole job sequence, and the **installed disk boots on its own** (encrypted variant: passphrase typed at the Plymouth prompt) up to `fabos-firstboot` | qemu-system-x86_64 + KVM, OVMF, podman (OCR), python3, >= 14 GB free under `build/` | ~20 min per variant on 2 vCPUs |

## 1. The offline job audit (`tests/calamares-jobs-test.sh`)

Runs one container from the ISO image with `--network none`, the working tree's `image/overlay/iso/etc/calamares` mounted
at `/cal`, and replays what each exec module does in the target chroot. Output is one `PASS`/`FAIL` line per check
(`build/calamares-jobs-test.out`); the last line is `### jobs-test: pass=N fail=M`. Configuration keys are validated with
`jsonschema` against the module schemas shipped in the image (`/usr/lib/x86_64-linux-gnu/calamares/modules/*/*.schema.yaml`)
when the host has the module, otherwise by a key/required check; C++ modules without an installed schema (partition,
users, welcome, initramfs, machineid, umount, shellprocess, finished, locale, keyboard) are checked against the key lists
read from their `Config.cpp`/`*Job.cpp` in Calamares 3.3.14.

What the audit found and fixed (2026-09-16, image `localhost/fabos:iso`, first run 145/147, third run 147/147):

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
  because the module only uses it for a *missing* `/etc/default/grub` unless `always_use_defaults: true`. Fixed.
- **initramfs** had `initramfsName` (a dracut key); the module reads `kernel` and `be_unsafe`. Fixed.
- **bootloader**: `efiBootloaderId: fabos` -> `ubuntu`. Ubuntu's Secure-Boot-signed `grubx64.efi` carries the fixed
  prefix `/EFI/ubuntu` and no `$cmdpath` logic (both facts checked by reading the binary in the test), so it only finds
  its `grub.cfg` there. Deprecated `kernel/img/fallback/timeout` keys dropped.
- **partition**: the signed GRUB contains `cryptodisk` and `luks2` but **no `argon2`** module, while cryptsetup 2.8's
  default for LUKS2 is argon2id; an encrypted `/boot` (Calamares' default one-partition layout) would have produced a
  system GRUB cannot open. The layout is now ESP + `/boot` (2 GiB ext4, `noEncrypt: true`) + `/` (LUKS2 when encryption
  is ticked). Calamares then skips the boot keyfile (`luksbootkeyfile`: "/boot partition is not encrypted"), installs
  `encrypt_hook_nokey`, writes `none` as the crypttab key and leaves `GRUB_ENABLE_CRYPTODISK` unset (all from the 3.3.14
  sources). `initialPartitioningChoice: erase`, `preCheckEncryption: true`; `xfs` removed from the offered filesystems
  (no `mkfs.xfs` in the image). See ADR-0021.
- **welcome**: `requiredRam: 4` contradicted the README's 2 GB minimum (a 2 GB machine would have been refused); now
  1.5 GiB. The internet check URL still pointed at the old `fabricos.` host; it is informative only and now the apt archive.
- **shellprocess**: every line guarded (`...; true`) and logging; the live-only `serial-getty@ttyS0` and
  `fabos-live-selftest` units are disabled; a second instance `shellprocess@efifallback` runs after `bootloader` and makes
  `EFI/boot/bootx64.efi` the signed shim (with `grubx64.efi` next to it) so firmware that ignores NVRAM entries still boots
  with Secure Boot on. Calamares runs each line through `/bin/sh -c`, so the guards work as written.
- **packages** stays `try_remove: [casper, calamares]` (the only live-only packages; the dry run shows nothing else
  removed).

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
2. launches `calamares -D6` as root on that display (what the desktop icon does through pkexec), prints
   `AUTOINSTALL_UI_READY` once `/root/.cache/calamares/session.log` exists (the log path is
   `Calamares::appLogDir()` = the running user's cache dir, verified in `libcalamares/utils/Logger.cpp`);
3. follows the session log: each `Starting job "..." ( n / m )` line is echoed as `CALAMARES_JOB: ...`; the run ends
   when the finished module logs `Sending notification of completion: succeeded|failed` (it does so because
   `finished.conf` has `notifyOnFinished: true`) or `ViewManager::onInstallationFailed` logs `- message:`;
4. prints `INSTALL_RESULT=ok|failed|crashed|timeout variant=... seconds=... jobs=...` (on failure also the last 40 log
   lines between `INSTALL_FAIL_TAIL_BEGIN/END`), then `LSBLK:` (partition table), `ESP:` (listing of the EFI system
   partition), `LUKS <part>:` (`cryptsetup luksDump` header lines), the whole session log between
   `CALAMARES_LOG_BEGIN/END`, `AUTOINSTALL_END`, and powers off.

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
  | Partitions | `Select storage device:` (+ `Erase disk` / `Encrypt system`) | luks: click the **Passphrase** field (OCR word box of the `EncryptWidget.ui` placeholder, excluding the `Confirm passphrase` one), type `fabos-test`, `Tab`, type again; plain: click **Encrypt system** to untick it, verify the passphrase boxes disappeared; then `Alt+N` |
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
| `INSTALL_RESULT=failed` | `guest-evidence.txt` ends with the last 40 session-log lines; `session.log` has the full `-D6` log — search for `- message:` (the dialog text) and the last `Starting job` line to see which module failed |
| `INSTALL_RESULT=timeout` | the guest deadline (45 min) passed; the last `CALAMARES_JOB:` line names the running job (unpackfs of the 8.8 GB rootfs is the long one) |
| `FAIL install-x: installed disk boots` | `build/install-vm-x/*boot*.png` show what the screen looked like (GRUB shell, kernel panic, unlock prompt still waiting); `build/install-vm-x-serial-2.log` has kernel messages only if the installed system logs to ttyS0 (it does not by default), so the screenshots are the evidence |

### Budget and space

An install writes the whole 8.8 GB rootfs plus a 512 MiB swap file into the sparse disk image; the preflight refuses to
start with less than 14 GB free under `build/` and the image is deleted after each variant unless `--keep-disk`. On 2 vCPUs
expect ~15 min for stage 1 (unpackfs dominates) and ~5 min for stage 2 (180 s of that is the offline first-boot wait).

## 3. What the tests deliberately do not cover

- Real hardware quirks (firmware that ignores NVRAM boot entries, NVMe namespaces, Optane RST): the layout follows
  Ubuntu's, the shim fallback is written, and `INSTALL_RESULT` + the ESP listing are the evidence to compare against.
- Manual partitioning, *Replace* and *Alongside*: those paths reuse the same modules; only *Erase disk* is automated.
- Typing into the live session with `wtype`: impossible on KWin 6.6 (above). If a future KWin adds the protocol, the
  driver can be switched to in-guest typing by teaching `live-autoinstall.sh` the same sequence; the page texts and field
  order documented above stay valid.
