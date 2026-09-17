#!/bin/bash
# @DISTRO_NAME@ disk_unlock.sh — the root helper behind the Start-up setting "Ask for the disk password when the computer starts".
#
#   disk_unlock.sh status        JSON: {encrypted, device, source, prompt_at_boot, keyfile_present, keyfile_in_initramfs, consistent, detail}
#   disk_unlock.sh off  < pass   stop asking: the current passphrase is read from STDIN (one line, never an argument)
#   disk_unlock.sh on            ask again (needs nothing)
#   disk_unlock.sh diagnose      JSON: the REAL state item by item (what GRUB boots, what that initrd carries and asks) and a verdict
#                                {switch, prompt_at_boot_expected, agrees, boot_risk, reason, repair, items[]} — works as the user
#                                (root-only items read "unknown", or come from the record the last root run left) or as root (complete)
#   disk_unlock.sh repair < pass make the start-up files match the switch again: diagnose, re-run 'off' (passphrase on STDIN) or 'on'
#                                idempotently, update-grub when the menu is stale, diagnose again (embedded as "diagnosis")
#
# Layout (ADR-0021 installer boot layout): ESP + unencrypted /boot (ext4) + LUKS2 root. The initramfs on /boot unlocks the
# root; cryptsetup-initramfs asks for the passphrase through Plymouth. "off" makes the initramfs carry a key instead:
#   1. a 4096-byte random keyfile /etc/fabos/luks-unlock.key (0400 root) — /etc is INSIDE the encrypted root, so this copy
#      is protected; the copy the cryptroot hook embeds in the initramfs on the unencrypted /boot is what unlocks the disk,
#      which is exactly why the setting warns that anyone who starts the computer can use it;
#   2. `cryptsetup luksAddKey <root device> <keyfile>` (the passphrase authorises it, on cryptsetup's stdin);
#   3. /etc/crypttab: key column -> the keyfile, `initramfs` added to the options (luks,discard kept);
#   4. /etc/cryptsetup-initramfs/conf-hook: KEYFILE_PATTERN="/etc/fabos/luks-unlock.key" (the hook copies matching keys
#      into /cryptroot/keyfiles/<name>.key and points the initramfs crypttab at that copy);
#   5. /etc/initramfs-tools/initramfs.conf: UMASK=0077 (the initrd now holds key material: root-only);
#   6. update-initramfs -u -k all; then PROVE it: lsinitramfs lists cryptroot/keyfiles/<name>.key in the running kernel's
#      initrd, and `cryptsetup open --test-passphrase --key-file <keyfile>` opens the slot. Any failure rolls everything back
#      (config files restored from backups, the slot removed, the keyfile deleted, initramfs rebuilt) — the disk never ends up
#      half-configured.
# THE ONE RULE both directions obey: an initrd on /boot must never name a key whose LUKS slot is gone. cryptsetup-initramfs
# tries a key file exactly once and does NOT fall back to the passphrase prompt (scripts/local-top/cryptroot: tries=1, then
# "maximum number of tries exceeded" and the initramfs shell), so the slot may only be removed AFTER every initrd on /boot has
# been rebuilt and proven free of the key. "on" does that in order: config restored (key -> none, pattern dropped) -> initramfs
# rebuilt -> proven free of the keyfile -> only then luksRemoveKey with the keyfile and the keyfile deleted. The rollback of "off"
# follows the same order once the initramfs was touched; when the rebuild fails and an initrd still carries the key, the slot
# and the keyfile are KEPT (the computer still starts) and exit 8 says so — "on" finishes the reversal later. UMASK=0077 is
# kept after "on" (harmless, stricter). Both directions refuse to run while /boot is listed in fstab but not mounted (the
# rebuilt initrd would land on the root filesystem, not where the firmware boots from) and serialise themselves with a lock.
# Every step is logged to /var/log/fabos/disk-unlock.log (root:adm 0640, the rootexec audit directory) and syslog authpriv
# (tag fabos-disk-unlock). The passphrase is never logged, never on a command line, never in a file.
#
# Exit codes: 0 ok · 2 usage / input · 3 the passphrase was not accepted · 4 not root · 5 not encrypted / no root crypt entry ·
# 6 a tool is missing · 7 the operation failed and was rolled back (or refused before anything changed) · 8 the operation
# failed and could NOT be fully reversed (the JSON says what is left to do). The last stdout line is always one JSON object
# {ok, action, prompt_at_boot, error?, detail?}.
#
# Test seams (tests/disk-unlock-test.sh runs this with stub cryptsetup/update-initramfs/lsinitramfs/findmnt on PATH):
#   FABOS_DU_CRYPTTAB FABOS_DU_KEYFILE FABOS_DU_CONF_HOOK FABOS_DU_INITRAMFS_CONF FABOS_DU_BOOT FABOS_DU_FSTAB FABOS_DU_LOCK
#   FABOS_DU_IT_STATE FABOS_DU_LOG FABOS_DU_ALLOW_NONROOT=1 · diagnose: FABOS_DU_GRUB_CFG FABOS_DU_GRUBENV FABOS_DU_EFI_DIR FABOS_DU_RECORD
set -u
umask 077

CRYPTTAB=${FABOS_DU_CRYPTTAB:-/etc/crypttab}
KEYFILE=${FABOS_DU_KEYFILE:-/etc/fabos/luks-unlock.key}
CONF_HOOK=${FABOS_DU_CONF_HOOK:-/etc/cryptsetup-initramfs/conf-hook}
INITRAMFS_CONF=${FABOS_DU_INITRAMFS_CONF:-/etc/initramfs-tools/initramfs.conf}
BOOT=${FABOS_DU_BOOT:-/boot}
FSTAB=${FABOS_DU_FSTAB:-/etc/fstab}
LOCK=${FABOS_DU_LOCK:-/run/lock/fabos-disk-unlock.lock}
LOG=${FABOS_DU_LOG:-/var/log/fabos/disk-unlock.log}
ACTION=${1:-}
IS_ROOT=0; [ "$(id -u)" = 0 ] && IS_ROOT=1
[ "${FABOS_DU_ALLOW_NONROOT:-0}" = 1 ] && IS_ROOT=1
STEPS=()

log() {   # to the audit log (root only; the directory is created by fabos-agent's postinst, root:adm 0750) and syslog
  local line="$1"
  STEPS+=("$line")
  echo "disk_unlock: $line" >&2
  if [ "$(id -u)" = 0 ] || [ -n "${FABOS_DU_LOG:-}" ]; then
    { mkdir -p "$(dirname "$LOG")" 2>/dev/null; ( umask 027; printf '%s %s uid=%s %s\n' "$(date -u +%FT%TZ)" "$ACTION" "${FABOS_ROOTEXEC_UID:-$(id -u)}" "$line" >> "$LOG" ); chgrp adm "$LOG"; } 2>/dev/null || true
  fi
  command -v logger >/dev/null 2>&1 && logger -t fabos-disk-unlock -p authpriv.notice -- "$ACTION: $line" 2>/dev/null || true
}

json_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1" 2>/dev/null || printf '"%s"' "$(printf '%s' "$1" | sed 's/["\\]/\\&/g')"; }

emit() {   # emit <ok true|false> <prompt_at_boot true|false|null> <error-or-empty> <detail-or-empty>
  local steps="" s
  for s in "${STEPS[@]+"${STEPS[@]}"}"; do steps="$steps${steps:+, }$(json_str "$s")"; done
  printf '{"ok": %s, "action": %s, "prompt_at_boot": %s, "error": %s, "detail": %s, "steps": [%s]%s}\n' \
    "$1" "$(json_str "$ACTION")" "$2" "$( [ -n "$3" ] && json_str "$3" || echo null )" "$( [ -n "$4" ] && json_str "$4" || echo null )" "$steps" "${EXTRA_JSON:+, $EXTRA_JSON}"
}

die() {   # die <exit code> <prompt_at_boot> <message> [detail]
  log "FAILED: $3"
  emit false "$2" "$3" "${4:-}"
  exit "$1"
}

need_tools() {
  local t missing=""
  for t in "$@"; do command -v "$t" >/dev/null 2>&1 || missing="$missing $t"; done
  [ -z "$missing" ] || die 6 null "missing tools:$missing (cryptsetup-initramfs and initramfs-tools are needed)"
}

