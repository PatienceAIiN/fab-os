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
       FABOS_DU_FSTAB=$T/etc/fstab FABOS_DU_LOCK=$T/disk-unlock.lock FABOS_DU_IT_STATE=$T/var/lib/initramfs-tools
export STUB_LOG=$T/calls.log STUB_STDIN=$T/stdin.log STUB_PASS=$PASS STUB_INITRD=$T/boot/initrd.img-$KERNEL STUB_KEYFILE=$FABOS_DU_KEYFILE STUB_NAME=$NAME
export STUB_ROOT_SRC=/dev/mapper/$NAME STUB_FAIL_UPDATE=0 STUB_HIDE_KEY=0 STUB_FAIL_REMOVE=0 STUB_FAIL_UPDATE_FROM=0 STUB_FAIL_KEYTEST=0 STUB_BOOT_UNMOUNTED=0 STUB_UI_COUNT=$T/ui.count
# the installed layout (ADR-0021): /boot is its own partition, listed in fstab — the helper must see it mounted; one kernel version
# registered with initramfs-tools (what `update-initramfs -k all` rebuilds and GRUB boots)
printf 'UUID=root / ext4 defaults 0 1\nUUID=boot %s ext4 defaults 0 2\nUUID=esp /boot/efi vfat umask=0077 0 1\n' "$FABOS_DU_BOOT" > "$FABOS_DU_FSTAB"
mkdir -p "$FABOS_DU_IT_STATE" && : > "$FABOS_DU_IT_STATE/$KERNEL"

# ---- stubs (record argv; cryptsetup also records what it read on stdin and checks the passphrase)
cat > "$T/bin/findmnt" <<'EOF'
#!/bin/sh
echo "findmnt $*" >> "$STUB_LOG"
case " $* " in *" $FABOS_DU_BOOT "*) [ "${STUB_BOOT_UNMOUNTED:-0}" = 1 ] && exit 1; echo "$FABOS_DU_BOOT"; exit 0;; esac
echo "$STUB_ROOT_SRC"
EOF
cat > "$T/bin/cryptsetup" <<'EOF'
#!/bin/bash
echo "cryptsetup $*" >> "$STUB_LOG"
for a in "$@"; do [ "$a" = "$STUB_PASS" ] && { echo "PASSPHRASE ON ARGV" >> "$STUB_LOG"; exit 99; }; done
case " $* " in
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
{ echo "."; echo "init"; echo "cryptroot"; echo "cryptroot/crypttab"; } > "$STUB_INITRD"
key=$(awk -v n="$STUB_NAME" '$1==n {print $3}' "$FABOS_DU_CRYPTTAB")
pat=$(sed -n 's/^KEYFILE_PATTERN="\([^"]*\)".*/\1/p' "$FABOS_DU_CONF_HOOK" 2>/dev/null | tail -1)
if [ -n "$key" ] && [ "$key" != none ] && [ -f "$key" ] && [ "$key" = "$pat" ] && [ "$STUB_HIDE_KEY" != 1 ]; then echo "cryptroot/keyfiles"; echo "cryptroot/keyfiles/$STUB_NAME.key"; fi >> "$STUB_INITRD"
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
  rm -f "$FABOS_DU_KEYFILE" "$FABOS_DU_KEYFILE.slot" "$STUB_LOG" "$STUB_STDIN"; : > "$STUB_LOG"; : > "$STUB_STDIN"
  STUB_FAIL_UPDATE=0 STUB_HIDE_KEY=0 STUB_FAIL_REMOVE=0 STUB_FAIL_UPDATE_FROM=0 STUB_FAIL_KEYTEST=0 STUB_BOOT_UNMOUNTED=0
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
chk "on: order = update-initramfs, lsinitramfs (running kernel + every initrd on /boot), THEN luksRemoveKey" "[ \"\$(grep -E '^(update-initramfs|lsinitramfs|cryptsetup)' $STUB_LOG | cut -d' ' -f1 | tr '\\n' '|')\" = 'lsinitramfs|update-initramfs|lsinitramfs|lsinitramfs|cryptsetup|' ]"
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
    "[ $rc = 0 ] && [ ! -e $FABOS_DU_KEYFILE ] && [ ! -e $FABOS_DU_KEYFILE.slot ] && ! grep -q keyfiles $STUB_INITRD && [ \"\$(grep -E '^(update-initramfs|cryptsetup -q luksRemoveKey)' $STUB_LOG | cut -d' ' -f1 | tr '\\n' '|')\" = 'update-initramfs|cryptsetup|' ]"
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

echo "### disk-unlock-test: $PASS_N passed, $FAIL_N failed"
[ $FAIL_N = 0 ]
