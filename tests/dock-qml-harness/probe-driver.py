#!/usr/bin/env python3
"""Drive tests/dock-qml-harness/ModelProbe.qml against a live TasksModel (virtual kwin_wayland in the image, or the VM's
Plasma session) and assert the dock's tap / indicator semantics on REAL rows. stdlib only (runs inside the image / VM).

  probe-driver.py <probe.log> <cmd file> matching  <base>...   every <base> (dolphin, konsole, firefox, ...) must be ONE
                                                             row that is a window (or group) AND carries its launcher
  probe-driver.py <probe.log> <cmd file> taps      <running base> <other running base> <not-running base>
                                                             activate -> minimise -> activate on the first, activate the
                                                             second, launch the third (its row merges; a tap while it
                                                             starts is ignored), middle click = new instance -> group ->
                                                             cycle; the window count never grows on a left tap
  probe-driver.py <probe.log> <cmd file> elsewhere <base>    with PROBE_FILTER_DESKTOP=1 and <base>'s window on another
                                                             desktop: the launcher row still reports running (dot) and a
                                                             tap activates that window (tap = "elsewhere"), no new window
Prints PASS/FAIL lines and "DRIVER DONE failures=N"; exit 1 on any failure.
"""
import json, os, sys, time

LOG, CMD = sys.argv[1], sys.argv[2]
failures = 0
def check(cond, msg):
    global failures
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond: failures += 1

def lines():
    try:
        with open(LOG, errors="replace") as f: return f.read().splitlines()
    except FileNotFoundError: return []
def last_rows():
    for l in reversed(lines()):
        i = l.find("ROWS ")
        if i >= 0:
            try: return json.loads(l[i + 5:])
            except ValueError: continue
    return None
def wait_rows(pred, timeout=20, msg=""):
    end = time.time() + timeout; last = None
    while time.time() < end:
        r = last_rows()
        if r and pred(r): return r
        last = r; time.sleep(0.5)
    return last if pred is None else None
def base(s):
    s = str(s or "").split("?")[0]; s = s[s.rfind("/") + 1:]; s = s[s.rfind(":") + 1:]
    return s[:-8].lower() if s.lower().endswith(".desktop") else s.lower()
def find(snapshot, b):
    """row index whose launcher url / app id names <b> (substring of the desktop-file base)."""
    for i, r in enumerate(snapshot["rows"]):
        if b in base(r.get("LauncherUrlWithoutIcon")) or b in base(r.get("AppId")): return i
    return -1
def row_of(snapshot, b):
    i = find(snapshot, b); return snapshot["rows"][i] if i >= 0 else None
def describe(r):
    kind = "group" if r["IsGroupParent"] else ("window" if r["IsWindow"] else ("startup" if r["IsStartup"] else "launcher"))
    return "%-34s %-28s %-8s launcher=%-5s active=%-5s min=%-5s children=%d state=%-9s tap=%s%s" % (
        r["LauncherUrlWithoutIcon"][:34], r["AppId"][:28], kind, r["HasLauncher"] or r["IsLauncher"], r["IsActive"], r["IsMinimized"], r["ChildCount"], r["state"], r["tap"],
        (" elsewhere=" + json.dumps(r["elsewhere"])) if r.get("elsewhere") else "")
def table(snapshot):
    print("ROWS count=%d windows=%d allWindows=%d desktop=%s" % (snapshot["count"], snapshot["windows"], snapshot["allWindows"], snapshot.get("desktop")))
    for i, r in enumerate(snapshot["rows"]): print("  [%d] %s" % (i, describe(r)))
def send(cmd):
    n = len(lines())
    with open(CMD + ".tmp", "w") as f: f.write(cmd + "\n")
    os.replace(CMD + ".tmp", CMD)
    end = time.time() + 10
    tag = cmd.split()[0].upper() + " " + cmd.split()[1] + " -> "
    while time.time() < end:
        for l in lines()[n:]:
            i = l.find(tag)
            if i >= 0: return l[i + len(tag):].strip()
        time.sleep(0.2)
    return None
def tap(snapshot, b):
    i = find(snapshot, b); check(i >= 0, "row for %s present (index %d)" % (b, i))
    a = send("tap %d" % i); print("  tap [%d] %s -> %s" % (i, b, a)); return a
def middle(snapshot, b):
    i = find(snapshot, b); return send("middle %d" % i)
