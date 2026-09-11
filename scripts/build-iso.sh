#!/usr/bin/env bash
# Assemble the Fab OS live/installer ISO (UEFI, Secure Boot via Ubuntu's signed shim + GRUB). Rootless.
# Prereq: scripts/build-rootfs.sh iso   (produces build/filesystem.squashfs, vmlinuz, initrd.img, shimx64.efi, gcdx64.efi)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; . brand/brand.conf
ISO=build/${DISTRO_ID}-${DISTRO_VERSION}-desktop-amd64.iso; T=build/iso-tree; rm -rf "$T"; mkdir -p "$T"/{casper,boot/grub,EFI/boot,.disk}
for f in filesystem.squashfs vmlinuz initrd.img shimx64.efi gcdx64.efi; do [ -f build/$f ] || { echo "missing build/$f — run scripts/build-rootfs.sh iso"; exit 1; }; done
cp build/filesystem.squashfs "$T/casper/"; cp build/vmlinuz "$T/casper/vmlinuz"; cp build/initrd.img "$T/casper/initrd"
cp build/filesystem.size build/filesystem.manifest "$T/casper/" 2>/dev/null || true
echo "$DISTRO_PRETTY_NAME - Release amd64 ($(date -u +%Y%m%d))" > "$T/.disk/info"; echo "$HOME_URL" > "$T/.disk/release_notes_url"; touch "$T/.disk/base_installable"; echo "full_cd/single" > "$T/.disk/cd_type"
cat > "$T/boot/grub/grub.cfg" <<GRUB
set default=0
set timeout=5
set gfxpayload=keep
loadfont unicode
set menu_color_normal=white/black
set menu_color_highlight=black/light-blue
menuentry "Try or Install $DISTRO_NAME" {
    linux /casper/vmlinuz boot=casper quiet splash console=tty1 console=ttyS0,115200 ---
    initrd /casper/initrd
}
menuentry "$DISTRO_NAME (safe graphics)" {
    linux /casper/vmlinuz boot=casper nomodeset quiet splash ---
    initrd /casper/initrd
}
menuentry "Check disc for defects" {
    linux /casper/vmlinuz boot=casper integrity-check quiet splash ---
    initrd /casper/initrd
}
GRUB
# EFI: shim -> signed grub (gcdx64 looks for /EFI/boot/grub.cfg, which chains to /boot/grub/grub.cfg on the ISO)
cp build/shimx64.efi "$T/EFI/boot/bootx64.efi"; cp build/gcdx64.efi "$T/EFI/boot/grubx64.efi"; [ -f build/mmx64.efi ] && cp build/mmx64.efi "$T/EFI/boot/mmx64.efi"
printf 'search --set=root --file /.disk/info\nset prefix=($root)/boot/grub\nconfigfile $prefix/grub.cfg\n' > "$T/EFI/boot/grub.cfg"
rm -f build/efi.img; mkfs.vfat -C -F 12 -n FABOS_EFI build/efi.img 8192 >/dev/null
export MTOOLS_SKIP_CHECK=1; mmd -i build/efi.img ::/EFI ::/EFI/boot
for f in bootx64.efi grubx64.efi mmx64.efi grub.cfg; do [ -f "$T/EFI/boot/$f" ] && mcopy -i build/efi.img "$T/EFI/boot/$f" ::/EFI/boot/$f; done
cp build/efi.img "$T/boot/grub/efi.img"
rm -f "$ISO"
xorriso -as mkisofs -r -V "FABOS" -J -joliet-long -l -iso-level 3 -o "$ISO" \
  -e boot/grub/efi.img -no-emul-boot -append_partition 2 0xef build/efi.img -partition_offset 16 \
  -isohybrid-gpt-basdat "$T" 2>&1 | grep -vE '^xorriso : (UPDATE|NOTE)' | tail -5 || true
[ -s "$ISO" ] || xorriso -as mkisofs -r -V "FABOS" -J -joliet-long -l -iso-level 3 -o "$ISO" -e boot/grub/efi.img -no-emul-boot -append_partition 2 0xef build/efi.img -partition_offset 16 "$T" 2>&1 | tail -3
sha256sum "$ISO" > "$ISO.sha256"; ls -lh "$ISO"; echo "== ISO ready: $ISO"
