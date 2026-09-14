# Fab OS on 2 GB of RAM

Fab OS is tuned so that a laptop with **2 GB of RAM** runs the full desktop — animations, rounded translucent
surfaces, the agent — without a degraded "lite" mode. The system stops spending memory on things a desktop user never
sees, and (since 1.0-3) stops spending CPU on polling and looping animation nobody is looking at — see "Idle budget"
below. The one visual concession is blur, which is off on machines under 3.5 GB. Everything below ships in
`fabos-desktop` (configuration) and the `image/Containerfile` low-RAM layer (packages), and every default can be changed
by the user.

## What is tuned

| Area | Default on Fab OS | Where | Why |
|---|---|---|---|
| **Swap on zram** | `/dev/zram0`, `min(RAM/2, 4 GiB)`, zstd, priority 100 | `/etc/systemd/zram-generator.conf` (package `systemd-zram-generator`) | Compressed swap in RAM at ~3:1 gives a 2 GB machine roughly 4 GB of effective working set with no disk I/O. The generator creates `dev-zram0.swap` at boot; there is no service to enable. |
| **VM sysctls** | `vm.swappiness=100`, `vm.vfs_cache_pressure=50`, `vm.page-cluster=0` | `/etc/sysctl.d/60-fabos-lowram.conf` | Swapping to zram is cheap, so let the kernel use it instead of dropping file cache; keep dentry/inode caches; read single pages from zram (it is RAM, not a disk). |
| **systemd-oomd** | enabled; pressure limit **70 %** for 20 s (Ubuntu: 60 % / 50 % for sessions) | `/etc/systemd/oomd.conf.d/fabos.conf`, `/etc/systemd/system/user@.service.d/90-fabos-oomd.conf` | Ends the largest offending process group before the kernel OOM killer freezes the machine, but tolerates the moderate pressure that is normal with zram so the browser is not killed prematurely. |
| **File-content indexing off** | Baloo `Indexing-Enabled=false`; when turned on, names only | `/etc/xdg/baloofilerc` | `baloo_file` + `baloo_file_extractor` are the largest idle consumers on a fresh Plasma install (tens of MB idle, hundreds while indexing). Fab Search still finds applications, settings, commands, recent documents and the Fab OS agent. |
| **Empty session at login** | `loginMode=emptySession` | `/etc/xdg/ksmserverrc` | Reopening every application from the previous session puts a 2 GB machine straight into swap before it is usable. |
| **Printing on demand** | `cups.service` disabled, `cups.socket` + `cups.path` enabled; `cups-browsed` disabled | `fabos-desktop` postinst (first install only) | cupsd starts when something opens `/run/cups/cups.sock` or spools a job and exits after 60 s idle (`IdleExitTimeout`). Driverless network printers are still discovered by cupsd and the print dialog themselves; `cups-browsed` only polled for legacy queues. |
| **smartd off** | `smartmontools.service` disabled | postinst (first install only) | It only mails root. Disk-health warnings come from `plasma-disks` (Fab Settings / Fab System Info) on demand. |
| **motd-news off** | `motd-news.timer` disabled | postinst (first install only) | No RAM saved; removes a Canonical news fetch that had nothing to display anyway. |
| **Welcome wizard** | exits in the shell wrapper once done | `/usr/bin/fabos-welcome` | It is autostarted at every login; after the first run it no longer starts Python/Qt (~37 MB, measured) just to notice it has nothing to do. |
| **Local AI daemon (`aiosd`)** | not started at login (opt-in: `systemctl --user enable --now aiosd`) | `fabos-ai` | Measured at 7.6 MB RSS while idle and it loads no model until asked, but it is only useful when a local model is configured, so it stays off until the user turns System-Wide local AI on. |
| **Fab OS agent (`fabos-agentd`)** | started at login | `fabos-agent` | Measured at 7.5 MB RSS after start: standard library only; the Anthropic SDK and other provider code are imported on first use, not at start. |
| **Blur off under 3.5 GB** | `[Plugins] blurEnabled=false` written once into the user's `kwinrc` when `MemTotal` < 3 500 000 kB | `/usr/lib/fabos/lowram-tune`, run from `/etc/xdg/plasma-workspace/env/40-fabos-lowram.sh` before KWin starts | Blur is the one effect whose cost scales with the screen and runs on every frame behind translucent surfaces; on the integrated GPUs of 2 GB laptops it is the difference between a smooth and a stuttering panel slide. A marker (`~/.config/fabos/lowram-tune-done-v1`) makes it a one-time default, so turning blur back on in Fab Settings sticks. A nominal 4 GB machine reports ~3.8–3.9 GB and keeps blur. |
| **Half-length animations** | `[KDE] AnimationDurationFactor=0.5` | `/etc/xdg/kdeglobals` | Every Plasma/KWin/Kirigami animation at half its stock duration (see docs/design/MOTION_GUIDELINES.md); fewer frames per transition on a slow GPU, and switching feels immediate. Settings > Animations moves the same slider. |
| **No tearing** | `[Compositing] AllowTearing=false` | `/etc/xdg/kwinrc` | On weak integrated GPUs a torn update reads as a stutter; a steady vsynced frame reads as smooth. (`LatencyPolicy` no longer exists in KWin 6.6: it is neither in `/usr/share/config.kcfg/kwin.kcfg` nor in `libkwin.so.6.6.6`, the render time is estimated automatically — so nothing is set for it.) |
| **File Search runner off** | `baloosearchEnabled=false` | `/etc/xdg/krunnerrc` | It is a D-Bus runner (`org.kde.runners.baloo` → `baloorunner`) activated for every Fab Search query; with the index off it could only answer "nothing". "Recent documents" stays: no index of its own. |
| **Voice listener in the background** | `Nice=15`, `IOSchedulingClass=idle`; no spotter without a microphone | `fabos-voiced.service` | The always-on `pw-record | pocketsphinx` pair never takes a time slice from the compositor, the shell or the foreground app; without a capture device no audio process runs at all (re-checked every 30 s). |

