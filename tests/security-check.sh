#!/usr/bin/env bash
# Static security checks on the built image (enterprise controls, ADR-0017, SECURITY.md, docs/ENTERPRISE.md).
# Same style as tests/branding-check.sh: one PASS/FAIL line per check, exit 1 when anything failed, exit 2 when the image
# is missing or podman is unusable (nothing may pass vacuously).
#   tests/security-check.sh [vm|iso]            image: localhost/fabos:<profile>
# Checks run read-only inside the image with podman (nothing is booted); source-tree checks read this checkout.
set -uo pipefail; PROFILE=${1:-vm}; TAG=fabos:$PROFILE; fail=0; npass=0; nfail=0
SRC=$(cd "$(dirname "$0")/.." && pwd)
command -v podman >/dev/null 2>&1 || { echo "security-check: podman not found"; exit 2; }
podman image exists "localhost/$TAG" || { echo "security-check: no image localhost/$TAG (build it first: scripts/build-rootfs.sh $PROFILE)"; exit 2; }
podman run --rm "$TAG" true >/dev/null 2>&1 || { echo "security-check: cannot run localhost/$TAG"; exit 2; }
chk() { if eval "$2"; then echo "PASS  $1"; npass=$((npass+1)); else echo "FAIL  $1"; fail=1; nfail=$((nfail+1)); fi; }
R() { podman run --rm "$TAG" bash -c "$1"; }
# Negative checks must not pass when podman itself fails: the command inside the image prints a verdict token and the check
# compares the token — no output (podman error, image missing) is a FAIL, never a PASS.
img_absent()   { [ "$(R "test -e $1 && echo present || echo absent" 2>/dev/null)" = absent ]; }
img_nomatch()  { [ "$(R "{ $1; } 2>/dev/null | grep -q . && echo match || echo nomatch" 2>/dev/null)" = nomatch ]; }
img_empty()    { local out; out=$(R "{ $1; } 2>/dev/null; echo __END__" 2>/dev/null) || return 1; [ "$out" = "__END__" ]; }
# Baseline checks: the listing must come from a successful run and be non-empty; then nothing may lie beyond the baseline.
img_baseline() { local out; out=$(R "$1" 2>/dev/null) || return 1; [ -n "$out" ] || return 1; [ -z "$(printf '%s\n' "$out" | grep -vxF -f <(grep -v '^#' "$2"))" ]; }
AGENTD=/usr/lib/fabos/agent/fabos_agentd.py

