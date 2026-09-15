# ADR-0018: Firefox returns — Mozilla's own build from Mozilla's repository, Brave removed, a quiet first run by policy

**Status:** accepted (2026-09-15) · supersedes [ADR-0016](ADR-0016-brave-browser.md) (Brave, which shipped in the 1.0-3 / 1.0-4
images only) · amends ADR-0004 (browser source) and ADR-0006 (update streams)

## Context

The owner changed their mind after the Brave build: Firefox is the browser again and Brave goes. The constraints of ADR-0016
still hold for whichever browser ships: the vendor's own official build, nothing patched or renamed by Fab OS, updates from the
vendor's repository through apt, a redistributable licence, no vendor logo in Fab OS artwork, and — new this time — **Firefox
must open fast and quietly**: no welcome tour, no telemetry notice, no "make Firefox your default browser" bar, no stock
bookmarks, nothing to download.

Ubuntu's own `firefox` package (`1:1snap1-0ubuntu9.1`) is a shim that installs the snap; snap is not installed (ADR-0004), so the
only correct source is Mozilla's apt repository, exactly as round 3 shipped it (commit `0fb1747`).

## Facts verified (2026-09-15, from this machine)

Replayed in a plain `ubuntu:26.04` container with the NITC mirror (`http://mirror.nitc.ac.in/ubuntu`, suite `resolute`):

- Mozilla's key `https://packages.mozilla.org/apt/repo-signing-key.gpg` has fingerprint
  `35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3` — the same one round 3 pinned; the `pkgs` stage fails the build on any other key.
- Source `Types: deb / URIs: https://packages.mozilla.org/apt / Suites: mozilla / Components: main / Signed-By:
  /usr/share/keyrings/packages.mozilla.org.gpg` and pin `Package: * / Pin: origin packages.mozilla.org / Pin-Priority: 1000`
  give `apt-cache policy firefox` the candidate **`155.0.1~build1`** at priority 1000 above the two archive shims (`1:1snap1-*`,
  priority 500); `apt-get install -y firefox` installs it: `Maintainer: Mozilla <release@mozilla.com>`, `Provides:
  gnome-www-browser, www-browser`, `Installed-Size: 322973`, `firefox --version` → `Mozilla Firefox 155.0.1`.
- **Round 4's blocklist entry was a trap.** `00-fabos-blocklist` said `Package: … firefox / Pin: release * / Pin-Priority: -1` to
  keep the snap shim out. A specific-form `Package:` record applies to *every* version of the name, so with it in place Mozilla's
  build is pinned to -1 as well: `Candidate: (none)`. Removing `firefox` from that line restores the candidate. The shim needs no
  pin at all: the origin pin already outranks it and `snapd` itself is pinned -1 by `fabos-nosnap`.
