#!/usr/bin/env python3
"""Fab AI Controls never waits on the daemon from the GUI thread — measured, not inferred. Offscreen, no daemon needed:
the test IS the daemon (one that accepts TCP and never answers, then one that answers).

  podman run --rm -v $PWD:/work:Z -e QT_QPA_PLATFORM=offscreen localhost/fabos:vm python3 /work/tests/ai-controls-workers-test.py

Checks (each a number printed, asserted, and worth pasting into docs/QA.md):
  1. ApiQueue.stop() against a hung daemon: a 3-call job blocked in its first read ends within one socket round trip
     (< 1 s, was 3 x API_TIMEOUT), no further call of the job is started — so closeEvent()'s wait cannot run out.
  2. ApiQueue normal path: results arrive through the queued `done` signal ON the GUI thread; a queued job with the same
     key is replaced (done(job, None)), never stacked behind a slow one.
  3. ApiWorker (provider / mail checks, 20-45 s timeouts) and ApiJobWorker (Settings > Save): abort() makes run() return
     within one round trip and emit nothing; abort() before start() never connects.
  4. SettingsDialog.done() with a provider check in flight (the user presses Cancel / Escape): returns in < 1 s on the
     GUI thread (was up to 5 s: it waited for the worker instead of aborting it).
"""
import json, os, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "packages", "fabos-agent", "usr", "lib", "fabos", "agent"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="fabos-workers-")   # command_center reads RUN at import: never a real daemon's files
RUN = os.path.join(os.environ["XDG_RUNTIME_DIR"], "fabos-agent"); os.makedirs(RUN)
open(os.path.join(RUN, "token"), "w").write("test-token\n")

import command_center as cc  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
failures = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    print(("PASS  " if cond else "FAIL  ") + what)
    if not cond:
        failures.append(what)


def spin(n=20):
    for _ in range(n):
        app.processEvents(); time.sleep(0.01)


class HungDaemon(threading.Thread):
    """Accepts every connection and never answers."""
    def __init__(self):
        super().__init__(daemon=True)
        self.s = socket.socket(); self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", 0)); self.s.listen(16); self.accepted = []

    def run(self):
        while True:
            try:
                c, _ = self.s.accept(); self.accepted.append(c)
            except OSError:
                return

    def wait_connected(self, n, timeout=5.0):
        t0 = time.time()
        while len(self.accepted) < n and time.time() - t0 < timeout:
            time.sleep(0.01)
        time.sleep(0.2)                     # the worker is now blocked in getresponse()
        return len(self.accepted) >= n


