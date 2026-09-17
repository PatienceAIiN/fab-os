# Laptop hardware on Fab OS: fingerprint, camera, microphone

Fab OS 1.0-8 (over-the-air update, package `fabos-hardware`, pulled in by `fabos-desktop-meta`) adds the
fingerprint, camera and microphone integration a laptop owner expects. This page says what is supported, what you
should see on a real machine, how it is wired, and — honestly — what the QEMU test VM could and could not prove.

## What you get

| Area | Packages (Ubuntu 26.04 "resolute") | Where it shows up |
|---|---|---|
| Fingerprint reader | `fprintd`, `libpam-fprintd`, `libfprint-2-2`, `libfprint-2-tod1` | Fab Settings › Users (enrol), login screen, lock screen, `sudo`, authorisation prompts |
| Camera | `libcamera0.7` + `libspa-0.2-libcamera` (PipeWire camera plugin), `gstreamer1.0-pipewire`, `gstreamer1.0-libcamera`, `v4l-utils`, **Fab Camera** (`plasma-camera`) | Fab Camera in the launcher; the camera portal for Flatpak / browser apps; the camera privacy glyph in the top bar |
| Microphone | PipeWire + WirePlumber (already on every Fab OS) | Quick settings: **Microphone** row (input volume, mute); microphone privacy glyph in the top bar |

### Fingerprint

* **Enrolment**: Fab Settings › Users › your user › *Configure Fingerprint Authentication*. The page comes from KDE's
  Users module; the button appears as soon as `fprintd` is installed (it talks to fprintd over D-Bus). With no reader
  the button opens a dialog that says no device was found — it does not crash.
* **Sign in**: the login screen, the lock screen, `sudo` and authorisation prompts (polkit) all take **a fingerprint OR
  the password**. Never the fingerprint alone: `fabos-hardware`'s postinst enables the `fprintd` profile of
  `pam-auth-update` and keeps the `unix` (password) profile, so `/etc/pam.d/common-auth` reads
  `pam_fprintd.so max-tries=1 timeout=10` followed by `pam_unix.so`. The lock screen additionally runs KDE's
  parallel fingerprint conversation (`/usr/lib/pam.d/kde-fingerprint`), so the unlock field says a finger is accepted
  while you can just type.