# ---- locate the root crypt device: findmnt SOURCE / -> /dev/mapper/<name> -> the /etc/crypttab entry with that name
ROOT_SRC=""; MAPPER=""; CT_NAME=""; CT_SRC=""; CT_KEY=""; CT_OPTS=""
find_root_entry() {
  ROOT_SRC=$(findmnt -n -o SOURCE / 2>/dev/null | head -1)
  ROOT_SRC=${ROOT_SRC%%\[*}                       # btrfs: /dev/mapper/x[/@] -> /dev/mapper/x
  case "$ROOT_SRC" in /dev/mapper/*) MAPPER=${ROOT_SRC#/dev/mapper/};; *) MAPPER="";; esac
  [ -n "$MAPPER" ] || return 1
  [ -r "$CRYPTTAB" ] || return 1
  local name src key opts
  while read -r name src key opts; do
    case "$name" in ""|\#*) continue;; esac
    if [ "$name" = "$MAPPER" ]; then CT_NAME=$name; CT_SRC=$src; CT_KEY=${key:-none}; CT_OPTS=${opts:-}; return 0; fi
  done < "$CRYPTTAB"
  return 1
}

# the block device for cryptsetup: UUID=x -> /dev/disk/by-uuid/x (PARTUUID/LABEL likewise), a path stays a path
crypt_device() {
  case "$CT_SRC" in
    UUID=*) echo "/dev/disk/by-uuid/${CT_SRC#UUID=}";;
    PARTUUID=*) echo "/dev/disk/by-partuuid/${CT_SRC#PARTUUID=}";;
    LABEL=*) echo "/dev/disk/by-label/${CT_SRC#LABEL=}";;
    *) echo "$CT_SRC";;
  esac
}

has_opt() { case ",$1," in *",$2,"*) return 0;; *) return 1;; esac; }
add_opt() { if [ -z "$1" ]; then echo "$2"; elif has_opt "$1" "$2"; then echo "$1"; else echo "$1,$2"; fi; }
del_opt() { echo ",$1," | sed "s/,$2,/,/g; s/^,//; s/,\$//"; }

# rewrite the root entry's key column and options in place (other lines untouched, comments kept); atomic
write_crypttab() {   # write_crypttab <key> <opts>
  local tmp; tmp=$(mktemp "$CRYPTTAB.XXXXXX") || return 1
  awk -v name="$CT_NAME" -v key="$1" -v opts="$2" '
    $0 ~ /^[[:space:]]*(#|$)/ { print; next }
    $1 == name { printf "%s %s %s", $1, $2, key; if (opts != "") printf " %s", opts; printf "\n"; next }
    { print }' "$CRYPTTAB" > "$tmp" || { rm -f "$tmp"; return 1; }
  chmod 0644 "$tmp" && mv -f "$tmp" "$CRYPTTAB"
}

# KEYFILE_PATTERN in conf-hook: set to our keyfile / drop our line (a pattern someone else set is left alone)
set_pattern() {
  mkdir -p "$(dirname "$CONF_HOOK")" || return 1
  local tmp; tmp=$(mktemp "$CONF_HOOK.XXXXXX") || return 1
  { [ -f "$CONF_HOOK" ] && grep -v -E '^[[:space:]]*KEYFILE_PATTERN=' "$CONF_HOOK"; printf 'KEYFILE_PATTERN="%s"   # %s: the start-up unlock key (disk_unlock.sh)\n' "$KEYFILE" "@DISTRO_NAME@"; } > "$tmp"
  chmod 0644 "$tmp" && mv -f "$tmp" "$CONF_HOOK"
}
drop_pattern() {
  [ -f "$CONF_HOOK" ] || return 0
  local tmp; tmp=$(mktemp "$CONF_HOOK.XXXXXX") || return 1
  grep -v -F "KEYFILE_PATTERN=\"$KEYFILE\"" "$CONF_HOOK" > "$tmp"
  chmod 0644 "$tmp" && mv -f "$tmp" "$CONF_HOOK"
}
set_umask() {
  local tmp; tmp=$(mktemp "$INITRAMFS_CONF.XXXXXX") || return 1
  if [ -f "$INITRAMFS_CONF" ] && grep -q -E '^[[:space:]]*UMASK=' "$INITRAMFS_CONF"; then
    sed -E 's/^[[:space:]]*UMASK=.*/UMASK=0077/' "$INITRAMFS_CONF" > "$tmp"
  else
    { [ -f "$INITRAMFS_CONF" ] && cat "$INITRAMFS_CONF"; printf '\n# %s: the initramfs carries the start-up unlock key (disk_unlock.sh): root-only images\nUMASK=0077\n' "@DISTRO_NAME@"; } > "$tmp"
  fi
  chmod 0644 "$tmp" && mv -f "$tmp" "$INITRAMFS_CONF"
}

# the initrd(s) to prove: the running kernel's, else the newest present
initrds() {
  local cur="$BOOT/initrd.img-$(uname -r 2>/dev/null)"
  if [ -f "$cur" ]; then echo "$cur"; return; fi
  ls -1 "$BOOT"/initrd.img-* 2>/dev/null | sort -V | tail -1
}
keyfile_in_initramfs() {   # 0 = present, 1 = absent, 2 = unknown (no initrd / unreadable)
  local i; i=$(initrds)
  [ -n "$i" ] && [ -r "$i" ] || return 2
  lsinitramfs "$i" 2>/dev/null | grep -q -x -E "/?cryptroot/keyfiles/${CT_NAME}\.key" && return 0
  return 1
}
# the initrds that matter: one per kernel version update-initramfs manages (/var/lib/initramfs-tools/<version> — what `-k all`
# rebuilds and what GRUB boots), else every initrd.img-* on $BOOT minus the copies nothing boots (.new, .dpkg-bak, dkms' .old-dkms)
IT_STATE=${FABOS_DU_IT_STATE:-/var/lib/initramfs-tools}
initrd_list() {
  local v i n=0
  if [ -d "$IT_STATE" ]; then
    for v in "$IT_STATE"/*; do [ -f "$v" ] || continue; n=$((n+1)); echo "$BOOT/initrd.img-${v##*/}"; done
  fi
  [ "$n" -gt 0 ] && return 0
  for i in "$BOOT"/initrd.img-*; do case "$i" in *.new|*.dpkg-bak|*.bak|*.old-dkms) continue;; *) [ -f "$i" ] && echo "$i";; esac; done
  return 0
}
# EVERY initrd that matters (a partial `-k all` may have rebuilt some). 0 = at least one still carries the key, or one cannot be
# listed, or there is none to look at — i.e. NOT proven free; 1 = every one of them is free of it.
any_initrd_has_key() {
  local i n=0 listing
  while IFS= read -r i; do
    [ -n "$i" ] && [ -f "$i" ] || continue
    n=$((n+1))
    listing=$(lsinitramfs "$i" 2>/dev/null) || return 0
    printf '%s\n' "$listing" | grep -q -x -E "/?cryptroot/keyfiles/${CT_NAME}\.key" && return 0
  done <<EOF
$(initrd_list)
EOF
  [ "$n" -gt 0 ] || return 0
  return 1
}
# $BOOT listed in fstab must be mounted: with it unmounted update-initramfs writes an initrd into the root filesystem that the
# firmware never boots, while the real one on the /boot partition keeps whatever it had
boot_mounted() {
  [ -r "$FSTAB" ] || return 0
  grep -q -E "^[[:space:]]*[^#[:space:]]+[[:space:]]+${BOOT}/?[[:space:]]" "$FSTAB" || return 0
  findmnt -n "$BOOT" >/dev/null 2>&1
}
cur_prompt() { if [ "$CT_KEY" = none ] || [ -z "$CT_KEY" ]; then echo true; else echo false; fi; }
# one change at a time (two helpers editing crypttab and adding slots together would leave an orphan slot)
take_lock() {
  command -v flock >/dev/null 2>&1 || { log "flock is not available; running without the lock"; return 0; }
  if ! exec 9>>"$LOCK" 2>/dev/null; then log "cannot open $LOCK; running without the lock"; return 0; fi
  flock -w 5 9 || die 7 "$(cur_prompt)" "another change of the start-up setting is still running; try again in a minute" "nothing was changed"
}
guard_ready() {   # both directions: the tools, the root entry, /boot mounted, the lock — before anything is touched
  need_tools cryptsetup update-initramfs lsinitramfs findmnt
  find_root_entry || die 5 false "the root filesystem is not on an encrypted volume listed in $CRYPTTAB"
  boot_mounted || die 7 "$(cur_prompt)" "$BOOT is listed in $FSTAB but is not mounted; the start-up files cannot be updated" "nothing was changed"
  take_lock
}

backup_configs() {
  BK=$(mktemp -d "${TMPDIR:-/tmp}/fabos-disk-unlock.XXXXXX") || die 7 "$1" "cannot create a backup directory"
  cp -p "$CRYPTTAB" "$BK/crypttab"
  [ -f "$CONF_HOOK" ] && cp -p "$CONF_HOOK" "$BK/conf-hook"
  [ -f "$INITRAMFS_CONF" ] && cp -p "$INITRAMFS_CONF" "$BK/initramfs.conf"
  return 0
}
restore_configs() {
  cp -p "$BK/crypttab" "$CRYPTTAB"
  if [ -f "$BK/conf-hook" ]; then cp -p "$BK/conf-hook" "$CONF_HOOK"; else rm -f "$CONF_HOOK"; fi
  if [ -f "$BK/initramfs.conf" ]; then cp -p "$BK/initramfs.conf" "$INITRAMFS_CONF"; fi
  log "configuration files restored from the backup"
}
cleanup_backup() { [ -n "${BK:-}" ] && rm -rf "$BK"; return 0; }
trap 'cleanup_backup; cleanup_diag' EXIT

