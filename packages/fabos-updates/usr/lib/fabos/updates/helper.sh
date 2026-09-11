#!/bin/sh
# Privileged helper for Fab OS Updates (invoked through pkexec; polkit action in.patienceai.fabos.updates).
# Subcommands: check | upgrade | channel stable|beta | auto on|off | notify-check
set -eu
export DEBIAN_FRONTEND=noninteractive
SRC=/etc/apt/sources.list.d/fabos.sources
case "${1:-}" in
  check)
    apt-get update -q 2>&1 | grep -vE '^(Hit|Get|Ign):' || true
    apt list --upgradable 2>/dev/null | grep -v '^Listing' || true
    echo "== check complete"
    ;;
  upgrade)
    apt-get update -q >/dev/null 2>&1 || true
    apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade
    apt-get -y autoremove --purge
    echo "== upgrade complete"
    ;;
  channel)
    case "${2:-}" in
      stable) suite=loom ;;
      beta)   suite="loom loom-beta" ;;
      *) echo "channel must be stable|beta" >&2; exit 2 ;;
    esac
    [ -f "$SRC" ] || { echo "$SRC missing" >&2; exit 1; }
    sed -i "s/^Suites: .*/Suites: $suite/" "$SRC"
    echo "== channel: $2 (Suites: $suite)"
    ;;
  auto)
    case "${2:-}" in
      on)  printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\nAPT::Periodic::Download-Upgradeable-Packages "1";\nAPT::Periodic::AutocleanInterval "7";\n' > /etc/apt/apt.conf.d/20auto-upgrades; systemctl enable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true ;;
      off) printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "0";\n' > /etc/apt/apt.conf.d/20auto-upgrades ;;
      *) echo "auto must be on|off" >&2; exit 2 ;;
    esac
    echo "== automatic updates: $2"
    ;;
  notify-check)
    # Run by fabos-update-check.timer as root: refresh lists and notify every graphical user if updates are waiting.
    apt-get update -q >/dev/null 2>&1 || exit 0
    n=$(apt list --upgradable 2>/dev/null | grep -vc '^Listing' || true)
    [ "${n:-0}" -gt 0 ] || exit 0
    fab=$(apt list --upgradable 2>/dev/null | grep -c '^fabos' || true)
    for d in /run/user/*; do
      uid=${d##*/}; u=$(id -nu "$uid" 2>/dev/null) || continue
      [ -S "$d/bus" ] || continue
      runuser -u "$u" -- env DBUS_SESSION_BUS_ADDRESS="unix:path=$d/bus" XDG_RUNTIME_DIR="$d" \
        notify-send -a "Fab OS" -i fabos "Updates available" "$n update(s) ready ($fab from Fab OS). Open Fab OS Updates to install." 2>/dev/null || true
    done
    ;;
  *) echo "usage: helper.sh check|upgrade|channel stable|beta|auto on|off|notify-check" >&2; exit 2 ;;
esac
