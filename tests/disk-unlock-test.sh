#!/usr/bin/env bash
# Offline test of packages/fabos-agent/usr/lib/fabos/agent/disk_unlock.sh (the root helper behind Settings › General › Start-up).
# Runs the helper against a temp /etc (crypttab, conf-hook, initramfs.conf, keyfile, fake /boot) with STUB cryptsetup /
# update-initramfs / lsinitramfs / findmnt on PATH that record every call (argv + stdin) and simulate success or failure:
#   status on an unencrypted system · off (crypttab key + initramfs option, conf-hook pattern, UMASK=0077, keyfile 4096 B 0400,
#   passphrase on cryptsetup's STDIN only, luksAddKey, update-initramfs -u -k all, lsinitramfs proof) · off with a wrong
#   passphrase changes nothing · rollback when update-initramfs fails / when the key is not in the initrd (files byte-identical,
#   slot removed, keyfile gone, initramfs rebuilt) · on (key -> none, initramfs option dropped, pattern dropped, safe order:
#   rebuild + prove BEFORE luksRemoveKey, keyfile deleted) · on is idempotent · the passphrase on argv is refused · non-root refused.
# Runs on the host (bash, coreutils, awk, python3) or inside the image:
#   podman run --rm -v $PWD:/work:Z localhost/fabos:vm bash /work/tests/disk-unlock-test.sh
# Exit 0 only when every check passed.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
HELPER=$ROOT/packages/fabos-agent/usr/lib/fabos/agent/disk_unlock.sh
T=$(mktemp -d "${TMPDIR:-/tmp}/fabos-disk-unlock-test.XXXXXX"); trap 'rm -rf "$T"' EXIT
PASS_N=0; FAIL_N=0
ok()   { PASS_N=$((PASS_N+1)); echo "PASS  $1"; }
fail() { FAIL_N=$((FAIL_N+1)); echo "FAIL  $1"; }
chk()  { if eval "$2" >/dev/null 2>&1; then ok "$1"; else fail "$1"; fi; }

UUID=1c2db46b-c108-47de-aa78-72cb6ff4904c; NAME=luks-$UUID; PASS=fabos-test; KERNEL=$(uname -r)
mkdir -p "$T/bin" "$T/etc/fabos" "$T/etc/cryptsetup-initramfs" "$T/etc/initramfs-tools" "$T/boot"
export FABOS_DU_CRYPTTAB=$T/etc/crypttab FABOS_DU_KEYFILE=$T/etc/fabos/luks-unlock.key FABOS_DU_CONF_HOOK=$T/etc/cryptsetup-initramfs/conf-hook \
       FABOS_DU_INITRAMFS_CONF=$T/etc/initramfs-tools/initramfs.conf FABOS_DU_BOOT=$T/boot FABOS_DU_LOG=$T/log/disk-unlock.log FABOS_DU_ALLOW_NONROOT=1 \
       FABOS_DU_FSTAB=$T/etc/fstab FABOS_DU_LOCK=$T/disk-unlock.lock FABOS_DU_IT_STATE=$T/var/lib/initramfs-tools \
       FABOS_DU_GRUB_CFG=$T/boot/grub/grub.cfg FABOS_DU_GRUBENV=$T/boot/grub/grubenv FABOS_DU_EFI_DIR=$T/boot/efi/EFI FABOS_DU_RECORD=$T/var/lib/fabos/disk-unlock-check.json
export STUB_LOG=$T/calls.log STUB_STDIN=$T/stdin.log STUB_PASS=$PASS STUB_INITRD=$T/boot/initrd.img-$KERNEL STUB_KEYFILE=$FABOS_DU_KEYFILE STUB_NAME=$NAME STUB_UUID=$UUID
export STUB_ROOT_SRC=/dev/mapper/$NAME STUB_FAIL_UPDATE=0 STUB_HIDE_KEY=0 STUB_FAIL_REMOVE=0 STUB_FAIL_UPDATE_FROM=0 STUB_FAIL_KEYTEST=0 STUB_BOOT_UNMOUNTED=0 STUB_UI_COUNT=$T/ui.count STUB_REBUILD_ALL=0
# the installed layout (ADR-0021): /boot is its own partition, listed in fstab — the helper must see it mounted; one kernel version
# registered with initramfs-tools (what `update-initramfs -k all` rebuilds and GRUB boots)
printf 'UUID=root / ext4 defaults 0 1\nUUID=boot %s ext4 defaults 0 2\nUUID=esp /boot/efi vfat umask=0077 0 1\n' "$FABOS_DU_BOOT" > "$FABOS_DU_FSTAB"
mkdir -p "$FABOS_DU_IT_STATE" && : > "$FABOS_DU_IT_STATE/$KERNEL"

# ---- stubs (record argv; cryptsetup also records what it read on stdin and checks the passphrase)
cat > "$T/bin/findmnt" <<'EOF'
#!/bin/sh
echo "findmnt $*" >> "$STUB_LOG"
case " $* " in *" UUID "*) case " $* " in *" $FABOS_DU_BOOT "*) echo 2b7e1516-28ae-d2a6-abf7-158809cf4f3c;; *) echo 0badf00d-0000-4000-8000-000000000001;; esac; exit 0;; esac
case " $* " in *" $FABOS_DU_BOOT "*) [ "${STUB_BOOT_UNMOUNTED:-0}" = 1 ] && exit 1; echo "$FABOS_DU_BOOT"; exit 0;; esac
echo "$STUB_ROOT_SRC"
EOF
cat > "$T/bin/lsblk" <<'EOF'
#!/bin/sh
echo "lsblk $*" >> "$STUB_LOG"; echo crypto_LUKS
EOF
cat > "$T/bin/unmkinitramfs" <<'EOF'
#!/bin/sh
# the fake initrd is a listing file; the crypttab INSIDE it sits next to it as <initrd>.ct (written by the update-initramfs stub)
echo "unmkinitramfs $*" >> "$STUB_LOG"; [ "$1" = "--" ] && shift
mkdir -p "$2/cryptroot" && { [ -f "$1.ct" ] && cp "$1.ct" "$2/cryptroot/crypttab" || : > "$2/cryptroot/crypttab"; }
EOF
cat > "$T/bin/update-grub" <<'EOF'
#!/bin/sh
# regenerates grub.cfg: the default entry boots the running kernel's initrd (as grub-mkconfig would after a kernel removal)
echo "update-grub $*" >> "$STUB_LOG"
printf "set default=\"0\"\nmenuentry 'Fab OS' --class fabos \$menuentry_id_option 'gnulinux-simple-%s' {\n\tlinux\t/vmlinuz-%s root=/dev/mapper/%s ro quiet splash\n\tinitrd\t/initrd.img-%s\n}\n" "$STUB_UUID" "$(uname -r)" "$STUB_NAME" "$(uname -r)" > "$FABOS_DU_GRUB_CFG"
EOF
cat > "$T/bin/cryptsetup" <<'EOF'
#!/bin/bash
echo "cryptsetup $*" >> "$STUB_LOG"
for a in "$@"; do [ "$a" = "$STUB_PASS" ] && { echo "PASSPHRASE ON ARGV" >> "$STUB_LOG"; exit 99; }; done
case " $* " in
  *" luksDump "*)     # one slot for the passphrase, one more while the keyfile has a slot
    echo "Keyslots:"; echo "  0: luks2"; [ -f "$STUB_KEYFILE.slot" ] && echo "  1: luks2"; exit 0;;
  *" --test-passphrase "*)
    if [[ " $* " == *" --key-file "* ]]; then
      [ "${STUB_FAIL_KEYTEST:-0}" = 1 ] && exit 2
      kf=$(sed -n 's/.*--key-file \([^ ]*\).*/\1/p' <<<" $* "); [ -f "$kf" ] && [ -f "$STUB_KEYFILE.slot" ] && cmp -s "$kf" "$STUB_KEYFILE.slot" && exit 0; exit 2
    fi
    IFS= read -r p; printf '%s' "$p" >> "$STUB_STDIN"; echo >> "$STUB_STDIN"; [ "$p" = "$STUB_PASS" ] && exit 0; exit 2;;
  *" luksAddKey "*)
    IFS= read -r p; printf '%s' "$p" >> "$STUB_STDIN"; echo >> "$STUB_STDIN"; [ "$p" = "$STUB_PASS" ] || exit 2
    cp "${!#}" "$STUB_KEYFILE.slot"; exit 0;;      # the slot now holds the keyfile's content
  *" luksRemoveKey "*)
    [ "$STUB_FAIL_REMOVE" = 1 ] && exit 1
    [ -f "$STUB_KEYFILE.slot" ] && cmp -s "${!#}" "$STUB_KEYFILE.slot" && { rm -f "$STUB_KEYFILE.slot"; exit 0; }; exit 1;;
