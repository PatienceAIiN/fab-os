#!/usr/bin/env bash
# Offline audit of the Fab OS installer's Calamares job sequence, replayed inside the ISO image (podman, network OFF, no QEMU).
#   tests/calamares-jobs-test.sh [IMAGE]        default IMAGE = localhost/fabos:iso
# For every exec module of image/overlay/iso/etc/calamares/settings.conf the test (a) parses the WORKING-TREE config
# (yaml; key names against the module's own JSON schema shipped in the image, or against the key lists read from the
# Calamares 3.3.14 sources for the C++ modules without an installed schema), and (b) replays what the module would run in
# the target chroot, inside the image: apt-get -s remove of the try_remove list, every shellprocess line, systemctl
# enable/disable of every unit, locale-gen, update-initramfs -k all -c -t, and presence of every tool/file the other jobs
# rely on (grub-install + signed shim/GRUB + efibootmgr, cryptsetup-initramfs, sddm + theme + session, locales, console
# setup, the users.conf groups, NetworkManager). PASS/FAIL per check like tests/branding-check.sh; exit 1 on any FAIL.
# Output copy: build/calamares-jobs-test.out
set -uo pipefail; HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
TAG=${1:-localhost/fabos:iso}; CAL=$HERE/image/overlay/iso/etc/calamares; FB=$HERE/packages/fabos-firstboot; LIVE=$HERE/image/overlay/iso/usr/lib/fabos
mkdir -p build; OUT=build/calamares-jobs-test.out; : > "$OUT"; exec > >(tee -a "$OUT") 2>&1
pass=0; fail=0
chk(){ local o; if o=$(eval "$2" 2>&1); then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); printf '%s\n' "$o" | tail -n 6 | sed 's/^/      | /'; fi; }
podman image exists "$TAG" || { echo "FAIL  image $TAG not found (build it with scripts/build-rootfs.sh iso)"; exit 1; }
echo "### Calamares job audit — image $TAG — $(date -u +%FT%TZ)"

# ---------- host side: configuration files ----------
for f in "$CAL"/settings.conf "$CAL"/modules/*.conf "$CAL"/branding/fabos/branding.desc; do
  chk "yaml: $(basename "$f") parses" "python3 -c \"import yaml,sys; yaml.safe_load(open('$f'))\""
done
chk "settings: exec sequence has the fix-ups in order (bootloader -> shellprocess@efifallback -> umount)" \
  "python3 - <<'PY'
import yaml; s=yaml.safe_load(open('$CAL/settings.conf'))
ex=[x for st in s['sequence'] if 'exec' in st for x in st['exec']]
i=ex.index('bootloader'); assert ex[i+1]=='shellprocess@efifallback' and ex[i+2]=='umount', ex
assert ex.index('mount')<ex.index('unpackfs')<ex.index('users')<ex.index('shellprocess')<ex.index('initramfs')<ex.index('grubcfg')<ex.index('bootloader')
assert 'packages' in ex and ex.index('services-systemd')<ex.index('packages')
PY"
chk "settings: every instance id maps to an existing module config" \
  "python3 - <<'PY'
import yaml,os; s=yaml.safe_load(open('$CAL/settings.conf'))
for i in s.get('instances') or []: assert os.path.exists('$CAL/modules/'+i['config']), i
PY"
chk "settings: every exec module has a config file in the overlay (or needs none by design)" \
  "python3 - <<'PY'
import yaml,os; s=yaml.safe_load(open('$CAL/settings.conf'))
ex=[x.split('@')[0] for st in s['sequence'] if 'exec' in st for x in st['exec']]
noconf={'hwclock','localecfg','luksbootkeyfile','networkcfg','initramfscfg'}
inst={i['module']:i['config'] for i in (s.get('instances') or [])}
for m in ex:
    if m in noconf: continue
    assert os.path.exists('$CAL/modules/%s.conf'%m) or m in inst, m
PY"
# Key names against the schemas shipped in the image (jsonschema when available, else key/required check) + source-derived
# key lists for the C++ modules that ship no schema.
podman run --rm --network none "$TAG" sh -c 'for f in /usr/lib/x86_64-linux-gnu/calamares/modules/*/*.schema.yaml; do echo "=== $f"; cat "$f"; done' > build/calamares-schemas.txt 2>/dev/null
chk "schemas: module schemas extracted from the image" "grep -q '=== .*grubcfg.schema.yaml' build/calamares-schemas.txt"
chk "keys: every module config uses only keys its module knows (schema/source check)" \
  "python3 - <<'PY'
