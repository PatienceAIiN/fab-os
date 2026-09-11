# Fab OS (repo: fabric-os)

**An AI-native Linux desktop by Patience AI, built on Ubuntu 26.04 LTS.**

Fab OS is Ubuntu underneath — every Ubuntu package, command and update
works unchanged — with its own identity, a KDE Plasma 6 desktop tuned by
Patience AI, and the Fabric AI stack (intents, agents, deterministic governor,
provenance, rollback) built in as first-class OS services.

> Naming notice: "Fab OS" is an internal codename. It collides with a
> registered Brocade/Broadcom mark and must be renamed before public release.
> See `legal/TRADEMARK-SEARCH.md`. Renaming is one line in `brand/brand.conf`.

## Layout

| Path | What |
|------|------|
| `brand/` | `brand.conf` (single source of identity), logo SVGs, `gen/make_assets.py` (renders icons, Plymouth frames, wallpapers, SDDM/KSplash art, 3D logo) |
| `packages/` | Debian packages: `fabric-branding`, `fabric-desktop`, `fabric-ai`, `fabric-desktop-meta` |
| `image/` | `Containerfile` (rootfs recipe, profiles `vm` and `iso`) and overlays |
| `scripts/` | `build-rootfs.sh`, `make-disk.sh`, `boot-vm.sh`, `source-offer.sh`, `stage-ai-binaries.sh` |
| `legal/` | trademarks, Ubuntu-derivative compliance, source offer, artwork, privacy |
| `docs/decisions/` | architecture decision records |
| `tests/` | boot and branding checks |
| `tools/rg` | resource guard — every heavy command runs under it |

## Quick start (developer laptop, no root)

```bash
scripts/stage-ai-binaries.sh          # copies aios/aiosd from ../ai-native-os (or builds them)
scripts/build-rootfs.sh vm            # podman build + export + ext4 (guarded)
scripts/make-disk.sh                  # GPT + ESP(systemd-boot) + rootfs -> build/fabric-vm.img
scripts/boot-vm.sh                    # QEMU/KVM + OVMF, 2 GB guest, guarded; window opens
scripts/boot-vm.sh --headless --autotest   # serial-only smoke test, powers off itself
```

Test image login: user `fabric`, password `fabric` (VM profile only, autologin
enabled). Never distribute the VM profile.

## Update model

- Ubuntu security and package updates: from Ubuntu mirrors, unchanged.
- Fab OS features, AI layer and branding: from the Patience AI apt
  repository (`/etc/apt/sources.list.d/fabric.sources`, enabled once the repo
  is live).

## Legal

Apache-2.0 for everything Patience AI wrote; artwork also CC0. Ubuntu is a
trademark of Canonical Ltd.; Fab OS is not affiliated with Canonical. Full
details in `legal/`.
