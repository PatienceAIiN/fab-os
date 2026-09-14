# Security policy

## Reporting a vulnerability

E-mail **info@patienceai.in** with the subject `Fab OS security`. Do not open a public issue for a
security problem. Include the affected component (package name and version, or file path in this
repository), a reproduction, and the impact you see. We reply within seven days, keep you informed while we
work on a fix, and credit you in the release notes if you want. Please give us 90 days before publishing
details, or agree a different timeline with us if a fix is straightforward or the issue is already public.

There is **no bug bounty**; Fab OS is a pre-release project.

## Scope

In scope:

- The Fab OS packages in `packages/` (`fabos-branding`, `fabos-desktop`, `fabos-agent`, `fabos-ai`,
  `fabos-feedback`, `fabos-updates`, `fabos-firstboot`, `fabos-welcome`, `fabos-voice`, `fabos-desktop-meta`).
- The image recipe (`image/`), build and publish scripts (`scripts/`), and the CI workflow.
- The rebranding scripts that modify upstream files on the installed system
  (`/usr/lib/fabos/rebrand-*`) if they can be made to alter anything other than display strings.
- The Fab OS apt repository (signing, channel switching in Fab OS Updates).
- The website and community server in `website/` and `community/`.

Out of scope: vulnerabilities in unmodified Ubuntu, KDE, Brave or other upstream packages (report them
upstream; tell us as well if Fab OS's default configuration makes them worse), and the VM test profile,
which has a known password and autologin by design and is never distributed.

## Threat model

Three attackers, and what stands between each of them and the machine. Every control named here is a file in this tree
(path given) and is asserted by `tests/security-check.sh` on a built image; `docs/ENTERPRISE.md` explains deployment,
`docs/decisions/ADR-0017-enterprise-security.md` the reasoning.

**1. A local attacker running as the user** (malware in the session, a compromised application, a script the user ran).

| It tries to | What stops it |
|---|---|
| become root through the agent's root path | there is no passwordless path: `rootexec` is started by `pkexec` under the polkit action `in.patienceai.fabos.rootexec` (`allow_active=auth_admin_keep`, `allow_any`/`allow_inactive=no`), so polkit asks the active user for their own password (an administrator's for non-admin accounts) in every mode; `rootexec` then runs only a record the daemon wrote (`O_EXCL 0600` in a `0700` dir, owner = `PKEXEC_UID`, no symlink, < 10 min, `id` and `command_sha256` verified, single use) and logs every decision (`/var/log/fabos/rootexec.log`, journal). No `NOPASSWD` sudoers rule exists; `postinst`/`postrm` remove the 1.0-3 one. |
| drive the agent through its API to approve steps or change the mode | the API token is `0600` in `$XDG_RUNTIME_DIR/fabos-agent`, hidden from every `run_shell` sandbox; approvals and mode changes are recorded in the chained activity log; the administrator's `mode_max` caps what any approval can allow |
| debug or dump the daemon to read provider keys | `kernel.yama.ptrace_scope=1` (only descendants), `LimitCORE=0` on the unit, `fs.suid_dumpable=0`; keys live in `systemd-creds`, decrypted on use |
| escalate through setuid binaries | the image's setuid/setgid/capability lists must equal the saved baselines (`tests/security/*.txt`, Ubuntu's stock set + Brave's sandbox helper); no Fab OS file is setuid, setgid or carries a capability; `fs.protected_*` block `/tmp` symlink and FIFO tricks |
| use `sudo` habits against the administrator | `/etc/sudoers.d/fabos-hardening`: `passwd_timeout=1`, `use_pty` (no TIOCSTI injection), `logfile` |

**2. A remote attacker via the browser or the network.**

| It tries to | What stops it |
|---|---|
| reach a listening service | ufw denies incoming (no rules on installed systems), no SSH server, mDNS off (`avahi-daemon` disabled), every Fab OS daemon binds 127.0.0.1 or a unix socket and runs as the user with `MemoryHigh` |
| reach the agent or the local model from a web page | `fabos-agentd` (127.0.0.1:8790) requires the bearer token on every request except `/health`; `llama-server` is bound to 127.0.0.1:8081 by its wrapper and confined by the enforced AppArmor profile `fabos-llama` (model directories read-only, no home, no /tmp) |
| persist or pivot after a browser compromise | Brave runs under its upstream AppArmor profiles with Chromium's sandbox; `kernel.kptr_restrict=2`, `dmesg_restrict=1`, `net.core.bpf_jit_harden=2`, strict `rp_filter`, SYN cookies (`/etc/sysctl.d/70-fabos-hardening.conf`); unattended security updates for Ubuntu, Fab OS and Brave; the screen locks after 10 idle minutes and on wake from sleep |

