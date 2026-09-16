#!/bin/sh
# Fab OS installer: shellprocess@efifallback — runs right after the bootloader module, in the target, with /boot/efi still
# mounted. grub-install (Ubuntu, Secure Boot mode) has written EFI/ubuntu/{shimx64.efi,grubx64.efi,mmx64.efi,grub.cfg}
# and the firmware boot entry; Calamares' installEFIFallback copied grubx64.efi to EFI/boot/bootx64.efi. Firmware that
# ignores NVRAM entries boots EFI/boot/bootx64.efi — with Secure Boot on, a bare GRUB there is refused (it is signed by
# Canonical, not Microsoft), so put shim there and GRUB next to it, exactly like `grub-install --force-extra-removable`.
# A file, not an inline command: Calamares expands $name in shellprocess command lines itself (see install-finish.sh).
# Guarded: if anything is missing the Calamares copy stays and this only logs. Offline; always exits 0.
E=/boot/efi/EFI
if [ -f "$E/ubuntu/grubx64.efi" ] && [ -f /usr/lib/shim/shimx64.efi.signed ]; then
  mkdir -p "$E/boot" \
    && cp -f /usr/lib/shim/shimx64.efi.signed "$E/boot/bootx64.efi" \
    && cp -f "$E/ubuntu/grubx64.efi" "$E/boot/grubx64.efi" \
    && { [ -f "$E/ubuntu/mmx64.efi" ] && cp -f "$E/ubuntu/mmx64.efi" "$E/boot/mmx64.efi"; true; } \
    && echo "fabos: EFI/boot fallback = shim + grub"
else
  echo "fabos: no signed GRUB under $E/ubuntu - fallback left as written by Calamares"
fi
ls -la "$E"/boot "$E"/ubuntu 2>/dev/null
exit 0
