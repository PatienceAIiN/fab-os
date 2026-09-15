# Fab OS in an organisation

How to deploy Fab OS on managed computers, what the administrator controls, what the audit trail contains, and what
Fab OS does **not** do. Every control named here exists in the tree (path given) and is asserted by
`tests/security-check.sh` against a built image; the design record is `docs/decisions/ADR-0017-enterprise-security.md`,
the threat model is `SECURITY.md`.

## 1. What the agent may do is decided in three places

| Layer | Who sets it | Where | Effect |
|---|---|---|---|
| Deterministic risk policy | Fab OS | `packages/fabos-agent/usr/lib/fabos/agent/fabos_agentd.py` (`classify`, `catastrophic`) | every tool call gets LOW / MEDIUM / HIGH / CRITICAL before it runs |
| Permission mode | the user | Fab AI Controls, `fabos mode` | `ask` approves MEDIUM+, `auto` approves CRITICAL only, `bypass` asks nothing |
| **Administrator policy** | you | `/etc/fabos/policy.json` (root:root 0644) | clamps the user's choices; absent file = no restriction |

Root is outside all three: an approved CRITICAL `as_root` step runs `pkexec /usr/lib/fabos/agent/rootexec <id>` and polkit
asks for the **user's own password** in the system dialog (`auth_admin_keep`, remembered five minutes) — in every mode,
`bypass` included. A user who is not an administrator is asked for an administrator's password (Ubuntu's
`49-ubuntu-admin.rules`: the `sudo` group). There is no `NOPASSWD` rule anywhere (`tests/security-check.sh` asserts it).

The five-minute cache is a trade-off you can remove. While it is warm, a further `as_root` step of the same user — including
one driven through the daemon's API by another process of that user, in `bypass` mode — runs without a new prompt. To ask
for the password on **every** root step, install one polkit rule (root-owned, `/etc/polkit-1/rules.d/40-fabos-rootexec.rules`):

```js
polkit.addRule(function(action, subject) {
    if (action.id == "in.patienceai.fabos.rootexec") { return polkit.Result.AUTH_ADMIN; }
});
```