# ---------------------------------------------------------------------------- status
cmd_status() {
  local encrypted=false prompt=null kp=false kin=null consistent=true detail="" dev="null" src="null"
  if find_root_entry; then
    encrypted=true; dev=$(json_str "$CT_NAME"); src=$(json_str "$CT_SRC")
    [ -e "$KEYFILE" ] && kp=true
    if [ "$CT_KEY" = none ] || [ -z "$CT_KEY" ]; then prompt=true; else prompt=false; fi
    if [ "$IS_ROOT" = 1 ] || [ -r "$(initrds)" ]; then
      case "$(keyfile_in_initramfs; echo $?)" in 0) kin=true;; 1) kin=false;; *) kin=null;; esac
    fi
    if [ "$prompt" = false ]; then
      [ "$CT_KEY" = "$KEYFILE" ] || { consistent=false; detail="crypttab uses a key file this setting did not create ($CT_KEY)"; }
      [ "$kp" = true ] || { consistent=false; detail="crypttab names $KEYFILE but the file is missing"; }
      [ "$kin" = false ] && { consistent=false; detail="the key is not inside the initramfs: run 'on' then 'off' again"; }
      [ -z "$detail" ] && detail="starts without asking: the unlock key is in the initramfs on $BOOT"
    else
      [ "$kp" = true ] && { consistent=false; detail="a leftover $KEYFILE exists while crypttab asks for the passphrase"; }
      [ "$kin" = true ] && { consistent=false; detail="the initramfs still carries a key file: rebuild it with update-initramfs -u -k all"; }
      [ -z "$detail" ] && detail="asks for the disk password at start-up"
    fi
  else
    if [ -n "$MAPPER" ]; then detail="root is on $ROOT_SRC but $CRYPTTAB has no entry named $MAPPER"; else detail="the root filesystem is not on an encrypted volume"; fi
    prompt=false
  fi
  printf '{"encrypted": %s, "device": %s, "source": %s, "prompt_at_boot": %s, "keyfile": %s, "keyfile_present": %s, "keyfile_in_initramfs": %s, "consistent": %s, "detail": %s}\n' \
    "$encrypted" "$dev" "$src" "$prompt" "$(json_str "$KEYFILE")" "$kp" "$kin" "$consistent" "$(json_str "$detail")"
}

# ---------------------------------------------------------------------------- off: stop asking
cmd_off() {
  [ $# -eq 0 ] || die 2 null "the passphrase is read from standard input, never from an argument"
  [ "$IS_ROOT" = 1 ] || die 4 null "must run as root (through pkexec rootexec)"
  guard_ready
  local pass=""
  if [ -t 0 ]; then die 2 true "the passphrase must be piped on standard input (one line)"; fi
  IFS= read -r pass || [ -n "$pass" ] || die 2 true "no passphrase on standard input"
  [ -n "$pass" ] || die 2 true "the passphrase is empty"
  local dev; dev=$(crypt_device)
  log "root is $ROOT_SRC (crypttab entry $CT_NAME, source $CT_SRC, key ${CT_KEY}, options ${CT_OPTS:-none})"
  if [ "$FORCE" != 1 ] && [ "$CT_KEY" = "$KEYFILE" ] && [ -e "$KEYFILE" ] && keyfile_in_initramfs; then
    log "already off: the initramfs carries $KEYFILE"
    emit true false "" "already starts without asking"; return 0
  fi
  [ "$FORCE" = 1 ] && log "repair: re-applying 'off' from the start (a fresh key, every start-up file rebuilt and proven)"
  # the passphrase must open the volume before anything is touched (a wrong one changes nothing)
  if ! printf '%s\n' "$pass" | cryptsetup open --test-passphrase "$dev" >/dev/null 2>&1; then
    die 3 true "the passphrase was not accepted for $CT_NAME" "nothing was changed"
  fi
  log "passphrase verified against $dev"
  backup_configs true
  local slot_added=0 initramfs_touched=0
  # rollback <why>: 0 = fully reversed, 1 = an initrd on $BOOT still carries the key, so the slot and the keyfile were KEPT.
  # Before the initramfs was touched the initrd on disk still asks for the passphrase and the slot can go at once. Once it was
  # rebuilt it may name the key: configuration restored -> initramfs rebuilt -> EVERY initrd proven free of the key -> only then
  # the slot removed and the keyfile deleted (a boot in between, or a failed rebuild, must never meet a key without its slot).
  rollback() {
    log "rolling back: $1"
    restore_configs
    if [ "$initramfs_touched" = 1 ]; then
      if update-initramfs -u -k all >/dev/null 2>&1; then log "initramfs rebuilt without the key"; else log "WARNING: update-initramfs failed during the rollback"; fi
      if any_initrd_has_key; then
        log "WARNING: an initrd on $BOOT still carries the key: the key slot and $KEYFILE are kept so the computer still starts; 'on' finishes the reversal"
        return 1
      fi
      log "verified: no initrd on $BOOT carries the key"
    fi
    if [ "$slot_added" = 1 ] && [ -f "$KEYFILE" ]; then
      if cryptsetup -q luksRemoveKey "$dev" "$KEYFILE" >/dev/null 2>&1; then log "key slot removed again"; else log "WARNING: could not remove the key slot; the keyfile is deleted so the slot is unusable"; fi
    fi
    rm -f "$KEYFILE"; log "keyfile deleted"
    return 0
  }
  fail_off() {   # fail_off <why> <message> [detail]: roll back, then exit 7 (fully reversed) or 8 (the key is still in use)
    if rollback "$1"; then die 7 true "$2" "${3:-}"; fi
    local p; case "$(keyfile_in_initramfs; echo $?)" in 0) p=false;; 1) p=true;; *) p=null;; esac
    die 8 "$p" "$2" "the change could not be fully reversed: the start-up files on $BOOT still carry the unlock key, so the key slot and $KEYFILE were kept; turn the setting on to finish${3:+ — $3}"
  }
  # 1. keyfile (4096 random bytes, 0400 root) — on the encrypted root. A leftover keyfile from an earlier run gives its slot
  #    back first (luksRemoveKey with that file removes exactly the slot it opens), so slots are not orphaned.
  mkdir -p "$(dirname "$KEYFILE")" && chmod 0755 "$(dirname "$KEYFILE")" 2>/dev/null
  if [ -f "$KEYFILE" ]; then
    if cryptsetup -q luksRemoveKey "$dev" "$KEYFILE" >/dev/null 2>&1; then log "leftover keyfile: its key slot removed"; else log "leftover keyfile: no key slot of its own"; fi
    rm -f "$KEYFILE"
  fi
  if ! ( umask 077; head -c 4096 /dev/urandom > "$KEYFILE" ) || [ "$(stat -c %s "$KEYFILE" 2>/dev/null)" != 4096 ]; then
    fail_off "could not write the keyfile" "could not create $KEYFILE"
  fi
  chmod 0400 "$KEYFILE"; chown root:root "$KEYFILE" 2>/dev/null || true
  log "keyfile created: $KEYFILE (4096 bytes, 0400)"
  # 2. the key slot (the passphrase authorises it — on cryptsetup's stdin, not its command line)
  if ! printf '%s\n' "$pass" | cryptsetup -q luksAddKey "$dev" "$KEYFILE" >/dev/null 2>&1; then
    fail_off "luksAddKey failed" "cryptsetup could not add the key to $CT_NAME"
  fi
  slot_added=1; log "key slot added on $dev"
  # 3-5. crypttab / conf-hook / initramfs.conf
  write_crypttab "$KEYFILE" "$(add_opt "$CT_OPTS" initramfs)" || fail_off "crypttab write failed" "could not write $CRYPTTAB"
  log "crypttab: $CT_NAME key -> $KEYFILE, options $(add_opt "$CT_OPTS" initramfs)"
  set_pattern || fail_off "conf-hook write failed" "could not write $CONF_HOOK"
  log "conf-hook: KEYFILE_PATTERN=\"$KEYFILE\""
  set_umask || fail_off "initramfs.conf write failed" "could not write $INITRAMFS_CONF"
  log "initramfs.conf: UMASK=0077"
  # 6. rebuild and prove
  initramfs_touched=1
  local out
  if ! out=$(update-initramfs -u -k all 2>&1); then
    log "update-initramfs: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
    fail_off "update-initramfs failed" "update-initramfs failed" "$(printf '%s' "$out" | tail -5)"
  fi
  log "update-initramfs -u -k all: ok"
  if ! keyfile_in_initramfs; then
    fail_off "the keyfile is not inside the initramfs" "the rebuilt initramfs does not contain the unlock key (cryptroot/keyfiles/$CT_NAME.key)"
  fi
  log "verified: $(initrds) contains cryptroot/keyfiles/$CT_NAME.key"
  if ! cryptsetup open --test-passphrase --key-file "$KEYFILE" "$dev" >/dev/null 2>&1; then
    fail_off "the keyfile does not open the volume" "the new key does not open $CT_NAME"
  fi
  log "verified: the keyfile opens $dev"
  log "DONE: the computer will start without asking for the disk password"
  write_record false
  emit true false "" "the unlock key is stored in the start-up files on $BOOT; anyone who starts this computer can use it"
}

