# Open-source release checklist (2026-09-13)

Review of the repository and the built image (`localhost/fabos:vm`, Ubuntu 26.04, kernel 7.0.0-31-generic,
Plasma 6.6.6) before the repository and a first image are made public. Each item is **PASS** (verified),
**FAIL** (was wrong, fixed in this review) or **ACTION** (cannot be fixed in the repository; a decision or an
external step is needed). Evidence is noted so the check can be repeated.

## A. Canonical intellectual-property rights policy (Ubuntu derivative)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| A1 | Ubuntu trademarks and logos removed from the product identity (name, os-release, greeter, boot, wallpapers, Plymouth/GRUB themes) | PASS | `packages/fabos-branding` diverts os-release/lsb-release/issue/legal/MOTD; `image/Containerfile` never installs `ubuntu-wallpapers`, `plymouth-theme-ubuntu-*`, `ubuntu-artwork`; `tests/branding-check.sh` enforces it; `os-release` in the image reads `NAME="Fab OS"`, `ID=fabos`, `ID_LIKE="ubuntu debian"` |
| A2 | Factual "based on Ubuntu 26.04 LTS" statement only; no suggestion of endorsement | PASS | `legal/TRADEMARKS.md`, `NOTICE`, `README.md`, `/etc/upstream-release/lsb-release` |
| A3 | Ubuntu packages redistributed unmodified from the Ubuntu archive; the shipped OS points at `archive.ubuntu.com` / `security.ubuntu.com`, not the build mirror | PASS | `image/ubuntu.sources` copied last in the Containerfile; verified in the image's `ubuntu.sources` |
| A4 | Modifications to upstream package files disclosed | PASS | `legal/PATCHED-BINARIES.md` (catalogs, byte patches, text-file overrides); exact rule tables in `packages/fabos-desktop/usr/lib/fabos/rebrand-*`; `.fabos-orig` copies kept |
| A5 | Functional identifiers (package names, `UBUNTU_CODENAME`, apt paths) unchanged | PASS | `brand/brand.conf` `BASE_CODENAME=resolute`; image `os-release` keeps `UBUNTU_CODENAME=resolute` |
| A6 | Ubuntu identity files are diverted, not destroyed | PASS | `tests/branding-check.sh` "Ubuntu identity diverted, not lost" |
| A7 | Canonical-controlled services not shipped (snap, Pro, telemetry) | PASS | apt pin -1 on `snapd ubuntu-pro-client ubuntu-advantage-tools ubuntu-report popularity-contest motd-news-config apport whoopsie` |

## B. KDE and other GPL/LGPL software

