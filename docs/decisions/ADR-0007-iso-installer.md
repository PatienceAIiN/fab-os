# ADR-0007: Live/installer ISO with casper + Calamares, Secure Boot via Ubuntu's signed shim and GRUB

## Selected
- `iso` image profile: generic kernel + linux-firmware, casper live boot (user `fabos`, autologin), Calamares
  installer with Fab OS branding and a module sequence matching Ubuntu flavours (partition/LUKS, users,
  displaymanager=sddm session fabos, packages: remove casper/calamares, shellprocess: clean live artefacts,
  grub bootloader, efiBootloaderId fabos).
- ISO layout as Ubuntu: casper/{vmlinuz,initrd,filesystem.squashfs}, EFI/boot/bootx64.efi = shimx64.efi.signed,
  grubx64.efi = gcdx64.efi.signed (reads EFI/boot/grub.cfg → boot/grub/grub.cfg). Built rootless with
  mksquashfs (zstd) and xorriso; UEFI-only.
- First boot after install: `fabos-firstboot` waits for network then installs updates, drivers, firmware,
  codecs and Flathub in the background.

## Tradeoffs
UEFI only (no legacy BIOS). Install testing in QEMU requires a manual Calamares run; live boot is
auto-tested (tests/iso-boot-test.sh). Third-party proprietary drivers arrive at first boot, not on the ISO.