import yaml,re,sys
txt=open('build/calamares-schemas.txt').read(); schemas={}
for part in txt.split('=== ')[1:]:
    head,body=part.split('\n',1); name=head.strip().split('/')[-1].replace('.schema.yaml','')
    try: schemas[name]=yaml.safe_load(body)
    except Exception as e: print('bad schema',name,e)
try:
    import jsonschema
except Exception: jsonschema=None
# key lists read from the Calamares 3.3.14 sources (Config.cpp / *Job.cpp) for modules without an installed schema
src={'partition':{'efi','efiSystemPartition','efiSystemPartitionSize','efiSystemPartitionName','userSwapChoices','initialSwapChoice','luksGeneration','drawNestedPartitions','alwaysShowPartitionLabels','allowManualPartitioning','initialPartitioningChoice','defaultFileSystemType','availableFileSystemTypes','enableLuksAutomatedPartitioning','preCheckEncryption','showNotEncryptedBootMessage','requiredPartitionTableType','essentialMounts','lvm','partitionLayout','armInstall','allowZfsEncryption','requiredStorage','neverCreateSwap','ensureSuspendToDisk'},
     'initramfs':{'kernel','be_unsafe'},'machineid':{'systemd','systemd-style','dbus','dbus-symlink','entropy-copy','entropy-files','symlink','entropy'},
     'umount':{'emergency','srcLog','destLog'},'shellprocess':{'dontChroot','timeout','script','i18n'},'shellprocess-efifallback':{'dontChroot','timeout','script','i18n'},
     'finished':{'restartNowEnabled','restartNowChecked','restartNowCommand','restartNowMode','notifyOnFinished'},
     'welcome':{'showSupportUrl','showKnownIssuesUrl','showReleaseNotesUrl','showDonateUrl','requirements','geoip'},
     'users':{'defaultGroups','sudoersGroup','sudoersConfigureWithGroup','autologinGroup','setRootPassword','doReusePassword','doAutologin','passwordRequirements','allowWeakPasswords','allowWeakPasswordsDefault','user','hostname','allowActiveDirectory','presets'},
     'locale':{'region','zone','geoip','localeGenPath','adjustLiveTimezone'},'keyboard':{'xOrgConfFileName','convertedKeymapPath','writeEtcDefaultKeyboard','useLocale1','guessLayout'}}
import glob,os; bad=[]
for f in sorted(glob.glob('$CAL/modules/*.conf')):
    name=os.path.basename(f)[:-5]; cfg=yaml.safe_load(open(f)) or {}
    if name in schemas:
        sch=schemas[name]
        if jsonschema:
            try: jsonschema.validate(cfg, sch); print('  schema ok:',name)
            except Exception as e: bad.append((name,str(e).splitlines()[0]))
        else:
            props=set(sch.get('properties',{})); extra=set(cfg)-props
            if sch.get('additionalProperties') is False and extra: bad.append((name,'unknown keys %s'%extra))
            miss=set(sch.get('required',[]))-set(cfg)
            if miss: bad.append((name,'missing %s'%miss))
            print('  key check ok:',name)
    elif name in src:
        extra=set(cfg)-src[name]
        if extra: bad.append((name,'unknown keys %s'%extra))
        print('  source-key check ok:',name)
    else: print('  (no schema/key list for %s)'%name)
