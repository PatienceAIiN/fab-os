#!/usr/bin/env bash
# Real-VM proof of the Start-up setting ("Ask for the disk password when the computer starts") on a LUKS-installed Fab OS disk.
#
# Needs the disk tests/install-vm.sh luks --keep-disk leaves behind: build/install-target-luks.img (raw) + its firmware variables
# build/OVMF_VARS_install-luks.fd (passphrase fabos-test; user fabtest / password fabos-test — the values install-vm-driver.py types).
# The disk itself is never written: QEMU boots a DISPOSABLE qcow2 overlay of it, which is deleted at the end.
#
# What it does, in ONE QEMU process (the reboots happen inside it), on the shared VM lock (flock /tmp/fabos-vm.lock — one VM at a time):
#   boot 1  the Plymouth prompt is recognised by screendump + OCR (tesseract inside localhost/fabos:iso, as install-vm-driver.py does)
#           and the passphrase typed with QMP send-key; the login screen (or FABOS_INSTALLED_OK on ttyS0) says the disk is up. Installed
#           systems have no SSH server and no getty on ttyS0, so the driver switches to a text console (Ctrl+Alt+F3), logs in with
#           send-key and starts a guest-side script delivered through fw_cfg whose whole output goes to /dev/ttyS0 (this side reads the
#           serial socket). The script copies the WORKING-TREE helper in (fw_cfg too), runs `disk_unlock.sh status`
#           (prompt_at_boot=true) and `off` with the passphrase on the helper's STDIN (a fw_cfg file — never a typed command line),
#           prints crypttab / conf-hook / initramfs.conf / the keyfile / lsinitramfs / the LUKS slot count / the audit log, and reboots
#   boot 2  NOTHING is typed: the disk must reach the login screen and print FABOS_INSTALLED_OK on its own, with no unlock prompt
#           recognised on any screendump (screenshots kept) — then the same console path runs `status` (prompt_at_boot=false), `on`, the
#           checks in reverse, and reboots
#   boot 3  the prompt must be BACK: OCR sees "unlock" (screenshot kept) and the system does not come up on its own; the passphrase is
#           typed, the login screen follows, `status` says prompt_at_boot=true, power off.
# Usage: tests/disk-unlock-vm.sh [--disk PATH] [--vars PATH] [--out DIR] [--image localhost/fabos:iso] [--mem MB] [--cpus N] [--keep]
#        [--user NAME] [--password PW] [--passphrase PW]     FABOS_BUILD=DIR: where the disk lives (a worktree -> the main checkout's build/)
# Output: <out>/driver.log (this run), <out>/serial.log (the guest's ttyS0: the script's output + FABOS_INSTALLED_OK), <out>/NNN-*.png + .txt
#         (screendumps + OCR), <out>/guest-*.txt (what the guest printed per phase). One PASS/FAIL line per check; exit 0 only when all passed.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
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
fail=0
verdict() { [ "$1" = PASS ] || fail=1; echo "$1  $2"; }
echo "### disk-unlock-vm — $(date -u +%FT%TZ) — disk $DISK, vars $VARS, helper $HELPER, evidence $OUT"
[ -f "$DISK" ] || { verdict FAIL "LUKS test disk $DISK missing (tests/install-vm.sh luks --keep-disk creates it; FABOS_BUILD points at another build/)"; exit 1; }
[ -f "$VARS" ] || { verdict FAIL "firmware variables $VARS missing"; exit 1; }
[ -x "$HELPER" ] || { verdict FAIL "helper $HELPER missing or not executable"; exit 1; }
[ -n "$QEMU_IMG" ] || { verdict FAIL "qemu-img not found"; exit 1; }
podman image exists "$IMAGE" || { verdict FAIL "OCR image $IMAGE missing"; exit 1; }
[ -z "$(ps -C qemu-system-x86_64 -o pid= 2>/dev/null)" ] || { verdict FAIL "another qemu-system-x86_64 is running although the lock is held; refusing to start a second VM"; exit 1; }
verdict PASS "preconditions: disk, firmware variables, helper, qemu-img ($QEMU_IMG), OCR image, no other VM"

