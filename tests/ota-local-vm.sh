#!/usr/bin/env bash
# Local over-the-air proof BEFORE anything is published (docs/UPDATES.md, "Testing"): boot a DISPOSABLE overlay of an
# older installed Fab OS disk, point its Fab OS apt source at a repository served from THIS machine, update through the
# same privileged helper Fab Updates uses, and assert the whole post-upgrade path — every fabos-* package at the
# repository's version, the dpkg trigger fabos-postupgrade ran, the stamp/journal and the banner state say what is left
# to do, the agent user service was restarted inside the session, and the fabos-desktop / fabos-branding hooks (greeter
# cache, initramfs) ran without a dpkg error. The base disk is never written; QEMU is always quit and the overlay removed.
#
#   tests/ota-local-vm.sh [--repo DIR] [--keyring FILE] [--stage [--bump SUFFIX]] [--disk IMG] [--build DIR] [--out DIR] [--keep]
#   --repo DIR      repository root to serve (holds dists/<suite>/InRelease + pool/); default build/apt-repo/<codename>,
#                   i.e. what scripts/publish-apt.sh --no-deploy just built and signed with the real archive key
#   --keyring FILE  public key the repository is signed with, when it is not the shipped archive key (test key)
#   --stage         build a throwaway repository first with tests/ota-stage-repo.sh: the built .debs with the working
#                   tree's fabos-updates / fabos-desktop / fabos-branding maintainer files, versions + SUFFIX
#                   (default +ota1) so the installed system sees an upgrade, signed with a throwaway key
#   --disk IMG      the older installed disk (raw), default build/fabos-vm-old.img — booted through a qcow2 overlay
#   --build DIR     where build/ lives (default <repo>/build; a worktree passes the main checkout's)
#   --out DIR       evidence: logs, JSON state, screenshots (default <build>/ota-local-vm)
#   --keep          keep the overlay and OVMF vars after the run (default: delete)
#   --fabos-only    put every non-Fab OS upgradable package on hold in the disposable guest before `helper.sh upgrade`, so a
#                   slow or unreachable third-party mirror (Firefox's 90 MB from packages.mozilla.org, Ubuntu -updates) cannot
#                   abort the apt transaction the Fab OS packages ride in. Reported as info; the default run holds nothing.
# Exit 0 only when every assertion passed. When the repository carries the versions already installed the run is a
# NO-OP (plumbing proof: source rewrite, signed fetch, helper, nothing to install) and the upgrade assertions are skipped.
# The whole VM session runs under flock /tmp/fabos-vm.lock (one VM at a time on this host).
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck disable=SC1091
. "$HERE/brand/brand.conf"
BUILD=${FABOS_BUILD:-$HERE/build}; REPO=""; KEYRING=""; STAGE=0; BUMP="+ota1"; DISK=""; OUT=""; KEEP=0; SUITE=$DISTRO_CODENAME; MEM=2048; FABOS_ONLY=0
while [ $# -gt 0 ]; do case $1 in
  --repo) REPO=$2; shift;; --keyring) KEYRING=$2; shift;; --stage) STAGE=1;; --bump) BUMP=$2; shift;; --disk) DISK=$2; shift;;
  --build) BUILD=$2; shift;; --out) OUT=$2; shift;; --keep) KEEP=1;; --suite) SUITE=$2; shift;; --mem) MEM=$2; shift;; --fabos-only) FABOS_ONLY=1;;
  *) echo "unknown arg $1"; exit 2;; esac; shift; done
BUILD=$(cd "$BUILD" && pwd) || { echo "no build dir $BUILD"; exit 1; }
DISK=${DISK:-$BUILD/fabos-vm-old.img}; OUT=${OUT:-$BUILD/ota-local-vm}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
[ -f "$DISK" ] || { echo "no disk $DISK"; exit 1; }
if [ -z "${OTA_INNER:-}" ]; then
  echo "== waiting for the VM lock (/tmp/fabos-vm.lock, up to 90 min)"
  OTA_INNER=1 exec flock -w 5400 /tmp/fabos-vm.lock "$0" --repo "${REPO:-}" --keyring "${KEYRING:-}" $( [ $STAGE = 1 ] && echo --stage ) --bump "$BUMP" --disk "$DISK" --build "$BUILD" --out "$OUT" --suite "$SUITE" --mem "$MEM" $( [ $KEEP = 1 ] && echo --keep ) $( [ $FABOS_ONLY = 1 ] && echo --fabos-only )
