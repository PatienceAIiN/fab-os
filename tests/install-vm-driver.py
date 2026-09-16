#!/usr/bin/env python3
"""Host-side driver for tests/install-vm.sh: steers Calamares in the live VM and unlocks the installed system.

Everything the guest needs from a human comes from here, over QEMU's QMP socket:
  * screendump  -> OCR (tesseract inside localhost/fabos:iso, run with podman on the host; the host needs no tesseract)
  * send-key    -> keystrokes through the emulated keyboard (Alt+N/Alt+I mnemonics of Calamares' Next/Install buttons,
                   Tab, text); KWin 6.6 has no virtual-keyboard protocol, so in-guest wtype cannot do this
  * input-send-event -> absolute pointer moves + clicks on the usb-tablet (to focus the passphrase field / the encryption
                   checkbox at the coordinates OCR found for their placeholder/label text)
The guest side (image/overlay/iso/usr/lib/fabos/live-autoinstall.sh) launches Calamares, follows its session log and
reports over the serial console; this driver reads that log. Success = the guest's INSTALL_RESULT=ok, which the helper
derives from the finished page being reached (any of the three Config::doNotify lines of the finished module - as root
the desktop notification itself can never be sent, so "completion: succeeded" alone is NOT waited for) with no
ViewManager::onInstallationFailed line ("Installation failed:" / "- message:") before it. The Calamares session log
arrives gzip+base64-encoded between CALAMARES_LOG_BEGIN/END with a sha256 (decode_log_block verifies it).
Page recognition uses the page BODY texts of Calamares 3.3.14
(src/modules/*: "Welcome to the %1 installer", "Region:"/"Zone:", "Keyboard Model", "Select storage device:",
"What is your name?", "This is an overview of what will happen", "Continue with Installation?"), never the sidebar.

  install-vm-driver.py install --qmp SOCK --serial LOG --variant luks|plain --outdir DIR [--image localhost/fabos:iso]
  install-vm-driver.py boot    --qmp SOCK --serial LOG --variant luks|plain --outdir DIR [--passphrase fabos-test]
Exit 0 = stage passed. Screenshots (PNG when Pillow is available, else PPM) + OCR text land in DIR as evidence.
"""
import argparse, atexit, json, os, re, shutil, socket, subprocess, sys, time

PASSPHRASE = "fabos-test"; FULLNAME = "Fab Tester"; USERNAME = "fabtest"
T0 = time.time()

def log(msg):
    print("[%6.1fs] %s" % (time.time() - T0, msg), flush=True)

# ---------------------------------------------------------------- QMP
class QMP:
    def __init__(self, path, wait=90):
        self.s = None; deadline = time.time() + wait
        while time.time() < deadline:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(30); s.connect(path)
                self.s = s; self.buf = b""; self._read_obj()          # greeting
                self.cmd("qmp_capabilities"); return
            except (OSError, ValueError):
                if self.s: self.s.close(); self.s = None
                time.sleep(1)
        raise SystemExit("QMP socket %s never came up" % path)
    def _read_obj(self):
        while True:
            if b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if line.strip():
                    return json.loads(line)
                continue
            chunk = self.s.recv(65536)
            if not chunk: raise OSError("QMP closed")
            self.buf += chunk
    def cmd(self, name, **args):
        self.s.sendall((json.dumps({"execute": name, "arguments": args}) + "\n").encode())
        while True:
            o = self._read_obj()
            if "return" in o: return o["return"]
            if "error" in o: raise RuntimeError("%s: %s" % (name, o["error"]))
            # events are ignored
    def alive(self):
        try: self.cmd("query-status"); return True
        except Exception: return False
    def send_keys(self, qcodes, hold=None):
        a = {"keys": [{"type": "qcode", "data": k} for k in qcodes]}
        if hold: a["hold-time"] = hold
        self.cmd("send-key", **a)
    def screendump(self, path):
        self.cmd("screendump", filename=path)
    def pointer(self, x, y, w, h, click=True):
        ev = [{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / max(1, w - 1))}},
              {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / max(1, h - 1))}}]
        self.cmd("input-send-event", events=ev); time.sleep(0.15)
        if click:
            self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": True, "button": "left"}}]); time.sleep(0.08)
            self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": False, "button": "left"}}]); time.sleep(0.25)

