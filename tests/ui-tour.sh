#!/usr/bin/env bash
# Visual verification tour: boots the VM headless (software display), drives the UI with keystrokes and captures
# screenshots for review. Output: build/screenshots/tour-*.png. Usage: tests/ui-tour.sh [--keep]
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
K=scripts/vm-key.sh; S=scripts/vm-screenshot.sh
shot() { $S "tour-$1" >/dev/null 2>&1 && echo "  shot $1"; }
run() { $K key alt-f2; sleep 1.5; $K type "$1"; sleep 1.5; $K key ret; sleep "${2:-5}"; }   # KRunner: commands
launch() { $K key meta_l; sleep 1.8; $K type "$1"; sleep 1.8; $K key ret; sleep "${2:-6}"; }   # launcher search: applications by name
pgrep -f qemu-system-x86_64 >/dev/null && { echo "a VM is already running; stop it first"; exit 1; }
(scripts/boot-vm.sh --headless --mem 2048 --cpus 4 > build/boot-headless.out 2>&1 &)
for i in $(seq 1 60); do [ -S build/qemu-monitor.sock ] && break; sleep 2; done; sleep 80
shot 01-desktop-dark
$K key meta_l; sleep 2; shot 02-launcher; $K type "wallet"; sleep 2; shot 03-launcher-wallet; $K key esc; sleep 1
$K key meta_l; sleep 2; $K type "console"; sleep 2; shot 04-launcher-console; $K key esc; sleep 1
run "system settings" 7; $K type "wallet"; sleep 2; $K key down; $K key ret; sleep 3; shot 05-settings-wallet
$K key ctrl-l 2>/dev/null; $K key alt-f4; sleep 1
launch "Fab Terminal" 6; shot 06-terminal-window; $K key alt-f4; sleep 1
launch "Fab Software" 10; shot 06b-software-window; $K key alt-f4; sleep 1
launch "Fab Files" 6; shot 06c-files-window; $K key alt-f4; sleep 1
run "fabos-command-center" 7; shot 07-command-center-dark; $K key alt-f4; sleep 1
run "plasma-apply-colorscheme BreezeLight" 6; shot 08-desktop-light
run "fabos-command-center" 7; shot 09-command-center-light; $K key alt-f4; sleep 1
run "fabos-updates" 7; shot 10-updates-light; $K key alt-f4; sleep 1
run "fabos-feedback" 6; shot 11-feedback-light; $K key alt-f4; sleep 1
run "systemsettings kcm_about-distro" 7; shot 12-about-light; $K key alt-f4; sleep 1
run "plasma-apply-colorscheme BreezeDark" 5
[ "${1:-}" = "--keep" ] || python3 -c "
import socket; s=socket.socket(socket.AF_UNIX); s.connect('build/qemu-monitor.sock'); s.sendall(b'quit\n'); s.close()" 2>/dev/null
echo "tour done -> build/screenshots/tour-*.png"
