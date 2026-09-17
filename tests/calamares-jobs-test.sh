#!/usr/bin/env bash
# Offline audit of the Fab OS installer's Calamares job sequence, replayed inside the ISO image (podman, network OFF, no QEMU).
#   tests/calamares-jobs-test.sh [IMAGE]        default IMAGE = localhost/fabos:iso
# For every exec module of image/overlay/iso/etc/calamares/settings.conf the test (a) parses the WORKING-TREE config
# (yaml; key names against the module's own JSON schema shipped in the image, or against the key lists read from the
# Calamares 3.3.14 sources for the C++ modules without an installed schema), and (b) replays what the module would run in
# the target chroot, inside the image: apt-get -s remove of the try_remove list, every shellprocess line, systemctl
# enable/disable of every unit, locale-gen, update-initramfs -k all -c -t, and presence of every tool/file the other jobs
# rely on (grub-install + signed shim/GRUB + efibootmgr, cryptsetup-initramfs, sddm + theme + session, locales, console
# setup, the users.conf groups, NetworkManager), plus the branding.desc style keys against libcalamaresui's StyleEntry names
# (the black-sidebar cause) and the install driver's "Encrypt system" detector on fixture screenshots. PASS/FAIL per check like
# tests/branding-check.sh; exit 1 on any FAIL.
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
chk "shellprocess*: no '\$' in any command line (Calamares expands \$name/\${name} itself and aborts with 'Missing variables' - the round-6 install failure)" \
  "python3 -c \"import yaml,glob,sys; bad=[(f,l) for f in glob.glob('$CAL/modules/shellprocess*.conf') for l in (yaml.safe_load(open(f)) or {}).get('script',[]) if '\\$' in str(l)]; print(bad); sys.exit(1 if bad else 0)\""
chk "shellprocess*: every command line is a shipped, executable script under image/overlay/iso/usr/lib/fabos" \
  "python3 -c \"import yaml,glob,os,sys; ls=[str(l) for f in glob.glob('$CAL/modules/shellprocess*.conf') for l in (yaml.safe_load(open(f)) or {}).get('script',[])]; bad=[l for l in ls if not (l.startswith('/usr/lib/fabos/') and os.access('$LIVE/'+os.path.basename(l), os.X_OK))]; print(ls, bad); sys.exit(1 if bad or not ls else 0)\""
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
chk "partition.conf: layout = /boot (ext4, noEncrypt) + / ; erase preselected; encryption offered but OPT-IN (box starts unticked, owner's decision 2026-09-17); LUKS2" \
  "python3 - <<'PY'
import yaml; c=yaml.safe_load(open('$CAL/modules/partition.conf'))
lay={p['mountPoint']:p for p in c['partitionLayout']}
assert lay['/boot']['noEncrypt'] is True and lay['/boot']['filesystem']=='ext4' and lay['/']['size']=='100%'
assert c['initialPartitioningChoice']=='erase' and c['enableLuksAutomatedPartitioning'] is True and c['preCheckEncryption'] is False
assert c['luksGeneration']=='luks2' and c['efi']['mountPoint']=='/boot/efi' and c['initialSwapChoice'] in c['userSwapChoices']
PY"
chk "branding.desc: sidebar style keys are the Calamares 3.3 Branding::StyleEntry names (SidebarBackground, SidebarText, SidebarTextCurrent, SidebarBackgroundCurrent), all four #rrggbb, text contrasts with its background" \
  "python3 - <<'PY'
