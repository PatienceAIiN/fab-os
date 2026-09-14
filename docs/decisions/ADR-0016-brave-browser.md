# ADR-0016: Brave Browser replaces Firefox, installed unmodified from Brave's own repository

**Status:** accepted (2026-09-15) · amends ADR-0004 (browser source) and ADR-0006 (update streams)

## Context
Owner's request: "ship with brave browser and remove firefox". Firefox came from Mozilla's apt repository (Ubuntu's own
`firefox` package is a snap shim and snap is not installed, ADR-0004). The same constraints apply to Brave: official build
only, nothing patched or renamed by Fab OS, updates keep flowing from the vendor's repository, redistributable licence, no
vendor logo in Fab OS artwork.

## Facts verified (2026-09-15, from this machine and inside the round-3 `localhost/fabos:pkgs` / `:vm` images)
- Repository: `https://brave-browser-apt-release.s3.brave.com` — `dists/stable/InRelease` and `Release` answer 200; the
  `stable/main` Packages index (309 866 bytes) lists `brave-browser 1.95.101` (`Maintainer: Brave Software
  <support@brave.com>`, 148 613 736-byte `.deb`, `Depends: brave-keyring, …, xdg-utils`), earlier versions, and
  `brave-keyring 1.20`.
- Keyring: `brave-browser-archive-keyring.gpg` (3 724 bytes, binary OpenPGP) parses with `gpg --show-keys` and holds three
  4096-bit RSA keys, all uid "Brave Linux Release": fingerprints `DBF1A116C220B8C7164F98230686B78420038257`,
  `47D32A74E9A9E013A4B4926C68D513D36A73CD96`, `B2A3DCA350E67256740DF904DE4EC67BE4B0DCA0`. Brave's own deb822 file
  (`/brave-browser.sources`) is `Types: deb / URIs: https://brave-browser-apt-release.s3.brave.com / Suites: stable /
  Components: main / Architectures: amd64 arm64 / Signed-By: /usr/share/keyrings/brave-browser-archive-keyring.gpg`.
- `brave-keyring 1.20` ships that keyring (plus beta/nightly ones) and `/etc/sysctl.d/30-brave.conf` whose two settings are
  commented out. Its postinst symlinks the key into `/etc/apt/trusted.gpg.d/` (globally trusted) **unless**
  `/etc/apt/sources.list.d/brave-browser-release.sources` exists with the `Signed-By:` line above — so Fab OS uses exactly
  that file name and line, and the key stays scoped to Brave's source. Because `brave-keyring` owns the keyring path, no Fab
  OS package may ship it (dpkg would refuse); the build stages the verified key outside the package tree.
- `brave-browser 1.95.101` deb: `usr/share/applications/brave-browser.desktop` (`Name=Brave Web Browser`,
  `Exec=/usr/bin/brave-browser-stable %U`, `StartupWMClass=brave-browser`, MimeType includes `text/html`,
  `x-scheme-handler/http(s)`, `application/xhtml+xml`) and `com.brave.Browser.desktop`; the postinst registers
  `x-www-browser`/`gnome-www-browser` alternatives and installs Brave's own AppArmor profile: whenever `/etc/apparmor.d/abi/4.0`
  and `/sbin/apparmor_parser` exist (both do in the image) it copies `/opt/brave.com/brave/apparmor.d/brave-browser-stable`
  (`profile brave-browser-stable /opt/brave.com/brave/brave flags=(unconfined) { userns, }`) to `/etc/apparmor.d/` and loads
  it — the "skip if a distro profile exists" branch of that template applies to `google-chrome-stable` only (postinst lines
  118-130). Ubuntu 26.04 already ships `/etc/apparmor.d/brave` (`profile brave /opt/brave.com/brave{,-origin}{,-beta,-nightly}/brave
  flags=(unconfined) { userns, }`, package `apparmor`), so the rebuilt image carries **two** unconfined+userns profiles that
  attach to the same binary. Both parse (`apparmor_parser -QK`, 5.0.2) and are policy-identical, so whichever the kernel
  attaches the Chromium sandbox works under `kernel.apparmor_restrict_unprivileged_userns = 1`; should the kernel report an
  attachment conflict and attach neither, Brave falls back to its setuid sandbox helper (next bullet). The overlapping load at
  boot (`aa-status`, `journalctl -b -u apparmor.service`) is on the next VM boot's checklist and has **not** been run.
