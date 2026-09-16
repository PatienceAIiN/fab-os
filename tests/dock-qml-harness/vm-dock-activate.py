#!/usr/bin/env python3
"""Real-VM proof of the dock's click semantics and running indicators (round 7, track dock-activate).

Runs on the HOST against a booted Fab OS VM (scripts/boot-vm.sh --headless ... --qmp SOCK, ssh on 127.0.0.1:2222):
  1. installs the working-tree dock plasmoid into the VM and restarts plasmashell (what the OTA package does);
  2. opens Firefox, Fab Terminal (Konsole) and Fab Files (Dolphin) from their desktop files, arranges them so the
     dodging dock stays visible, and screenshots the dock through QEMU (QMP screendump) — indicators on all three;
  3. clicks the REAL dock's Firefox icon with QEMU's absolute pointer (usb-tablet): activate -> minimise -> restore,
     asserting through KWin's own window list and the probe's TasksModel rows that no second Firefox window appears;
  4. drives the tap decisions on the live TasksModel rows through tests/dock-qml-harness/probe-driver.py (the probe
     applet runs the dock's contents/code/dock-logic.js inside the session): activate / minimise / restore / launch /
     starting-ignored / middle = new instance -> group -> cycle, window counts checked at every step;
  5. moves Firefox to a second virtual desktop: with the default filters the marker stays; with "only the current
     desktop" switched on in the real dock's config the pinned icon still shows the marker (unfiltered model) and a tap
     brings the window back instead of launching (probe-driver.py elsewhere).
Evidence (PNG, logs) goes to --out. Prints PASS / FAIL lines and "VM DRIVER DONE failures=N"; exit 1 on any failure.
  vm-dock-activate.py --qmp /tmp/r7-dock.qmp --out build/r7-dock-activate --repo .
"""
import argparse, json, os, re, shlex, socket, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--qmp", required=True); ap.add_argument("--out", required=True); ap.add_argument("--repo", default=".")
ap.add_argument("--port", default="2222"); ap.add_argument("--no-install", action="store_true")
a = ap.parse_args()
OUT = os.path.abspath(a.out); os.makedirs(OUT, exist_ok=True); REPO = os.path.abspath(a.repo)
DOCK = os.path.join(REPO, "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock")
HARN = os.path.join(REPO, "tests/dock-qml-harness")
SSH = ["sshpass", "-p", "fabos", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8", "-p", a.port, "fabos@127.0.0.1"]
SCP = ["sshpass", "-p", "fabos", "scp", "-r", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-P", a.port]
# Everything that runs inside the session runs with plasmashell's EXACT environment (/tmp/sess, installed below): an ssh
# login lacks Plasma's XDG_MENU_PREFIX=plasma- (without it kbuildsycoca6 indexes no applications, so KService resolves no
# desktop file: launchers show raw file names, windows fall back to file:///usr/bin/<exe> and never merge with their pins),
# and the session bus / Wayland socket variables. Read afresh on every call (plasmashell restarts during the install).
SESS_HELPER = '''#!/usr/bin/env python3
import os, sys, subprocess
pid = subprocess.check_output(["pgrep", "-x", "plasmashell"]).split()[0].decode()
env = dict(l.split("=", 1) for l in open("/proc/%s/environ" % pid).read().split("\\0") if "=" in l)
for k, v in os.environ.items():
    if k.startswith("PROBE_"): env[k] = v
os.execvpe(sys.argv[1], sys.argv[1:], env)
'''
failures = 0; T0 = time.time()
LOG = open(os.path.join(OUT, "vm-driver.log"), "a")
def log(msg):
    line = "[%6.1fs] %s" % (time.time() - T0, msg); print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
def check(cond, msg):
    global failures
    log(("PASS " if cond else "FAIL ") + msg)
    if not cond: failures += 1
def vm(cmd, timeout=60):
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except subprocess.TimeoutExpired:
        log("  (ssh timeout after %ss: %s)" % (timeout, cmd[:80])); return ""
def vms(cmd, timeout=60): return vm("/tmp/sess sh -c " + shlex.quote(cmd), timeout)   # in the session's environment
def scp(src, dst): subprocess.run(SCP + (src if isinstance(src, list) else [src]) + ["fabos@127.0.0.1:" + dst], check=True, capture_output=True)

# ---------------------------------------------------------------- QMP (screendump + absolute pointer on the usb-tablet)
class QMP:
    def __init__(self, path, wait=120):
        deadline = time.time() + wait
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.s.settimeout(30); self.s.connect(path); self.buf = b""
                self._read(); self.cmd("qmp_capabilities"); return
            except (OSError, ValueError):
                if time.time() > deadline: raise SystemExit("QMP socket %s never came up" % path)
                time.sleep(1)
    def _read(self):
        while True:
            if b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if line.strip(): return json.loads(line)
                continue
            chunk = self.s.recv(65536)
            if not chunk: raise OSError("QMP closed")
            self.buf += chunk
    def cmd(self, name, **args):
        self.s.sendall((json.dumps({"execute": name, "arguments": args}) + "\n").encode())
        while True:
            o = self._read()
            if "return" in o: return o["return"]
            if "error" in o: raise RuntimeError("%s: %s" % (name, o["error"]))
    def screendump(self, path):
        self.cmd("screendump", filename=path)
    def move(self, x, y, w, h):
        self.cmd("input-send-event", events=[{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / max(1, w - 1))}},
                                             {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / max(1, h - 1))}}])
    def click(self, x, y, w, h, button="left"):
        self.move(x, y, w, h); time.sleep(0.45)   # let the dock magnify and settle under the pointer
        self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": True, "button": button}}]); time.sleep(0.08)
        self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": False, "button": button}}]); time.sleep(0.3)

