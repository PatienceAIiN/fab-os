# Fab OS updates over the air

How an installed Fab OS gets Fab OS updates without a reinstall, how the user hears about them, how they are applied
(by hand or automatically), what still has to happen afterwards, and how the whole path is proven locally before a
package revision is published. Everything here ships in the Debian packages (`packages/fabos-updates`,
`packages/fabos-desktop`, `packages/fabos-branding`) — nothing depends on a new ISO.

ADR: [ADR-0006](decisions/ADR-0006-updates-and-channels.md) (signed repository, channels, automatic updates on).

## 1. Where updates come from

| What | Where it lives on the installed system | Shipped by |
|---|---|---|
| Fab OS apt source | `/etc/apt/sources.list.d/fabos.sources` — `URIs: https://fabos.patienceai.in/apt`, `Suites: loom` (Standard) or `loom loom-beta` (Beta), `Components: main`, `Signed-By: /usr/share/keyrings/fabos-archive-keyring.gpg` | `fabos-branding` |
| Archive key | `/usr/share/keyrings/fabos-archive-keyring.gpg` (ed25519 "Fab OS Archive") | `fabos-branding` |
| Ubuntu sources | `/etc/apt/sources.list.d/ubuntu.sources` (unchanged Ubuntu archive) | Ubuntu base |
| Firefox | `/etc/apt/sources.list.d/mozilla.sources` (packages.mozilla.org, origin-pinned 1000) | `fabos-branding` |

The source file and the key are **package content**, so the ISO, the installed system and any later revision carry
exactly the same repository configuration; the installer only verifies they survived the copy
(`image/overlay/iso/usr/lib/fabos/install-finish.sh` logs `fabos: ok /etc/apt/sources.list.d/fabos.sources`). Every
`Release` file is signed with the archive key and apt refuses anything else. The server keeps one `dists/loom` tree
(Standard) and one `dists/loom-beta` tree (Beta); `scripts/publish-apt.sh` builds, signs and rsyncs them.

Every fabos-* package carries the same version `DISTRO_VERSION-PKG_REVISION` from `brand/brand.conf`
(`1.0-7` for this round). Bumping `PKG_REVISION` and publishing is what makes an installed system see an upgrade.

Note for Beta users: `fabos.sources` is not a dpkg conffile, so an upgrade of `fabos-branding` rewrites it to the
Standard channel; Fab Updates shows the channel and one click switches back. (Tracked; the fix belongs to
`fabos-branding`.)

## 2. How the user is told an update exists

Two timers, one notifier, always inside the user's own session:

1. **`fabos-update-check.timer` (system, root)** — 10 min after boot, then every 12 h, `Persistent=true`. Runs
   `/usr/lib/fabos/updates/helper.sh notify-check`: `apt-get update`, then `state.py poke`, which for every logged-in
   user (a `/run/user/<uid>/bus`) runs `systemctl --user daemon-reload` and `systemctl --user start
   fabos-update-notify.service` through `runuser`. Root never talks to the notification server itself.
2. **`fabos-update-notify.timer` (user, enabled `--global`)** — 4 min after login, then every 4 h. Starts the same
   service from inside the session.
3. **`fabos-update-notify.service` (user, oneshot)** runs `state.py session-check`:
   * if `apt list --upgradable` (the lists root refreshed) offers fabos-* packages: one notification
     **"Fab OS update available — Fab OS 1.0-7 is ready to install — open Fab OS Updates."** with an
     **Open Fab OS Updates** button (`notify-send -A`, the session bus, the icon theme). Remembered per offered
     version and per boot in `~/.local/state/fabos/updates-notify.json`, so it is said once, not every 4 hours.
   * after an update was installed (section 4): the "finish it" notification, once per event.

Why not `notify-send` from the system unit: root has no session bus, no icon theme, and the old `runuser … notify-send`
bridge could not carry an action button or remember what it already said. The user unit has all of that and is
started by root only through the user's own systemd manager.

## 3. How updates are applied

