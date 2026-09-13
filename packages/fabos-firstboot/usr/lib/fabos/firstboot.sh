#!/bin/bash
# Fab OS first-boot provisioning (runs once as root after installation): wait for network, pull all updates,
# install FREE hardware drivers and firmware, enable Flathub, then mark done. Progress goes to the journal and to a
# desktop notification for the logged-in user. Safe to re-run; never blocks login.
#   firstboot.sh extras   — opt-in step started from Welcome to Fab OS: proprietary drivers (e.g. NVIDIA) and
#                           patent-encumbered media codecs. Never run automatically (legal/UBUNTU-DERIVATIVE-COMPLIANCE.md D6).
set -u
MARK=/var/lib/fabos/firstboot-done
LOG(){ echo "fabos-firstboot: $*"; }
mkdir -p /var/lib/fabos
notify_all(){ for d in /run/user/*; do uid=${d##*/}; u=$(id -nu "$uid" 2>/dev/null) || continue; [ -S "$d/bus" ] || continue
  runuser -u "$u" -- env DBUS_SESSION_BUS_ADDRESS="unix:path=$d/bus" XDG_RUNTIME_DIR="$d" notify-send -a "Fab OS" -i fabos "$1" "$2" 2>/dev/null || true; done; }
if [ "${1:-}" = extras ]; then
  export DEBIAN_FRONTEND=noninteractive
  notify_all "Installing extras" "Proprietary drivers and media codecs are being installed in the background."
  apt-get update -q || true
  if command -v ubuntu-drivers >/dev/null 2>&1; then LOG "drivers (including proprietary)"; ubuntu-drivers install --include-dkms || LOG "ubuntu-drivers had errors"; fi
  LOG "codecs"; apt-get -y install --no-install-recommends gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav libavcodec-extra 2>/dev/null || true
  date -u +%FT%TZ > /var/lib/fabos/extras-done
  notify_all "Extras installed" "Restart to load new drivers."; LOG "extras done"; exit 0
fi

LOG "waiting for network (up to 3 min)"
nm-online -q -t 180 2>/dev/null || LOG "no network yet; will retry next boot" && :
if ! nm-online -q -t 5 2>/dev/null; then exit 0; fi
notify_all "Setting up Fab OS" "Connected. Installing updates and drivers in the background — you can keep working."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q || true
LOG "updates"; apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade || LOG "upgrade had errors"
if command -v ubuntu-drivers >/dev/null 2>&1; then LOG "drivers (free only; proprietary ones are opt-in via Welcome)"; ubuntu-drivers install --free-only --no-oem || LOG "ubuntu-drivers had errors"; fi
LOG "firmware"; apt-get -y install --no-install-recommends linux-firmware fwupd 2>/dev/null || true
LOG "flathub"; flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
LOG "codecs (free)"; apt-get -y install --no-install-recommends gstreamer1.0-plugins-good 2>/dev/null || true
apt-get -y autoremove --purge >/dev/null 2>&1 || true
date -u +%FT%TZ > "$MARK"
notify_all "Fab OS is ready" "Updates and drivers are installed. A restart is recommended if the kernel or graphics driver changed."
LOG "done"