from PIL import Image
SHOTS = 0
def shot(label, crop_dock=True):
    """QMP screendump -> PNG in OUT (full screen + the dock band). Returns (png path, width, height)."""
    global SHOTS
    SHOTS += 1; ppm = "/tmp/r7-dock-shot.ppm"
    q.screendump(ppm); time.sleep(0.2)
    im = Image.open(ppm).convert("RGB"); w, h = im.size
    png = os.path.join(OUT, "%02d-%s.png" % (SHOTS, label)); im.save(png)
    if crop_dock:
        band = im.crop((0, h - 140, w, h)); band.save(os.path.join(OUT, "%02d-%s-dock.png" % (SHOTS, label)))
    log("SHOT %s (%dx%d)" % (os.path.basename(png), w, h)); return png, w, h

# ---------------------------------------------------------------- KWin: window list / arrange / minimise / desktops (scripts + bus monitor)
KWIN_GEOM = ('function setGeom(w, x, y, wd, h) { try { w.frameGeometry = { x: x, y: y, width: wd, height: h }; } catch (e) {} var g = w.frameGeometry;'
             ' if (Math.abs(g.width - wd) > 4) { try { w.frameGeometry = Qt.rect(x, y, wd, h); } catch (e2) { rep("geom-fail " + e2); } } }\n')
KWIN_HEAD = ('function rep(m) { var s = "%(tag)s " + m; try { console.warn("fabos-docktest " + s); } catch (e) {} callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus", "in.patienceai.fabos.docktest", "event", s); }\n'
             + KWIN_GEOM + 'function wl(w) { var g = w.frameGeometry || { x: 0, y: 0, width: 0, height: 0 }; var ds = "all"; try { if (w.desktops && w.desktops.length) ds = w.desktops.map(function (d) { return d.x11DesktopNumber }).join("+"); } catch (e) { ds = "?"; }'
             ' return String(w.resourceClass) + "|" + (w.dock ? "dock" : (w.normalWindow ? "normal" : "other")) + "|" + (w.minimized ? "min" : "vis") + "|" + (w.active ? "act" : "-") + "|" + Math.round(g.x) + "," + Math.round(g.y) + "," + Math.round(g.width) + "," + Math.round(g.height) + "|" + ds + "|" + String(w.caption).replace(/[\\s|"\\\\]+/g, "_").slice(0, 40); }\n'
             'function listAll(pred) { var ws = workspace.windowList(); for (var i = 0; i < ws.length; i++) { try { if (!pred || pred(ws[i])) rep("win " + wl(ws[i])); } catch (e) { rep("win-err " + e); } } return ws.length; }\n')
SEQ = 0
def kwin(body, wait=1.5):
    """Load a one-shot KWin script whose rep() lines land in the bus monitor; return the lines of THIS run."""
    global SEQ
    SEQ += 1; tag = "r%d" % SEQ; name = "fabos-docktest-%d" % SEQ
    src = (KWIN_HEAD + body).replace("%(tag)s", tag)
    with open("/tmp/r7-dock-kwin.js", "w") as f: f.write(src)
    scp("/tmp/r7-dock-kwin.js", "/tmp/dock-kwin-%d.js" % SEQ)
    vms("qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript /tmp/dock-kwin-%d.js %s >/dev/null && qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start" % (SEQ, name))
    time.sleep(wait)
    vms("qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript %s >/dev/null 2>&1" % name)
    raw = vm("cat /tmp/dock-monitor.jsonl 2>/dev/null")
    lines = [m.group(1) for m in re.finditer(r'"data"\s*:\s*\[\s*"' + tag + r' ((?:[^"\\]|\\.)*)"', raw)]
    if not lines:   # fallback: the same reports through console.warn -> KWin's stderr -> the user journal
        raw = vm("journalctl --user -b --no-pager -o cat _COMM=kwin_wayland 2>/dev/null | grep -F 'fabos-docktest %s ' | tail -400" % tag)
        lines = [m.group(1) for m in re.finditer(r"fabos-docktest " + tag + r" (.*)", raw)]
        if lines: log("  (KWin reports read from the journal)")
    return lines
def windows():
    """KWin's normal windows: list of dicts (class, min, act, geom, desktops, caption)."""
    out = []
    for l in kwin('var n = listAll(null); rep("done windows=" + n);'):
        if not l.startswith("win "): continue
        p = l[4:].split("|")
        if len(p) < 7 or p[1] != "normal": continue
        out.append({"class": p[0], "min": p[2] == "min", "act": p[3] == "act", "geom": [int(v) for v in p[4].split(",")], "desktops": p[5], "caption": p[6]})
    return out
def count(cls, ws=None):
    ws = windows() if ws is None else ws
    return sum(1 for w in ws if w["class"] == cls)
