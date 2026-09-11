# ADR-0002: Rootless image build (podman) + rootless GPT assembly + QEMU/OVMF

## Problem
Images must be built and booted on a 7 GB laptop with no passwordless sudo,
without hanging the host, and reproducibly in CI later.

## Alternatives
live-build / mmdebstrap (not installed on the Fedora host, chroot needs root or
unshare quirks); mkosi (excellent, but Ubuntu 26.04 support on a Fedora host
unverified and needs root for some steps); Cubic (GUI, not reproducible).

## Selected
- Rootfs is a multi-stage `image/Containerfile` on `ubuntu:26.04`, built with
  rootless podman. Stage `assets` renders artwork, stage `pkgs` builds .debs,
  stage `rootfs` installs Ubuntu + Plasma + Fabric packages.
- Export with `podman export`, unpack and `mkfs.ext4 -d` inside `podman
  unshare` so ownership is recorded correctly without host root.
- ESP built with `mkfs.vfat` + `mtools`; GPT written with `sfdisk`; partitions
  placed with `dd conv=notrunc`. Fully rootless.
- Boot: QEMU q35 + KVM + OVMF (UEFI) + systemd-boot, guarded by `tools/rg
  --profile vm` (hard RAM cap, CPU quota, low priority). The guest is capped at
  2 GB by default so the host desktop cannot be starved.
- ISO profile (later): same rootfs squashed with `mksquashfs`, booted by
  casper, installed with Calamares, using Ubuntu's signed shim + GRUB so
  Secure Boot works on real hardware.

## Tradeoffs
Container base is "minimized" (no man pages); we delete the dpkg excludes file
before installing so new packages are complete, and the ISO profile runs
`unminimize`. Copyright files are always kept (legal requirement).

## Security
Base image pinned by digest in CI; guest fully isolated in KVM; VM test image
has a known test password and autologin — never ship the VM profile.
