#!/usr/bin/env bash
# Live-VM proof of the installer's user interface with the WORKING-TREE Calamares configuration. The ISO is frozen (its
# /etc/calamares is whatever was built into it), so the working tree's files are injected into the live session and Calamares
# is started a second time - which is exactly what the NEXT ISO build will show users. One QEMU, on the shared VM lock:
#   1. boot the ISO headless (KVM, OVMF, a blank sparse 24 GB target disk so the partition page has a device); no autoinstall,
#      so nothing in the guest starts Calamares on its own
#   2. log in on the live ISO's serial getty (ttyS0: the ISO's kernel line has console=ttyS0; live user fabos, empty password,
#      NOPASSWD sudo) and start Calamares AS SHIPPED IN THE ISO on the live desktop (the launch of live-autoinstall.sh) ->
#      screendumps of the welcome and partition pages = BASELINE. On the 1.0 ISO this reproduces the owner's report: black
#      sidebar with only the current step readable, "Encrypt system" pre-ticked
#   3. copy the working tree's branding.desc + partition.conf (delivered through fw_cfg) over /etc/calamares in the live overlay
#      and restart Calamares -> the same pages again: every sidebar step name must be OCR-readable (welcome, partition and users
#      pages), "Encrypt system" must start UNTICKED, ticking it must show the passphrase fields and unticking hide them again;
#      the driver's encrypt_state() detector (tests/install-vm-driver.py) is compared with what OCR sees at every step
#   4. both Calamares session logs: the baseline one must carry 'Unknown branding *style* entry' lines (the cause of the black
#      sidebar), the fixed one none
# Usage: tests/installer-ui-vm.sh [--out DIR] [--image localhost/fabos:iso] [--mem MB] [--cpus N]    ISO=path overrides the ISO
# Output: <out>/driver.log (this run), serial.log (ttyS0), NNN-*.png + .txt (screendumps + OCR), region-*.png (sidebar / row crops
#   as OCR saw them), guest.txt (what the guest printed); one PASS/FAIL line per check, exit 0 only when all passed.
# Needs: qemu-system-x86_64 + /dev/kvm, OVMF, podman with the ISO image (OCR: tesseract inside it), python3 + Pillow.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"; . brand/brand.conf
ISO=${ISO:-build/${DISTRO_ID}-${DISTRO_VERSION}-desktop-amd64.iso}; OUT=build/installer-ui-vm; IMAGE=localhost/fabos:iso; MEM=2560; CPUS=2; LOCKED=0
while [ $# -gt 0 ]; do case "$1" in
  --out) OUT=$2; shift;; --image) IMAGE=$2; shift;; --mem) MEM=$2; shift;; --cpus) CPUS=$2; shift;; --locked) LOCKED=1;;
  *) echo "unknown arg $1"; exit 2;; esac; shift; done
if [ $LOCKED = 0 ]; then
  echo "== waiting for the shared VM lock (/tmp/fabos-vm.lock; one QEMU at a time on this host)"
  exec flock -w 5400 /tmp/fabos-vm.lock "$0" --locked --out "$OUT" --image "$IMAGE" --mem "$MEM" --cpus "$CPUS"
fi
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd); LOG=$OUT/driver.log; : > "$LOG"; exec > >(tee -a "$LOG") 2>&1
CAL=$HERE/image/overlay/iso/etc/calamares
fail=0; verdict(){ [ "$1" = PASS ] || fail=1; echo "$1  $2"; }
echo "### installer-ui-vm — $(date -u +%FT%TZ) — iso $ISO, config $CAL, evidence $OUT"
[ -f "$ISO" ] || { verdict FAIL "ISO $ISO missing (ISO=path/to/x.iso)"; exit 1; }
[ -e /dev/kvm ] || { verdict FAIL "/dev/kvm missing"; exit 1; }
CODE=$(ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd 2>/dev/null | head -1); VARS_SRC=$(ls /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/ovmf/OVMF_VARS.fd 2>/dev/null | head -1)
[ -n "$CODE" ] && [ -n "$VARS_SRC" ] || { verdict FAIL "OVMF firmware missing"; exit 1; }
podman image exists "$IMAGE" || { verdict FAIL "OCR image $IMAGE missing"; exit 1; }
python3 -c "import PIL" 2>/dev/null || { verdict FAIL "python3 Pillow missing (sidebar region OCR needs it)"; exit 1; }
[ -z "$(ps -C qemu-system-x86_64 -o pid= 2>/dev/null)" ] || { verdict FAIL "another qemu-system-x86_64 is running although the lock is held; refusing to start a second VM"; exit 1; }
verdict PASS "preconditions: ISO, KVM, OVMF, OCR image, Pillow, no other VM"