fi

LOG=$OUT/ota-local-vm.out; : > "$LOG"
say() { echo "$*" | tee -a "$LOG"; }
fail=0; npass=0; nfail=0
chk() { if [ "$2" = 0 ]; then say "PASS  $1"; npass=$((npass+1)); else say "FAIL  $1${3:+  [$3]}"; fail=1; nfail=$((nfail+1)); fi; }
info() { say "info  $*"; }

# ---- 0. repository -------------------------------------------------------------------------------------------------
if [ $STAGE = 1 ]; then
  say "== staging a local test repository (versions $BUMP, throwaway key)"
  "$HERE/tests/ota-stage-repo.sh" --out "$OUT/stage" --debs "$BUILD/debs" --bump "$BUMP" --sign-test-key --suite "$SUITE" > "$OUT/stage-repo.out" 2>&1 || { tail -5 "$OUT/stage-repo.out"; say "FAIL  stage repo"; exit 1; }
  REPO=$OUT/stage; KEYRING=$OUT/stage/test-archive-keyring.gpg
fi
REPO=${REPO:-$BUILD/apt-repo/$SUITE}
[ -f "$REPO/dists/$SUITE/InRelease" ] || { say "no signed repository at $REPO (dists/$SUITE/InRelease missing)"; exit 1; }
PACKAGES=$REPO/dists/$SUITE/main/binary-amd64/Packages
awk '/^Package:/{p=$2} /^Version:/{print p, $2}' "$PACKAGES" | sort > "$OUT/repo-versions.txt"
say "== repository $REPO (suite $SUITE): $(wc -l < "$OUT/repo-versions.txt") packages"; sed 's/^/   /' "$OUT/repo-versions.txt" | tee -a "$LOG"
REPO_UPD=$(awk '$1=="fabos-updates"{print $2}' "$OUT/repo-versions.txt")

# ---- 1. serve it to the guest (QEMU user networking: the host is 10.0.2.2; slirp maps that to the host's loopback) -------
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
(cd "$REPO" && python3 -m http.server --bind 127.0.0.1 "$PORT" > "$OUT/http.log" 2>&1 &)
sleep 1; HTTP_PID=$(pgrep -f "http.server --bind 127.0.0.1 $PORT" | head -1)
curl -fsS "http://127.0.0.1:$PORT/dists/$SUITE/InRelease" > /dev/null; chk "repository served on http://127.0.0.1:$PORT (guest: http://10.0.2.2:$PORT)" $?
GUEST_URL="http://10.0.2.2:$PORT"

# ---- 2. disposable VM -----------------------------------------------------------------------------------------------
if pgrep -f "^qemu-system" >/dev/null; then say "FAIL  another VM is running outside the lock"; kill "$HTTP_PID" 2>/dev/null; exit 1; fi
TAG=ota-$$; OVL=/tmp/r7-$TAG.qcow2; QMP=/tmp/r7-$TAG.qmp; MON=/tmp/r7-$TAG.mon; VARS=/tmp/r7-$TAG.vars.fd
qemu-img create -f qcow2 -b "$DISK" -F raw "$OVL" > /dev/null || { say "FAIL  overlay"; kill "$HTTP_PID" 2>/dev/null; exit 1; }
cp "$BUILD/OVMF_VARS.fd" "$VARS" 2>/dev/null || cp "$(ls /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd 2>/dev/null | head -1)" "$VARS"
cat > "$OUT/qmp.py" <<'PY'
import json, socket, sys
def qmp(cmd, **args):
    s = socket.socket(socket.AF_UNIX); s.settimeout(30); s.connect(sys.argv[1]); f = s.makefile("rwb", buffering=0)
    f.readline(); f.write(b'{"execute":"qmp_capabilities"}\n'); f.readline()
    f.write((json.dumps({"execute": cmd, "arguments": args}) + "\n").encode())
    while True:
        line = f.readline()
        if not line: return None
        o = json.loads(line)
        if "return" in o or "error" in o: return o