import yaml,re; st=yaml.safe_load(open('$CAL/branding/fabos/branding.desc'))['style']
assert set(st)=={'SidebarBackground','SidebarText','SidebarTextCurrent','SidebarBackgroundCurrent'}, set(st)   # the 3.2 spellings are rejected by 3.3 -> black sidebar
assert all(re.fullmatch(r'#[0-9A-Fa-f]{6}', str(v)) for v in st.values()), st
def lum(c): r,g,b=(int(c[i:i+2],16) for i in (1,3,5)); return (299*r+587*g+114*b)/1000
assert abs(lum(st['SidebarText'])-lum(st['SidebarBackground']))>100 and abs(lum(st['SidebarTextCurrent'])-lum(st['SidebarBackgroundCurrent']))>60, st
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
# live-autoinstall.sh in library mode (FABOS_AUTOINSTALL_LIB=1): the result classifier and the serial log transport, on
# synthetic logs built from the exact strings Calamares 3.3.14 logs (the image-side part below proves those strings are
# in THIS image's binaries). The classifier bug this guards against: waiting for "completion: succeeded" alone, which the
# finished module only logs when it can reach org.freedesktop.Notifications - never as root on the live user's bus.
HT=build/autoinstall-helper-test; rm -rf "$HT"; mkdir -p "$HT"
cat > "$HT/run.sh" <<'HT_SH'
#!/bin/bash
set -u; HT=$1; LIVE=$2; export FABOS_AUTOINSTALL_LIB=1 FABOS_AUTOINSTALL_OUT=$HT/serial.txt; rm -f "$HT/serial.txt"
. "$LIVE/live-autoinstall.sh"
printf '%s\n' '10:00:01 [6]: void Calamares::JobThread::run()' '    Starting job "Create partition table" ( 1 / 22 )' '    Starting job "Unmount file systems" ( 22 / 22 )' \
  '10:20:00 [2]: Could not get dbus interface for notifications at end of installation. QDBusError("org.freedesktop.DBus.Error.NoServer", "Not connected to D-Bus server")' > "$HT/ok-root.log"
printf '%s\n' '    Starting job "a" ( 1 / 3 )' '    Starting job "b" ( 2 / 3 )' '    Starting job "c" ( 3 / 3 )' '10:30:00 [6]: Sending notification of completion: succeeded' > "$HT/ok-notify.log"
printf '%s\n' '    Starting job "a" ( 1 / 2 )' '    Starting job "b" ( 2 / 2 )' '10:30:00 [6]: Notification not sent; completion: succeeded' > "$HT/ok-quiet.log"
printf '%s\n' '    Starting job "Create partition table" ( 1 / 22 )' '    Starting job "Remove packages" ( 15 / 22 )' '10:10:00 [1]: void Calamares::ViewManager::onInstallationFailed(const QString&, const QString&)' \
  '    Installation failed: Package Manager error' '    ..  - message: "Package Manager error"' '    Starting EMERGENCY JOB "Unmount file systems" ( 22 / 22 )' '10:10:30 [6]: Sending notification of completion: failed' > "$HT/failed.log"