for b in bad: print('  BAD',b)
sys.exit(1 if bad else 0)
PY"
chk "partition.conf: layout = /boot (ext4, noEncrypt) + / ; erase preselected; encryption offered + pre-checked; LUKS2" \
  "python3 - <<'PY'
import yaml; c=yaml.safe_load(open('$CAL/modules/partition.conf'))
lay={p['mountPoint']:p for p in c['partitionLayout']}
assert lay['/boot']['noEncrypt'] is True and lay['/boot']['filesystem']=='ext4' and lay['/']['size']=='100%'
assert c['initialPartitioningChoice']=='erase' and c['enableLuksAutomatedPartitioning'] is True and c['preCheckEncryption'] is True
assert c['luksGeneration']=='luks2' and c['efi']['mountPoint']=='/boot/efi' and c['initialSwapChoice'] in c['userSwapChoices']
PY"
chk "grubcfg.conf: snake_case keys, defaults applied to the existing file (always_use_defaults), GRUB_TIMEOUT + GRUB_DEFAULT present" \
  "python3 -c \"import yaml; c=yaml.safe_load(open('$CAL/modules/grubcfg.conf')); assert c['always_use_defaults'] is True and c['overwrite'] is False and 'GRUB_TIMEOUT' in c['defaults'] and 'GRUB_DEFAULT' in c['defaults'] and all(k.startswith('GRUB_') for k in c['defaults'])\""
chk "bootloader.conf: efiBootloaderId is ubuntu (prefix of the signed GRUB), grub + efibootmgr, EFI fallback on" \
  "python3 -c \"import yaml; c=yaml.safe_load(open('$CAL/modules/bootloader.conf')); assert c['efiBootLoader']=='grub' and c['efiBootloaderId']=='ubuntu' and c['installEFIFallback'] is True and c['grubInstall']=='grub-install'\""
chk "packages.conf: apt backend, try_remove only, no update_db, not skipped offline" \
  "python3 -c \"import yaml; c=yaml.safe_load(open('$CAL/modules/packages.conf')); assert c['backend']=='apt' and c['update_db'] is False and c['skip_if_no_internet'] is False and all(list(o)==['try_remove'] for o in c['operations'])\""
chk "welcome.conf: RAM requirement within the README minimum (2 GB), internet check informative only, URL = Fab OS archive" \
  "python3 -c \"import yaml; c=yaml.safe_load(open('$CAL/modules/welcome.conf'))['requirements']; assert float(c['requiredRam'])<=1.9 and 'internet' not in c['required'] and c['internetCheckUrl'].startswith('https://fabos.patienceai.in/')\""
chk "unpackfs.conf: squashfs source path matches the ISO layout written by scripts/build-iso.sh (casper/filesystem.squashfs)" \
  "grep -q 'source: \"/cdrom/casper/filesystem.squashfs\"' $CAL/modules/unpackfs.conf && grep -q 'cp build/filesystem.squashfs \"\$T/casper/\"' scripts/build-iso.sh && ! grep -q exclude $CAL/modules/unpackfs.conf"
chk "live helpers: bash -n live-selftest.sh live-autoinstall.sh; selftest hands over to the autoinstall helper; unit points at the script" \
  "bash -n $LIVE/live-selftest.sh && bash -n $LIVE/live-autoinstall.sh && grep -q 'exec /usr/lib/fabos/live-autoinstall.sh' $LIVE/live-selftest.sh && grep -q 'ExecStart=/usr/lib/fabos/live-selftest.sh' $HERE/image/overlay/iso/usr/lib/systemd/system/fabos-live-selftest.service && test -x $LIVE/live-autoinstall.sh"
chk "firstboot: unit prints FABOS_INSTALLED_OK on ttyS0 after the script (ExecStartPost, guarded); firstboot.sh bash -n; no network needed to finish" \
  "grep -q 'ExecStartPost=/bin/sh -c \"echo FABOS_INSTALLED_OK >/dev/ttyS0 2>/dev/null || true\"' $FB/usr/lib/systemd/system/fabos-firstboot.service && bash -n $FB/usr/lib/fabos/firstboot.sh && grep -q 'nm-online -q -t 180' $FB/usr/lib/fabos/firstboot.sh"