**By hand — Fab OS Updates** (`fabos-updates`, menu "Fab OS Updates", also the button on every notification).
*Check for updates* and *Install all updates* run `/usr/lib/fabos/updates/helper.sh check|upgrade` through `pkexec`
(polkit action `in.patienceai.fabos.updates`, the user's own password). `upgrade` = `apt-get update`,
`apt-get full-upgrade` with `--force-confdef --force-confold`, `apt-get autoremove --purge`. The window also switches
the channel (Standard/Beta) and toggles automatic updates.

**Automatically — unattended-upgrades** (on by default; `/etc/apt/apt.conf.d/20auto-upgrades` written by
`fabos-updates` postinst if absent, switchable in Fab OS Updates):

* `apt-daily.timer` refreshes the package lists (Ubuntu default: twice a day, randomised);
  `apt-daily-upgrade.timer` runs `unattended-upgrade` at **06:00 + up to 60 min random delay**, `Persistent=true`
  (a machine that was off runs it at the next start).
* Allowed origins (`/etc/apt/apt.conf.d/52fabos-unattended`, shipped by `fabos-updates`): `Patience AI:loom`,
  `Patience AI:loom-beta`, **`Ubuntu:resolute-security`**, `UbuntuESMApps:resolute-apps-security`,
  `UbuntuESM:resolute-infra-security`, `Mozilla:mozilla`. The Ubuntu names are literal on purpose: on Fab OS
  `lsb_release -is` is `Fabos` and the codename is `loom`, so Ubuntu's own `${distro_id}:${distro_codename}-security`
  template resolves to `Fabos:loom-security` and matches nothing — before 1.0-7 unattended-upgrades installed Fab OS
  packages but **no Ubuntu security updates** on an installed Fab OS. Verified on the 1.0-6 disk
  (`unattended-upgrade --dry-run -d` → "Allowed origins are: o=Fabos,a=loom-security, …, o=Patience AI,a=loom").
* `Unattended-Upgrade::Automatic-Reboot "false"`, `Remove-Unused-Dependencies "true"`. **Nothing ever restarts the
  desktop session, plasmashell, KWin or SDDM during an update** — neither unattended-upgrades nor any maintainer
  script. The user is advised; the user decides.

## 4. What happens after the packages are installed

### The dpkg trigger `fabos-postupgrade`

`fabos-updates` declares (`DEBIAN/triggers`) `interest-noawait fabos-postupgrade` plus file-trigger interests in
`/usr/lib/fabos`, `/usr/share/fabos`, the Fab OS plasmoids, look-and-feel packages, the KWin script, the SDDM theme and
the Plymouth theme. `fabos-desktop` and `fabos-branding` declare `activate-noawait fabos-postupgrade`; `fabos-agent`
and `fabos-voice` activate it through the `/usr/lib/fabos` file interest without any change to their packages.
`fabos-updates`' own postinst calls `dpkg-trigger --no-await fabos-postupgrade` on upgrade. `-noawait`: no package ever
waits on this step, so it can never leave anything unconfigured. dpkg runs `postinst triggered` **once, at the end of
the whole apt run**, after every package is configured → `helper.sh post-upgrade` → `state.py record`:

1. compares the installed fabos-* versions with `/var/lib/fabos/updates/versions` (written at image build / first
   install by `state.py snapshot`; absent on a 1.0-6 system, in which case every installed package counts as changed —
   which is true for 1.0-6 → 1.0-7),
2. appends one line to `/var/lib/fabos/updates/journal` (mirrored in `last-upgrade`):
   `time= mono= boot= version=1.0-7 packages=fabos-agent,… classes=reboot,session,agent,voice`,
   where the classes come from a fixed table:

   | Package changed | Class | What finishes it |
   |---|---|---|
   | `fabos-branding` (Plymouth theme → initramfs) | `reboot` | **Restart** |
   | `fabos-desktop` (plasmoids, look-and-feel, KWin scripts, greeter theme, colour schemes) | `session` | **Log out and back in** |
   | `fabos-agent` (ask-bar plasmoid) | `session` + `agent` | log out and back in; the daemon is restarted automatically |
   | `fabos-voice` | `voice` | the listener is restarted automatically |
   | `fabos-updates`, `fabos-welcome`, `fabos-feedback`, `fabos-firstboot`, `fabos-ai`, `fabos-desktop-meta` | — | nothing (next launch / own postinst) |
   | a new kernel (`linux-version list` newer than `uname -r`) or `/run/reboot-required` | `reboot` | Restart |

3. refreshes the snapshot and **pokes every graphical session** (same mechanism as section 2).

### In the session (`fabos-update-notify.service` → `state.py session-check`)

* **Agent daemon:** if `fabos-agent` changed after `fabos-agent.service` started, and the daemon reports no running,
  queued or approval-waiting task (`GET /status`), `systemctl --user try-restart fabos-agent.service`. A busy agent
  is left alone and retried on the next check. Same for `fabos-voiced.service`.
* **Notification, once per event and boot:** "**Fab OS updated** — Fab OS 1.0-7 is installed. Restart to finish." or
  "… Log out and back in to finish.", with an **Open Fab OS Updates** button.
* plasmashell, KWin, SDDM, the user's windows: **never touched**.

### In Fab OS Updates: the banner

The window computes the same state (`state.py pending`, also `fabos-updates --state` as JSON) against **this boot**
and **this login** (monotonic timestamps of `graphical-session.target`, `fabos-agent.service`, the kernel boot id):

* `needs_restart` → banner **"Fab OS updated — Fab OS 1.0-7 is installed. Restart to finish."** with **Restart now**
  (Plasma's own `org.kde.LogoutPrompt` confirmation, nothing forced) and **Later**;
* else `needs_logout` (a `session`-class change newer than the current login) → **"… Log out and back in to finish."**
  with **Log out now**;
* after the log out / restart the banner is gone by construction (the entry is older than the new session / other
  boot). The status line after *Install all updates* says the same thing ("All updates installed. Restart to finish.").

### Hooks inside the packages (`postinst`, idempotent, never failing the upgrade)

* `fabos-desktop` → `/usr/lib/fabos/desktop-postupgrade.sh <old-version>`: drops the greeter's compiled-QML cache
  (`/var/lib/sddm/.cache/sddm-greeter-qt6/qmlcache`, so the next login screen is built from the new theme files —
  dpkg keeps archive mtimes, which can be older than the cache), refreshes the FabOS icon cache and the desktop
  database, and on a live upgrade also activates the trigger in case `fabos-updates` is older than 1.0-7.
* `fabos-branding`: `update-alternatives` for the Plymouth theme, `update-initramfs -u -k all`, `update-grub`
  (visible after a restart → class `reboot`).
* `fabos-updates`: enables both timers (`--global` for the user one), writes `20auto-upgrades` if absent, snapshots
  on first install, triggers the record on upgrade.

## 5. Testing — prove it locally before publishing

| Test | What it proves | Needs |
|---|---|---|
| `tests/updates-state-test.py` | the state machine: snapshot/record/journal, classes, needs_restart/needs_logout/agent_restart against fake systemd timestamps, one notification per event, "update available" once per offer, JSON output, malformed journal lines | host python3 only (all tools shimmed) |
| `tests/updates-banner-render.sh` | the Fab OS Updates window renders the banner text for a pending restart and for a pending log out (PNGs under `build/`) | podman `localhost/fabos:vm`, offscreen |
| `tests/ota-stage-repo.sh --out DIR --bump +ota1 --sign-test-key` | a throwaway local repository from the built .debs with the working tree's fabos-updates package and the fabos-desktop/fabos-branding maintainer files, versions bumped so an installed system sees an upgrade, signed with a throwaway key (never the real one) | `build/debs`, podman `localhost/fabos:pkgs`, host gpg |
| **`tests/ota-local-vm.sh`** | the real path on a real installed system: a **disposable overlay** of `build/fabos-vm-old.img` (the owner's version), the repository served from this host over HTTP (`http://10.0.2.2:PORT` inside QEMU), the guest's `fabos.sources` rewritten to it, `helper.sh check` + `upgrade` exactly as Fab Updates runs them; asserts every fabos-* package at the repository's version, `trigproc fabos-updates` in `dpkg.log`, no maintainer-script error in `apt/term.log`, initramfs rebuilt, greeter cache dropped, `last-upgrade` stamp with packages/version/classes, `fabos-updates --state` says restart+logout with the version, the agent user service restarted (monotonic timestamp) while plasmashell was not, the notifier journal shows the restart and the notification, both timers enabled, unattended-upgrades' allowed origins include `Patience AI:loom` and `Ubuntu:resolute-security`; screenshots of the desktop and the Fab Updates window | the VM lock, `build/fabos-vm-old.img` |
| `tests/update-channel-test.sh [--repo-url URL] [--overlay]` | the classic channel test (public repository by default) — now also prints the stamp and the banner text | the VM lock |

Typical round:

```
scripts/publish-apt.sh --no-deploy                                        # build + sign build/apt-repo/loom with the real key
tests/updates-state-test.py && tests/updates-banner-render.sh
tests/ota-local-vm.sh --stage                                             # 1.0-N -> 1.0-N+ota1 through the whole path (throwaway key)
tests/ota-local-vm.sh                                                     # the real build/apt-repo/loom, real key, same assertions
scripts/publish-apt.sh                                                    # only after the owner's go-ahead (docs/QA.md release gate)
tests/update-channel-test.sh --expect 1.0-N                               # the public channel, an older disk
```

When the served repository carries the versions already installed, `ota-local-vm.sh` reports a **NO-OP** run: the
plumbing (source rewrite, signed fetch, helper) is exercised and the upgrade assertions are skipped.

## 6. For a user, in short

* You are told when an update is ready (a notification with an **Open Fab OS Updates** button), and it also installs
  by itself every morning around six if the computer is on (or at the next start).
* Fab OS Updates → *Install all updates* installs it now.
* Afterwards Fab OS says exactly one thing: nothing to do, **log out and back in**, or **restart**. Your session is
  never closed for you.