- Setuid (security pass): the deb's `data.tar.xz` records exactly one setuid file, `-rwsr-xr-x root/root 15224
  ./opt/brave.com/brave/chrome-sandbox` — Chromium's setuid sandbox helper, used only when the namespace sandbox is unavailable.
  It is the single non-stock setuid-root file in the image (the stock set of the 2026-09-14 `vm`/`iso` images is 13/15 files:
  `chfn chsh fusermount3 gpasswd mount newgrp ntfs-3g passwd pkexec su sudo.ws umount dbus-daemon-launch-helper ssh-keysign
  mount.cifs`). `SECURITY.md` records it, the Containerfile asserts `root:4755`, and `tests/branding-check.sh` compares the
  image's full setuid, setgid and file-capability lists against allowlists that contain it by path. Nothing else in the deb
  is setuid or setgid; `chrome-management-service` becomes setgid `chromemgmt` only if `/etc/default/brave-browser` contains
  `install_device_trust_key_management_command=true`, which the file Fab OS ships does not.
- Packaging quirk (security pass): the deb's postinst and daily cron script (`/etc/cron.daily/brave-browser` →
  `/opt/brave.com/brave/cron/brave-browser`) are Chromium's installer template with **Google's** repository constants left in
  (`REPOCONFIG=… dl.google.com/linux/chrome/deb/`, `install_key` → Google's key as `/usr/share/keyrings/brave-browser.gpg`).
  Both scripts exit before any of that code runs: the postinst `exit 0`s at line 502, immediately before `install_key` at 504
  ("Don't add the Chrome repo (brave/brave-browser#54299)"), and the cron script `exit 0`s at line 23, before `DEFAULTS_FILE`
  (line 26) and `REPOCONFIG` (line 29) are even defined ("Don't add the Chrome repo (brave/brave-browser#1084)"). Installing
  Brave therefore adds no Google source and the cron job is a no-op. Fab OS still ships `/etc/default/brave-browser` with
  `repo_add_once="false"` and `repo_reenable_on_distupgrade="false"` — belt-and-braces for a future package that re-enables the
  template (the postinst reads that file) — and the image has no `cron`/`anacron` (`brave-browser` does not depend on one), so
  `cron.daily` never runs anyway.
- Live test: inside the `fabos:pkgs` image (Ubuntu 26.04 base) the layer was replayed — keyring fetched and checked, the
  `.sources` file written, `apt-get update` against Brave's repository alone succeeded (InRelease verified by the keyring),
  `apt-cache policy` shows candidate 1.95.101, `apt-get install -s brave-browser` resolves to `brave-keyring` +
  `brave-browser`. The full image was **not** rebuilt in this round.

## Decision
1. `image/Containerfile`: the pkgs stage fetches the keyring and fails the build unless gpg reads a keyring with uid
   "Brave Linux Release" carrying one of the three fingerprints above; the rootfs stage writes Brave's documented
   `brave-browser-release.sources` and `/etc/default/brave-browser`, installs `brave-browser`, and fails if
   `firefox` is installed, the desktop file is missing, any source other than Ubuntu's and Brave's exists, or `dl.google.com`
   appears under `/etc/apt/sources.list.d/`. The Mozilla source, pin and keyring are gone; `firefox` is pinned `-1`.
2. Default browser: `/etc/xdg/mimeapps.list` (fabos-desktop) maps `x-scheme-handler/http`, `x-scheme-handler/https`,
   `text/html`, `application/xhtml+xml` to `brave-browser.desktop`. Users change it in Settings > Default Applications.
3. `fabos-desktop-meta` Recommends `brave-browser | www-browser` (on a plain Ubuntu install without Brave's repository the
   alternative satisfies it; adding the repository follows Brave's documentation).
4. Updates: Brave updates arrive through the same `apt` path as everything else, from Brave's repository (README "How
   updates work"). Fab OS never rebuilds, patches or renames Brave.
5. Legal: `ATTRIBUTIONS.md`, `legal/THIRD-PARTY.md`, `THIRD_PARTY_LICENSES/README.md` (MPL-2.0 text already present),
   `LICENSING.md`, `NOTICE`, `legal/TRADEMARKS.md` and README carry: "Brave and the Brave lion logo are trademarks of Brave
   Software, Inc.; Fab OS ships the unmodified official build from Brave's repository and is not endorsed by Brave Software."
   The FabOS icon theme maps `brave-browser` to the same generic globe tile Firefox used, so the lion logo is never part of
   Fab OS artwork (the application keeps its own branding inside).
6. `tests/branding-check.sh`: firefox absent, brave-browser installed from Brave, keyring present, `.sources` correct,
   Mozilla files gone, mimeapps defaults, no Google source, cron re-add disabled.

## Consequences
- Until the image is rebuilt, `localhost/fabos:vm` still contains Firefox; the new checks pass only on a rebuilt image.
- Other tracks: the dock launcher list in the desktop layout (`applications:firefox.desktop`) must become
  `applications:brave-browser.desktop`; the agent/ask-bar/voice app-name tables and the welcome wizard still list Firefox.
- Brave is a third-party application with its own privacy settings (Brave Rewards, Brave News, product analytics); Fab OS
  ships it as delivered and does not preconfigure it (`legal/PRIVACY.md`).
- Brave's password store is not wallet-backed on Fab OS (ADR-0015).
- Brave's `chrome-sandbox` is the one setuid-root file Fab OS adds to Ubuntu's stock set (allowlisted by path; `SECURITY.md`).
- Two AppArmor profiles (`brave`, `brave-browser-stable`) attach to `/opt/brave.com/brave/brave` in the rebuilt image; policy-identical,
  but the overlapping load must be checked in the next VM boot (`aa-status`, apparmor.service journal; the VM self-test prints
  `APPARMOR_BRAVE=<loaded count>:errors=<n>`).