def wait_for(pred, timeout, step=1.0):
    end = time.time() + timeout; last = None
    while time.time() < end:
        last = pred()
        if last: return last
        time.sleep(step)
    return None

# ---------------------------------------------------------------- probe (the dock's model + logic inside the session)
PROBE_LOG = "/tmp/dock-probe.log"; PROBE_CMD = "/tmp/dock-probe-cmd"
def probe_start(filter_desktop=False, filter_activity=False, filter_hidden=False):
    vm("pkill -x plasmawindowed; rm -f %s %s; sleep 1" % (PROBE_LOG, PROBE_CMD))
    vms("PROBE_CMD=%s PROBE_FILTER_DESKTOP=%d PROBE_FILTER_ACTIVITY=%d PROBE_FILTER_HIDDEN=%d setsid -f plasmawindowed in.patienceai.fabos.dockprobe > %s 2>&1" % (PROBE_CMD, 1 if filter_desktop else 0, 1 if filter_activity else 0, 1 if filter_hidden else 0, PROBE_LOG))
    r = wait_for(lambda: "READY" in vm("grep -c READY %s 2>/dev/null; grep -m1 READY %s 2>/dev/null" % (PROBE_LOG, PROBE_LOG)), 60)
    check(r is not None, "probe applet (dock TasksModel + dock-logic.js) is up in the session%s%s%s" % (" with filterByVirtualDesktop" if filter_desktop else "", " with filterByActivity" if filter_activity else "", " with v3's filterHidden" if filter_hidden else ""))
    if r is None: log("  probe log tail: " + vm("tail -n 8 %s 2>/dev/null" % PROBE_LOG).strip().replace("\n", " | ")[:900])   # a QML error (e.g. a non-existent property) shows here
    time.sleep(2)
def rows():
    raw = vm("grep 'ROWS ' %s | tail -1" % PROBE_LOG)
    i = raw.find("ROWS ")
    if i < 0: return None
    try: return json.loads(raw[i + 5:])
    except ValueError: return None
def row(snap, base):
    if not snap: return None
    for r in snap["rows"]:
        for k in ("LauncherUrlWithoutIcon", "AppId"):
            s = str(r.get(k) or "").split("?")[0]; s = s[s.rfind("/") + 1:]; s = s[s.rfind(":") + 1:].lower()
            if base in s: return r
    return None
def row_index(snap, base):
    for i, r in enumerate(snap["rows"]):
        if row({"rows": [r]}, base): return i
    return -1
def driver(mode, *args):
    """probe-driver.py inside the VM (it reads the probe log and writes the command file there)."""
    out = vm("python3 /tmp/probe-driver.py %s %s %s %s" % (PROBE_LOG, PROBE_CMD, mode, " ".join(args)), timeout=400)
    with open(os.path.join(OUT, "driver-%s.log" % mode), "a") as f: f.write(out)
    for l in out.splitlines():
        if l.startswith("PASS ") or l.startswith("FAIL ") or l.startswith("INFO ") or l.startswith("ROWS ") or l.startswith("  ["): log("  driver: " + l)
    global failures
    m = re.search(r"DRIVER DONE failures=(\d+)", out)
    n = int(m.group(1)) if m else 99
    check(n == 0, "probe-driver %s %s: %d failure(s)" % (mode, " ".join(args), n))
    return out
def table(snap):
    if not snap: log("  (no ROWS)"); return
    log("  ROWS count=%d windows=%d allWindows=%d desktop=%s" % (snap["count"], snap["windows"], snap["allWindows"], snap.get("desktop")))
    for i, r in enumerate(snap["rows"]):
        kind = "group" if r["IsGroupParent"] else ("window" if r["IsWindow"] else ("startup" if r["IsStartup"] else "launcher"))
        log("   [%d] %-32s %-18s %-8s active=%-5s min=%-5s ch=%d state=%-9s dots=%d tap=%s%s" % (i, r["LauncherUrlWithoutIcon"][:32], r["AppId"][:18], kind, r["IsActive"], r["IsMinimized"], r["ChildCount"], r["state"], r["dots"], r["tap"], (" elsewhere=" + json.dumps(r["elsewhere"])) if r.get("elsewhere") else ""))