What is **not** changed: KWin effects other than blur on small machines (slide, magic lamp, overview, translucency,
fade, scale), the Plasma theme, fonts, icon theme, the Discover update notifier, NetworkManager, PipeWire, SDDM,
PackageKit, unattended-upgrades, ModemManager (some laptops have WWAN), Avahi (printer discovery and `.local` names),
Bluetooth. The task switcher keeps `thumbnail_grid` — it is the only layout in the image (`/usr/share/kwin-wayland/tabbox/`;
`kwin-addons` with `compact` is not installed).

## Idle budget

A desktop that feels laggy on 2 GB is rarely short of memory alone: it is busy. Two things make a Plasma session busy
while nobody touches it — **processes being created** (every `executable` DataSource call in a plasmoid runs
`/bin/sh -c`, which forks the shell, each tool in the command and each tool's threads; on a 2 GB machine every
fork+exec is 5–15 ms of CPU plus page-cache churn) and **frames being drawn** (looping animations and tickers repaint
whether or not anyone looks). This is what the Fab OS components did per second before this round, measured in the
image with `/proc/stat processes` (counts forks **and** threads: "tasks") and `bash time` (CPU):

| Component | Before (per second, idle desktop) | After |
|---|---|---|
| Ask bar (`in.patienceai.fabos.askbar`) | `GET /status` every 4 s through `sh` + `cat` + `cat` + `printf\|curl` = **7 tasks per call** → 1.75 tasks/s idle; while a task was followed a second call every 1.5 s → **6.4 tasks/s**, ~17 ms CPU per call | **One** curl fetches `/status` and `/tasks/{id}` together (`read` builtins for port/token, `-w '\n'` separates the bodies): **4 tasks per snapshot**. 2 s while a task is followed (2 tasks/s), 8 s while the bar is awake (0.5/s), **nothing** once the mark has slept for 30 s; a hover, focus, click or task wakes it and snapshots at once |
| Quick settings (`in.patienceai.fabos.quicksettings`) | `status.sh` every 10 s = **166 tasks per run** (a subshell per `$(...)`, `timeout` around every tool, grep/awk/head/sed pipelines, `powerprofilesctl` = a Python interpreter costing ~110 ms CPU, `bluetoothctl` = 10 tasks per call) + `/proc/net` every 2 s (5 tasks) → **≈ 19 tasks/s** | One script, one JSON line. Pane closed: the `--light` probe (sh + one awk = **2 tasks**, 3 ms) every 5 s reads the kernel's net counters, Wi-Fi link quality, battery and backlight; the full probe (sh, `nmcli` 1–2×, `busctl` 2–3× for BlueZ and the power profile, `wpctl`, awk ≈ 16–25 tasks) every 30 s. → **≈ 1 task/s** closed; pane open: full probe every 2 s (short-lived) |
| Dock (`in.patienceai.fabos.dock`) | libtaskmanager `TasksModel`, event-driven; magnify = one `Behavior`; startup pulse looped **for ever** while `IsStartup` | unchanged model; the startup pulse runs 4 times and stops; 0 tasks/s, 0 frames idle |
| Fab AI Controls (`command_center.py`) | 3 HTTP GETs every 4 s + one GET per active/changed task every 1.5 s **on the GUI thread** (`urllib`, 5 s timeout): a slow daemon reply froze the window; polls could stack | the same cadence on **one worker thread** (`ApiQueue`), results applied in place by callbacks; a poll still queued is replaced, never stacked; 6 s / 12 s while the window is hidden or minimised; the typing dots tick at 60 ms and the busy arc at 50 ms (were 40 / 30) and only while shown. 0 tasks/s (no processes at all) |
| `fabos-agentd` | HTTP server thread + `Watcher` thread sleeping 20 s + one thread per running task; 0 tasks/s idle | unchanged (not part of this round) |
| `fabos-voiced` | `pw-record \| pocketsphinx` always on when a microphone exists (the wake word), `Nice=10` | same pipeline at `Nice=15` + `IOSchedulingClass=idle`; without a capture device nothing runs but one `pactl list short sources` every 30 s |
| Plasma / KWin | stock `AnimationDurationFactor=1`, tearing allowed, blur on everywhere, File Search runner activated per query | factor 0.5, `AllowTearing=false`, blur off under 3.5 GB, File Search runner off |