- Mozilla's deb owns `/usr/lib/firefox/distribution/` and `distribution.ini` in it (`id=mozilla-deb`, `about=Mozilla Firefox
  Debian Package`), **not** `policies.json`, and there is no `/etc/firefox`. A Fab OS package can therefore ship
  `/usr/lib/firefox/distribution/policies.json` (dpkg shares the directory; no file conflict). Mozilla's policy documentation
  (`mozilla.github.io/policy-templates`, fetched 2026-09-15) names exactly this place: *"On Linux, the file goes into
  `firefox/distribution`, where `firefox` is the installation directory for firefox … or you can specify system-wide policy by
  placing the file in `/etc/firefox/policies`."*
- Desktop file: `/usr/share/applications/firefox.desktop`, `Exec=firefox %u`, `StartupWMClass=firefox`, `Icon=firefox`, MIME types
  include `text/html`, `x-scheme-handler/http(s)`. The app id / `resourceClass` KWin sees on Wayland is `firefox`.
- The Firefox deb ships no setuid file (Brave's `chrome-sandbox` was the one non-stock setuid file of the 1.0-3 / 1.0-4 images).
  Ubuntu 26.04's `apparmor` package ships `/etc/apparmor.d/firefox`: `profile firefox /{usr/lib/firefox{,-esr,…},opt/firefox}/
  firefox{,-esr,-bin} flags=(unconfined) { userns, … }` — the grant Firefox's content sandbox needs under Ubuntu's
  `kernel.apparmor_restrict_unprivileged_userns = 1` (`/usr/lib/sysctl.d/10-apparmor.conf`). It is already present in the
  current image (`ls /etc/apparmor.d` → `firefox`).
- `firefox-esr` (153.2.0esr) is also in Mozilla's repository; `kdotool` is **not** in Ubuntu 26.04 (`apt-cache policy` empty),
  `xdotool` is X11-only — hence the KWin-script window probe in `tests/browser-vm.sh`.
- Policy keys checked against Mozilla's policy-templates documentation (all present; types as used): `DisableAppUpdate` (bool),
  `OverrideFirstRunPage` (string, `""` disables the page), `OverridePostUpdatePage` (string), `DisableTelemetry` (bool),
  `DisableFirefoxStudies` (bool), `DisablePocket` (bool, **marked deprecated** there — kept because the owner asked for it and a
  deprecated key is still accepted; drop it when Mozilla removes it), `NoDefaultBookmarks` (bool), `DontCheckDefaultBrowser`
  (bool), `Homepage` (`URL`, `Locked`, `StartPage` ∈ `homepage`|`previous-session`), `UserMessaging` (`SkipOnboarding`,
  `ExtensionRecommendations`, `FeatureRecommendations`, `UrlbarInterventions`, `MoreFromMozilla`), `FirefoxHome`
  (`SponsoredTopSites`, `SponsoredPocket`, `Locked`). `FirefoxLabs`, `WhatsNew`, `Stories` exist in the schema but are not used
  (`WhatsNew` is deprecated; `FirefoxLabs` only hides a Preferences section).
- **`SkipTermsOfUse`** (review finding, added 2026-09-15): Firefox 138+ opens with a Terms of Use / Privacy Notice modal at first
  start, and none of the keys above silences it. The schema (`browser/components/enterprisepolicies/schemas/policies-schema.json`,
  fetched from `mozilla-firefox/firefox` main) has `SkipTermsOfUse`: `type: boolean`, `x-category: Startup`, `version_added` 138
  (ESR 140), `x-restart-required`; the Startup category holds exactly `DefaultBrowserSettingEnabled, DisableLaunchOnLogin,
  DontCheckDefaultBrowser, FirefoxHome, Homepage, NewTabPage, OverrideFirstRunPage, OverridePostUpdatePage, ShowHomeButton,
  SkipTermsOfUse`, so with it every Startup-category screen of the shipped 155 build is covered. Mozilla's documentation attaches a
  condition: *"You represent that you accept and have the authority to accept the Terms of Use on behalf of all individuals to whom
  you provide access to this browser."* Shipping the key means Patience AI makes that representation for Fab OS users — recorded
  in `legal/OPEN-SOURCE-RELEASE-CHECKLIST.md` C6, README and `legal/PRIVACY.md`; delete the key to hand that one click back.

## Decision

1. **Containerfile**: the `pkgs` stage fetches Mozilla's key, checks the fingerprint and dearmors it into
   `fabos-branding/usr/share/keyrings/packages.mozilla.org.gpg` (so the installed system keeps the keyring); the rootfs stage
   writes `mozilla.sources` + `preferences.d/mozilla`, installs `firefox`, and fails the build unless `dpkg -s firefox` says
   Mozilla, the desktop file and `firefox-bin` exist, only `ubuntu.sources` and `mozilla.sources` are present, nothing Brave
   remains (`brave-browser`, `brave-keyring`, `/opt/brave.com`, `/etc/default/brave-browser`), `firefox` is not in the
   blocklist, and `/usr/lib/firefox` holds no setuid file. The Brave keyring fetch, source, defaults file and sandbox assertions
   are gone; `firefox` is removed from `00-fabos-blocklist`.
2. **`fabos-branding`** ships `etc/apt/sources.list.d/mozilla.sources` and `etc/apt/preferences.d/mozilla` again (the round-3
   files, verbatim), so a system that adds Fab OS to Ubuntu also gets Firefox from Mozilla.
3. **`fabos-desktop`** sets the default browser in `/etc/xdg/mimeapps.list` (`x-scheme-handler/http(s)`, `text/html`,
   `application/xhtml+xml` → `firefox.desktop`) and ships **`/usr/lib/firefox/distribution/policies.json`** — Mozilla's own
   enterprise-policy mechanism, the only Fab OS addition to Firefox:
   `DisableAppUpdate: false` (updates come from Mozilla through apt; Firefox's own updater is left as Mozilla built it),
   `SkipTermsOfUse: true` (no Terms of Use / Privacy Notice screen — see the representation above),
   `OverrideFirstRunPage: ""`, `OverridePostUpdatePage: ""`, `DisableTelemetry: true`, `DisableFirefoxStudies: true`,
   `DisablePocket: true`, `NoDefaultBookmarks: true`, `DontCheckDefaultBrowser: true`, `Homepage: {URL: <HOME_URL>, Locked:
   false, StartPage: "homepage"}`, `UserMessaging: {SkipOnboarding: true, ExtensionRecommendations: false,
   FeatureRecommendations: false, UrlbarInterventions: false, MoreFromMozilla: false}`, `FirefoxHome: {SponsoredTopSites: false,
   SponsoredPocket: false, Locked: false}`. **Nothing is locked**: every one of these is a default the user can change in
   Firefox's own settings. `@HOME_URL@` is rendered by `packages/build-debs.sh` (`https://fabos.patienceai.in/`). No
   `browser/defaults/preferences/fabos.js` is shipped: the policies cover everything that was asked for, and a prefs file would
   be a second mechanism to keep in step.