TAG=$$; OVL=/tmp/r7-disk-unlock-$TAG.qcow2; VARS_COPY=/tmp/r7-disk-unlock-$TAG.vars.fd; QMP=/tmp/r7-du-$TAG.qmp; SER=/tmp/r7-du-$TAG.serial.sock; MON=/tmp/r7-du-$TAG.mon
PASSFILE=$OUT/.passphrase; DUT=$OUT/.dut.sh; SERIAL_LOG=$OUT/serial.log
cleanup() {
  if [ -n "${QPID:-}" ] && kill -0 "$QPID" 2>/dev/null; then echo "== stopping qemu (pid $QPID)"; kill "$QPID" 2>/dev/null; sleep 3; kill -9 "$QPID" 2>/dev/null; fi
  pkill -KILL -f -- "qmp unix:$QMP" 2>/dev/null
  rm -f "$PASSFILE" "$DUT" "$QMP" "$SER" "$MON"
  if [ $KEEP = 1 ]; then echo "== --keep: overlay $OVL and vars $VARS_COPY left in place"; else rm -f "$OVL" "$VARS_COPY"; fi
}
trap cleanup EXIT
"$QEMU_IMG" create -f qcow2 -b "$(cd "$(dirname "$DISK")" && pwd)/$(basename "$DISK")" -F raw "$OVL" >/dev/null || { verdict FAIL "qemu-img create overlay"; exit 1; }
cp "$VARS" "$VARS_COPY"; ( umask 077; printf '%s\n' "$PASSPHRASE" > "$PASSFILE" ); : > "$SERIAL_LOG"
verdict PASS "disposable overlay $OVL over the read-only base; firmware variables copied"

# ---- the guest-side script (root; delivered through fw_cfg; its stdout is /dev/ttyS0). Phase 1: helper in, state, status, off, state,
# status, reboot. Phase 2: state, status, on, state, status, reboot. Phase 3: status, power off. Before a reboot it lets
# fabos-firstboot finish (its ExecStartPost prints FABOS_INSTALLED_OK on ttyS0) so the marker of that boot is on the serial log.
cat > "$DUT" <<'GUEST'
#!/bin/sh
P=$1; H=/usr/local/sbin/fabos-disk-unlock.sh; FW=/sys/firmware/qemu_fw_cfg/by_name/opt/fabos
echo "__DUT_BEGIN $P"
dev() { blkid -t TYPE=crypto_LUKS -o device | head -1; }
state() {
  echo "--- crypttab"; grep -v '^#' /etc/crypttab | grep .; echo "(end)"
  echo "--- root"; findmnt -n -o SOURCE,FSTYPE /
  echo "--- conf-hook"; grep -v '^#' /etc/cryptsetup-initramfs/conf-hook 2>/dev/null | grep .; echo "(end)"
  echo "--- umask"; grep -E '^UMASK' /etc/initramfs-tools/initramfs.conf; echo "(end)"
  echo "--- key"; stat -c '%a %U %s %n' /etc/fabos/luks-unlock.key 2>&1
  echo "--- initrd"; ls -la /boot/initrd.img-*
  echo "--- lsinitramfs"; lsinitramfs "/boot/initrd.img-$(uname -r)" | grep cryptroot; echo "(end)"
  echo "--- slots"; cryptsetup luksDump "$(dev)" | grep -cE '^ +[0-9]+: luks2'
  echo "--- log"; cat /var/log/fabos/disk-unlock.log 2>/dev/null; echo "(end)"
  echo "--- passphrase-in-log"; grep -c -- "$(tr -d '\n' < $FW/du-pass/raw)" /var/log/fabos/disk-unlock.log 2>/dev/null || echo 0
  echo "--- logmode"; stat -c '%a %U:%G %n' /var/log/fabos/disk-unlock.log 2>&1
}
status() { $H status > /tmp/du.o 2> /tmp/du.e; rc=$?; echo "--- $1-stderr"; cat /tmp/du.e; echo "__DUT_JSON_$1 rc=$rc $(tail -1 /tmp/du.o)"; }
action() { T=$(date +%s); if [ "$1" = off ]; then $H off < $FW/du-pass/raw > /tmp/du.o 2> /tmp/du.e; else $H on > /tmp/du.o 2> /tmp/du.e; fi; rc=$?
           echo "--- $1-stderr"; cat /tmp/du.e; echo "__DUT_JSON_$1 rc=$rc secs=$(( $(date +%s) - T )) $(tail -1 /tmp/du.o)"; }
