#!/bin/bash
# Fab OS live-session self-test: writes markers to the serial console (tests/iso-boot-test.sh reads them). Under QEMU
# only (qemu_fw_cfg entries set by scripts/boot-iso.sh) it then either powers off (opt/fabos/autotest) or hands over to
# the automated installer run (opt/fabos/autoinstall = luks|plain, tests/install-vm.sh). On real hardware neither entry
# exists: the markers go to the (usually absent) serial port and nothing else happens.
sleep 55; out=/dev/ttyS0; [ -w $out ] || out=/dev/console
modprobe qemu_fw_cfg 2>/dev/null || true
FW=/sys/firmware/qemu_fw_cfg/by_name/opt/fabos
{
  echo "FABOS_LIVE_BEGIN"; . /usr/lib/os-release; echo "PRETTY_NAME=$PRETTY_NAME"; echo "KERNEL=$(uname -r)"
  echo "LIVE_USER=$(id -u fabos 2>/dev/null && echo present || echo missing)"; echo "SDDM=$(systemctl is-active sddm)"; echo "PLASMA=$(pgrep -c plasmashell)"
  echo "CALAMARES=$([ -x /usr/bin/calamares ] && echo present || echo missing)"; echo "INSTALLER_ICON=$(ls /home/fabos/Desktop/fabos-install.desktop 2>/dev/null && echo yes || echo no)"
  echo "AGENT=$(runuser -u fabos -- env XDG_RUNTIME_DIR=/run/user/$(id -u fabos) systemctl --user is-active fabos-agent 2>/dev/null)"
  echo "FIRMWARE=$(ls /lib/firmware | wc -l) files"; echo "MEM_USED_MB=$(free -m | awk '/Mem:/{print $3}')"
  echo "AUTOINSTALL_HELPER=$([ -x /usr/lib/fabos/live-autoinstall.sh ] && echo present || echo missing)"
  echo "FABOS_LIVE_OK"; echo "FABOS_LIVE_END"
} > $out 2>&1
if [ -f $FW/autoinstall/raw ]; then exec /usr/lib/fabos/live-autoinstall.sh "$(tr -d '[:space:]' < $FW/autoinstall/raw)"; fi
if [ -f $FW/autotest/raw ]; then sleep 3; systemctl poweroff; fi
