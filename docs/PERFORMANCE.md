# Fab OS performance and power (1.0-8)

The owner's report on a Lenovo laptop running 1.0-7: "battery consumption high; any app opening lags, the PC lags even
though memory is free; make it open things faster and handle multiple apps easily; best optimised for work, gaming and
server use". This document records what was **measured** on the 1.0-7 desktop, what 1.0-8 **changes** (all of it in the
packages, delivered over the air), the **numbers after**, and the honest **limits** of measuring power in a virtual
machine. Every number below was read inside the booted VM by `tests/perf-vm.sh`; the raw data is under
`build/r8-perf/{before,after}/` (`facts.txt`, `idle.json`, `launch.jsonl`, `modes.jsonl`, `perf-vm.json`).

The VM is a 2 GB / 2 vCPU QEMU guest with software rendering (llvmpipe) and a silent emulated microphone; it has no
battery, no RAPL power counters and no cpufreq. Absolute launch times there are several times a real laptop's; the
comparisons before/after are like for like. Power is inferred from what wakes the CPU (context switches, interrupts,
per-process wake-ups), which is what drains a battery when the machine is otherwise idle.

## 1. What the 1.0-7 desktop was doing while idle

Measured after installing the shipped 1.0-7 packages into a 1.0-6 disk, rebooting, logging in (autologin) and leaving
the session alone for 3 minutes, then sampling 60 s (`tests/perf/sample.py`: `/proc/<pid>/stat` deltas, context
switches summed over every thread, `smaps_rollup`, `/proc/stat`, `/proc/interrupts`).

«BEFORE_IDLE_TABLE»

Findings, each one verified in the guest rather than assumed:

- **The always-on "Hey Fab" listener was the largest steady consumer.** `pw-record | pocketsphinx` decoded the
  microphone stream continuously: pocketsphinx alone «BEFORE_PS_CPU» of a core with «BEFORE_PS_WAKE» wake-ups/s, pw-record
  «BEFORE_PW_WAKE» wake-ups/s, the python daemon polling its pipe with a 1 s `select`. On a laptop this also keeps the
  audio codec and its DMA engine powered the whole time the lid is open — a steady drain that no CPU number shows.
- **plasmashell and kwin_wayland were quiet** («BEFORE_SHELL_CPU» / «BEFORE_KWIN_CPU» of a core): the 1.0-3 idle-budget work
  (docs/LOW-RAM.md) holds. Tasks created system-wide: «BEFORE_TASKS»/s.
- **The local model was not resident**: `fabos-llama.socket` carries `ConditionMemory=>3G`, so at 2 GB it is not even
  listening (ConditionResult=no); on a bigger machine nothing runs until the first request, the proxy exits after
  10 idle minutes (`--exit-idle-time=10min`) and `StopWhenUnneeded=yes` unloads the model. No preload at login.
- **Baloo was off** (`Indexing-Enabled=false`, no `baloo_file` process; `kde-baloo.service` has an `ExecCondition` on
  exactly that key).
- **power-profiles-daemon was installed and running**, but the quick-settings battery card only offered its three
  profiles and nothing tied them to the compositor, the listener or the display policy.
- Memory: «BEFORE_MEM». The biggest resident processes were plasmashell («BEFORE_SHELL_PSS» PSS) and the Discover update
  notifier (rebranded "Fab Software Updater", «BEFORE_DISCOVER_PSS» PSS while it checks for updates after a package
  install) — neither is the lag the owner describes; with memory free, lag is CPU and I/O scheduling.
- The kernel was at `vm.swappiness=100`, zram `min(ram/2, 4096)` zstd, systemd-oomd at 70 % / 20 s — exactly what
  `fabos-desktop` ships. (A machine that shows `vm.swappiness=180` and a 7.1 GiB zram device is not running these
  files; `sysctl vm.swappiness` and `systemctl cat dev-zram0.swap` tell which file won.)
- The virtio disk ran the `mq-deadline` scheduler with `rotational=1`; a laptop's spinning disk would run the same
  scheduler, which does not protect an application start from a background stream of reads.

## 2. What 1.0-8 changes

