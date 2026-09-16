#!/usr/bin/env bash
# Real-VM proof of the Start-up setting ("Ask for the disk password when the computer starts") on a LUKS-installed Fab OS disk.
#
# Needs the disk tests/install-vm.sh luks --keep-disk leaves behind: build/install-target-luks.img (raw) + its firmware variables
# build/OVMF_VARS_install-luks.fd (passphrase fabos-test; user fabtest / password fabos-test, the values install-vm-driver.py types).
# The disk itself is never written: QEMU boots a DISPOSABLE qcow2 overlay of it, which is deleted at the end.
#
# What it does, in ONE QEMU process (reboots happen inside it), on the shared VM lock (flock /tmp/fabos-vm.lock — one VM at a time):
#   boot 1  the Plymouth prompt is recognised by screendump + OCR (tesseract inside localhost/fabos:iso, as install-vm-driver.py does)
#           and the passphrase typed with QMP send-key; the driver then logs in on the SERIAL console (serial-getty@ttyS0 is enabled on
#           installed systems; there is no SSH server), becomes root with sudo, copies the WORKING-TREE helper in through fw_cfg,
#           runs `disk_unlock.sh status` (prompt_at_boot=true) and `off` with the passphrase on the helper's STDIN (a fw_cfg file —
#           never typed on a command line), checks crypttab / conf-hook / initramfs.conf / the keyfile / lsinitramfs / the LUKS slot
#           count and the audit log, then reboots
#   boot 2  NOTHING is typed: the disk must reach the login prompt on its own, with no unlock prompt recognised on any screendump
#           (screenshots kept) — then `status` (prompt_at_boot=false, consistent), `on`, the same checks in reverse, reboot
#   boot 3  the prompt must be BACK: OCR sees "unlock" (screenshot kept) and the system does not log in on its own; the passphrase is
#           typed, the login prompt follows, `status` says prompt_at_boot=true, power off.
# Usage: tests/disk-unlock-vm.sh [--disk PATH] [--vars PATH] [--out DIR] [--image localhost/fabos:iso] [--mem MB] [--cpus N] [--keep]
#        [--user NAME] [--password PW] [--passphrase PW]
# Output: <out>/driver.log (this run), <out>/serial.log (the guest's serial console), <out>/NNN-*.png + .txt (screendumps + OCR),
#         <out>/guest-*.txt (what the guest printed for each check). One PASS/FAIL line per check; exit 0 only when every check passed.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
# FABOS_BUILD: where the installed test disk lives (default build/ of this checkout; a git worktree points it at the main checkout's build/)
BUILD=${FABOS_BUILD:-build}
DISK=$BUILD/install-target-luks.img; VARS=$BUILD/OVMF_VARS_install-luks.fd; OUT=$BUILD/r7-disk-unlock; IMAGE=localhost/fabos:iso
MEM=2048; CPUS=2; KEEP=0; USER_=fabtest; PASSWORD=fabos-test; PASSPHRASE=fabos-test; LOCKED=0
while [ $# -gt 0 ]; do case "$1" in
  --disk) DISK=$2; shift;; --vars) VARS=$2; shift;; --out) OUT=$2; shift;; --image) IMAGE=$2; shift;; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;;
  --keep) KEEP=1;; --user) USER_=$2; shift;; --password) PASSWORD=$2; shift;; --passphrase) PASSPHRASE=$2; shift;; --locked) LOCKED=1;;
  *) echo "unknown arg $1"; exit 2;; esac; shift; done
if [ $LOCKED = 0 ]; then
  echo "== waiting for the shared VM lock (/tmp/fabos-vm.lock; one QEMU at a time on this host)"
  exec flock -w 5400 /tmp/fabos-vm.lock "$0" --locked --disk "$DISK" --vars "$VARS" --out "$OUT" --image "$IMAGE" --mem "$MEM" --cpus "$CPUS" \
       --user "$USER_" --password "$PASSWORD" --passphrase "$PASSPHRASE" $( [ $KEEP = 1 ] && echo --keep )
