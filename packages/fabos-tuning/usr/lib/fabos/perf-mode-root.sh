#!/bin/sh
# Root side of the Fab OS performance mode (docs/PERFORMANCE.md). Run by fabos-perf-mode@<mode>.service (started by a
# logged-in user through the polkit rule 49-fabos-perf-mode.rules) and by fabos-perf-mode-restore.service at boot.
#   perf-mode-root.sh MODE          MODE = power-saver | balanced | performance | gaming | server
# What it touches, and only this:
#   /sys/devices/system/cpu/cpufreq/policy*/scaling_governor   gaming -> performance, power-saver -> powersave (when the
#         driver offers them), every other mode -> what the machine had before the first change (saved once in
#         /var/lib/fabos/perf-mode-governor.orig). power-profiles-daemon drives the energy-performance preference
#         separately; the governor pins the frequency range on top of that. Absent cpufreq (virtual machines): nothing.
#   /proc/sys/kernel/split_lock_mitigate   gaming -> 0, others -> 1 (Intel 12th gen+: the kernel stalls a process that
#         performs a split lock; some games do, and the stall reads as a stutter — the value gamemode uses, too).
#   /var/lib/fabos/perf-mode                the mode, for the restore service.
# Prints one key=value line per setting so the caller can log what happened; exit 0 even when nothing was writable.
set -u
MODE=${1:-balanced}
case "$MODE" in power-saver|balanced|performance|gaming|server) ;; *) echo "fabos-perf-mode: unknown mode '$MODE'" >&2; exit 2;; esac
STATE=/var/lib/fabos
mkdir -p "$STATE" 2>/dev/null
ORIG=$STATE/perf-mode-governor.orig

govs=$(ls /sys/devices/system/cpu/cpufreq/policy*/scaling_governor 2>/dev/null)
if [ -n "$govs" ]; then
  first=$(echo "$govs" | head -1)
  if [ ! -f "$ORIG" ]; then read -r g < "$first" 2>/dev/null && printf '%s\n' "$g" > "$ORIG"; fi
  avail=""; read -r avail < "$(dirname "$first")/scaling_available_governors" 2>/dev/null
  case "$MODE" in gaming) want=performance;; power-saver) want=powersave;; *) read -r want < "$ORIG" 2>/dev/null || want="";; esac
  case " $avail " in
    *" $want "*) n=0; for f in $govs; do printf '%s\n' "$want" > "$f" 2>/dev/null && n=$((n+1)); done; echo "governor=$want policies=$n";;
    *) echo "governor=unchanged wanted=${want:-none} available='$avail'";;
  esac
else
  echo "governor=unavailable (no cpufreq: virtual machine or a driver without frequency scaling)"
fi

if [ -w /proc/sys/kernel/split_lock_mitigate ]; then
  case "$MODE" in gaming) v=0;; *) v=1;; esac
  printf '%s\n' "$v" > /proc/sys/kernel/split_lock_mitigate 2>/dev/null && echo "split_lock_mitigate=$v" || echo "split_lock_mitigate=unchanged"
else
  echo "split_lock_mitigate=unavailable"
fi

printf '%s\n' "$MODE" > "$STATE/perf-mode" 2>/dev/null && echo "saved=$STATE/perf-mode"
exit 0