finish() { echo "__DUT_END $P"; n=0; while [ "$(systemctl is-active fabos-firstboot.service 2>/dev/null)" = activating ] && [ $n -lt 150 ]; do sleep 2; n=$((n+1)); done; sleep 3; systemctl "$1"; }
case "$P" in
  1) modprobe qemu_fw_cfg 2>/dev/null; echo "--- fwcfg"; ls $FW; cat $FW/disk_unlock.sh/raw > $H && chmod 0755 $H && bash -n $H && echo "helper $(wc -c < $H) bytes sha256 $(sha256sum $H | cut -c1-16)"
     echo "--- whoami"; id -u; echo "--- getty"; systemctl is-enabled serial-getty@ttyS0.service 2>&1; systemctl is-active serial-getty@ttyS0.service 2>&1; echo "--- firstboot"; ls /var/lib/fabos 2>&1
     echo "=== state-before"; state; status status1
     echo "=== off"; action off
     echo "=== state-after-off"; state; status status2
     finish reboot;;
  2) echo "--- uptime"; cut -d' ' -f1 /proc/uptime; echo "--- journal"; journalctl -b --no-pager -o short-monotonic 2>/dev/null | grep -iE 'cryptsetup|ask-for-password|Please unlock' | head -12; echo "(end)"
     echo "--- plymouth-asked"; journalctl -b --no-pager 2>/dev/null | grep -ciE 'ask-for-password|Please unlock'
     echo "=== state-boot2"; state; status status1
     echo "=== on"; action on
     echo "=== state-after-on"; state; status status2
     finish reboot;;
  3) echo "=== state-boot3"; state; status status1; rm -f $H
     finish poweroff;;
esac
GUEST

# ---- QEMU: the arguments of scripts/boot-vm.sh (q35/KVM, OVMF, virtio disk, usb-kbd + usb-tablet for send-key / OCR, virtio-vga
# headless) with two changes: the serial port is a unix SOCKET that also logs to serial.log, and fw_cfg carries the helper, the
# guest script and the passphrase file into the guest (/sys/firmware/qemu_fw_cfg/by_name/opt/fabos/...). No network (nothing needs it;
# no port clash with another VM), no audio, no -no-reboot (the reboots happen inside this one process).
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1)
ACCEL=$([ -e /dev/kvm ] && echo kvm || echo tcg); CPU=$([ -e /dev/kvm ] && echo host || echo max)
args=( -name "Fab OS disk-unlock test" -machine q35,accel=$ACCEL -cpu $CPU -smp "$CPUS" -m "$MEM"
  -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$VARS_COPY"
  -drive file="$OVL",format=qcow2,if=virtio,cache=writeback,discard=unmap
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci
  -chardev socket,id=ser0,path="$SER",server=on,wait=off,logfile="$SERIAL_LOG" -serial chardev:ser0
  -monitor unix:"$MON",server,nowait -qmp unix:"$QMP",server,nowait -rtc base=utc -nic none
  -display none -device virtio-vga,id=vga0,max_outputs=1
  -fw_cfg name=opt/fabos/disk_unlock.sh,file="$HELPER" -fw_cfg name=opt/fabos/du-pass,file="$PASSFILE" -fw_cfg name=opt/fabos/dut.sh,file="$DUT" )
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1100))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+800))M}
echo "== booting the overlay: mem=${MEM}M cpus=$CPUS accel=$ACCEL (serial -> $SERIAL_LOG, socket $SER, qmp $QMP)"
tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}" > "$OUT/qemu.out" 2>&1 &
QPID=$!
sleep 2; kill -0 $QPID 2>/dev/null || { cat "$OUT/qemu.out"; verdict FAIL "qemu did not start"; exit 1; }