if sys.argv[2] == "screendump": print(qmp("screendump", filename=sys.argv[3], format="png"))
elif sys.argv[2] == "quit": print(qmp("quit"))
PY
cleanup() {
  say "== quitting QEMU"
  python3 "$OUT/qmp.py" "$QMP" quit >/dev/null 2>&1 || true
  for i in $(seq 1 30); do pgrep -f "qemu-system-x86_64.*$OVL" >/dev/null || break; sleep 1; done
  pkill -f "qemu-system-x86_64.*$OVL" 2>/dev/null || true
  kill "$HTTP_PID" 2>/dev/null || true
  if [ $KEEP = 0 ]; then rm -f "$OVL" "$VARS" "$QMP" "$MON"; else say "kept overlay $OVL"; fi
}
trap cleanup EXIT
say "== booting overlay $OVL of $DISK"
("$HERE/scripts/boot-vm.sh" --headless --mem "$MEM" --cpus 2 --disk "$OVL" --qmp "$QMP" --monitor "$MON" --serial "$OUT/serial.log" --vars "$VARS" > "$OUT/boot.out" 2>&1 &)
SSH="sshpass -p fabos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -p 2222 fabos@127.0.0.1"
SCP="sshpass -p fabos scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P 2222"
vm() { timeout "${VM_TIMEOUT:-120}" $SSH "$@"; }
for i in $(seq 1 120); do vm true 2>/dev/null && break; sleep 3; done
vm true 2>/dev/null; chk "guest reachable over ssh" $?; [ $fail = 1 ] && exit 1
for i in $(seq 1 40); do vm "nm-online -q -t 5" 2>/dev/null && break; sleep 3; done
vm "curl -fsS -m 10 $GUEST_URL/dists/$SUITE/InRelease > /dev/null"; chk "guest fetches the local repository at $GUEST_URL" $?
shot() { python3 "$OUT/qmp.py" "$QMP" screendump "$OUT/$1.png" > /dev/null 2>&1 && say "shot  $OUT/$1.png"; }

# ---- 3. before -------------------------------------------------------------------------------------------------------
vm "dpkg-query -W -f='\${Package} \${Version}\n' 'fabos-*' | sort" > "$OUT/versions-before.txt"; say "== installed before:"; sed 's/^/   /' "$OUT/versions-before.txt" | tee -a "$LOG"
BEFORE_UPD=$(awk '$1=="fabos-updates"{print $2}' "$OUT/versions-before.txt")
AGENT_BEFORE=$(vm "systemctl --user show -p ActiveEnterTimestampMonotonic --value fabos-agent.service" 2>/dev/null | tr -d '\r'); info "agent ActiveEnterTimestampMonotonic before: $AGENT_BEFORE ($(vm 'systemctl --user is-active fabos-agent.service' 2>/dev/null))"
DPKG_LINES=$(vm "wc -l < /var/log/dpkg.log" 2>/dev/null | tr -d '\r'); TERM_LINES=$(vm "echo fabos | sudo -S sh -c 'wc -l < /var/log/apt/term.log' 2>/dev/null" | tr -d '\r'); DPKG_LINES=${DPKG_LINES:-0}; TERM_LINES=${TERM_LINES:-0}
SDDM_CACHE_BEFORE=$(vm "echo fabos | sudo -S sh -c 'test -d /var/lib/sddm/.cache/sddm-greeter-qt6/qmlcache && echo yes || echo no' 2>/dev/null" | tr -d '\r' | grep -E '^(yes|no)$' | head -1); SDDM_CACHE_BEFORE=${SDDM_CACHE_BEFORE:-unknown}; info "greeter QML cache present before the upgrade: $SDDM_CACHE_BEFORE"
vm "grep -h URIs /etc/apt/sources.list.d/fabos.sources" | tee -a "$LOG"