* **What to expect with a reader present**: touch the reader → you are in. Type the password instead → the password
  is checked after the fingerprint attempt gives up (`timeout=10`: at most ten seconds; a second touch attempt is not
  offered in the same prompt — `max-tries=1`). The same ten-second rule applies to `sudo` in a terminal ("Place your
  finger on the fingerprint reader" is printed first). This is Ubuntu's standard profile; it is the trade-off for a
  stack that every PAM user shares.
* **Drivers**: libfprint's in-tree drivers cover most Validity/Synaptics/Elan/Goodix/UPEK/AuthenTec readers of the last
  decade; `libfprint-2-tod1` loads the vendor "TOD" drivers that some Lenovo/Dell readers need (the vendor ships those
  separately, e.g. through `fwupd`/LVFS or the OEM archive; Fab OS does not bundle proprietary drivers). Check with
  `fprintd-list $USER` and `journalctl -u fprintd`.

### Camera

* **Fab Camera** (Plasma Camera under its Fab OS name — the desktop entry is overridden and the window title reads
  "Fab OS Camera"; the program and its About dialog are unchanged, KDE is credited in ATTRIBUTIONS.md) takes photos
  and records video from the built-in or a USB webcam on Wayland. It finds cameras through **libcamera** (GStreamer
  `libcamerasrc` / PipeWire): every UVC webcam — which is what laptops have — is a libcamera device; the kernel's
  `vivid` test driver used in the QEMU checks is not, so there Fab Camera correctly says "Camera not available" while
  `v4l2-ctl` and PipeWire do list the virtual device.
* **PipeWire camera**: `libspa-0.2-libcamera` lets PipeWire offer the camera to the camera portal
  (`xdg-desktop-portal`), so Flatpak apps and browsers using the portal see the camera without direct `/dev/video*`
  access. `v4l2-ctl --list-devices` (v4l-utils) lists what the kernel sees.
* **Privacy glyph**: while any app films (a running PipeWire video capture stream, or one of your processes holding a
  `/dev/video*` handle) a camera glyph appears at the right of the top bar; its tooltip says how many apps and, when
  PipeWire knows them, their names.

### Microphone

* **Quick settings › Microphone**: a full-width row under Volume — glyph (click = mute/unmute), slider = input volume
  of the default PipeWire source, percentage, chevron to the full audio applet. It hides itself on a machine without a
  microphone. Wheel over the bar glyph adjusts the level in 5 % steps. Commands issued: `wpctl set-volume
  @DEFAULT_AUDIO_SOURCE@ <0.00–1.00>` and `wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle` — the same source Fab Settings
  › Sound calls the default input.
* **Privacy glyph**: a microphone glyph shows in the top bar while the microphone is muted (like the speaker glyph),
  for three seconds after a change, and whenever an app records (a running PipeWire audio capture stream). The
  tooltip names the recording apps as PipeWire knows them. Fab OS's own wake-word listener (Fab Voice) records through
  `pw-record` and is listed as such — a permanent glyph while it listens is correct, not a bug; turn the listener off
  in Fab AI Controls if you would rather not have it.
* Bluetooth headsets: pairing stays in the Bluetooth tile / Fab Settings › Bluetooth; once connected, WirePlumber makes
  the headset the default source and the Microphone row follows it.

## Permissions and hints

* Camera and microphone access needs no group membership: PipeWire runs in your session and `/dev/video*` is
  `video`-group + ACL for the logged-in seat (systemd-logind `uaccess`), so Fab Camera works out of the box.
* Flatpak apps ask through the portal; the first request shows the portal's permission dialog. Browsers list "Fab OS"
  cameras/microphones under the names PipeWire reports.
* The privacy glyphs are informational; they do not block a device. To stop an app, close it (or mute the microphone).

## How this was verified — and what it could not prove

Everything above was exercised on the QEMU test VM (`tests/hardware-vm.sh`, `tests/rebrand-sweep-vm.sh`) with the
1.0-8 packages installed the over-the-air way on a 1.0-6 disk:

* the packages install from the Ubuntu archive with no errors and `fabos-hardware`'s postinst leaves
  `common-auth` with `pam_fprintd` before `pam_unix`;
* `fprintd` starts, answers `GetDevices` with an empty array and `fprintd-list` with "No devices available" — no
  error;
* Fab Settings › Users opens with fprintd present and stays up (no enrolment button: KDE shows it only when fprintd
  reports a reader); Fab Camera starts and stays up without a camera ("Camera not available"); the kernel's `vivid`
  test driver gave `v4l2-ctl` and PipeWire a virtual `/dev/video0` to list, which libcamera (and so Fab Camera) does
  not treat as a camera;
* the Microphone row's exact `wpctl` commands move the VM's emulated microphone (Intel HDA `hda-micro`) and the probe
  follows them; a `pw-record` process shows as "1 app is using the microphone".

**Not provable in QEMU — please check on the laptop**: there is **no fingerprint reader and no real camera** in the
VM. Detection of a reader (`fprintd-list`), enrolment, the login/lock/sudo fingerprint prompts and the ten-second
password fallback, real webcam capture in Fab Camera, and the camera portal with a real device are proven only
structurally here (packages, PAM stack, D-Bus service, app start-up). A laptop owner should see: the *Configure
Fingerprint Authentication* button in Fab Settings › Users listing the reader, enrolment of a finger, a fingerprint
hint on the lock screen, and Fab Camera showing the webcam picture. If any of those is missing, `fprintd-list $USER`,
`journalctl -u fprintd -b` and `v4l2-ctl --list-devices` are the first things to send with Fab Feedback.