# ---------------------------------------------------------------------------- on: ask again
cmd_on() {
  [ $# -eq 0 ] || die 2 null "on takes no arguments"
  [ "$IS_ROOT" = 1 ] || die 4 null "must run as root (through pkexec rootexec)"
  guard_ready
  local dev; dev=$(crypt_device)
  log "root is $ROOT_SRC (crypttab entry $CT_NAME, key ${CT_KEY}, options ${CT_OPTS:-none})"
  local kin; keyfile_in_initramfs; kin=$?
  if [ "$CT_KEY" = none ] && [ ! -e "$KEYFILE" ] && [ "$kin" != 0 ]; then
    log "already on: crypttab asks for the passphrase and no keyfile exists"
    emit true true "" "already asks for the disk password"; return 0
  fi
  if [ "$CT_KEY" != none ] && [ "$CT_KEY" != "$KEYFILE" ]; then
    die 5 false "crypttab uses a key file this setting did not create ($CT_KEY); not touching it"
  fi
  backup_configs false
  # 1. configuration first: key -> none, initramfs option dropped, pattern dropped
  write_crypttab none "$(del_opt "$CT_OPTS" initramfs)" || die 7 false "could not write $CRYPTTAB"
  log "crypttab: $CT_NAME key -> none, options $(del_opt "$CT_OPTS" initramfs)"
  drop_pattern || { restore_configs; die 7 false "could not write $CONF_HOOK"; }
  log "conf-hook: KEYFILE_PATTERN for $KEYFILE dropped"
  # 2. rebuild and prove the initramfs is free of the key BEFORE the slot goes (a failed rebuild must leave a bootable disk)
  local out
  if ! out=$(update-initramfs -u -k all 2>&1); then
    log "update-initramfs: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
    restore_configs
    update-initramfs -u -k all >/dev/null 2>&1 && log "initramfs rebuilt with the previous configuration" || log "WARNING: update-initramfs failed again during the rollback"
    die 7 false "update-initramfs failed" "$(printf '%s' "$out" | tail -5)"
  fi
  log "update-initramfs -u -k all: ok"
  keyfile_in_initramfs; kin=$?
  if [ "$kin" = 0 ]; then
    restore_configs; update-initramfs -u -k all >/dev/null 2>&1 || true
    die 7 false "the rebuilt initramfs still contains the unlock key; the previous configuration was restored"
  fi
  # not proven = not done: an initrd that cannot be listed, or none found where the firmware boots from, keeps its slot
  if [ "$kin" != 1 ] || any_initrd_has_key; then
    log "WARNING: cannot prove every initrd on $BOOT is free of the key; the key slot and $KEYFILE are kept"
    emit false null "the start-up files on $BOOT could not be checked after the rebuild; the unlock key was kept so the computer still starts — run 'on' again" "keyfile kept at $KEYFILE"
    exit 8
  fi
  log "verified: no initrd on $BOOT contains cryptroot/keyfiles/$CT_NAME.key"
  # 3. the slot and the keyfile
  if [ -f "$KEYFILE" ]; then
    if cryptsetup -q luksRemoveKey "$dev" "$KEYFILE" >/dev/null 2>&1; then
      log "key slot removed from $dev"
    else
      log "WARNING: luksRemoveKey failed; the keyfile is kept so a retry can remove the slot"
      emit false true "the start-up prompt is back, but the key slot could not be removed from $CT_NAME; run 'on' again" "keyfile kept at $KEYFILE"
      exit 8
    fi
    rm -f "$KEYFILE"; log "keyfile deleted"
  else
    log "no keyfile present; the slot (if any) cannot be identified and is left alone"
  fi
  log "DONE: the computer asks for the disk password at start-up"
  write_record true
  emit true true "" "asks for the disk password at start-up"
}

# ---------------------------------------------------------------------------- diagnose: the REAL state, item by item
# Owner's report (real laptop): "currently disabled, still sees it". `status` only reads the configuration; `diagnose` looks at what
# the firmware will actually boot. Every item is {id, result: pass|fail|unknown|info, label, detail} in plain English. "unknown" =
# it needs administrator rights (the initrd on $BOOT is root-only) — unless the record the last root run left behind ($RECORD:
# size + mtime + has-key per initrd, and the crypttab key column at that time) still matches the file, in which case that verified
# answer is used and says so. The verdict:
#   switch                   what /etc/crypttab is configured for: "on" (asks), "off" (this setting's key file), "keyscript", "other"
#   prompt_at_boot_expected  true / false / null (cannot be told without administrator rights, or the start-up would FAIL: boot_risk)
#   agrees                   the verdict matches the switch; when false, `reason` says exactly why and `repair` what 'repair' would do
# What decides the prompt is the initrd GRUB boots (grub.cfg default entry), else the running kernel's: does it carry
# cryptroot/keyfiles/<name>.key, and what does ITS crypttab say for the root device (read with unmkinitramfs as root: "none" = asks;
# a key path = unlocks with it — or, if that key is not inside, cryptsetup-initramfs skips the device and the computer does not start;
# no root line at all = the hook skipped the root target = the computer does not start). Other crypttab entries with no key (a swap
# partition) ask for THEIR password at start-up, which looks exactly like the disk prompt: they are reported and count as a prompt.
GRUB_CFG=${FABOS_DU_GRUB_CFG:-$BOOT/grub/grub.cfg}
GRUBENV=${FABOS_DU_GRUBENV:-$BOOT/grub/grubenv}
EFI_DIR=${FABOS_DU_EFI_DIR:-$BOOT/efi/EFI}
RECORD=${FABOS_DU_RECORD:-/var/lib/fabos/disk-unlock-check.json}
ITEMS=(); NEEDS_ROOT=0; DIAG_TMP=""; EXTRA_JSON=""
item() {   # item <id> <pass|fail|unknown|info> <label> <detail>   (NEEDS_ROOT is set by the caller when the VERDICT depends on the unknown)
  ITEMS+=("{\"id\": $(json_str "$1"), \"result\": $(json_str "$2"), \"label\": $(json_str "$3"), \"detail\": $(json_str "$4")}")
  return 0
}
diag_tmp() { [ -n "$DIAG_TMP" ] || DIAG_TMP=$(mktemp -d "${TMPDIR:-/tmp}/fabos-du-diag.XXXXXX"); }
cleanup_diag() { [ -n "$DIAG_TMP" ] && rm -rf "$DIAG_TMP"; return 0; }
listing_of() {   # listing_of <initrd>: the file list inside it (cached per run); 1 = cannot be read (root-only file, or not an initrd)
  local f="$1" c; diag_tmp; c=$DIAG_TMP/listing.$(printf '%s' "$f" | tr '/' '_')
  if [ ! -f "$c" ]; then
    [ -r "$f" ] || return 1
    lsinitramfs "$f" > "$c" 2>/dev/null || { rm -f "$c"; return 1; }
  fi
  cat "$c"
}
listing_has_key() { listing_of "$1" 2>/dev/null | grep -q -x -E "/?cryptroot/keyfiles/${CT_NAME}\.key"; }
initrd_crypttab() {   # initrd_crypttab <initrd>: the crypttab INSIDE it (unmkinitramfs; root); 1 = cannot
  local f="$1" d; command -v unmkinitramfs >/dev/null 2>&1 || return 1; [ -r "$f" ] || return 1
  diag_tmp; d=$DIAG_TMP/x.$(printf '%s' "$f" | tr '/' '_')
  if [ ! -d "$d" ]; then mkdir -p "$d"; unmkinitramfs -- "$f" "$d" >/dev/null 2>&1 || { rm -rf "$d"; return 1; }; fi
  local c found=0
  for c in "$d/cryptroot/crypttab" "$d/main/cryptroot/crypttab"; do [ -f "$c" ] && { cat "$c"; found=1; }; done
  [ "$found" = 1 ]
}
# the verified record a root run leaves behind: per initrd size|mtime|has_key, plus the crypttab key column and the verdict then
write_record() {   # write_record <prompt_at_boot_expected true|false|null>
  [ "$IS_ROOT" = 1 ] || return 0
  local i lines="" hk
  while IFS= read -r i; do
    [ -n "$i" ] && [ -f "$i" ] || continue
    hk=null; if listing_of "$i" >/dev/null 2>&1; then if listing_has_key "$i"; then hk=true; else hk=false; fi; fi
    lines="$lines$i|$(stat -c '%s|%Y' "$i" 2>/dev/null)|$hk"$'\n'
  done <<EOF
$(initrd_list)
EOF
  mkdir -p "$(dirname "$RECORD")" 2>/dev/null
  printf '%s' "$lines" | python3 -c '
import json, os, sys, time
out, expected, key, action = sys.argv[1:5]
rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "action": action, "crypttab_key": key,
       "prompt_at_boot_expected": {"true": True, "false": False}.get(expected), "initrds": {}}
for line in sys.stdin.read().splitlines():
    p = line.split("|")
    if len(p) == 4 and p[1].isdigit():
        rec["initrds"][p[0]] = {"size": int(p[1]), "mtime": int(p[2]), "has_key": {"true": True, "false": False}.get(p[3])}
tmp = out + ".tmp"
open(tmp, "w").write(json.dumps(rec, indent=1) + "\n")
os.chmod(tmp, 0o644); os.replace(tmp, out)' "$RECORD" "$1" "$CT_KEY" "$ACTION" 2>/dev/null && log "record written: $RECORD" || true
}
record_lookup() {   # record_lookup <initrd>: "true|false <at>" when the record covers this file UNCHANGED and crypttab's key column is unchanged
  [ -r "$RECORD" ] || return 1
  python3 - "$RECORD" "$1" "$(stat -c '%s|%Y' "$1" 2>/dev/null)" "$CT_KEY" <<'PY'
import json, sys
p, f, st, key = sys.argv[1:5]
try:
    r = json.load(open(p))
except Exception:
    sys.exit(1)
e = (r.get("initrds") or {}).get(f)
if not e or "%s|%s" % (e.get("size"), e.get("mtime")) != st or r.get("crypttab_key") != key or e.get("has_key") is None:
    sys.exit(1)
print("true" if e["has_key"] else "false", r.get("at", ""))
PY
}
# the initrd GRUB will boot: grub.cfg's default entry (set default=N | saved -> grubenv saved_entry | an id/title), its `initrd` line.
# Paths in grub.cfg are relative to the filesystem GRUB reads: with a separate /boot partition "/initrd.img-x" -> $BOOT/initrd.img-x;
# without one "/boot/initrd.img-x" as it is. Prints "<path>\t<title>\t<how>"; 1 = no grub.cfg / nothing found.
grub_default_initrd() {
  [ -r "$GRUB_CFG" ] || return 1
  python3 - "$GRUB_CFG" "$GRUBENV" "$BOOT" <<'PY'
import re, sys
cfg, envp, boot = sys.argv[1:4]
txt = open(cfg, errors="replace").read()
default = None
m = re.search(r'^\s*set\s+default="?([^"\n]*)"?\s*$', txt, re.M)
if m:
    default = m.group(1).strip()
saved = None
try:
    for line in open(envp, errors="replace"):
        if line.startswith("saved_entry="):
            saved = line.split("=", 1)[1].strip()
except OSError:
    pass
how = "default=%s" % default
if default is None:
    default = "0"
if "saved" in default:
    default = saved if saved is not None else "0"
    how = "default=saved (grubenv saved_entry=%s)" % saved
# entries: blocks opened by "menuentry '...' ... {" / "submenu '...' ... {" lines, closed by a "}" line (grub-mkconfig's layout)
entries = []
stack = []
top = -1
for line in txt.splitlines():
    m = re.match(r"""^\s*(menuentry|submenu)\s+(['"])(.*?)\2(.*)\{\s*$""", line)
    if m:
        kind, title, rest = m.group(1), m.group(3), m.group(4)
        idm = re.search(r"""menuentry_id_option\s+(['"])(.*?)\1""", rest)
        if not stack:
            top += 1
        e = {"kind": kind, "title": title, "id": idm.group(2) if idm else None, "depth": len(stack), "top_index": top, "initrd": None, "linux": None}
        entries.append(e)
        stack.append(e)
        continue
    if re.match(r"^\s*}\s*$", line) and stack:
        stack.pop()
        continue
    if stack and stack[-1]["kind"] == "menuentry":
        mi = re.match(r"^\s*initrd(?:16|efi)?\s+(.+?)\s*$", line)
        if mi and stack[-1]["initrd"] is None:
            stack[-1]["initrd"] = mi.group(1).split()
        ml = re.match(r"^\s*linux(?:16|efi)?\s+(\S+)", line)
        if ml and stack[-1]["linux"] is None:
            stack[-1]["linux"] = ml.group(1)
menus = [e for e in entries if e["kind"] == "menuentry"]
if not menus:
    sys.exit(1)
chosen = None
if re.match(r"^\d+(>\d+)*$", default):
    parts = [int(x) for x in default.split(">")]
    cand = [e for e in entries if e["depth"] == 0 and e["top_index"] == parts[0]]
    if cand:
        c = cand[0]
        if c["kind"] == "submenu":
            inner = [e for e in menus if e["depth"] >= 1 and e["top_index"] == parts[0]]
            idx = parts[1] if len(parts) > 1 else 0
            chosen = inner[idx] if idx < len(inner) else (inner[0] if inner else None)
        else:
            chosen = c
else:
    for e in menus:
        if default in (e["id"], e["title"]):
            chosen = e
            break
if chosen is None:
    chosen = menus[0]
    how += " (not found: first entry)"
if not chosen["initrd"]:
    sys.exit(1)
def mapped(p):
    p = p.replace("${prefix}", boot + "/grub").replace("$prefix", boot + "/grub")
    if p.startswith("(") and ")" in p:
        p = p[p.index(")") + 1:]
    if p.startswith("/boot/"):
        return p
    return boot.rstrip("/") + p
imgs = [mapped(p) for p in chosen["initrd"] if "initrd" in p] or [mapped(chosen["initrd"][-1])]
print("%s\t%s\t%s" % (imgs[-1], chosen["title"], how))
PY
}
efi_chain_check() {   # the EFI grub.cfg stub(s): "search.fs_uuid X" must be the UUID of the filesystem holding /boot
  local want got f any=0
  want=$(findmnt -n -o UUID "$BOOT" 2>/dev/null | head -1)
  [ -n "$want" ] || want=$(findmnt -n -o UUID / 2>/dev/null | head -1)
  for f in "$EFI_DIR"/*/grub.cfg; do
    [ -r "$f" ] || continue
    any=1
    got=$(sed -n -E 's/.*search\.fs_uuid[[:space:]]+([0-9a-fA-F-]+).*/\1/p' "$f" | head -1)
    [ -n "$got" ] || continue
    if [ -z "$want" ]; then echo "info|$f names filesystem $got; the UUID of $BOOT could not be read to compare"; return 0; fi
    if [ "$got" = "$want" ]; then echo "pass|$f loads the GRUB menu from the filesystem that holds $BOOT ($got)"; else echo "fail|$f loads the GRUB menu from filesystem $got, but $BOOT is $want: the firmware may boot ANOTHER system's start-up files (dual boot?)"; fi
    return 0
  done
  if [ "$any" = 1 ]; then echo "info|no search.fs_uuid line in the EFI grub.cfg"; else echo "info|no EFI grub.cfg under $EFI_DIR (legacy BIOS boot, or the EFI partition is not mounted)"; fi
}
emit_diag() {   # emit_diag <encrypted> <switch> <configured prompt> <expected> <agrees> <boot_risk> <reason> <repair> <checked_at> <booted_initrd>
  local items="" it nr=false ar=false
  for it in "${ITEMS[@]+"${ITEMS[@]}"}"; do items="$items${items:+, }$it"; done
  [ "$NEEDS_ROOT" = 1 ] && nr=true; [ "$IS_ROOT" = 1 ] && ar=true
  printf '{"encrypted": %s, "device": %s, "switch": %s, "prompt_at_boot": %s, "prompt_at_boot_expected": %s, "agrees": %s, "boot_risk": %s, "needs_root": %s, "as_root": %s, "fails": %s, "reason": %s, "repair": %s, "checked_at": %s, "booted_initrd": %s, "items": [%s]}\n' \
    "$1" "$( [ -n "$CT_NAME" ] && json_str "$CT_NAME" || echo null )" "$(json_str "$2")" "$3" "$4" "$5" "$6" "$nr" "$ar" "${DIAG_FAILS:-0}" \
    "$( [ -n "$7" ] && json_str "$7" || echo null )" "$( [ -n "$8" ] && json_str "$8" || echo null )" "$(json_str "$9")" "$( [ -n "${10}" ] && json_str "${10}" || echo null )" "$items"
}
DIAG_EXPECTED=null; DIAG_AGREES=null; DIAG_BOOT_RISK=false; DIAG_GRUB_FAIL=0; DIAG_JSON=""; DIAG_BOOT_RISK_KEY=0; DIAG_FAILS=0
run_diagnose() {   # fills ITEMS and DIAG_*; prints nothing (cmd_diagnose prints, repair embeds). Fresh listings every run (repair rebuilds)
  ITEMS=(); NEEDS_ROOT=0; DIAG_GRUB_FAIL=0; DIAG_BOOT_RISK=false; DIAG_BOOT_RISK_KEY=0; DIAG_FAILS=0
  [ -n "$DIAG_TMP" ] && rm -rf "$DIAG_TMP"/listing.* "$DIAG_TMP"/x.* 2>/dev/null
  local checked_at; checked_at=$(date -u +%FT%TZ)
  if ! find_root_entry; then
    if [ -n "$MAPPER" ]; then
      item root_luks fail "Root filesystem on an encrypted volume" "root is $ROOT_SRC but $CRYPTTAB has no entry named $MAPPER; this setting cannot manage it"
    else
      item root_luks info "Root filesystem on an encrypted volume" "the root filesystem (${ROOT_SRC:-unknown}) is not on an encrypted volume, so there is no disk password at start-up — a password asked at start-up is the LOGIN screen's, which is a different thing"
    fi
    DIAG_EXPECTED=false; DIAG_AGREES=null
    DIAG_JSON=$(emit_diag false none false false null false "" "" "$checked_at" "")
    return 0
  fi
  local dev switch configured fstype; dev=$(crypt_device)
  fstype=$(lsblk -n -o FSTYPE "$dev" 2>/dev/null | head -1)
  if [ "$fstype" = crypto_LUKS ] || [ -z "$fstype" ]; then
    item root_luks pass "Root filesystem on an encrypted volume" "root is $ROOT_SRC, unlocked from $CT_SRC${fstype:+ ($fstype)}"
  else
    item root_luks fail "Root filesystem on an encrypted volume" "root is $ROOT_SRC from $CT_SRC, but that device reads as $fstype, not LUKS"
  fi
  if [ "$CT_KEY" = none ]; then switch=on; configured=true
  elif [ "$CT_KEY" = "$KEYFILE" ]; then switch=off; configured=false
  elif case ",$CT_OPTS," in *,keyscript=*) true;; *) false;; esac; then switch=keyscript; configured=false
  else switch=other; configured=false; fi
  item crypttab_entry pass "Entry for the root device in $CRYPTTAB" "$CT_NAME $CT_SRC key=$CT_KEY options=${CT_OPTS:-none}"
  case "$switch" in
    on) item crypttab_key info "Configured to ask for the disk password (switch ON)" "the key column is 'none': the start-up files are meant to ask";;
    off) item crypttab_key info "Configured to start without asking (switch OFF)" "the key column names this setting's key file $KEYFILE";;
    keyscript) item crypttab_key info "Unlocked by a keyscript" "the entry uses a keyscript (${CT_OPTS}); this setting does not manage it";;
    *) item crypttab_key info "Unlocked by a key file this setting did not create" "key column $CT_KEY; this setting does not manage it";;
  esac
  if [ "$switch" = off ]; then
    if has_opt "$CT_OPTS" initramfs; then item crypttab_initramfs_opt pass "'initramfs' option on the root entry" "present — the entry is forced into the start-up files"
    else item crypttab_initramfs_opt fail "'initramfs' option on the root entry" "missing — this setting adds it; cryptsetup-initramfs still includes the root device on its own, so this alone does not bring the prompt back, but 'repair' restores it"; fi
    if [ -e "$KEYFILE" ]; then
      local mode size; mode=$(stat -c %a "$KEYFILE" 2>/dev/null); size=$(stat -c %s "$KEYFILE" 2>/dev/null)
      if [ "$mode" = 400 ] && [ "$size" = 4096 ]; then item keyfile pass "Unlock key file $KEYFILE" "present, $size bytes, mode 0$mode"
      else item keyfile fail "Unlock key file $KEYFILE" "present but mode 0$mode / $size bytes (expected 0400, 4096); 'repair' recreates it"; fi
    else item keyfile fail "Unlock key file $KEYFILE" "missing although crypttab names it: the start-up files cannot carry it; 'repair' recreates it (needs the disk passphrase)"; fi
  else
    if [ -e "$KEYFILE" ]; then item keyfile fail "Leftover unlock key file" "$KEYFILE exists while crypttab asks for the passphrase; 'repair' removes it and its key slot"
    else item keyfile info "Unlock key file" "none (the setting is on)"; fi
  fi
  # the LUKS slots and whether the key file opens the volume (root)
  if [ "$IS_ROOT" = 1 ] && command -v cryptsetup >/dev/null 2>&1; then
    local slots; slots=$(cryptsetup luksDump "$dev" 2>/dev/null | grep -cE '^ +[0-9]+: luks2')
    if [ -n "$slots" ] && [ "$slots" != 0 ]; then
      if [ "$switch" = off ]; then
        if [ "$slots" -ge 2 ]; then item key_slots pass "LUKS key slots in use" "$slots (your passphrase + the start-up key)"; else item key_slots fail "LUKS key slots in use" "$slots — the start-up key has no slot of its own; 'repair' adds it (needs the disk passphrase)"; fi
      else item key_slots info "LUKS key slots in use" "$slots"; fi
    else item key_slots info "LUKS key slots in use" "could not read the LUKS header of $dev"; fi
    if [ -f "$KEYFILE" ]; then
      if cryptsetup open --test-passphrase --key-file "$KEYFILE" "$dev" >/dev/null 2>&1; then item keyfile_opens pass "The key file opens the volume" "cryptsetup --test-passphrase with $KEYFILE: accepted"
      else item keyfile_opens fail "The key file opens the volume" "cryptsetup does not accept $KEYFILE: a start-up file carrying this key will NOT start the computer (cryptsetup-initramfs tries a key file once, then drops to a rescue shell); 'repair' fixes it (needs the disk passphrase)"; DIAG_BOOT_RISK_KEY=1; fi
    fi
  else
    item key_slots unknown "LUKS key slots in use" "needs administrator rights to read the LUKS header"
  fi
  # /boot
  if boot_mounted; then
    if grep -q -E "^[[:space:]]*[^#[:space:]]+[[:space:]]+${BOOT}/?[[:space:]]" "$FSTAB" 2>/dev/null; then item boot_mounted pass "$BOOT partition mounted" "$BOOT is its own partition and is mounted: the start-up files land where the firmware boots from"
    else item boot_mounted info "$BOOT partition" "$BOOT is part of the root filesystem (no separate partition in $FSTAB)"; fi
  else
    item boot_mounted fail "$BOOT partition mounted" "$BOOT is listed in $FSTAB but NOT mounted: anything rebuilt goes to the wrong place and the real start-up files keep whatever they had; mount it (sudo mount $BOOT) and run 'repair'"
  fi
  # conf-hook / initramfs.conf
  local pat; pat=$(sed -n 's/^[[:space:]]*KEYFILE_PATTERN="\{0,1\}\([^"#]*\)"\{0,1\}.*/\1/p' "$CONF_HOOK" 2>/dev/null | tail -1)
  if [ "$switch" = off ]; then
    if [ "$pat" = "$KEYFILE" ]; then item conf_hook_pattern pass "KEYFILE_PATTERN in $CONF_HOOK" "\"$pat\" — the hook copies the key into the start-up files"
    elif [ -n "$pat" ] && case "$KEYFILE" in $pat) true;; *) false;; esac; then item conf_hook_pattern pass "KEYFILE_PATTERN in $CONF_HOOK" "\"$pat\" matches $KEYFILE"
    else item conf_hook_pattern fail "KEYFILE_PATTERN in $CONF_HOOK" "${pat:+\"$pat\" does not match $KEYFILE}${pat:-not set}: cryptsetup-initramfs will not copy the key, and skips a root device whose key it cannot copy — the next rebuild leaves start-up files that do not start; 'repair' sets it"; fi
  else
    if [ -n "$pat" ]; then item conf_hook_pattern info "KEYFILE_PATTERN in $CONF_HOOK" "\"$pat\" (set although the setting is on; harmless without a key column)"
    else item conf_hook_pattern info "KEYFILE_PATTERN in $CONF_HOOK" "not set (the setting is on)"; fi
  fi
  local um; um=$(sed -n 's/^[[:space:]]*UMASK=\(.*\)/\1/p' "$INITRAMFS_CONF" 2>/dev/null | tail -1)
  if [ "$um" = 0077 ] || [ "$um" = 077 ]; then item initramfs_umask pass "UMASK=0077 in $INITRAMFS_CONF" "start-up files are written root-only"
  elif [ "$switch" = off ]; then item initramfs_umask fail "UMASK=0077 in $INITRAMFS_CONF" "${um:-not set}: a start-up file carrying key material could be world-readable; 'repair' sets it"
  else item initramfs_umask info "UMASK in $INITRAMFS_CONF" "${um:-not set} (no key material in the start-up files while the setting is on)"; fi
  # the kernels and their initrds
  local list n=0 missing="" i v
  list=$(initrd_list)
  while IFS= read -r i; do [ -n "$i" ] || continue; n=$((n+1)); [ -f "$i" ] || missing="$missing ${i##*/}"; done <<EOF
