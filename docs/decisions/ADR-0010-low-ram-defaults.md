# ADR-0010: Low-RAM defaults — zram, oomd, indexing off, on-demand services; the look is untouched

**Status:** accepted (2026-09-14)

## Context
Fab OS targets laptops with 2 GB of RAM (docs/LOW-RAM.md, README "Minimum requirements"). A fresh Plasma 6 session
plus the Fab OS agent fits, but Ubuntu/Plasma defaults spend memory on services a desktop user never sees: Baloo
indexes file contents at first login, cupsd, cups-browsed and smartd run permanently, the previous session's
applications are all reopened at login, and the container-based image lacked systemd-oomd and any swap at all. The
design language (blur, translucency, rounded surfaces, motion) is part of the product and must not be traded away.

## Facts verified in the image (`podman run localhost/fabos:vm`)
- Ubuntu 26.04 offers `systemd-zram-generator` 1.2.1-2 (ships `/usr/lib/systemd/zram-generator.conf`, overridden by
  `/etc/systemd/zram-generator.conf`) and `systemd-oomd` 259.5, which ships `user@.service.d` with
  `ManagedOOMMemoryPressureLimit=50%`; neither was installed. The generator run against a fake root with our config
  wrote `dev-zram0.swap` (Priority=100, size RAM/2).
- `kde-baloo.service` and `/etc/xdg/autostart/baloo_file.desktop` are gated on `baloofilerc:Basic Settings:Indexing-Enabled`.
- `cups.service` has `Also=cups.socket cups.path`, cupsd.conf has `IdleExitTimeout 60`; `plasma-disks` is installed.
- `aiosd` is not enabled globally (opt-in); idle RSS 7.6 MB, no model loaded. `fabos-agentd` idle RSS 7.5 MB; its
  provider SDKs are imported lazily. The Welcome wizard autostart imported PyQt6 (37 MB RSS) at every login before exiting.

## Decision
1. Swap on zram (`min(ram/2, 4096)` MiB, zstd, priority 100) via systemd-zram-generator; `vm.swappiness=100`,
   `vm.vfs_cache_pressure=50`, `vm.page-cluster=0`.
2. systemd-oomd enabled with a 70 % / 20 s pressure limit for both the default and the user session slice.
3. Baloo file indexing off by default (names only when turned on); empty session at login.
4. First-install-only service policy in `fabos-desktop` postinst: cups on demand (socket + path), cups-browsed,
   smartd and motd-news off. Never touched: NetworkManager, PipeWire, SDDM, Plasma, PackageKit,
   unattended-upgrades, ModemManager, Avahi, Bluetooth, the Discover notifier.
5. No compositor or theme setting is lowered. KWin `LatencyPolicy` stays `Medium`.
6. The Welcome wrapper exits before Python when its marker exists.
7. Vendor identity (`Patience AI`, support@patienceai.in, HOME_URL) is present in every metadata.json, control file
   (`Homepage:`), the SDDM and Plymouth theme descriptors, the icon theme, and an about line in each Fab OS app.

## Consequences
- ~90–140 MB less resident memory after login on a stock install, plus ~37 MB less transient at every login and a
  compressed swap that makes 2 GB behave like roughly 4 GB (estimates, see docs/LOW-RAM.md).
- Searching inside file contents needs File Search to be turned on once (Fab Settings → Search); network printers
  that only advertise legacy (non-IPP) queues need `cups-browsed` re-enabled.
- `tests/branding-check.sh` asserts the zram config, sysctl file, Baloo default, oomd limit, on-demand cups and the
  SDDM/Plymouth author metadata.
