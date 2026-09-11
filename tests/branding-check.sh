#!/usr/bin/env bash
# Static checks on the built image: identity, no Canonical branding packages, no snap, legal files present.
set -uo pipefail; PROFILE=${1:-vm}; TAG=fabos:$PROFILE; fail=0
chk() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
R() { podman run --rm "$TAG" bash -c "$1"; }
chk "os-release ID=fabos"               "R 'grep -q ^ID=fabos /usr/lib/os-release && grep -q ID_LIKE=.ubuntu /usr/lib/os-release'"
chk "os-release keeps UBUNTU_CODENAME"    "R 'grep -q ^UBUNTU_CODENAME=resolute /usr/lib/os-release'"
chk "lsb_release says FabOS"             "R 'lsb_release -ds' | grep -q 'Fab OS'"
chk "/etc/os-release is a symlink"        "R 'test -L /etc/os-release'"
chk "Ubuntu identity diverted, not lost"  "R 'test -f /usr/lib/os-release.ubuntu && grep -q ^ID=ubuntu /usr/lib/os-release.ubuntu'"
chk "no Canonical artwork packages"       "! R 'dpkg -l ubuntu-wallpapers* plymouth-theme-ubuntu* ubuntu-artwork 2>/dev/null | grep ^ii'"
chk "snapd absent"                        "! R 'dpkg -s snapd 2>/dev/null' >/dev/null"
chk "ubuntu-pro-client absent"            "! R 'dpkg -s ubuntu-pro-client 2>/dev/null' >/dev/null"
chk "plymouth default theme = fabos"     "R 'readlink -f /usr/share/plymouth/themes/default.plymouth' | grep -q themes/fabos/fabos.plymouth"
chk "plymouth script plugin present"      "R 'ls /usr/lib/x86_64-linux-gnu/plymouth/script.so'"
chk "initramfs contains fabos theme"     "R 'lsinitramfs /boot/initrd.img-* | grep -q themes/fabos/fabos.script'"
chk "Inter font installed"                "R 'fc-list | grep -qi Inter-Regular'"
chk "SDDM theme configured"               "R 'grep -q Current=fabos /etc/sddm.conf.d/10-fabos.conf && test -f /usr/share/sddm/themes/fabos/Main.qml'"
chk "look-and-feel package present"       "R 'test -f /usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/defaults'"
chk "wallpaper installed"                 "R 'test -f /usr/share/wallpapers/FabOS/contents/images/1920x1080.png'"
chk "3D logo shipped"                     "R 'test -s /usr/share/fabos/3d/fabos-mark.glb'"
chk "aios runs"                           "R 'aios settings list' >/dev/null 2>&1"
chk "llama-cli present"                   "R 'test -x /usr/bin/llama-cli'"
chk "copyright files retained (>300)"     "[ \$(R 'find /usr/share/doc -name copyright | wc -l') -gt 300 ]"
chk "source offer shipped on system"      "R 'test -f /usr/share/doc/fabos-branding/SOURCE-OFFER'"
chk "kernel + initrd present"             "R 'ls /boot/vmlinuz-* /boot/initrd.img-*'"
chk "sddm enabled"                        "R 'systemctl is-enabled sddm' | grep -q enabled"
exit $fail