esac
exit 0
EOF
cat > "$T/bin/update-initramfs" <<'EOF'
#!/bin/bash
# rebuilds the fake initrd: a listing file. The key goes in when the crypttab entry names an existing keyfile matching conf-hook's pattern.
echo "update-initramfs $*" >> "$STUB_LOG"
n=$(( $(cat "$STUB_UI_COUNT" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_UI_COUNT"      # STUB_FAIL_UPDATE_FROM=N: the Nth call on fails
if [ "$STUB_FAIL_UPDATE" = 1 ] || { [ "${STUB_FAIL_UPDATE_FROM:-0}" -gt 0 ] && [ "$n" -ge "$STUB_FAIL_UPDATE_FROM" ]; }; then echo "update-initramfs: failed to create the image (simulated)" >&2; exit 1; fi
key=$(awk -v n="$STUB_NAME" '$1==n {print $3}' "$FABOS_DU_CRYPTTAB"); src=$(awk -v n="$STUB_NAME" '$1==n {print $2}' "$FABOS_DU_CRYPTTAB"); opts=$(awk -v n="$STUB_NAME" '$1==n {print $4}' "$FABOS_DU_CRYPTTAB")
pat=$(sed -n 's/^KEYFILE_PATTERN="\([^"]*\)".*/\1/p' "$FABOS_DU_CONF_HOOK" 2>/dev/null | tail -1)
build() {   # build <initrd>: the listing + the crypttab the cryptroot hook would put inside (<initrd>.ct): key none -> asks; key matching
            # KEYFILE_PATTERN -> copied to cryptroot/keyfiles/<name>.key; key NOT matching -> the root target is SKIPPED (no line at all)
  { echo "."; echo "init"; echo "cryptroot"; echo "cryptroot/crypttab"; echo "scripts/local-top/cryptroot"; echo "usr/bin/cryptroot-unlock"; } > "$1"
  if [ -z "$key" ] || [ "$key" = none ]; then printf '%s %s none %s\n' "$STUB_NAME" "$src" "$opts" > "$1.ct"
  elif [ -f "$key" ] && [ "$key" = "$pat" ] && [ "$STUB_HIDE_KEY" != 1 ]; then { echo "cryptroot/keyfiles"; echo "cryptroot/keyfiles/$STUB_NAME.key"; } >> "$1"; printf '%s %s /cryptroot/keyfiles/%s.key %s\n' "$STUB_NAME" "$src" "$STUB_NAME" "$opts" > "$1.ct"
  else printf '# root target skipped: key file does not match KEYFILE_PATTERN\n' > "$1.ct"; fi
}
build "$STUB_INITRD"
if [ "${STUB_REBUILD_ALL:-0}" = 1 ]; then for v in "$FABOS_DU_IT_STATE"/*; do [ -f "$v" ] && [ "$FABOS_DU_BOOT/initrd.img-${v##*/}" != "$STUB_INITRD" ] && build "$FABOS_DU_BOOT/initrd.img-${v##*/}"; done; fi
exit 0
EOF
cat > "$T/bin/lsinitramfs" <<'EOF'
#!/bin/sh
echo "lsinitramfs $*" >> "$STUB_LOG"; cat "$1"
EOF
chmod 755 "$T"/bin/*
export PATH=$T/bin:$PATH

reset_system() {   # a freshly installed Fab OS (ADR-0021): crypttab from Calamares, Ubuntu's conf-hook + initramfs.conf, an initrd without keys
  printf '# /etc/crypttab: mappings for encrypted partitions.\n%s UUID=%s none luks,discard\n' "$NAME" "$UUID" > "$FABOS_DU_CRYPTTAB"
  printf '# Configuration options for the cryptroot initramfs hook.\n#KEYFILE_PATTERN=\n' > "$FABOS_DU_CONF_HOOK"
  printf '# initramfs.conf\nMODULES=most\nBUSYBOX=auto\nCOMPRESS=zstd\n' > "$FABOS_DU_INITRAMFS_CONF"
  rm -f "$FABOS_DU_KEYFILE" "$FABOS_DU_KEYFILE.slot" "$STUB_LOG" "$STUB_STDIN" "$FABOS_DU_RECORD"; : > "$STUB_LOG"; : > "$STUB_STDIN"
  STUB_FAIL_UPDATE=0 STUB_HIDE_KEY=0 STUB_FAIL_REMOVE=0 STUB_FAIL_UPDATE_FROM=0 STUB_FAIL_KEYTEST=0 STUB_BOOT_UNMOUNTED=0 STUB_REBUILD_ALL=0
  rm -f "$T"/boot/initrd.img-*; : > "$FABOS_DU_IT_STATE/$KERNEL"; for v in "$FABOS_DU_IT_STATE"/*; do [ "${v##*/}" = "$KERNEL" ] || rm -f "$v"; done
  # GRUB as grub-mkconfig writes it (a separate /boot: paths relative to it) + the EFI stub chain-loading it from the /boot filesystem
  mkdir -p "$T/boot/grub" "$T/boot/efi/EFI/fabos"; rm -f "$FABOS_DU_GRUBENV"
  printf "set default=\"0\"\nif [ x\"\${feature_menuentry_id}\" = xy ]; then\n  menuentry_id_option=\"--id\"\nfi\nmenuentry 'Fab OS' --class fabos \$menuentry_id_option 'gnulinux-simple-%s' {\n\trecordfail\n\tlinux\t/vmlinuz-%s root=/dev/mapper/%s ro quiet splash\n\tinitrd\t/initrd.img-%s\n}\nsubmenu 'Advanced options for Fab OS' \$menuentry_id_option 'gnulinux-advanced-%s' {\n\tmenuentry 'Fab OS, with Linux %s' \$menuentry_id_option 'gnulinux-%s-advanced-%s' {\n\t\tlinux\t/vmlinuz-%s root=/dev/mapper/%s ro quiet splash\n\t\tinitrd\t/initrd.img-%s\n\t}\n\tmenuentry 'Fab OS, with Linux %s (recovery mode)' {\n\t\tlinux\t/vmlinuz-%s root=/dev/mapper/%s ro recovery nomodeset\n\t\tinitrd\t/initrd.img-%s\n\t}\n}\n" \
    "$UUID" "$KERNEL" "$NAME" "$KERNEL" "$UUID" "$KERNEL" "$KERNEL" "$UUID" "$KERNEL" "$NAME" "$KERNEL" "$KERNEL" "$KERNEL" "$NAME" "$KERNEL" > "$FABOS_DU_GRUB_CFG"
  printf "search.fs_uuid 2b7e1516-28ae-d2a6-abf7-158809cf4f3c root hd0,gpt2\nset prefix=(\$root)'/grub'\nconfigfile \$prefix/grub.cfg\n" > "$T/boot/efi/EFI/fabos/grub.cfg"
  update-initramfs -u -k all >/dev/null; : > "$STUB_LOG"; rm -f "$STUB_UI_COUNT"
  cp "$FABOS_DU_CRYPTTAB" "$T/crypttab.orig"; cp "$FABOS_DU_CONF_HOOK" "$T/conf-hook.orig"; cp "$FABOS_DU_INITRAMFS_CONF" "$T/initramfs.conf.orig"
}
last_json() { tail -1 "$1"; }
jget() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print(json.dumps(v) if not isinstance(v,str) else v)' "$1" "$2" 2>/dev/null; }

echo "### disk-unlock-test — helper $HELPER — temp $T"
chk "helper is executable and passes bash -n" "[ -x $HELPER ] && bash -n $HELPER"

# ---- 1. status on an unencrypted system
reset_system; STUB_ROOT_SRC=/dev/vda2 "$HELPER" status > "$T/st0.json"; rc=$?
chk "status (unencrypted): exit 0, encrypted=false, prompt_at_boot=false" "[ $rc = 0 ] && [ \"\$(jget $T/st0.json encrypted)\" = false ] && [ \"\$(jget $T/st0.json prompt_at_boot)\" = false ]"
"$HELPER" status > "$T/st1.json"; rc=$?
chk "status (encrypted, fresh install): encrypted=true, device=$NAME, prompt_at_boot=true, consistent" "[ $rc = 0 ] && [ \"\$(jget $T/st1.json encrypted)\" = true ] && [ \"\$(jget $T/st1.json device)\" = $NAME ] && [ \"\$(jget $T/st1.json prompt_at_boot)\" = true ] && [ \"\$(jget $T/st1.json consistent)\" = true ]"
chk "status as non-root (no FABOS_DU_ALLOW_NONROOT) still works and reads the initrd it can" "env -u FABOS_DU_ALLOW_NONROOT $HELPER status | grep -q '\"encrypted\": true'"

# ---- 2. the passphrase is never accepted on the command line; stdin must carry it
"$HELPER" off "$PASS" > "$T/argv.json" 2>/dev/null </dev/null; rc=$?
chk "off with the passphrase on argv is refused (exit 2) and nothing changed" "[ $rc = 2 ] && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && [ ! -e $FABOS_DU_KEYFILE ] && ! grep -q cryptsetup $STUB_LOG"
"$HELPER" off > "$T/empty.json" 2>/dev/null </dev/null; rc=$?
chk "off with empty stdin is refused (exit 2)" "[ $rc = 2 ] && grep -q 'no passphrase' $T/empty.json"
printf 'wrong-pass\n' | "$HELPER" off > "$T/wrong.json" 2>/dev/null; rc=$?
chk "off with a wrong passphrase: exit 3, 'not accepted', prompt_at_boot stays true, files byte-identical, no keyfile, no luksAddKey" \
    "[ $rc = 3 ] && grep -q 'not accepted' $T/wrong.json && [ \"\$(jget $T/wrong.json prompt_at_boot)\" = true ] && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && cmp -s $FABOS_DU_CONF_HOOK $T/conf-hook.orig && [ ! -e $FABOS_DU_KEYFILE ] && ! grep -q luksAddKey $STUB_LOG && ! grep -q update-initramfs $STUB_LOG"
if [ "$(id -u)" = 0 ]; then echo "SKIP  non-root refusal (this test runs as root here)"; else
chk "non-root without the test seam: off refused (exit 4), nothing run" "printf '%s\n' $PASS | env -u FABOS_DU_ALLOW_NONROOT $HELPER off >/dev/null 2>&1; [ \$? = 4 ] && ! grep -q luksAddKey $STUB_LOG"; fi

# ---- 3. off, happy path
reset_system
printf '%s\n' "$PASS" | "$HELPER" off > "$T/off.json" 2> "$T/off.err"; rc=$?
chk "off: exit 0, ok=true, prompt_at_boot=false" "[ $rc = 0 ] && [ \"\$(jget $T/off.json ok)\" = true ] && [ \"\$(jget $T/off.json prompt_at_boot)\" = false ]"
chk "off: crypttab entry -> keyfile column + 'initramfs' added, luks,discard kept, comment line kept" \
    "grep -qx \"$NAME UUID=$UUID $FABOS_DU_KEYFILE luks,discard,initramfs\" $FABOS_DU_CRYPTTAB && grep -q '^# /etc/crypttab' $FABOS_DU_CRYPTTAB && [ \$(grep -c . $FABOS_DU_CRYPTTAB) = 2 ]"
chk "off: conf-hook KEYFILE_PATTERN=\"$FABOS_DU_KEYFILE\" (Ubuntu's comment kept, the commented example gone)" \
    "grep -q \"^KEYFILE_PATTERN=\\\"$FABOS_DU_KEYFILE\\\"\" $FABOS_DU_CONF_HOOK && grep -q '^# Configuration options' $FABOS_DU_CONF_HOOK && [ \$(grep -c '^KEYFILE_PATTERN=' $FABOS_DU_CONF_HOOK) = 1 ]"
chk "off: initramfs.conf gains UMASK=0077 once, other keys kept" "grep -qx 'UMASK=0077' $FABOS_DU_INITRAMFS_CONF && [ \$(grep -c '^UMASK=' $FABOS_DU_INITRAMFS_CONF) = 1 ] && grep -q '^MODULES=most' $FABOS_DU_INITRAMFS_CONF"
chk "off: keyfile is 4096 random bytes, mode 0400" "[ \"\$(stat -c %s $FABOS_DU_KEYFILE)\" = 4096 ] && [ \"\$(stat -c %a $FABOS_DU_KEYFILE)\" = 400 ] && [ \$(tr -d '\\0' < $FABOS_DU_KEYFILE | wc -c) -gt 3000 ]"
chk "off: cryptsetup calls in order: open --test-passphrase, luksAddKey <dev> <keyfile>, then open --test-passphrase --key-file" \
    "[ \"\$(grep '^cryptsetup' $STUB_LOG | tr '\\n' '|')\" = \"cryptsetup open --test-passphrase /dev/disk/by-uuid/$UUID|cryptsetup -q luksAddKey /dev/disk/by-uuid/$UUID $FABOS_DU_KEYFILE|cryptsetup open --test-passphrase --key-file $FABOS_DU_KEYFILE /dev/disk/by-uuid/$UUID|\" ]"
chk "off: the passphrase reached cryptsetup on STDIN (twice) and never on any argv" "[ \"\$(tr '\\n' '|' < $STUB_STDIN)\" = '$PASS|$PASS|' ] && ! grep -q 'PASSPHRASE ON ARGV' $STUB_LOG && ! grep -q -- \"$PASS\" $STUB_LOG"
chk "off: update-initramfs -u -k all ran once, lsinitramfs proved the key inside the running kernel's initrd" \
    "[ \$(grep -c '^update-initramfs -u -k all' $STUB_LOG) = 1 ] && grep -q \"^lsinitramfs $STUB_INITRD\" $STUB_LOG && grep -qx 'cryptroot/keyfiles/$NAME.key' $STUB_INITRD"
chk "off: the passphrase is in no log, no JSON, no stderr" "! grep -q -- $PASS $T/off.json $T/off.err $FABOS_DU_LOG"
chk "off: audit log has the steps (keyfile created, key slot added, crypttab, conf-hook, initramfs.conf, verified, DONE)" \
    "grep -q 'keyfile created' $FABOS_DU_LOG && grep -q 'key slot added' $FABOS_DU_LOG && grep -q 'crypttab:' $FABOS_DU_LOG && grep -q 'conf-hook:' $FABOS_DU_LOG && grep -q 'UMASK=0077' $FABOS_DU_LOG && grep -q 'verified: .*contains cryptroot' $FABOS_DU_LOG && grep -q 'DONE: the computer will start without asking' $FABOS_DU_LOG"
"$HELPER" status > "$T/st2.json"
chk "status after off: prompt_at_boot=false, keyfile_present, keyfile_in_initramfs, consistent" "[ \"\$(jget $T/st2.json prompt_at_boot)\" = false ] && [ \"\$(jget $T/st2.json keyfile_present)\" = true ] && [ \"\$(jget $T/st2.json keyfile_in_initramfs)\" = true ] && [ \"\$(jget $T/st2.json consistent)\" = true ]"
: > "$STUB_LOG"; printf '%s\n' "$PASS" | "$HELPER" off > "$T/off2.json" 2>/dev/null; rc=$?
chk "off again is idempotent: exit 0, 'already', no cryptsetup / update-initramfs call" "[ $rc = 0 ] && grep -q already $T/off2.json && ! grep -q '^cryptsetup' $STUB_LOG && ! grep -q '^update-initramfs' $STUB_LOG"

# ---- 4. on, happy path (safe order: rebuild + prove BEFORE the slot goes)
: > "$STUB_LOG"; "$HELPER" on > "$T/on.json" 2> "$T/on.err"; rc=$?
chk "on: exit 0, ok=true, prompt_at_boot=true" "[ $rc = 0 ] && [ \"\$(jget $T/on.json ok)\" = true ] && [ \"\$(jget $T/on.json prompt_at_boot)\" = true ]"
chk "on: crypttab byte-identical to the fresh install (key none, initramfs option dropped, luks,discard kept)" "cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig"
chk "on: conf-hook has no KEYFILE_PATTERN= line left, Ubuntu's comment kept" "! grep -q '^KEYFILE_PATTERN=' $FABOS_DU_CONF_HOOK && grep -q '^# Configuration options' $FABOS_DU_CONF_HOOK"
chk "on: UMASK=0077 stays (stricter, harmless)" "grep -qx 'UMASK=0077' $FABOS_DU_INITRAMFS_CONF"
chk "on: keyfile deleted, slot removed with the keyfile, initrd free of the key" "[ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ] && grep -q \"^cryptsetup -q luksRemoveKey /dev/disk/by-uuid/$UUID $FABOS_DU_KEYFILE\" $STUB_LOG && ! grep -q keyfiles $STUB_INITRD"
chk "on: order = update-initramfs, lsinitramfs (running kernel + every initrd on /boot), THEN luksRemoveKey (the record's listing afterwards)" "[ \"\$(grep -E '^(update-initramfs|lsinitramfs|cryptsetup)' $STUB_LOG | sed '/luksRemoveKey/q' | cut -d' ' -f1 | tr '\\n' '|')\" = 'lsinitramfs|update-initramfs|lsinitramfs|lsinitramfs|cryptsetup|' ]"
"$HELPER" status > "$T/st3.json"
chk "status after on: prompt_at_boot=true, no keyfile, consistent" "[ \"\$(jget $T/st3.json prompt_at_boot)\" = true ] && [ \"\$(jget $T/st3.json keyfile_present)\" = false ] && [ \"\$(jget $T/st3.json consistent)\" = true ]"
: > "$STUB_LOG"; "$HELPER" on > "$T/on2.json" 2>/dev/null; rc=$?
chk "on again is idempotent: exit 0, 'already', no cryptsetup / update-initramfs call" "[ $rc = 0 ] && grep -q already $T/on2.json && ! grep -q '^cryptsetup' $STUB_LOG && ! grep -q '^update-initramfs' $STUB_LOG"

# ---- 5. rollback: update-initramfs fails during off
reset_system; export STUB_FAIL_UPDATE=1
printf '%s\n' "$PASS" | "$HELPER" off > "$T/rb1.json" 2>/dev/null; rc=$?
chk "rollback (update-initramfs fails): exit 7, ok=false, prompt_at_boot=true, error names update-initramfs" "[ $rc = 7 ] && [ \"\$(jget $T/rb1.json ok)\" = false ] && [ \"\$(jget $T/rb1.json prompt_at_boot)\" = true ] && grep -q 'update-initramfs failed' $T/rb1.json"
chk "rollback: crypttab, conf-hook, initramfs.conf byte-identical to before" "cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && cmp -s $FABOS_DU_CONF_HOOK $T/conf-hook.orig && cmp -s $FABOS_DU_INITRAMFS_CONF $T/initramfs.conf.orig"
chk "rollback: key slot removed (luksRemoveKey with the keyfile), keyfile deleted, initramfs rebuilt again" "grep -q luksRemoveKey $STUB_LOG && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ] && [ \$(grep -c '^update-initramfs -u -k all' $STUB_LOG) = 2 ]"
chk "rollback: logged" "grep -q 'rolling back: update-initramfs failed' $FABOS_DU_LOG && grep -q 'configuration files restored' $FABOS_DU_LOG"
export STUB_FAIL_UPDATE=0

# ---- 6. rollback: the rebuilt initrd does not contain the key
reset_system; export STUB_HIDE_KEY=1
printf '%s\n' "$PASS" | "$HELPER" off > "$T/rb2.json" 2>/dev/null; rc=$?
chk "rollback (key missing from the initrd): exit 7, error names the initramfs, files restored, slot + keyfile gone" \
    "[ $rc = 7 ] && grep -q 'does not contain the unlock key' $T/rb2.json && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && cmp -s $FABOS_DU_CONF_HOOK $T/conf-hook.orig && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ] && grep -q luksRemoveKey $STUB_LOG"
export STUB_HIDE_KEY=0
"$HELPER" status > "$T/st4.json"
chk "status after the rollbacks: prompt_at_boot=true, consistent" "[ \"\$(jget $T/st4.json prompt_at_boot)\" = true ] && [ \"\$(jget $T/st4.json consistent)\" = true ]"

# ---- 7. on when luksRemoveKey fails: the prompt is back (config + initramfs), keyfile kept for a retry, exit 8
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; export STUB_FAIL_REMOVE=1; : > "$STUB_LOG"
"$HELPER" on > "$T/on3.json" 2>/dev/null; rc=$?
chk "on with luksRemoveKey failing: exit 8, ok=false, prompt_at_boot=true, crypttab restored, keyfile kept, initrd free of the key" \
    "[ $rc = 8 ] && [ \"\$(jget $T/on3.json ok)\" = false ] && [ \"\$(jget $T/on3.json prompt_at_boot)\" = true ] && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && [ -e $FABOS_DU_KEYFILE ] && ! grep -q keyfiles $STUB_INITRD"
export STUB_FAIL_REMOVE=0; "$HELPER" on > "$T/on4.json" 2>/dev/null; rc=$?
chk "on retried: exit 0, slot removed, keyfile deleted" "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ]"

# ---- 8. a key file this setting did not create is never touched
reset_system; printf '%s UUID=%s /etc/keys/other.key luks,discard\n' "$NAME" "$UUID" > "$FABOS_DU_CRYPTTAB"; cp "$FABOS_DU_CRYPTTAB" "$T/other.orig"
"$HELPER" on > "$T/other.json" 2>/dev/null; rc=$?
chk "on with a foreign key file in crypttab: exit 5, 'not touching', crypttab untouched" "[ $rc = 5 ] && grep -q 'not touching' $T/other.json && cmp -s $FABOS_DU_CRYPTTAB $T/other.orig"

# ---- 9. missing tools
reset_system; PATH=$T/nobin:/usr/bin:/bin printf '%s\n' "$PASS" | env PATH="$T/nobin:/usr/bin:/bin" "$HELPER" off > "$T/tools.json" 2>/dev/null; rc=$?
chk "off without cryptsetup/update-initramfs on PATH: exit 6, error names the missing tools, nothing changed" "[ $rc = 6 ] && grep -q 'missing tools' $T/tools.json && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig"

# ---- 10. no forbidden or user-facing 'download' wording in the helper's messages
chk "helper wording: no ChatGPT/OpenAI/GPT/download strings" "! grep -Eiq 'chatgpt|openai|gpt|download' $HELPER"

# ---- 11. the helper's assumptions about cryptsetup-initramfs hold on the real hook when this runs inside the image (skipped elsewhere):
#          KEYFILE_PATTERN from conf-hook, the key copied to /cryptroot/keyfiles/<name>.key, the UMASK warning the helper pre-empts
HOOK=/usr/share/initramfs-tools/hooks/cryptroot
if [ -r "$HOOK" ]; then
  chk "image hook: cryptroot reads KEYFILE_PATTERN, stores keys as cryptroot/keyfiles/<name>.key, warns on a permissive UMASK" \
      "grep -q 'KEYFILE_PATTERN' $HOOK && grep -q 'cryptroot/keyfiles/' $HOOK && grep -qi 'umask' $HOOK && grep -q 'cryptsetup-initramfs/conf-hook' $HOOK"
  REAL_CS="env PATH=/usr/sbin:/usr/bin:/sbin:/bin cryptsetup"      # the real binary, not the stub this test put on PATH
  chk "image: cryptsetup knows the exact actions/options the helper uses (luksAddKey, luksRemoveKey, --test-passphrase, --key-file)" \
      "$REAL_CS --help 2>&1 | grep -q luksAddKey && $REAL_CS --help 2>&1 | grep -q luksRemoveKey && $REAL_CS --help 2>&1 | grep -q -- '--test-passphrase' && $REAL_CS --help 2>&1 | grep -q -- '--key-file'"
else
  echo "SKIP  image hook assumptions ($HOOK not present here; run inside localhost/fabos:vm)"
fi

# ---- 12. the one rule: no initrd on /boot may name a key whose slot is gone (cryptsetup-initramfs tries a key file once, no prompt fallback)
# 12a. off's rollback after the initramfs was touched: configuration restored -> rebuilt -> proven free of the key -> THEN the slot removed
reset_system; export STUB_HIDE_KEY=1
printf '%s\n' "$PASS" | "$HELPER" off > "$T/ord.json" 2>/dev/null; rc=$?
chk "rollback order (initramfs touched): update-initramfs, lsinitramfs proof, THEN luksRemoveKey — never the slot first" \
    "[ $rc = 7 ] && [ \"\$(grep -E '^(update-initramfs|lsinitramfs|cryptsetup -q luksRemoveKey)' $STUB_LOG | cut -d' ' -f1 | tr '\\n' '|')\" = 'update-initramfs|lsinitramfs|update-initramfs|lsinitramfs|cryptsetup|' ]"
export STUB_HIDE_KEY=0
# 12b. the rollback's own rebuild fails while the initrd carries the key: the slot and the keyfile are KEPT (the computer still starts), exit 8,
#      prompt_at_boot=false is the truth, the configuration is back; a later `on` finishes the reversal in the safe order
reset_system; export STUB_FAIL_KEYTEST=1 STUB_FAIL_UPDATE_FROM=2
printf '%s\n' "$PASS" | "$HELPER" off > "$T/keep.json" 2>/dev/null; rc=$?
chk "rollback rebuild fails with the key in the initrd: exit 8, ok=false, prompt_at_boot=false, 'could not be fully reversed'" \
    "[ $rc = 8 ] && [ \"\$(jget $T/keep.json ok)\" = false ] && [ \"\$(jget $T/keep.json prompt_at_boot)\" = false ] && grep -q 'could not be fully reversed' $T/keep.json"
chk "rollback rebuild fails: configuration restored, keyfile AND slot kept, no luksRemoveKey, initrd still has the key, WARNING logged" \
    "cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && cmp -s $FABOS_DU_CONF_HOOK $T/conf-hook.orig && [ -e $FABOS_DU_KEYFILE ] && [ -e $FABOS_DU_KEYFILE.slot ] && ! grep -q luksRemoveKey $STUB_LOG && grep -q keyfiles $STUB_INITRD && grep -q 'WARNING: an initrd on .* still carries the key' $FABOS_DU_LOG"
"$HELPER" status > "$T/st5.json"
chk "status in that state: consistent=false (a leftover key while crypttab asks for the passphrase)" "[ \"\$(jget $T/st5.json consistent)\" = false ] && [ \"\$(jget $T/st5.json keyfile_present)\" = true ]"
export STUB_FAIL_KEYTEST=0 STUB_FAIL_UPDATE_FROM=0; : > "$STUB_LOG"
"$HELPER" on > "$T/keep-on.json" 2>/dev/null; rc=$?
chk "'on' afterwards finishes the reversal: exit 0, rebuilt + proven first, slot removed, keyfile deleted, initrd free" \
    "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ] && ! grep -q keyfiles $STUB_INITRD && [ \"\$(grep -E '^(update-initramfs|cryptsetup -q luksRemoveKey)' $STUB_LOG | sed '/luksRemoveKey/q' | cut -d' ' -f1 | tr '\\n' '|')\" = 'update-initramfs|cryptsetup|' ]"
# 12c. /boot listed in fstab but not mounted: nothing is touched in either direction
reset_system; export STUB_BOOT_UNMOUNTED=1
printf '%s\n' "$PASS" | "$HELPER" off > "$T/unm.json" 2>/dev/null; rc=$?
chk "off with /boot unmounted: exit 7, 'not mounted', prompt_at_boot=true, no cryptsetup / update-initramfs call, files identical" \
    "[ $rc = 7 ] && grep -q 'not mounted' $T/unm.json && [ \"\$(jget $T/unm.json prompt_at_boot)\" = true ] && ! grep -q '^cryptsetup' $STUB_LOG && ! grep -q '^update-initramfs' $STUB_LOG && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.orig && [ ! -e $FABOS_DU_KEYFILE ]"
export STUB_BOOT_UNMOUNTED=0; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; cp "$FABOS_DU_CRYPTTAB" "$T/crypttab.off"; export STUB_BOOT_UNMOUNTED=1; : > "$STUB_LOG"
"$HELPER" on > "$T/unm-on.json" 2>/dev/null; rc=$?
chk "on with /boot unmounted: exit 7, 'not mounted', prompt_at_boot=false, crypttab still names the key, slot + keyfile kept" \
    "[ $rc = 7 ] && grep -q 'not mounted' $T/unm-on.json && [ \"\$(jget $T/unm-on.json prompt_at_boot)\" = false ] && cmp -s $FABOS_DU_CRYPTTAB $T/crypttab.off && [ -e $FABOS_DU_KEYFILE ] && [ -e $FABOS_DU_KEYFILE.slot ] && ! grep -q '^update-initramfs' $STUB_LOG && ! grep -q luksRemoveKey $STUB_LOG"
export STUB_BOOT_UNMOUNTED=0
# 12d. on cannot find an initrd where the firmware boots from (not proven = not done): the slot and the keyfile stay, exit 8; a later on finishes
mkdir -p "$T/boot-empty"; : > "$STUB_LOG"
FABOS_DU_BOOT=$T/boot-empty "$HELPER" on > "$T/noinitrd.json" 2>/dev/null; rc=$?
chk "on with no initrd to prove: exit 8, ok=false, no luksRemoveKey, keyfile + slot kept" \
    "[ $rc = 8 ] && [ \"\$(jget $T/noinitrd.json ok)\" = false ] && ! grep -q luksRemoveKey $STUB_LOG && [ -e $FABOS_DU_KEYFILE ] && [ -e $FABOS_DU_KEYFILE.slot ]"
"$HELPER" on > "$T/noinitrd-on.json" 2>/dev/null; rc=$?
chk "on again with /boot in view: exit 0, slot removed, keyfile deleted" "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ]"
# 12e. one change at a time
reset_system
( flock 9; sleep 8 ) 9>>"$FABOS_DU_LOCK" & LOCKER=$!; sleep 0.5
"$HELPER" on > "$T/lock.json" 2>/dev/null; rc=$?
chk "a second run while the lock is held: exit 7, 'another change', nothing run" "[ $rc = 7 ] && grep -q 'another change' $T/lock.json && ! grep -q '^update-initramfs' $STUB_LOG"
wait $LOCKER 2>/dev/null
"$HELPER" on > "$T/lock2.json" 2>/dev/null; rc=$?
chk "after the lock is released the same run succeeds" "[ $rc = 0 ]"
# 12f. off over a leftover keyfile (crypttab names it, the initrd lacks it): its old slot is given back before the new key is added
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1
STUB_HIDE_KEY=1 update-initramfs -u -k all >/dev/null; : > "$STUB_LOG"
printf '%s\n' "$PASS" | "$HELPER" off > "$T/left.json" 2>/dev/null; rc=$?
chk "off over a leftover keyfile: exit 0, luksRemoveKey (old slot) BEFORE luksAddKey (new slot), the slot holds the new key" \
    "[ $rc = 0 ] && [ \"\$(grep -E '^cryptsetup -q luks(RemoveKey|AddKey)' $STUB_LOG | awk '{print \$3}' | tr '\\n' '|')\" = 'luksRemoveKey|luksAddKey|' ] && cmp -s $FABOS_DU_KEYFILE $FABOS_DU_KEYFILE.slot"
# 12g. copies nothing boots (dkms' .old-dkms, dpkg's .dpkg-bak, a .new) that still carry the key must not block `on`, with the version list
#      (/var/lib/initramfs-tools) and with the glob fallback alike; a second REGISTERED kernel whose initrd still has the key (a partial
#      `-k all`) must block the slot removal (exit 8) until that initrd is rebuilt or gone
cp "$STUB_INITRD" "$STUB_INITRD.old-dkms"; cp "$STUB_INITRD" "$STUB_INITRD.dpkg-bak"; cp "$STUB_INITRD" "$STUB_INITRD.new"
"$HELPER" on > "$T/stray.json" 2>/dev/null; rc=$?
chk "on with stray .old-dkms/.dpkg-bak/.new copies carrying the key (version list): exit 0, slot removed" "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE.slot ] && grep -q keyfiles $STUB_INITRD.old-dkms"
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; cp "$STUB_INITRD" "$STUB_INITRD.old-dkms"
FABOS_DU_IT_STATE=$T/absent "$HELPER" on > "$T/stray2.json" 2>/dev/null; rc=$?
chk "on with a stray .old-dkms copy (glob fallback, no version list): exit 0, slot removed" "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE.slot ]"
rm -f "$STUB_INITRD".old-dkms "$STUB_INITRD".dpkg-bak "$STUB_INITRD".new
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; cp "$STUB_INITRD" "$T/boot/initrd.img-6.0.0-1-other"; : > "$FABOS_DU_IT_STATE/6.0.0-1-other"; : > "$STUB_LOG"
"$HELPER" on > "$T/other.json" 2>/dev/null; rc=$?
chk "on while a second registered kernel's initrd still carries the key: exit 8, slot + keyfile kept, no luksRemoveKey" \
    "[ $rc = 8 ] && [ -e $FABOS_DU_KEYFILE ] && [ -e $FABOS_DU_KEYFILE.slot ] && ! grep -q luksRemoveKey $STUB_LOG"
rm -f "$T/boot/initrd.img-6.0.0-1-other" "$FABOS_DU_IT_STATE/6.0.0-1-other"
"$HELPER" on > "$T/other2.json" 2>/dev/null; rc=$?
chk "on once that initrd is gone: exit 0, slot removed, keyfile deleted" "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ]"

# ---- 13. diagnose (the REAL state, item by item, with a verdict) and repair (make the files match the switch again) — 1.0-8, after the
#          owner's "currently disabled, still sees it": every way the start-up files can disagree with the switch must be NAMED and repairable
jitem()   { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next((i["result"] for i in d["items"] if i["id"]==sys.argv[2]), "absent"))' "$1" "$2" 2>/dev/null; }
jdetail() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next((i["detail"] for i in d["items"] if i["id"]==sys.argv[2]), ""))' "$1" "$2" 2>/dev/null; }
jdiag()   { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["diagnosis"]; v=d[sys.argv[2]]; print(json.dumps(v) if not isinstance(v,str) else v)' "$1" "$2" 2>/dev/null; }
OTHER=6.0.0-1-other
# 13a. a fresh install (switch on): the verdict agrees, every item has a plain-English detail, the record is written (root run)
reset_system; "$HELPER" diagnose > "$T/dg1.json" 2>/dev/null; rc=$?
chk "diagnose (fresh install): exit 0, encrypted, switch=on, prompt_at_boot_expected=true, agrees=true, boot_risk=false, needs_root=false, as_root=true" \
    "[ $rc = 0 ] && [ \"\$(jget $T/dg1.json encrypted)\" = true ] && [ \"\$(jget $T/dg1.json switch)\" = on ] && [ \"\$(jget $T/dg1.json prompt_at_boot_expected)\" = true ] && [ \"\$(jget $T/dg1.json agrees)\" = true ] && [ \"\$(jget $T/dg1.json boot_risk)\" = false ] && [ \"\$(jget $T/dg1.json needs_root)\" = false ] && [ \"\$(jget $T/dg1.json as_root)\" = true ]"
chk "diagnose (fresh): items root_luks/crypttab_entry/boot_mounted/kernels/grub_default_initrd/efi_grub_chain/initrd_key/initrd_unlocker pass; crypttab_key/keyfile/key_slots/conf_hook_pattern/initramfs_umask/other_devices info" \
    "[ \"\$(jitem $T/dg1.json root_luks)\$(jitem $T/dg1.json crypttab_entry)\$(jitem $T/dg1.json boot_mounted)\$(jitem $T/dg1.json kernels)\$(jitem $T/dg1.json grub_default_initrd)\$(jitem $T/dg1.json efi_grub_chain)\$(jitem $T/dg1.json initrd_key:$KERNEL)\$(jitem $T/dg1.json initrd_unlocker)\" = passpasspasspasspasspasspasspass ] && [ \"\$(jitem $T/dg1.json crypttab_key)\$(jitem $T/dg1.json keyfile)\$(jitem $T/dg1.json key_slots)\$(jitem $T/dg1.json conf_hook_pattern)\$(jitem $T/dg1.json initramfs_umask)\$(jitem $T/dg1.json other_devices)\" = infoinfoinfoinfoinfoinfo ]"
chk "diagnose (fresh): GRUB's default entry resolved to the running kernel's initrd ('Fab OS', default=0); the booted initrd is named; every item has a detail" \
    "grep -q \"'Fab OS' -> initrd.img-$KERNEL (default=0)\" $T/dg1.json && [ \"\$(jget $T/dg1.json booted_initrd)\" = $STUB_INITRD ] && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"items\"] and all(i[\"detail\"] and i[\"label\"] and i[\"result\"] in (\"pass\",\"fail\",\"unknown\",\"info\") for i in d[\"items\"])' $T/dg1.json"
chk "diagnose (fresh): the record is written (initrd size/mtime/has_key=false, crypttab_key=none, verdict true)" \
    "[ -f $FABOS_DU_RECORD ] && python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); e=r[\"initrds\"][sys.argv[2]]; assert e[\"has_key\"] is False and e[\"size\"]>0 and r[\"crypttab_key\"]==\"none\" and r[\"prompt_at_boot_expected\"] is True' $FABOS_DU_RECORD $STUB_INITRD"
# 13b. after off: agrees (expected=false); the key items pass; the record says has_key=true
printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; "$HELPER" diagnose > "$T/dg2.json" 2>/dev/null; rc=$?
chk "diagnose after off: switch=off, expected=false, agrees=true; keyfile/key_slots(2)/keyfile_opens/conf_hook_pattern/initramfs_umask/crypttab_initramfs_opt/initrd_key pass" \
    "[ $rc = 0 ] && [ \"\$(jget $T/dg2.json switch)\" = off ] && [ \"\$(jget $T/dg2.json prompt_at_boot_expected)\" = false ] && [ \"\$(jget $T/dg2.json agrees)\" = true ] && [ \"\$(jitem $T/dg2.json keyfile)\$(jitem $T/dg2.json key_slots)\$(jitem $T/dg2.json keyfile_opens)\$(jitem $T/dg2.json conf_hook_pattern)\$(jitem $T/dg2.json initramfs_umask)\$(jitem $T/dg2.json crypttab_initramfs_opt)\$(jitem $T/dg2.json initrd_key:$KERNEL)\" = passpasspasspasspasspasspass ] && grep -q '2 (your passphrase + the start-up key)' $T/dg2.json && grep -q 'carries the unlock key' $T/dg2.json"
# 13c. the 'initramfs' option removed and the files rebuilt: the option item FAILS but the verdict stays honest (the key is still inside — Ubuntu
#      includes the root device on its own); repair restores the option and is idempotent otherwise
sed -i "s/,initramfs\$//" "$FABOS_DU_CRYPTTAB"; update-initramfs -u -k all >/dev/null; "$HELPER" diagnose > "$T/dg3.json" 2>/dev/null
chk "missing 'initramfs' option: item crypttab_initramfs_opt fail (detail says 'repair' restores it), verdict still agrees (the key is inside)" \
    "[ \"\$(jitem $T/dg3.json crypttab_initramfs_opt)\" = fail ] && grep -q \"'repair' restores it\" $T/dg3.json && [ \"\$(jget $T/dg3.json agrees)\" = true ] && [ \"\$(jget $T/dg3.json prompt_at_boot_expected)\" = false ]"
: > "$STUB_LOG"; printf '%s\n' "$PASS" | "$HELPER" repair > "$T/rp1.json" 2>/dev/null; rc=$?
chk "repair (option missing, verdict agreeing): exit 0 (agrees but an item failed -> the 'off' path re-applied), crypttab has 'initramfs' again, the embedded diagnosis agrees with every key item pass" \
    "[ $rc = 0 ] && [ \"\$(jget $T/rp1.json ok)\" = true ] && grep -q \"$FABOS_DU_KEYFILE luks,discard,initramfs\" $FABOS_DU_CRYPTTAB && [ \"\$(jdiag $T/rp1.json agrees)\" = true ] && python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))[\"diagnosis\"]; assert next(i[\"result\"] for i in d[\"items\"] if i[\"id\"]==\"crypttab_initramfs_opt\")==\"pass\"' $T/rp1.json"
# 13d. KEYFILE_PATTERN lost (a conf-hook overwritten by an upgrade) and the files rebuilt: cryptsetup-initramfs SKIPS the root target -> the
#      start-up file cannot start the computer: boot_risk, expected=null, agrees=false, the reason says so; repair puts everything back
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1
printf '# Configuration options for the cryptroot initramfs hook.\n#KEYFILE_PATTERN=\n' > "$FABOS_DU_CONF_HOOK"; update-initramfs -u -k all >/dev/null
"$HELPER" diagnose > "$T/dg4.json" 2>/dev/null; rc=$?
chk "pattern lost + rebuilt: boot_risk=true, expected=null, agrees=false, reason 'may not start', conf_hook_pattern fail, initrd_key fail, initrd_crypttab fail 'no entry'" \
    "[ $rc = 0 ] && [ \"\$(jget $T/dg4.json boot_risk)\" = true ] && [ \"\$(jget $T/dg4.json prompt_at_boot_expected)\" = null ] && [ \"\$(jget $T/dg4.json agrees)\" = false ] && grep -q 'may not start' $T/dg4.json && [ \"\$(jitem $T/dg4.json conf_hook_pattern)\" = fail ] && [ \"\$(jitem $T/dg4.json initrd_key:$KERNEL)\" = fail ] && [ \"\$(jitem $T/dg4.json initrd_crypttab:$KERNEL)\" = fail ] && grep -q 'no entry for' $T/dg4.json"
chk "pattern lost: the suggested repair names Fix now / fabos disk-unlock repair" "grep -q 'Fix now' $T/dg4.json && grep -q 'fabos disk-unlock repair' $T/dg4.json"
: > "$STUB_LOG"; printf '%s\n' "$PASS" | "$HELPER" repair > "$T/rp2.json" 2> "$T/rp2.err"; rc=$?
chk "repair (pattern lost): exit 0 ok, KEYFILE_PATTERN back, key inside the initrd, initrd crypttab names the key, diagnosis agrees, no boot risk; the old slot given back before the new one (luksRemoveKey then luksAddKey), update-initramfs ran, steps logged" \
    "[ $rc = 0 ] && [ \"\$(jget $T/rp2.json ok)\" = true ] && [ \"\$(jget $T/rp2.json prompt_at_boot)\" = false ] && grep -q \"^KEYFILE_PATTERN=\\\"$FABOS_DU_KEYFILE\\\"\" $FABOS_DU_CONF_HOOK && grep -q keyfiles $STUB_INITRD && grep -q '/cryptroot/keyfiles/' $STUB_INITRD.ct && [ \"\$(jdiag $T/rp2.json agrees)\" = true ] && [ \"\$(jdiag $T/rp2.json boot_risk)\" = false ] && [ \"\$(grep -E '^cryptsetup -q luks(RemoveKey|AddKey)' $STUB_LOG | awk '{print \$3}' | tr '\\n' '|')\" = 'luksRemoveKey|luksAddKey|' ] && grep -q '^update-initramfs -u -k all' $STUB_LOG && grep -q 'diagnose before repair' $FABOS_DU_LOG && grep -q 'DONE: the start-up files match the setting again' $FABOS_DU_LOG"
chk "repair: the passphrase reached cryptsetup on STDIN only and is in no log / JSON / stderr" "! grep -q 'PASSPHRASE ON ARGV' $STUB_LOG && ! grep -q -- $PASS $T/rp2.json $T/rp2.err $FABOS_DU_LOG $STUB_LOG"
# 13e. a stale start-up file: crypttab names the key but the file was built when it still asked (the switch flipped, the rebuild never happened) —
#      the owner's exact symptom: "disabled, still sees it"
reset_system; cp "$STUB_INITRD" "$T/initrd.asks"; cp "$STUB_INITRD.ct" "$T/initrd.asks.ct"; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1
cp "$T/initrd.asks" "$STUB_INITRD"; cp "$T/initrd.asks.ct" "$STUB_INITRD.ct"; "$HELPER" diagnose > "$T/dg5.json" 2>/dev/null
chk "stale start-up file (switch off, file still asks): expected=true, agrees=false, reason 'switch is off but the start-up files still ask' naming the initrd, initrd_key fail, initrd_crypttab 'asks for the passphrase', no boot risk" \
    "[ \"\$(jget $T/dg5.json switch)\" = off ] && [ \"\$(jget $T/dg5.json prompt_at_boot_expected)\" = true ] && [ \"\$(jget $T/dg5.json agrees)\" = false ] && grep -q 'the switch is off but the start-up files still ask for the password: initrd.img-' $T/dg5.json && [ \"\$(jitem $T/dg5.json initrd_key:$KERNEL)\" = fail ] && grep -q 'asks for the passphrase (key none)' $T/dg5.json && [ \"\$(jget $T/dg5.json boot_risk)\" = false ]"
printf '%s\n' "$PASS" | "$HELPER" repair > "$T/rp3.json" 2>/dev/null; rc=$?
chk "repair (stale file): exit 0, the file carries the key, diagnosis agrees (expected=false)" "[ $rc = 0 ] && grep -q keyfiles $STUB_INITRD && [ \"\$(jdiag $T/rp3.json agrees)\" = true ] && [ \"\$(jdiag $T/rp3.json prompt_at_boot_expected)\" = false ]"
# 13f. a second registered kernel whose start-up file lacks the key (a partial -k all): its item fails and the verdict disagrees although the
#      booted file is fine (the GRUB menu offers that kernel too); repair rebuilds every kernel
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1
cp "$T/initrd.asks" "$T/boot/initrd.img-$OTHER"; cp "$T/initrd.asks.ct" "$T/boot/initrd.img-$OTHER.ct"; : > "$FABOS_DU_IT_STATE/$OTHER"
"$HELPER" diagnose > "$T/dg6.json" 2>/dev/null
chk "second kernel without the key: initrd_key:$OTHER fail, initrd_key:$KERNEL pass, expected=false (the booted one), agrees=false, reason names kernel $OTHER" \
    "[ \"\$(jitem $T/dg6.json initrd_key:$OTHER)\" = fail ] && [ \"\$(jitem $T/dg6.json initrd_key:$KERNEL)\" = pass ] && [ \"\$(jget $T/dg6.json prompt_at_boot_expected)\" = false ] && [ \"\$(jget $T/dg6.json agrees)\" = false ] && grep -q \"start-up file for kernel $OTHER does not match\" $T/dg6.json"
export STUB_REBUILD_ALL=1; printf '%s\n' "$PASS" | "$HELPER" repair > "$T/rp4.json" 2>/dev/null; rc=$?; export STUB_REBUILD_ALL=0
chk "repair (second kernel): exit 0, both start-up files carry the key, diagnosis agrees" "[ $rc = 0 ] && grep -q keyfiles $STUB_INITRD && grep -q keyfiles $T/boot/initrd.img-$OTHER && [ \"\$(jdiag $T/rp4.json agrees)\" = true ]"
rm -f "$T/boot/initrd.img-$OTHER" "$T/boot/initrd.img-$OTHER.ct" "$FABOS_DU_IT_STATE/$OTHER"
# 13g. GRUB's default entry boots a start-up file update-initramfs does not maintain (left by a removed kernel): the item fails, the verdict
#      disagrees, repair runs update-grub; also: default=saved via grubenv (an id and a numeric path) resolves the right entry
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; cp "$T/initrd.asks" "$T/boot/initrd.img-6.0.0-1-old"
sed -i "s|initrd\t/initrd.img-$KERNEL|initrd\t/initrd.img-6.0.0-1-old|" "$FABOS_DU_GRUB_CFG"; "$HELPER" diagnose > "$T/dg7.json" 2>/dev/null
chk "GRUB default boots an unmaintained initrd: grub_default_initrd fail ('not one of the files update-initramfs maintains'), agrees=false, repair suggests update-grub" \
    "[ \"\$(jitem $T/dg7.json grub_default_initrd)\" = fail ] && grep -q 'not one of the files update-initramfs maintains' $T/dg7.json && [ \"\$(jget $T/dg7.json agrees)\" = false ] && grep -q 'update-grub' $T/dg7.json"
: > "$STUB_LOG"; printf '%s\n' "$PASS" | "$HELPER" repair > "$T/rp5.json" 2>/dev/null; rc=$?
chk "repair (GRUB stale): exit 0, update-grub ran, grub.cfg boots initrd.img-$KERNEL again, diagnosis agrees" "[ $rc = 0 ] && grep -q '^update-grub' $STUB_LOG && grep -q \"initrd.img-$KERNEL\" $FABOS_DU_GRUB_CFG && [ \"\$(jdiag $T/rp5.json agrees)\" = true ] && [ \"\$(jdiag $T/rp5.json grub_default_initrd)\" != fail ] 2>/dev/null || { [ $rc = 0 ] && grep -q '^update-grub' $STUB_LOG && [ \"\$(jdiag $T/rp5.json agrees)\" = true ]; }"
rm -f "$T/boot/initrd.img-6.0.0-1-old"
reset_system; sed -i 's/^set default="0"/set default="${saved_entry}"/' "$FABOS_DU_GRUB_CFG"; printf 'saved_entry=gnulinux-%s-advanced-%s\n' "$KERNEL" "$UUID" > "$FABOS_DU_GRUBENV"
"$HELPER" diagnose > "$T/dg8.json" 2>/dev/null
chk "GRUB default=saved with an entry id in grubenv: resolved to 'Fab OS, with Linux $KERNEL' -> the registered initrd, pass" "[ \"\$(jitem $T/dg8.json grub_default_initrd)\" = pass ] && grep -q \"'Fab OS, with Linux $KERNEL' -> initrd.img-$KERNEL (default=saved (grubenv saved_entry=gnulinux-\" $T/dg8.json"
printf 'saved_entry=1>1\n' > "$FABOS_DU_GRUBENV"; "$HELPER" diagnose > "$T/dg9.json" 2>/dev/null
chk "GRUB default=saved with a numeric path 1>1: the submenu's second entry (recovery) -> the registered initrd, pass" "[ \"\$(jitem $T/dg9.json grub_default_initrd)\" = pass ] && grep -q '(recovery mode)' $T/dg9.json"
sed -i "s|search.fs_uuid 2b7e1516-28ae-d2a6-abf7-158809cf4f3c|search.fs_uuid 00000000-0000-0000-0000-00000000dead|" "$T/boot/efi/EFI/fabos/grub.cfg"; "$HELPER" diagnose > "$T/dg10.json" 2>/dev/null
chk "EFI grub.cfg pointing at another filesystem: efi_grub_chain fail ('ANOTHER system')" "[ \"\$(jitem $T/dg10.json efi_grub_chain)\" = fail ] && grep -q 'ANOTHER system' $T/dg10.json"
# 13h. a root entry unlocked by a keyscript: not this setting's; agrees=null with the reason; repair refuses (exit 5) and touches nothing
reset_system; printf '%s UUID=%s /dev/urandom luks,keyscript=/lib/cryptsetup/scripts/decrypt_derived\n' "$NAME" "$UUID" > "$FABOS_DU_CRYPTTAB"; cp "$FABOS_DU_CRYPTTAB" "$T/ks.orig"
"$HELPER" diagnose > "$T/dg11.json" 2>/dev/null; rc=$?
chk "keyscript root entry: exit 0, switch=keyscript, agrees=null, reason names the keyscript, crypttab_key info 'Unlocked by a keyscript'" \
    "[ $rc = 0 ] && [ \"\$(jget $T/dg11.json switch)\" = keyscript ] && [ \"\$(jget $T/dg11.json agrees)\" = null ] && grep -q 'keyscript=/lib/cryptsetup/scripts/decrypt_derived' $T/dg11.json && grep -q 'Unlocked by a keyscript' $T/dg11.json"
: > "$STUB_LOG"; printf '%s\n' "$PASS" | "$HELPER" repair > "$T/rp6.json" 2>/dev/null; rc=$?
chk "repair on a keyscript entry: exit 5 'not touching', crypttab untouched, no changing cryptsetup call (luksDump only) / no update-initramfs, diagnosis embedded" "[ $rc = 5 ] && grep -q 'not touching' $T/rp6.json && cmp -s $FABOS_DU_CRYPTTAB $T/ks.orig && ! grep -q '^cryptsetup -q' $STUB_LOG && ! grep -q '^update-initramfs' $STUB_LOG && grep -q '\"diagnosis\"' $T/rp6.json"
# 13i. other encrypted devices: an encrypted swap with NO key asks for its own password at start-up (looks exactly like the disk prompt) ->
#      counts as a prompt even with the root switch off; a swap keyed from /dev/urandom does not
reset_system; printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; printf 'swap_crypt UUID=aaaa-bbbb none swap\n' >> "$FABOS_DU_CRYPTTAB"; "$HELPER" diagnose > "$T/dg12.json" 2>/dev/null
chk "encrypted swap without a key: other_device:swap_crypt fail ('ITS OWN password'), expected=true although root is off, agrees=false, reason names swap_crypt, repair points at the device not the switch" \
    "[ \"\$(jitem $T/dg12.json other_device:swap_crypt)\" = fail ] && grep -q 'ITS OWN password' $T/dg12.json && [ \"\$(jget $T/dg12.json prompt_at_boot_expected)\" = true ] && [ \"\$(jget $T/dg12.json agrees)\" = false ] && grep -q 'another encrypted device ( swap_crypt )' $T/dg12.json && grep -q 'give that device a key' $T/dg12.json"
sed -i 's|^swap_crypt .*|swap_crypt UUID=aaaa-bbbb /dev/urandom swap,cipher=aes-xts-plain64,size=256|' "$FABOS_DU_CRYPTTAB"; "$HELPER" diagnose > "$T/dg13.json" 2>/dev/null
chk "encrypted swap keyed from /dev/urandom: other_device pass ('no prompt'), expected=false, agrees=true" "[ \"\$(jitem $T/dg13.json other_device:swap_crypt)\" = pass ] && grep -q 'unlocked with key /dev/urandom; no prompt' $T/dg13.json && [ \"\$(jget $T/dg13.json prompt_at_boot_expected)\" = false ] && [ \"\$(jget $T/dg13.json agrees)\" = true ]"
# 13j. an unencrypted computer: no disk password at all, and the item says the start-up password one sees is the LOGIN screen's
reset_system; STUB_ROOT_SRC=/dev/vda2 "$HELPER" diagnose > "$T/dg14.json" 2>/dev/null; rc=$?
chk "diagnose (unencrypted): exit 0, encrypted=false, switch=none, expected=false, agrees=null, one info item mentioning the LOGIN screen" \
    "[ $rc = 0 ] && [ \"\$(jget $T/dg14.json encrypted)\" = false ] && [ \"\$(jget $T/dg14.json switch)\" = none ] && [ \"\$(jget $T/dg14.json prompt_at_boot_expected)\" = false ] && [ \"\$(jget $T/dg14.json agrees)\" = null ] && grep -q 'LOGIN screen' $T/dg14.json && [ \"\$(jitem $T/dg14.json root_luks)\" = info ]"
# 13k. as the USER (no root): a root-only initrd is 'unknown' and the verdict says it needs administrator rights — unless the record a root run
#      left still matches the file, in which case the verified answer is used and says so; a rebuilt (changed) file is unknown again
if [ "$(id -u)" = 0 ]; then echo "SKIP  user-level diagnose on a root-only initrd (this test runs as root here)"; else
reset_system; rm -f "$FABOS_DU_RECORD"; chmod 000 "$STUB_INITRD"
env -u FABOS_DU_ALLOW_NONROOT "$HELPER" diagnose > "$T/dg15.json" 2>/dev/null; rc=$?
chk "user-level diagnose, root-only initrd, no record: exit 0, as_root=false, initrd_key unknown, expected=null, agrees=null, needs_root=true, repair says 'Check as administrator' / 'diagnose --admin'" \
    "[ $rc = 0 ] && [ \"\$(jget $T/dg15.json as_root)\" = false ] && [ \"\$(jitem $T/dg15.json initrd_key:$KERNEL)\" = unknown ] && [ \"\$(jget $T/dg15.json prompt_at_boot_expected)\" = null ] && [ \"\$(jget $T/dg15.json agrees)\" = null ] && [ \"\$(jget $T/dg15.json needs_root)\" = true ] && grep -q 'Check as administrator' $T/dg15.json && grep -q 'diagnose --admin' $T/dg15.json"
chmod 644 "$STUB_INITRD"; "$HELPER" diagnose >/dev/null 2>&1; chmod 000 "$STUB_INITRD"
env -u FABOS_DU_ALLOW_NONROOT "$HELPER" diagnose > "$T/dg16.json" 2>/dev/null
chk "user-level diagnose after a root run left the record: initrd_key pass 'verified as administrator at … unchanged since', expected=true, agrees=true, needs_root=false" \
    "[ \"\$(jitem $T/dg16.json initrd_key:$KERNEL)\" = pass ] && grep -q 'verified as administrator at' $T/dg16.json && grep -q 'unchanged since' $T/dg16.json && [ \"\$(jget $T/dg16.json prompt_at_boot_expected)\" = true ] && [ \"\$(jget $T/dg16.json agrees)\" = true ] && [ \"\$(jget $T/dg16.json needs_root)\" = false ]"
chmod 644 "$STUB_INITRD"; echo "rebuilt" >> "$STUB_INITRD"; touch -d '2 minutes ago' "$STUB_INITRD"; chmod 000 "$STUB_INITRD"
env -u FABOS_DU_ALLOW_NONROOT "$HELPER" diagnose > "$T/dg17.json" 2>/dev/null
chk "user-level diagnose after the file changed: unknown again ('has changed since it was last verified'), needs_root=true" "[ \"\$(jitem $T/dg17.json initrd_key:$KERNEL)\" = unknown ] && grep -q 'has changed since it was last verified' $T/dg17.json && [ \"\$(jget $T/dg17.json needs_root)\" = true ]"
chmod 644 "$STUB_INITRD"
chk "repair as non-root: exit 4, nothing run" "printf '%s\n' $PASS | env -u FABOS_DU_ALLOW_NONROOT $HELPER repair >/dev/null 2>&1; [ \$? = 4 ]"
fi
# 13l. repair with nothing to repair runs no root step; repair while off without the passphrase refuses (exit 3); /boot unmounted is a fail item
reset_system; : > "$STUB_LOG"; "$HELPER" repair > "$T/rp7.json" 2>/dev/null < /dev/null; rc=$?
chk "repair with nothing to repair (switch on, files agree): exit 0 ok 'nothing to repair', no cryptsetup / update-initramfs / update-grub call, diagnosis embedded" \
    "[ $rc = 0 ] && grep -q 'nothing to repair' $T/rp7.json && ! grep -q '^cryptsetup -q\\|^update-initramfs\\|^update-grub' $STUB_LOG && [ \"\$(jdiag $T/rp7.json agrees)\" = true ]"
printf '%s\n' "$PASS" | "$HELPER" off >/dev/null 2>&1; cp "$T/initrd.asks" "$STUB_INITRD"; cp "$T/initrd.asks.ct" "$STUB_INITRD.ct"; : > "$STUB_LOG"
printf '\n' | "$HELPER" repair > "$T/rp8.json" 2>/dev/null; rc=$?
chk "repair while off without the passphrase: exit 3 'passphrase is needed', nothing changed, diagnosis embedded" "[ $rc = 3 ] && grep -q 'passphrase is needed' $T/rp8.json && ! grep -q '^cryptsetup -q' $STUB_LOG && ! grep -q '^update-initramfs' $STUB_LOG && grep -q '\"diagnosis\"' $T/rp8.json"
export STUB_BOOT_UNMOUNTED=1; "$HELPER" diagnose > "$T/dg18.json" 2>/dev/null; export STUB_BOOT_UNMOUNTED=0
chk "diagnose with /boot unmounted: boot_mounted fail ('NOT mounted')" "[ \"\$(jitem $T/dg18.json boot_mounted)\" = fail ] && grep -q 'NOT mounted' $T/dg18.json"
# 13m. the user-facing HOWTO ships inside the package identical to the repository copy; wording rules hold for it and the helper
chk "docs/HOWTO-disk-password.md is shipped as usr/share/doc/fabos-agent/HOWTO-disk-password.md (identical)" "cmp -s $ROOT/docs/HOWTO-disk-password.md $ROOT/packages/fabos-agent/usr/share/doc/fabos-agent/HOWTO-disk-password.md"
chk "HOWTO wording: names the switch location, 1.0-7, Fix now, the login screen; no ChatGPT/OpenAI/GPT/SnowUI/download" \
    "grep -q 'Settings → General → Start-up' $ROOT/docs/HOWTO-disk-password.md && grep -q '1.0-7' $ROOT/docs/HOWTO-disk-password.md && grep -q 'Fix now' $ROOT/docs/HOWTO-disk-password.md && grep -qi 'login' $ROOT/docs/HOWTO-disk-password.md && ! grep -Eiq 'chatgpt|openai|gpt|snowui|download' $ROOT/docs/HOWTO-disk-password.md"

echo "### disk-unlock-test: $PASS_N passed, $FAIL_N failed"
[ $FAIL_N = 0 ]