TAG=$$; DISK=$OUT/.target-$TAG.img; VARS=$OUT/.vars-$TAG.fd; QMP=/tmp/installer-ui-$TAG.qmp; SER=/tmp/installer-ui-$TAG.serial.sock; GUEST=$OUT/.ui.sh
cleanup() {
  if [ -n "${QPID:-}" ] && kill -0 "$QPID" 2>/dev/null; then echo "== stopping qemu (pid $QPID)"; kill "$QPID" 2>/dev/null; sleep 3; kill -9 "$QPID" 2>/dev/null; fi
  pkill -KILL -f -- "qmp unix:$QMP" 2>/dev/null
  rm -f "$DISK" "$VARS" "$QMP" "$SER" "$GUEST"
}
trap cleanup EXIT
truncate -s 24G "$DISK"; cp "$VARS_SRC" "$VARS"; : > "$OUT/serial.log"

# ---- the guest-side script (root, delivered through fw_cfg, run from the serial shell). Calamares is launched exactly as
# live-autoinstall.sh does it (root, on the live user's Wayland display, -D6 session log in /root/.cache/calamares).
cat > "$GUEST" <<'GUEST'
#!/bin/bash
P=$1; FW=/sys/firmware/qemu_fw_cfg/by_name/opt/fabos; LOG=/root/.cache/calamares/session.log
pid=$(pgrep -u fabos -n plasmashell); envof(){ tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n "s/^$1=//p" | head -1; }
launch(){ mkdir -p /root/.cache/calamares; rm -f "$LOG"
  WD=$(envof WAYLAND_DISPLAY); XRD=$(envof XDG_RUNTIME_DIR); DISP=$(envof DISPLAY); XA=$(envof XAUTHORITY); DBUS=$(envof DBUS_SESSION_BUS_ADDRESS)
  nohup setsid env -i HOME=/root USER=root LOGNAME=root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=en_US.UTF-8 \
    ${WD:+WAYLAND_DISPLAY=$WD} ${XRD:+XDG_RUNTIME_DIR=$XRD} ${DISP:+DISPLAY=$DISP} ${XA:+XAUTHORITY=$XA} ${DBUS:+DBUS_SESSION_BUS_ADDRESS=$DBUS} \
    QT_QPA_PLATFORM="wayland;xcb" calamares -D6 > /var/log/fabos-ui-$1.log 2>&1 < /dev/null &
  for i in $(seq 1 90); do [ -s "$LOG" ] && break; sleep 1; done; sleep 8
  echo "UI_READY $1 pid=$(pgrep -x calamares | head -1) log=$( [ -s "$LOG" ] && echo yes || echo no) session=${WD:-none}"; }
stop(){ pkill -x calamares 2>/dev/null; sleep 2; pkill -9 -x calamares 2>/dev/null; sleep 1; }
case "$P" in
  baseline) dmesg -n 1 2>/dev/null; echo "SHIPPED $(sha256sum /etc/calamares/branding/fabos/branding.desc /etc/calamares/modules/partition.conf | cut -c1-16 | tr '\n' ' ')"; launch baseline;;
  inject) stop; cp "$LOG" /root/session-baseline.log 2>/dev/null
    install -m644 $FW/branding.desc/raw /etc/calamares/branding/fabos/branding.desc && install -m644 $FW/partition.conf/raw /etc/calamares/modules/partition.conf
    echo "INJECTED $(sha256sum /etc/calamares/branding/fabos/branding.desc /etc/calamares/modules/partition.conf | cut -c1-16 | tr '\n' ' ')"
    launch inject;;
  stop) stop; cp "$LOG" /root/session-inject.log 2>/dev/null
    echo "STYLE_BASELINE=$(grep -c 'Unknown branding \*style\* entry' /root/session-baseline.log 2>/dev/null)"
    echo "STYLE_INJECT=$(grep -c 'Unknown branding \*style\* entry' /root/session-inject.log 2>/dev/null)"
    grep -h 'style\* entry' /root/session-baseline.log 2>/dev/null | sed 's/^.*WARNING: *//; s/^ *\.\. *//; s/^/BASELINE| /'
    grep -h -i 'branding' /root/session-inject.log 2>/dev/null | grep -iE 'style|stylesheet' | sed 's/^/INJECT| /'
    echo "UI_DONE";;
