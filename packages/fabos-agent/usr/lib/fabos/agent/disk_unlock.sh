#!/bin/bash
# @DISTRO_NAME@ disk_unlock.sh — the root helper behind the Start-up setting "Ask for the disk password when the computer starts".
#
#   disk_unlock.sh status        JSON: {encrypted, device, source, prompt_at_boot, keyfile_present, keyfile_in_initramfs, consistent, detail}
#   disk_unlock.sh off  < pass   stop asking: the current passphrase is read from STDIN (one line, never an argument)
#   disk_unlock.sh on            ask again (needs nothing)
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
# "on" reverses it in the SAFE order: config restored (key -> none, pattern dropped) -> initramfs rebuilt -> proven free of the
# keyfile -> only then the LUKS slot removed (luksRemoveKey with the keyfile) and the keyfile deleted. UMASK=0077 is kept
# (harmless, stricter). Every step is logged to /var/log/fabos/disk-unlock.log (root:adm 0640, the rootexec audit directory)
# and syslog authpriv (tag fabos-disk-unlock). The passphrase is never logged, never on a command line, never in a file.
#
# Exit codes: 0 ok · 2 usage / input · 3 the passphrase was not accepted · 4 not root · 5 not encrypted / no root crypt entry ·
# 6 a tool is missing · 7 the operation failed and was rolled back · 8 the operation failed and could NOT be fully reversed
# (the JSON says what is left to do). The last stdout line is always one JSON object {ok, action, prompt_at_boot, error?, detail?}.
#
# Test seams (tests/disk-unlock-test.sh runs this with stub cryptsetup/update-initramfs/lsinitramfs/findmnt on PATH):
#   FABOS_DU_CRYPTTAB FABOS_DU_KEYFILE FABOS_DU_CONF_HOOK FABOS_DU_INITRAMFS_CONF FABOS_DU_BOOT FABOS_DU_LOG FABOS_DU_ALLOW_NONROOT=1
set -u
umask 077