def settle(t=2.5):
    time.sleep(t); return last_rows()

mode = sys.argv[3]; args = sys.argv[4:]
snap = wait_rows(lambda r: r["count"] > 0, 30)
check(snap is not None, "probe is running and the TasksModel has rows")
if snap is None: print("DRIVER DONE failures=%d" % failures); sys.exit(1)

if mode == "matching":
    for b in args:
        snap = wait_rows(lambda r, b=b: (row_of(r, b) or {}).get("IsWindow") or (row_of(r, b) or {}).get("IsGroupParent"), 45)
        r = row_of(snap or last_rows(), b)
        if r is None: check(False, "%s: no row at all" % b); continue
        check(r["IsWindow"] or r["IsGroupParent"], "%s: its window is a row (%s)" % (b, "group" if r["IsGroupParent"] else "window" if r["IsWindow"] else "launcher only -> the pinned icon lost its window"))
        check(r["HasLauncher"], "%s: the window row carries its pinned launcher (HasLauncher) -> ONE icon, marker on the pinned icon" % b)
        dup = [i for i, x in enumerate((snap or last_rows())["rows"]) if base(x["LauncherUrlWithoutIcon"]) == base(r["LauncherUrlWithoutIcon"])]
        check(len(dup) == 1, "%s: exactly one row for %s (rows %s)" % (b, base(r["LauncherUrlWithoutIcon"]), dup))
        check(r["state"] in ("active", "running", "minimized"), "%s: indicator state %s" % (b, r["state"]))
        check(r["tap"] in ("activate", "minimize", "recent", "cycle"), "%s: a tap would %s (never launch)" % (b, r["tap"]))
    table(last_rows())

elif mode == "taps":
    a, b2, c = args[0], args[1], args[2]
    table(snap)
    w0 = snap["windows"]
    # 1. a running, not active window: tap -> activate
    ra = row_of(snap, a)
    if ra and ra["IsActive"] and not ra["IsMinimized"]:
        tap(snap, b2); snap = settle(); ra = row_of(snap, a)   # make it inactive first
    act = tap(snap, a); check(act in ("activate", "recent"), "%s (running, not active): tap -> %s" % (a, act))
    snap = wait_rows(lambda r: (row_of(r, a) or {}).get("IsActive") and not (row_of(r, a) or {}).get("IsMinimized"), 8) or settle()
    ra = row_of(snap, a); check(ra["IsActive"] and not ra["IsMinimized"], "%s is now the active window (state %s)" % (a, ra["state"]))
    check(snap["windows"] == w0, "window count unchanged by the tap (%d)" % snap["windows"])
    # 2. the active window: tap -> minimise
    act = tap(snap, a); check(act == "minimize", "%s (active): tap -> %s" % (a, act))
    snap = wait_rows(lambda r: (row_of(r, a) or {}).get("IsMinimized"), 8) or settle()
    ra = row_of(snap, a); check(ra["IsMinimized"], "%s is minimised" % a)
    check(ra["state"] == "minimized" and ra["dots"] >= 1, "%s keeps its marker while minimised (state %s, dots %d)" % (a, ra["state"], ra["dots"]))
    check((ra["IsWindow"] or ra["IsGroupParent"]) and snap["windows"] == w0, "minimised window is still a row, count %d" % snap["windows"])
    # 3. minimised: tap -> activate (restore)
    act = tap(snap, a); check(act in ("activate", "recent"), "%s (minimised): tap -> %s" % (a, act))
    snap = wait_rows(lambda r: (row_of(r, a) or {}).get("IsActive") and not (row_of(r, a) or {}).get("IsMinimized"), 8) or settle()
    ra = row_of(snap, a); check(ra["IsActive"] and not ra["IsMinimized"], "%s restored and active" % a)
    # 4. another running app: tap -> activate it, the first goes inactive but keeps its dot
    act = tap(snap, b2); check(act in ("activate", "recent"), "%s (running, not active): tap -> %s" % (b2, act))
    snap = wait_rows(lambda r: (row_of(r, b2) or {}).get("IsActive"), 8) or settle()
    check(row_of(snap, b2)["IsActive"] and not row_of(snap, a)["IsActive"] and row_of(snap, a)["state"] == "running", "%s active, %s running with its dot" % (b2, a))
    check(snap["windows"] == w0, "still %d windows: no tap opened a new one" % snap["windows"])
    # 5. a launcher without a window: tap -> launch; a tap while it starts is ignored; the window merges into the row
    rc = row_of(snap, c); check(rc is not None and rc["IsLauncher"] and not rc["IsWindow"], "%s is a bare launcher (state %s)" % (c, rc["state"] if rc else "?"))
    act = tap(snap, c); check(act == "launch", "%s (launcher): tap -> %s" % (c, act))
    s2 = wait_rows(lambda r: (row_of(r, c) or {}).get("IsStartup"), 4)
    if s2 is not None:
        act2 = tap(s2, c); check(act2 == "none", "%s while starting: tap -> %s (no second instance)" % (c, act2))
    else: print("INFO no startup row seen for %s (fast start or no startup notification); skipping the starting-tap check" % c)
    snap = wait_rows(lambda r: (row_of(r, c) or {}).get("IsWindow") or (row_of(r, c) or {}).get("IsGroupParent"), 60)
    check(snap is not None, "%s window appeared" % c)
    if snap is None: snap = last_rows()
    rc = row_of(snap, c)
    check(rc and rc["HasLauncher"] and (rc["IsWindow"] or rc["IsGroupParent"]), "%s: the window merged into the pinned row (HasLauncher, marker on the pinned icon)" % c)
    check(snap["windows"] == w0 + 1, "exactly one new window (%d -> %d)" % (w0, snap["windows"]))
    w1 = snap["windows"]
    # 6. middle click -> a second instance; the row becomes a group; a tap on the group with an active child cycles
    act = middle(snap, b2); check(act == "new", "%s middle click -> %s" % (b2, act))
    snap = wait_rows(lambda r: (row_of(r, b2) or {}).get("IsGroupParent") and (row_of(r, b2) or {}).get("ChildCount", 0) >= 2, 60)
    check(snap is not None, "%s is now a group of 2+ windows" % b2)
    if snap is None: snap = last_rows()
    rb = row_of(snap, b2)
    check(rb["dots"] == 2 and rb["state"] in ("active", "running"), "%s group: two dots (state %s)" % (b2, rb["state"]))
    check(snap["windows"] == w1 + 1, "middle click added exactly one window (%d -> %d)" % (w1, snap["windows"]))
    act = tap(snap, b2)
    check(act in ("cycle", "recent"), "%s group tap -> %s (activates a window of the group, never a new one)" % (b2, act))
    snap = settle(); check(snap["windows"] == w1 + 1, "group tap did not add a window (%d)" % snap["windows"])
    table(snap)

