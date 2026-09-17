#!/usr/bin/env python3
"""Real-VM proof of the Research · Computer use switches (round 8, track modes-toggles; docs/design/MODES.md).

Runs on the HOST against a booted Fab OS VM (scripts/boot-vm.sh --headless ... --qmp SOCK, ssh on 127.0.0.1:2222):
  1. installs the working tree's ask bar plasmoid (contents/ui + contents/code/modes.js), fabos_agentd.py and
     command_center.py into the VM (what the 1.0-8 package does) and restarts fabos-agent + plasmashell;
  2. focuses the home bar's field with QEMU's absolute pointer (usb-tablet through QMP) so the strip unfolds, photographs
     the bar (spectacle -b -n -f -o inside the session + a QMP screendump), finds the two accent pills (40x22) in the
     screendump, CLICKS the Research one for real, reads `fabos settings agent.research` -> false, photographs the bar
     with one switch off (and its "Research off for new chats" line), clicks again -> true;
  3. opens Fab AI Controls, photographs the window with the same strip (header + composer), clicks the composer's
     Research switch for real -> `fabos settings agent.research` false -> photograph -> click again -> true;
  4. health: plasmashell alive, no QML/JS diagnostics from the ask bar in the journal, /status carries `capabilities`.
Evidence (PNG, logs) goes to --out. Prints PASS / FAIL lines and "VM MODES DONE failures=N"; exit 1 on any failure.
  modes-vm.py --qmp /tmp/r8-modes.qmp --out build/r8-modes --repo .
"""
import argparse, json, os, re, shlex, socket, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--qmp", required=True); ap.add_argument("--out", required=True); ap.add_argument("--repo", default=".")
ap.add_argument("--port", default="2222"); ap.add_argument("--no-install", action="store_true")
a = ap.parse_args()
OUT = os.path.abspath(a.out); os.makedirs(OUT, exist_ok=True); REPO = os.path.abspath(a.repo)
ASKBAR = os.path.join(REPO, "packages/fabos-agent/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar")
AGENT = os.path.join(REPO, "packages/fabos-agent/usr/lib/fabos/agent")
SSH = ["sshpass", "-p", "fabos", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8", "-p", a.port, "fabos@127.0.0.1"]
SCP = ["sshpass", "-p", "fabos", "scp", "-r", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-P", a.port]
# everything that runs inside the session runs with plasmashell's exact environment (Wayland socket, session bus, XDG_*)
SESS_HELPER = '''#!/usr/bin/env python3
import os, sys, subprocess
pid = subprocess.check_output(["pgrep", "-x", "plasmashell"]).split()[0].decode()
env = dict(l.split("=", 1) for l in open("/proc/%s/environ" % pid).read().split("\\0") if "=" in l)
os.execvpe(sys.argv[1], sys.argv[1:], env)
'''
ACCENT = (59, 110, 245)          # #3B6EF5; a switch that is ON is a 40x22 pill of it (modes.js TOKENS.switch). QEMU's screendump
                                 # renders it darker (measured (48, 84, 184) on the first run), so accent_pills() matches "blue-dominant", not one value
failures = 0; T0 = time.time()
LOG = open(os.path.join(OUT, "vm-modes.log"), "a")
def log(msg):
    line = "[%6.1fs] %s" % (time.time() - T0, msg); print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
def check(cond, msg):
    global failures
    log(("PASS " if cond else "FAIL ") + msg)
    if not cond: failures += 1
    return cond
def vm(cmd, timeout=60):
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except subprocess.TimeoutExpired:
        log("  (ssh timeout after %ss: %s)" % (timeout, cmd[:80])); return ""
def vms(cmd, timeout=60): return vm("/tmp/sess sh -c " + shlex.quote(cmd), timeout)
def scp(src, dst): subprocess.run(SCP + (src if isinstance(src, list) else [src]) + ["fabos@127.0.0.1:" + dst], check=True, capture_output=True)
def scp_back(src, dst): subprocess.run(SCP + ["fabos@127.0.0.1:" + src, dst], check=True, capture_output=True)
def wait_for(pred, timeout, step=1.0):
    end = time.time() + timeout; last = None
    while time.time() < end:
        last = pred()
        if last: return last
        time.sleep(step)
    return None

# ---------------------------------------------------------------- QMP (screendump + absolute pointer on the usb-tablet)
class QMP:
    def __init__(self, path, wait=180):
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
    def screendump(self, path): self.cmd("screendump", filename=path)
    def move(self, x, y, w, h):
        self.cmd("input-send-event", events=[{"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / max(1, w - 1))}},
                                             {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / max(1, h - 1))}}])
    def click(self, x, y, w, h, button="left"):
        self.move(x, y, w, h); time.sleep(0.35)
        self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": True, "button": button}}]); time.sleep(0.08)
        self.cmd("input-send-event", events=[{"type": "btn", "data": {"down": False, "button": button}}]); time.sleep(0.25)

from PIL import Image
SHOTS = 0
def shot(label):
    """QMP screendump -> PNG in OUT; returns (Image, path). The pointer is parked in the top-right corner first so no hover tint is in the picture."""
    global SHOTS
    SHOTS += 1; ppm = "/tmp/r8-modes-shot.ppm"
    q.screendump(ppm); time.sleep(0.2)
    im = Image.open(ppm).convert("RGB")
    png = os.path.join(OUT, "%02d-%s.png" % (SHOTS, label)); im.save(png)
    log("SHOT %s (%dx%d)" % (os.path.basename(png), im.width, im.height)); return im, png
def spectacle(label):
    """The in-session screenshot (what the owner would take): spectacle -b -n -f -o, copied back beside the QMP dump."""
    dst = "/tmp/r8-%s.png" % label
    vms("rm -f %s; spectacle -b -n -f -o %s >/dev/null 2>&1" % (dst, dst), timeout=40)
    ok = wait_for(lambda: "yes" in vm("test -s %s && echo yes" % dst), 15)
    if ok:
        scp_back(dst, os.path.join(OUT, "spectacle-%s.png" % label)); log("SPECTACLE spectacle-%s.png" % label)
    return bool(ok)

def accent_pills(im, region=None, wmin=37, wmax=43, hmin=20, hmax=24):
    """Connected regions of accent-coloured pixels whose bounding box is a 40x22 pill (the switch ON). region = (x0, y0, x1, y1)."""
    w, h = im.size; px = im.load()
    x0, y0, x1, y1 = region or (0, 0, w, h)
    mask = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = px[x, y]
            # the accent as the screendump shows it: strongly blue, little red — (59,110,245) live, (48,84,184) through QEMU, hover-lifted too
            if b >= 140 and b - r >= 70 and b - g >= 40 and r <= 130 and g <= 170:
                mask.add((x, y))
    seen, boxes = set(), []
    for p in mask:
        if p in seen: continue
        stack = [p]; seen.add(p); xs = []; ys = []
        while stack:
            cx, cy = stack.pop(); xs.append(cx); ys.append(cy)
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1), (cx + 1, cy + 1), (cx - 1, cy - 1), (cx + 1, cy - 1), (cx - 1, cy + 1)):
                if (nx, ny) in mask and (nx, ny) not in seen:
                    seen.add((nx, ny)); stack.append((nx, ny))
        bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if wmin <= bw <= wmax and hmin <= bh <= hmax:
            boxes.append((min(xs), min(ys), bw, bh))
    return sorted(boxes, key=lambda b: (b[1], b[0]))
