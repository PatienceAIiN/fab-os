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
  `fabos-feedback`, `fabos-updates`, `fabos-firstboot`, `fabos-welcome`, `fabos-desktop-meta`).
- The image recipe (`image/`), build and publish scripts (`scripts/`), and the CI workflow.
- The rebranding scripts that modify upstream files on the installed system
  (`/usr/lib/fabos/rebrand-*`) if they can be made to alter anything other than display strings.
- The Fab OS apt repository (signing, channel switching in Fab OS Updates).
- The website and community server in `website/` and `community/`.

Out of scope: vulnerabilities in unmodified Ubuntu, KDE, Brave or other upstream packages (report them
upstream; tell us as well if Fab OS's default configuration makes them worse), and the VM test profile,
which has a known password and autologin by design and is never distributed.

## The agent's root path

The agent (`fabos-agentd`) runs with the logged-in user's privileges. Root is reachable only through
`/usr/lib/fabos/agent/rootexec`, which `sudoers.d/fabos-agent` lets members of the `sudo` group run without
a password. `rootexec` executes a command only if the calling user's own agent daemon wrote a single-use
authorization record for it under the user's runtime directory (`/run/user/<uid>/fabos-agent/authz/`)
after the deterministic policy classified the step as CRITICAL and the user's mode allowed it (approval
in `ask` and `auto` modes; `bypass` mode is an explicit user opt-in). Records must be owned by the caller,
must not be symlinks, expire after ten minutes and are deleted on use.

We treat the following as vulnerabilities and want to hear about them privately:

- any way for a process that is not the user's agent daemon to create or reuse an authorization record;
- any way for model or tool output (prompt injection) to run a CRITICAL step without the approval the
  user's mode requires, or to change the mode or the System-Wide AI switch without a user action;
- policy classification bypasses that let a command reach `run_shell` with `as_root` while being
  classified below CRITICAL;
- leakage of provider API keys or mail credentials from the daemon, Fab AI Controls or logs;
- privilege escalation through the feedback relay (`fabos-feedback-relay`, root, socket-activated) or the
  updates helper (`pkexec` + polkit action `in.patienceai.fabos.updates`).

The design is described in `docs/decisions/ADR-0005-agent-architecture.md`; the code is in
`packages/fabos-agent/usr/lib/fabos/agent/`.

## Posture

Unmodified Ubuntu kernel and shim (Secure Boot works on the ISO), AppArmor on, firewall on by default (ufw:
incoming denied, outgoing allowed, no rules; no SSH server in the shipped image), LUKS full-disk encryption
offered by the installer, no snap, no telemetry (`legal/PRIVACY.md`). Cloud AI providers are off until the
user adds a key; model output is treated as untrusted data. Fab OS updates are apt packages signed with the
Fab OS Archive key; Ubuntu updates come from Ubuntu unchanged. Known gap: the Fab OS repository is served
over HTTP until a certificate is issued for the host (integrity is protected by signatures; package names
are not private).

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
`tests/branding-check.sh` compares the full `find / -xdev -perm -4000`, `-perm -2000` and `getcap -r /` lists of the
built image against explicit allowlists (any new entry fails; the lists are the 2026-09-14 `vm`/`iso` images' stock set
plus the Brave helper), and asserts that nothing under `/usr/lib/fabos` or `/usr/share/fabos` is world-writable.
