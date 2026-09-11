# ADR-0001: Base on Ubuntu 26.04 LTS instead of building from scratch

## Problem
The earlier ai-native-os effort assembled its own rootfs, initramfs and boot
path. Every OS feature (installer, hardware support, updates, package archive)
had to be hand-built, and the desktop stayed partial. Fab OS must be a
complete, distributable desktop OS that keeps full Ubuntu software and command
compatibility while carrying its own identity and AI layer.

## Alternatives
1. From scratch (Linux From Scratch / Buildroot): total control, no package
   archive, no security team. Rejected.
2. Debian stable derivative: cleanest trademark story, older packages, older
   kernel. Viable fallback.
3. Ubuntu interim release: newest packages, 9 months support. Rejected.
4. **Ubuntu 26.04 LTS derivative.** Selected.

## Why
- Supported until 2031; first point release (26.04.1) is out, which is the
  point at which derivatives traditionally rebase.
- Kernel 7.0 with sched_ext, current NPU/GPU drivers, memory-safe sudo-rs and
  Rust coreutils by default.
- Users get Ubuntu security updates unchanged, plus Fab OS updates from the
  Patience AI repository. Every `apt install` that works on Ubuntu works here.
- Canonical's IP policy explicitly permits rebranded derivatives.

## Tradeoffs
- Must strip Canonical trademarks (see legal/) and avoid snap.
- `ID=fabos` in os-release will make a few Ubuntu-only checks fail; we set
  `ID_LIKE="ubuntu debian"` and keep `UBUNTU_CODENAME` so PPAs and distro-info
  tooling work, and ship `/etc/upstream-release/lsb-release` (Mint convention).

## Security / performance
Unchanged Ubuntu kernel and shim keep Secure Boot working. AppArmor stays on.
No performance claims until measured (tests/ boot timing).

## Migration path
Rebase every two years at the next LTS point release. Debian remains a drop-in
fallback because everything here is a Containerfile plus .deb packages.