# ---- 4. point the guest at the local repository: a SEPARATE source file next to the shipped one --------------------------------
# Not a rewrite of /etc/apt/sources.list.d/fabos.sources: fabos-branding ships that file as plain package content (no conffile),
# so upgrading fabos-branding in the middle of the run puts the shipped URI back — apt then loses the local candidates and
# unattended-upgrades, which installs one package per dpkg run, silently skips everything after that step (seen 2026-09-16:
# 9 of 10, then 2 of 10 packages installed). The shipped source stays as it is (the public server carries the older release,
# so it never competes); the test source carries the local repository and the shipped key (or the test key).
cat > "$OUT/guest-prepare.sh" <<EOF
set -e
KEY=/usr/share/keyrings/fabos-archive-keyring.gpg
if [ -f /tmp/test-archive-keyring.gpg ]; then install -m 0644 /tmp/test-archive-keyring.gpg /usr/share/keyrings/fabos-ota-test-keyring.gpg; KEY=/usr/share/keyrings/fabos-ota-test-keyring.gpg; fi
printf 'Types: deb\nURIs: %s\nSuites: %s\nComponents: main\nSigned-By: %s\n' "$GUEST_URL" "$SUITE" "\$KEY" > /etc/apt/sources.list.d/zz-fabos-ota-test.sources
cat /etc/apt/sources.list.d/zz-fabos-ota-test.sources
EOF
$SCP "$OUT/guest-prepare.sh" fabos@127.0.0.1:/tmp/guest-prepare.sh > /dev/null
[ -n "$KEYRING" ] && $SCP "$KEYRING" fabos@127.0.0.1:/tmp/test-archive-keyring.gpg > /dev/null
vm "echo fabos | sudo -S sh /tmp/guest-prepare.sh 2>&1 | grep -v '^\[sudo\]'" | tee -a "$LOG"
vm "grep -q '^URIs: $GUEST_URL' /etc/apt/sources.list.d/zz-fabos-ota-test.sources && grep -q '^URIs: https://' /etc/apt/sources.list.d/fabos.sources"; chk "guest gets the local repository as an extra source (shipped fabos.sources untouched)" $?

# ---- 5. the same code path as Fab Updates: helper.sh check, then upgrade -------------------------------------------------------
say "== helper.sh check"
VM_TIMEOUT=600 vm "echo fabos | sudo -S /usr/lib/fabos/updates/helper.sh check 2>&1 | grep -v '^\[sudo\]'" > "$OUT/helper-check.log"; grep -E "^fabos-|== check" "$OUT/helper-check.log" | tee -a "$LOG"
OFFERED=$(grep -cE '^fabos-.*upgradable' "$OUT/helper-check.log" || true)
if [ "$BEFORE_UPD" = "$REPO_UPD" ]; then MODE=noop; info "repository version $REPO_UPD == installed $BEFORE_UPD: NO-OP run (plumbing only)"; else MODE=upgrade; chk "newer Fab OS packages offered ($OFFERED): $BEFORE_UPD -> $REPO_UPD" $([ "$OFFERED" -gt 0 ] && echo 0 || echo 1); fi
if [ $FABOS_ONLY = 1 ]; then
  HELD=$(vm "apt list --upgradable 2>/dev/null | grep -v '^Listing' | grep -v '^fabos-' | cut -d/ -f1 | tr '\n' ' '" | tr -d '\r')
  if [ -n "${HELD// /}" ]; then
    vm "echo fabos | sudo -S apt-mark hold $HELD 2>&1 | grep -v '^\[sudo\]'" | sed 's/^/   /' | tee -a "$LOG"
    info "--fabos-only: held in the disposable guest for this run (third-party mirrors kept out of the transaction): $HELD"
  else info "--fabos-only: nothing else was upgradable; nothing held"; fi
fi
say "== helper.sh upgrade"
VM_TIMEOUT=1800 vm "echo fabos | sudo -S /usr/lib/fabos/updates/helper.sh upgrade 2>&1 | grep -v '^\[sudo\]'" > "$OUT/helper-upgrade.log"; rc=$?
tail -4 "$OUT/helper-upgrade.log" | sed 's/^/   /' | tee -a "$LOG"
chk "helper.sh upgrade exit 0 (== upgrade complete)" $([ $rc = 0 ] && grep -q "== upgrade complete" "$OUT/helper-upgrade.log" && echo 0 || echo 1)
shot after-upgrade