**3. Malicious model output** (prompt injection through a web page, a mail, a file the agent read).

| It tries to | What stops it |
|---|---|
| run a destructive or privileged command | the deterministic classifier (`classify`, `catastrophic`) rates every tool call before it runs; CRITICAL needs approval in `ask` and `auto`; `as_root` additionally costs the user's password through polkit — the model cannot supply it |
| read the user's keys or the agent's secrets | `run_shell` runs inside bubblewrap with `~/.ssh`, `~/.gnupg`, `~/.config/fabos` and any wallet replaced by empty tmpfs, the agent's runtime dir hidden and its history read-only; `read_file`/`write_file`/`list_dir` refuse the secrets and runtime directories even when approved; the classifier already rates those paths CRITICAL |
| approve its own steps or reload policy | the token is unreachable from the sandbox; policy is a root-owned file |
| use a forbidden tool, provider or host | the administrator's `/etc/fabos/policy.json` removes denied tools from the model's tool list and refuses them at the gate, refuses disallowed providers, and refuses `web_fetch`, mail and provider endpoints outside `hosts_allowed` with a clear message; the system prompt tells the model the limits |
| cover its tracks | the activity log is an HMAC chain (`fabos audit verify`); details are capped and exported as JSON Lines with the chain head (`fabos audit export`) |

What the model output can still do: everything a MEDIUM/HIGH step may do in the user's chosen mode within the home
directory and on the network the policy allows — which is the product. The mode is the user's dial; `mode_max` is the
organisation's.

## The agent's root path

The agent (`fabos-agentd`) runs with the logged-in user's privileges. Root is reachable only through
`pkexec /usr/lib/fabos/agent/rootexec <authz-id>` — the polkit action `in.patienceai.fabos.rootexec`
(`packages/fabos-agent/usr/share/polkit-1/actions/in.patienceai.fabos.rootexec.policy`, defaults `no` / `no` /
`auth_admin_keep`). The system's authentication dialog asks for the user's own password before `rootexec` runs, in every
mode including `bypass`, and keeps the authorization for five minutes. `rootexec` executes a command only if the calling
user's own agent daemon wrote a single-use authorization record for it under `/run/user/<uid>/fabos-agent/authz/` after the
deterministic policy classified the step as CRITICAL and the user's mode allowed it. Records and their directory must be
owned by the caller (`PKEXEC_UID`) and private, must not be symlinks, must carry the record id and a sha256 that matches
their command, expire after ten minutes and are deleted before anything runs. Every decision is logged
(`/var/log/fabos/rootexec.log`, journal `fabos-rootexec`) and mirrored in the daemon's chained activity log.

Fab OS 1.0-3 and earlier used `sudo -n` with a `NOPASSWD` rule for members of the `sudo` group; that rule let any process
of such a user become root without a password and is gone (ADR-0017). An organisation that needs unattended root writes
its own polkit rule for the action; `require_password_for_root: false` in the policy additionally lets the daemon use an
administrator-installed `sudo -n` rule when pkexec is absent. Fab OS ships neither.

We treat the following as vulnerabilities and want to hear about them privately:

- any way for a process that is not the user's agent daemon to create or reuse an authorization record, or to run
  `rootexec` without the polkit authentication;
- any way for model or tool output (prompt injection) to run a CRITICAL step without the approval the
  user's mode requires, to change the mode, the System-Wide AI switch or the policy without a user action, or to read the
  API token, the secrets directory or `~/.ssh`/`~/.gnupg` from inside `run_shell`;
- policy classification bypasses that let a command reach `run_shell` with `as_root` while being
  classified below CRITICAL, or a `hosts_allowed`/`tools_denied`/`mode_max` clamp that can be sidestepped;
- a way to alter or truncate the activity log that `fabos audit verify` plus an exported chain head cannot detect;
- an escape from the `run_shell` sandbox, or a path the enforced AppArmor profiles (`fabos-voiced`, `fabos-llama`) allow
  that they should not;
