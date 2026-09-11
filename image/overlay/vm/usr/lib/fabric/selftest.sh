#!/bin/bash
# Emits machine-checkable markers; powers off only when the kernel cmdline says fabric.autopoweroff.
sleep 20
out=/dev/ttyS0; [ -w $out ] || out=/dev/console
{
  echo "FABRIC_SELFTEST_BEGIN"
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
  U=fabric; UID_=$(id -u $U); RU="runuser -u $U -- env XDG_RUNTIME_DIR=/run/user/$UID_ DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_/bus WAYLAND_DISPLAY=wayland-0"
  echo "AGENT=$($RU systemctl --user is-active fabric-agent 2>/dev/null)"
  $RU fabric settings provider fake >/dev/null 2>&1
  TID=$($RU fabric --json do --mode bypass "open editor and write hi from Fab OS" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
  for i in $(seq 1 30); do ST=$($RU fabric --json show "$TID" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))"); case "$ST" in done|failed|cancelled) break;; esac; sleep 1; done
  echo "AGENT_TASK=$TID:$ST"
  echo "AGENT_NOTE=$(cat /home/$U/Documents/fabric-note.txt 2>/dev/null | head -1)"
  echo "AGENT_KATE=$(pgrep -c -u $U kate)"
  echo "FEEDBACK_SOCKET=$(systemctl is-active fabric-feedback.socket)"
  echo "KDE_FEEDBACK_KCM=$([ -e /usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_feedback.so ] && echo present || echo hidden)"
  $RU fabric settings provider claude >/dev/null 2>&1
  echo "MEM_USED_MB=$(free -m | awk '/Mem:/{print $3}')"
  echo "BOOT_TIME=$(systemd-analyze 2>/dev/null | head -1)"
  echo "FABRIC_BOOT_OK"
  echo "FABRIC_SELFTEST_END"
} > $out 2>&1
if grep -q fabric.autopoweroff /proc/cmdline; then sleep 3; systemctl poweroff; fi
