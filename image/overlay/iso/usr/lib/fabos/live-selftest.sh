#!/bin/bash
sleep 55; out=/dev/ttyS0; [ -w $out ] || out=/dev/console
modprobe qemu_fw_cfg 2>/dev/null || true
{
  echo "FABOS_LIVE_BEGIN"; . /usr/lib/os-release; echo "PRETTY_NAME=$PRETTY_NAME"; echo "KERNEL=$(uname -r)"
  echo "LIVE_USER=$(id -u fabos 2>/dev/null && echo present || echo missing)"; echo "SDDM=$(systemctl is-active sddm)"; echo "PLASMA=$(pgrep -c plasmashell)"
  echo "CALAMARES=$([ -x /usr/bin/calamares ] && echo present || echo missing)"; echo "INSTALLER_ICON=$(ls /home/fabos/Desktop/fabos-install.desktop 2>/dev/null && echo yes || echo no)"
  echo "AGENT=$(runuser -u fabos -- env XDG_RUNTIME_DIR=/run/user/$(id -u fabos) systemctl --user is-active fabos-agent 2>/dev/null)"
  echo "FIRMWARE=$(ls /lib/firmware | wc -l) files"; echo "MEM_USED_MB=$(free -m | awk '/Mem:/{print $3}')"; echo "FABOS_LIVE_OK"; echo "FABOS_LIVE_END"
} > $out 2>&1
if [ -f /sys/firmware/qemu_fw_cfg/by_name/opt/fabos/autotest/raw ]; then sleep 3; systemctl poweroff; fi