# ---- the driver: QMP (send-key / screendump), OCR and type_text come from tests/install-vm-driver.py; the serial socket is ours
export HELPER_PATH="$HELPER"
python3 - "$QMP" "$SER" "$OUT" "$IMAGE" "$USER_" "$PASSWORD" "$PASSPHRASE" "$HERE/tests/install-vm-driver.py" <<'PY'
import importlib.machinery, importlib.util, json, os, re, socket, subprocess, sys, threading, time
QMP_PATH, SER_PATH, OUT, IMAGE, USER, PASSWORD, PASSPHRASE, DRIVER = sys.argv[1:9]
loader = importlib.machinery.SourceFileLoader("ivd", DRIVER); spec = importlib.util.spec_from_loader("ivd", loader); ivd = importlib.util.module_from_spec(spec); loader.exec_module(ivd)
T0 = time.time(); ivd.T0 = T0
def log(m): print("[%6.1fs] %s" % (time.time() - T0, m), flush=True)
fails = []
def verdict(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: fails.append(what)
ANSI = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]")

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
    def expect(self, rx, timeout, since=0):
        r = re.compile(rx); deadline = time.time() + timeout
        while True:
            m = r.search(self.text(since))
            if m or time.time() > deadline: return m
            time.sleep(0.25)

def save(name, text):
    with open(os.path.join(OUT, "guest-%s.txt" % name), "w") as f: f.write(text + "\n")

def watch_boot(ser, since, phase, expect_prompt, timeout=300, blind_after=120):
    """Until the login screen (OCR: 'select your user' / the test user's name) or FABOS_INSTALLED_OK (serial). expect_prompt=False
    never types; expect_prompt=True types the passphrase when the Plymouth prompt is recognised (or blind after blind_after s) and
    notes whether the system came up BEFORE any typing."""
    t_start = time.time(); res = {"prompt_seen": None, "up_at": None, "typed_at": None, "shots": [], "unlocked_msg": None, "up_before_typing": False, "marker_at": None, "up_via": None}
    last = ""; n = 0
    while time.time() - t_start < timeout:
        if ser.expect(r"FABOS_INSTALLED_OK", 0.05, since) and res["marker_at"] is None:
            res["marker_at"] = time.time() - t_start; log("serial: FABOS_INSTALLED_OK at +%.0fs" % res["marker_at"])
        n += 1
        try:
            text, words, w, h, shot = scr.grab("%s-poll" % phase if n % 6 else "%s-boot" % phase)
        except Exception as e:
            log("screendump failed (%s)" % e); time.sleep(3); continue
        if text.strip() and text != last:
            log("screen: %s" % text[:150]); last = text
        prompt = ("unlock" in text or "passphrase" in text) and "select your user" not in text
        up = "select your user" in text or "fab tester" in text or res["marker_at"] is not None
        if "set up successfully" in text and res["unlocked_msg"] is None:
            res["unlocked_msg"] = time.time() - t_start; log("screen shows 'set up successfully' (the volume is open)")
        if prompt and res["prompt_seen"] is None:
            res["prompt_seen"] = time.time() - t_start; res["shots"].append(shot); log("Plymouth unlock prompt recognised by OCR at +%.0fs (%s)" % (res["prompt_seen"], os.path.basename(shot)))
        if up:
            res["up_at"] = time.time() - t_start; res["up_via"] = "login screen" if ("select your user" in text or "fab tester" in text) else "FABOS_INSTALLED_OK"
            if expect_prompt and res["typed_at"] is None: res["up_before_typing"] = True
            res["shots"].append(shot); log("system is up (%s) at +%.0fs" % (res["up_via"], res["up_at"])); return res
        if expect_prompt and res["typed_at"] is None and (prompt or time.time() - t_start > blind_after):
            why = "prompt seen" if prompt else "blind after %ds without a recognised prompt" % blind_after
            log("typing the passphrase (%s)" % why); ivd.type_text(q, PASSPHRASE, delay=0.11); q.send_keys(["ret"]); res["typed_at"] = time.time() - t_start
        time.sleep(3)
    return res

