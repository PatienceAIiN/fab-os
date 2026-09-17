#!/bin/sh
# Configuration and service state in effect — runs INSIDE the guest as the logged-in user, in the graphical session's
# environment (tests/perf-vm.sh exports XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS / WAYLAND_DISPLAY first).
# Plain text, one "### section" per topic; tests/perf-vm.sh keeps the whole output as facts.txt and reads a few lines.
sec() { printf '\n### %s\n' "$1"; }
kr() { kreadconfig6 --file "$1" --group "$2" --key "$3" --default unset 2>/dev/null; }

sec system
cat /etc/fabos-release 2>/dev/null | head -3; uname -r; nproc; head -1 /proc/meminfo; uptime -p
dpkg-query -W -f '${Package} ${Version}\n' 'fabos-*' 2>/dev/null

sec memory
sysctl -n vm.swappiness vm.page-cluster vm.vfs_cache_pressure 2>/dev/null | tr '\n' ' '; echo " (swappiness page-cluster vfs_cache_pressure)"
grep -rn "swappiness" /etc/sysctl.d /usr/lib/sysctl.d /run/sysctl.d 2>/dev/null
zramctl 2>/dev/null; swapon --show 2>/dev/null
systemctl is-active systemd-oomd 2>/dev/null | sed 's/^/systemd-oomd: /'
oomctl 2>/dev/null | head -12
for d in /sys/block/*/queue/scheduler; do [ -r "$d" ] && printf '%s: %s rotational=%s\n' "$d" "$(cat "$d")" "$(cat "${d%/scheduler}/rotational" 2>/dev/null)"; done

sec power
systemctl is-active power-profiles-daemon 2>/dev/null | sed 's/^/power-profiles-daemon: /'
powerprofilesctl get 2>/dev/null | sed 's/^/ppd active: /'
powerprofilesctl list 2>/dev/null | head -20
ls /sys/class/power_supply/ 2>/dev/null | tr '\n' ' ' | sed 's/^/power_supply: /'; echo
ls /sys/class/powercap/ 2>/dev/null | tr '\n' ' ' | sed 's/^/powercap(RAPL): /'; echo
ls /sys/devices/system/cpu/cpufreq/ 2>/dev/null | head -3 | tr '\n' ' ' | sed 's/^/cpufreq: /'; echo
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null | sed 's/^/governor: /'
for g in AC Battery; do printf 'powerdevilrc [%s][Performance] PowerProfile=%s  [%s][Display] TurnOffDisplayWhenIdle=%s DimDisplayWhenIdle=%s  [%s][SuspendAndShutdown] AutoSuspendAction=%s LidAction=%s\n' \
  "$g" "$(kr powerdevilrc "$g][Performance" PowerProfile)" "$g" "$(kr powerdevilrc "$g][Display" TurnOffDisplayWhenIdle)" "$(kr powerdevilrc "$g][Display" DimDisplayWhenIdle)" \
  "$g" "$(kr powerdevilrc "$g][SuspendAndShutdown" AutoSuspendAction)" "$(kr powerdevilrc "$g][SuspendAndShutdown" LidAction)"; done
cat "${XDG_CONFIG_HOME:-$HOME/.config}/fabos/performance-mode" 2>/dev/null | sed 's/^/performance-mode file: /'
command -v fabos-perf-mode >/dev/null 2>&1 && fabos-perf-mode status --json 2>/dev/null | sed 's/^/perf-mode status: /'

sec kwin
printf 'kwinrc [Compositing] AllowTearing=%s  [KDE] AnimationDurationFactor=%s\n' "$(kr kwinrc Compositing AllowTearing)" "$(kr kdeglobals KDE AnimationDurationFactor)"
for e in blur translucency magiclamp slide fade scale dimscreen squash slidingpopups zoom overview kwin4_effect_shapecorners fabos-snap-assist; do printf '%s=%s ' "$e" "$(kr kwinrc Plugins "${e}Enabled")"; done; echo
printf 'loaded effects: '; qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.loadedEffects 2>/dev/null | tr '\n' ' '; echo
printf 'active effects: '; qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.activeEffects 2>/dev/null | tr '\n' ' '; echo
kscreen-doctor -o 2>/dev/null | grep -i -E "Output|VRR|Vrr|enabled|Modes:" | head -12
cat "${XDG_CONFIG_HOME:-$HOME/.config}/kwinoutputconfig.json" 2>/dev/null | grep -i -E '"vrrPolicy"|"name"' | head -8

sec indexing
printf 'baloofilerc Indexing-Enabled=%s  krunnerrc baloosearchEnabled=%s\n' "$(kr baloofilerc 'Basic Settings' Indexing-Enabled)" "$(kr krunnerrc Plugins baloosearchEnabled)"
balooctl6 status 2>/dev/null | head -4
pgrep -a baloo 2>/dev/null | sed 's/^/baloo process: /'
systemctl --user is-active kde-baloo.service 2>/dev/null | sed 's/^/kde-baloo.service: /'

sec voice
systemctl --user is-active fabos-voiced 2>/dev/null | sed 's/^/fabos-voiced: /'
systemctl --user show fabos-voiced -p Nice -p IOSchedulingClass -p MemoryHigh 2>/dev/null | tr '\n' ' '; echo
fabos-voice status 2>/dev/null
pgrep -a -f 'pw-record|pocketsphinx|fabos_voiced' 2>/dev/null | sed 's/^/voice process: /'
pactl list short sources 2>/dev/null | grep -v monitor | sed 's/^/mic source: /'
journalctl --user -u fabos-voiced -b --no-pager -o cat 2>/dev/null | tail -25 | sed 's/^/voiced log: /'

sec llama
systemctl --user is-enabled fabos-llama.socket 2>/dev/null | sed 's/^/fabos-llama.socket enabled: /'
systemctl --user show fabos-llama.socket -p ActiveState -p ConditionResult -p ConditionTimestamp 2>/dev/null | tr '\n' ' '; echo
systemctl --user show fabos-llama.service -p ActiveState -p StopWhenUnneeded -p Nice -p MemoryHigh -p MemoryMax 2>/dev/null | tr '\n' ' '; echo
systemctl --user cat fabos-llama-proxy.service 2>/dev/null | grep -E '^ExecStart' | sed 's/^/proxy: /'
pgrep -a llama-server 2>/dev/null | sed 's/^/llama process: /'; [ -z "$(pgrep llama-server 2>/dev/null)" ] && echo "llama-server: not running"
curl -s -m 3 -o /dev/null -w 'GET 127.0.0.1:8080/health -> HTTP %{http_code}\n' http://127.0.0.1:8080/health 2>/dev/null || echo "127.0.0.1:8080: connection refused (socket not listening)"
sh -n /usr/lib/fabos/ai/llama-start.sh 2>&1 && awk -F': *' '/^physical id/ {p=$2} /^core id/ {c[p ":" $2]=1} END {n=0; for (k in c) n++; print "physical cores seen by llama-start.sh: " n}' /proc/cpuinfo

sec session
systemctl --user list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | awk '{print $1}' | tr '\n' ' '; echo
ls /etc/xdg/autostart/ 2>/dev/null | tr '\n' ' ' | sed 's/^/autostart: /'; echo
systemctl --user show -p DefaultTimeoutStopUSec 2>/dev/null
systemd-analyze --user critical-chain 2>/dev/null | head -12
systemd-analyze --user blame 2>/dev/null | head -12

sec boot
systemd-analyze 2>/dev/null
systemd-analyze critical-chain 2>/dev/null | head -14
systemd-analyze blame 2>/dev/null | head -20

sec journal-warnings
journalctl -b -p warning --no-pager -o short 2>/dev/null | wc -l | sed 's/^/system warnings this boot: /'
journalctl -b -p warning --no-pager -o cat 2>/dev/null | sed -E 's/[0-9]+/N/g' | sort | uniq -c | sort -rn | head -25
journalctl --user -b -p warning --no-pager -o cat 2>/dev/null | sed -E 's/[0-9]+/N/g' | sort | uniq -c | sort -rn | head -15 | sed 's/^/user: /'
journalctl -b --no-pager -o cat 2>/dev/null | grep -i -E 'sycoca|xdg-desktop-portal.*(timeout|failed)|fontconfig' | tail -6

sec processes
ps -eo pid,ni,rss,pcpu,etimes,comm --sort=-rss --no-headers 2>/dev/null | head -25
