#!/usr/bin/env python3
"""KRunner D-Bus runner: type a request in the Plasma search (Alt+Space / Kickoff search) and hand it to the Fab OS agent.

Triggers: queries starting with "do ", "fab ", "fabos " or "ask ", or any query ending with "?" or longer than 40 chars.
Implements org.kde.krunner1 (Actions, Match, Run). Activated on demand via D-Bus (see fabos-runner.desktop + .service).
"""
import json, os, sys, urllib.request
import dbus, dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent")
IFACE = "org.kde.krunner1"
PREFIXES = ("do ", "fab ", "fabos ", "ask ")


def agent(method, path, body=None):
    token = open(os.path.join(RUN, "token")).read().strip()
    port = open(os.path.join(RUN, "port")).read().strip()
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


class Runner(dbus.service.Object):
    def __init__(self):
        bus = dbus.SessionBus()
        name = dbus.service.BusName("in.patienceai.fabos.runner", bus)
        super().__init__(name, "/runner")
        self.loop = GLib.MainLoop()
        GLib.timeout_add_seconds(300, self.loop.quit)   # exit when idle; D-Bus re-activates us

    @dbus.service.method(IFACE, out_signature="a(sss)")
    def Actions(self):
        return [("open", "Open Fab AI Controls", "fabos-command-center")]

    @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})")
    def Match(self, query):
        q = query.strip()
        low = q.lower()
        text = None
        for p in PREFIXES:
            if low.startswith(p):
                text = q[len(p):].strip()
                break
        if text is None and (low.endswith("?") or len(q) > 40):
            text = q
        if not text or len(text) < 3:
            return []
        return [("task:" + text, "Ask Fab OS to: " + text, "fabos-command-center", 100, 1.0, {"subtext": "runs autonomously; watch it in Fab AI Controls"})]

    @dbus.service.method(IFACE, in_signature="ss")
    def Run(self, match_id, action_id):
        if action_id == "open":
            GLib.spawn_command_line_async("fabos-command-center")
            return
        if match_id.startswith("task:"):
            try:
                r = agent("POST", "/tasks", {"request": match_id[5:]})
                GLib.spawn_command_line_async("notify-send -a 'Fab OS' -i fabos 'Fab OS agent' 'Task #%d started: %s'" % (r.get("id", 0), match_id[5:60].replace("'", "")))
            except Exception as e:
                GLib.spawn_command_line_async("notify-send -a 'Fab OS' -i fabos -u critical 'Fab OS agent' 'Could not start task: %s'" % str(e).replace("'", ""))


def main():
    DBusGMainLoop(set_as_default=True)
    Runner().loop.run()


if __name__ == "__main__":
    main()