esac
GUEST

# ---- QEMU: the arguments of scripts/boot-iso.sh (q35/KVM, OVMF, ISO on AHCI, virtio target disk, usb-tablet + usb-kbd for
# QMP input, virtio-vga headless) with the serial port as a unix SOCKET that also logs to serial.log, fw_cfg carrying the guest
# script and the two working-tree files, and no network (nothing here needs it; no port clash with another VM).
ACCEL=kvm
args=( -name "Fab OS installer UI test" -machine q35,accel=$ACCEL -cpu host -smp "$CPUS" -m "$MEM"
  -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$VARS"
  -drive file="$ISO",media=cdrom,if=none,id=cd -device ahci,id=ahci -device ide-cd,drive=cd,bus=ahci.0,bootindex=1
  -drive file="$DISK",if=virtio,format=raw,cache=writeback,discard=unmap
  -device qemu-xhci -device usb-tablet -device usb-kbd -device virtio-rng-pci -nic none
  -chardev socket,id=ser0,path="$SER",server=on,wait=off,logfile="$OUT/serial.log" -serial chardev:ser0
  -qmp unix:"$QMP",server,nowait -monitor none -rtc base=utc -display none -device virtio-vga -no-reboot
  -fw_cfg name=opt/fabos/ui.sh,file="$GUEST" -fw_cfg name=opt/fabos/branding.desc,file="$CAL/branding/fabos/branding.desc"
  -fw_cfg name=opt/fabos/partition.conf,file="$CAL/modules/partition.conf" )
export RG_MEMMAX=${RG_MEMMAX:-$((MEM+1200))M} RG_MEMHIGH=${RG_MEMHIGH:-$((MEM+900))M}
echo "== booting $ISO: mem=${MEM}M cpus=$CPUS (serial -> $OUT/serial.log, socket $SER, qmp $QMP, target disk $DISK)"
tools/rg --profile vm -- qemu-system-x86_64 "${args[@]}" > "$OUT/qemu.out" 2>&1 &
QPID=$!
sleep 2; kill -0 $QPID 2>/dev/null || { cat "$OUT/qemu.out"; verdict FAIL "qemu did not start"; exit 1; }

# ---- the driver: QMP / screendump+OCR / pointer / keys / encrypt_state come from tests/install-vm-driver.py; the serial socket is ours
python3 - "$QMP" "$SER" "$OUT" "$IMAGE" "$HERE/tests/install-vm-driver.py" "$CAL" <<'PY'
import difflib, hashlib, importlib.machinery, importlib.util, os, re, socket, sys, threading, time
QMP_PATH, SER_PATH, OUT, IMAGE, DRIVER, CAL = sys.argv[1:7]
loader = importlib.machinery.SourceFileLoader("ivd", DRIVER); spec = importlib.util.spec_from_loader("ivd", loader); ivd = importlib.util.module_from_spec(spec); loader.exec_module(ivd)
T0 = time.time(); ivd.T0 = T0
def log(m): print("[%6.1fs] %s" % (time.time() - T0, m), flush=True)
fails = []
def verdict(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: fails.append(what)
ANSI = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]")

class Serial:
    """The guest's ttyS0 over QEMU's chardev socket (the live ISO runs a getty there): reader thread + expect()."""
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
    def send(self, s): self.s.sendall(s.encode())
    def run(self, cmd, rx, timeout):
        """Types cmd into the serial shell and waits for rx in the output that follows it; returns (match, output)."""
        mk = self.mark(); self.send(cmd + "\n"); m = self.expect(rx, timeout, mk); return m, self.text(mk)

