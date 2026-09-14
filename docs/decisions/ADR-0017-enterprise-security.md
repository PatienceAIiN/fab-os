# ADR-0017: Enterprise-grade security for the agent and the OS

**Status:** accepted (2026-09-15) · amends ADR-0005 (agent architecture: the root path) · extends ADR-0006 (updates)
· numbered 0017 because ADR-0016 (Brave) already existed when this track started.

## Context

Fab OS 1.0-3 reached root through `sudo -n /usr/lib/fabos/agent/rootexec <id>` with `/etc/sudoers.d/fabos-agent`
granting `%sudo ALL=(root) NOPASSWD: /usr/lib/fabos/agent/rootexec`. `rootexec` ran whatever command a user-owned record
under `/run/user/<uid>/fabos-agent/authz/` named. Consequence, verified by reading both files: **any process running as a
member of the `sudo` group could write such a record and become root without a password** — a local privilege escalation
that no deterministic policy in the daemon could prevent, because the daemon was not in the path. Shell steps ran as the
user with the whole home directory (SSH keys, GnuPG, the agent's own provider keys) readable and writable; the activity log
was plain SQLite rows; there was no way for an organisation to bound what a user may allow the agent to do; AppArmor was
installed but no Fab OS profile existed; several Ubuntu hardening defaults were left at their permissive value.

Facts checked in the round-3 image (`localhost/fabos:vm`, 2026-09-15) before deciding: `pkexec` 127 and `polkitd` present
and `pkexec` setuid root; `bubblewrap` 0.11.1 installed with Ubuntu's `/etc/apparmor.d/bwrap-userns-restrict` (the profile
that grants bwrap user namespaces while `kernel.apparmor_restrict_unprivileged_userns = 1`); `apparmor_parser` 5.0.2;
`systemd` 259; no `xmllint`; kernel 7.0.0-31-generic config has `SECURITY_YAMA`, `BPF_JIT`, `SYN_COOKIES`, `SECCOMP`,
`USER_NS`; Ubuntu ships `kptr_restrict = 1`, `yama.ptrace_scope = 1`, `rp_filter = 2`; `unattended-upgrades` enabled with
`20auto-upgrades` and the Fab OS origins in `52fabos-unattended`; `avahi-daemon` enabled; kscreenlocker's `[Daemon]` keys
`Autolock`, `Timeout`, `LockGrace`, `LockOnResume` and powerdevil's `LidAction` under `[AC|Battery|LowBattery][SuspendAndShutdown]`
exist as UTF-16 literals in the shipped libraries; `polkit-kde-authentication-agent-1` is autostarted in the session.

## Decisions