def wait_marker(ser, since, res, budget):
    """FABOS_INSTALLED_OK for this boot (fabos-firstboot waits for a network that is not there: up to ~3 min after the login screen)."""
    if res.get("marker_at") is None:
        t = time.time(); m = ser.expect(r"FABOS_INSTALLED_OK", budget, since)
        if m: res["marker_at"] = res["up_at"] + (time.time() - t) if res.get("up_at") else time.time() - t
        log("FABOS_INSTALLED_OK %s" % ("received" if m else "NOT received within %ds" % budget))
    return res["marker_at"] is not None

def vt_run(ser, phase, cmd, tty):
    """Ctrl+Alt+F<n> -> the text console's getty -> user / password -> the command, whose output the guest sends to /dev/ttyS0.
    True when the guest script announced itself (__DUT_BEGIN <phase>) on the serial line."""
    mk = ser.mark()
    q.send_keys(["ctrl", "alt", tty], hold=150); time.sleep(3)
    seen = False; t0 = time.time()
    while time.time() - t0 < 25:
        try:
            text, *_ = scr.grab("%s-vt" % phase)
        except Exception:
            text = ""
        if "login" in text: seen = True; break
        time.sleep(2)
    log("text console %s: login prompt %s" % (tty, "recognised" if seen else "not recognised, typing blind"))
    ivd.type_text(q, USER); q.send_keys(["ret"]); time.sleep(2.5)
    ivd.type_text(q, PASSWORD); q.send_keys(["ret"]); time.sleep(4)
    ivd.type_text(q, cmd, delay=0.06); q.send_keys(["ret"])
    m = ser.expect(r"__DUT_BEGIN %s" % phase, 60, mk)
    try: scr.grab("%s-vt-after-command" % phase)
    except Exception: pass
    return m is not None

def run_phase(ser, phase, cmd, timeout):
    mk = ser.mark()
    ok = vt_run(ser, phase, cmd, "f3") or vt_run(ser, phase, cmd, "f4")
    verdict(ok, "boot %s: text-console login as %s + sudo -> the guest script started (output on ttyS0)" % (phase, USER))
    if not ok: return None
    m = ser.expect(r"__DUT_END %s" % phase, timeout, mk)
    out = ser.text(mk); save("phase%s" % phase, out)
    verdict(m is not None, "boot %s: the guest script ran to its end (%d bytes on the serial line)" % (phase, len(out)))
    return out if m else None

def section(out, name):
    i = out.find("=== " + name)
    if i < 0: return ""
    rest = out[i + len(name) + 4:]
    j = re.search(r"\n=== |\n__DUT_END", rest)
    return rest[:j.start()] if j else rest
def part(sec, name):
    i = sec.find("--- " + name + "\n")
    if i < 0: return ""
    rest = sec[i + len(name) + 5:]
    j = re.search(r"\n--- |\n__DUT_JSON_|\n\(end\)", rest)
    return (rest[:j.start()] if j else rest).strip("\n")
def jline(out, label):
    m = re.search(r"__DUT_JSON_%s rc=(\d+)(?: secs=(\d+))? (\{.*\})" % re.escape(label), out)
    if not m: return None, None, None
    try: obj = json.loads(m.group(3))
    except ValueError: obj = None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None), obj
def num(s):
    m = re.search(r"\d+", s or ""); return int(m.group(0)) if m else None