# ---- 6. every fabos-* package at the repository's version -----------------------------------------------------------------------
vm "dpkg-query -W -f='\${Package} \${Version}\n' 'fabos-*' | sort" > "$OUT/versions-after.txt"; say "== installed after:"; sed 's/^/   /' "$OUT/versions-after.txt" | tee -a "$LOG"
bad=""
while read -r p v; do iv=$(awk -v p="$p" '$1==p{print $2}' "$OUT/versions-after.txt"); [ "$iv" = "$v" ] || bad="$bad $p($iv!=$v)"; done < "$OUT/repo-versions.txt"
chk "every fabos-* package is at the repository version" $([ -z "$bad" ] && echo 0 || echo 1) "$bad"
notii=$(vm "dpkg -l 'fabos-*' | awk '/^[a-z]/ && \$1!=\"ii\"{print}'; echo __END__" 2>/dev/null | tr -d '\r')   # __END__ proves the ssh call itself worked
chk "no fabos package left half-configured" $([ "$notii" = "__END__" ] && echo 0 || echo 1) "$(echo "$notii" | grep -v __END__ | head -3 | tr '\n' ';')"

# ---- 7. dpkg / apt logs: trigger processed, hooks ran, no maintainer-script error -------------------------------------------------
vm "tail -n +$((DPKG_LINES+1)) /var/log/dpkg.log" > "$OUT/dpkg-run.log" 2>/dev/null
vm "echo fabos | sudo -S sh -c 'tail -n +$((TERM_LINES+1)) /var/log/apt/term.log' 2>/dev/null" > "$OUT/apt-term-run.log"
if [ $MODE = upgrade ]; then
  grep -q "trigproc fabos-updates" "$OUT/dpkg-run.log"; chk "dpkg processed the fabos-postupgrade trigger (trigproc fabos-updates)" $?
  grep -q "status installed fabos-desktop" "$OUT/dpkg-run.log" && grep -q "status installed fabos-branding" "$OUT/dpkg-run.log"; chk "fabos-desktop and fabos-branding configured" $?
  grep -qiE "update-initramfs: Generating|update-initramfs" "$OUT/apt-term-run.log"; chk "fabos-branding hook rebuilt the initramfs (Plymouth theme)" $?
  # only meaningful when the greeter had compiled QML before the upgrade (a disk that showed the login screen has it)
  if [ "$SDDM_CACHE_BEFORE" = yes ]; then
    vm "echo fabos | sudo -S test ! -d /var/lib/sddm/.cache/sddm-greeter-qt6/qmlcache 2>/dev/null"; chk "fabos-desktop hook dropped the greeter QML cache (it existed before the upgrade)" $?
  else
    info "greeter QML cache was absent before the upgrade ($SDDM_CACHE_BEFORE); the cache-drop hook had nothing to do — not asserted"
  fi
fi
err=$(grep -iE "dpkg: error|returned error exit status|Traceback|update-initramfs: failed|E: " "$OUT/apt-term-run.log" | head -3)
chk "no dpkg/maintainer-script error in the apt run" $([ -z "$err" ] && echo 0 || echo 1) "$err"

# ---- 8. the stamp the banner reads ------------------------------------------------------------------------------------------------
if [ $MODE = upgrade ]; then
  vm "cat /var/lib/fabos/updates/last-upgrade" > "$OUT/last-upgrade.txt" 2>/dev/null; sed 's/^/   /' "$OUT/last-upgrade.txt" | tee -a "$LOG"
  grep -q "packages=.*fabos-desktop" "$OUT/last-upgrade.txt" && grep -q "version=$REPO_UPD" "$OUT/last-upgrade.txt"; chk "stamp /var/lib/fabos/updates/last-upgrade names the packages and version $REPO_UPD" $?
  grep -q "classes=reboot,session,agent" "$OUT/last-upgrade.txt"; chk "stamp classes: reboot (branding) + session (desktop/agent) + agent" $?
  vm "test -s /var/lib/fabos/updates/versions && test -r /var/lib/fabos/updates/journal"; chk "versions snapshot + journal present and world-readable" $?
  # the banner state, computed as the logged-in user (same code the Fab Updates window runs)
  vm "fabos-updates --state --no-apt" > "$OUT/state.json" 2>/dev/null
  python3 - "$OUT/state.json" "$REPO_UPD" <<'PY' | tee -a "$LOG"; chk "banner state: needs_restart + needs_logout, text 'Restart to finish', version" ${PIPESTATUS[0]}