printf '%s\n' '    Starting job "Create partition table" ( 1 / 22 )' '    Starting job "Unpack file systems" ( 5 / 22 )' > "$HT/running.log"
: > "$HT/empty.log"
classify_log "$HT/ok-root.log";   [ "$VERDICT $JOBS_STARTED $JOBS_TOTAL" = "ok 2 22" ]      || { echo "ok-root -> $VERDICT $JOBS_STARTED $JOBS_TOTAL"; exit 1; }
classify_log "$HT/ok-notify.log"; [ "$VERDICT $JOBS_STARTED $JOBS_TOTAL" = "ok 3 3" ]       || { echo "ok-notify -> $VERDICT $JOBS_STARTED $JOBS_TOTAL"; exit 1; }
classify_log "$HT/ok-quiet.log";  [ "$VERDICT $JOBS_STARTED $JOBS_TOTAL" = "ok 2 2" ]       || { echo "ok-quiet -> $VERDICT $JOBS_STARTED $JOBS_TOTAL"; exit 1; }
classify_log "$HT/failed.log";    [ "$VERDICT $JOBS_STARTED $JOBS_TOTAL" = "failed 3 22" ]  || { echo "failed -> $VERDICT $JOBS_STARTED $JOBS_TOTAL"; exit 1; }
classify_log "$HT/running.log";   [ "$VERDICT $JOBS_STARTED $JOBS_TOTAL" = "running 2 22" ] || { echo "running -> $VERDICT $JOBS_STARTED $JOBS_TOTAL"; exit 1; }
classify_log "$HT/empty.log";     [ "$VERDICT $JOBS_STARTED $JOBS_TOTAL" = "running 0 0" ]  || { echo "empty -> $VERDICT $JOBS_STARTED $JOBS_TOTAL"; exit 1; }
classify_log "$HT/nosuch.log";    [ "$VERDICT" = running ] || { echo "missing -> $VERDICT"; exit 1; }
[ "$(grep -E "$JOB_RX" "$HT/failed.log" | sed -E "s/^.*$JOB_RX/CALAMARES_JOB:/" | wc -l)" = 3 ] || { echo "job echo"; exit 1; }
# transport: a 2.5 MB log that compresses (like a real -D6 log) -> whole file; an incompressible one -> capped tail
{ for i in $(seq 1 30000); do echo "10:0$((i%10)):00 [6]: Starting job \"Job number $i\" ( $i / 30000 ) with some repeated text about the target env call"; done; } > "$HT/big.log"
head -c 400000 /dev/urandom | base64 -w 76 > "$HT/random.log"
dump_log "$HT/big.log" CALAMARES_LOG; dump_log "$HT/random.log" CAPPED 120000; dump_log "$HT/nosuch.log" NOLOG; say "AUTOINSTALL_END"
grep -q '^CALAMARES_LOG_BEGIN encoding=gzip+base64 bytes=' "$HT/serial.txt" && grep -q '^CAPPED_BEGIN encoding=gzip+base64 .*kept=' "$HT/serial.txt" && grep -q '^NOLOG_BEGIN encoding=none' "$HT/serial.txt"
HT_SH
cat > "$HT/decode.py" <<'HT_PY'
import importlib.util, os, sys
HT, DRV = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("drv", DRV); drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)
ser = drv.Serial(os.path.join(HT, "serial.txt")); ser.poll(); L = ser.lines
def block(label):
    i = max(i for i, l in enumerate(L) if l.startswith(label + "_BEGIN")); j = next(k for k in range(i + 1, len(L)) if L[k].startswith(label + "_END")); return L[i], L[i + 1:j]
h, b = block("CALAMARES_LOG"); text, status, kv = drv.decode_log_block(h, b)
assert status == "ok" and text.encode() == open(os.path.join(HT, "big.log"), "rb").read(), (status, len(text), kv)
print("  full log: %s bytes -> %s gz -> %d serial lines -> identical, sha256 %s" % (kv["bytes"], kv["gz"], len(b), status))
h, b = block("CAPPED"); text, status, kv = drv.decode_log_block(h, b); kept = int(kv["kept"])
assert status.startswith("ok (newest") and kept < int(kv["bytes"]) and int(kv["gz"]) <= 120000 and text.encode() == open(os.path.join(HT, "random.log"), "rb").read()[-kept:], (status, kv)
print("  capped log: kept %d of %s bytes, gz %s <= cap 120000, tail identical" % (kept, kv["bytes"], kv["gz"]))
b2 = list(b); b2[3] = "[  123.456] EXT4-fs (vda3): unmounted filesystem"; b2[4] = "AAAA" + b2[4][4:]
status2 = drv.decode_log_block(h, b2)[1]; assert "mismatch" in status2 or "error" in status2, status2
print("  corrupted stream -> reported: %s" % status2[:70])
assert drv.decode_log_block("CALAMARES_LOG_BEGIN", ["x", "y"])[1] == "plain" and drv.decode_log_block("CALAMARES_LOG_BEGIN", [])[1] == "empty"
h, b = block("NOLOG"); assert drv.decode_log_block(h, b)[1] == "plain"
HT_PY
chk "live-autoinstall: classify_log -> ok on each of the three finished-page (doNotify) lines incl. the dbus warning root always gets, failed on 'Installation failed:' / '- message:', running otherwise; job counters n/m; dump_log writes gzip+base64 blocks" \
  "bash $HT/run.sh $HERE/$HT $LIVE"