| # | Item | Status | Evidence |
|---|------|--------|----------|
| B1 | Written offer for corresponding source | PASS | `legal/SOURCE-OFFER.md` (3 years, any third party); shipped as `/usr/share/doc/fabos-branding/SOURCE-OFFER` (checked by `tests/branding-check.sh`) |
| B2 | Per-image manifest and source URIs recorded and mirrored | ACTION | `scripts/source-offer.sh` generates `legal/source-offer/<image-id>/` and can `--download` the sources; **no manifest is committed yet because no image has been released**. Run it for every published image, commit the manifest, mirror the `.dsc`/`.orig.tar` files and print the mirror location in the release notes |
| B3 | Modifications (renamed strings) available in source form | PASS | `rebrand-catalogs`, `rebrand-binaries`, `rebrand-desktop-entries` are scripts inside `fabos-desktop` and in this repository; `SOURCE-OFFER.md` now names them |
| B4 | Copyright and licence notices preserved on the image | PASS | dpkg `excludes` removed before installation; `tests/branding-check.sh` requires more than 300 `/usr/share/doc/*/copyright` files; the catalog generator's `SKIP` regex never rewrites strings containing Copyright/author/licence/URL/e-mail content |
| B5 | Attribution in About dialogs untouched | PASS | ADR-0008/0009; "About KDE" content unchanged (reachable as "About Desktop Platform") |
| B6 | Derived colour schemes (Fab Dark / Fab Light from Breeze) licensed LGPL-3.0-or-later | PASS | `LICENSING.md` ownership table |
| B7 | GPL-2.0 text present (kernel, KDE GPL-2.0+, SDDM, Plymouth, casper, PackageKit) | FAIL, fixed | `THIRD_PARTY_LICENSES/` only had GPL-3; added `GPL-2.0.txt` from `/usr/share/common-licenses/GPL-2` |
| B8 | LGPL-3.0 text present (Breeze, derived schemes) | FAIL, fixed | added `LGPL-3.0.txt`; `GPL.txt`/`LGPL.txt` renamed to `GPL-3.0.txt`/`LGPL-2.1.txt` so the version is explicit |
| B9 | Byte-patched packages all listed in attributions | FAIL, fixed | PackageKit (origin label) is patched but was absent from `ATTRIBUTIONS.md` and `legal/THIRD-PARTY.md`; added |
| B10 | Calamares (GPL-3.0-or-later) unmodified, only branding/config | PASS | `image/overlay/iso/etc/calamares/`; GPL-3.0 text present |
| B11 | Plymouth (GPL-2.0-or-later) unmodified, Fab OS theme only | PASS | `packages/fabos-branding/usr/share/plymouth/themes/fabos`; GPL-2.0 text now present |
| B12 | PyQt6 (GPL-3) used by Apache-2.0 Fab OS apps | PASS | Fab OS apps are distributed as Apache-2.0 source; GPL-3 governs the combined distribution (`ATTRIBUTIONS.md`); Apache-2.0 is GPL-3-compatible |

## C. Mozilla

| # | Item | Status | Evidence |
|---|------|--------|----------|
| C1 | Firefox is Mozilla's own unmodified `.deb` from `packages.mozilla.org` | PASS | Containerfile installs from `https://packages.mozilla.org/apt`, key fingerprint `35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3` verified at build, apt pin 1000; the image's `dpkg -s firefox` Maintainer is Mozilla (checked by `tests/branding-check.sh`) |
| C2 | Firefox not renamed or re-branded by Fab OS | PASS | no `firefox` rule in `rebrand-catalogs`, `rebrand-binaries` or `rebrand-desktop-entries`; only the FabOS icon theme maps the launcher tile to a generic "public" (globe) glyph, not the Firefox logo |
| C3 | Statement that we ship Mozilla's unmodified package | FAIL, fixed | `legal/UBUNTU-DERIVATIVE-COMPLIANCE.md` #13 still said "not preinstalled; pending"; corrected. Now stated in `ATTRIBUTIONS.md`, `LICENSING.md`, `NOTICE`, `README.md` |
| C4 | MPL-2.0 text present | FAIL, fixed | added `THIRD_PARTY_LICENSES/MPL-2.0.txt` |

## D. Other third-party components

| # | Item | Status | Evidence |
|---|------|--------|----------|
| D1 | Google Material Symbols (Apache-2.0): attribution and licence text | PASS | `ATTRIBUTIONS.md`, `legal/ARTWORK.md`, the icon theme's `index.theme` Comment names Google Material Symbols, `THIRD_PARTY_LICENSES/Apache-2.0.txt` |
| D2 | Inter (OFL-1.1, also Apache-2.0 upstream), JetBrains Mono, Noto Sans (OFL-1.1): unmodified Ubuntu packages, OFL text present | FAIL, fixed | `SIL-OFL-1.1.txt` carried the Comfortaa font's copyright header (a font Fab OS does not ship); replaced by a header naming Inter, JetBrains Mono and Noto with their copyright holders; OFL body unchanged |
| D3 | llama.cpp (MIT) and anthropic SDK (MIT): licence text present | FAIL, fixed | `THIRD_PARTY_LICENSES/MIT.txt` actually contained Adobe's Source-fonts OFL notice; replaced with the MIT/Expat text and the two copyright holders |
| D4 | CC0-1.0 text present (artwork dual licence) | FAIL, fixed | added `THIRD_PARTY_LICENSES/CC0-1.0.txt` |
| D5 | Index of which text applies to which component | FAIL, fixed | added `THIRD_PARTY_LICENSES/README.md` |
| D6 | Proprietary drivers, non-free firmware and patent-encumbered codecs | ACTION | ISO carries `linux-firmware`; `fabos-firstboot` runs `ubuntu-drivers autoinstall` and installs `libavcodec-extra` and GStreamer "bad" plugins automatically after install. Compliance item #8 claimed this was opt-in; corrected to describe reality. Decide: opt-in prompt, or document as a default |