import json, sys
st = json.load(open(sys.argv[1])); ok = st["needs_restart"] and st["needs_logout"] and "Restart to finish" in st["banner"] and st["version"] == sys.argv[2]
print("   banner:", st["title"], "|", st["banner"]); print("   restart_reasons:", st["restart_reasons"]); print("   logout_reasons:", st["logout_reasons"])
sys.exit(0 if ok else 1)
PY
fi

# ---- 9. inside the session: the notifier ran, the agent was restarted (idle), nothing else was touched --------------------------------
if [ $MODE = upgrade ]; then
  for i in $(seq 1 30); do a=$(vm "systemctl --user show -p ActiveEnterTimestampMonotonic --value fabos-agent.service" 2>/dev/null | tr -d '\r'); [ -n "$a" ] && [ "$a" != "$AGENT_BEFORE" ] && break; sleep 3; done
  AGENT_AFTER=$a; info "agent ActiveEnterTimestampMonotonic after: $AGENT_AFTER ($(vm 'systemctl --user is-active fabos-agent.service' 2>/dev/null))"
  chk "fabos-agent user service restarted after the upgrade and is active" $([ -n "$AGENT_AFTER" ] && [ "$AGENT_AFTER" != "$AGENT_BEFORE" ] && [ "$(vm 'systemctl --user is-active fabos-agent.service' 2>/dev/null | tr -d '\r')" = active ] && echo 0 || echo 1)
  vm "journalctl --user -u fabos-update-notify.service --no-pager -o cat" > "$OUT/notify-journal.log" 2>/dev/null; sed 's/^/   /' "$OUT/notify-journal.log" | tail -8 | tee -a "$LOG"
  grep -q "agent: try-restart -> 0" "$OUT/notify-journal.log"; chk "notifier journal: agent try-restart -> 0" $?
  grep -q "notify: .*Restart to finish" "$OUT/notify-journal.log"; chk "notifier journal: 'Restart to finish' notification shown in the session" $?
  P0=$(vm "systemctl --user show -p ActiveEnterTimestampMonotonic --value plasma-plasmashell.service" 2>/dev/null | tr -d '\r'); info "plasmashell ActiveEnterTimestampMonotonic: $P0 (must predate the agent restart $AGENT_AFTER)"
  chk "plasmashell was not restarted by the update" $([ -n "$P0" ] && [ "$P0" -lt "${AGENT_AFTER:-0}" ] && echo 0 || echo 1)
  vm "systemctl --user is-enabled fabos-update-notify.timer 2>/dev/null | grep -q enabled"; chk "user timer fabos-update-notify.timer enabled (--global)" $?
  vm "systemctl --user is-active fabos-update-notify.timer 2>/dev/null | grep -qE 'active|waiting'"; chk "user timer running in the live session (poked without re-login)" $?
fi
vm "systemctl is-enabled fabos-update-check.timer 2>/dev/null | grep -q enabled"; chk "system timer fabos-update-check.timer enabled" $?

# ---- 10. unattended-upgrades sees the right origins (Fab OS + Ubuntu security, literal names) ------------------------------------------
vm "echo fabos | sudo -S unattended-upgrade --dry-run -d 2>&1 | grep -m1 'Allowed origins'" > "$OUT/uu-origins.txt"; sed 's/^/   /' "$OUT/uu-origins.txt" | cut -c1-400 | tee -a "$LOG"
grep -q "o=$VENDOR_NAME,a=$SUITE" "$OUT/uu-origins.txt"; chk "unattended-upgrades allows origin $VENDOR_NAME:$SUITE" $?
if [ $MODE = upgrade ]; then grep -q "o=Ubuntu,a=$BASE_CODENAME-security" "$OUT/uu-origins.txt"; chk "unattended-upgrades allows Ubuntu:$BASE_CODENAME-security (literal; distro_id here is $(vm 'lsb_release -is' 2>/dev/null | tr -d '\r'))" $?; fi
if [ $MODE = upgrade ]; then grep -q "site=packages.mozilla.org" "$OUT/uu-origins.txt"; chk "unattended-upgrades matches Firefox by site=packages.mozilla.org (Origins-Pattern)" $?; fi
info "Mozilla release line as apt sees it: $(vm "apt-cache policy 2>/dev/null | grep -m1 -E 'packages.mozilla.org' " 2>/dev/null | tr -d '\r' | cut -c1-160)"
vm "echo fabos | sudo -S unattended-upgrade --dry-run 2>&1 | grep -viE 'sudo|^$' | tail -3" | sed 's/^/   /' | tee -a "$LOG"