STEPS = ["Welcome", "Location", "Keyboard", "Partitions", "Users", "Summary", "Install", "Finish"]   # the sidebar of settings.conf's show sequence
def sidebar_names(shot, w, h, label):
    """The step names OCR can read in the sidebar of a screenshot (window 900x600 centred: sidebar = left ~15% of the frame,
    below the title bar). Returns (found, missing, raw words)."""
    box = (int(w * 0.09), int(h * 0.165), int(w * 0.26), int(h * 0.93))
    words = scr.ocr_region(shot, box, label + "-sidebar", thresholds=(96,))
    # the CURRENT step is the one row of opposite polarity (dark text on the highlight) inside a light-on-dark block; tesseract
    # drops it whatever the threshold, but reads it alone: find the highlight band (rows of mid luminance) and OCR that strip
    from PIL import Image, ImageOps
    g = ImageOps.grayscale(Image.open(shot).convert("RGB").crop(box)); rows = [sum(g.crop((0, y, g.width, y + 1)).getdata()) / float(g.width) for y in range(g.height)]
    band = [y for y, m in enumerate(rows) if 100 < m < 200]
    if band and max(band) - min(band) < 80:
        strip = (box[0], box[1] + max(0, min(band) - 4), box[2], box[1] + max(band) + 5)
        words += scr.ocr_region(shot, strip, label + "-sidebar-current", psm=7)
    seen = [re.sub(r"[^a-z]", "", t.lower()) for t, *_ in words]
    found = [s for s in STEPS if any(c == s.lower() or (len(c) >= 4 and difflib.SequenceMatcher(None, c, s.lower()).ratio() >= 0.8) for c in seen)]
    return found, [s for s in STEPS if s not in found], [t for t, *_ in words]
def row_has_passphrase(shot, anchor, w, h, label):
    """OCR of the checkbox row right of the label, upscaled: the 'Passphrase' / 'Confirm passphrase' placeholders."""
    x0 = anchor[0] + 60; box = (x0, max(0, anchor[1] - 24), min(w, x0 + 520), min(h, anchor[1] + 24))
    words = scr.ocr_region(shot, box, label + "-row", thresholds=(200,))   # 200: light-grey placeholder text inside the white line edit
    return any(re.sub(r"[^a-z]", "", t.lower()) == "passphrase" for t, *_ in words), [t for t, *_ in words]
def go_to(page, label, max_steps=8):
    """Alt+N through welcome/location/keyboard until `page` is on screen; returns a grab of it labelled <label>-<page>, or None."""
    for _ in range(max_steps):
        text, words, w, h, shot = scr.grab(label + "-poll"); cur = ivd.classify(text)
        if cur == page: return scr.grab(label + "-" + page)
        if cur in ("welcome", "location", "keyboard"): ivd.alt(q, "n"); time.sleep(2.5)
        elif cur == "partition" and page == "users": ivd.alt(q, "n"); time.sleep(3)
        else: time.sleep(2.5)
    return None
def state_of(grab, label):
    text, words, w, h, shot = grab; st, anchor, why = ivd.encrypt_state(words, shot)
    log("%s: encrypt_state -> %s (%s)" % (label, st, why)); return st, anchor, why

ser = Serial(SER_PATH); q = ivd.QMP(QMP_PATH); scr = ivd.Screen(q, OUT, IMAGE)
log("QMP + serial up; waiting for the live session (FABOS_LIVE_OK on ttyS0)")
m = ser.expect(r"FABOS_LIVE_OK", 480)
verdict(m is not None, "live ISO booted to the desktop (FABOS_LIVE_OK on the serial console)")
if not m: log("STAGE_RESULT failed (no live session)"); sys.exit(1)
time.sleep(3)
# ---- serial login: agetty prompt -> fabos (empty password: pam_unix nullok skips the Password prompt; answer it if it comes)
ok = False
for attempt in range(6):
    mk = ser.mark(); ser.send("\n"); m = ser.expect(r"login:", 8, mk)
    if not m: continue
    mk = ser.mark(); ser.send("fabos\n"); m = ser.expect(r"(Password:|\$ )", 20, mk)
    if m and "Password" in m.group(0): mk = ser.mark(); ser.send("\n"); m = ser.expect(r"\$ ", 20, mk)
    if m: ok = True; break