class AnsweringDaemon(threading.Thread):
    """Answers every request with {"path": ..., "ok": true}."""
    def __init__(self):
        super().__init__(daemon=True)
        self.s = socket.socket(); self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", 0)); self.s.listen(16)

    def run(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            req = c.recv(65536).decode(errors="replace")
            path = req.split(" ")[1] if " " in req else "?"
            body = json.dumps({"path": path, "ok": True}).encode()
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            c.close()


def use(daemon):
    open(os.path.join(RUN, "port"), "w").write("%d\n" % daemon.s.getsockname()[1])


hung = HungDaemon(); hung.start(); use(hung)
print("API_TIMEOUT = %d s" % cc.API_TIMEOUT)

# ---------------------------------------------------------------- 1. ApiQueue.stop() against a hung daemon
q = cc.ApiQueue(); got = []
q.done.connect(lambda job, res: got.append(res))
q.submit([("GET", "/status", None), ("GET", "/tasks", None), ("GET", "/settings", None)])
check(hung.wait_connected(1), "ApiQueue worker connected to the (hung) daemon")
t = time.time(); q.stop(); finished = q.wait(20000); dt = time.time() - t
check(finished and dt < 1.0, "ApiQueue.stop(): 3-call job blocked in its first read ended in %.3f s (< 1 s; was 3 x API_TIMEOUT = %d s)" % (dt, 3 * cc.API_TIMEOUT))
check(len(hung.accepted) == 1, "no further call of the stopped job was started (connections: %d)" % len(hung.accepted))

# ---------------------------------------------------------------- 2. normal path: signals on the GUI thread, key replacement
ans = AnsweringDaemon(); ans.start(); use(ans)
q2 = cc.ApiQueue(); got2 = []; gui_ident = threading.get_ident()
q2.done.connect(lambda job, res: got2.append((threading.get_ident() == gui_ident, res)))
q2.submit([("GET", "/status", None)], key="list")
q2.submit([("GET", "/tasks", None)], key="list")           # replaces the first while it is still queued
q2.submit([("GET", "/a", None), ("GET", "/b", None)])
t = time.time()
while len(got2) < 3 and time.time() - t < 5:
    spin(1)
check(len(got2) == 3, "three job results delivered (%d)" % len(got2))
check(all(g[0] for g in got2), "every result callback ran on the GUI thread")
check(any(g[1] is None for g in got2), "a queued job with the same key was replaced (done(job, None)), not run twice")
check(any(isinstance(g[1], list) and len(g[1]) == 2 and g[1][1].get("path") == "/b" for g in got2), "a 2-call job returned both results in order")
q2.stop(); check(q2.wait(5000), "ApiQueue against an answering daemon stops cleanly")

# ---------------------------------------------------------------- 3. one-off workers: abort()
use(hung); before = len(hung.accepted)
em = []
w = cc.ApiWorker("POST", "/mail/test", {"x": 1}, timeout=45); w.done.connect(lambda r: em.append(r)); w.start()
check(hung.wait_connected(before + 1), "ApiWorker (mail check, 45 s timeout) connected")
t = time.time(); w.abort(); ok = w.wait(20000); dt = time.time() - t; spin()
check(ok and dt < 1.0 and not em, "ApiWorker.abort(): returned in %.3f s, emitted nothing (%d)" % (dt, len(em)))

em2 = []
j = cc.ApiJobWorker([("PUT", "/settings", {}), ("POST", "/secrets", {}), ("POST", "/secrets", {})]); j.done.connect(lambda r: em2.append(r)); j.start()
check(hung.wait_connected(before + 2), "ApiJobWorker (Save: PUT + 2 secrets) connected")
t = time.time(); j.abort(); ok = j.wait(20000); dt = time.time() - t; spin()
check(ok and dt < 1.0 and not em2, "ApiJobWorker.abort(): returned in %.3f s, emitted nothing (%d)" % (dt, len(em2)))
check(len(hung.accepted) == before + 2, "no second call of the aborted job was started (connections: %d)" % (len(hung.accepted) - before))

em3 = []
w3 = cc.ApiWorker("POST", "/providers/test", {}, timeout=20); w3.done.connect(lambda r: em3.append(r))
w3.abort(); w3.start(); ok = w3.wait(5000); time.sleep(0.3); spin()
check(ok and not em3 and len(hung.accepted) == before + 2, "abort() before start(): run() returned without connecting or emitting")

# ---------------------------------------------------------------- 4. SettingsDialog.done() with a check in flight
sd = cc.SettingsDialog(None, {})
em4 = []
sd.worker = cc.ApiWorker("POST", "/providers/test", {"provider": "x"}, timeout=20); sd.worker.done.connect(lambda r: em4.append(r))
sd._workers.append(sd.worker); sd.worker.start()
check(hung.wait_connected(before + 3), "SettingsDialog provider check connected")
t = time.time(); sd.done(0); dt = time.time() - t; spin()
check(dt < 1.0 and not em4, "SettingsDialog.done() with a provider check in flight: %.3f s on the GUI thread (was up to 5 s), emitted %d" % (dt, len(em4)))

print("ai-controls-workers-test: %s  checks=%d failures=%d" % ("FAIL" if failures else "PASS", CHECKS[0], len(failures)))
sys.exit(1 if failures else 0)