SUDO = "echo %s | sudo -S " % PASSWORD          # the user's own password (test-only; the LUKS passphrase never appears on a command line)
# the redirection to /dev/ttyS0 must happen INSIDE the root shell (the user's shell may not open the serial device)
CMD1 = SUDO + "sh -c 'modprobe qemu_fw_cfg; cp /sys/firmware/qemu_fw_cfg/by_name/opt/fabos/dut.sh/raw /usr/local/sbin/dut; chmod 755 /usr/local/sbin/dut; /usr/local/sbin/dut 1 >/dev/ttyS0 2>&1'"
CMD2 = SUDO + "sh -c '/usr/local/sbin/dut 2 >/dev/ttyS0 2>&1'"
CMD3 = SUDO + "sh -c '/usr/local/sbin/dut 3 >/dev/ttyS0 2>&1'"

ser = Serial(SER_PATH); q = ivd.QMP(QMP_PATH); scr = ivd.Screen(q, OUT, IMAGE)
log("QMP + serial up")

# ------------------------------------------------------------------ boot 1: passphrase, then off
r1 = watch_boot(ser, 0, "boot1", expect_prompt=True)
verdict(r1["up_at"] is not None and r1["typed_at"] is not None, "boot 1: the LUKS disk comes up after the passphrase (prompt %s; typed at +%s s; up via %s at +%s s)" %
        ("recognised by OCR at +%.0fs" % r1["prompt_seen"] if r1["prompt_seen"] else "NOT recognised", "%.0f" % r1["typed_at"] if r1["typed_at"] else "-", r1["up_via"], "%.0f" % r1["up_at"] if r1["up_at"] else "-"))
if r1["up_at"] is None: log("STAGE_RESULT failed (boot 1)"); ivd.shutdown(q, hard=True); sys.exit(1)
out = run_phase(ser, "1", CMD1, 1200)
if out is None: log("STAGE_RESULT failed (phase 1)"); ivd.shutdown(q, hard=True); sys.exit(1)
wait_marker(ser, 0, r1, 60)      # fabos-firstboot (offline: ~3 min after the login screen) has usually finished during `off`; the guest waited for it before rebooting
verdict(r1["marker_at"] is not None, "boot 1: FABOS_INSTALLED_OK on ttyS0 (fabos-firstboot ExecStartPost) at +%s s" % ("%.0f" % r1["marker_at"] if r1["marker_at"] else "never"))
local_sha = subprocess.run(["sha256sum", os.environ["HELPER_PATH"]], capture_output=True, text=True).stdout[:16]
verdict(("sha256 " + local_sha) in out, "boot 1: the working-tree helper is what runs in the guest (sha256 %s… via fw_cfg)" % local_sha)
before = section(out, "state-before")
slots_before = num(part(before, "slots"))
verdict(" none " in re.sub(r"\s+", " ", " " + part(before, "crypttab") + " ") and "/dev/mapper/" in part(before, "root"), "boot 1: fresh install state — crypttab key column 'none', root on %s (LUKS slots in use: %s; getty on ttyS0: %s)" % (part(before, "root").split()[0] if part(before, "root") else "?", slots_before, part(out, "getty").replace("\n", "/")))
rc, _, st = jline(out, "status1")
verdict(st and st.get("encrypted") is True and st.get("prompt_at_boot") is True and st.get("consistent") is True, "boot 1: status -> encrypted=true prompt_at_boot=true consistent=true (%s)" % json.dumps(st)[:160])
rc, secs, res = jline(out, "off")
verdict(rc == 0 and res and res.get("ok") is True and res.get("prompt_at_boot") is False, "boot 1: `off` with the passphrase on STDIN (fw_cfg file) -> exit %s, ok=%s prompt_at_boot=%s in %s s" % (rc, (res or {}).get("ok"), (res or {}).get("prompt_at_boot"), secs))
after = section(out, "state-after-off"); slots_after = num(part(after, "slots")); ct = part(after, "crypttab")
verdict("/etc/fabos/luks-unlock.key" in ct and "initramfs" in ct and "luks" in ct, "boot 1: crypttab root entry -> key /etc/fabos/luks-unlock.key + initramfs option (%s)" % ct.replace("\n", " ")[:160])
verdict('KEYFILE_PATTERN="/etc/fabos/luks-unlock.key"' in part(after, "conf-hook") and "UMASK=0077" in part(after, "umask"), "boot 1: conf-hook KEYFILE_PATTERN set, initramfs.conf UMASK=0077")
verdict(part(after, "key").startswith("400 root 4096 /etc/fabos/luks-unlock.key"), "boot 1: keyfile 4096 bytes, 0400 root, on the encrypted root (%s)" % part(after, "key"))
verdict("cryptroot/keyfiles/" in part(after, "lsinitramfs"), "boot 1: lsinitramfs shows %s inside the running kernel's initrd on /boot" % ([l for l in part(after, "lsinitramfs").splitlines() if "keyfiles/" in l] or ["nothing"])[-1])
verdict(re.search(r"-rw-------.*initrd\.img", part(after, "initrd")) is not None, "boot 1: the rebuilt initrd on /boot is root-only (0600)")
verdict(slots_before is not None and slots_after == slots_before + 1, "boot 1: one LUKS key slot added (%s -> %s)" % (slots_before, slots_after))
lg = part(after, "log")
verdict("DONE: the computer will start without asking" in lg and "keyfile created" in lg and "key slot added" in lg and "verified:" in lg, "boot 1: /var/log/fabos/disk-unlock.log has every step (keyfile, slot, crypttab, conf-hook, UMASK, update-initramfs, verified, DONE)")
verdict(num(part(after, "passphrase-in-log")) == 0 and part(after, "logmode").startswith("640 root:adm"), "boot 1: the passphrase is not in the root log; disk-unlock.log is 0640 root:adm")
rc, _, st = jline(out, "status2")
verdict(st and st.get("prompt_at_boot") is False and st.get("keyfile_in_initramfs") is True and st.get("consistent") is True, "boot 1: status -> prompt_at_boot=false keyfile_in_initramfs=true consistent=true")
mark2 = ser.mark(); log("the guest reboots itself (boot 2 must unlock from the keyfile; nothing is typed)")
verdict(ser.expect(r"BdsDxe: starting Boot", 240, mark2) is not None, "boot 2: firmware restarted the disk (BdsDxe on ttyS0)")