# ---------- image side: replay the jobs offline (one container, --network none) ----------
INSIDE=build/calamares-jobs-inside.sh
cat > "$INSIDE" <<'INSIDE'
#!/bin/bash
# runs as root inside the ISO image with the working-tree /etc/calamares at /cal and no network
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
chk(){ local o; if o=$(eval "$2" 2>&1); then echo "PASS  $1"; else echo "FAIL  $1"; printf '%s\n' "$o" | tail -n 6 | sed 's/^/      | /'; fi; }
Y(){ python3 -c "import yaml,sys; d=yaml.safe_load(open('/cal/modules/$1.conf')) or {}; $2"; }
chk "offline: the replay container has no network (getent hosts archive.ubuntu.com fails)" "! getent hosts archive.ubuntu.com"
chk "offline: the image carries no apt package lists (the condition that broke the packages job on the owner's device)" "! ls /var/lib/apt/lists/*Packages 2>/dev/null | grep -q ."
# settings/modules
for m in $(python3 -c "import yaml; s=yaml.safe_load(open('/cal/settings.conf')); print(' '.join(sorted({x.split('@')[0] for st in s['sequence'] for k in st for x in st[k]})))"); do
  chk "module $m: installed in the image (module.desc)" "test -f /usr/lib/x86_64-linux-gnu/calamares/modules/$m/module.desc"; done
chk "calamares: 3.3.x binary present" "calamares --version 2>/dev/null | grep -q '^calamares 3\.3' || dpkg-query -W calamares | grep -q '3\.3\.'"
# partition
chk "partition: cryptsetup present (LUKS2 default format)" "cryptsetup --help 2>&1 | grep -q 'LUKS2 (for luksFormat'"
for fs in $(Y partition "print(' '.join(d['availableFileSystemTypes']))"); do chk "partition: mkfs.$fs available for the offered filesystem $fs" "command -v mkfs.$fs"; done
chk "partition: mkfs.vfat for the EFI system partition, partprobe for the new partition table" "command -v mkfs.vfat && command -v partprobe"
chk "partition: KPMcore helper service registered (polkit-free root path used by Calamares)" "ls /usr/share/dbus-1/system-services/ | grep -q kpmcore"
chk "partition: the layout root filesystem defers to the user's choice (filesystem: unknown) and /boot is 1-2 GiB" "Y partition \"lay={p['mountPoint']:p for p in d['partitionLayout']}; assert lay['/']['filesystem']=='unknown' and lay['/boot']['size'] in ('1G','1.5G','2G')\""
# mount
chk "mount: extraMounts cover /proc /sys /dev /run /run/udev and efivarfs (grub-install + efibootmgr need them)" "Y mount \"mp={m['mountPoint'] for m in d['extraMounts']}; assert {'/proc','/sys','/dev','/run','/run/udev','/sys/firmware/efi/efivars'} <= mp\""
# unpackfs
chk "unpackfs: rsync + unsquashfs present (file count + copy)" "command -v rsync && command -v unsquashfs"
chk "unpackfs: global user units (fabos-agent, fabos-voiced, fabos-llama.socket) are plain symlinks in the rootfs that unpackfs copies 1:1" \
  "test -L /etc/systemd/user/default.target.wants/fabos-agent.service && test -L /etc/systemd/user/default.target.wants/fabos-voiced.service && test -L /etc/systemd/user/sockets.target.wants/fabos-llama.socket"