# ---------------------------------------------------------------- dock geometry from a screenshot (for the real pointer)
def dock_items(png, panel):
    """Icon columns inside the dock panel rect (x, y, w, h): runs of columns whose luminance varies vertically."""
    im = Image.open(png).convert("L"); x0, y0, pw, ph = panel
    inner_y0, inner_y1 = y0 + 6, y0 + ph - 10   # skip the panel's top edge and the indicator band, icons are mid-panel
    cols = []
    for x in range(x0 + 4, x0 + pw - 4):
        vals = [im.getpixel((x, y)) for y in range(inner_y0, inner_y1)]
        mean = sum(vals) / len(vals); var = sum((v - mean) ** 2 for v in vals) / len(vals)
        cols.append(var ** 0.5)
    thr = max(6.0, sorted(cols)[len(cols) // 2] * 1.5)
    runs = []; start = None
    for i, v in enumerate(cols):
        on = v > thr
        if on and start is None: start = i
        if not on and start is not None:
            runs.append([x0 + 4 + start, x0 + 4 + i]); start = None
    if start is not None: runs.append([x0 + 4 + start, x0 + 4 + len(cols)])
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= 5: merged[-1][1] = r[1]
        else: merged.append(r)
    return [r for r in merged if r[1] - r[0] >= 18]
def panel_rect():
    for l in kwin('listAll(function (w) { return w.dock === true; }); rep("done");'):
        p = l[4:].split("|")
        if len(p) >= 5 and p[0] == "plasmashell" and p[1] == "dock":
            g = [int(v) for v in p[4].split(",")]
            if g[1] > 300 and g[3] < 200: return g   # the bottom one (the top bar is at y 0)
    return None

# ================================================================ run
log("== waiting for ssh + the Plasma session")
check(wait_for(lambda: "ok" in vm("echo ok", timeout=20) if True else None, 600, 5) is not None, "ssh to the VM")
check(wait_for(lambda: "1" in vm("pgrep -x plasmashell >/dev/null && pgrep -x kwin_wayland >/dev/null && echo 1"), 300, 3) is not None, "plasmashell + kwin_wayland running")
time.sleep(5)
log(vm("cat /etc/os-release | grep PRETTY; dpkg-query -W -f '${Version}\\n' fabos-desktop; head -1 /proc/meminfo").strip().replace("\n", " | "))
with open("/tmp/r7-sess.py", "w") as f: f.write(SESS_HELPER)
scp("/tmp/r7-sess.py", "/tmp/sess"); vm("chmod +x /tmp/sess")
env_probe = vms("echo XDG_MENU_PREFIX=$XDG_MENU_PREFIX WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_DATA_DIRS=$XDG_DATA_DIRS LANG=$LANG")
check("XDG_MENU_PREFIX=plasma-" in env_probe and "WAYLAND_DISPLAY=wayland" in env_probe, "session environment helper (/tmp/sess) reads plasmashell's env: " + env_probe.strip()[:200])
q = QMP(a.qmp)

if not a.no_install:
    log("== installing the working-tree dock plasmoid + the probe applet; restarting plasmashell")
    vm("rm -rf /tmp/in.patienceai.fabos.dock /tmp/dockprobe; mkdir -p /tmp/dockprobe/contents/ui /tmp/dockprobe/contents/code")
    scp(DOCK, "/tmp/")
    scp([os.path.join(HARN, "ModelProbe.qml")], "/tmp/dockprobe/contents/ui/main.qml")
    scp([os.path.join(DOCK, "contents/code/dock-logic.js")], "/tmp/dockprobe/contents/code/")
    scp([os.path.join(HARN, "probe-driver.py")], "/tmp/probe-driver.py")
    vm("printf '{\"KPackageStructure\":\"Plasma/Applet\",\"KPlugin\":{\"Id\":\"in.patienceai.fabos.dockprobe\",\"Name\":\"Dock model probe\",\"Version\":\"0\"},\"X-Plasma-API-Minimum-Version\":\"6.0\"}\\n' > /tmp/dockprobe/metadata.json; "
       "mkdir -p ~/.local/share/plasma/plasmoids && rm -rf ~/.local/share/plasma/plasmoids/in.patienceai.fabos.dockprobe && cp -r /tmp/dockprobe ~/.local/share/plasma/plasmoids/in.patienceai.fabos.dockprobe")
    vm("sed -i 's/@DISTRO_NAME@/Fab OS/g; s/@VENDOR_NAME@/Patience AI/g; s/@DISTRO_VERSION@/1.0/g; s#@HOME_URL@#https://fabos.patienceai.in#g' /tmp/in.patienceai.fabos.dock/metadata.json")
    inst = vm("echo fabos | sudo -S sh -c 'rm -rf /usr/share/plasma/plasmoids/in.patienceai.fabos.dock && cp -r /tmp/in.patienceai.fabos.dock /usr/share/plasma/plasmoids/in.patienceai.fabos.dock && chown -R root:root /usr/share/plasma/plasmoids/in.patienceai.fabos.dock && echo installed' 2>/dev/null")
    check("installed" in inst, "dock plasmoid installed under /usr/share/plasma/plasmoids (the package path)")
    check("dock-logic.js" in vm("ls /usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/code/"), "contents/code/dock-logic.js present in the installed plasmoid")
    vms("kbuildsycoca6 --noincremental >/dev/null 2>&1; systemctl --user restart plasma-plasmashell; sleep 6")
    check(wait_for(lambda: "1" in vm("pgrep -x plasmashell >/dev/null && echo 1"), 60, 2) is not None, "plasmashell restarted with the new dock")
    time.sleep(6)

log("== KWin window probe (bus monitor)")
# The monitor command lives in a script file: a pkill pattern in the same ssh command line as the literal command text
# matches (and ends) that very shell before setsid runs — that is what emptied the first VM run's KWin reports.
vm("pkill -f 'busctl --user [m]onitor' 2>/dev/null; rm -f /tmp/dock-monitor.jsonl; printf '%s\\n' '#!/bin/sh' 'exec busctl --user monitor --json=short --match \"interface=in.patienceai.fabos.docktest\" > /tmp/dock-monitor.jsonl 2>&1' > /tmp/dock-monitor.sh; chmod +x /tmp/dock-monitor.sh")
vms("setsid -f /tmp/dock-monitor.sh")
time.sleep(1.5)
log("  INFO bus monitor for the KWin script reports: %s" % ("running" if "busctl" in vm("pgrep -fa 'busctl --user [m]onitor'") else "not running (" + vm("head -c 300 /tmp/dock-monitor.jsonl 2>/dev/null").strip().replace("\n", " / ") + ") — the journal path (console.warn) is used"))
ws0 = windows(); log("  windows before: " + json.dumps([w["class"] for w in ws0]))
check(len(kwin('rep("done windows=" + workspace.windowList().length);')) >= 1, "KWin scripting reports reach the driver (bus monitor or KWin's journal)")
vm("rm -f /tmp/dock-appletsrc-before; grep -n 'showOnlyCurrent\\|launchers=' ~/.config/plasma-org.kde.plasma.desktop-appletsrc > /tmp/dock-appletsrc-before 2>/dev/null; true")
log("  dock config lines: " + vm("cat /tmp/dock-appletsrc-before").strip().replace("\n", " ; "))

log("== opening Firefox, Fab Terminal (Konsole) and Fab Files (Dolphin) from their desktop files")
vm("pkill -f /usr/lib/firefox/firefox; pkill -x konsole; pkill -x dolphin; pkill -x kate; sleep 1; true")
for d in ("firefox", "org.kde.konsole", "org.kde.dolphin"):
    vms("setsid -f kioclient exec /usr/share/applications/%s.desktop >/dev/null 2>&1 || setsid -f gio launch /usr/share/applications/%s.desktop >/dev/null 2>&1" % (d, d)); time.sleep(2)
ws = wait_for(lambda: (lambda w: w if all(count(c, w) >= 1 for c in ("firefox", "org.kde.konsole", "org.kde.dolphin")) else None)(windows()), 150, 3)
check(ws is not None, "KWin has one window each of firefox / org.kde.konsole / org.kde.dolphin: " + json.dumps([(w["class"], w["geom"]) for w in (ws or windows())]))
time.sleep(4)
# arrange: small windows in the upper half so the dodging dock stays visible (the owner's laptop dock dodges windows too)
kwin('var ws = workspace.windowList(), k = 0; for (var i = 0; i < ws.length; i++) { var w = ws[i]; if (!w.normalWindow) continue; setGeom(w, 40 + k * 60, 60 + k * 40, 620, 380); k++; } rep("done arranged=" + k);', 1.0)
time.sleep(2.5)
png, W, H = shot("three-open-firefox-konsole-dolphin")
snapW = windows(); log("  windows now: " + json.dumps([(w["class"], w["min"], w["act"], w["geom"]) for w in snapW]))
N_FF, N_K, N_D = count("firefox", snapW), count("org.kde.konsole", snapW), count("org.kde.dolphin", snapW)

log("== probe: the dock's TasksModel rows as dock-logic.js reads them (launcher <-> window merge)")
probe_start(False)
snap = wait_for(lambda: (lambda s: s if s and s["count"] > 0 else None)(rows()), 30)
table(snap)
driver("matching", "firefox", "konsole", "dolphin")
s = rows()
for b in ("firefox", "konsole", "dolphin"):
    r = row(s, b)
    check(r is not None and (r["IsWindow"] or r["IsGroupParent"]) and r["HasLauncher"] and r["state"] in ("active", "running", "minimized"), "%s: pinned row is its window (IsWindow=%s HasLauncher=%s state=%s tap=%s)" % (b, r and r["IsWindow"], r and r["HasLauncher"], r and r["state"], r and r["tap"]))
check(all(row(s, b) is not None and not row(s, b)["IsLauncher"] for b in ("firefox", "konsole", "dolphin")), "no bare launcher left for an open app (the marker cannot vanish while the window exists)")
check(row(s, "kate") is not None and row(s, "kate")["IsLauncher"] and row(s, "kate")["state"] == "none" and row(s, "kate")["tap"] == "launch", "Fab Editor (not running) is a plain launcher: no marker, a tap would launch")
for b in ("firefox", "konsole", "dolphin"):
    r = row(s, b)
    if r is None: log("  INFO %s: no row (probe down?)" % b); continue
    log("  INFO %s window: Activities=%s VirtualDesktops=%s onAll=%s (session activity %s, desktop %s)" % (b, json.dumps(r.get("Activities")), json.dumps(r.get("VirtualDesktops")), r.get("IsOnAllVirtualDesktops"), s.get("activity"), s.get("desktop")))
n_open = s["windows"] if s else 0

log("== LibreOffice Writer: does its window resolve to its own desktop file (so a pinned Writer would merge)?")
vms("setsid -f kioclient exec /usr/share/applications/libreoffice-writer.desktop >/dev/null 2>&1")
lo_row = lambda x: row(x, "libreoffice") or row(x, "soffice")
lo = wait_for(lambda: (lambda x: x if x and lo_row(x) and (lo_row(x)["IsWindow"] or lo_row(x)["IsGroupParent"]) else None)(rows()), 120, 2)
if lo is None: log("  INFO no LibreOffice window row within 120 s in this 2 GB VM — Writer matching not verified here")
else:
    r = lo_row(lo); log("  INFO LibreOffice row: url=%s appid=%s display=%s state=%s tap=%s" % (r["LauncherUrlWithoutIcon"], r["AppId"], r["display"][:40], r["state"], r["tap"]))
    check(r["LauncherUrlWithoutIcon"].startswith("applications:libreoffice"), "LibreOffice window resolves to applications:libreoffice-*.desktop (%s): a pinned Writer / Start Center merges with it" % r["LauncherUrlWithoutIcon"])
vm("pkill -f soffice.bin; pkill -x soffice; sleep 2; true")
n_open = (wait_for(lambda: (lambda x: x if x and x["windows"] == n_open else None)(rows()), 20) or rows() or {"windows": n_open})["windows"]

log("== hypothesis check: the shipped default 'only the current activity' (v3) — does the activity filter drop these windows here?")
probe_start(False, True)
sA = wait_for(lambda: (lambda x: x if x and x["count"] > 0 else None)(rows()), 30); table(sA)
dropped = [b for b in ("firefox", "konsole", "dolphin") if row(sA, b) is not None and row(sA, b)["IsLauncher"]]
log("  INFO activity filter on: windows %d -> %d; pins turned back into launchers: %s%s" % (n_open, sA["windows"] if sA else -1, dropped or "none",
    " — with the v3 code these would show no marker and a click would launch a second copy" if dropped else " (activities agree in this VM; the v4 code keeps the marker either way)"))
if dropped:
    for b in dropped:
        r = row(sA, b)
        check(r.get("elsewhere") and r["state"] in ("running", "minimized") and r["tap"] == "elsewhere", "%s under the activity filter: v4 keeps the marker and taps bring the window back (state %s tap %s)" % (b, r["state"], r["tap"]))
probe_start(False, False)
s = wait_for(lambda: (lambda x: x if x and x["count"] > 0 else None)(rows()), 30)

log("== REAL pointer on the REAL dock (QEMU usb-tablet through QMP): Firefox icon -> activate, minimise, restore")
panel = panel_rect(); log("  dock panel (KWin): %s" % panel)
check(panel is not None, "found the dock panel window through KWin")
if panel:
    items = dock_items(png, panel); log("  icon columns: %s" % items)
    ffi = row_index(s, "firefox"); want = 1 + ffi   # start button first, then the task rows
    n_items = s["count"] + 2
    log("  INFO screenshot: %d icon columns detected for start + %d tasks + peek = %d items (faint tiles may merge or drop; positions come from the layout arithmetic below)" % (len(items), s["count"], n_items))
    check(ffi >= 0, "Firefox is a row of the live model for the real-pointer test (row %d)" % ffi)
    # Item centres from the dock's own layout: ONE evenly spaced row centred in the applet (main.qml: the reserve is split
    # equally to both ends). Pitch = icon + 12 px gap, measured here as the median distance between detected columns
    # (robust to one faint icon); the row's centre is the panel's + 2.5 px (the 1 px launcher anchor and its 4 px spacing).
    # A detected column that contains the predicted centre wins; otherwise the prediction itself is used.
    centres = [(r[0] + r[1]) / 2.0 for r in items]
    diffs = sorted(b - a for a, b in zip(centres, centres[1:])); pitch = diffs[len(diffs) // 2] if diffs else 47.0
    mid = panel[0] + panel[2] / 2.0 + 2.5
    def item_x(i):
        px = mid + (i - (n_items - 1) / 2.0) * pitch
        for r in items:
            if r[0] <= px <= r[1]: return (r[0] + r[1]) // 2
        return int(round(px))
    log("  pitch %.1f px, row centre %.1f, predicted item centres: %s" % (pitch, mid, [int(round(mid + (i - (n_items - 1) / 2.0) * pitch)) for i in range(n_items)]))
    if ffi >= 0:
        cx = item_x(want); cy = panel[1] + panel[3] // 2 - 2
        log("  Firefox is task row %d -> dock item %d of %d; clicking (%d,%d)" % (ffi, want, n_items, cx, cy))
        # 1. running, not active -> activate (Dolphin was activated last by the arrange order; make sure Firefox is not active)
        if row(s, "firefox")["IsActive"]:
            kwin('var ws = workspace.windowList(); for (var i = 0; i < ws.length; i++) if (ws[i].resourceClass == "org.kde.dolphin") workspace.activeWindow = ws[i]; rep("done");', 1.0); time.sleep(1.5)
        before = windows()
        q.click(cx, cy, W, H)
        got = wait_for(lambda: (lambda r: r if r and r["IsActive"] and not r["IsMinimized"] else None)(row(rows(), "firefox")), 10)
        after = windows()
        check(got is not None, "click 1 on the Firefox icon: Firefox is the active window (state %s)" % (got and got["state"]))
        check(count("firefox", after) == count("firefox", before) == N_FF, "click 1 opened no new Firefox window (KWin: %d before, %d after)" % (count("firefox", before), count("firefox", after)))
        time.sleep(1.0); shot("after-click1-firefox-active")
        # 2. active -> minimise
        q.click(cx, cy, W, H)
        got = wait_for(lambda: (lambda r: r if r and r["IsMinimized"] else None)(row(rows(), "firefox")), 10)
        after2 = windows()
        check(got is not None and got["state"] == "minimized" and got["dots"] >= 1, "click 2: Firefox minimised, marker kept as a dimmed dot (state %s dots %s)" % (got and got["state"], got and got["dots"]))
        check(any(w["class"] == "firefox" and w["min"] for w in after2) and count("firefox", after2) == N_FF, "KWin: the Firefox window is minimised, still %d window(s)" % count("firefox", after2))
        time.sleep(1.0); shot("after-click2-firefox-minimised")
        # 3. minimised -> restore
        q.click(cx, cy, W, H)
        got = wait_for(lambda: (lambda r: r if r and r["IsActive"] and not r["IsMinimized"] else None)(row(rows(), "firefox")), 10)
        after3 = windows()
        check(got is not None, "click 3: Firefox restored and active again (state %s)" % (got and got["state"]))
        check(count("firefox", after3) == N_FF, "three real clicks, still exactly %d Firefox window(s) (KWin)" % N_FF)
        time.sleep(1.0); shot("after-click3-firefox-restored")
        q.move(W // 2, H // 3, W, H); time.sleep(0.8)   # pointer away from the dock (no magnification in the next shots)

log("== Konsole minimised through KWin: its dot dims, the marker stays")
kwin('var ws = workspace.windowList(); for (var i = 0; i < ws.length; i++) if (ws[i].resourceClass == "org.kde.konsole") ws[i].minimized = true; rep("done");', 1.0)
got = wait_for(lambda: (lambda r: r if r and r["IsMinimized"] else None)(row(rows(), "konsole")), 10)
check(got is not None and got["state"] == "minimized" and got["dots"] == 1 and got["tap"] == "activate", "Konsole row: minimised, one dimmed dot, a tap would restore (state %s tap %s)" % (got and got["state"], got and got["tap"]))
check(got is not None and got.get("IsHidden") is True and got.get("SkipTaskbar") is not True, "libtaskmanager reports the minimised Konsole window as IsHidden (not SkipTaskbar) — the role v3's filterHidden: true dropped from the model")
time.sleep(1.2); shot("konsole-minimised-dimmed-dot")
log("== v3 replay: the same rows through a model with filterHidden: true (what 1.0-6 ships) — the minimised Konsole must vanish and its pin turn into a bare launcher")
probe_start(False, False, True)
sv3 = wait_for(lambda: (lambda x: x if x and x["count"] > 0 else None)(rows()), 30); table(sv3)
rv3 = row(sv3, "konsole")
check(rv3 is not None and rv3["IsLauncher"] and not rv3["IsWindow"] and rv3["state"] == "none" and rv3["tap"] == "launch" and sv3["windows"] == n_open - 1,
      "v3 reproduced: with filterHidden the minimised Konsole is gone from the model (%s windows), its pin is a bare launcher with no marker and a tap would LAUNCH a second copy (state %s tap %s)" % (sv3 and sv3["windows"], rv3 and rv3["state"], rv3 and rv3["tap"]))
probe_start(False, False)
sv4 = wait_for(lambda: (lambda x: x if x and (row(x, "konsole") or {}).get("IsMinimized") else None)(rows()), 20)
check(sv4 is not None and row(sv4, "konsole")["state"] == "minimized" and row(sv4, "konsole")["tap"] == "activate", "v4 model again: the minimised Konsole is back as a window row, dimmed dot, tap restores")

log("== the view from a second virtual desktop (every window on desktop 1, Konsole still minimised): all three markers stay")
s_before = rows()
kwin('if (workspace.desktops.length < 2) workspace.createDesktop(1, "Desktop 2"); workspace.currentDesktop = workspace.desktops[1]; rep("done desktops=" + workspace.desktops.length + " current=" + workspace.currentDesktop.x11DesktopNumber);', 1.0)
time.sleep(3.0)
s2 = rows(); table(s2)
kinds = dict((b, (row(s2, b) or {}).get("state")) for b in ("firefox", "konsole", "dolphin"))
check(all(row(s2, b) is not None and (row(s2, b)["IsWindow"] or row(s2, b)["IsGroupParent"]) for b in kinds) and kinds["firefox"] == "running" and kinds["konsole"] == "minimized" and kinds["dolphin"] == "running",
      "seen from desktop 2 with the default filters: Firefox / Konsole / Dolphin are still window rows with their markers, Konsole's dimmed (%s)" % json.dumps(kinds))
check(s2 is not None and s_before is not None and str(s2.get("desktop")) != str(s_before.get("desktop")), "the probe's VirtualDesktopInfo saw the switch (%s -> %s)" % (s_before and s_before.get("desktop"), s2 and s2.get("desktop")))
shot("desktop2-view-three-markers-konsole-dimmed")
kwin('workspace.currentDesktop = workspace.desktops[0]; rep("done");', 0.8); time.sleep(1.5)

log("== tap transitions on the live rows through the dock's own code path (probe-driver taps: dolphin, konsole, kate)")
kwin('var ws = workspace.windowList(); for (var i = 0; i < ws.length; i++) if (ws[i].resourceClass == "org.kde.konsole") ws[i].minimized = false; rep("done");', 1.0); time.sleep(1.5)
driver("taps", "dolphin", "konsole", "kate")
s = rows(); table(s)
wsT = windows()
check(count("org.kde.kate", wsT) == 1, "KWin: exactly one Fab Editor window after the launcher tap (%d)" % count("org.kde.kate", wsT))
check(count("org.kde.konsole", wsT) == 2, "KWin: middle click made a second Konsole window (%d) and nothing else did" % count("org.kde.konsole", wsT))
check(count("firefox", wsT) == N_FF and count("org.kde.dolphin", wsT) == N_D, "KWin: Firefox %d, Dolphin %d unchanged by all the taps" % (count("firefox", wsT), count("org.kde.dolphin", wsT)))
kwin('var ws = workspace.windowList(), k = 0; for (var i = 0; i < ws.length; i++) { var w = ws[i]; if (!w.normalWindow) continue; setGeom(w, 40 + k * 50, 60 + k * 30, 620, 380); k++; } rep("done arranged=" + k);', 1.0)
time.sleep(2.0); shot("after-taps-konsole-group-kate-open")

log("== a second virtual desktop: Firefox moves there; the marker must stay")
kwin('if (workspace.desktops.length < 2) workspace.createDesktop(1, "Desktop 2"); rep("done desktops=" + workspace.desktops.length);', 1.0)
kwin('var ws = workspace.windowList(); for (var i = 0; i < ws.length; i++) if (ws[i].resourceClass == "firefox") ws[i].desktops = [workspace.desktops[1]]; workspace.currentDesktop = workspace.desktops[0]; rep("done");', 1.0)
time.sleep(2.5)
s = rows(); r = row(s, "firefox")
check(r is not None and (r["IsWindow"] or r["IsGroupParent"]) and r["state"] in ("running", "minimized"), "default filters (desktop / activity off): Firefox on desktop 2 is still a window row with its marker (state %s)" % (r and r["state"]))
shot("firefox-on-desktop2-default-filters-marker-kept")
log("== 'only the current desktop' ON in the real dock's config + the probe: the pinned icon keeps the marker; a tap brings the window back")
vms("qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript 'var ps = panels(); for (var i = 0; i < ps.length; i++) { var ws = ps[i].widgets(\"in.patienceai.fabos.dock\"); for (var j = 0; j < ws.length; j++) { ws[j].currentConfigGroup = [\"General\"]; ws[j].writeConfig(\"showOnlyCurrentDesktop\", true) } }'")
probe_start(True)
s = wait_for(lambda: (lambda x: x if x and x["count"] > 0 else None)(rows()), 30); table(s)
r = row(s, "firefox")
check(r is not None and r["IsLauncher"] and not r["IsWindow"] and r.get("elsewhere") and r["elsewhere"]["windows"] >= 1, "filtered model: Firefox is a bare launcher here, the unfiltered model finds its window on desktop 2 (%s)" % json.dumps(r and r.get("elsewhere")))
check(r is not None and r["state"] in ("running", "minimized") and r["dots"] >= 1 and r["tap"] == "elsewhere", "filtered: marker kept (state %s), a tap would bring the window back (tap=%s), never launch" % (r and r["state"], r and r["tap"]))
time.sleep(2.0); shot("filter-on-firefox-elsewhere-marker-kept")
driver("elsewhere", "firefox")
wsE = windows()
check(count("firefox", wsE) == N_FF, "KWin: still %d Firefox window(s) after the elsewhere tap" % count("firefox", wsE))
time.sleep(1.5); shot("after-elsewhere-tap-on-desktop2")
vms("qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript 'var ps = panels(); for (var i = 0; i < ps.length; i++) { var ws = ps[i].widgets(\"in.patienceai.fabos.dock\"); for (var j = 0; j < ws.length; j++) { ws[j].currentConfigGroup = [\"General\"]; ws[j].writeConfig(\"showOnlyCurrentDesktop\", false) } }'")
kwin('workspace.currentDesktop = workspace.desktops[0]; rep("done");', 0.5)

log("== plasmashell health + evidence")
check("1" in vm("pgrep -x plasmashell >/dev/null && echo 1"), "plasmashell still running at the end")
diag = vm("journalctl --user -b --no-pager -o cat _COMM=plasmashell 2>/dev/null | grep -E 'in.patienceai.fabos.dock.*(qml|js):[0-9]+' | tail -5")
check(diag.strip() == "", "no QML / JS diagnostics from the dock in plasmashell's journal" + (": " + diag.strip()[:300] if diag.strip() else ""))
vm("cat %s" % PROBE_LOG) and open(os.path.join(OUT, "probe.log"), "w").write(vm("cat %s" % PROBE_LOG, timeout=120))
open(os.path.join(OUT, "kwin-monitor.jsonl"), "w").write(vm("cat /tmp/dock-monitor.jsonl", timeout=120))
open(os.path.join(OUT, "plasmashell-journal.log"), "w").write(vm("journalctl --user -b --no-pager -o short-iso _COMM=plasmashell 2>/dev/null | tail -200", timeout=120))
vm("echo quit > %s; sleep 1; pkill -x plasmawindowed; pkill -f 'busctl --user [m]onitor'; true" % PROBE_CMD)
log("VM DRIVER DONE failures=%d" % failures)
sys.exit(1 if failures else 0)