chk "live-autoinstall: dump_log -> install-vm-driver.py decode_log_block round trip is byte-identical with the sha256 verified (whole log; capped tail for an oversized one); a corrupted stream is reported, not raised" \
  "python3 $HT/decode.py $HERE/$HT $HERE/tests/install-vm-driver.py"
chk "live-autoinstall: the helper never waits for 'completion: succeeded' alone (unreachable as root) and reports finished_page + AUTOINSTALL_JOBS" \
  "! grep -q \"grep -q 'completion: succeeded'\" $LIVE/live-autoinstall.sh && grep -q 'finished_page=\$FINISHED_PAGE' $LIVE/live-autoinstall.sh && grep -q 'AUTOINSTALL_JOBS started=' $LIVE/live-autoinstall.sh && grep -q 'dmesg -n 1' $LIVE/live-autoinstall.sh"
chk "install-vm.sh: verdicts cover finished page, all jobs started and an intact (sha256-verified) session log; bash -n" \
  "bash -n tests/install-vm.sh && grep -q 'finished_page=yes' tests/install-vm.sh && grep -q 'AUTOINSTALL_JOBS' tests/install-vm.sh && grep -q 'SESSION_LOG_DECODE=ok' tests/install-vm.sh && python3 -m py_compile tests/install-vm-driver.py"
# the partition page: the driver reads whether "Encrypt system" is ticked from the screendump and clicks only when the variant
# needs the other state, so it works with the 1.0 ISO (pre-ticked) and the tree (opt-in). The detector is replayed on crops of
# real 1280x800 partition-page screendumps (tests/fixtures/installer/, from the 2026-09-16 runs); OCR runs inside the image.
chk "install-vm-driver.py: the partition page is state-aware for both variants (encrypt_state from the screendump; luks ticks, plain unticks); installer-ui-vm.sh bash -n + executable" \
  "grep -q 'want = \"ticked\" if a.variant == \"luks\" else \"unticked\"' tests/install-vm-driver.py && grep -q '^def encrypt_state' tests/install-vm-driver.py && bash -n tests/installer-ui-vm.sh && test -x tests/installer-ui-vm.sh"
for fx in ticked:ticked ticked-typed:ticked unticked:unticked; do
  chk "install-vm-driver.py encrypt-state: fixture partition-${fx%%:*}.png (crop of a real partition-page screendump) -> ${fx##*:}" \
    "python3 -c 'import PIL' && python3 tests/install-vm-driver.py encrypt-state --shot tests/fixtures/installer/partition-${fx%%:*}.png --expect ${fx##*:} --outdir build/encrypt-state --image $TAG 2>/dev/null"