fi
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd); LOG=$OUT/driver.log; : > "$LOG"; exec > >(tee -a "$LOG") 2>&1
HELPER=$HERE/packages/fabos-agent/usr/lib/fabos/agent/disk_unlock.sh
QEMU_IMG=$(command -v qemu-img || ls /usr/bin/qemu-img "$HOME"/Android/Sdk/emulator/qemu-img 2>/dev/null | head -1)
fail=0; npass=0
verdict() { if [ "$1" = PASS ]; then npass=$((npass+1)); else fail=1; fi; echo "$1  $2"; }
echo "### disk-unlock-vm — $(date -u +%FT%TZ) — disk $DISK, vars $VARS, helper $HELPER, evidence $OUT"
[ -f "$DISK" ] || { verdict FAIL "LUKS test disk $DISK missing (tests/install-vm.sh luks --keep-disk creates it)"; exit 1; }
[ -f "$VARS" ] || { verdict FAIL "firmware variables $VARS missing"; exit 1; }
[ -x "$HELPER" ] || { verdict FAIL "helper $HELPER missing or not executable"; exit 1; }
[ -n "$QEMU_IMG" ] || { verdict FAIL "qemu-img not found"; exit 1; }
podman image exists "$IMAGE" || { verdict FAIL "OCR image $IMAGE missing"; exit 1; }
pgrep -f qemu-system-x86_64 >/dev/null && { verdict FAIL "another qemu-system-x86_64 is running although the lock is held; refusing to start a second VM"; exit 1; }
verdict PASS "preconditions: disk, firmware variables, helper, qemu-img ($QEMU_IMG), OCR image, no other VM"

TAG=$$; OVL=/tmp/r7-disk-unlock-$TAG.qcow2; VARS_COPY=/tmp/r7-disk-unlock-$TAG.vars.fd; QMP=/tmp/r7-du-$TAG.qmp; SER=/tmp/r7-du-$TAG.serial.sock; MON=/tmp/r7-du-$TAG.mon
PASSFILE=$OUT/.passphrase; SERIAL_LOG=$OUT/serial.log
cleanup() {
  if [ -n "${QPID:-}" ] && kill -0 "$QPID" 2>/dev/null; then echo "== stopping qemu (pid $QPID)"; kill "$QPID" 2>/dev/null; sleep 3; kill -9 "$QPID" 2>/dev/null; fi
  pkill -KILL -f -- "qmp unix:$QMP" 2>/dev/null
  rm -f "$PASSFILE" "$QMP" "$SER" "$MON"
  if [ $KEEP = 1 ]; then echo "== --keep: overlay $OVL and vars $VARS_COPY left in place"; else rm -f "$OVL" "$VARS_COPY"; fi
}
trap cleanup EXIT
"$QEMU_IMG" create -f qcow2 -b "$(cd "$(dirname "$DISK")" && pwd)/$(basename "$DISK")" -F raw "$OVL" >/dev/null || { verdict FAIL "qemu-img create overlay"; exit 1; }
cp "$VARS" "$VARS_COPY"; ( umask 077; printf '%s\n' "$PASSPHRASE" > "$PASSFILE" ); : > "$SERIAL_LOG"
verdict PASS "disposable overlay $OVL over the read-only base; firmware variables copied"

