# ADR-0021: Installer boot layout — unencrypted /boot, EFI directory `ubuntu`, offline job sequence

## Context
The installer failed on the owner's device ("Installation Failed — Package Manager error", fixed in 692a6f0) and had
never been run end to end by a test. An offline audit of every Calamares 3.3.14 job (tests/calamares-jobs-test.sh,
2026-09-16) against the ISO image found, beyond the packages job:

- Ubuntu's `calamares` package installs no default module configurations, so `mount`, `machineid` and `umount` ran with
  every option off (no `/dev`, `/proc`, `/sys`, efivarfs in the target chroot; no machine-id).
- `services-systemd.conf` used the pre-3.3 nested format, `fstab.conf` and `grubcfg.conf` used keys the modules do not
  read (`keepDistributor`, `mountOptions`), `initramfs.conf` a dracut key.
- Ubuntu's Secure-Boot-signed `grubx64.efi` (grub-efi-amd64-signed 1.215+2.14) carries the fixed prefix `/EFI/ubuntu`
  and no `$cmdpath` logic, and it contains `cryptodisk` + `luks2` but **no `argon2`** module. cryptsetup 2.8 formats
  LUKS2 with argon2id by default. Calamares' default automated layout puts `/boot` inside the LUKS volume, so an
  encrypted install would have ended in a GRUB that can neither find its `grub.cfg` (EFI id `fabos`) nor open the volume.
- `wtype` cannot type into the live desktop: KWin 6.6 implements no virtual-keyboard protocol.

## Decision
1. **Partition layout** (`partition.conf`, `partitionLayout`): EFI system partition (512 MiB, `FABOS-ESP`) + `/boot`
   2 GiB ext4 with `noEncrypt: true` + `/` (rest, the user's filesystem choice; LUKS2/argon2id when encryption is ticked)
   + a 512 MiB swap file. This is the layout Ubuntu's own installer uses. GRUB never touches LUKS; the initramfs
   (cryptsetup-initramfs) asks for the passphrase once per boot through the Fab OS Plymouth theme. Calamares' own logic
   follows: no boot keyfile, `encrypt_hook_nokey`, crypttab key `none`, no `GRUB_ENABLE_CRYPTODISK`.
   *Erase disk* and *Encrypt system* are preselected (`initialPartitioningChoice: erase`, `preCheckEncryption: true`).
2. **EFI directory `ubuntu`** (`bootloader.conf: efiBootloaderId`): required by the signed GRUB's prefix. The firmware
   boot entry is therefore labelled `ubuntu`; the GRUB menu entry says *Fab OS* (`GRUB_DISTRIBUTOR` from the branding).
   After `bootloader`, `shellprocess@efifallback` makes `EFI/boot/bootx64.efi` the signed shim with `grubx64.efi` beside
   it, so firmware that ignores NVRAM entries boots with Secure Boot on.
3. **Every exec module has a config file** in `/etc/calamares/modules` (`mount.conf`, `machineid.conf`, `umount.conf`
   added; the others corrected to the 3.3 schemas), and **nothing in the sequence needs the network**: `packages` only
   `try_remove`s the two live-only packages, every shellprocess line is guarded and logged, `welcome` treats the internet
   check as informative. `tests/calamares-jobs-test.sh` replays all of it in a network-less container.
4. **Automated installation from the host** (`tests/install-vm.sh`, `tests/install-vm-driver.py`): QEMU QMP
   `screendump` + OCR (tesseract inside the ISO image) recognises the Calamares pages by their body text, `send-key` and
   absolute pointer events drive them; the guest helper `live-autoinstall.sh` launches Calamares and reports over the
   serial console. The installed disk is then booted alone and must print `FABOS_INSTALLED_OK`
   (`fabos-firstboot.service` `ExecStartPost`).

## Consequences
- Kernel and initramfs are readable on an unencrypted partition (as on Ubuntu); user data, system and swap file are
  inside LUKS2. Documented in the README ("passphrase at every boot"; no recovery of a lost passphrase).
- Dual-boot with a real Ubuntu shares `EFI/ubuntu` (same as any Ubuntu flavour); the boot entry name is not `Fab OS`.
- A future GRUB with argon2 in Ubuntu's signed image would allow an encrypted `/boot` again; the layout is one file.
- The RAM requirement of the welcome page is 1.5 GiB (README minimum: 2 GB machines are supported).
- `xfs` is no longer offered (no `mkfs.xfs` in the image); `ext4` and `btrfs` are.