def centre(b): return (b[0] + b[2] // 2, b[1] + b[3] // 2)
def fabos(args):
    """The fabos CLI inside the session's environment (it reads $XDG_RUNTIME_DIR/fabos-agent/token; a plain ssh command has no XDG_RUNTIME_DIR)."""
    return vms("fabos " + args + " 2>&1")
def settings():
    try: return json.loads(fabos("settings --json"))
    except ValueError: return {}
def research_default():
    return str(settings().get("agent.research", "?"))

# ================================================================ run
log("== waiting for ssh + the Plasma session")
check(wait_for(lambda: "ok" in vm("echo ok", timeout=20), 600, 5) is not None, "ssh to the VM")
check(wait_for(lambda: "1" in vm("pgrep -x plasmashell >/dev/null && pgrep -x kwin_wayland >/dev/null && echo 1"), 300, 3) is not None, "plasmashell + kwin_wayland running")
time.sleep(5)
log(vm("cat /etc/os-release | grep PRETTY; dpkg-query -W -f '${Version}\\n' fabos-agent; head -1 /proc/meminfo").strip().replace("\n", " | "))
with open("/tmp/r8-sess.py", "w") as f: f.write(SESS_HELPER)
scp("/tmp/r8-sess.py", "/tmp/sess"); vm("chmod +x /tmp/sess")
env_probe = vms("echo WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR")
check("WAYLAND_DISPLAY=wayland" in env_probe, "session environment helper (/tmp/sess) reads plasmashell's env: " + env_probe.strip()[:120])
q = QMP(a.qmp)

if not a.no_install:
    log("== installing the working tree's ask bar plasmoid + fabos_agentd.py + command_center.py; restarting fabos-agent and plasmashell")
    vm("rm -rf /tmp/askbar-contents /tmp/agent; mkdir -p /tmp/askbar-contents /tmp/agent")
    scp([os.path.join(ASKBAR, "contents/ui"), os.path.join(ASKBAR, "contents/code")], "/tmp/askbar-contents/")
    scp([os.path.join(AGENT, "fabos_agentd.py"), os.path.join(AGENT, "command_center.py")], "/tmp/agent/")
    inst = vm("echo fabos | sudo -S sh -c '"
              "P=/usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents && mkdir -p $P/code && cp /tmp/askbar-contents/ui/* $P/ui/ && cp /tmp/askbar-contents/code/modes.js $P/code/ && "
              "cp /tmp/agent/fabos_agentd.py /tmp/agent/command_center.py /usr/lib/fabos/agent/ && chmod 755 /usr/lib/fabos/agent/fabos_agentd.py /usr/lib/fabos/agent/command_center.py && "
              "chown -R root:root $P /usr/lib/fabos/agent && echo installed' 2>/dev/null")
    if not check("installed" in inst, "files installed under /usr (the package paths) through sudo"):
        # no sudo: the user-level fallback — the plasmoid from ~/.local, the daemon from /tmp through a unit override
        vm("mkdir -p ~/.local/share/plasma/plasmoids && rm -rf ~/.local/share/plasma/plasmoids/in.patienceai.fabos.askbar && cp -r /usr/share/plasma/plasmoids/in.patienceai.fabos.askbar ~/.local/share/plasma/plasmoids/ && "
           "cp /tmp/askbar-contents/ui/* ~/.local/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/ui/ && mkdir -p ~/.local/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/code && cp /tmp/askbar-contents/code/modes.js ~/.local/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/code/ && "
           "mkdir -p ~/.config/systemd/user/fabos-agent.service.d && printf '[Service]\\nExecStart=\\nExecStart=/usr/bin/python3 /tmp/agent/fabos_agentd.py\\n' > ~/.config/systemd/user/fabos-agent.service.d/r8.conf && chmod +x /tmp/agent/*.py")
        log("  (user-level fallback: ~/.local plasmoid override + unit ExecStart override to /tmp/agent)")
    check("modes.js" in vm("ls /usr/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/code/ ~/.local/share/plasma/plasmoids/in.patienceai.fabos.askbar/contents/code/ 2>/dev/null"), "contents/code/modes.js present in the installed plasmoid")
    vms("systemctl --user daemon-reload; systemctl --user restart fabos-agent; sleep 2; kbuildsycoca6 --noincremental >/dev/null 2>&1; systemctl --user restart plasma-plasmashell; sleep 6")
    check(wait_for(lambda: "1" in vm("pgrep -x plasmashell >/dev/null && echo 1"), 60, 2) is not None, "plasmashell restarted with the new ask bar")
    time.sleep(4)
st = wait_for(lambda: (lambda o: o if "capabilities" in o else None)(fabos("status --json")), 60, 2)
check(st is not None, "fabos-agentd is the 1.0-8 daemon: /status carries `capabilities` " + (json.dumps(json.loads(st).get("capabilities")) if st else "(missing: %s)" % fabos("status --json").strip()[:160]))
check(research_default() == "true", "fabos settings agent.research is true to begin with (got %s)" % research_default())

# ---------------------------------------------------------------- the home bar
log("== the home bar: focus the field -> the strip unfolds; find the two pills; click Research; check the setting")
q.move(2, 2, 1280, 800)
im, _ = shot("desktop-before")
W, H = im.size
field_x, field_y = W // 2, int(H * 0.24) + 34           # the card sits at the top of the applet strip (24 % of the screen height); the field is its first row
q.click(field_x, field_y, W, H); time.sleep(0.6)
q.move(W - 2, 2, W, H); time.sleep(0.4)                   # park the pointer: no hover tint in the pictures
im, png = shot("bar-both-on")
spectacle("bar-both-on")
bar_region = (0, int(H * 0.20), W, int(H * 0.24) + 160)
pills = accent_pills(im, bar_region)
log("  accent pills in the bar region: %s" % pills)
check(len(pills) == 2, "the strip shows two switches ON (accent 40x22 pills) under the field: %d found" % len(pills))
if len(pills) >= 2:
    research, computer = pills[0], pills[1]
    check(abs(research[1] - computer[1]) <= 2 and research[0] < computer[0], "Research left, Computer use right, on one line (y %d / %d)" % (research[1], computer[1]))
    check(research[1] > field_y + 10, "the strip lies BELOW the field row (pill y %d, field y %d): never over the mic or Do it" % (research[1], field_y))
    rx, ry = centre(research)
    q.click(rx, ry, W, H)                                  # a REAL pointer click on the Research switch
    time.sleep(0.5)
    q.move(W - 2, 2, W, H); time.sleep(0.3)
    im2, _ = shot("bar-research-off")                      # within the 2.4 s confirmation line
    spectacle("bar-research-off")
    v = research_default()
    check(v == "false", "fabos settings agent.research after the click: %s (no chat open -> the default for new chats)" % v)
    p2 = accent_pills(im2, bar_region)
    check(len(p2) == 1 and abs(p2[0][0] - computer[0]) <= 2, "one accent pill left (Computer use); Research's track is grey: %s" % p2)
    try: caps = json.loads(fabos("status --json")).get("capabilities")
    except ValueError: caps = None
    check(caps == {"research": False, "computer_use": True}, "/status.capabilities follows: %s" % json.dumps(caps))
    q.click(rx, ry, W, H); time.sleep(0.6)                # and back on
    q.move(W - 2, 2, W, H); time.sleep(0.3)
    im3, _ = shot("bar-both-on-again")
    check(research_default() == "true", "second click: agent.research back to true")
    check(len(accent_pills(im3, bar_region)) == 2, "both pills accent again")
    # Computer use through the pointer too
    cx, cy = centre(computer)
    q.click(cx, cy, W, H); time.sleep(0.6); q.move(W - 2, 2, W, H); time.sleep(0.3)
    im4, _ = shot("bar-computer-use-off")
    check(str(settings().get("agent.computer_use")) == "false", "Computer use click: agent.computer_use false")
    p4 = accent_pills(im4, bar_region)
    check(len(p4) == 1 and abs(p4[0][0] - research[0]) <= 2, "one accent pill left (Research): %s" % p4)
    q.click(cx, cy, W, H); time.sleep(0.6)
    check(str(settings().get("agent.computer_use")) == "true", "Computer use back to true")

# ---------------------------------------------------------------- Fab AI Controls
log("== Fab AI Controls: the same strip in the chat header and the composer; click the composer's Research switch")
vms("pkill -f command_center.py; sleep 1; rm -f /tmp/r8-controls.log; setsid -f fabos-command-center > /tmp/r8-controls.log 2>&1")
check(wait_for(lambda: "1" in vm("pgrep -f 'command_center.py' >/dev/null && echo 1"), 30, 1) is not None, "Fab AI Controls process is up")
# the window takes its time on a 2-vCPU guest with a cold cache: poll the screen for the composer strip (up to 60 s), then read the log
def controls_pills():
    q.move(W - 2, 2, W, H); time.sleep(0.3)
    im, _ = shot("controls-poll")
    found = [p for p in accent_pills(im) if p[1] > H * 0.55]
    return (im, found) if len(found) >= 2 else None
got = wait_for(controls_pills, 60, 4)
alive = "1" in vm("pgrep -f 'command_center.py' >/dev/null && echo 1")
clog = vm("tail -n 12 /tmp/r8-controls.log 2>/dev/null").strip()
open(os.path.join(OUT, "controls-stderr.log"), "w").write(vm("cat /tmp/r8-controls.log 2>/dev/null"))
check(alive, "Fab AI Controls still running once its window is up" + ("" if alive else " — its log: " + clog[:400].replace("\n", " | ")))
im5, _ = shot("controls-both-on")
spectacle("controls-both-on")
pills5 = accent_pills(im5)
log("  accent pills on screen: %s" % pills5)
lower = [p for p in pills5 if p[1] > H * 0.55]
upper = [p for p in pills5 if p[1] <= H * 0.55]
check(len(lower) >= 2, "the composer row shows two switches ON: %s%s" % (lower, "" if lower else " (log: " + clog[:300].replace("\n", " | ") + ")"))
if upper:
    log("  header strip pills too (window wide enough): %s" % upper)
if len(lower) >= 2:
    lower = sorted(lower, key=lambda b: b[0])
    rx, ry = centre(lower[0])
    q.click(rx, ry, W, H); time.sleep(0.5)
    q.move(W - 2, 2, W, H); time.sleep(0.3)
    im6, _ = shot("controls-research-off")               # the toast "Research off for new chats" is up for 2.4 s
    spectacle("controls-research-off")
    v = research_default()
    check(v == "false", "fabos settings agent.research after the click in Fab AI Controls: %s" % v)
    p6 = [p for p in accent_pills(im6) if p[1] > H * 0.55]
    check(len(p6) == 1 and abs(p6[0][0] - lower[1][0]) <= 2, "the composer strip shows one accent pill (Computer use) now: %s" % p6)
    if upper:
        u6 = [p for p in accent_pills(im6) if p[1] <= H * 0.55]
        check(len(u6) == len(upper) - 1, "the header strip mirrored the change (%d -> %d accent pills)" % (len(upper), len(u6)))
    q.click(rx, ry, W, H); time.sleep(0.6)
    check(research_default() == "true", "second click in Fab AI Controls: agent.research back to true")
    q.move(W - 2, 2, W, H); time.sleep(0.3)
    shot("controls-both-on-again")
vms("pkill -f command_center.py")

# ---------------------------------------------------------------- health + evidence
log("== health + evidence")
check("1" in vm("pgrep -x plasmashell >/dev/null && echo 1"), "plasmashell still running at the end")
diag = vm("journalctl --user -b --no-pager -o cat _COMM=plasmashell 2>/dev/null | grep -E 'in.patienceai.fabos.askbar.*(qml|js):[0-9]+' | tail -5")
check(diag.strip() == "", "no QML / JS diagnostics from the ask bar in plasmashell's journal" + (": " + diag.strip()[:300] if diag.strip() else ""))
agent_log = vm("journalctl --user -b --no-pager -o cat -u fabos-agent 2>/dev/null | tail -40", timeout=60)
check("Traceback" not in agent_log, "no Python traceback in the fabos-agent journal")
open(os.path.join(OUT, "fabos-agent-journal.log"), "w").write(agent_log)
open(os.path.join(OUT, "plasmashell-journal.log"), "w").write(vm("journalctl --user -b --no-pager -o short-iso _COMM=plasmashell 2>/dev/null | tail -150", timeout=120))
open(os.path.join(OUT, "fabos-settings.json"), "w").write(fabos("settings --json"))
open(os.path.join(OUT, "fabos-status.json"), "w").write(fabos("status --json"))
open(os.path.join(OUT, "fabos-log.json"), "w").write(fabos("log --json --limit 40"))
log("VM MODES DONE failures=%d" % failures)
sys.exit(1 if failures else 0)