# ---- QEMU: the arguments of scripts/boot-vm.sh (q35/KVM, OVMF, virtio disk, usb-kbd + usb-tablet for send-key / OCR, virtio-vga
# headless) with two changes: the serial port is a unix SOCKET (interactive login) that also logs to serial.log, and fw_cfg carries the
# helper + the passphrase file into the guest (/sys/firmware/qemu_fw_cfg/by_name/opt/fabos/...). No network (nothing needs it; no
# port clash with another VM), no audio, no -no-reboot (the reboots happen inside this one process).
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1)
ACCEL=$([ -e /dev/kvm ] && echo kvm || echo tcg); CPU=$([ -e /dev/kvm ] && echo host || echo max)
args=( -name "Fab OS disk-unlock test" -machine q35,accel=$ACCEL -cpu $CPU -smp "$CPUS" -m "$MEM"
  -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$VARS_COPY"
  -drive file="$OVL",format=qcow2,if=virtio,cache=writeback,discard=unmap
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci
  -chardev socket,id=ser0,path="$SER",server=on,wait=off,logfile="$SERIAL_LOG" -serial chardev:ser0
  -monitor unix:"$MON",server,nowait -qmp unix:"$QMP",server,nowait -rtc base=utc -nic none
  -display none -device virtio-vga,id=vga0,max_outputs=1
  -fw_cfg name=opt/fabos/disk_unlock.sh,file="$HELPER" -fw_cfg name=opt/fabos/du-pass,file="$PASSFILE" )
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1100))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+800))M}
echo "== booting the overlay: mem=${MEM}M cpus=$CPUS accel=$ACCEL (serial -> $SERIAL_LOG, socket $SER, qmp $QMP)"
tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}" > "$OUT/qemu.out" 2>&1 &
QPID=$!
sleep 2; kill -0 $QPID 2>/dev/null || { cat "$OUT/qemu.out"; verdict FAIL "qemu did not start"; exit 1; }

