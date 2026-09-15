# Ubuntu derivative compliance checklist

Each item maps to an obligation from Canonical's IP policy, the GPL/LGPL, or
general copyright law, and to the mechanism in this repository that satisfies
it. Re-check this list before every public release.

| # | Obligation | Mechanism | Status |
|---|-----------|-----------|--------|
| 1 | Remove Canonical trademarks (name, logos, artwork) from the product identity | `packages/fabos-branding` diverts os-release, lsb-release, issue, legal, MOTD, dpkg origin; `image/Containerfile` never installs `ubuntu-wallpapers`, `plymouth-theme-ubuntu-*`, `ubuntu-artwork`, `ubuntu-pro-client` | done |
| 2 | Do not claim Canonical endorsement | `legal/TRADEMARKS.md`, `/etc/legal`, About page text | done |
| 3 | "Based on Ubuntu" attribution is factual and allowed | os-release `ID_LIKE=ubuntu`, `/etc/upstream-release/lsb-release` | done |
| 4 | Offer corresponding source for every GPL/LGPL binary distributed | `scripts/source-offer.sh` records the exact package manifest and source URIs per image; `legal/SOURCE-OFFER.md` is the written offer. Mirror the sources for each released ISO for at least 3 years | tooling done; mirror per release |
| 5 | Preserve copyright and license notices | `/usr/share/doc/*/copyright` is never excluded from the image (the Docker base's `dpkg.cfg.d/excludes` is removed before package installation) | done |
| 6 | License our own additions clearly | `LICENSE` (Apache-2.0), `NOTICE`, `legal/ARTWORK.md` | done |
| 7 | Do not link GPL libraries into our Apache-licensed daemons in the same process | FabOS AI daemons are static Rust binaries talking to system components over IPC / exec | done |
| 8 | Proprietary drivers, firmware and patent-encumbered codecs are opt-in | VM profile: `linux-image-virtual`, no `linux-firmware`. ISO profile: `linux-firmware` (redistributable non-free firmware, as on Ubuntu media) is on the ISO; after installation `fabos-firstboot` runs `ubuntu-drivers autoinstall` and installs `linux-firmware`, `libavcodec-extra` and GStreamer "bad" plugins automatically, without a prompt | VM done; **ACTION for ISO: make first-boot driver/codec installation opt-in in the installer or Welcome wizard, or document it as a default in the release notes** |
| 9 | No telemetry without consent; privacy law (DPDP Act India, GDPR) | no telemetry; `ubuntu-report`, `apport` autoreport, `motd-news`, `ubuntu-pro-client` not installed; AI local-first | done |
| 10 | Snap Store terms | snapd not installed and pinned to priority -1 | done |
| 11 | Product name cleared | see `TRADEMARK-SEARCH.md`: "Fab OS" searched 2026-09-11, no registered software mark found in the sources checked (the earlier Brocade conflict concerned the old name "Fabric OS") | searched; **ACTION: formal clearance (IP India Class 9, EUIPO, USPTO, WIPO) and filing before launch** |
| 12 | Fonts redistributable | Inter and JetBrains Mono under SIL OFL 1.1 via Ubuntu packages | done |
| 13 | Browser trademark (Firefox) | Firefox is Mozilla's own unmodified `.deb` from `packages.mozilla.org` (signing key pinned by fingerprint in the build, apt origin pin 1000, `fabos-branding` ships the source and keyring; ADR-0018); Firefox keeps its own branding inside the application; the FabOS launcher tile uses a generic globe glyph, not the Firefox logo; Firefox is never renamed by the catalogs, binary patches or desktop-entry overrides; the only added file is Mozilla's documented enterprise-policy file with unlocked first-run defaults (including `SkipTermsOfUse`, under which Patience AI accepts the Firefox Terms of Use for Fab OS users — legal/OPEN-SOURCE-RELEASE-CHECKLIST.md C6); statement "Firefox is a trademark of the Mozilla Foundation; Fab OS ships Mozilla's own unmodified build" in ATTRIBUTIONS.md, LICENSING.md, NOTICE, legal/TRADEMARKS.md, README. The browser of the 1.0-3 / 1.0-4 images (ADR-0016), with its repository, keyring, setuid helper and trademark lines, was removed when the owner brought Firefox back; upgraded systems are moved over by `fabos-browser-migrate.service` | done |

## Notes

- "Removing Canonical trademarks" does not mean scrubbing the word "ubuntu"
  from package names, apt sources, or the codename field. Those are functional
  and Canonical's policy explicitly allows them.
- The base container image is "minimized" (no man pages). The ISO profile
  should run `unminimize` or install `man-db` and docs so users get a normal
  system. Copyright files are retained in all profiles.