1. **Root through polkit, never without the user's credentials.** `rootexec` is invoked as `pkexec /usr/lib/fabos/agent/rootexec
   <authz-id>` under the action `in.patienceai.fabos.rootexec` (`allow_any=no`, `allow_inactive=no`,
   `allow_active=auth_admin_keep`). polkit authenticates the active local user (an administrator's password for non-admin
   accounts) before rootexec runs, in **every** agent mode including `bypass`, and remembers it for five minutes. The sudoers
   file is removed from the package; `postinst` and `postrm` delete it on upgrade and removal. `rootexec` keeps and tightens
   its own checks: caller from `PKEXEC_UID`, record and its directory owned by that uid and private, no symlink, younger than
   ten minutes, record `id` equal to the argument, `command_sha256` equal to the sha256 of the command (a record whose command
   differs from what was approved is refused), single use, decisions logged to syslog (authpriv) and
   `/var/log/fabos/rootexec.log`. The daemon writes the record `O_EXCL 0600` in a `0700` directory and logs the request and
   result in its chained activity log. A `sudo -n` fallback exists only when the administrator's policy says
   `require_password_for_root: false` **and** pkexec is absent — for a rule the administrator installs themselves.
   Why polkit and not a password prompt of our own: polkit is the desktop's existing, audited authentication path with a
   session-aware agent, admin-group semantics and its own logging; a prompt drawn by the agent could be spoofed by a task.
2. **Shell steps run in bubblewrap.** `run_shell` (and `schedule_watch` commands) execute under `bwrap`: `/` read-only,
   `$HOME` writable except `~/.ssh`, `~/.gnupg`, `~/.config/fabos` and `~/.local/share/kwalletd` (empty tmpfs), the agent's
   history read-only, the agent's runtime directory hidden (a task cannot read the API token and approve itself), `/tmp`
   shared, fresh `/dev` (+GPU nodes) and `/proc`, own PID namespace, `--die-with-parent`, network kept unless policy
   `sandbox_network` is false. The step records `"sandbox": "bwrap"` or `"none"` (fallback when namespaces are unavailable,
   e.g. inside the build container). Cancelling kills the process group; the kernel tears the PID namespace down with it.
   Consequence accepted: a process a command leaves in the background ends with the step — long-running programs go through
   `open_app`, which runs in the session (the tool description says so). `read_file`/`write_file`/`list_dir` additionally
   **refuse** the secrets and runtime directories outright, approval or not.
3. **Administrator policy** `/etc/fabos/policy.json` (`Policy` class): `mode_max`, `providers_allowed`, `cloud_allowed`,
   `tools_denied`, `hosts_allowed`, `audit_export_dir`, `require_password_for_root`, `sandbox_network`. Loaded at start and
   on SIGHUP; clamps `mode` (stored, per task, and on PUT), providers (PUT, task start, connection check, speech), tools (model
   tool list + gate, recorded as `denied-by-policy`), hosts (`web_fetch`, SMTP/IMAP, provider base URLs). `/status` carries
   `"policy"` (UIs show "Managed by your organisation" from `policy.managed`), `"root_path"` and `"sandbox"`. Malformed file:
   ignored and reported (fail-open) — a typo must not lock a user out.
4. **Tamper-evident activity log.** `activity.hmac = HMAC-SHA256(audit_key, prev_hmac || canonical row)`; the key is a
   `systemd-creds` secret (`audit_key`) generated at first start, not settable through `/secrets`. `fabos audit verify`
   walks the chain (legacy unsigned rows before the chain are tolerated; a hole or an altered row fails); `fabos audit export
   --since` writes JSON Lines with the chain head in a header line to the policy's directory. Details stay capped as before.
5. **OS hardening files.** `/etc/sysctl.d/70-fabos-hardening.conf` (eleven keys, each verified against the image kernel's
   `/proc/sys` or config); AppArmor profiles attached to the executed script paths — **enforce** for `fabos-voiced` and
   `fabos-llama` (every path read from the programs' code and the binaries' data directories; helpers inherit because
   `NoNewPrivileges` forbids later profile changes), **complain** for `fabos-agentd` with the enforce path written in the
   profile; `fabos-voiced.service` gains a drop-in that runs the script directly so the profile attaches (no
   `change_onexec`, which Ubuntu's `apparmor_restrict_unprivileged_unconfined=1` may refuse to unprivileged processes);
   `avahi-daemon` disabled; `/etc/sudoers.d/fabos-hardening` (`passwd_timeout=1`, `use_pty`, `logfile`); lock after 10 idle
   minutes and on resume (`kscreenlockerrc [Daemon]`, `powerdevilrc LidAction=1`); `fabos-agent.service` with `LimitCORE=0`,
   `MemoryHigh=75%`, `TasksMax=512` and deliberately **without** `NoNewPrivileges` or any seccomp option — pkexec is setuid
   and would fail under no_new_privs — and without mount sandboxing, which user units cannot get under the userns
   restriction. Both facts are written in the unit.
6. **Evidence tooling.** `tests/security-check.sh` (PASS/FAIL per control against the image, including the privileged-file
   baselines in `tests/security/*.txt`), `scripts/sbom.py` (CycloneDX 1.5), unit tests for every clamp, the chain, rootexec's
   refusals, the pkexec argv and the sandbox in `tests/agent-test.py`.

## Consequences

- An approved administrator step now costs the user a password dialog (cached five minutes). That is the point.
- The `sudo` group no longer confers passwordless root through Fab OS; an administrator's own polkit rule
  (`/etc/polkit-1/rules.d/`) is the supported way to make the action `yes` on unattended machines.
- Background processes started by `run_shell` end with the step (see decision 2); `test_11b` in `tests/agent-test.py`
  documents both behaviours (sandboxed and fallback).
- The voice daemon and the local model server break loudly if their profiles miss a path (they are enforced). The
  profiles were written from the complete list of paths in the code and the binaries' data directories, and parse with the
  image's parser; running them under an AppArmor kernel was not possible in this round's container (no AppArmor interface)
  and is the first item to verify in the next VM boot — `journalctl -k | grep 'profile="fabos-'` must stay empty through
  `tests/voice-vm.sh` and a local-model task.
- Another track owns Fab AI Controls; it reads `status.policy.managed` / `status.policy.mode_max` to show the
  "Managed by your organisation" line and disable the clamped switches.