# ---- the driver: QMP (send-key / screendump), OCR and type_text come from tests/install-vm-driver.py; the serial console is ours
export HELPER_PATH="$HELPER"
python3 -"$QMP" "$SER" "$OUT" "$IMAGE" "$USER_" "$PASSWORD" "$PASSPHRASE" "$HERE/tests/install-vm-driver.py" <<'PY'
import importlib.machinery, importlib.util, json, os, re, socket, sys, threading, time
QMP_PATH, SER_PATH, OUT, IMAGE, USER, PASSWORD, PASSPHRASE, DRIVER = sys.argv[1:9]
loader = importlib.machinery.SourceFileLoader("ivd", DRIVER); spec = importlib.util.spec_from_loader("ivd", loader); ivd = importlib.util.module_from_spec(spec); loader.exec_module(ivd)
T0 = time.time(); ivd.T0 = T0
def log(m): print("[%6.1fs] %s" % (time.time() - T0, m), flush=True)
fails = []
def verdict(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: fails.append(what)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

class Serial:
    """The guest's ttyS0 over QEMU's chardev socket: a reader thread accumulates everything; expect() waits for a regex from a mark."""
    def __init__(self, path, wait=60):
        deadline = time.time() + wait
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.s.connect(path); break
            except OSError:
                if time.time() > deadline: raise SystemExit("serial socket %s never came up" % path)
                time.sleep(0.5)
        self.buf = ""; self.lock = threading.Lock(); self.raw = b""
        threading.Thread(target=self._reader, daemon=True).start()
    def _reader(self):
        while True:
            try: d = self.s.recv(65536)
            except OSError: return
            if not d: return
            with self.lock:
                self.raw += d
                try: txt = self.raw.decode("utf-8"); self.raw = b""
                except UnicodeDecodeError: txt = self.raw[:-3].decode("utf-8", "replace"); self.raw = self.raw[-3:]
                self.buf += ANSI.sub("", txt).replace("\r", "")
    def mark(self):
        with self.lock: return len(self.buf)
    def text(self, since=0):
        with self.lock: return self.buf[since:]
    def send(self, s):
        self.s.sendall((s + "\r").encode())
    def expect(self, rx, timeout, since=0):
        r = re.compile(rx); deadline = time.time() + timeout
        while time.time() < deadline:
            m = r.search(self.text(since))
            if m: return m
            time.sleep(0.25)
        return None
    def expect_prompt(self, timeout, since=0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.text(since).rstrip(" ").endswith("FABOSDU#") or self.text(since).endswith("FABOSDU# "): return True
            time.sleep(0.25)
        return False

def strip_ctrl(s): return re.sub(r"[^\x09\x0a\x20-\x7e]", "", s)

class Guest:
    def __init__(self, ser): self.ser = ser
    def login(self, since, timeout=240):
        """getty -> user shell -> root shell (sudo -i, the user's password) -> a unique prompt."""
        m = self.ser.expect(r"login: ?$", timeout, since)
        if not m: return False
        self.ser.send(USER)
        if not self.ser.expect(r"Password: ?$", 30, since): return False
        self.ser.send(PASSWORD)
        if not self.ser.expect(r"\$ ?$", 60, since): return False
        mk = self.ser.mark(); self.ser.send("sudo -i")
        m2 = self.ser.expect(r"(password for %s: ?$|# ?$)" % re.escape(USER), 30, mk)
        if not m2: return False
        if "password" in m2.group(0):
            self.ser.send(PASSWORD)
            if not self.ser.expect(r"# ?$", 30, self.ser.mark()): return False
        # a plain shell (no readline redraws on the serial line), a unique prompt, no history
        mk = self.ser.mark(); self.ser.send("exec bash --noediting"); time.sleep(1.5)
        mk = self.ser.mark(); self.ser.send("export PS1='FABOSDU# ' LANG=C.UTF-8; stty cols 240; unset HISTFILE")
        return self.ser.expect_prompt(20, mk)
    def run(self, cmd, timeout=120):
        """Runs cmd in the root shell, returns (rc, output). The output is what lies between two markers the shell COMPUTES
        (echo __S$((40+2))__ prints __S42__), so the echoed command line — which carries the unexpanded text — never matches."""
        mk = self.ser.mark()
        self.ser.send('echo "__FABOSDU_S$((40+2))__"; ' + cmd + '; echo "__FABOSDU_E$((40+3))__=$?"')
        m = None; deadline = time.time() + timeout
        while time.time() < deadline:
            m = re.search(r"__FABOSDU_S42__\n(.*?)__FABOSDU_E43__=(\d+)", self.ser.text(mk), re.S)
            if m: break
            time.sleep(0.25)
        if not m:
            return None, self.ser.text(mk)
        self.ser.expect_prompt(15, mk)
        return int(m.group(2)), strip_ctrl(m.group(1)).strip("\n")
    def json(self, cmd, timeout=900):
        rc, out = self.run(cmd, timeout)
        obj = None
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try: obj = json.loads(line); break
                except ValueError: continue
        return rc, obj, out

def save(name, text):
    with open(os.path.join(OUT, "guest-%s.txt" % name), "w") as f: f.write(text + "\n")

def watch_boot(scr, ser, since, phase, expect_prompt, type_pass, timeout=300, blind_after=120):
    """Boot watcher. Polls screendump+OCR and the serial console until the login prompt. Returns dict(prompt_seen, login_at,
    typed_at, texts). expect_prompt=False: never types; the login must arrive on its own. expect_prompt=True: types the passphrase
    when the Plymouth prompt is recognised (or blind after blind_after s), and notes whether a login arrived BEFORE any typing."""
    t_start = time.time(); res = {"prompt_seen": None, "login_at": None, "typed_at": None, "shots": [], "unlocked_msg": None, "login_before_typing": False}
    last = ""; n = 0
    while time.time() - t_start < timeout:
        if ser.expect(r"login: ?$", 0.1, since):
            res["login_at"] = time.time() - t_start
            if res["typed_at"] is None and expect_prompt: res["login_before_typing"] = True
            try: scr.grab("%s-login-reached" % phase)
            except Exception: pass
            return res
        n += 1
        try:
            text, words, w, h, shot = scr.grab("%s-poll" % phase if n % 6 else "%s-boot" % phase)
        except Exception as e:
            log("screendump failed (%s)" % e); time.sleep(3); continue
        if text.strip() and text != last:
            log("screen: %s" % text[:150]); last = text
        prompt = "unlock" in text or "passphrase" in text
        if "set up successfully" in text and res["unlocked_msg"] is None:
            res["unlocked_msg"] = time.time() - t_start; log("screen shows 'set up successfully' (the volume is open)")
        if prompt and res["prompt_seen"] is None:
            res["prompt_seen"] = time.time() - t_start; res["shots"].append(shot); log("Plymouth unlock prompt recognised by OCR at +%.0fs (%s)" % (res["prompt_seen"], os.path.basename(shot)))
        if expect_prompt and res["typed_at"] is None and (prompt or time.time() - t_start > blind_after):
            why = "prompt seen" if prompt else "blind after %ds without a recognised prompt" % blind_after
            log("typing the passphrase (%s)" % why); ivd.type_text(q, PASSPHRASE, delay=0.11); q.send_keys(["ret"]); res["typed_at"] = time.time() - t_start
        time.sleep(3)
    return res

ser = Serial(SER_PATH); q = ivd.QMP(QMP_PATH); scr = ivd.Screen(q, OUT, IMAGE); g = Guest(ser)
log("QMP + serial up")

# ------------------------------------------------------------------ boot 1: passphrase, login, off
r1 = watch_boot(scr, ser, 0, "boot1", expect_prompt=True, type_pass=True)
verdict(r1["login_at"] is not None, "boot 1: LUKS disk boots to the serial login prompt after the passphrase (prompt %s, typed at +%s s, login at +%s s)" %
        ("recognised by OCR at +%.0fs" % r1["prompt_seen"] if r1["prompt_seen"] else "NOT recognised", "%.0f" % r1["typed_at"] if r1["typed_at"] else "-", "%.0f" % r1["login_at"] if r1["login_at"] else "-"))
if r1["login_at"] is None: log("STAGE_RESULT failed (boot 1)"); ivd.shutdown(q, hard=True); sys.exit(1)
verdict(g.login(0), "boot 1: serial login as %s and sudo -i -> root shell" % USER)
rc, out = g.run("modprobe qemu_fw_cfg 2>/dev/null; ls /sys/firmware/qemu_fw_cfg/by_name/opt/fabos/ && cat /sys/firmware/qemu_fw_cfg/by_name/opt/fabos/disk_unlock.sh/raw > /usr/local/sbin/fabos-disk-unlock.sh && chmod 0755 /usr/local/sbin/fabos-disk-unlock.sh && bash -n /usr/local/sbin/fabos-disk-unlock.sh && wc -c /usr/local/sbin/fabos-disk-unlock.sh && sha256sum /usr/local/sbin/fabos-disk-unlock.sh")
local_sha = os.popen("sha256sum " + os.environ.get("HELPER_PATH", "/dev/null")).read().split()[0] if os.environ.get("HELPER_PATH") else None
verdict(rc == 0 and (local_sha is None or local_sha in out), "boot 1: working-tree helper copied in through fw_cfg (%s)" % (out.splitlines()[-1] if out else "no output"))
save("boot1-copy", out)
rc, out = g.run("echo '--- crypttab'; cat /etc/crypttab; echo '--- root'; findmnt -n -o SOURCE,FSTYPE /; echo '--- lsblk'; lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS; echo '--- initrd'; ls -la /boot/initrd.img-*; echo '--- firstboot'; ls /var/lib/fabos/ 2>/dev/null; echo '--- slots'; cryptsetup luksDump \"$(blkid -t TYPE=crypto_LUKS -o device | head -1)\" | grep -cE '^ +[0-9]+: luks2'; echo '--- conf-hook'; grep -v '^#' /etc/cryptsetup-initramfs/conf-hook | grep .; echo '--- umask'; grep -E '^UMASK' /etc/initramfs-tools/initramfs.conf; echo '--- key'; ls -la /etc/fabos/luks-unlock.key 2>&1", 60)
save("boot1-before", out); log("guest before:\n" + out)
slots_before = re.search(r"--- slots\n(\d+)", out); slots_before = int(slots_before.group(1)) if slots_before else None
verdict(rc == 0 and " none " in re.sub(r"\s+", " ", out.split("--- root")[0]) and "/dev/mapper/" in out, "boot 1: fresh install state — crypttab key column 'none', root on /dev/mapper (LUKS slots in use: %s)" % slots_before)
rc, st, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh status", 120); save("boot1-status", out)
verdict(rc == 0 and st and st.get("encrypted") is True and st.get("prompt_at_boot") is True and st.get("consistent") is True, "boot 1: status -> encrypted=true prompt_at_boot=true consistent=true (%s)" % json.dumps(st)[:200])
t = time.time()
rc, res, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh off < /sys/firmware/qemu_fw_cfg/by_name/opt/fabos/du-pass/raw", 900); save("boot1-off", out)
log("off took %.0f s; helper said: %s" % (time.time() - t, json.dumps(res)[:400] if res else out[-600:]))
verdict(rc == 0 and res and res.get("ok") is True and res.get("prompt_at_boot") is False, "boot 1: `off` with the passphrase on STDIN (fw_cfg file) -> ok=true prompt_at_boot=false in %.0f s" % (time.time() - t))
rc, out = g.run("echo '--- crypttab'; cat /etc/crypttab; echo '--- conf-hook'; grep -v '^#' /etc/cryptsetup-initramfs/conf-hook | grep .; echo '--- umask'; grep -E '^UMASK' /etc/initramfs-tools/initramfs.conf; echo '--- key'; stat -c '%a %U %s %n' /etc/fabos/luks-unlock.key; echo '--- initrd'; ls -la /boot/initrd.img-*; echo '--- lsinitramfs'; lsinitramfs /boot/initrd.img-$(uname -r) | grep cryptroot; echo '--- slots'; cryptsetup luksDump \"$(blkid -t TYPE=crypto_LUKS -o device | head -1)\" | grep -cE '^ +[0-9]+: luks2'; echo '--- log'; cat /var/log/fabos/disk-unlock.log; echo '--- passphrase-in-log'; grep -c -- fabos-test /var/log/fabos/disk-unlock.log /var/log/fabos/rootexec.log 2>/dev/null; stat -c '%a %U:%G %n' /var/log/fabos/disk-unlock.log", 120)
save("boot1-after-off", out); log("guest after off:\n" + out)
slots_after = re.search(r"--- slots\n(\d+)", out); slots_after = int(slots_after.group(1)) if slots_after else None
ct = out.split("--- crypttab")[1].split("--- conf-hook")[0]
verdict("/etc/fabos/luks-unlock.key" in ct and "initramfs" in ct and "luks" in ct, "boot 1: crypttab root entry -> key /etc/fabos/luks-unlock.key + initramfs option (%s)" % " ".join(l for l in ct.splitlines() if l and not l.startswith("#"))[:160])
verdict('KEYFILE_PATTERN="/etc/fabos/luks-unlock.key"' in out and "UMASK=0077" in out, "boot 1: conf-hook KEYFILE_PATTERN set, initramfs.conf UMASK=0077")
verdict(re.search(r"--- key\n400 root 4096 /etc/fabos/luks-unlock.key", out) is not None, "boot 1: keyfile 4096 bytes, 0400 root, on the encrypted root")
verdict("cryptroot/keyfiles/" in out.split("--- lsinitramfs")[1].split("--- slots")[0], "boot 1: lsinitramfs shows cryptroot/keyfiles/<name>.key inside the running kernel's initrd on /boot")
verdict(re.search(r"-rw-------.*initrd\.img", out) is not None, "boot 1: the rebuilt initrd on /boot is root-only (0600)")
verdict(slots_before is not None and slots_after == slots_before + 1, "boot 1: one LUKS key slot added (%s -> %s)" % (slots_before, slots_after))
verdict("DONE: the computer will start without asking" in out and "keyfile created" in out and "key slot added" in out, "boot 1: /var/log/fabos/disk-unlock.log has every step")
verdict(re.search(r"--- passphrase-in-log\n0\n", out) is not None and re.search(r"\n640 root:adm /var/log/fabos/disk-unlock.log", out) is not None, "boot 1: the passphrase is in no root log; disk-unlock.log is 0640 root:adm")
rc, st, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh status", 120); save("boot1-status-after-off", out)
verdict(rc == 0 and st and st.get("prompt_at_boot") is False and st.get("keyfile_in_initramfs") is True and st.get("consistent") is True, "boot 1: status -> prompt_at_boot=false keyfile_in_initramfs=true consistent=true")
log("rebooting (boot 2 must unlock from the keyfile, nothing typed)"); mark2 = ser.mark(); ser.send("systemctl reboot"); time.sleep(8)

# ------------------------------------------------------------------ boot 2: no passphrase typed
r2 = watch_boot(scr, ser, mark2, "boot2", expect_prompt=False, type_pass=False, timeout=300)
verdict(r2["login_at"] is not None and r2["prompt_seen"] is None, "boot 2: disk boots to the serial login WITHOUT a passphrase typed and with NO unlock prompt on any screendump (login at +%s s; 'set up successfully' seen at +%s s)" %
        ("%.0f" % r2["login_at"] if r2["login_at"] else "never", "%.0f" % r2["unlocked_msg"] if r2["unlocked_msg"] else "-"))
if r2["login_at"] is None: log("STAGE_RESULT failed (boot 2)"); ivd.shutdown(q, hard=True); sys.exit(1)
verdict(g.login(mark2), "boot 2: serial login + root shell")
rc, out = g.run("echo '--- root'; findmnt -n -o SOURCE,FSTYPE /; echo '--- uptime'; cut -d' ' -f1 /proc/uptime; echo '--- cmdline'; cat /proc/cmdline; echo '--- journal'; journalctl -b --no-pager -o short-monotonic 2>/dev/null | grep -iE 'cryptsetup|plymouth.*(ask|password)|Please unlock' | head -12; echo '--- plymouth-asked'; journalctl -b --no-pager 2>/dev/null | grep -ciE 'ask-for-password|Please unlock'", 60)
save("boot2-state", out); log("guest boot 2:\n" + out)
verdict("/dev/mapper/" in out and re.search(r"--- plymouth-asked\n0\n", out) is not None, "boot 2: root is the LUKS mapper device and the journal records no password request in this boot")
rc, st, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh status", 120); save("boot2-status", out)
verdict(rc == 0 and st and st.get("prompt_at_boot") is False and st.get("consistent") is True, "boot 2: status -> prompt_at_boot=false consistent=true")
t = time.time()
rc, res, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh on", 900); save("boot2-on", out)
log("on took %.0f s; helper said: %s" % (time.time() - t, json.dumps(res)[:400] if res else out[-600:]))
verdict(rc == 0 and res and res.get("ok") is True and res.get("prompt_at_boot") is True, "boot 2: `on` -> ok=true prompt_at_boot=true in %.0f s" % (time.time() - t))
rc, out = g.run("echo '--- crypttab'; cat /etc/crypttab; echo '--- conf-hook'; grep -v '^#' /etc/cryptsetup-initramfs/conf-hook | grep -c KEYFILE_PATTERN; echo '--- key'; ls /etc/fabos/luks-unlock.key 2>&1; echo '--- lsinitramfs'; lsinitramfs /boot/initrd.img-$(uname -r) | grep -c keyfiles; echo '--- slots'; cryptsetup luksDump \"$(blkid -t TYPE=crypto_LUKS -o device | head -1)\" | grep -cE '^ +[0-9]+: luks2'; echo '--- log'; tail -8 /var/log/fabos/disk-unlock.log", 120)
save("boot2-after-on", out); log("guest after on:\n" + out)
slots_on = re.search(r"--- slots\n(\d+)", out); slots_on = int(slots_on.group(1)) if slots_on else None
ct = out.split("--- crypttab")[1].split("--- conf-hook")[0]
verdict(" none " in re.sub(r"\s+", " ", ct) and "luks-unlock.key" not in ct and re.search(r"--- conf-hook\n0\n", out) is not None, "boot 2: crypttab key column back to 'none', KEYFILE_PATTERN dropped")
verdict("No such file" in out.split("--- key")[1].split("--- lsinitramfs")[0] and re.search(r"--- lsinitramfs\n0\n", out) is not None, "boot 2: keyfile deleted, initrd free of cryptroot/keyfiles")
verdict(slots_on == slots_before, "boot 2: LUKS slot removed (%s -> %s)" % (slots_after, slots_on))
verdict("DONE: the computer asks for the disk password" in out, "boot 2: disk-unlock.log records the reversal")
rc, st, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh status", 120); save("boot2-status-after-on", out)
verdict(rc == 0 and st and st.get("prompt_at_boot") is True and st.get("consistent") is True, "boot 2: status -> prompt_at_boot=true consistent=true")
log("rebooting (boot 3 must ask for the passphrase again)"); mark3 = ser.mark(); ser.send("systemctl reboot"); time.sleep(8)

# ------------------------------------------------------------------ boot 3: the prompt is back
r3 = watch_boot(scr, ser, mark3, "boot3", expect_prompt=True, type_pass=True, timeout=360, blind_after=150)
verdict(r3["prompt_seen"] is not None, "boot 3: the Plymouth unlock prompt is BACK — recognised by OCR at +%s s (%s)" % ("%.0f" % r3["prompt_seen"] if r3["prompt_seen"] else "never", ", ".join(os.path.basename(s) for s in r3["shots"]) or "no shot"))
verdict(not r3["login_before_typing"], "boot 3: the system did not reach the login prompt on its own before the passphrase was typed")
verdict(r3["login_at"] is not None and r3["typed_at"] is not None and r3["login_at"] > r3["typed_at"], "boot 3: after typing the passphrase (+%s s) the login prompt follows (+%s s)" % ("%.0f" % r3["typed_at"] if r3["typed_at"] else "-", "%.0f" % r3["login_at"] if r3["login_at"] else "never"))
if r3["login_at"] is not None and g.login(mark3):
    rc, st, out = g.json("/usr/local/sbin/fabos-disk-unlock.sh status", 120); save("boot3-status", out)
    verdict(rc == 0 and st and st.get("prompt_at_boot") is True and st.get("consistent") is True and st.get("keyfile_present") is False, "boot 3: status -> prompt_at_boot=true, no keyfile, consistent")
    rc, out = g.run("rm -f /usr/local/sbin/fabos-disk-unlock.sh; echo removed", 30)
    ser.send("systemctl poweroff")
else:
    verdict(False, "boot 3: serial login after the passphrase")
ivd.shutdown(q)
log("STAGE_RESULT %s (%d check(s) failed)" % ("ok" if not fails else "failed", len(fails)))
sys.exit(1 if fails else 0)
PY
rc=$?
if [ -f "$OUT/qemu.out" ] && grep -q . "$OUT/qemu.out"; then echo "== qemu output:"; tail -5 "$OUT/qemu.out"; fi
# every PASS/FAIL line above came from the driver; count them from this run's log
npass=$(grep -c '^PASS  ' "$LOG"); nfail=$(grep -c '^FAIL  ' "$LOG")
echo "### disk-unlock-vm: $npass passed, $nfail failed (driver exit $rc) — evidence: $OUT/ (driver.log, serial.log, screenshots + OCR text, guest-*.txt)"
[ "$nfail" = 0 ] && [ "$rc" = 0 ]