done
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
# live-autoinstall.sh: every string its log classifier greps for must be a string of THIS image's Calamares binaries
# (finished module: Config::doNotify; libcalamaresui: ViewManager::onInstallationFailed; libcalamares: JobThread::run)
FABOS_AUTOINSTALL_LIB=1 FABOS_AUTOINSTALL_OUT=/run/autoinstall-lib.out . /live/live-autoinstall.sh
FIN=$(ls /usr/lib/x86_64-linux-gnu/calamares/modules/finished/libcalamares_viewmodule_finished.so 2>/dev/null | head -1); UI=$(ls /usr/lib/x86_64-linux-gnu/libcalamaresui.so.3.3* 2>/dev/null | head -1); CORE=$(ls /usr/lib/x86_64-linux-gnu/libcalamares.so.3.3* 2>/dev/null | head -1)
IFS='|' read -r -a alts <<< "$FINISHED_RX"; for s in "${alts[@]}"; do chk "live-autoinstall: finished-page marker '$s' is a string of the finished module ($FIN)" "grep -q -a -F -e '$s' '$FIN'"; done
IFS='|' read -r -a alts <<< "$FAIL_RX"; for s in "${alts[@]}"; do chk "live-autoinstall: failure marker '$s' is a string of libcalamaresui" "grep -q -a -F -e '$s' '$UI'"; done
chk "live-autoinstall: job markers 'Starting' + 'EMERGENCY JOB' are strings of libcalamares" "grep -q -a -F -e 'Starting' '$CORE' && grep -q -a -F -e 'EMERGENCY JOB' '$CORE'"
chk "live-autoinstall: the success marker is not 'completion: succeeded' alone - root is refused on a uid-1000 session bus (dbus-daemon, no <allow user> rule) so the finished module logs the dbus warning instead (replayed: bus as fabos accepts fabos, refuses root)" \
  "mkdir -p /run/fabos-rt && chown fabos /run/fabos-rt && chmod 700 /run/fabos-rt && A=\$(runuser -u fabos -- env XDG_RUNTIME_DIR=/run/fabos-rt dbus-daemon --session --fork --print-address --address=unix:path=/run/fabos-rt/bus | head -1) && sleep 1 && runuser -u fabos -- env DBUS_SESSION_BUS_ADDRESS=\"\$A\" dbus-send --session --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null && ! DBUS_SESSION_BUS_ADDRESS=\"\$A\" timeout 20 dbus-send --session --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; r=\$?; pkill -u fabos dbus-daemon; [ \$r = 0 ]"
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
# PMApt.remove (packages/main.py) runs 'apt-get --purge -q -y autoremove' right after the removal: replay that too
chk "packages: replay the backend's second step, 'apt-get --purge -q -y autoremove' after the removal, offline exits 0" "apt-get -s -q -y --purge --autoremove remove $pk"
auto_purged=$(apt-get -s -q -y --purge --autoremove remove $pk 2>/dev/null | sed -n -E 's/^(Remv|Purg) ([^ ]+).*/\2/p' | tr '\n' ' ')
echo "      | remove + autoremove would purge: $auto_purged"
chk "packages: the autoremove takes nothing the installed system needs (no cryptsetup*/grub*/shim*/efibootmgr/plymouth*/sddm*/network-manager/fabos-*/linux-*/initramfs-tools/systemd*/plasma*/kwin*/dbus*/udev)" \
  "! printf '%s\n' $auto_purged | grep -E -q '^(cryptsetup|grub|shim|efibootmgr|plymouth|sddm|network-manager|fabos-|linux-|initramfs-tools|systemd|plasma|kwin|dbus|udev)'"
chk "packages: every package the autoremove would purge is apt-mark auto (pulled in by casper/calamares only), never a manually installed one" \
  "apt-mark showauto > /run/auto.lst; for p in $auto_purged; do case \" $pk \" in *\" \$p \"*) continue;; esac; grep -qx \"\$p\" /run/auto.lst || { echo \"\$p is not auto\"; exit 1; }; done"
# shellprocess: the conf lines are paths of shipped scripts (Calamares expands $name in the lines itself); stage the working-tree
# copies where the lines expect them, then replay every line as Calamares does (sh -c, in the target)
install -m755 /live/install-finish.sh /live/install-efi-fallback.sh /usr/lib/fabos/
chk "shellprocess: both scripts parse (sh -n) and are executable in the target" "sh -n /usr/lib/fabos/install-finish.sh && sh -n /usr/lib/fabos/install-efi-fallback.sh && test -x /usr/lib/fabos/install-finish.sh && test -x /usr/lib/fabos/install-efi-fallback.sh"
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
# grub-mkconfig sources /etc/default/grub and THEN /etc/default/grub.d/*.cfg, so fabos-branding's fabos.cfg (GRUB_TIMEOUT,
# GRUB_TIMEOUT_STYLE, GRUB_CMDLINE_LINUX_DEFAULT, GRUB_DISTRIBUTOR) is what the generated grub.cfg gets - whatever the
# grubcfg module wrote into /etc/default/grub. Replay: apply the module's edit to a copy, source both like grub-mkconfig.
chk "grubcfg: grub-mkconfig sources /etc/default/grub first and /etc/default/grub.d/*.cfg after it (the snippet wins)" \
  "a=\$(grep -n -E '^\s*\.\s+\\\$\{sysconfdir\}/default/grub\s*\$' /usr/sbin/grub-mkconfig | head -1 | cut -d: -f1); b=\$(grep -n -F 'default/grub.d/*.cfg' /usr/sbin/grub-mkconfig | head -1 | cut -d: -f1); [ -n \"\$a\" ] && [ -n \"\$b\" ] && [ \"\$a\" -lt \"\$b\" ]"