# machineid
chk "machineid: systemd-machine-id-setup + dbus-uuidgen present; config enables both + the dbus symlink" "command -v systemd-machine-id-setup && command -v dbus-uuidgen && Y machineid \"assert d['systemd'] and d['dbus'] and d['dbus-symlink']\""
# fstab
chk "fstab: crypttabOptions luks + tmpOptions (3.3 schema); per-filesystem mount options live in mount.conf (efi umask=0077)" "Y fstab \"assert d['crypttabOptions']=='luks' and 'default' in d['tmpOptions']\" && Y mount \"assert any(m['filesystem']=='efi' and 'umask=0077' in m['options'] for m in d['mountOptions'])\""
# locale / localecfg / keyboard
chk "locale: configured zone exists in the target (zoneinfo)" "z=\$(Y locale \"print(d['region']+'/'+d['zone'])\"); test -f /usr/share/zoneinfo/\$z"
chk "localecfg: locales + locale-gen + /usr/share/i18n/SUPPORTED with en_US.UTF-8 and en_IN" "command -v locale-gen && grep -q '^en_US.UTF-8 UTF-8' /usr/share/i18n/SUPPORTED && grep -q '^en_IN UTF-8' /usr/share/i18n/SUPPORTED"
chk "localecfg: replay locale-gen offline (the module runs it in the target)" "locale-gen"
chk "keyboard: console-setup + xkb rules (setupcon, /usr/share/X11/xkb/rules/base.lst), /etc/default/keyboard present" "command -v setupcon && test -f /usr/share/X11/xkb/rules/base.lst && test -f /etc/default/keyboard"
# luksbootkeyfile / initramfscfg
chk "luksbootkeyfile: cryptsetup-initramfs installed (the initramfs unlocks / at boot)" "dpkg -s cryptsetup-initramfs | grep -q 'Status: install ok installed'"
chk "initramfscfg: hooks directory + the module's encrypt_hook_nokey (used when /boot is unencrypted)" "test -d /usr/share/initramfs-tools/hooks && test -f /usr/lib/x86_64-linux-gnu/calamares/modules/initramfscfg/encrypt_hook_nokey"
# users
for g in $(Y users "print(' '.join(x['name'] if isinstance(x,dict) else x for x in d['defaultGroups']))"); do chk "users: default group '$g' exists (getent group)" "getent group $g"; done
chk "users: sudoers group exists; useradd/usermod/chpasswd present; live user name forbidden" "getent group \$(Y users \"print(d['sudoersGroup'])\") && command -v useradd && command -v usermod && command -v chpasswd && Y users \"assert 'fabos' in d['user']['forbidden_names']\""
# displaymanager
chk "displaymanager: sddm installed and enabled" "command -v sddm && systemctl is-enabled sddm"
chk "displaymanager: the configured SDDM theme exists (Current=... in /etc/sddm.conf.d)" "t=\$(sed -n 's/^Current=//p' /etc/sddm.conf.d/zz-fabos.conf | head -1); test -f /usr/share/sddm/themes/\$t/Main.qml"
chk "displaymanager: configured session executable + desktop file exist in SDDM's SessionDir" "exe=\$(Y displaymanager \"print(d['defaultDesktopEnvironment']['executable'])\"); df=\$(Y displaymanager \"print(d['defaultDesktopEnvironment']['desktopFile'])\"); sd=\$(sed -n 's/^SessionDir=//p' /etc/sddm.conf.d/zz-fabos.conf | head -1); command -v \$exe && test -f \$sd/\$df.desktop"
# networkcfg / hwclock
chk "networkcfg: NetworkManager installed, system-connections dir present" "command -v NetworkManager && test -d /etc/NetworkManager/system-connections"
chk "hwclock: hwclock binary present (module ignores RTC errors)" "command -v hwclock"
# services-systemd
chk "services-systemd: 'units' is the flat 3.3 list (name/action/mandatory), not the old enable:/disable: map" "Y services-systemd \"assert isinstance(d['units'], list) and all(set(u) <= {'name','action','mandatory'} and 'name' in u for u in d['units'])\""
for u in $(Y services-systemd "print(' '.join(x['name'] for x in d['units']))"); do chk "services-systemd: unit $u exists in the image" "systemctl cat $u"; done
for u in $(Y services-systemd "print(' '.join(x['name'] for x in d['units'] if x.get('action','enable')=='enable'))"); do chk "services-systemd: replay 'systemctl enable $u' offline" "systemctl enable $u"; done
for u in $(Y services-systemd "print(' '.join(x['name'] for x in d['units'] if x.get('action')=='disable'))"); do chk "services-systemd: replay 'systemctl disable $u' offline" "systemctl disable $u"; done
# packages
pk=$(Y packages "print(' '.join(p for o in d['operations'] for p in o['try_remove']))")
for p in $pk; do chk "packages: '$p' is really installed (an unknown name makes apt fail hard without lists)" "dpkg -s $p | grep -q 'Status: install ok installed'"; done
chk "packages: replay 'apt-get -s -q -y --purge remove $pk' offline exits 0" "apt-get -s -q -y --purge remove $pk"
chk "packages: the dry run removes only the listed packages (no fabos-*, plasma or kwin package goes with them)" "apt-get -s -q -y --purge remove $pk 2>/dev/null | grep -E '^(Remv|Purg)' | grep -v -E '^(Remv|Purg) ($(echo $pk | sed 's/ /|/g')) ' | wc -l | grep -qx 0"
for p in $pk; do chk "packages: try_remove of '$p' alone also succeeds (Calamares removes per package on the try_ path)" "apt-get -s -q -y --purge remove $p"; done
# shellprocess: replay every line as Calamares does (sh -c, in the target)
n=0; while IFS= read -r line; do n=$((n+1)); chk "shellprocess: line $n exits 0 offline" "sh -c \"\$(printf %s \"\$line\")\""; done < <(Y shellprocess "[print(l) for l in d['script']]")
chk "shellprocess: after the replay the live user, casper.conf, live sudo rule and live autologin are gone" "! id fabos && ! test -e /etc/casper.conf && ! test -e /etc/sudoers.d/fabos-live && ! test -e /etc/sddm.conf.d/20-autologin-live.conf"
chk "shellprocess: after the replay the post-install units are enabled and the live-only ones disabled" "systemctl is-enabled fabos-firstboot.service fabos-update-check.timer fabos-feedback.socket unattended-upgrades.service | grep -vq disabled && ! systemctl is-enabled serial-getty@ttyS0.service 2>/dev/null | grep -q '^enabled' && ! systemctl is-enabled fabos-live-selftest.service 2>/dev/null | grep -q '^enabled'"
chk "shellprocess: install stamp written" "test -s /etc/fabos/installed-at"
n=0; while IFS= read -r line; do n=$((n+1)); chk "shellprocess@efifallback: line $n exits 0 offline (no ESP here: logs and leaves things alone)" "sh -c \"\$(printf %s \"\$line\")\""; done < <(Y shellprocess-efifallback "[print(l) for l in d['script']]")
chk "shellprocess@efifallback: signed shim + signed GRUB present for the EFI/boot fallback" "test -f /usr/lib/shim/shimx64.efi.signed && test -f /usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"
# initramfs
chk "initramfs: replay 'update-initramfs -k all -c -t' offline (what the module runs)" "update-initramfs -k all -c -t"
chk "initramfs: the rebuilt initramfs contains cryptroot (LUKS unlock) and the Fab OS Plymouth theme (passphrase prompt)" "f=\$(ls /boot/initrd.img-* | head -1); lsinitramfs \$f | grep -q scripts/local-top/cryptroot && lsinitramfs \$f | grep -q themes/fabos/fabos.script"
chk "initramfs: Plymouth theme implements the password prompt (SetDisplayPasswordFunction)" "grep -q SetDisplayPasswordFunction /usr/share/plymouth/themes/fabos/fabos.script"
# grubcfg / bootloader
chk "grubcfg: /etc/default/grub exists in the target and is valid shell" "test -f /etc/default/grub && sh -n /etc/default/grub"
chk "bootloader: grub-install, grub-mkconfig, grub-probe, efibootmgr present" "command -v grub-install && command -v grub-mkconfig && command -v grub-probe && command -v efibootmgr"
chk "bootloader: grub-efi-amd64-signed + shim-signed installed (Secure Boot chain)" "dpkg -s grub-efi-amd64-signed | grep -q 'install ok installed' && dpkg -s shim-signed | grep -q 'install ok installed'"
chk "bootloader: the signed GRUB's embedded prefix is /EFI/ubuntu = efiBootloaderId (id 'fabos' would leave GRUB without its grub.cfg)" \
  "python3 -c \"import re; d=open('/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed','rb').read(); assert b'/EFI/ubuntu' in d and b'cmdpath' not in d\" && Y bootloader \"assert d['efiBootloaderId']=='ubuntu'\""