## E. Fab OS's own licence, notices and trademarks

| # | Item | Status | Evidence |
|---|------|--------|----------|
| E1 | `LICENSE` contains the full Apache-2.0 text with "Copyright 2026 Patience AI" | FAIL, fixed | the previous `LICENSE` was a 20-line stub pointing at the Apache URL |
| E2 | `NOTICE` names the project, copyright, third-party pointers and trademark disclaimers | FAIL, fixed | rewritten |
| E3 | Contributor Covenant 2.1, contact info@patienceai.in | FAIL, fixed | `CODE_OF_CONDUCT.md` added (text from contributor-covenant.org) |
| E4 | Contributing guide with prerequisites, build/test commands, DCO, no-vendor-trademarks rule | FAIL, fixed | `CONTRIBUTING.md` rewritten |
| E5 | Security policy with scope, agent root-path disclosure expectations, no bounty | FAIL, fixed | `SECURITY.md` rewritten |
| E6 | Issue and pull-request templates | FAIL, fixed | `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md` |
| E7 | No claim of a registered trademark for "Fab OS" or the mark | PASS | a repository-wide search for "registered trademark" matches only the Ubuntu and Debian statements |
| E8 | Stale statement that "Fab OS" collides with a Brocade mark and must be renamed | FAIL, fixed | `legal/TRADEMARKS.md` and the shipped copy `/usr/share/doc/fabos-branding/TRADEMARKS` referred to the *previous* name "Fabric OS"; corrected to the 2026-09-11 search result (no registered software mark found; formal clearance recommended) |
| E9 | Product-name clearance | ACTION | Formal searches (IP India Class 9, EUIPO, USPTO, WIPO) and an Indian Class 9 filing recommended in `TRADEMARK-SEARCH.md` have not been done; "FabOS" is also the name of a German Industrie 4.0 research project |
| E10 | Marketing renders (`marketing/*.png`) | ACTION | Four promotional images carry "All rights reserved" inside the image and are not generated by `brand/gen/`; their provenance and licence are unrecorded. `legal/ARTWORK.md` now says so; decide the licence before publishing |
| E11 | `BUG_REPORT_URL` in `brand/brand.conf` (rendered into `/etc/os-release`) | ACTION | points at `github.com/PatienceAIiN/fabric-os/issues`; the remote is `PatienceAIiN/fab-os`. Not changed here because it alters shipped OS content; fix in `brand/brand.conf` and rebuild |
| E12 | Website copyright footers | ACTION | `website/*/index.html` footers say "All rights reserved" / "All copyright reserved" while the repository is Apache-2.0; decide whether the website sources in this repository are Apache-2.0 too and align the wording |
| E13 | Design intake note references a Figma Community file | PASS, note | `design/tina/README.md` says the file's licence must be recorded before reproducing anything; nothing from it is in the repository (no `design/tina/export/`, now ignored) |

## F. Secrets, credentials and personal data

Scanned the working tree and the full history (all 43 commits, patch form) for `xkeysib-`, `-----BEGIN`,
`PRIVATE KEY`, `ghp_`, `github_pat_`, `AKIA`, `sk-ant`, `sk-`, `Bearer `, `password`, `.env` contents, GPG and
SSH key blocks, public IP addresses, phone numbers and e-mail addresses.

