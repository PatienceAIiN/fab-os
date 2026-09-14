#!/bin/bash
# Emits machine-checkable markers; powers off only when the kernel cmdline says fabos.autopoweroff.
sleep 20
out=/dev/ttyS0; [ -w $out ] || out=/dev/console
{
  echo "FABOS_SELFTEST_BEGIN"
  . /usr/lib/os-release; echo "PRETTY_NAME=$PRETTY_NAME ID=$ID ID_LIKE=$ID_LIKE"
  echo "LSB=$(lsb_release -ds 2>/dev/null)"
  echo "KERNEL=$(uname -r)"
  echo "SDDM=$(systemctl is-active sddm)"
  echo "PLASMA=$(pgrep -c plasmashell)"
  echo "KWIN=$(pgrep -c kwin_wayland)"
  echo "PLYMOUTH_THEME=$(readlink -f /usr/share/plymouth/themes/default.plymouth)"
  echo "AIOS=$(/usr/bin/aios settings status 2>&1 | head -1)"
  echo "LLAMA=$([ -x /usr/bin/llama-cli ] && echo present || echo missing)"
  echo "SNAPD=$(dpkg -s snapd >/dev/null 2>&1 && echo INSTALLED || echo absent)"
  # agent smoke test as the desktop user with the scripted provider: writes a note and opens it in kate
  U=fabos; UID_=$(id -u $U); RU="runuser -u $U -- env XDG_RUNTIME_DIR=/run/user/$UID_ DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_/bus WAYLAND_DISPLAY=wayland-0"
  echo "AGENT=$($RU systemctl --user is-active fabos-agent 2>/dev/null)"
  $RU fabos settings provider fake >/dev/null 2>&1
  TID=$($RU fabos --json do --mode bypass "open editor and write hi from Fab OS" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
  for i in $(seq 1 30); do ST=$($RU fabos --json show "$TID" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))"); case "$ST" in done|failed|cancelled) break;; esac; sleep 1; done
  echo "AGENT_TASK=$TID:$ST"
  echo "AGENT_NOTE=$(cat /home/$U/Documents/fabos-note.txt 2>/dev/null | head -1)"
  echo "AGENT_KATE=$(pgrep -c -u $U kate)"
  RT=$($RU fabos --json do --mode bypass "check the system as root" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
  for i in $(seq 1 20); do RS=$($RU fabos --json show "$RT" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))"); case "$RS" in done|failed|cancelled) break;; esac; sleep 1; done
  echo "AGENT_ROOT=$RS:$($RU fabos --json show "$RT" 2>/dev/null | python3 -c "
import sys,json; t=json.load(sys.stdin); o=[s for s in t['steps'] if s['name']=='run_shell']; print(json.loads(o[0]['output']).get('stdout','').replace(chr(10),'/') if o else 'no-step')")"
  echo "FEEDBACK_SOCKET=$(systemctl is-active fabos-feedback.socket)"
  echo "UPDATE_TIMER=$(systemctl is-active fabos-update-check.timer)"
  echo "SESSION_NAME=$(grep ^Name= /usr/local/share/wayland-sessions/fabos.desktop | cut -d= -f2)"
  # ADR-0015 (no wallet): manager absent, kwalletrc Enabled=false, no kwalletd6 after the desktop user's login
  echo "NO_WALLET=$(dpkg -s kwalletmanager >/dev/null 2>&1 && echo kwalletmanager-INSTALLED || echo no-manager):$(grep -q '^Enabled=false' /etc/xdg/kwalletrc && echo disabled || echo ENABLED):kwalletd6=$(pgrep -c kwalletd6)"
  # ADR-0016: Ubuntu's `brave` and Brave's `brave-browser-stable` profiles both attach to /opt/brave.com/brave/brave — count what loaded, and apparmor.service errors
  echo "APPARMOR_BRAVE=$(aa-status 2>/dev/null | grep -cE '^ +(brave|brave-browser-stable)$'):errors=$(journalctl -b -u apparmor.service -p err --no-pager -q 2>/dev/null | wc -l)"
  echo "SUID_COUNT=$(find / -xdev -perm -4000 -type f 2>/dev/null | wc -l):brave_sandbox=$(stat -c %U:%a /opt/brave.com/brave/chrome-sandbox 2>/dev/null || echo absent)"
  echo "PLASMA_THEME=$(runuser -u $U -- kreadconfig6 --file plasmarc --group Theme --key name 2>/dev/null || grep -A1 '^\[Theme\]' /etc/xdg/plasmarc | tail -1)"
  echo "ICON_THEME=$(grep -A2 '^\[Icons\]' /etc/xdg/kdeglobals | grep Theme | cut -d= -f2)"
  echo "RUNNER_DBUS=$([ -f /usr/share/dbus-1/services/in.patienceai.fabos.runner.service ] && echo present || echo missing)"
  echo "KDE_FEEDBACK_KCM=$([ -e /usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_feedback.so ] && echo present || echo hidden)"
  $RU fabos settings provider claude >/dev/null 2>&1
  echo "MEM_USED_MB=$(free -m | awk '/Mem:/{print $3}')"
  echo "BOOT_TIME=$(systemd-analyze 2>/dev/null | head -1)"
  echo "FABOS_BOOT_OK"
  echo "FABOS_SELFTEST_END"
} > $out 2>&1
if grep -q fabos.autopoweroff /proc/cmdline; then sleep 3; systemctl poweroff; fi