4. **`fabos-desktop-meta`** Recommends `firefox` alone. The first draft's `firefox | www-browser` was a trap for upgrades (review
   finding): `brave-browser` Provides `www-browser` (`dpkg -s brave-browser` in the 1.0-4 image), so on an upgraded system the
   Recommends was already satisfied and Firefox never arrived. On a plain Ubuntu the shim is uninstallable (`snapd` pinned -1)
   and apt simply skips the unsatisfiable Recommends until Mozilla's source is in place — the migration job below covers that too.
5. **Privileged-file baseline** back to Ubuntu's stock set: `/opt/brave.com/brave/chrome-sandbox` leaves
   `tests/security/suid-baseline.txt` and the `SUID_OK` allowlist in `tests/branding-check.sh`.
6. **`tests/branding-check.sh`**: the browser block asserts Firefox from Mozilla, Brave absent (package, source, keyring, defaults
   file, desktop files, setuid helper), the Mozilla source with its `Signed-By` keyring and origin pin 1000, `firefox` **not** in
   the blocklist, `firefox.desktop` in `mimeapps.list`, the layout and dock pins, `policies.json` valid with the quiet-first-run
   keys (including `SkipTermsOfUse`) and nothing locked, Mozilla's `distribution.ini` untouched, and Ubuntu's `firefox` AppArmor
   profile parsing. Rewritten in place where the Brave expectation was the defect; three checks appended (181 → 184), then two
   upgrade-path checks (→ 186): the migration unit + script in the image (executable, `ConditionPathExists`, `MemoryHigh`,
   `systemd-analyze verify`, no stale flag) and the source-tree side (meta Recommends `firefox` alone, postinst flags the job,
   the script un-pins firefox and purges the old browser, `policies.json` has `SkipTermsOfUse`, the dock harness fixture pins
   `firefox.desktop`).
7. **New `tests/browser-vm.sh`** (SSH-driven, run by the orchestrator in the VM): Mozilla maintainer + version, `xdg-settings
   get default-web-browser == firefox.desktop`, the agent's `fabos do --mode bypass "open firefox"`, a direct launch and
   `xdg-open https://fabos.patienceai.in/`, each timed from the first firefox process to the first KWin window (budget 12 s),
   one window only after the first launch and no first-run caption (Terms of Use / Privacy Notice / Welcome), a screenshot to
   `build/browser-firefox.png`, PASS/FAIL lines like `tests/agent-live-vm.sh`, numbers in `build/browser-vm.json`. `--selftest`
   runs the window probe's monitor + parser half without a VM: a private `dbus-run-session`, the same `busctl --user monitor`
   line, five calls shaped like the KWin script's, then the parser must report `added=2 first_t=2000 current=1`.
