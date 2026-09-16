#!/usr/bin/env bash
# End-to-end update-channel test: boot an OLDER installed Fab OS disk, let it fetch the signed Patience AI repository
# (https://fabos.patienceai.in/apt, preconfigured in the image), confirm newer fabos-* packages are offered, install them
# through the same helper Fab Updates uses, and verify the installed version changed. PASS only on real version change.
#   tests/update-channel-test.sh [--disk build/fabos-vm-old.img] [--expect 1.0-2] [--repo-url URL] [--overlay]
#   --repo-url URL  rewrite the guest's Fab OS apt source to URL first (default: the shipped public URL is left as is).
#                   A repository served from this host is reached as http://10.0.2.2:PORT (tests/ota-local-vm.sh does
#                   that end to end, with the post-upgrade assertions); the key stays the shipped archive key.
#   --overlay       boot a disposable qcow2 overlay instead of writing the disk (the default writes the disk, which is
#                   how build/fabos-vm-old.img was carried from 1.0-1 to the previous release each round)
# Runs under flock /tmp/fabos-vm.lock like every other VM test (one VM at a time on this host).
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
DISK=build/fabos-vm-old.img; EXPECT=""; REPO_URL=""; OVERLAY=0
while [ $# -gt 0 ]; do case $1 in --disk) DISK=$2; shift;; --expect) EXPECT=$2; shift;; --repo-url) REPO_URL=$2; shift;; --overlay) OVERLAY=1;; esac; shift; done
if [ -z "${UCT_INNER:-}" ]; then UCT_INNER=1 exec flock -w 5400 /tmp/fabos-vm.lock "$0" --disk "$DISK" --expect "$EXPECT" --repo-url "$REPO_URL" $( [ $OVERLAY = 1 ] && echo --overlay ); fi
[ -f "$DISK" ] || { echo "no $DISK"; exit 1; }
pgrep -f "^qemu-system" >/dev/null && { echo "a VM is already running; stop it first"; exit 1; }
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
BOOT_DISK=$DISK
if [ $OVERLAY = 1 ]; then BOOT_DISK=/tmp/update-channel-$$.qcow2; qemu-img create -f qcow2 -b "$(cd "$(dirname "$DISK")" && pwd)/$(basename "$DISK")" -F raw "$BOOT_DISK" > /dev/null || exit 1; trap 'rm -f "$BOOT_DISK"' EXIT; fi
(scripts/boot-vm.sh --headless --mem 2048 --cpus 2 --disk "$BOOT_DISK" > build/boot-update-test.out 2>&1 &)
for i in $(seq 1 100); do $SSH true 2>/dev/null && break; sleep 3; done; $SSH true || { echo "FAIL  no ssh"; exit 1; }
for i in $(seq 1 40); do $SSH "nm-online -q -t 5" 2>/dev/null && break; sleep 3; done
OUT=build/update-channel-test.out; : > "$OUT"; fail=0
before=$($SSH "dpkg-query -W -f='\${Version}' fabos-desktop" 2>/dev/null); echo "installed before: $before" | tee -a "$OUT"
if [ -n "$REPO_URL" ]; then
  echo "== pointing the guest's Fab OS source at $REPO_URL (Signed-By unchanged)" | tee -a "$OUT"
  $SSH "echo fabos | sudo -S sed -i 's|^URIs:.*|URIs: $REPO_URL|' /etc/apt/sources.list.d/fabos.sources 2>/dev/null"
fi
echo "== apt update against the ${REPO_URL:+rewritten }Fab OS source" | tee -a "$OUT"
$SSH "echo fabos | sudo -S apt-get update 2>&1 | grep -iE 'fabos.patienceai.in|Err|W:' | head -5" | tee -a "$OUT"
$SSH "grep -h URIs /etc/apt/sources.list.d/fabos.sources" | tee -a "$OUT"
upg=$($SSH "apt list --upgradable 2>/dev/null | grep -E '^fabos-' | head -12"); echo "upgradable:"; echo "$upg" | tee -a "$OUT"
[ -n "$upg" ] && echo "PASS  newer fabos packages offered by the channel" | tee -a "$OUT" || { echo "FAIL  channel offers no newer fabos packages" | tee -a "$OUT"; fail=1; }
echo "== unattended-upgrades allows the Patience AI origin" | tee -a "$OUT"
$SSH "echo fabos | sudo -S unattended-upgrade --dry-run -d 2>&1 | grep -iE 'Allowed origins|Patience AI|fabos-' | head -4" | tee -a "$OUT"
echo "== upgrade through the Fab Updates helper (same code path as the app)" | tee -a "$OUT"
$SSH "echo fabos | sudo -S /usr/lib/fabos/updates/helper.sh check 2>&1 | tail -3; echo fabos | sudo -S /usr/lib/fabos/updates/helper.sh upgrade 2>&1 | tail -4" | tee -a "$OUT"
after=$($SSH "dpkg-query -W -f='\${Version}' fabos-desktop" 2>/dev/null); echo "installed after:  $after" | tee -a "$OUT"
if [ -n "$after" ] && [ "$after" != "$before" ] && { [ -z "$EXPECT" ] || [ "$after" = "$EXPECT" ]; }; then echo "PASS  fabos-desktop updated $before -> $after" | tee -a "$OUT"; else echo "FAIL  version unchanged or unexpected ($before -> $after, expected ${EXPECT:-any newer})" | tee -a "$OUT"; fail=1; fi
$SSH "dpkg -l 'fabos-*' | awk '/^ii/{print \$2, \$3}'" | tee -a "$OUT"
$SSH "systemctl --user is-active fabos-agent; fabos status --brief" 2>/dev/null | tee -a "$OUT"
$SSH "cat /var/lib/fabos/updates/last-upgrade 2>/dev/null; fabos-updates --state --no-apt 2>/dev/null | python3 -c 'import json,sys; s=json.load(sys.stdin); print(\"banner:\", s[\"title\"], \"|\", s[\"banner\"])' 2>/dev/null" | tee -a "$OUT"
python3 -c "import socket; s=socket.socket(socket.AF_UNIX); s.connect('build/qemu-monitor.sock'); s.sendall(b'quit\n')" 2>/dev/null
for i in $(seq 1 30); do pgrep -f "qemu-system-x86_64.*$BOOT_DISK" >/dev/null || break; sleep 1; done; pkill -f "qemu-system-x86_64.*$BOOT_DISK" 2>/dev/null
echo "UPDATE CHANNEL: $([ $fail = 0 ] && echo PASS || echo FAIL)" | tee -a "$OUT"; exit $fail