CHARS = {" ": "spc", "-": "minus", ".": "dot", ",": "comma", "/": "slash", ";": "semicolon", "'": "apostrophe", "=": "equal",
         "[": "bracket_left", "]": "bracket_right", "\\": "backslash", "`": "grave_accent", "\n": "ret", "\t": "tab"}
SHIFTED = {"_": "minus", "@": "2", "!": "1", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
           ":": "semicolon", '"': "apostrophe", "<": "comma", ">": "dot", "?": "slash", "+": "equal", "{": "bracket_left",
           "}": "bracket_right", "|": "backslash", "~": "grave_accent"}
def type_text(q, text, delay=0.09):
    for ch in text:
        if ch.isalpha() and ch.isupper(): q.send_keys(["shift", ch.lower()], hold=60)
        elif ch.isalnum(): q.send_keys([ch], hold=60)
        elif ch in CHARS: q.send_keys([CHARS[ch]], hold=60)
        elif ch in SHIFTED: q.send_keys(["shift", SHIFTED[ch]], hold=60)
        else: raise ValueError("cannot type %r" % ch)
        time.sleep(delay)
def alt(q, letter): q.send_keys(["alt", letter], hold=120); time.sleep(0.4)

# ---------------------------------------------------------------- serial log
class Serial:
    ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
    def __init__(self, path): self.path = path; self.pos = 0; self.lines = []; self.partial = b""
    def poll(self):
        """Appends the COMPLETE new lines of the serial log (a line still being written stays in self.partial, so a
        marker can never be split across two polls)."""
        new = []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos); data = f.read(); self.pos += len(data)
        except FileNotFoundError:
            return new
        parts = (self.partial + data).split(b"\n"); self.partial = parts[-1]
        for raw in parts[:-1]:
            s = self.ansi.sub("", raw.decode("utf-8", "replace")).rstrip()
            if s: self.lines.append(s); new.append(s)
        return new
    def find(self, rx):
        r = re.compile(rx)
        for l in self.lines:
            m = r.search(l)
            if m: return m
        return None
    def wait(self, rx, timeout, on_line=None, abort_rx=None):
        deadline = time.time() + timeout; r = re.compile(rx); ab = re.compile(abort_rx) if abort_rx else None
        while time.time() < deadline:
            for l in self.poll():
                if on_line: on_line(l)
                if r.search(l): return l
                if ab and ab.search(l): return None
            time.sleep(1)
        return None