`pkaction --verbose --action-id in.patienceai.fabos.rootexec` shows the shipped defaults; `tests/security-check.sh` fails if
any rule *weakens* the action, never if one tightens it. The reverse — unattended root on a kiosk — is a rule returning
`polkit.Result.YES` **plus** `"require_password_for_root": false` in the policy file: `rootexec` accepts the `sudo` launcher
(an administrator's own sudoers rule) only when the policy says so, and refuses it otherwise, so a stray `NOPASSWD` line
cannot quietly bring the 1.0-3 escalation back.

## 2. Deploying the policy file

Write `/etc/fabos/policy.json` with your configuration management (Ansible, Puppet, cloud-init, a `.deb` of your own — it is
one root-owned file). The shipped example is `/usr/share/fabos/policy.example.json`. Every key is optional:

```json
{
  "mode_max": "auto",
  "providers_allowed": ["local", "claude"],
  "cloud_allowed": true,
  "tools_denied": ["send_email"],
  "hosts_allowed": ["api.anthropic.com", "127.0.0.1", "*.example.com"],
  "audit_export_dir": "/var/lib/fabos/audit",
  "require_password_for_root": true,
  "sandbox_network": true
}
```

| Key | Meaning | What the user sees |
|---|---|---|
| `mode_max` | most permissive mode a user may pick (`ask` < `auto` < `bypass`) | `fabos mode bypass` / the Controls switch answer HTTP 403 "Managed by your organisation: the permission mode is limited to auto"; a `bypass` stored earlier is applied as `auto` |
| `providers_allowed` | provider ids the user may select (`claude`, `openai`, `gemini`, `deepseek`, `local`) | selecting another one is refused (403); a task with a disallowed provider fails with the reason |
| `cloud_allowed` | `false`: only the built-in local model; cloud speech is off too | as above; the voice falls back to the offline engine |
| `tools_denied` | tools the agent may never call | the tool is removed from the model's tool list and refused at the gate (`denied-by-policy` in the history) |
| `hosts_allowed` | hosts `web_fetch`, mail (SMTP/IMAP) and provider endpoints may reach; `"example.com"` also matches subdomains, `"*.example.com"` only subdomains | "web_fetch may not reach host. Allowed hosts: …" |
| `audit_export_dir` | where `fabos audit export` writes (must exist and be writable by the user, see §4) | — |
| `require_password_for_root` | `true` (default): root only through pkexec. `false`: when pkexec is absent the daemon may use an administrator-installed `sudo -n` rule for rootexec (kiosks); Fab OS ships no such rule | — |
| `sandbox_network` | `false`: shell steps run with `--unshare-net` (no network inside the sandbox) | commands that need the network fail inside `run_shell`; the agent's own provider/mail/web tools are unaffected |

The daemon reads the file at start and on `SIGHUP` (`systemctl --user kill -s HUP fabos-agent`, per logged-in user) or
`fabos policy --reload`; a relogin reloads it as well. `fabos policy` and `GET /status` (`"policy"`) show what is applied,
including a parse error if the file is malformed — a malformed file is **ignored** (fail-open) so that a typo does not lock
users out of their own computers; the error is visible until fixed. Fab AI Controls shows a "Managed by your organisation"
line from `status.policy.managed`.

## 3. Disabling cloud AI

`"cloud_allowed": false` keeps every prompt, file and tool result on the machine: only the built-in model
(Qwen2.5-1.5B, `fabos-llama.socket`, 127.0.0.1:8080, needs 4 GB RAM) is selectable, cloud speech is off, and the voice uses
PocketSphinx + whisper.cpp + eSpeak NG offline. For a private inference server instead, allow `local`, set the endpoint
(`fabos settings local.base_url https://llm.example.com/v1`) and list its host in `hosts_allowed`.

## 4. Audit trail

Three records exist, all append-only from the user's point of view:

1. **The agent's activity log** (`~/.local/share/fabos/agent.db`, table `activity`): every task, step decision, approval,
   setting change, secret change (name only), root request/result, policy reload. Each row is sealed with
   `hmac = HMAC-SHA256(key, previous row's hmac || row)`; the key is a `systemd-creds` secret (`audit_key`, 0600 fallback)
   created at first start and never exposed through the API. Details are capped (commands 300 chars, requests 500) and
   never contain secrets or full tool outputs.
   - `fabos audit verify` recomputes the chain (exit 1 and the first altered or missing row when it breaks).
   - `fabos audit export --since 24h` writes JSON Lines to `audit_export_dir`; the first line carries the chain head so a
     collector that keeps the previous head can detect truncation of the newest rows, which a single verification cannot.
   - The export directory must exist and be writable by the user's uid: create it with `install -d -m 2770 -o root -g users
     /var/lib/fabos/audit` (or a per-user subdirectory) and collect the files with your log shipper. A `systemd --user` timer
     that runs `fabos audit export --since 1d` daily is the intended deployment.
   - What the chain protects against: edits by tasks and tools (the database is read-only inside the shell sandbox), casual
     edits with a database browser, and edits by anyone without the key. It does not protect against the user themselves
     re-signing with their own key — collect exports off the machine for that.
2. **rootexec's log**: `/var/log/fabos/rootexec.log` (root:adm 0640) and the journal (`fabos-rootexec`, authpriv) — one
   line per request: uid, record id, decision (RUN / REFUSED with reason / DONE exit code), sha256 of the command.
3. **sudo and polkit**: `/var/log/sudo.log` (`Defaults logfile`, `/etc/sudoers.d/fabos-hardening`) and polkitd's journal
   entries for `in.patienceai.fabos.rootexec` authentications.

## 5. What runs where — the isolation you can rely on

- `run_shell` executes inside **bubblewrap**: the system read-only, the home directory writable except `~/.ssh`, `~/.gnupg`,
  `~/.config/fabos` (the agent's own configuration and secrets) and a wallet if present (empty tmpfs over each), the agent's
  history read-only, the agent's runtime directory (API token, root authorization records) hidden — so a task cannot approve
  its own steps — a fresh `/dev` and `/proc`, its own PID namespace (everything a command started ends with it), network kept
  unless `sandbox_network` is `false`. When bubblewrap cannot create namespaces the step runs unsandboxed and the history says
  `"sandbox": "none"`; `GET /status` reports the mode in use.
- **The environment a command sees is not the daemon's.** The unit loads `/etc/fabos/agent.env` and `~/.config/fabos/agent.env`
  (a provider key may live there), so a child that inherited the daemon's environment would read the key the tmpfs hides.
  Instead every child gets an allowlist of desktop-session variables (`PATH`, `HOME`, `XDG_*`, `WAYLAND_DISPLAY`, `DISPLAY`,
  `DBUS_SESSION_BUS_ADDRESS`, locale, `QT_*`/`KDE_*`/`GTK_*`…) with credential-like names (`*_API_KEY`, `*TOKEN*`, `*SECRET*`,
  `*PASSW*`, `*CREDENTIAL*`, `FABOS_*`, `CREDENTIALS_DIRECTORY`…) dropped whatever their source — the session manager's
  environment included. Shell and watch commands additionally lose the ssh/gpg agent variables, and inside the sandbox the
  agents' sockets are unreachable: `$XDG_RUNTIME_DIR/gnupg`, `/gcr`, `/keyring` are empty tmpfs and the ssh-agent socket
  files (`$XDG_RUNTIME_DIR/openssh_agent`, whatever `SSH_AUTH_SOCK` names) have `/dev/null` bound over them. What the runtime
  dir still exposes is the desktop itself (Wayland socket, session D-Bus, PipeWire), as for any application the user starts.
  Applications launched *for* the user (`open_app`) keep their ssh agent, as a launcher would — they are the user's programs.
  The shell is a login shell: `~/.profile` inside the sandbox is the user's own file in the writable home.
- `read_file`, `write_file`, `list_dir` refuse the agent's secrets directory and runtime directory even when approved.
- **AppArmor**: `fabos-llama` (the local model server) runs in **enforce** mode — it can read only `/usr/share/fabos/models`
  and `/var/lib/fabos/models` and never a home directory. `fabos-voiced` (the "Hey Fab" listener) and `fabos-agentd` ship in
  **complain** mode: their rule sets list everything the code executes and reads (the voice profile was re-derived from every
  subprocess call, including `wpctl`, the `true` -> `gnutrue` symlink the `systemd-run` probe execs, the `ffplay`/`mpv`
  playback fallbacks and Ubuntu 26.04's Rust coreutils under `/usr/lib/cargo/bin/coreutils/`), but they have not yet run
  under an AppArmor kernel, and an enforce-mode gap would silently break the wake word or every task. The path to enforce is
  written in each profile (`/etc/apparmor.d/fabos-voiced`, `/etc/apparmor.d/fabos-agentd`): boot, run `tests/voice-vm.sh` and
  the agent ladder, and `journalctl -k | grep 'profile="fabos-'` must stay empty before `complain` is removed.
- Kernel: `/etc/sysctl.d/70-fabos-hardening.conf` (ptrace only of descendants, hidden kernel pointers, restricted dmesg,
  protected symlinks/hardlinks/FIFOs/regular files in sticky directories, no setuid core dumps, strict reverse-path
  filtering, SYN cookies, hardened BPF JIT). Ubuntu's `apparmor_restrict_unprivileged_userns=1` stays on; bubblewrap works
  through Ubuntu's own `bwrap-userns-restrict` profile.
- Services: firewall on (ufw, incoming denied, no rules on installed systems), mDNS off (`avahi-daemon` disabled; re-enable
  with `systemctl enable --now avahi-daemon.socket avahi-daemon.service` if printers or network shares need discovery), no
  SSH server, the agent's user unit with `LimitCORE=0`, `TasksMax=512`, `MemoryHigh`.
- Session: the screen locks after 10 idle minutes and on wake from sleep (which the lid close triggers) —
  `/etc/xdg/kscreenlockerrc` `[Daemon]`, `/etc/xdg/powerdevilrc`.

## 6. Updates

Unattended upgrades are on (`/etc/apt/apt.conf.d/20auto-upgrades`) for Ubuntu security updates and the Fab OS repository
(`/etc/apt/apt.conf.d/52fabos-unattended`, origins `Patience AI:loom` and `-beta`); no automatic reboot. Channels (stable /
beta) are switched in Fab OS Updates through its own polkit action (`in.patienceai.fabos.updates`, ADR-0006). Firefox updates
come from Mozilla's own signed repository (`packages.mozilla.org`, ADR-0018). Ubuntu packages are unmodified: their CVE handling is Ubuntu's.

## 7. Verifying an ISO and an installation

- Checksum and signature: `scripts/release-checksums.sh` writes `SHA256SUMS` and a detached OpenPGP signature `SHA256SUMS.gpg`
  with the Fab OS Archive key (the key that signs the apt repository; same `APT_GNUPGHOME`), and `scripts/publish-iso.sh` /
  `scripts/release-github.sh` call it and publish both files next to the ISO (the download directory and the GitHub release
  assets) together with the public key `fabos-archive-key.asc`. Verify with `sha256sum -c SHA256SUMS` and
  `gpg --verify SHA256SUMS.gpg SHA256SUMS` after importing the key — the same key is served at
  `https://fabos.patienceai.in/apt/fabos-archive-key.asc` (`scripts/publish-apt.sh`) and installed as
  `/usr/share/keyrings/fabos-archive-keyring.gpg` on a Fab OS system. **Said plainly:** the 1.0 pre-release ISO on the download
  page today was published before this script existed and carries only `<iso>.sha256` (an unsigned checksum); the first
  release published with the current scripts is the first with a signature (§8). The QA record (`docs/QA.md`) lists the
  checksum of the image it describes.
- The installer preselects LUKS2 full-disk encryption (`enableLuksAutomatedPartitioning: true`); Secure Boot works with
  Ubuntu's signed shim, GRUB and kernel, which Fab OS does not modify.
- On an installed system: `tests/security-check.sh` cannot run (it inspects a container image), but the same facts are
  visible directly: `aa-status`, `sysctl -a | grep -f <(grep -Eo '^[a-z.0-9_]+' /etc/sysctl.d/70-fabos-hardening.conf)`,
  `ufw status`, `systemctl is-enabled avahi-daemon unattended-upgrades`, `ls -l /etc/sudoers.d`, `pkaction --verbose
  --action-id in.patienceai.fabos.rootexec`, `fabos status`, `fabos policy`, `fabos audit verify`.
- Software bill of materials: `scripts/sbom.py --image localhost/fabos:iso` writes a CycloneDX 1.5 JSON with every dpkg
  package (name, version, licence from its copyright file), the model weights (sha256) and Firefox (Mozilla as supplier).

## 8. What is NOT covered

Said plainly so nobody plans around it:

- **No MDM agent, no remote management.** Policy is a file you place; there is no server that pushes it, inventories
  devices, wipes them or shows a fleet dashboard. Use your existing configuration management.
- **No remote attestation, no measured boot reporting.** Secure Boot verifies the boot chain locally; nothing reports
  PCR values anywhere. TPM-backed LUKS unlock is not configured by the installer.
- **No central identity.** Local accounts only; no SSSD/LDAP/Kerberos/Entra join is preconfigured (Ubuntu's packages can
  be added).
- **The user owns their home directory.** Policy bounds the agent, not the person: a user can still copy their own files
  anywhere. The sandbox and hard denials protect the agent's secrets and keys from *tasks*, not from the account owner.
- **The audit chain is per user and keyed per user** (see §4). Export it if you need evidence the user cannot re-sign.
- **Cloud providers see what is sent to them.** With `cloud_allowed` true, requests and tool results go to the selected
  provider under its terms; Fab OS adds no proxy, redaction or DLP.
- **`fabos-agentd` and `fabos-voiced` are not yet AppArmor-enforced** (complain mode, path documented in each profile;
  only `fabos-llama` is enforced); Firefox runs under Ubuntu's `firefox` profile (unconfined, user namespaces for its own sandbox); the rest of the desktop is Ubuntu's stock
  confinement.
- **The ISO currently on the download page is not signed.** It predates `scripts/release-checksums.sh` and has only an
  unsigned `<iso>.sha256`; releases published from now on carry `SHA256SUMS` + `SHA256SUMS.gpg` (§7).
- **The polkit authorization for root is cached for five minutes** by default (`auth_admin_keep`); §1 shows the one-line
  rule that makes every root step ask again.
- **The Fab OS apt repository is served over HTTP** until a certificate is issued for the host; integrity is protected by
  signatures, package names are not private (SECURITY.md "Posture").
- **The `vm` image profile** has autologin, SSH and a known password by design and is never distributed.