| Area | Change | Package | Read it back |
|---|---|---|---|
| **Wake-word listener** | Voice-activity gate in front of pocketsphinx: the recorder keeps running (the ring buffer and the second-look verification need the whole clip), the decoder is fed only around speech-like audio (RMS ≥ 3× the running noise floor and ≥ 40; 300 ms pre-roll; 1.2 s hang-over so the endpointer closes the utterance). Silence and steady room noise cost the decoder nothing. | `fabos-voice` | `journalctl --user -u fabos-voiced` prints "decoder fed X s, gate held back Y s" when the spotter closes |
| **Listener policy** | New setting `voice.spotter = on \| battery-off \| off` (`fabos settings voice.spotter battery-off`). `battery-off` releases the microphone after 5 min without input while discharging and reopens it within 10 s of the user's return; every policy pauses while the screen is locked (nobody should drive a locked machine by voice; the codec can power down). The performance mode tightens it: Server → off, Power saver → battery-off. | `fabos-voice` | `fabos-voice status` → `spotter_policy`, `spotter_state` (listening / paused:locked / paused:battery-idle / off) |
| **Performance mode** | `fabos-perf-mode set power-saver \| balanced \| performance \| gaming \| server`, and the five-segment control in the quick-settings battery card. Table below. Persists across reboots (user file + `/var/lib/fabos/perf-mode` re-applied by `fabos-perf-mode-restore.service`). | new `fabos-tuning` (Depends of `fabos-desktop`) | `fabos-perf-mode status [--json]` |
| **Gaming CPU governor** | `fabos-perf-mode@<mode>.service` (root, one-shot) writes `scaling_governor` (gaming → performance, power-saver → powersave, others → the value saved before the first change) and `kernel.split_lock_mitigate` (gaming → 0). A logged-in active local user may start exactly this unit without a password (`49-fabos-perf-mode.rules`). | `fabos-tuning` | `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`, `status --json` → `governor` |
| **I/O scheduler** | udev: NVMe → `none`, other flash → `mq-deadline`, spinning disks → `bfq` (the kernel documentation's recommendation per class; bfq keeps an application start responsive while something else streams from a hard disk). Applied to present disks by the postinst. | `fabos-tuning` | `cat /sys/block/*/queue/scheduler` |
| **Logout/shutdown** | `systemd --user` `DefaultTimeoutStopSec=15s` (stock 90 s): a user service that ignores SIGTERM no longer holds the session for a minute and a half. | `fabos-tuning` | `systemctl --user show -p DefaultTimeoutStopUSec` |
| **Local model priority** | `fabos-llama.service` at `Nice=10` (was 5): generation saturates its threads; the compositor, the shell and the foreground app always win the core. Threads stay one per physical core, at most 8; the 10 min idle unload and the no-preload behaviour are asserted by the test. | `fabos-ai` | `systemctl --user show fabos-llama -p Nice` |

### The mode table

| Mode | power-profiles-daemon | KWin | Display / sleep (PowerDevil) | Listener | CPU (root helper) |
|---|---|---|---|---|---|
| Power saver | `power-saver` | as the user has it | as the user has it | `battery-off` (unless the user set `off`) | governor `powersave` where offered |
| Balanced (default) | `balanced` | as the user has it | as the user has it | the user's `voice.spotter` | saved original governor |
| Performance | `performance` | as the user has it | as the user has it | the user's `voice.spotter` | saved original governor |
| Gaming | `performance` | `AllowTearing=true`; VRR `Automatic` on every output; blur, translucency, magic lamp, dim-screen off (rounded corners and fades stay) | as the user has it | the user's `voice.spotter` | governor `performance`, `split_lock_mitigate=0` |
| Server | `balanced` | `AllowTearing=false`; every animation effect and the rounded corners off | display never dims or turns off, `AutoSuspendAction=0`, `LidAction=0` (AC and battery); the screen still locks after the usual idle time | `off` | saved original governor |

Entering Gaming or Server snapshots the user's own values of every key those modes write (`~/.config/fabos/performance-mode.snapshot`);
leaving them restores exactly those values, or deletes the key so the `/etc/xdg` default applies again. KWin is told to
`reconfigure`, PowerDevil to `refreshStatus`; nothing is restarted. The defaults of 1.0-7 (`AllowTearing=false`, blur on
above 3.5 GB, `AnimationDurationFactor=0.5`) are unchanged for Balanced.

Deliberately **not** changed: `vm.swappiness`, zram size, systemd-oomd limits (no evidence in the measurements that
memory pressure is the owner's lag; they are documented in docs/LOW-RAM.md), `LatencyPolicy` (does not exist in KWin
6.6), `kwinrc [Wayland]` (KWin 6.6 keeps `AllowTearing` under `[Compositing]`).

## 3. The same measurements after 1.0-8

The same run, with the 1.0-7 pool installed first and then the 1.0-8 packages on top (the upgrade a user's machine
makes), rebooted into the new session.

«AFTER_IDLE_TABLE»

«AFTER_LAUNCH_TABLE»

«AFTER_MODES»

## 4. Expected effect on a real laptop

- **Battery**: the listener no longer keeps a decoder busy on room noise; with `voice.spotter=battery-off` (Power saver
  mode sets it) the microphone is released after 5 idle minutes on battery and the audio codec can enter its low-power
  state — that, not the CPU %, is the visible saving on a laptop. Locking the screen pauses it in every mode.
- **Foreground responsiveness**: the model generates at `Nice=10`; a spinning disk runs `bfq`; Gaming pins the governor
  and lets a full-screen game present unsynchronised frames with VRR.
- **A machine used as a server**: Server mode keeps the display on, never sleeps, ignores the lid, drops every effect
  and the listener, and survives reboots.

## 5. Honest limits

- QEMU has **no battery, no RAPL, no cpufreq**: power is inferred from wake-ups and CPU; the governor path is exercised
  (`governor=unavailable (no cpufreq)`) but its effect cannot be measured here. `powertop` is in the Ubuntu archive
  (2.15) and not installed by default; on the laptop, `sudo powertop --csv=/tmp/pt.csv --time=60` gives the per-process
  wake-up table this document infers.
- The emulated microphone is **silent**, so the gate keeps the decoder fully idle; in a room with people talking the
  decoder runs during speech (that is its job), roughly the fraction of time someone is speaking.
- The local model **cannot run at 2 GB** (`ConditionMemory=>3G`); its idle unload is asserted from the unit files and the
  absence of any llama-server process after login, not from a live query.
- Launch latencies are **software-rendered VM numbers**; a laptop with a GPU is several times faster. The budgets in
  `tests/perf-vm.sh` are for this VM and fail on a regression against a stored baseline (`--baseline-json`).
- `irqbalance`, `thermald`, `tlp` are not installed and not measurable here; nothing in this round depends on them.

## 6. Running the test

```
FABOS_BUILD=/path/to/checkout/build tests/perf-vm.sh --debs build/apt-repo/loom/pool/loom --baseline --out build/r8-perf/before
FABOS_BUILD=/path/to/checkout/build tests/perf-vm.sh --debs build/apt-repo/loom/pool/loom --debs build/r8-debs-after \
    --baseline-json build/r8-perf/before/perf-vm.json --reboot-check --out build/r8-perf/after
```

The run boots a disposable qcow2 overlay of `build/fabos-vm.img` under `flock /tmp/fabos-vm.lock`, installs the debs
with `apt-get install` (every postinst runs as an upgrade and must exit 0), reboots, waits 180 s, samples 60 s, times
3 launches each of Fab Terminal, Fab Files, Fab Editor and Firefox (KWin's `windowAdded` on the guest clock — `kdotool`
is not in Ubuntu 26.04), round-trips the five modes through `systemd-run --user` (an ssh login is an inactive session
polkit would refuse; a process under the user manager is attributed to the active seat, which is how the card runs it),
optionally sets Gaming and reboots to prove persistence, flips `voice.spotter` off and on, and always quits QEMU and
deletes the overlay. Budgets: voice pipeline < 1.5 % of a core idle; plasmashell and kwin_wayland < 3 %; no other
process ≥ 5 %; < 1.5 task creations/s; warm launch budgets per application and ≤ 1.25× + 200 ms of the baseline.
