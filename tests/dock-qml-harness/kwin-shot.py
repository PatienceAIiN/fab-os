#!/usr/bin/env python3
"""Screenshot of the virtual kwin_wayland session through KWin's org.kde.KWin.ScreenShot2 D-Bus interface.

KWin hands that interface only to a trusted caller: a process whose executable matches a .desktop file carrying
X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2 (Spectacle's does; it hangs in the virtual session, so this
does the two calls itself). tests/desktop-applets-qml-test.sh (step shot) starts the harness's kwin_wayland with KWin's
own test switch KWIN_SCREENSHOT_NO_PERMISSION_CHECKS=1, so plain python3 may call it there (a real session keeps the
check; the desktop-file route was refused in the container: org.kde.KWin.ScreenShot2.Error.NoAuthorized).

  kwin-shot.py <out.raw> <out.json>     CaptureWorkspace (falls back to CaptureActiveScreen) -> raw pixels + a JSON
                                        header {width, height, stride, format}; the host converts it to PNG (Pillow).
Exit 0 on success, 2 when the interface refused (not trusted / not offered).
"""
import json, os, sys
import dbus


def main(raw_path, json_path):
    bus = dbus.SessionBus()
    obj = bus.get_object("org.kde.KWin", "/org/kde/KWin/ScreenShot2")
    iface = dbus.Interface(obj, "org.kde.KWin.ScreenShot2")
    opts = dbus.Dictionary({"include-cursor": dbus.Boolean(False), "native-resolution": dbus.Boolean(True)}, signature="sv")
    r, w = os.pipe()
    try:
        try:
            res = iface.CaptureWorkspace(opts, dbus.types.UnixFd(w), timeout=30)
        except dbus.exceptions.DBusException as e:
            print("CaptureWorkspace failed (" + e.get_dbus_name() + "): trying CaptureActiveScreen", file=sys.stderr)
            res = iface.CaptureActiveScreen(opts, dbus.types.UnixFd(w), timeout=30)
    except dbus.exceptions.DBusException as e:
        print("ScreenShot2 refused: " + e.get_dbus_name() + " " + str(e), file=sys.stderr)
        return 2
    os.close(w)
    width, height, stride, fmt = int(res["width"]), int(res["height"]), int(res["stride"]), int(res["format"])
    want = stride * height
    data = bytearray()
    while len(data) < want:
        chunk = os.read(r, 1 << 20)
        if not chunk:
            break
        data += chunk
    os.close(r)
    open(raw_path, "wb").write(bytes(data))
    json.dump({"width": width, "height": height, "stride": stride, "format": fmt, "bytes": len(data)}, open(json_path, "w"))
    print(f"kwin-shot: {width}x{height} stride {stride} format {fmt} ({len(data)} of {want} bytes)")
    return 0 if len(data) == want else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
