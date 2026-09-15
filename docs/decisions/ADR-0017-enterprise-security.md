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
5. **OS hardening files.** `/etc/sysctl.d/70-fabos-hardening.conf` (twelve keys, each verified against the image kernel's
   `/proc/sys` or config); AppArmor profiles attached to the executed script paths — **enforce** for `fabos-llama`,
   **complain** for `fabos-agentd` and (since amendment 9 below; the first cut shipped it enforced) `fabos-voiced`, each with
   the enforce path written in the profile (every path read from the programs' code and the binaries' data directories;
   helpers inherit because `NoNewPrivileges` forbids later profile changes); `fabos-voiced.service` gains a drop-in that runs the script directly so the profile attaches (no
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

## Amendment (2026-09-15, after review)

Four findings against the first cut, each verified with a command before the change:

7. **Children never inherit the daemon's environment.** `Agent.session_env()` started from `dict(os.environ)`, and the unit
   loads `EnvironmentFile` `agent.env` where `ANTHROPIC_API_KEY` may live — so `env` inside the bubblewrap sandbox printed the
   provider key the tmpfs over `~/.config/fabos` was meant to hide (reproduced on the host with a planted key; the session
   manager's `systemctl --user show-environment` on the developer's machine held a real key too). Now `clean_env()` builds
   every child environment from an allowlist (`ENV_ALLOW`, `ENV_ALLOW_PREFIX`: the desktop-session variables) with a deny
   pattern on top (`ENV_DENY`: `*_API_KEY`, `*TOKEN*`, `*SECRET*`, `*PASSW*`, `*CREDENTIAL*`, `FABOS_*`, provider prefixes,
   systemd bookkeeping), applied to the session manager's environment and the daemon's alike; `Agent.tool_env()` further
   removes the ssh/gpg agent variables for `run_shell` and `schedule_watch` commands, sandboxed or not. Applications launched
   for the user (`open_app`) keep `SSH_AUTH_SOCK`, as a launcher would. `tests/agent-test.py test_25` starts the daemon with
   a planted key, a `*_TOKEN` variable and a live ssh-agent socket and proves none reaches the step; `SecurityUnits` covers
   the filter as a pure function. The login shell (`bash -lc`) still sources the user's own `~/.profile` from the writable
   home: that is the user's file, documented as such.
8. **Key-agent sockets in `$XDG_RUNTIME_DIR` are unreachable from the sandbox.** The runtime dir must stay bound (Wayland,
   D-Bus, PipeWire), but `gnupg/`, `gcr/`, `keyring/` become empty tmpfs (`sandbox_hidden`) and the ssh-agent socket files
   (`openssh_agent` — Ubuntu's `ssh-agent.socket` — and whatever `SSH_AUTH_SOCK` names) get `/dev/null` bound over them
   (`sandbox_masked`; bubblewrap accepts a file bind over a socket, verified on the host: `test -S` fails, `connect()` gets
   ECONNREFUSED). What the runtime dir still exposes is written in the docs.
9. **`fabos-voiced` goes to complain mode until it has run under an AppArmor kernel.** The "complete" enforce profile missed
   `wpctl` (volume/mute), `ffplay`/`mpv` (playback fallbacks) and, decisively, the `systemd-run --scope -- true` probe: on
   Ubuntu 26.04 `/usr/bin/true` is a symlink to `gnutrue` and the coreutils are Rust symlinks into
   `/usr/lib/cargo/bin/coreutils/`, and AppArmor matches the resolved path — under enforce the probe would fail and whisper
   would run inside the 200M service cgroup. The profile now lists every helper (re-derived with `grep -nE '_run\(\[|Popen\(|
   subprocess.run'` over `voicelib.py`/`fabos_voiced.py`, resolved with `readlink -f` in the image), the agentd profile names
   `gnutrue` and `sudo.ws` too, all three still compile with the image's `apparmor_parser` 5.0.2, and the enforce gate is the
   same as for `fabos-agentd`: a VM run with an empty `journalctl -k | grep 'profile="fabos-voiced"'`. `fabos-llama` stays
   enforced (a shell wrapper and one server; its coreutils rule already used the resolved paths).
10. **Honest release verification.** `docs/ENTERPRISE.md` §7 described `SHA256SUMS` + `SHA256SUMS.gpg` that nothing produced.
    `scripts/release-checksums.sh` now writes both (detached signature with the Fab OS Archive key, the apt key, same
    `APT_GNUPGHOME`; exports `fabos-archive-key.asc`; refuses to run without the key unless `FABOS_UNSIGNED=1`), and
    `publish-iso.sh` / `release-github.sh` call it and upload the files. Exercised end to end with a throwaway key (sign,
    import, `sha256sum -c`, `gpg --verify`, a tampered ISO and a tampered SHA256SUMS both rejected). The 1.0 ISO already on
    the download page is unsigned and the docs say so.
11. **Documentation of the residual root window and a tighter `rootexec`.** `auth_admin_keep` caches for five minutes; within
    that window a process of the user holding the API token can drive a `bypass` `as_root` step without a new prompt. Written
    in `SECURITY.md`/`docs/ENTERPRISE.md` with the `polkit.Result.AUTH_ADMIN` rule that removes it. `rootexec` now honours the
    `sudo` launcher (`SUDO_UID`) only when `/etc/fabos/policy.json` sets `require_password_for_root: false` — an explicit
    administrator opt-in — and refuses it otherwise, so a sudoers rule alone can no longer re-create the 1.0-3 escalation.
12. **`tests/security-check.sh` cannot pass vacuously**: it exits 2 when podman or the image is missing, and every negated or
    empty-output check compares a verdict token printed inside the image (no output = FAIL); baseline checks require a
    successful, non-empty listing before filtering.