# ---- kernel hardening (sysctl)
SYSCTL=/etc/sysctl.d/70-fabos-hardening.conf
chk "sysctl: 70-fabos-hardening.conf shipped"            "R 'test -f $SYSCTL'"
for kv in kernel.yama.ptrace_scope=1 kernel.kptr_restrict=2 kernel.dmesg_restrict=1 fs.protected_symlinks=1 fs.protected_hardlinks=1 fs.protected_fifos=2 fs.protected_regular=2 fs.suid_dumpable=0 net.ipv4.conf.all.rp_filter=1 net.ipv4.tcp_syncookies=1 net.core.bpf_jit_harden=2; do
  k=${kv%%=*}; v=${kv##*=}
  chk "sysctl: $k = $v"                                     "R 'grep -Eq \"^$k *= *$v\$\" $SYSCTL 2>/dev/null'"
done
# every key is a real knob of the image's kernel: present under /proc/sys in the build container (shared host kernel) or,
# for the init-netns-only BPF knob, enabled in the image kernel's configuration
chk "sysctl: every key exists in /proc/sys or the kernel config" "R 'for k in \$(grep -Eo \"^[a-z][a-z0-9._]+\" $SYSCTL); do f=/proc/sys/\$(echo \$k | tr . /); [ -e \"\$f\" ] || { [ \$k = net.core.bpf_jit_harden ] && grep -q ^CONFIG_BPF_JIT=y /boot/config-*; } || { echo missing \$k; exit 1; }; done'"
chk "sysctl: Yama (ptrace_scope) built into the image kernel" "R 'grep -q ^CONFIG_SECURITY_YAMA=y /boot/config-*'"

# ---- AppArmor
chk "apparmor: package + apparmor.service enabled"        "R 'dpkg -s apparmor >/dev/null 2>&1 && systemctl is-enabled apparmor >/dev/null'"
for p in fabos-agentd fabos-voiced fabos-llama; do
  chk "apparmor: /etc/apparmor.d/$p present and parses (apparmor_parser -Q -K)" "R 'test -f /etc/apparmor.d/$p && apparmor_parser -Q -K /etc/apparmor.d/$p 2>/dev/null'"
done
chk "apparmor: fabos-llama enforce; fabos-voiced + fabos-agentd complain with their path to enforce documented" "R 'test -f /etc/apparmor.d/fabos-voiced && test -f /etc/apparmor.d/fabos-llama && test -f /etc/apparmor.d/fabos-agentd && ! grep -q complain /etc/apparmor.d/fabos-llama && grep -q \"flags=(complain\" /etc/apparmor.d/fabos-voiced && grep -q \"Path to enforce\" /etc/apparmor.d/fabos-voiced && grep -q \"flags=(complain\" /etc/apparmor.d/fabos-agentd && grep -q \"Path to enforce\" /etc/apparmor.d/fabos-agentd'"
chk "apparmor: fabos-voiced lists every helper voicelib execs (wpctl, true->gnutrue, ffplay/mpv fallbacks, Rust coreutils)" "R 'grep -q wpctl /etc/apparmor.d/fabos-voiced && grep -q gnutrue /etc/apparmor.d/fabos-voiced && grep -q ffplay /etc/apparmor.d/fabos-voiced && grep -q cargo/bin/coreutils /etc/apparmor.d/fabos-voiced && grep -q gnutrue /etc/apparmor.d/fabos-agentd && grep -q sudo.ws /etc/apparmor.d/fabos-agentd'"
chk "apparmor: profiles attach to the paths the units execute"  "R 'grep -q \"^ExecStart=/usr/lib/fabos/agent/fabos_agentd.py\" /usr/lib/systemd/user/fabos-agent.service && grep -q \"^ExecStart=/usr/lib/fabos/voice/fabos_voiced.py\" /usr/lib/systemd/user/fabos-voiced.service.d/50-fabos-apparmor.conf && grep -q \"^ExecStart=/usr/lib/fabos/ai/llama-start.sh\" /usr/lib/systemd/user/fabos-llama.service && test -x /usr/lib/fabos/agent/fabos_agentd.py && test -x /usr/lib/fabos/voice/fabos_voiced.py && test -x /usr/lib/fabos/ai/llama-start.sh'"
chk "apparmor: bwrap profile (userns grant for the agent's sandbox) present; userns restriction on" "R 'test -f /etc/apparmor.d/bwrap-userns-restrict && grep -rq \"apparmor_restrict_unprivileged_userns *= *1\" /usr/lib/sysctl.d/'"

# ---- root path: polkit, no NOPASSWD
POLICY=/usr/share/polkit-1/actions/in.patienceai.fabos.rootexec.policy
chk "root: no /etc/sudoers.d/fabos-agent (NOPASSWD rule removed)" "img_absent /etc/sudoers.d/fabos-agent"
chk "root: no NOPASSWD rule for rootexec anywhere in sudoers"  "img_nomatch 'grep -rE \"NOPASSWD.*rootexec\" /etc/sudoers /etc/sudoers.d'"
chk "root: sudoers.d/fabos-hardening (passwd_timeout, use_pty, logfile) 0440 and parses" "R 'test -f /etc/sudoers.d/fabos-hardening && [ \"\$(stat -c %a /etc/sudoers.d/fabos-hardening)\" = 440 ] && grep -q passwd_timeout /etc/sudoers.d/fabos-hardening && grep -q use_pty /etc/sudoers.d/fabos-hardening && grep -q logfile /etc/sudoers.d/fabos-hardening && visudo -cqf /etc/sudoers.d/fabos-hardening'"
chk "root: pkexec + polkitd installed"                       "R 'dpkg -s pkexec >/dev/null 2>&1 && dpkg -s polkitd >/dev/null 2>&1 && test -u /usr/bin/pkexec'"
chk "root: polkit action file present and well-formed XML"   "R 'test -f $POLICY && python3 -c \"import xml.etree.ElementTree as E; E.parse(\\\"$POLICY\\\")\"'"
chk "root: action defaults no / no / auth_admin_keep, exec.path = rootexec" "R 'test -f $POLICY && python3 -c \"
import xml.etree.ElementTree as E
a = E.parse(\\\"$POLICY\\\").getroot().find(\\\"action\\\"); d = {x.tag: x.text for x in a.find(\\\"defaults\\\")}
assert a.get(\\\"id\\\") == \\\"in.patienceai.fabos.rootexec\\\", a.get(\\\"id\\\")
assert d == {\\\"allow_any\\\": \\\"no\\\", \\\"allow_inactive\\\": \\\"no\\\", \\\"allow_active\\\": \\\"auth_admin_keep\\\"}, d
assert [x.text for x in a.findall(\\\"annotate\\\") if x.get(\\\"key\\\") == \\\"org.freedesktop.policykit.exec.path\\\"] == [\\\"/usr/lib/fabos/agent/rootexec\\\"]
\"'"
chk "root: no polkit rule weakens in.patienceai.fabos.rootexec" "img_nomatch 'grep -rl in.patienceai.fabos.rootexec /etc/polkit-1/rules.d /usr/share/polkit-1/rules.d'"
chk "root: daemon invokes pkexec rootexec (no sudo -n literal)"  "R 'grep -q \"\\[\\\"pkexec\\\", ROOTEXEC, aid\\]\" $AGENTD && ! grep -q \"\\\"sudo\\\", \\\"-n\\\", \\\"/usr/lib/fabos/agent/rootexec\\\"\" $AGENTD'"
chk "root: rootexec checks PKEXEC_UID, record id, sha256, owner, age; root-owned 0755" "R 'grep -q PKEXEC_UID /usr/lib/fabos/agent/rootexec && grep -q command_sha256 /usr/lib/fabos/agent/rootexec && grep -q \"st_uid != uid\" /usr/lib/fabos/agent/rootexec && grep -q MAX_AGE_S /usr/lib/fabos/agent/rootexec && [ \$(stat -c %U:%a /usr/lib/fabos/agent/rootexec) = root:755 ]'"
chk "root: rootexec honours a sudo launcher only when policy.json sets require_password_for_root false" "R 'grep -q \"def sudo_path_allowed\" /usr/lib/fabos/agent/rootexec && grep -q \"refusing the sudo launcher\" /usr/lib/fabos/agent/rootexec'"
chk "root: /var/log/fabos for the rootexec audit log (root:adm 0750)" "R '[ \"\$(stat -c %U:%G:%a /var/log/fabos 2>/dev/null)\" = root:adm:750 ]'"

# ---- daemon and sandbox
chk "daemon: binds 127.0.0.1 only"                           "R 'grep -q \"ThreadingHTTPServer((\\\"127.0.0.1\\\", PORT)\" $AGENTD && ! grep -q \"0\\.0\\.0\\.0\" $AGENTD'"
chk "daemon: token file created 0600"                         "R 'grep -q \"os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600\" $AGENTD'"
chk "daemon: policy.json loader present"                      "R 'grep -q \"class Policy\" $AGENTD && grep -q \"/etc/fabos/policy.json\" $AGENTD && grep -q SIGHUP $AGENTD'"
chk "daemon: run_shell sandbox (bubblewrap) + sandbox recorded on the step" "R 'dpkg -s bubblewrap >/dev/null 2>&1 && grep -q \"def sandbox_argv\" $AGENTD && grep -q \"\\\"sandbox\\\": sandbox\" $AGENTD && grep -q -- --unshare-pid $AGENTD'"
chk "daemon: children get an allowlisted environment; tool commands lose the ssh/gpg agents; agent sockets masked in the sandbox" "R 'grep -q \"def clean_env\" $AGENTD && grep -q \"def tool_env\" $AGENTD && grep -q \"^ENV_DENY = \" $AGENTD && grep -q \"def sandbox_masked\" $AGENTD && grep -q \"env=self.agent.tool_env(senv)\" $AGENTD && ! grep -q \"env = dict(os.environ)\" $AGENTD'"
chk "daemon: secrets + token hard-denied to tools"            "R 'grep -q \"def protected_path\" $AGENTD && grep -c \"check_protected(p)\" $AGENTD | grep -qx 3'"
chk "daemon: HMAC audit chain + verify/export, CLI has audit" "R 'grep -q \"def audit_hmac\" $AGENTD && grep -q \"def audit_verify\" $AGENTD && grep -q \"/audit/export\" $AGENTD && grep -q \"\\\"audit\\\"\" /usr/bin/fabos'"
chk "daemon: user unit LimitCORE=0, TasksMax=512, MemoryHigh; no NoNewPrivileges (pkexec is setuid)" "R 'grep -q ^LimitCORE=0 /usr/lib/systemd/user/fabos-agent.service && grep -q ^TasksMax=512 /usr/lib/systemd/user/fabos-agent.service && grep -q ^MemoryHigh= /usr/lib/systemd/user/fabos-agent.service && ! grep -q ^NoNewPrivileges /usr/lib/systemd/user/fabos-agent.service'"
chk "daemon: user units verify (systemd-analyze --user verify)" "R 'mkdir -p /tmp/xr && chmod 700 /tmp/xr && XDG_RUNTIME_DIR=/tmp/xr systemd-analyze --user verify /usr/lib/systemd/user/fabos-agent.service /usr/lib/systemd/user/fabos-voiced.service /usr/lib/systemd/user/fabos-llama.service'"
chk "daemon: every Fab OS daemon carries MemoryHigh"          "R 'for u in /usr/lib/systemd/user/fabos-agent.service /usr/lib/systemd/user/fabos-voiced.service /usr/lib/systemd/user/fabos-llama.service; do grep -q ^MemoryHigh= \$u || exit 1; done'"
chk "local model: llama-server bound to 127.0.0.1 (wrapper)"  "R 'grep -q -- \"--host 127.0.0.1\" /usr/lib/fabos/ai/llama-start.sh'"
chk "policy example shipped; /etc/fabos/policy.json absent or root 0644" "R 'test -f /usr/share/fabos/policy.example.json && { ! test -e /etc/fabos/policy.json || [ \$(stat -c %U:%a /etc/fabos/policy.json) = root:644 ]; }'"

# ---- firewall, updates, discovery, lock screen
chk "ufw: ENABLED=yes, incoming DROP, outgoing ACCEPT"       "R 'grep -q ^ENABLED=yes /etc/ufw/ufw.conf && grep -q \"DEFAULT_INPUT_POLICY=\\\"DROP\\\"\" /etc/default/ufw && grep -q \"DEFAULT_OUTPUT_POLICY=\\\"ACCEPT\\\"\" /etc/default/ufw && systemctl is-enabled ufw >/dev/null'"
if [ "$PROFILE" = iso ]; then
  chk "ufw: iso profile has no allow rules"                   "[ \"\$(R 'grep -c \"^-A ufw-user-input\" /etc/ufw/user.rules')\" = 0 ]"
else
  chk "ufw: vm profile allows only 22/tcp (QEMU harness)"     "[ \"\$(R 'grep \"^-A ufw-user-input\" /etc/ufw/user.rules')\" = '-A ufw-user-input -p tcp --dport 22 -j ACCEPT' ]"
fi
chk "updates: unattended-upgrades installed + enabled"       "R 'dpkg -s unattended-upgrades >/dev/null 2>&1 && systemctl is-enabled unattended-upgrades >/dev/null'"
chk "updates: 20auto-upgrades turns the daily run on"        "R 'grep -q \"APT::Periodic::Unattended-Upgrade \\\"1\\\"\" /etc/apt/apt.conf.d/20auto-upgrades && grep -q \"APT::Periodic::Update-Package-Lists \\\"1\\\"\" /etc/apt/apt.conf.d/20auto-upgrades'"
chk "updates: origins = Ubuntu security + the Fab OS repository" "R 'grep -q -- \"-security\" /etc/apt/apt.conf.d/52fabos-unattended && grep -q \"Patience AI:loom\" /etc/apt/apt.conf.d/52fabos-unattended && grep -q -- \"-security\" /etc/apt/apt.conf.d/50unattended-upgrades'"
chk "mDNS: avahi-daemon service + socket disabled"            "R 'test \"\$(systemctl is-enabled avahi-daemon.service 2>/dev/null)\" = disabled && test \"\$(systemctl is-enabled avahi-daemon.socket 2>/dev/null)\" = disabled'"
chk "lock screen: 10 min idle + lock on resume (kscreenlockerrc [Daemon])" "R 'grep -q ^Autolock=true /etc/xdg/kscreenlockerrc && grep -q ^Timeout=10 /etc/xdg/kscreenlockerrc && grep -q ^LockOnResume=true /etc/xdg/kscreenlockerrc'"
chk "lock screen: lid close sleeps on AC/Battery/LowBattery (powerdevilrc)" "R '[ \"\$(grep -c ^LidAction=1 /etc/xdg/powerdevilrc 2>/dev/null)\" = 3 ]'"

# ---- privileged files vs the saved baselines (tests/security/*.txt); permissions of the Fab OS trees
BASE=$SRC/tests/security
chk "privileged files: no setuid file beyond the baseline"    "img_baseline 'find / -xdev -perm -4000 -type f 2>/dev/null' $BASE/suid-baseline.txt"
chk "privileged files: no setgid file beyond the baseline"    "img_baseline 'find / -xdev -perm -2000 -type f 2>/dev/null' $BASE/sgid-baseline.txt"
chk "privileged files: no file capability beyond the baseline" "img_baseline 'getcap -r / 2>/dev/null' $BASE/caps-baseline.txt"
chk "privileged files: no Fab OS file is setuid/setgid/capability" "img_empty 'find /usr/lib/fabos /usr/share/fabos /usr/bin/fabos* -xdev \( -perm -4000 -o -perm -2000 \) -type f; getcap -r /usr/lib/fabos /usr/share/fabos'"
chk "permissions: /etc/fabos root 0755, nothing group/world-writable inside" "R '[ \$(stat -c %U:%a /etc/fabos) = root:755 ] && [ -z \"\$(find /etc/fabos -perm /022 -not -type d)\" ]'"
chk "permissions: no world-writable file/dir under /usr/lib/fabos /usr/share/fabos /etc/fabos" "img_empty 'find /usr/lib/fabos /usr/share/fabos /etc/fabos -xdev \( -type f -o -type d \) -perm -0002'"
chk "permissions: feedback.env root-only (relay credentials)" "R '! test -e /etc/fabos/feedback.env || [ \$(stat -c %U:%a /etc/fabos/feedback.env) = root:600 ]'"

# ---- boot chain and disk encryption (the installed system's guarantees)
chk "kernel: Ubuntu signed kernel image present (Secure Boot capable)" "R 'ls /boot/vmlinuz-*-generic >/dev/null 2>&1 && dpkg -l | grep -qE \"^ii  linux-image-[0-9].*(generic|virtual)\"'"
chk "disk: cryptsetup + initramfs hooks installed (LUKS)"     "R 'dpkg -s cryptsetup >/dev/null 2>&1 && dpkg -s cryptsetup-initramfs >/dev/null 2>&1'"
if [ "$PROFILE" = iso ]; then
  chk "iso: shim-signed + grub-efi-amd64-signed (Secure Boot)" "R 'dpkg -s shim-signed >/dev/null 2>&1 && dpkg -s grub-efi-amd64-signed >/dev/null 2>&1'"
  chk "iso: installer preselects LUKS2 full-disk encryption"  "grep -q '^enableLuksAutomatedPartitioning: true' $SRC/image/overlay/iso/etc/calamares/modules/partition.conf && grep -q '^luksGeneration: luks2' $SRC/image/overlay/iso/etc/calamares/modules/partition.conf"
  chk "iso: live NOPASSWD rule + autologin removed by the installer" "grep -q '/etc/sudoers.d/fabos-live' $SRC/image/overlay/iso/etc/calamares/modules/shellprocess.conf && grep -q '20-autologin-live.conf' $SRC/image/overlay/iso/etc/calamares/modules/shellprocess.conf"
fi

# ---- source-tree checks (this checkout)
chk "source: agent tests cover policy clamps, audit chain, rootexec, sandbox, environment leak" "grep -q 'class SecurityUnits' $SRC/tests/agent-test.py && grep -q 'class PolicyDaemon' $SRC/tests/agent-test.py && grep -q 'test_audit_chain' $SRC/tests/agent-test.py && grep -q 'rootexec' $SRC/tests/agent-test.py && grep -q 'LEAKTEST' $SRC/tests/agent-test.py && grep -q 'sudo_path_allowed' $SRC/tests/agent-test.py"
chk "source: SECURITY.md, docs/ENTERPRISE.md, ADR-0017, PRIVACY audit paragraph present" "test -f $SRC/docs/ENTERPRISE.md && test -f $SRC/docs/decisions/ADR-0017-enterprise-security.md && grep -q 'audit' $SRC/legal/PRIVACY.md && grep -q 'pkexec' $SRC/SECURITY.md"
chk "source: docs state the 5-minute polkit cache and the rules.d way to remove it; sudo launcher gated by policy" "grep -q 'AUTH_ADMIN' $SRC/SECURITY.md && grep -q 'AUTH_ADMIN' $SRC/docs/ENTERPRISE.md && grep -q 'five minutes' $SRC/SECURITY.md && grep -q 'require_password_for_root' $SRC/SECURITY.md"
chk "source: no sudoers NOPASSWD rule in any Fab OS package (comments aside)" "! test -e $SRC/packages/fabos-agent/etc/sudoers.d/fabos-agent && ! grep -rEq '^[^#]*NOPASSWD' $SRC/packages/*/etc/sudoers.d/ 2>/dev/null"
chk "source: bubblewrap + polkit attributed (ATTRIBUTIONS.md, THIRD_PARTY_LICENSES)" "grep -qi bubblewrap $SRC/ATTRIBUTIONS.md && grep -qi polkit $SRC/ATTRIBUTIONS.md && test -f $SRC/THIRD_PARTY_LICENSES/LGPL-2.0.txt"
chk "source: release checksums are signed (scripts/release-checksums.sh; publish-iso.sh + release-github.sh call it)" "test -x $SRC/scripts/release-checksums.sh && grep -q -- '--detach-sign' $SRC/scripts/release-checksums.sh && grep -q release-checksums.sh $SRC/scripts/publish-iso.sh && grep -q release-checksums.sh $SRC/scripts/release-github.sh && grep -q 'SHA256SUMS.gpg' $SRC/docs/ENTERPRISE.md"

echo; echo "security-check ($TAG): $npass PASS / $nfail FAIL"
exit $fail