cat > /run/grubcfg-sim.sh <<'SIM'
cp /etc/default/grub /run/grub.sim
python3 - <<'PY'
import yaml, re
c = yaml.safe_load(open('/cal/modules/grubcfg.conf'))
items = {k: ("'true'" if v is True else "'false'" if v is False else "'%s'" % v) for k, v in c['defaults'].items()}   # always_use_defaults
# modify_grub_default() for a LUKS root with initramfs-tools (no dracut/mkinitcpio): kernel_params + cryptdevice/root + splash
items['GRUB_CMDLINE_LINUX_DEFAULT'] = "'%s cryptdevice=UUID=0000:luks-0000 root=/dev/mapper/luks-0000 splash'" % ' '.join(c.get('kernel_params', ['quiet']))
items['GRUB_DISTRIBUTOR'] = "'Fab OS'"
out = []; seen = set()
for l in open('/run/grub.sim').read().splitlines():
    m = re.match(r'^(GRUB_[A-Z_]+)=', l)
    if m and m.group(1) in items: out.append('%s=%s' % (m.group(1), items[m.group(1)])); seen.add(m.group(1))
    else: out.append(l)
out += ['%s=%s' % (k, v) for k, v in items.items() if k not in seen]
open('/run/grub.sim', 'w').write('\n'.join(out) + '\n')
PY
. /run/grub.sim
echo "after Calamares (/etc/default/grub): GRUB_TIMEOUT=$GRUB_TIMEOUT GRUB_CMDLINE_LINUX_DEFAULT=$GRUB_CMDLINE_LINUX_DEFAULT"
for x in /etc/default/grub.d/*.cfg; do [ -e "$x" ] && . "$x"; done
echo "GRUB_TIMEOUT=$GRUB_TIMEOUT GRUB_TIMEOUT_STYLE=$GRUB_TIMEOUT_STYLE GRUB_DISTRIBUTOR=$GRUB_DISTRIBUTOR GRUB_CMDLINE_LINUX_DEFAULT=$GRUB_CMDLINE_LINUX_DEFAULT"
SIM
eff=$(sh /run/grubcfg-sim.sh 2>&1); echo "$eff" | sed 's/^/      | /'; eff=$(echo "$eff" | tail -n 1)
chk "grubcfg: effective values after grub.d (what grub-mkconfig uses): GRUB_TIMEOUT 1-10 s, hidden menu, 'quiet splash' (Plymouth passphrase prompt), distributor 'Fab OS'" \
  "echo '$eff' | grep -q -E '^GRUB_TIMEOUT=([1-9]|10) GRUB_TIMEOUT_STYLE=hidden GRUB_DISTRIBUTOR=Fab OS GRUB_CMDLINE_LINUX_DEFAULT=quiet splash\$'"
chk "grubcfg: the mkinitcpio-only cryptdevice=/root= parameters the module adds are dropped by the snippet (harmless: initramfs-tools unlocks from crypttab); no resume= is ever computed (swap is a file, not a partition)" \
  "! echo '$eff' | grep -q cryptdevice && Y partition \"assert d['initialSwapChoice']=='file'\""
chk "grubcfg: grubcfg.conf's GRUB_TIMEOUT default equals fabos.cfg's, so /etc/default/grub and grub.d never contradict each other on disk" \
  "t=\$(Y grubcfg \"print(d['defaults']['GRUB_TIMEOUT'])\"); grep -q -x \"GRUB_TIMEOUT=\$t\" /etc/default/grub.d/fabos.cfg"
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
# Branding::styleString() resolves a branding.desc style key by the NAME of the StyleEntry enum (QMetaEnum::valueToKey), so a key
# is only valid if it is one of those names - which are strings of libcalamaresui. The 1.0 ISO shipped the 3.2 spellings and got
# 'Unknown branding *style* entry' for all four (session logs of the 2026-09-16 installs) -> invalid QColor -> black sidebar.
for k in $(python3 -c "import yaml; print(' '.join(yaml.safe_load(open('/cal/branding/fabos/branding.desc'))['style']))"); do
  chk "branding: style key '$k' is a Branding::StyleEntry name of this libcalamaresui" "grep -q -a -F -e '$k' '$UI'"; done
chk "branding: the 3.2 keys sidebarTextSelect / sidebarTextHighlight are unknown to this Calamares, which logs 'Unknown branding *style* entry' for them (the black-sidebar cause on the 1.0 ISO)" \
  "! grep -q -a -F -e 'sidebarTextSelect' '$UI' && ! grep -q -a -F -e 'sidebarTextHighlight' '$UI' && grep -q -a -F -e 'Unknown branding *style* entry' '$UI'"
chk "branding: the image's default branding.desc (/usr/share/calamares/branding/default) uses the same four style keys as ours" \
  "python3 -c \"import yaml; d=yaml.safe_load(open('/usr/share/calamares/branding/default/branding.desc'))['style']; c=yaml.safe_load(open('/cal/branding/fabos/branding.desc'))['style']; assert set(d)==set(c), (set(d), set(c))\""
# post-install hardening evidence
chk "hardening: Fab OS apt source + archive keyring shipped by fabos-branding" "test -f /etc/apt/sources.list.d/fabos.sources && test -f /usr/share/keyrings/fabos-archive-keyring.gpg && grep -q 'Signed-By: /usr/share/keyrings/fabos-archive-keyring.gpg' /etc/apt/sources.list.d/fabos.sources"
chk "hardening: unattended-upgrades enabled with the Fab OS origins (52fabos-unattended, 20auto-upgrades)" "systemctl is-enabled unattended-upgrades && grep -q 'Patience AI:loom' /etc/apt/apt.conf.d/52fabos-unattended && grep -q 'Unattended-Upgrade \"1\"' /etc/apt/apt.conf.d/20auto-upgrades"
chk "hardening: ufw enabled in the image (survives the copy)" "grep -q '^ENABLED=yes' /etc/ufw/ufw.conf && systemctl is-enabled ufw"
chk "firstboot: unit file (working tree, placeholders filled) passes systemd-analyze verify" "sed 's/@DISTRO_NAME@/Fab OS/' /fb/usr/lib/systemd/system/fabos-firstboot.service > /run/fabos-firstboot.service && systemd-analyze verify /run/fabos-firstboot.service"
INSIDE
podman run --rm --network none -v "$CAL:/cal:ro,Z" -v "$FB:/fb:ro,Z" -v "$LIVE:/live:ro,Z" -v "$HERE/$INSIDE:/inside.sh:ro,Z" "$TAG" /bin/bash /inside.sh 2>&1 | tee build/calamares-jobs-inside.out | grep -E '^(PASS|FAIL)  |^      \| ' || true
ip=$(grep -c '^PASS  ' build/calamares-jobs-inside.out); if_=$(grep -c '^FAIL  ' build/calamares-jobs-inside.out); pass=$((pass+ip)); fail=$((fail+if_))
echo "### jobs-test: pass=$pass fail=$fail (details: $OUT, container log: build/calamares-jobs-inside.out)"
[ $fail = 0 ]