$list
EOF
  if [ -d "$IT_STATE" ] && [ "$n" -gt 0 ]; then
    if [ -z "$missing" ]; then item kernels pass "Registered kernels have a start-up file" "$n kernel(s) in $IT_STATE, each with its initrd on $BOOT"
    else item kernels fail "Registered kernels have a start-up file" "missing on $BOOT:$missing (update-initramfs -c -k <version> creates it; 'repair' rebuilds all)"; fi
  elif [ "$n" -gt 0 ]; then item kernels info "Start-up files on $BOOT" "$n initrd(s) found by name (no $IT_STATE list)"
  else item kernels fail "Start-up files on $BOOT" "no initrd.img-* on $BOOT at all"; fi
  # which one GRUB boots
  local booted="" gtitle="" ghow="" gline
  if gline=$(grub_default_initrd); then
    booted=${gline%%$'\t'*}; gtitle=$(printf '%s' "$gline" | cut -f2); ghow=$(printf '%s' "$gline" | cut -f3)
    if printf '%s\n' "$list" | grep -q -x -F "$booted"; then
      item grub_default_initrd pass "GRUB's default entry boots a registered start-up file" "'$gtitle' -> ${booted##*/} ($ghow)"
    elif [ -f "$booted" ]; then
      item grub_default_initrd fail "GRUB's default entry boots a registered start-up file" "'$gtitle' boots ${booted##*/}, which is not one of the files update-initramfs maintains — changes to the setting never reach it; 'repair' runs update-grub"; DIAG_GRUB_FAIL=1
      list="$list"$'\n'"$booted"          # what it carries still decides the prompt: look at it too
    else
      item grub_default_initrd fail "GRUB's default entry boots a registered start-up file" "'$gtitle' names ${booted##*/}, which does not exist on $BOOT: the menu is stale (update-grub); 'repair' runs it"; DIAG_GRUB_FAIL=1; booted=""
    fi
  else
    item grub_default_initrd info "GRUB's default entry" "$( [ -r "$GRUB_CFG" ] && echo "no initrd line found in $GRUB_CFG" || echo "$GRUB_CFG is not readable here" )"
  fi
  local efi; efi=$(efi_chain_check); item efi_grub_chain "${efi%%|*}" "EFI boot chain" "${efi#*|}"
  [ -n "$booted" ] || { booted=$(initrds); [ -n "$booted" ] && ghow="running kernel's (GRUB's choice unknown)"; }
  # every initrd: does it carry the key? and the booted one: what does its crypttab say?
  local want_key=false; [ "$switch" = off ] && want_key=true
  local root_expected=null root_why="" rec hk=null at tag ic rootline ikey bad_initrds="" booted_hk=null
  while IFS= read -r i; do
    [ -n "$i" ] && [ -f "$i" ] || continue
    v=${i##*/initrd.img-}; tag=""; [ "$i" = "$booted" ] && tag=" — this is the one GRUB starts"
    if listing_of "$i" >/dev/null 2>&1; then
      if listing_has_key "$i"; then hk=true; else hk=false; fi
      if [ "$hk" = "$want_key" ]; then
        item "initrd_key:$v" pass "Start-up file for kernel $v carries the unlock key: $( [ $hk = true ] && echo yes || echo no )" "as the setting expects$tag"
      elif [ "$hk" = true ]; then bad_initrds="$bad_initrds $v"; item "initrd_key:$v" fail "Start-up file for kernel $v carries the unlock key: yes" "but the setting is on (it should ask): the computer starts without asking; 'repair' rebuilds it without the key$tag"
      else bad_initrds="$bad_initrds $v"; item "initrd_key:$v" fail "Start-up file for kernel $v carries the unlock key: no" "but the setting is off: this file was built before the key was configured, or the hook could not copy the key; 'repair' rebuilds it$tag"; fi
    elif rec=$(record_lookup "$i"); then
      hk=${rec%% *}; at=${rec#* }
      if [ "$hk" = "$want_key" ]; then item "initrd_key:$v" pass "Start-up file for kernel $v carries the unlock key: $( [ $hk = true ] && echo yes || echo no )" "verified as administrator at $at; the file is unchanged since$tag"
      else bad_initrds="$bad_initrds $v"; item "initrd_key:$v" fail "Start-up file for kernel $v carries the unlock key: $( [ $hk = true ] && echo yes || echo no )" "verified as administrator at $at (unchanged since) — does not match the setting$tag"; fi
    else
      hk=null; [ "$i" = "$booted" ] && NEEDS_ROOT=1
      item "initrd_key:$v" unknown "Start-up file for kernel $v carries the unlock key: ?" "the file is root-only and has changed since it was last verified (or never was): needs administrator rights$tag"
    fi
    [ "$i" = "$booted" ] && booted_hk=$hk
    if [ "$i" = "$booted" ]; then
      if [ "$hk" = true ]; then root_expected=false; root_why="${i##*/} carries the unlock key"
      elif [ "$hk" = false ]; then
        if ic=$(initrd_crypttab "$i"); then
          rootline=$(printf '%s\n' "$ic" | awk -v n="$CT_NAME" '$1==n {print; exit}')
          if [ -z "$rootline" ]; then
            root_expected=null; DIAG_BOOT_RISK=true; root_why="${i##*/} does not set up the encrypted root at all (cryptsetup-initramfs skipped it because its key file did not match KEYFILE_PATTERN): the computer will not start from it"
            item "initrd_crypttab:$v" fail "Root device inside start-up file $v" "no entry for $CT_NAME: the computer will NOT start from this file; 'repair' rebuilds it"
          else
            ikey=$(printf '%s\n' "$rootline" | awk '{print $3}')
            if [ -z "$ikey" ] || [ "$ikey" = none ]; then root_expected=true; root_why="${i##*/} asks for the passphrase (its own crypttab has no key for $CT_NAME)"
              item "initrd_crypttab:$v" info "Root device inside start-up file $v" "asks for the passphrase (key none)"
            else root_expected=null; DIAG_BOOT_RISK=true; root_why="${i##*/} names the key $ikey but does not carry it: the computer will not start from it"
              item "initrd_crypttab:$v" fail "Root device inside start-up file $v" "names key $ikey which is not inside the file: the computer will NOT start from it; 'repair' rebuilds it"; fi
          fi
        else
          if [ "$switch" = on ]; then root_expected=true; root_why="${i##*/} has no unlock key inside"
          else root_expected=null; NEEDS_ROOT=1; root_why="${i##*/} has no unlock key inside; whether it asks for the passphrase or fails to start depends on its own crypttab, which needs administrator rights to read"
            item "initrd_crypttab:$v" unknown "Root device inside start-up file $v" "needs administrator rights (unmkinitramfs) to read"; fi
        fi
      else
        root_expected=null; root_why="${i##*/} is root-only and unverified"
      fi
    fi
  done <<EOF
$list
EOF
  [ -n "$booted" ] || root_why="no start-up file to look at on $BOOT"
  # the unlocker inside the booted initrd
  if [ -n "$booted" ] && listing_of "$booted" >/dev/null 2>&1; then
    if listing_of "$booted" | grep -q -E '^/?scripts/local-top/cryptroot$'; then item initrd_unlocker pass "Unlock method inside the start-up file" "cryptsetup-initramfs (scripts/local-top/cryptroot) — the method this setting configures"
    elif listing_of "$booted" | grep -q -E 'systemd-cryptsetup'; then item initrd_unlocker fail "Unlock method inside the start-up file" "systemd-cryptsetup (dracut-style) — this setting configures cryptsetup-initramfs; the key file would not be used"
    else item initrd_unlocker fail "Unlock method inside the start-up file" "neither cryptroot nor systemd-cryptsetup found in ${booted##*/}: is cryptsetup-initramfs installed?"; fi
  elif [ -n "$booted" ]; then item initrd_unlocker unknown "Unlock method inside the start-up file" "needs administrator rights to read ${booted##*/}"; fi
  # other encrypted devices in crypttab (swap, data): a 'none' key asks for ITS password at start-up — looks exactly like the disk prompt
  local oname osrc okey oopts others=0 others_prompt=""
  while read -r oname osrc okey oopts; do
    case "$oname" in ""|\#*) continue;; esac
    [ "$oname" = "$CT_NAME" ] && continue
    others=$((others+1)); okey=${okey:-none}
    if case ",${oopts:-}," in *,keyscript=*) true;; *) false;; esac; then item "other_device:$oname" pass "Other encrypted device '$oname'" "unlocked by a keyscript (${oopts}); no prompt"
    elif [ "$okey" = none ]; then others_prompt="$others_prompt $oname"
      item "other_device:$oname" fail "Other encrypted device '$oname'" "has no key (key column 'none'): it asks for ITS OWN password at start-up$( has_opt "${oopts:-}" initramfs && echo " (in the start-up files)" || echo " (after the root disk is unlocked)" ) — this looks exactly like the disk prompt; this setting only manages the root disk. Give it a key (crypttab option keyscript=decrypt_derived for swap, or a key file stored on the encrypted root) or remove the entry"
    else item "other_device:$oname" pass "Other encrypted device '$oname'" "unlocked with key $okey; no prompt"; fi
  done < "$CRYPTTAB"
  [ "$others" = 0 ] && item other_devices info "Other encrypted devices in $CRYPTTAB" "none — only the root device"
  # ---- verdict
  local expected=$root_expected agrees=null reason="" repair="" fails=0
  for it in "${ITEMS[@]+"${ITEMS[@]}"}"; do case "$it" in *'"result": "fail"'*) fails=$((fails+1));; esac; done
  DIAG_FAILS=$fails
  [ "$DIAG_BOOT_RISK_KEY" = 1 ] && [ "$booted_hk" = true ] && DIAG_BOOT_RISK=true
  if [ -n "$others_prompt" ] && [ "$DIAG_BOOT_RISK" = false ] && [ "$expected" != null ]; then expected=true; fi
  if [ "$DIAG_BOOT_RISK" = true ]; then
    expected=null; agrees=false
    reason="the computer may not start: $root_why"
    repair="run 'repair' now (Fab AI Controls › Settings › General › Start-up › Fix now, or: fabos disk-unlock repair) — it rebuilds the start-up files so they carry a key that opens the disk, or ask for the passphrase again"
  else
    case "$switch" in
      on) [ "$expected" = true ] && agrees=true; [ "$expected" = false ] && agrees=false;;
      off) [ "$expected" = false ] && agrees=true; [ "$expected" = true ] && agrees=false;;
      *) agrees=null;;
    esac
    if [ "$DIAG_GRUB_FAIL" = 1 ] && { [ "$switch" = on ] || [ "$switch" = off ]; }; then
      agrees=false
      reason="GRUB's default entry boots ${booted:+${booted##*/}}${booted:-a start-up file that does not exist}, which update-initramfs does not maintain: changes to the setting never reach it${root_why:+ — $root_why}"
      repair="run 'repair' (Fix now / fabos disk-unlock repair): it rebuilds the start-up files and refreshes the GRUB menu (update-grub)"
    elif [ "$agrees" = false ]; then
      if [ "$switch" = off ]; then
        if [ -n "$others_prompt" ] && [ "$root_expected" != true ]; then
          reason="the root disk itself starts without asking, but another encrypted device ($others_prompt ) has no key and asks for its own password at start-up"
          repair="give that device a key (crypttab: keyscript=decrypt_derived for swap, or a key file on the encrypted root) — this setting only manages the root disk"
        else
          reason="the switch is off but the start-up files still ask for the password: $root_why"
          repair="run 'repair' (Fix now / fabos disk-unlock repair): it stores the key again and rebuilds every start-up file, then proves the key is inside$( [ "$DIAG_GRUB_FAIL" = 1 ] && echo ", and refreshes the GRUB menu" )"
        fi
      else
        reason="the switch is on but the start-up files do not ask: $root_why"
        repair="run 'repair' (Fix now / fabos disk-unlock repair): it rebuilds the start-up files without the key and removes the key slot"
      fi
    elif [ "$agrees" = null ]; then
      if [ "$switch" = keyscript ] || [ "$switch" = other ]; then reason="the root device is unlocked by ${CT_KEY}${CT_OPTS:+ ($CT_OPTS)}, which this setting did not set up"
      elif [ "$NEEDS_ROOT" = 1 ]; then reason="cannot tell without administrator rights: $root_why"; repair="check as administrator (Fab AI Controls › Start-up › Check as administrator, or: fabos disk-unlock diagnose --admin)"
      else reason="$root_why"; fi
    else
      reason="the start-up files match the setting: $root_why"
      if [ -n "$bad_initrds" ]; then agrees=false
        reason="$reason — but the start-up file for kernel$bad_initrds does not match the setting (chosen from the GRUB menu it would $( [ "$switch" = off ] && echo "ask for the password" || echo "start without asking" ))"
        repair="run 'repair' (Fix now / fabos disk-unlock repair): it rebuilds the start-up files of every kernel (update-initramfs -k all)"
      elif [ "$fails" -gt 0 ]; then
        reason="$reason — but $fails check$( [ "$fails" = 1 ] || echo s ) failed (see the items)"
        repair="run 'repair' (Fix now / fabos disk-unlock repair): it re-applies the setting from the start and puts the configuration right"; fi
    fi
  fi
  DIAG_EXPECTED=$expected; DIAG_AGREES=$agrees
  DIAG_JSON=$(emit_diag true "$switch" "$configured" "$expected" "$agrees" "$DIAG_BOOT_RISK" "$reason" "$repair" "$checked_at" "$booted")
  return 0
}
cmd_diagnose() {
  [ $# -eq 0 ] || die 2 null "diagnose takes no arguments"
  command -v lsinitramfs >/dev/null 2>&1 || die 6 null "missing tools: lsinitramfs (initramfs-tools is needed)"
  run_diagnose
  [ "$IS_ROOT" = 1 ] && [ -n "$CT_NAME" ] && write_record "$DIAG_EXPECTED"
  printf '%s\n' "$DIAG_JSON"
}

# ---------------------------------------------------------------------------- repair: make the start-up files match the switch
# Runs diagnose, then re-applies the configured direction idempotently: switch off -> the whole 'off' path again (the passphrase on
# STDIN authorises a fresh key slot; every step proven and rolled back on failure exactly as in 'off'), switch on -> the 'on' path
# (nothing needed), then update-grub when GRUB's default entry does not boot a maintained start-up file, then diagnose again. The
# result JSON embeds the new diagnosis. Nothing to repair = ok with the diagnosis, no root step run.
FORCE=0
cmd_repair() {
  [ $# -eq 0 ] || die 2 null "the passphrase is read from standard input, never from an argument"
  [ "$IS_ROOT" = 1 ] || die 4 null "must run as root (through pkexec rootexec)"
  need_tools cryptsetup update-initramfs lsinitramfs findmnt
  run_diagnose
  find_root_entry || die 5 false "the root filesystem is not on an encrypted volume listed in $CRYPTTAB"
  local switch=off; [ "$CT_KEY" = none ] && switch=on
  log "diagnose before repair: switch=$switch expected=$DIAG_EXPECTED agrees=$DIAG_AGREES boot_risk=$DIAG_BOOT_RISK grub_stale=$DIAG_GRUB_FAIL"
  if [ "$CT_KEY" != none ] && [ "$CT_KEY" != "$KEYFILE" ]; then
    EXTRA_JSON="\"diagnosis\": $DIAG_JSON"; die 5 false "the root device is unlocked by $CT_KEY, which this setting did not set up; not touching it"
  fi
  if [ "$DIAG_AGREES" = true ] && [ "$DIAG_BOOT_RISK" = false ] && [ "$DIAG_GRUB_FAIL" = 0 ] && [ "$DIAG_FAILS" = 0 ]; then
    write_record "$DIAG_EXPECTED"
    EXTRA_JSON="\"diagnosis\": $DIAG_JSON"; log "nothing to repair: the start-up files match the setting"
    emit true "$DIAG_EXPECTED" "" "nothing to repair: the start-up files already match the setting"; return 0
  fi
  local pass="" sub rc
  if [ "$switch" = off ]; then
    [ -t 0 ] && die 2 false "the passphrase must be piped on standard input (one line)"
    IFS= read -r pass || true
    [ -n "$pass" ] || { EXTRA_JSON="\"diagnosis\": $DIAG_JSON"; die 3 false "the disk passphrase is needed to store the unlock key again" "nothing was changed"; }
    FORCE=1
    # the 'off' path in a subshell: on failure it has rolled back and printed its own JSON (passed through, its exit code kept)
    sub=$(printf '%s\n' "$pass" | cmd_off); rc=$?
    if [ "$rc" != 0 ]; then printf '%s\n' "$sub" | tail -1; exit "$rc"; fi
    log "repair: the 'off' path completed (key stored, start-up files rebuilt and proven)"
  else
    sub=$(cmd_on); rc=$?
    if [ "$rc" != 0 ]; then printf '%s\n' "$sub" | tail -1; exit "$rc"; fi
    log "repair: the 'on' path completed"
  fi
  if [ "$DIAG_GRUB_FAIL" = 1 ]; then
    if command -v update-grub >/dev/null 2>&1; then
      if update-grub >/dev/null 2>&1; then log "update-grub: ok (GRUB menu refreshed)"; else log "WARNING: update-grub failed"; fi
    else log "WARNING: GRUB's menu looked stale but update-grub is not available"; fi
  fi
  run_diagnose
  write_record "$DIAG_EXPECTED"
  log "diagnose after repair: expected=$DIAG_EXPECTED agrees=$DIAG_AGREES boot_risk=$DIAG_BOOT_RISK"
  EXTRA_JSON="\"diagnosis\": $DIAG_JSON"
  if [ "$DIAG_AGREES" = true ] && [ "$DIAG_BOOT_RISK" = false ]; then
    log "DONE: the start-up files match the setting again"
    if [ "$switch" = off ]; then emit true false "" "the unlock key is stored in the start-up files on $BOOT again; anyone who starts this computer can use it"
    else emit true true "" "asks for the disk password at start-up"; fi
  else
    emit false "$DIAG_EXPECTED" "the repair ran but the start-up files still do not match the setting: $(printf '%s' "$DIAG_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason") or "")' 2>/dev/null)" "see the diagnosis"
    exit 7
  fi
}

case "$ACTION" in
  status) shift; cmd_status;;
  off) shift; cmd_off "$@";;
  on) shift; cmd_on "$@";;
  diagnose) shift; cmd_diagnose "$@";;
  repair) shift; cmd_repair "$@";;
  *) echo "usage: disk_unlock.sh status | off (passphrase on stdin) | on | diagnose | repair (passphrase on stdin)" >&2; emit false null "usage: disk_unlock.sh status | off | on | diagnose | repair" ""; exit 2;;
esac