Total task creation from Fab OS components on an idle desktop: **≈ 21 tasks/s before, ≈ 1 task/s after** (the quick
settings' light probe; the ask bar contributes 0 once asleep, 0.5/s while awake). The numbers are re-measured in the
booted 2 GB VM by `tests/perf-vm.sh` (CPU of the components over 20 s, system-wide task creations per second, the
Alt+Tab latency through KWin's own handler, and a repaint check of Fab AI Controls while a task streams); its thresholds
are 8 % of one core, 1 task/s and a 150 ms median switch.

## Expected effect

Estimates from the maintainers' own measurements and experience, not a benchmark of every machine:
- idle RSS not spent at login: Baloo ~30–40 MB (idle; far more while indexing), cups-browsed ~10–15 MB, cupsd ~8 MB,
  smartd ~4 MB, Welcome wizard ~37 MB transient at every login (measured);
- zram adds ~1 GiB of compressed swap on a 2 GB machine, typically holding 2–3 GiB of pages;
- systemd-oomd keeps the compositor and panel responsive when a browser tab runs away.

## Changing the defaults

- **File Search**: Fab Settings → Search → File Search → "Enable File Search" (and, optionally, "Also index file
  content"). The per-user `~/.config/baloofilerc` overrides the system default.
- **Session restore**: Fab Settings → Session → Desktop Session → "When logging in".
- **zram**: edit `/etc/systemd/zram-generator.conf` (`zram-size`, `compression-algorithm`), or remove the `[zram0]`
  section to disable; `systemd.zram=0` on the kernel command line disables it for one boot. See `zram-generator.conf(5)`.
- **Swappiness and friends**: drop a file after `60-fabos-lowram.conf` in `/etc/sysctl.d/`.
- **systemd-oomd**: `oomctl` shows what it monitors; `/etc/systemd/oomd.conf.d/` for limits; `systemctl disable --now systemd-oomd` to turn it off.
- **Printing always on**: `sudo systemctl enable --now cups.service cups-browsed.service`.
- **smartd**: `sudo systemctl enable --now smartmontools.service`.
- **Local AI daemon**: `systemctl --user enable --now aiosd`.
- **Blur on a small machine**: Fab Settings → Desktop Effects → Blur (the login tune never re-applies once its marker
  `~/.config/fabos/lowram-tune-done-v1` exists; delete the marker to have it decide again at the next login).
- **Animation speed**: Fab Settings → Animations (the slider is `AnimationDurationFactor`; 1 = stock Plasma length).
- **Probe cadence of the quick settings**: right-click the bar → Configure → "Refresh every" (5–120 s).
- **File Search in Fab Search**: Fab Settings → Search → Plugins → File Search (only useful with the index on).

## Minimum requirements

- 2 GB RAM (with the zram swap above; 4 GB recommended for large office documents and many browser tabs)
- 20 GB disk (the installed system is about 7 GB — the VM rootfs measures 6.1 GB, the installer profile adds firmware; 20 GB leaves room for updates, Flatpaks and documents)
- 64-bit (x86-64) processor and UEFI firmware (Secure Boot supported: Ubuntu's signed kernel and shim)
- Any GPU with a Mesa or vendor driver; the desktop is Wayland-only
