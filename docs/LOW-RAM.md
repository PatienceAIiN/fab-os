# Fab OS on 2 GB of RAM

Fab OS is tuned so that a laptop with **2 GB of RAM** runs the full desktop — blur, animations, rounded translucent
surfaces, the agent — without a degraded "lite" mode. Nothing visual is turned down; instead the system stops spending
memory on things a desktop user never sees. Everything below ships in `fabos-desktop` (configuration) and the
`image/Containerfile` low-RAM layer (packages), and every default can be changed by the user.

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

What is **not** changed: KWin effects (blur, slide, magic lamp, overview, translucency), animation speed, the Plasma
theme, fonts, icon theme, the Discover update notifier, NetworkManager, PipeWire, SDDM, PackageKit,
unattended-upgrades, ModemManager (some laptops have WWAN), Avahi (printer discovery and `.local` names), Bluetooth.
KWin's `LatencyPolicy` is left at its default (`Medium`): a lower policy shortens the render budget and drops frames
on the weak integrated GPUs found in 2 GB machines, which would make the animations look worse, not better.

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

## Minimum requirements

- 2 GB RAM (with the zram swap above; 4 GB recommended for large office documents and many browser tabs)
- 20 GB disk (the installed system is about 7 GB — the VM rootfs measures 6.1 GB, the installer profile adds firmware; 20 GB leaves room for updates, Flatpaks and documents)
- 64-bit (x86-64) processor and UEFI firmware (Secure Boot supported: Ubuntu's signed kernel and shim)
- Any GPU with a Mesa or vendor driver; the desktop is Wayland-only