verdict(ok, "serial getty on ttyS0: logged in as the live user fabos (attempt %d)" % (attempt + 1))
if not ok: log("STAGE_RESULT failed (no shell)"); sys.exit(1)
m, out = ser.run("sudo sh -c 'modprobe qemu_fw_cfg; install -m755 /sys/firmware/qemu_fw_cfg/by_name/opt/fabos/ui.sh/raw /usr/local/sbin/fabos-ui && echo UI_INSTALLED'", r"UI_INSTALLED", 30)
verdict(m is not None, "guest script installed from fw_cfg (sudo without password)")
if not m: log(out[-400:]); sys.exit(1)
guest = []
def shas(paths): return " ".join(hashlib.sha256(open(p, "rb").read()).hexdigest()[:16] for p in paths)
local = shas([CAL + "/branding/fabos/branding.desc", CAL + "/modules/partition.conf"])

# ================================================================= baseline: Calamares as shipped in the ISO
m, out = ser.run("sudo fabos-ui baseline", r"UI_READY baseline.*", 150); guest.append(out)
verdict(m is not None and "log=yes" in (m.group(0) if m else ""), "baseline: Calamares (as shipped in the ISO) started on the live desktop (%s)" % (m.group(0).strip() if m else out[-200:]))
if not m: sys.exit(1)
shipped = re.search(r"SHIPPED (\S+) (\S+)", out); shipped = "%s %s" % shipped.groups() if shipped else "?"
log("shipped config sha256 prefixes: %s; working tree: %s" % (shipped, local))
time.sleep(2)
g = go_to("welcome", "baseline")
verdict(g is not None, "baseline: welcome page recognised")
if g:
    found, missing, raw = sidebar_names(g[4], g[2], g[3], "baseline-welcome")
    log("baseline welcome sidebar OCR words: %s" % raw)
    verdict(len(found) <= 2, "baseline welcome page reproduces the report: only %d of 8 step names readable in the sidebar (%s) - missing %s" % (len(found), found, missing))
g = go_to("partition", "baseline")
verdict(g is not None, "baseline: partition page reached (Alt+N through location and keyboard)")
if g:
    found, missing, raw = sidebar_names(g[4], g[2], g[3], "baseline-partition")
    verdict(len(found) <= 2, "baseline partition page: only %d of 8 step names readable (%s) - the owner's photo (current step readable, others black on black)" % (len(found), found))
    st, anchor, why = state_of(g, "baseline partition")
    seen, raw = row_has_passphrase(g[4], anchor, g[2], g[3], "baseline-partition") if anchor else (None, [])
    verdict(st == "ticked" and seen, "baseline: 'Encrypt system' is pre-ticked on this ISO - detector %s, passphrase placeholders %s in the row OCR %s" % (st, "seen" if seen else "NOT seen", raw))
    if anchor:
        q.pointer(anchor[0] + 20, anchor[1], g[2], g[3]); time.sleep(1.5)
        g2 = scr.grab("baseline-partition-after-untick"); st2, a2, why2 = state_of(g2, "baseline after one click")
        seen2, raw2 = row_has_passphrase(g2[4], a2 or anchor, g2[2], g2[3], "baseline-unticked")
        verdict(st2 == "unticked" and not seen2, "baseline: one click on the label unticks it (what the plain install driver does on this ISO) - detector %s, placeholders %s" % (st2, "gone" if not seen2 else "still there: %s" % raw2))

# ================================================================= fixed: the working tree's branding.desc + partition.conf
m, out = ser.run("sudo fabos-ui inject", r"UI_READY inject.*", 180); guest.append(out)
inj = re.search(r"INJECTED (\S+) (\S+)", out)
verdict(inj is not None and "%s %s" % inj.groups() == local, "inject: the working tree's branding.desc + partition.conf are now in the live /etc/calamares (sha256 %s)" % local)
verdict(m is not None and "log=yes" in (m.group(0) if m else ""), "inject: Calamares restarted with the working-tree configuration (%s)" % (m.group(0).strip() if m else out[-200:]))
if not m: sys.exit(1)
time.sleep(2)
g = go_to("welcome", "fixed")
verdict(g is not None, "fixed: welcome page recognised")
if g:
    found, missing, raw = sidebar_names(g[4], g[2], g[3], "fixed-welcome")
    verdict(not missing, "fixed welcome page: every sidebar step name readable by OCR: %s%s" % (found, "" if not missing else " - MISSING %s (raw %s)" % (missing, raw)))