# ------------------------------------------------------------------ boot 2: no passphrase typed
r2 = watch_boot(ser, mark2, "boot2", expect_prompt=False, timeout=300)
verdict(r2["up_at"] is not None and r2["prompt_seen"] is None, "boot 2: the disk comes up WITHOUT a passphrase typed and with NO unlock prompt on any screendump (up via %s at +%s s; 'set up successfully' at +%s s)" %
        (r2["up_via"], "%.0f" % r2["up_at"] if r2["up_at"] else "never", "%.0f" % r2["unlocked_msg"] if r2["unlocked_msg"] else "-"))
if r2["up_at"] is None: log("STAGE_RESULT failed (boot 2)"); ivd.shutdown(q, hard=True); sys.exit(1)
out = run_phase(ser, "2", CMD2, 1200)
if out is None: log("STAGE_RESULT failed (phase 2)"); ivd.shutdown(q, hard=True); sys.exit(1)
wait_marker(ser, mark2, r2, 240)
verdict(r2["marker_at"] is not None, "boot 2: FABOS_INSTALLED_OK on ttyS0 in the boot where nothing was typed (fabos-firstboot ExecStartPost)")
b2 = section(out, "state-boot2")
verdict("/dev/mapper/" in part(b2, "root") and num(part(out, "plymouth-asked")) == 0, "boot 2: root is the LUKS mapper device and this boot's journal records no password request (uptime %s s)" % part(out, "uptime"))
rc, _, st = jline(out, "status1")
verdict(st and st.get("prompt_at_boot") is False and st.get("consistent") is True, "boot 2: status -> prompt_at_boot=false consistent=true")
rc, secs, res = jline(out, "on")
verdict(rc == 0 and res and res.get("ok") is True and res.get("prompt_at_boot") is True, "boot 2: `on` -> exit %s, ok=%s prompt_at_boot=%s in %s s" % (rc, (res or {}).get("ok"), (res or {}).get("prompt_at_boot"), secs))
a2 = section(out, "state-after-on"); slots_on = num(part(a2, "slots")); ct = part(a2, "crypttab")
verdict(" none " in re.sub(r"\s+", " ", " " + ct + " ") and "luks-unlock.key" not in ct and "KEYFILE_PATTERN" not in part(a2, "conf-hook"), "boot 2: crypttab key column back to 'none', KEYFILE_PATTERN dropped (%s)" % ct.replace("\n", " ")[:120])
verdict("No such file" in part(a2, "key") and "keyfiles" not in part(a2, "lsinitramfs"), "boot 2: keyfile deleted, initrd free of cryptroot/keyfiles")
verdict(slots_on == slots_before, "boot 2: LUKS slot removed (%s -> %s)" % (slots_after, slots_on))
verdict("DONE: the computer asks for the disk password" in part(a2, "log"), "boot 2: disk-unlock.log records the reversal")
rc, _, st = jline(out, "status2")
verdict(st and st.get("prompt_at_boot") is True and st.get("consistent") is True, "boot 2: status -> prompt_at_boot=true consistent=true")
mark3 = ser.mark(); log("the guest reboots itself (boot 3 must ask for the passphrase again)")
verdict(ser.expect(r"BdsDxe: starting Boot", 240, mark3) is not None, "boot 3: firmware restarted the disk (BdsDxe on ttyS0)")

