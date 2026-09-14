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
build time, key scoped to that source only, never in `/etc/apt/trusted.gpg.d/`); its sandbox runs under Ubuntu's
AppArmor `brave` profile. Brave's packaging still contains Chromium's "re-add the vendor repository" cron script with
Google's constants in it; Fab OS ships `/etc/default/brave-browser` with `repo_add_once="false"` and installs no
`cron`, so it never runs, and the build fails if any source other than Ubuntu's and Brave's appears. Every daemon Fab
OS adds binds 127.0.0.1 or a unix socket, runs as the user and carries a `MemoryHigh` limit; the image's SUID/SGID set
and file capabilities are Ubuntu's stock set (no Fab OS binary is setuid), and `tests/branding-check.sh` asserts that
nothing under `/usr/lib/fabos` or `/usr/share/fabos` is world-writable.