g = go_to("partition", "fixed")
verdict(g is not None, "fixed: partition page reached")
if g:
    w, h = g[2], g[3]
    found, missing, raw = sidebar_names(g[4], w, h, "fixed-partition")
    verdict(not missing, "fixed partition page: every sidebar step name readable by OCR: %s%s" % (found, "" if not missing else " - MISSING %s (raw %s)" % (missing, raw)))
    st, anchor, why = state_of(g, "fixed partition")
    seen, raw = row_has_passphrase(g[4], anchor, w, h, "fixed-partition") if anchor else (None, [])
    verdict(st == "unticked" and anchor is not None and not seen, "fixed: 'Encrypt system' starts UNTICKED (preCheckEncryption: false) - detector %s, no passphrase placeholder in the row (%s)" % (st, raw))
    if anchor:
        q.pointer(anchor[0] + 20, anchor[1], w, h); time.sleep(1.5)
        g2 = scr.grab("fixed-partition-ticked"); st2, a2, why2 = state_of(g2, "fixed after ticking")
        seen2, raw2 = row_has_passphrase(g2[4], a2 or anchor, w, h, "fixed-ticked")
        verdict(st2 == "ticked" and seen2, "fixed: the user can opt in - one click ticks the box and the Passphrase / Confirm passphrase fields appear (detector %s, row OCR %s)" % (st2, raw2))
        q.pointer(anchor[0] + 20, anchor[1], w, h); time.sleep(1.5)
        g3 = scr.grab("fixed-partition-unticked-again"); st3, a3, why3 = state_of(g3, "fixed after unticking")
        seen3, raw3 = row_has_passphrase(g3[4], a3 or anchor, w, h, "fixed-unticked")
        verdict(st3 == "unticked" and not seen3, "fixed: a second click unticks it again and the fields disappear (detector %s)" % st3)
    g4 = go_to("users", "fixed", max_steps=4)
    verdict(g4 is not None, "fixed: Next from the unencrypted partition page reaches the users page")
    if g4:
        found, missing, raw = sidebar_names(g4[4], g4[2], g4[3], "fixed-users")
        verdict(not missing, "fixed users page: every sidebar step name readable by OCR: %s%s" % (found, "" if not missing else " - MISSING %s (raw %s)" % (missing, raw)))

# ================================================================= session logs
m, out = ser.run("sudo fabos-ui stop", r"UI_DONE", 60); guest.append(out)
sb = re.search(r"STYLE_BASELINE=(\d+)", out); si = re.search(r"STYLE_INJECT=(\d+)", out)
verdict(sb is not None and int(sb.group(1)) >= 1, "baseline session log: Calamares rejected the shipped style keys ('Unknown branding *style* entry' x %s: %s)" % (sb.group(1) if sb else "?", "; ".join(l[10:].strip() for l in out.splitlines() if l.startswith("BASELINE|"))[:300]))
verdict(si is not None and int(si.group(1)) == 0, "fixed session log: no 'Unknown branding *style* entry' with the working-tree branding.desc (count %s)" % (si.group(1) if si else "?"))
with open(os.path.join(OUT, "guest.txt"), "w") as f: f.write("\n".join(guest))
try: q.cmd("quit")
except Exception: pass
log("STAGE_RESULT %s (%d check(s) failed)" % ("ok" if not fails else "failed", len(fails)))
sys.exit(1 if fails else 0)
PY
rc=$?
if [ -f "$OUT/qemu.out" ] && grep -q . "$OUT/qemu.out"; then echo "== qemu output:"; tail -3 "$OUT/qemu.out"; fi
npass=$(grep -c '^PASS  ' "$LOG"); nfail=$(grep -c '^FAIL  ' "$LOG")
echo "### installer-ui-vm: $npass passed, $nfail failed (driver exit $rc) — evidence: $OUT/ (driver.log, serial.log, screenshots + OCR text, region crops, guest.txt)"
[ "$nfail" = 0 ] && [ "$rc" = 0 ]