# ------------------------------------------------------------------ boot 3: the prompt is back
r3 = watch_boot(ser, mark3, "boot3", expect_prompt=True, timeout=360, blind_after=150)
verdict(r3["prompt_seen"] is not None, "boot 3: the Plymouth unlock prompt is BACK — recognised by OCR at +%s s (%s)" % ("%.0f" % r3["prompt_seen"] if r3["prompt_seen"] else "never", ", ".join(os.path.basename(s) for s in r3["shots"][:1]) or "no shot"))
verdict(not r3["up_before_typing"], "boot 3: the system did not come up on its own before the passphrase was typed")
verdict(r3["up_at"] is not None and r3["typed_at"] is not None and r3["up_at"] > r3["typed_at"], "boot 3: after the passphrase (+%s s) the system comes up (%s at +%s s)" % ("%.0f" % r3["typed_at"] if r3["typed_at"] else "-", r3["up_via"], "%.0f" % r3["up_at"] if r3["up_at"] else "never"))
if r3["up_at"] is not None:
    out = run_phase(ser, "3", CMD3, 600)
    if out:
        rc, _, st = jline(out, "status1")
        verdict(st and st.get("prompt_at_boot") is True and st.get("consistent") is True and st.get("keyfile_present") is False, "boot 3: status -> prompt_at_boot=true, no keyfile, consistent")
    wait_marker(ser, mark3, r3, 30)
    log("boot 3: FABOS_INSTALLED_OK %s" % ("received" if r3["marker_at"] else "not received before power-off (informational; the login screen after the passphrase is the proof)"))
ivd.shutdown(q)
log("STAGE_RESULT %s (%d check(s) failed)" % ("ok" if not fails else "failed", len(fails)))
sys.exit(1 if fails else 0)
PY
rc=$?
if [ -f "$OUT/qemu.out" ] && grep -q . "$OUT/qemu.out"; then echo "== qemu output:"; tail -3 "$OUT/qemu.out"; fi
npass=$(grep -c '^PASS  ' "$LOG"); nfail=$(grep -c '^FAIL  ' "$LOG")
echo "### disk-unlock-vm: $npass passed, $nfail failed (driver exit $rc) — evidence: $OUT/ (driver.log, serial.log, screenshots + OCR text, guest-*.txt)"
[ "$nfail" = 0 ] && [ "$rc" = 0 ]