# ---------------------------------------------------------------- screenshots + OCR
class Screen:
    def __init__(self, qmp, outdir, image):
        self.q = qmp; self.out = os.path.abspath(outdir); self.image = image; self.n = 0; self.ctr = None
        os.makedirs(self.out, exist_ok=True)
        try:
            from PIL import Image  # noqa
            self.pil = True
        except Exception:
            self.pil = False
        name = "fabos-ocr-%d" % os.getpid()
        r = subprocess.run(["podman", "run", "-d", "--rm", "--network", "none", "--name", name, "-v", self.out + ":/w:Z", image, "sleep", "infinity"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            self.ctr = name; atexit.register(lambda: subprocess.run(["podman", "rm", "-f", name], capture_output=True))
        else:
            log("OCR container could not start (%s); falling back to podman run per shot" % r.stderr.strip()[:200])
    def grab(self, label):
        """Returns (text_lower, words[(text,x,y,w,h)], width, height, png_or_ppm_path)."""
        self.n += 1; base = "%03d-%s" % (self.n, re.sub(r"[^a-z0-9]+", "-", label.lower()))
        ppm = os.path.join(self.out, base + ".ppm"); self.q.screendump(ppm)
        for _ in range(30):
            if os.path.exists(ppm) and os.path.getsize(ppm) > 100: break
            time.sleep(0.2)
        w, h = self._ppm_size(ppm); ocr_src = base + ".ppm"; scale = 1; shot = ppm
        if self.pil:
            from PIL import Image, ImageOps
            im = Image.open(ppm).convert("RGB"); im.save(os.path.join(self.out, base + ".png")); os.unlink(ppm); shot = os.path.join(self.out, base + ".png")
            g = ImageOps.grayscale(im)
            if sum(g.resize((64, 48)).getdata()) / (64 * 48) < 90: g = ImageOps.invert(g)   # light text on dark (Plymouth, GRUB)
            scale = 2; g = g.resize((w * scale, h * scale), Image.LANCZOS); g.save(os.path.join(self.out, "ocr-input.png")); ocr_src = "ocr-input.png"
        tsv = self._tesseract(ocr_src)
        words = []
        for line in tsv.splitlines()[1:]:
            p = line.split("\t")
            if len(p) >= 12 and p[0] == "5" and p[11].strip():
                try: words.append((p[11].strip(), int(p[6]) // scale, int(p[7]) // scale, int(p[8]) // scale, int(p[9]) // scale))
                except ValueError: pass
        text = re.sub(r"\s+", " ", " ".join(t for t, *_ in words)).lower()
        with open(os.path.join(self.out, base + ".txt"), "w") as f: f.write(text + "\n")
        return text, words, w, h, shot
    def _ppm_size(self, path):
        with open(path, "rb") as f:
            head = f.read(64).split()
        return int(head[1]), int(head[2])
    def _tesseract(self, name):
        if self.ctr:
            r = subprocess.run(["podman", "exec", self.ctr, "tesseract", "/w/" + name, "stdout", "--psm", "3", "tsv"], capture_output=True, text=True, timeout=120)
            if r.returncode == 0: return r.stdout
            log("podman exec tesseract failed (%s)" % r.stderr.strip()[-200:])
        r = subprocess.run(["podman", "run", "--rm", "--network", "none", "-v", self.out + ":/w:Z", self.image, "tesseract", "/w/" + name, "stdout", "--psm", "3", "tsv"],
                           capture_output=True, text=True, timeout=180)
        return r.stdout

def find_word(words, target, exclude_prev=None):
    """Centre of the first OCR word equal (case-insensitive, punctuation-stripped) to target; exclude_prev skips a word
    whose predecessor is that text (e.g. the 'passphrase' of 'Confirm passphrase')."""
    prev = None
    for t, x, y, w, h in words:
        clean = re.sub(r"[^a-z]", "", t.lower())
        if clean == target and not (exclude_prev and prev == exclude_prev):
            return x + w // 2, y + h // 2
        prev = clean
    return None

# ---------------------------------------------------------------- stage 1: drive Calamares
def stage_install(a):
    ser = Serial(a.serial); q = QMP(a.qmp); scr = Screen(q, a.outdir, a.image)
    log("QMP up; waiting for the live session and the autoinstall helper (serial %s)" % a.serial)
    l = ser.wait(r"AUTOINSTALL_UI_READY", a.timeout_live, on_line=lambda s: log("serial: " + s) if re.match(r"(FABOS_LIVE|AUTOINSTALL|INSTALL_RESULT|PLASMA|SDDM|CALAMARES)", s) else None,
                 abort_rx=r"INSTALL_RESULT=")
    if not l:
        if ser.find(r"INSTALL_RESULT="): log("guest reported a result before the UI came up: " + ser.find(r"INSTALL_RESULT=.*").group(0))
        else: log("no AUTOINSTALL_UI_READY within %ds" % a.timeout_live)
        scr.grab("no-ui"); return fail_install(ser, a, q)
    log("Calamares is up; driving the pages by OCR")
    done = {"partition": False, "users": False, "summary": False, "confirm": False}; attempts = {}; last_page = None; same_since = time.time()
    deadline = time.time() + a.timeout_ui; installing = False
    while time.time() < deadline:
        for s in ser.poll():
            if s.startswith(("CALAMARES_JOB:", "INSTALL_RESULT=", "AUTOINSTALL")): log("serial: " + s)
            if s.startswith("CALAMARES_JOB:"): installing = True
            if s.startswith("INSTALL_RESULT="): installing = True
        if installing: break
        try:
            text, words, w, h, shot = scr.grab("poll")
        except Exception as e:
            if not q.alive(): log("QEMU is gone (%s)" % e); return fail_install(ser, a, q)
            log("screendump/OCR failed (%s); retrying" % e); time.sleep(3); continue
        page = classify(text)
        if page != last_page: log("page: %s  (%s)" % (page, os.path.basename(shot))); last_page = page; same_since = time.time()
        elif time.time() - same_since > 150 and page not in ("install", None):
            log("stuck on page %s for 150 s; last OCR: %s" % (page, text[:300])); return fail_install(ser, a, q)
        if page == "failed":
            log("Calamares shows Installation Failed"); return fail_install(ser, a, q)
        elif page == "confirm":
            if not done["confirm"]: log("confirm dialog -> Alt+I (&Install Now)"); done["confirm"] = True
            alt(q, "i"); time.sleep(3)
        elif page == "summary":
            log("summary -> Alt+I (&Install)"); alt(q, "i"); time.sleep(2.5)
        elif page == "users":
            if not done["users"]:
                log("users page: full name / login / (hostname) / password x2 via keyboard, focus starts in the name field (UsersPage::onActivate)")
                time.sleep(1.0)
                type_text(q, FULLNAME); time.sleep(0.6); q.send_keys(["tab"]); time.sleep(0.4)
                type_text(q, USERNAME); time.sleep(0.6); q.send_keys(["tab"]); time.sleep(0.4)   # hostname (auto-filled) -> keep
                q.send_keys(["tab"]); time.sleep(0.4)
                type_text(q, PASSPHRASE); time.sleep(0.4); q.send_keys(["tab"]); time.sleep(0.4)
                type_text(q, PASSPHRASE); time.sleep(1.0)
                t2, *_ = scr.grab("users-filled")
                if "fab tester" not in t2 or "fabtest" not in t2:
                    log("WARNING: OCR after typing does not show both 'Fab Tester' and 'fabtest': %s" % t2[:300])
                done["users"] = True
            attempts["users"] = attempts.get("users", 0) + 1
            if attempts["users"] > 6: log("users page did not accept Next"); return fail_install(ser, a, q)
            log("users -> Alt+N"); alt(q, "n"); time.sleep(3)
        elif page == "partition":
            if not done["partition"]:
                if a.variant == "luks":
                    p = find_word(words, "passphrase", exclude_prev="confirm")
                    if not p:
                        # the placeholder "Passphrase" is light grey and OCR often misses it; the field sits on the same row
                        # as the "Encrypt system" checkbox label, ~190 px to the right of the word "Encrypt" (1280x800 layout)
                        e = find_word(words, "encrypt")
                        if e: p = (e[0] + 190, e[1]); log("passphrase placeholder not read by OCR; using the row of 'Encrypt system' at %s" % (p,))
                    if not p:
                        log("passphrase field not found by OCR (words: %s)" % [t for t, *_ in words][:60]); return fail_install(ser, a, q, wait_guest=False)
                    log("partition page: click the Passphrase field at %s, type passphrase, Tab, repeat" % (p,))
                    q.pointer(p[0], p[1], w, h); time.sleep(0.4)
                    type_text(q, PASSPHRASE); time.sleep(0.4); q.send_keys(["tab"]); time.sleep(0.4); type_text(q, PASSPHRASE); time.sleep(1.0)
                    scr.grab("partition-passphrase-typed")
                else:
                    p = find_word(words, "encrypt")
                    if not p:
                        log("'Encrypt system' checkbox not found by OCR (words: %s)" % [t for t, *_ in words][:60]); return fail_install(ser, a, q, wait_guest=False)
                    log("partition page: untick 'Encrypt system' at %s" % (p,)); q.pointer(p[0], p[1], w, h); time.sleep(1.2)
                    t2, w2, *_ = scr.grab("partition-unticked")
                    if find_word(w2, "passphrase"):
                        log("passphrase fields still visible after the click -> clicking once more"); q.pointer(p[0], p[1], w, h); time.sleep(1.2); scr.grab("partition-unticked-2")
                done["partition"] = True
            attempts["partition"] = attempts.get("partition", 0) + 1
            if attempts["partition"] > 6: log("partition page did not accept Next"); return fail_install(ser, a, q)
            log("partition -> Alt+N"); alt(q, "n"); time.sleep(3)
        elif page in ("keyboard", "location", "welcome"):
            log("%s -> Alt+N" % page); alt(q, "n"); time.sleep(2.5)
        elif page == "install":
            log("execution page visible (slideshow)"); time.sleep(3)
        else:
            time.sleep(2.5)
    if not installing:
        log("UI phase timed out"); return fail_install(ser, a, q)
    # ---- jobs are running: follow the guest's report
    log("installation running; following CALAMARES_JOB lines (timeout %ds)" % a.timeout_install)
    last_shot = time.time()
    def on_line(s):
        nonlocal last_shot
        if s.startswith(("CALAMARES_JOB:", "INSTALL_RESULT=", "AUTOINSTALL", "INSTALL_FAIL")): log("serial: " + s)
    end = time.time() + a.timeout_install; res = None
    while time.time() < end:
        for s in ser.poll():
            on_line(s)
            if s.startswith("INSTALL_RESULT="): res = s
        if res: break
        if time.time() - last_shot > 240:
            try: scr.grab("installing"); last_shot = time.time()
            except Exception as e: log("screenshot failed: %s" % e); last_shot = time.time()
        time.sleep(3)
    if not res:
        log("no INSTALL_RESULT within %ds" % a.timeout_install); return fail_install(ser, a, q)
    ok = "INSTALL_RESULT=ok" in res
    try:
        if ok: scr.grab("finished")
    except Exception: pass
    l = ser.wait(r"AUTOINSTALL_END", 600)
    save_guest_evidence(ser, a)
    log("STAGE_RESULT install %s (%s)" % ("ok" if ok else "failed", res))
    return 0 if ok else 1

def classify(text):
    if "installation failed" in text: return "failed"
    if "continue with installation" in text: return "confirm"
    if "this is an overview of what will happen" in text or "overview of what will happen" in text: return "summary"
    if "what is your name" in text: return "users"
    if "select storage device" in text or ("erase disk" in text and ("encrypt" in text or "swap" in text)): return "partition"
    if "keyboard model" in text or "type here to test your keyboard" in text: return "keyboard"
    if "region:" in text or ("region" in text and "zone" in text and "system language" in text) or "the system language will be set to" in text: return "location"
    if "welcome to the fab os" in text or "welcome to the" in text and "installer" in text: return "welcome"
    if "installing" in text and "fab os" in text and "next" not in text: return "install"
    return None

def fail_install(ser, a, q=None, wait_guest=True):
    time.sleep(2); ser.poll()
    tail = [l for l in ser.lines if l.startswith(("INSTALL_RESULT", "INSTALL_FAIL", "CALAMARES_JOB", "AUTOINSTALL"))][-30:]
    for l in tail: log("serial: " + l)
    # the guest still dumps its log and powers off on its own deadline (45 min after AUTOINSTALL_BEGIN); wait for that
    # unless QEMU is already gone. wait_guest=False: the driver itself could not operate the UI (nothing was installed, so
    # there is no session log worth 45 minutes) - collect the serial for a minute and return.
    end = time.time() + (2700 if wait_guest else 60)
    while time.time() < end and not ser.find(r"AUTOINSTALL_END"):
        ser.poll()
        if q is not None and not q.alive(): break
        time.sleep(5)
    save_guest_evidence(ser, a)
    log("STAGE_RESULT install failed"); return 1

def decode_log_block(header, body):
    """Decodes what live-autoinstall.sh's dump_log sent: header = the *_BEGIN line ("... encoding=gzip+base64 bytes=N
    kept=N gz=N sha256=HEX"), body = the lines between BEGIN and END. Returns (text, status, info) with status
    'ok' (sha256 of the received compressed bytes matches, gunzip succeeded), 'plain' (older helper: raw text lines),
    'empty', or an error description; the text is the best available content in every case."""
    import base64, gzip, hashlib
    kv = dict(p.split("=", 1) for p in header.split()[1:] if "=" in p)
    if kv.get("encoding") != "gzip+base64":
        return "\n".join(body) + ("\n" if body else ""), ("empty" if not body else "plain"), kv
    b64 = "".join(l.strip() for l in body if re.fullmatch(r"[A-Za-z0-9+/=]+", l.strip() or "x"))
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        return "\n".join(body) + "\n", "base64-error: %s" % e, kv
    digest = hashlib.sha256(raw).hexdigest()
    integrity = "ok" if kv.get("sha256") == digest else "sha256-mismatch (received %d bytes, expected gz=%s)" % (len(raw), kv.get("gz"))
    try:
        text = gzip.decompress(raw).decode("utf-8", "replace")
    except Exception as e:
        return "\n".join(body) + "\n", "gunzip-error: %s; %s" % (e, integrity), kv
    if kv.get("kept") and kv.get("bytes") and kv["kept"] != kv["bytes"] and integrity == "ok":
        integrity = "ok (newest %s of %s bytes; the full log is in the ESP under /fabos-install/)" % (kv["kept"], kv["bytes"])
    return text, integrity, kv

def save_guest_evidence(ser, a):
    ser.poll(); lines = ser.lines
    def block(begin, end):
        try:
            i = max(i for i, l in enumerate(lines) if l.startswith(begin)); j = next(k for k in range(i + 1, len(lines)) if lines[k].startswith(end))
            return lines[i], lines[i + 1:j]
        except (ValueError, StopIteration): return "", []
    head, body = block("CALAMARES_LOG_BEGIN", "CALAMARES_LOG_END")
    text, status, kv = decode_log_block(head, body) if head else ("", "missing (no CALAMARES_LOG_BEGIN/END block on the serial console)", {})
    with open(os.path.join(a.outdir, "session.log"), "w") as f: f.write(text)
    if head and body and not status.startswith(("ok", "plain")):
        with open(os.path.join(a.outdir, "session.log.raw-serial"), "w") as f: f.write(head + "\n" + "\n".join(body) + "\n")
    nlines = text.count("\n")
    ev = [l for l in lines if l.startswith(("LSBLK:", "ESP:", "LUKS ", "AUTOINSTALL", "INSTALL_RESULT", "CALAMARES_JOB:", "INSTALL_FAIL", "CALSTDERR|"))]
    ev.append("SESSION_LOG_DECODE=%s lines=%d bytes=%d serial_lines=%d" % (status.split(" ")[0], nlines, len(text.encode()), len(body)))
    _, tail = block("INSTALL_FAIL_TAIL_BEGIN", "INSTALL_FAIL_TAIL_END")
    with open(os.path.join(a.outdir, "guest-evidence.txt"), "w") as f: f.write("\n".join(ev + (["--- failure tail (last 40 session-log lines) ---"] + tail if tail else [])) + "\n")
    log("session log: %s -> %s (%d lines, %d bytes); guest-evidence.txt (%d lines)" % (status, os.path.join(a.outdir, "session.log"), nlines, len(text.encode()), len(ev)))
    for l in [x for x in lines if x.startswith(("LSBLK:", "ESP:", "LUKS ", "AUTOINSTALL_JOBS", "AUTOINSTALL_FINISHED_LINE", "AUTOINSTALL_LOG_COPY"))][:44]: log("evidence: " + l)

# ---------------------------------------------------------------- stage 2: boot the installed disk
def stage_boot(a):
    ser = Serial(a.serial); q = QMP(a.qmp); scr = Screen(q, a.outdir, a.image)
    log("QMP up; booting the installed disk alone (variant %s)" % a.variant)
    typed = []; deadline = time.time() + a.timeout; last_text = ""; unlocked_hint = False; n = 0
    while time.time() < deadline:
        for s in ser.poll():
            if "FABOS_INSTALLED_OK" in s:
                log("serial: FABOS_INSTALLED_OK (fabos-firstboot.service reached ExecStartPost on the installed system)")
                try: scr.grab("installed-desktop")
                except Exception: pass
                log("STAGE_RESULT boot ok (unlock typed %d time(s))" % len(typed)); shutdown(q); return 0
            if re.search(r"(Kernel panic|BUG:|grub rescue|error: )", s): log("serial: " + s)
        n += 1
        try:
            text, words, w, h, shot = scr.grab("boot-poll" if n % 8 else "boot")
        except Exception as e:
            log("screendump failed (%s)" % e); time.sleep(4); continue
        if text != last_text and text.strip(): log("screen: %s" % text[:160]); last_text = text
        if "grub rescue" in text or "minimal bash-like" in text or "grub>" in text:
            log("GRUB dropped to a shell"); log("STAGE_RESULT boot failed (grub shell)"); shutdown(q, hard=True); return 1
        if a.variant == "luks":
            # the Plymouth prompt ("Please unlock disk luks-...:"), but not the login screen's password box (it shows the user)
            login_seen = "fab tester" in text or "fabtest" in text
            prompt = ("unlock" in text or "passphrase" in text or "password" in text) and not login_seen
            elapsed = time.time() - (T0)
            blind_due = not typed and elapsed > a.blind_after
            # retry every 90 s (at most 4 attempts) while the prompt is still visible OR nothing recognisable is on screen
            # yet (OCR of the Plymouth prompt is not guaranteed: light text on a dark, animated background); once the
            # login screen shows the test user the volume is open and typing stops
            retry_due = typed and time.time() - typed[-1] > 90 and len(typed) < 4 and (prompt or not login_seen) and not unlocked_hint
            if (prompt and (not typed or retry_due)) or blind_due or retry_due:
                why = "prompt seen" if prompt else ("blind (no prompt recognised after %ds)" % a.blind_after if not typed else "retry, no login screen recognised yet")
                log("typing the LUKS passphrase (%s)" % why); type_text(q, PASSPHRASE, delay=0.11); q.send_keys(["ret"]); typed.append(time.time())
                time.sleep(4); continue
            if login_seen and typed and not unlocked_hint:
                unlocked_hint = True; log("login screen shows the test user: the encrypted volume is open (passphrase typed %d time(s))" % len(typed))
        time.sleep(4)
    log("no FABOS_INSTALLED_OK within %ds" % a.timeout)
    try: scr.grab("boot-timeout")
    except Exception: pass
    log("STAGE_RESULT boot failed (timeout)"); shutdown(q, hard=True); return 1

def shutdown(q, hard=False):
    try:
        if hard: q.cmd("quit"); return
        q.cmd("system_powerdown")
        for _ in range(90):
            time.sleep(1)
            if not q.alive(): return
        q.cmd("quit")
    except Exception:
        pass

# ---------------------------------------------------------------- main
def main():
    global PASSPHRASE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["install", "boot"])
    ap.add_argument("--qmp", required=True); ap.add_argument("--serial", required=True); ap.add_argument("--variant", choices=["luks", "plain"], required=True)
    ap.add_argument("--outdir", required=True); ap.add_argument("--image", default="localhost/fabos:iso")
    ap.add_argument("--timeout-live", type=int, default=420, help="s to wait for AUTOINSTALL_UI_READY")
    ap.add_argument("--timeout-ui", type=int, default=600, help="s for the page-driving phase")
    ap.add_argument("--timeout-install", type=int, default=2400, help="s for the job phase (unpackfs of 8.8 GB dominates)")
    ap.add_argument("--timeout", type=int, default=780, help="boot stage: s to wait for FABOS_INSTALLED_OK (firstboot waits 180 s for a network)")
    ap.add_argument("--blind-after", type=int, default=110, help="boot stage (luks): type the passphrase even if OCR saw no prompt after this many s")
    ap.add_argument("--passphrase", default=PASSPHRASE)
    a = ap.parse_args()
    PASSPHRASE = a.passphrase
    os.makedirs(a.outdir, exist_ok=True)
    return stage_install(a) if a.stage == "install" else stage_boot(a)

if __name__ == "__main__":
    sys.exit(main())