| # | Item | Status | Evidence |
|---|------|--------|----------|
| F1 | API keys, tokens, private keys, certificates in tree or history | PASS | no hits; the only match is the documentation placeholder `ANTHROPIC_API_KEY=sk-ant-...` in `tests/agent-live-test.py` (commit 719b7b5) |
| F2 | `.env` files | PASS | three tracked `.env` files are shipped package defaults with empty or commented credentials (`agent.env`, `ai.env`, `feedback.env`); the real `feedback.env` overlay path is gitignored and was never committed; tests write `BREVO_API_KEY=test-key` to a temporary directory only |
| F3 | `build/` (contains `build/secrets/` with the apt signing key) never tracked | PASS | no tracked path under `build/`; no `build/` path in any commit |
| F4 | Shipped keyring is public material only | PASS | `fabos-archive-keyring.gpg` contains one public key (`Fab OS Archive Automatic Signing Key <support@patienceai.in>`) |
| F5 | Personal e-mail used as demo text in the UI | FAIL, fixed | a personal `<name>@patienceai.in` address in the Command Center placeholder replaced by `someone@example.com`; the plasmoid, tests and docs already used neutral text. Remains in history (commit fd64ec2 and later) |
| F6 | Deploy host and user name in a script | FAIL, fixed | `scripts/publish-apt.sh` defaulted `APT_DEPLOY_TARGET` to a personal user name and the public IP address of the apt server and the key to `~/.ssh/google_compute_engine`; it now requires `APT_DEPLOY_TARGET` to be set and defaults the key to `~/.ssh/id_ed25519`. Remains in history (commit ce9f24d) |
| F7 | Personal e-mail address in commit metadata | ACTION | 10 of 43 commits are authored with a personal Gmail address (the rest as `Patience AI <support@patienceai.in>`). Only a history rewrite before the first public push can change this; not done here per instructions. Alternatively accept it or add a `.mailmap` |
| F8 | Phone numbers | PASS | none in tree or history |
| F9 | Other public IP addresses | PASS | after F6 only `127.0.1.1` in `/etc/hosts` remains |

## G. Repository hygiene

| # | Item | Status | Evidence |
|---|------|--------|----------|
| G1 | `.gitignore` covers `build/`, `__pycache__/`, `*.pyc`, `*.qcow2`, `*.iso`, `*.ext4`, `.env`, editor swap files | FAIL, fixed | extended |
| G2 | Tracked junk | FAIL, fixed | ten `__pycache__/*.pyc` files were tracked (and would have been packed into the `.deb`s); removed from the index; `__pycache__`/`*.pyc` added to `.containerignore` |
| G3 | Executable bits | PASS, one fix | all `scripts/`, `tests/`, `tools/`, package hooks and `usr/lib/fabos` scripts are mode 755 with shebangs; `brand/gen/plasma_theme.py` had a shebang but mode 644 (set to 755); `etc/xdg/plasma-workspace/env/50-fabos-language.sh` is intentionally 644 (sourced, not executed; `build-debs.sh` normalises package modes anyway) |
| G4 | Website: third-party images and local absolute paths | PASS | the only image is `website/fabos.svg`; no `/home/`, `file://` or drive-letter paths; external references are Google Fonts (`fonts.googleapis.com`, `fonts.gstatic.com`), GitHub and patienceai.in |
| G5 | Large binaries | PASS, note | four `marketing/*.png` at about 1.5 MB each (see E10); consider moving marketing material out of the source repository |

## Summary

PASS 29 (three of them with notes) · FAIL (fixed) 20 · ACTION 7 rows (B2, D6, E9, E10, E11, E12, F7), plus
HTTPS transport for the Fab OS apt repository as recorded in `LICENSING.md` / `SECURITY.md`).

Blocking before a public image (not before publishing the source): B2 (source manifest and mirror per
image), E9 (name clearance), D6 (decide on automatic driver/codec installation), E11 (bug-report URL).
Before publishing the source: E10 (licence of the marketing renders) and F7 (decide on author identities in
history).