chk "bootloader: the signed GRUB has no argon2 module -> an encrypted /boot (LUKS2 argon2id) could not be opened by GRUB; layout keeps /boot outside LUKS" \
  "python3 -c \"d=open('/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed','rb').read(); assert b'argon2' not in d and b'cryptodisk' in d\" && Y partition \"assert [p for p in d['partitionLayout'] if p['mountPoint']=='/boot'][0]['noEncrypt']\""
chk "bootloader: grub-mkconfig's os-prober present; GRUB font for the menu present" "command -v os-prober && test -f /boot/grub/unicode.pf2"
# umount / finished
chk "umount: emergency job (runs even after a failure)" "Y umount \"assert d['emergency'] is True\""
chk "finished: restart command binary exists" "c=\$(Y finished \"print(d['restartNowCommand'].split()[0])\"); command -v \$c"
# branding
chk "branding: images referenced by branding.desc exist in the image" "for i in logo.png wordmark.png slide.png; do test -f /etc/calamares/branding/fabos/\$i || exit 1; done; test -f /etc/calamares/branding/fabos/show.qml"
# post-install hardening evidence
chk "hardening: Fab OS apt source + archive keyring shipped by fabos-branding" "test -f /etc/apt/sources.list.d/fabos.sources && test -f /usr/share/keyrings/fabos-archive-keyring.gpg && grep -q 'Signed-By: /usr/share/keyrings/fabos-archive-keyring.gpg' /etc/apt/sources.list.d/fabos.sources"
chk "hardening: unattended-upgrades enabled with the Fab OS origins (52fabos-unattended, 20auto-upgrades)" "systemctl is-enabled unattended-upgrades && grep -q 'Patience AI:loom' /etc/apt/apt.conf.d/52fabos-unattended && grep -q 'Unattended-Upgrade \"1\"' /etc/apt/apt.conf.d/20auto-upgrades"
chk "hardening: ufw enabled in the image (survives the copy)" "grep -q '^ENABLED=yes' /etc/ufw/ufw.conf && systemctl is-enabled ufw"
chk "firstboot: unit file (working tree, placeholders filled) passes systemd-analyze verify" "sed 's/@DISTRO_NAME@/Fab OS/' /fb/usr/lib/systemd/system/fabos-firstboot.service > /run/fabos-firstboot.service && systemd-analyze verify /run/fabos-firstboot.service"
INSIDE
podman run --rm --network none -v "$CAL:/cal:ro,Z" -v "$FB:/fb:ro,Z" -v "$HERE/$INSIDE:/inside.sh:ro,Z" "$TAG" /bin/bash /inside.sh 2>&1 | tee build/calamares-jobs-inside.out | grep -E '^(PASS|FAIL)  ' || true
ip=$(grep -c '^PASS  ' build/calamares-jobs-inside.out); if_=$(grep -c '^FAIL  ' build/calamares-jobs-inside.out); pass=$((pass+ip)); fail=$((fail+if_))
echo "### jobs-test: pass=$pass fail=$fail (details: $OUT, container log: build/calamares-jobs-inside.out)"
[ $fail = 0 ]