8. **Legal and docs**: Firefox rows back in `ATTRIBUTIONS.md`, `legal/THIRD-PARTY.md`, `THIRD_PARTY_LICENSES/README.md`
   (MPL-2.0 text kept), the trademark sentence *"Firefox is a trademark of the Mozilla Foundation; Fab OS ships Mozilla's own
   unmodified build"* in README, LICENSING, NOTICE, `legal/TRADEMARKS.md` (+ the shipped copy), `legal/PRIVACY.md`,
   `legal/UBUNTU-DERIVATIVE-COMPLIANCE.md` #13, `legal/OPEN-SOURCE-RELEASE-CHECKLIST.md` §C, `SECURITY.md`, `docs/ENTERPRISE.md`;
   the SBOM script annotates `firefox` with Mozilla as supplier and `packages.mozilla.org` as distribution. ADR-0016 stays as the
   record of the two Brave images.

## Upgrading a system installed from the 1.0-3 / 1.0-4 images (review finding, 2026-09-15)

What such a system has (read from `localhost/fabos:vm`, the 1.0-4 image): `brave-browser 1.95.101` (Provides `www-browser`),
`brave-keyring 1.20` owning `/usr/share/keyrings/brave-browser-archive-keyring.gpg`, `/etc/apt/sources.list.d/brave-browser-release.sources`
and `/etc/default/brave-browser` written by the Containerfile (no package owns them), `/etc/apt/preferences.d/00-fabos-blocklist`
with `firefox` on its `Package:` line — also written by the Containerfile, so **no package upgrade ever rewrites it**, and the pin
keeps Mozilla's build at -1 — and `/etc/xdg/mimeapps.list` → `brave-browser.desktop`. A plain `apt upgrade` to the 1.0-5 packages
would therefore have left the system with Brave, a default browser pointing at a desktop file that no longer exists after the layout
switches, and no way to get Firefox short of hand-editing the blocklist. The fix has three parts:

- `/etc/xdg/mimeapps.list` stays a plain file (not a conffile) so the new `fabos-desktop` overwrites it with `firefox.desktop`:
  the system default follows the shipped browser, and a user's own choice lives in `~/.config/mimeapps.list`, which wins anyway.
  Declaring it a conffile now would make dpkg keep the old Brave file under `--force-confold`.
- `fabos-desktop`'s postinst flags **`fabos-browser-migrate.service`** (`touch /var/lib/fabos/browser-migrate-pending`, enable,
  `start --no-block`) whenever it finds `brave-browser` installed, Brave's source file, `firefox` in the blocklist, or no
  Mozilla-maintained `firefox`; on a live system only (`/run/systemd/system`), never in the image build. A fresh image has Mozilla's
  Firefox and nothing of Brave, so nothing is flagged there (asserted by `tests/branding-check.sh`). The unit is a root one-shot like
  `fabos-firstboot` (`Type=oneshot`, `ConditionPathExists` on the flag, `After=network-online.target`, `Nice=10`, idle I/O,
  `MemoryHigh=768M`, `WantedBy=multi-user.target`), so an upgrade done offline is retried at the next boot until it succeeds.