elif mode == "elsewhere":
    b = args[0]
    snap = wait_rows(lambda r: r.get("allWindows", 0) > r.get("windows", 0), 30)
    check(snap is not None, "the filtered model hides at least one window the unfiltered model sees (windows %s / all %s)" % ((snap or last_rows()).get("windows"), (snap or last_rows()).get("allWindows")))
    snap = snap or last_rows(); table(snap)
    r = row_of(snap, b)
    check(r is not None and r["IsLauncher"] and not r["IsWindow"], "%s shows as a bare launcher on this desktop" % b)
    check(r is not None and r.get("elsewhere") and r["elsewhere"]["windows"] >= 1, "%s: the unfiltered model finds its window elsewhere (%s)" % (b, json.dumps(r.get("elsewhere")) if r else None))
    check(r is not None and r["state"] in ("running", "minimized") and r["dots"] >= 1, "%s keeps its marker (state %s)" % (b, r["state"] if r else None))
    check(r is not None and r["tap"] == "elsewhere", "%s: a tap would activate the window elsewhere, not launch (tap=%s)" % (b, r["tap"] if r else None))
    all0 = snap["allWindows"]
    act = tap(snap, b); check(act == "elsewhere", "tap -> %s" % act)
    snap = wait_rows(lambda r: (row_of(r, b) or {}).get("IsWindow") or (row_of(r, b) or {}).get("IsGroupParent"), 10) or settle()
    r = row_of(snap, b)
    check(r["IsWindow"] or r["IsGroupParent"], "after the tap the window is on the current desktop (KWin switched): row is a %s" % ("window" if r["IsWindow"] else "group" if r["IsGroupParent"] else "launcher"))
    check(snap["allWindows"] == all0, "no new window anywhere (%d)" % snap["allWindows"])
    table(snap)

print("DRIVER DONE failures=%d" % failures)
sys.exit(1 if failures else 0)