CRYPTTAB=${FABOS_DU_CRYPTTAB:-/etc/crypttab}
KEYFILE=${FABOS_DU_KEYFILE:-/etc/fabos/luks-unlock.key}
CONF_HOOK=${FABOS_DU_CONF_HOOK:-/etc/cryptsetup-initramfs/conf-hook}
INITRAMFS_CONF=${FABOS_DU_INITRAMFS_CONF:-/etc/initramfs-tools/initramfs.conf}
BOOT=${FABOS_DU_BOOT:-/boot}
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
  printf '{"ok": %s, "action": %s, "prompt_at_boot": %s, "error": %s, "detail": %s, "steps": [%s]}\n' \
    "$1" "$(json_str "$ACTION")" "$2" "$( [ -n "$3" ] && json_str "$3" || echo null )" "$( [ -n "$4" ] && json_str "$4" || echo null )" "$steps"
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
cleanup_backup() { [ -n "${BK:-}" ] && rm -rf "$BK"; }
trap cleanup_backup EXIT

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
  need_tools cryptsetup update-initramfs lsinitramfs findmnt
  find_root_entry || die 5 false "the root filesystem is not on an encrypted volume listed in $CRYPTTAB"
  local pass=""
  if [ -t 0 ]; then die 2 true "the passphrase must be piped on standard input (one line)"; fi
  IFS= read -r pass || [ -n "$pass" ] || die 2 true "no passphrase on standard input"
  [ -n "$pass" ] || die 2 true "the passphrase is empty"
  local dev; dev=$(crypt_device)
  log "root is $ROOT_SRC (crypttab entry $CT_NAME, source $CT_SRC, key ${CT_KEY}, options ${CT_OPTS:-none})"
  if [ "$CT_KEY" = "$KEYFILE" ] && [ -e "$KEYFILE" ] && keyfile_in_initramfs; then
    log "already off: the initramfs carries $KEYFILE"
    emit true false "" "already starts without asking"; return 0
  fi
  # the passphrase must open the volume before anything is touched (a wrong one changes nothing)
  if ! printf '%s\n' "$pass" | cryptsetup open --test-passphrase "$dev" >/dev/null 2>&1; then
    die 3 true "the passphrase was not accepted for $CT_NAME" "nothing was changed"
  fi
  log "passphrase verified against $dev"
  backup_configs true
  local slot_added=0 initramfs_touched=0
  rollback() {
    log "rolling back: $1"
    restore_configs
    if [ "$slot_added" = 1 ] && [ -f "$KEYFILE" ]; then
      if cryptsetup -q luksRemoveKey "$dev" "$KEYFILE" >/dev/null 2>&1; then log "key slot removed again"; else log "WARNING: could not remove the key slot; the keyfile is deleted so the slot is unusable"; fi
    fi
    rm -f "$KEYFILE"; log "keyfile deleted"
    if [ "$initramfs_touched" = 1 ]; then
      if update-initramfs -u -k all >/dev/null 2>&1; then log "initramfs rebuilt without the key"; else log "WARNING: update-initramfs failed during the rollback"; fi
    fi
  }
  # 1. keyfile (4096 random bytes, 0400 root) — on the encrypted root
  mkdir -p "$(dirname "$KEYFILE")" && chmod 0755 "$(dirname "$KEYFILE")" 2>/dev/null
  rm -f "$KEYFILE"
  if ! ( umask 077; head -c 4096 /dev/urandom > "$KEYFILE" ) || [ "$(stat -c %s "$KEYFILE" 2>/dev/null)" != 4096 ]; then
    rollback "could not write the keyfile"; die 7 true "could not create $KEYFILE"
  fi
  chmod 0400 "$KEYFILE"; chown root:root "$KEYFILE" 2>/dev/null || true
  log "keyfile created: $KEYFILE (4096 bytes, 0400)"
  # 2. the key slot (the passphrase authorises it — on cryptsetup's stdin, not its command line)
  if ! printf '%s\n' "$pass" | cryptsetup -q luksAddKey "$dev" "$KEYFILE" >/dev/null 2>&1; then
    rollback "luksAddKey failed"; die 7 true "cryptsetup could not add the key to $CT_NAME"
  fi
  slot_added=1; log "key slot added on $dev"
  # 3-5. crypttab / conf-hook / initramfs.conf
  write_crypttab "$KEYFILE" "$(add_opt "$CT_OPTS" initramfs)" || { rollback "crypttab write failed"; die 7 true "could not write $CRYPTTAB"; }
  log "crypttab: $CT_NAME key -> $KEYFILE, options $(add_opt "$CT_OPTS" initramfs)"
  set_pattern || { rollback "conf-hook write failed"; die 7 true "could not write $CONF_HOOK"; }
  log "conf-hook: KEYFILE_PATTERN=\"$KEYFILE\""
  set_umask || { rollback "initramfs.conf write failed"; die 7 true "could not write $INITRAMFS_CONF"; }
  log "initramfs.conf: UMASK=0077"
  # 6. rebuild and prove
  initramfs_touched=1
  local out
  if ! out=$(update-initramfs -u -k all 2>&1); then
    log "update-initramfs: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
    rollback "update-initramfs failed"; die 7 true "update-initramfs failed" "$(printf '%s' "$out" | tail -5)"
  fi
  log "update-initramfs -u -k all: ok"
  if ! keyfile_in_initramfs; then
    rollback "the keyfile is not inside the initramfs"; die 7 true "the rebuilt initramfs does not contain the unlock key (cryptroot/keyfiles/$CT_NAME.key)"
  fi
  log "verified: $(initrds) contains cryptroot/keyfiles/$CT_NAME.key"
  if ! cryptsetup open --test-passphrase --key-file "$KEYFILE" "$dev" >/dev/null 2>&1; then
    rollback "the keyfile does not open the volume"; die 7 true "the new key does not open $CT_NAME"
  fi
  log "verified: the keyfile opens $dev"
  log "DONE: the computer will start without asking for the disk password"
  emit true false "" "the unlock key is stored in the start-up files on $BOOT; anyone who starts this computer can use it"
}

# ---------------------------------------------------------------------------- on: ask again
cmd_on() {
  [ $# -eq 0 ] || die 2 null "on takes no arguments"
  [ "$IS_ROOT" = 1 ] || die 4 null "must run as root (through pkexec rootexec)"
  need_tools cryptsetup update-initramfs lsinitramfs findmnt
  find_root_entry || die 5 false "the root filesystem is not on an encrypted volume listed in $CRYPTTAB"
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
  log "verified: the initramfs no longer contains cryptroot/keyfiles/$CT_NAME.key"
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
  emit true true "" "asks for the disk password at start-up"
}

case "$ACTION" in
  status) shift; cmd_status;;
  off) shift; cmd_off "$@";;
  on) shift; cmd_on "$@";;
  *) echo "usage: disk_unlock.sh status | off (passphrase on stdin) | on" >&2; emit false null "usage: disk_unlock.sh status | off | on" ""; exit 2;;
esac