# ---- 10b. the notification path itself, probed from inside the session: capabilities, a critical popup with buttons ---------------------
vm "dbus-send --session --print-reply --dest=org.freedesktop.Notifications /org/freedesktop/Notifications org.freedesktop.Notifications.GetCapabilities 2>/dev/null | grep -oE 'string \"[a-z-]+\"' | tr -d '\"' | sed 's/string //' | tr '\n' ' '" > "$OUT/notify-caps.txt" 2>/dev/null; info "notification server capabilities: $(cat "$OUT/notify-caps.txt")"
grep -qw actions "$OUT/notify-caps.txt"; chk "notification server supports actions (buttons)" $?
vm "t0=\$(date +%s%N); timeout 12 notify-send -a 'Fab OS Updates' -i fabos-updates -u critical -A open='Open Fab OS Updates' -A later=Later 'probe' 'notify-send action probe (closed by the test)'; rc=\$?; echo \"rc=\$rc elapsed_ms=\$(( (\$(date +%s%N) - t0) / 1000000 ))\"" > "$OUT/notify-probe.txt" 2>&1; info "notify-send -A probe: $(tr -d '\r' < "$OUT/notify-probe.txt")"
shot notify-send-probe
if [ $MODE = upgrade ]; then
  # the shipped path (state.py notify_dbus): a critical popup with "Open Fab OS Updates" / "Later", held 6 s for the screenshot
  (sleep 3; python3 "$OUT/qmp.py" "$QMP" screendump "$OUT/notify-dbus-probe.png" > /dev/null 2>&1) &
  vm "python3 /usr/lib/fabos/updates/state.py notify-probe 6" > "$OUT/notify-dbus-probe.txt" 2>&1; wait
  info "D-Bus notify probe: $(tr -d '\r' < "$OUT/notify-dbus-probe.txt" | tail -1)"
  grep -q '"closed_by": "probe"' "$OUT/notify-dbus-probe.txt"; chk "notification via D-Bus with action buttons shown and closed in the session ($OUT/notify-dbus-probe.png)" $?
  if grep -q "notify: .*Restart to finish" "$OUT/notify-journal.log" && ! grep -q "notify-send: rc=\|Actions are not supported\|dbus path failed" "$OUT/notify-journal.log"; then
    chk "notifier used the D-Bus path (notification logged, no notify-send fallback message in its journal)" 0
  else chk "notifier used the D-Bus path (notification logged, no notify-send fallback message in its journal)" 1 "$(grep -m1 'notify-send: rc=\|dbus path failed' "$OUT/notify-journal.log")"; fi
fi
vm "gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications --method org.freedesktop.Notifications.GetServerInformation 2>/dev/null" | tr -d '\r' | sed 's/^/   /' | tee -a "$LOG"

# ---- 11. the Fab Updates window with its banner (offscreen render inside the guest; same state as the session) ----------------------------
if [ $MODE = upgrade ]; then   # --screenshot exists from 1.0-7 on; a NO-OP run still has the older app
  vm "QT_QPA_PLATFORM=offscreen timeout 60 fabos-updates --screenshot /tmp/fabos-updates.png >/dev/null 2>&1; test -s /tmp/fabos-updates.png" && $SCP fabos@127.0.0.1:/tmp/fabos-updates.png "$OUT/fabos-updates-banner.png" > /dev/null 2>&1
  chk "Fab Updates window rendered in the guest ($OUT/fabos-updates-banner.png)" $([ -s "$OUT/fabos-updates-banner.png" ] && echo 0 || echo 1)
fi
shot end
say "OTA LOCAL VM ($MODE, $REPO_UPD): $([ $fail = 0 ] && echo PASS || echo FAIL)  ($npass passed, $nfail failed)"
exit $fail