- leakage of provider API keys or mail credentials from the daemon, Fab AI Controls or logs;
- privilege escalation through the feedback relay (`fabos-feedback-relay`, root, socket-activated) or the
  updates helper (`pkexec` + polkit action `in.patienceai.fabos.updates`).

The design is described in `docs/decisions/ADR-0005-agent-architecture.md` and `ADR-0017-enterprise-security.md`; the
code is in `packages/fabos-agent/usr/lib/fabos/agent/`.

## Posture

Unmodified Ubuntu kernel and shim (Secure Boot works on the ISO), AppArmor on with Fab OS profiles (`fabos-voiced` and
`fabos-llama` enforced, `fabos-agentd` in complain mode with its path to enforce documented in the profile), kernel
hardening sysctls (`/etc/sysctl.d/70-fabos-hardening.conf`), firewall on by default (ufw: incoming denied, outgoing
allowed, no rules; no SSH server in the shipped image), mDNS off, LUKS2 full-disk encryption preselected by the
installer, no snap, no telemetry (`legal/PRIVACY.md`). Cloud AI providers are off until the user adds a key; model output
is treated as untrusted data. Fab OS updates are apt packages signed with the Fab OS Archive key and installed by
`unattended-upgrades` together with Ubuntu's security updates; Ubuntu updates come from Ubuntu unchanged. An
administrator policy file (`/etc/fabos/policy.json`) bounds every user setting (`docs/ENTERPRISE.md`); a CycloneDX SBOM of
any built image is produced by `scripts/sbom.py`. Known gap: the Fab OS repository is served over HTTP until a certificate
is issued for the host (integrity is protected by signatures; package names are not private).

Secrets and the browser (2026-09-15, ADR-0015 / ADR-0016): KWallet is disabled system-wide (`/etc/xdg/kwalletrc`
`Enabled=false`), KWallet Manager is not installed and pinned out, and `pam_kwallet5` is removed from the SDDM PAM
stack, so no wallet daemon runs and no wallet prompt appears. Wi-Fi/VPN secrets are therefore held by NetworkManager
in root-only files under `/etc/NetworkManager/system-connections/`; the agent's keys stay in `systemd-creds`. Brave
Browser is Brave's unmodified official build from Brave's own signed apt repository (keyring fingerprint-checked at
build time, key scoped to that source only, never in `/etc/apt/trusted.gpg.d/`). Brave's packaging is Chromium's
installer template with Google's repository constants still inside it, but both scripts that contain that code stop
before reaching it: the postinst `exit 0`s immediately before `install_key` (brave/brave-browser#54299) and the daily
cron script `exit 0`s at its line 23, before it even defines `DEFAULTS_FILE` (brave/brave-browser#1084) — checked in
`brave-browser 1.95.101`. No Google source or key is ever added; the `/etc/default/brave-browser` Fab OS ships
(`repo_add_once="false"`) is belt-and-braces for a future package that re-enables the template, the image has no cron
daemon, and the build fails if any source other than Ubuntu's and Brave's appears.

Setuid, setgid and file capabilities: the image carries Ubuntu's stock set plus **exactly one non-stock setuid-root
file, `/opt/brave.com/brave/chrome-sandbox`** — Chromium's setuid sandbox helper (15 224 bytes, mode 4755, shipped
unmodified in Brave's package). Brave's sandbox normally uses unprivileged user namespaces under an AppArmor profile
that grants `userns` (Ubuntu's `/etc/apparmor.d/brave`; Brave's postinst also installs its own `brave-browser-stable`
profile for the same binary — both `flags=(unconfined)`, both parse with `apparmor_parser` 5.0.2); the setuid helper is
Chromium's fallback when user namespaces are unavailable. No Fab OS binary is setuid or setgid or carries a capability;
every daemon Fab OS adds binds 127.0.0.1 or a unix socket, runs as the user and carries a `MemoryHigh` limit.
`tests/branding-check.sh` and `tests/security-check.sh` compare the full `find / -xdev -perm -4000`, `-perm -2000` and
`getcap -r /` lists of the built image against explicit allowlists / the saved baselines in `tests/security/` (any new
entry fails), and assert that nothing under `/usr/lib/fabos`, `/usr/share/fabos` or `/etc/fabos` is world-writable.
