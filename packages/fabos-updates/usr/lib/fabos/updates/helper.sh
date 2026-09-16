#!/bin/sh
# Privileged helper for @DISTRO_NAME@ Updates (invoked through pkexec; polkit action in.patienceai.fabos.updates) and for the
# system timer fabos-update-check + the dpkg trigger fabos-postupgrade (both run it as root directly).
# Subcommands: check | upgrade | channel stable|beta | auto on|off | notify-check | post-upgrade
set -eu
export DEBIAN_FRONTEND=noninteractive
SRC=/etc/apt/sources.list.d/fabos.sources
STATE=/usr/lib/fabos/updates/state.py
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
    # the fabos-postupgrade trigger has recorded what changed by now (dpkg runs it at the end of the apt run); make sure
    # a run without any trigger activation (Ubuntu-only updates) still refreshes the snapshot for the next comparison
    python3 "$STATE" record >/dev/null 2>&1 || true
    echo "== upgrade complete"
    ;;
  channel)
    case "${2:-}" in
      stable) suite=@DISTRO_CODENAME@ ;;
      beta)   suite="@DISTRO_CODENAME@ @DISTRO_CODENAME@-beta" ;;
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
    # fabos-update-check.timer (system, root): refresh the package lists, then wake the notifier in every graphical session.
    # The notification itself is shown from inside the session (fabos-update-notify.service, user unit) so it has the
    # user's bus, icon theme and an "Open @DISTRO_NAME@ Updates" button; root never talks to the notification server directly.
    apt-get update -q >/dev/null 2>&1 || exit 0
    python3 "$STATE" poke || true
    ;;
  post-upgrade)
    # dpkg trigger fabos-postupgrade (DEBIAN/triggers): runs once at the end of every apt/dpkg run that changed a Fab OS
    # package. Records what changed and what it needs (log out / restart / agent restart), pokes the sessions. Never fails.
    python3 "$STATE" record || true
    ;;
  *) echo "usage: helper.sh check|upgrade|channel stable|beta|auto on|off|notify-check|post-upgrade" >&2; exit 2 ;;
esac
