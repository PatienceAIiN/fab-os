# Fab OS (repo: fabos)

**An AI-native Linux desktop by Patience AI, built on Ubuntu 26.04 LTS.**

Fab OS is Ubuntu underneath — every Ubuntu package, command and update
works unchanged — with its own identity, a KDE Plasma 6 desktop tuned by
Patience AI, and the FabOS AI stack (intents, agents, deterministic governor,
provenance, rollback) built in as first-class OS services.

> Naming notice: "Fab OS" is the current working product name. Trademark
> clearance is still required before a broad public launch; see
> `legal/TRADEMARK-SEARCH.md`. The name is maintained in `brand/brand.conf`.

## Layout

| Path | What |
|------|------|
| `brand/` | `brand.conf` (single source of identity), logo SVGs, `gen/make_assets.py` (renders icons, Plymouth frames, wallpapers, SDDM/KSplash art, 3D logo) |
| `packages/` | Debian packages: `fabos-branding`, `fabos-desktop`, `fabos-ai`, `fabos-desktop-meta` |
| `image/` | `Containerfile` (rootfs recipe, profiles `vm` and `iso`) and overlays |
| `scripts/` | `build-rootfs.sh`, `make-disk.sh`, `boot-vm.sh`, `source-offer.sh`, `stage-ai-binaries.sh` |
| `legal/` | trademarks, Ubuntu-derivative compliance, source offer, artwork, privacy |
| `docs/decisions/` | architecture decision records |
| `tests/` | boot and branding checks |
| `tools/rg` | resource guard — every heavy command runs under it |

## What ships

| Package | Purpose |
|---------|---------|
| `fabos-branding` | identity (os-release, MOTD, GRUB title, Plymouth animation, icons), Mozilla + Fab OS apt sources, KDE feedback panel hidden |
| `fabos-desktop` | Plasma 6 defaults: FabOS rounded desktop theme, FabOS icon theme (Material Symbols tiles), look-and-feel, SDDM greeter, KSplash, wallpapers, "Fab OS" session, Fab Wallet + launcher de-branding |
| `fabos-agent` | the autonomous agent: `fabos-agentd` service, `fabos` CLI, Command Center (Meta+Space), desktop "Ask me to do…" bar, KRunner plugin, file-manager action; Claude / OpenAI / Gemini / local providers; ask/auto/bypass policy; System-Wide AI switch |
| `fabos-ai` | aios/aiosd local-first AI stack from ai-native-os |
| `fabos-feedback` | "Send feedback to Patience AI" dialog + root relay → Brevo → info@patienceai.in |
| `fabos-updates` | Fab OS Updates app (check/install, Standard/Beta channel, automatic updates), daily check with notifications, unattended-upgrades policy |
| `fabos-firstboot` | after installation: updates, drivers (ubuntu-drivers), firmware, codecs, Flathub — in the background |
| `fabos-desktop-meta` | everything above on a stock Ubuntu 26.04 |

## Quick start (developer laptop, no root)

```bash
scripts/stage-ai-binaries.sh               # copies aios/aiosd from ../ai-native-os (or builds them)
MIRROR=http://mirror.nitc.ac.in/ubuntu scripts/build-rootfs.sh vm   # podman build -> ext4 (guarded)
scripts/make-disk.sh && scripts/boot-vm.sh # UEFI disk + QEMU window
tests/boot-test.sh && tests/branding-check.sh vm && python3 tests/agent-test.py && python3 tests/feedback-test.py

scripts/build-rootfs.sh iso && scripts/build-iso.sh   # live/installer ISO (Secure Boot shim + GRUB, Calamares)
scripts/boot-iso.sh --target-disk                     # try it, or install into a 24 GB virtual disk
scripts/publish-apt.sh --channel beta                 # sign + publish packages (stable = --channel stable)
```

Test image login: user `fabos`, password `fabos` (VM profile only, autologin
enabled). Never distribute the VM profile. Live ISO user: `fabos`, no password.

## Update model

- Ubuntu security and package updates: from Ubuntu mirrors, unchanged.
- Fab OS features, AI layer and branding: from the signed Patience AI apt
  repository at fabricos.patienceai.in/apt — suite `loom` (Standard) or
  `loom-beta` (Beta, pushed on every commit to main; Standard on `v*` tags).
  Users switch channels in **Fab OS Updates**; automatic installation is on by default.

## Legal

Apache-2.0 for everything Patience AI wrote; artwork also CC0. Ubuntu is a
trademark of Canonical Ltd.; Fab OS is not affiliated with Canonical. Full
details in `legal/`.
