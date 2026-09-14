#!/usr/bin/env python3
"""Real pointer input for the headless applet harnesses: a raw Wayland client (no pywayland in the image) that binds
KWin's org_kde_kwin_fake_input global, authenticates and moves / clicks the session's pointer, so QML hover state
(HoverHandler, MouseArea.containsMouse) is exercised by the compositor exactly as a mouse would.

KWin only advertises the fake-input global to a trusted client: one whose executable matches a .desktop file
carrying X-KDE-Wayland-Interfaces=org_kde_kwin_fake_input. tests/dock-qml-harness/kwin-session.sh writes that
file for the image's python3 before starting kwin_wayland.

  fakeinput.py hold SECONDS        keep one authenticated device connected (run it in the background for the whole
                                   pointer phase: with no other pointer device, every one-shot process would otherwise
                                   add and remove the seat's only pointer, and the client would see leave/enter per move)
  fakeinput.py move X Y            absolute pointer motion (output pixels)
  fakeinput.py click X Y           move, then left button press + release
  fakeinput.py globals             list the advertised globals (diagnostics)
Exit 0 after a wl_display.sync round trip (the compositor has processed the request); 2 if the global is missing.
"""
import os, socket, struct, sys, time

BTN_LEFT = 0x110
OP = {"authenticate": 0, "pointer_motion": 1, "button": 2, "axis": 3, "pointer_motion_absolute": 9}   # fake-input.xml request order


class Wayland:
    def __init__(self):
        path = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        if not path.startswith("/"):
            path = os.path.join(os.environ["XDG_RUNTIME_DIR"], path)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.next_id = 2
        self.buf = b""
        self.globals = {}        # interface -> (name, version)
        self.done = set()

    # ---- wire format: header (object id, size<<16 | opcode), then 32-bit little-endian args
    @staticmethod
    def wstr(text):
        b = text.encode() + b"\0"
        return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)

    def send(self, obj, opcode, payload=b""):
        self.s.sendall(struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload)

    def new_id(self):
        i = self.next_id; self.next_id += 1; return i

    def read_events(self, until_done):
        while until_done not in self.done:
            chunk = self.s.recv(65536)
            if not chunk:
                raise SystemExit("compositor closed the connection")
            self.buf += chunk
            while len(self.buf) >= 8:
                obj, so = struct.unpack("<II", self.buf[:8])
                size, opcode = so >> 16, so & 0xFFFF
                if len(self.buf) < size:
                    break
                body = self.buf[8:size]; self.buf = self.buf[size:]
                self.event(obj, opcode, body)

    def event(self, obj, opcode, body):
        if obj == 1 and opcode == 0:      # wl_display.error
            oid, code = struct.unpack("<II", body[:8]); n = struct.unpack("<I", body[8:12])[0]
            raise SystemExit(f"wl_display.error object {oid} code {code}: {body[12:12 + n - 1].decode(errors='replace')}")
        if obj == 2 and opcode == 0:      # wl_registry.global: name, interface, version
            name = struct.unpack("<I", body[:4])[0]; n = struct.unpack("<I", body[4:8])[0]
            iface = body[8:8 + n - 1].decode(); off = 8 + n + (-n % 4)
            version = struct.unpack("<I", body[off:off + 4])[0]
            self.globals[iface] = (name, version)
        elif obj >= 3 and opcode == 0 and obj in self.callbacks:   # wl_callback.done
            self.done.add(obj)

    callbacks = set()

    def sync(self):
        cb = self.new_id(); self.callbacks.add(cb)
        self.send(1, 0, struct.pack("<I", cb))   # wl_display.sync
        self.read_events(cb)

    def registry(self):
        self.send(1, 1, struct.pack("<I", 2))    # wl_display.get_registry -> id 2
        self.next_id = 3
        self.sync()

    def bind(self, iface, max_version):
        name, version = self.globals[iface]
        v = min(version, max_version); oid = self.new_id()
        self.send(2, 0, struct.pack("<I", name) + self.wstr(iface) + struct.pack("<II", v, oid))   # wl_registry.bind
        return oid


def main(argv):
    w = Wayland(); w.registry()
    if argv[:1] == ["globals"]:
        for k, (n, v) in sorted(w.globals.items()): print(f"{k} v{v} (name {n})")
        return 0
    if "org_kde_kwin_fake_input" not in w.globals:
        print("org_kde_kwin_fake_input is not advertised to this client (not trusted?)", file=sys.stderr)
        print("globals: " + " ".join(sorted(w.globals)), file=sys.stderr)
        return 2
    fi = w.bind("org_kde_kwin_fake_input", 4)
    w.send(fi, OP["authenticate"], w.wstr("Fab OS applet harness") + w.wstr("pointer for the headless QML checks"))
    if argv[0] == "hold":
        w.sync(); print("holding a pointer device for " + argv[1] + " s", flush=True)
        time.sleep(float(argv[1])); return 0
    cmd, x, y = argv[0], float(argv[1]), float(argv[2])
    fixed = lambda v: struct.pack("<i", int(round(v * 256)))
    w.send(fi, OP["pointer_motion_absolute"], fixed(x) + fixed(y))
    w.sync()
    if cmd == "click":
        time.sleep(0.15)   # let the client see the motion (and its enter) before the press, as a hand would
        w.send(fi, OP["button"], struct.pack("<II", BTN_LEFT, 1)); w.sync()
        time.sleep(0.06)   # a human-length press
        w.send(fi, OP["button"], struct.pack("<II", BTN_LEFT, 0)); w.sync()
    print(f"{cmd} {x:g} {y:g} ok")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or (sys.argv[1] == "hold" and len(sys.argv) != 3) or (sys.argv[1] not in ("globals", "hold") and len(sys.argv) != 4):
        print(__doc__); sys.exit(1)
    sys.exit(main(sys.argv[1:]))