- `/usr/lib/fabos/browser-migrate.sh` (idempotent, also runnable by hand): (1) waits up to 30 min while another apt/dpkg run holds
  a lock (`fuser` on the three lock files — the postinst starts it from inside an apt run) and passes `-o DPkg::Lock::Timeout=600`
  to every `apt-get` (apt 3.2.0 sets `Version::2.0::Dpkg::Lock::Timeout 120` for the `apt` front-end only, not for `apt-get`);
  (2) removes the word `firefox` from the blocklist's `Package:` line; (3) requires Mozilla's source, pin and keyring, all shipped
  by `fabos-branding` — the job never fetches or invents a key; (4) `apt-get update` + `apt-get install firefox` and accepts only
  `Maintainer: Mozilla` (Ubuntu's `firefox` is the snap shim); (5) only then purges `brave-browser` + `brave-keyring` and deletes
  the source, defaults file, keyring and `/opt/brave.com` — the user's Brave profile in `~/.config/BraveSoftware` is left alone
  (Firefox's import wizard reads it); (6) repoints stale per-user pins (`brave-browser.desktop` → `firefox.desktop`) in
  `~/.config/mimeapps.list`, `~/.local/share/applications/mimeapps.list` and the Plasma launcher lists in
  `plasma-org.kde.plasma.desktop-appletsrc` (the dock pin of an existing user; the look-and-feel layout only shapes new users;
  `sed -i` keeps owner and mode); (7) clears the flag, writes `browser-migrate-done` and sends one desktop notification to the
  logged-in users. Every early exit keeps the flag (offline, source missing, install not Mozilla's), so the job simply runs again.

**Why remove rather than offer.** The review suggested "offer to remove". Leaving Brave installed without its apt source would
leave an un-updated browser on the machine; leaving the source would keep trusting a vendor key Fab OS no longer ships. Brave was
a Fab OS component for one release, not a user's choice, and its data survives, so the job removes the package set and tells the
user what happened and where the data is. Reversing this is one `apt install` away for anyone who wants Brave back.

**Replayed in the 1.0-4 image on 2026-09-15** (`podman run localhost/fabos:vm` with the worktree mounted; the new
`fabos-branding` apt files, keyring (fingerprint checked), `browser-migrate.sh`, unit, `mimeapps.list` and `policies.json` copied in
as the upgraded debs would install them; a fabos-user `mimeapps.list` and `appletsrc` seeded with `brave-browser.desktop`): the real
`fabos-desktop` postinst (`configure 1.0-4`) exits 0 and creates the flag; `systemd-analyze verify` of the unit passes; with the
dpkg frontend lock held for 12 s the job removes `firefox` from the blocklist, installs `firefox 155.0.1~build1` from
`packages.mozilla.org` (89.7 MB in 20 s), purges `brave-browser 1.95.101` + `brave-keyring 1.20`, repoints the user files (owner
kept), clears the flag and writes the done marker, exit 0 after 152 s in total; a second run is a no-op `done`; the lock-wait
loop, measured on its own, releases 10 s after an 8 s `flock` hold and at once with no holder. The postinst run again on the
migrated system (`configure 1.0-5`) sets no flag. The exact log is summarised in `docs/QA.md` (browser track).

## Consequences / left for other tracks

- The desktop layout (`org.kde.plasma.desktop-layout.js`, owned by the layout track), the dock plasmoid's default launcher list
  (`in.patienceai.fabos.dock/contents/config/main.xml`) and `tests/layout-js-dry-run.js` still pin `applications:brave-browser.desktop`;
  the two layout/dock checks in `tests/branding-check.sh` now expect `firefox.desktop` and fail until those files switch.
- The agent daemon's app-name table and system prompt (`fabos_agentd.py`: `_app_name`, "brave-browser = Brave for the web") and
  `tests/agent-test.py` (lines asserting "Opening Brave for you now." / "brave-browser = Brave") still say Brave; the ask bar
  (`agent.js`) and `fabos-voice` (`phrases.py`) never dropped Firefox. Fab AI Controls' `APP_NAMES` and its "Try asking" chip are
  switched here.
- `unattended-upgrades` allows Ubuntu security and the Fab OS origins only (`52fabos-unattended`); Firefox updates arrive with
  every `apt upgrade` / Fab Updates run, not unattended — the same as before for Brave. Adding Mozilla's origin is a
  `fabos-updates` decision, not made here.
- `DisablePocket` is deprecated upstream; remove it when Mozilla drops the key.
- Historical records are untouched: ADR-0016, the `1.0-3` / `1.0-4` sections of `docs/QA.md`, and the committed
  `legal/source-offer/*/manifest.txt` files that list `brave-browser 1.95.101` for the images that really contained it.

## Not verified here

A full image rebuild; the live launch timing in a booted VM (`tests/browser-vm.sh` has not been run against a VM yet); that
Firefox 155 honours every key of the shipped `policies.json` at first start — `SkipTermsOfUse` included — (checked against
Mozilla's schema and documentation only; the VM run's one-window and no-first-run-caption checks are what will show it); and the
KWin half of the window probe (`callDBus` from a KWin script, `windowAdded`) against a real session. `tests/perf-vm.sh` uses the
same idea but has never been run (docs/QA.md), so it is no precedent — the first draft of this ADR overstated that. What *is*
verified is the monitor + parser half: `tests/browser-vm.sh --selftest` passes on the host and inside `localhost/fabos:vm` under
`dbus-run-session` (5 method_calls captured by `busctl --user monitor`, parser `added=2 first_t=2000 current=1`); if KWin's
script does not report, the VM run falls back to Firefox's `org.mozilla.firefox` bus name. Also not observed: a real `apt upgrade`
from 1.0-4 to the new debs — the migration was replayed step by step in the 1.0-4 container (above), not through dpkg's own
upgrade sequence.
