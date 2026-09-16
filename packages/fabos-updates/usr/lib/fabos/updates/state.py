#!/usr/bin/env python3
"""@DISTRO_NAME@ Updates — the over-the-air state machine shared by the Fab Updates app, the per-session notifier and the
root-side dpkg trigger. No Qt here: this file runs from systemd units and maintainer scripts.

Root side (dpkg trigger `fabos-postupgrade`, see DEBIAN/triggers and helper.sh post-upgrade):
  state.py record   compare the installed fabos-* versions with the last snapshot, append what changed to the journal
                    with the classes of follow-up it needs, refresh the snapshot, then poke every graphical session
  state.py poke     start fabos-update-notify.service in every graphical session (after `apt-get update` too)
  state.py snapshot write the versions file only (fresh install / image build: makes the first record exact)

User side (fabos-update-notify.service, a user unit started by the timer and by the pokes above):
  state.py session-check   restart the agent/voice user services when their code changed and they are idle, show one
                           notification per event ("update installed — log out and back in / restart to finish", or
                           "update available — open Fab Updates"), never touch plasmashell or the user's windows
  state.py state           print the pending state as JSON (the Fab Updates banner and the tests read this)

Files (root-written, world-readable) in /var/lib/fabos/updates:
  versions      "package version" per installed fabos-* package, as of the last record
  journal       one line per record that found a change: time= mono= boot= version= packages= classes=
  last-upgrade  the newest journal line (for people and tests)
Times are CLOCK_MONOTONIC seconds plus the kernel boot id, so "is this newer than my login / than the running agent /
than this boot" is answered against systemd's *TimestampMonotonic properties without parsing wall-clock strings."""
import glob, hashlib, json, os, pwd, subprocess, sys, time, urllib.request

STATE_DIR = os.environ.get("FABOS_UPDATES_STATE_DIR") or "/var/lib/fabos/updates"   # env: tests only (tests/updates-state-test.py)
JOURNAL = os.path.join(STATE_DIR, "journal")
VERSIONS = os.path.join(STATE_DIR, "versions")
LAST = os.path.join(STATE_DIR, "last-upgrade")
DISTRO = "@DISTRO_NAME@"
APP = "@DISTRO_NAME@ Updates"
# What each package needs after it changed on a running system. Everything else (fabos-updates, fabos-welcome,
# fabos-feedback, fabos-firstboot, fabos-ai, fabos-desktop-meta) applies at the next launch or is restarted by its own
# postinst (system services); no follow-up is advised for them.
CLASSES = {
    "fabos-desktop": {"session"},            # plasmoids, look-and-feel, KWin scripts, greeter theme, colour schemes
    "fabos-agent": {"session", "agent"},     # ask-bar plasmoid (plasmashell) + the per-user daemon (restarted here)
    "fabos-voice": {"voice"},                # the "Hey Fab" listener user service (restarted here)
    "fabos-branding": {"reboot"},            # Plymouth splash lives in the initramfs; visible after a restart
    "fabos-rounded-corners": {"session"},    # KWin effect plugin: loaded once per compositor lifetime
}
CLASS_ORDER = ("reboot", "session", "agent", "voice")
RUNUSER = os.environ.get("FABOS_UPDATES_RUNUSER") or ("/usr/sbin/runuser" if os.path.exists("/usr/sbin/runuser") else "runuser")   # env: tests only
RUN_USER = os.environ.get("FABOS_UPDATES_RUN_USER") or "/run/user"   # env: tests only (a fake session directory)


def mono_now():
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def boot_id():
    try:
        return open("/proc/sys/kernel/random/boot_id").read().strip()
    except OSError:
        return ""


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)
    except (OSError, subprocess.SubprocessError) as e:
        class R: returncode = 1; stdout = ""; stderr = str(e)
        return R()


def installed_versions():
    r = run(["dpkg-query", "-W", "-f=${Package} ${Version} ${db:Status-Status}\n", "fabos-*"])
    out = {}
    for line in r.stdout.splitlines():
        p = line.split()
        if len(p) == 3 and p[2] in ("installed", "unpacked", "half-configured", "triggers-pending", "triggers-awaited"):
            out[p[0]] = p[1]
    return out


def read_kv(line):
    d = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d[k] = v
    return d


def journal_entries():
    try:
        lines = open(JOURNAL).read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        d = read_kv(line)
        if "mono" in d:
            try:
                d["mono"] = float(d["mono"])
                d["time"] = float(d.get("time", "0"))
            except ValueError:
                continue
            d["packages"] = [p for p in d.get("packages", "").split(",") if p]
            d["classes"] = set(c for c in d.get("classes", "").split(",") if c)
            out.append(d)
    return out


def classes_for(packages):
    cls = set()
    for p in packages:
        cls |= CLASSES.get(p, set())
    return cls


# ---------------------------------------------------------------- root side
def write_versions(now):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(VERSIONS + ".tmp", "w") as f:
        for p in sorted(now):
            f.write("%s %s\n" % (p, now[p]))
    os.replace(VERSIONS + ".tmp", VERSIONS)
    os.chmod(VERSIONS, 0o644)


def snapshot(argv):
    now = installed_versions()
    write_versions(now)
    print("fabos-updates: snapshot of %d packages" % len(now))
    return 0


def record(argv):
    """Called from the dpkg trigger at the end of an apt run (and from fabos-updates' postinst). Never raises."""
    if not os.path.isdir("/run/systemd/system") and "--force" not in argv:
        return 0  # image build container: no sessions, nothing to advise
    os.makedirs(STATE_DIR, exist_ok=True)
    now = installed_versions()
    before = {}
    try:
        for line in open(VERSIONS):
            p = line.split()
            if len(p) == 2:
                before[p[0]] = p[1]
    except OSError:
        before = None  # first record on this system: every installed package counts as changed (1.0-6 -> 1.0-7 path)
    changed = sorted(p for p, v in now.items() if before is None or before.get(p) != v)
    write_versions(now)
    if not changed:
        print("fabos-updates: no fabos package changed; nothing to record")
        return 0
    cls = classes_for(changed)
    version = now.get("fabos-updates") or now.get("fabos-desktop") or max(now.values(), default="")
    line = "time=%d mono=%.3f boot=%s version=%s packages=%s classes=%s" % (
        time.time(), mono_now(), boot_id(), version, ",".join(changed), ",".join(c for c in CLASS_ORDER if c in cls))
    with open(JOURNAL, "a") as f:
        f.write(line + "\n")
    os.chmod(JOURNAL, 0o644)
    with open(LAST + ".tmp", "w") as f:
        f.write(line + "\n")
    os.replace(LAST + ".tmp", LAST)
    os.chmod(LAST, 0o644)
    print("fabos-updates: recorded " + line)
    poke(["--installed"])
    return 0


def graphical_sessions():
    """[(user, uid, runtime_dir)] for every user with a session bus (Plasma sessions have one; ssh-only logins too, harmless)."""
    out = []
    for d in glob.glob(os.path.join(RUN_USER, "*")):
        try:
            uid = int(os.path.basename(d))
        except ValueError:
            continue
        if uid == 0 or not os.path.exists(os.path.join(d, "bus")):
            continue
        try:
            user = pwd.getpwuid(uid).pw_name
        except KeyError:
            continue
        out.append((user, uid, d))
    return out


def as_user(user, rd, cmd, timeout=30):
    env = ["env", "XDG_RUNTIME_DIR=" + rd, "DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus" % rd]
    return run([RUNUSER, "-u", user, "--"] + env + cmd, timeout=timeout)


def poke(argv):
    """Root: make every graphical session run its notifier now. `systemctl --user daemon-reload` first so a session that
    was logged in before this package version sees the new unit; fall back to a plain notify-send bridge."""
    n = 0
    for user, uid, rd in graphical_sessions():
        as_user(user, rd, ["systemctl", "--user", "daemon-reload"])
        as_user(user, rd, ["systemctl", "--user", "start", "--no-block", "fabos-update-notify.timer"])
        r = as_user(user, rd, ["systemctl", "--user", "start", "--no-block", "fabos-update-notify.service"])
        if r.returncode == 0:
            print("fabos-updates: poked session of %s" % user)
            n += 1
            continue
        # no user manager for this bus (unusual): say it directly
        if "--installed" in argv:
            title, body = "%s updated" % DISTRO, "An update was installed. Open %s to finish." % APP
        else:
            title, body = "%s update available" % DISTRO, "Open %s to install it." % APP
        as_user(user, rd, ["notify-send", "-a", APP, "-i", "fabos-updates", title, body])
        print("fabos-updates: notified %s directly (%s)" % (user, (r.stderr or "").strip()[:80]))
    return 0


# ---------------------------------------------------------------- shared state
def user_unit_mono(unit):
    """ActiveEnterTimestampMonotonic of a user unit in seconds, or None when inactive/unknown."""
    r = run(["systemctl", "--user", "show", "-p", "ActiveState,ActiveEnterTimestampMonotonic", unit], timeout=15)
    d = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    if d.get("ActiveState") != "active":
        return None
    try:
        return int(d.get("ActiveEnterTimestampMonotonic", "0")) / 1e6
    except ValueError:
        return None


def kernel_state():
    running = os.uname().release
    newest = ""
    r = run(["linux-version", "list"], timeout=15)
    vers = [v for v in r.stdout.split() if v]
    if not vers:
        vers = [os.path.basename(p)[len("vmlinuz-"):] for p in glob.glob("/boot/vmlinuz-*")]
    if vers:
        # linux-version prints them sorted; the /boot fallback gets a version sort
        if not (r.returncode == 0 and r.stdout.strip()):
            vers.sort(key=lambda s: [(0, int(x)) if x.isdigit() else (1, x) for x in s.replace("-", ".").split(".")])
        newest = vers[-1]
    return {"running": running, "newest": newest, "restart": bool(newest) and newest != running}


def pending():
    """What the current user still has to do after updates: log out / restart, and whether the agent or voice user
    services still run pre-update code. Compared against this boot and this graphical session (monotonic clock)."""
    bid = boot_id()
    sess = user_unit_mono("graphical-session.target")
    agent = user_unit_mono("fabos-agent.service")
    voice = user_unit_mono("fabos-voiced.service")
    st = {"needs_restart": False, "needs_logout": False, "agent_restart": False, "voice_restart": False,
          "restart_reasons": [], "logout_reasons": [], "changed": [], "version": "", "entries": 0,
          "session_active": sess is not None, "kernel": kernel_state(), "boot_id": bid}
    for e in journal_entries():
        same_boot = e.get("boot") == bid
        if not same_boot:
            continue  # a restart since then finished everything that entry asked for
        st["entries"] += 1
        st["version"] = e.get("version", st["version"])
        for p in e["packages"]:
            if p not in st["changed"]:
                st["changed"].append(p)
        if "reboot" in e["classes"]:
            st["needs_restart"] = True
            reason = "boot screen: %s" % ", ".join(p for p in e["packages"] if "reboot" in CLASSES.get(p, ()))
            if reason not in st["restart_reasons"]:   # several records of one update (unattended-upgrades steps) say it once
                st["restart_reasons"].append(reason)
        if "session" in e["classes"] and (sess is None or e["mono"] > sess):
            st["needs_logout"] = True
            reason = ", ".join(p for p in e["packages"] if "session" in CLASSES.get(p, ()))
            if reason not in st["logout_reasons"]:
                st["logout_reasons"].append(reason)
        if "agent" in e["classes"] and agent is not None and e["mono"] > agent:
            st["agent_restart"] = True
        if "voice" in e["classes"] and voice is not None and e["mono"] > voice:
            st["voice_restart"] = True
    if st["kernel"]["restart"]:
        st["needs_restart"] = True
        st["restart_reasons"].append("kernel %s (running %s)" % (st["kernel"]["newest"], st["kernel"]["running"]))
    if os.path.exists("/run/reboot-required"):
        st["needs_restart"] = True
        st["restart_reasons"].append("system packages")
    return st


def upgradable():
    """Unprivileged read of the apt lists that root refreshed (apt-daily.timer / fabos-update-check.timer)."""
    r = run(["apt", "list", "--upgradable"], timeout=60, env=dict(os.environ, LC_ALL="C"))
    lines = [l for l in r.stdout.splitlines() if "/" in l and "upgradable" in l]
    fab = [l.split("/")[0] for l in lines if l.startswith("fabos-")]
    ver = ""
    for l in lines:
        if l.startswith("fabos-"):
            parts = l.split()
            if len(parts) >= 2:
                ver = parts[1]
                break
    return {"total": len(lines), "fabos": fab, "fabos_version": ver, "lines": lines}


def auto_enabled():
    try:
        return 'Unattended-Upgrade "1"' in open("/etc/apt/apt.conf.d/20auto-upgrades").read()
    except OSError:
        return False


def current_channel():
    try:
        for line in open("/etc/apt/sources.list.d/fabos.sources"):
            if line.startswith("Suites:"):
                return "beta" if "beta" in line else "stable"
    except OSError:
        pass
    return "stable"


def summary_text(st):
    """The banner / notification wording for a pending state; ("", "") when nothing is pending."""
    v = (" %s" % st["version"]) if st.get("version") else ""
    if st["needs_restart"]:
        if st.get("entries"):
            return ("%s updated" % DISTRO, "%s%s is installed. Restart to finish." % (DISTRO, v))
        return ("Update installed", "A system update is installed. Restart to finish.")
    if st["needs_logout"]:
        return ("%s updated" % DISTRO, "%s%s is installed. Log out and back in to finish." % (DISTRO, v))
    return ("", "")


# ---------------------------------------------------------------- user side
def agent_busy():
    rd = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    try:
        token = open(os.path.join(rd, "fabos-agent", "token")).read().strip()
        port = open(os.path.join(rd, "fabos-agent", "port")).read().strip()
        req = urllib.request.Request("http://127.0.0.1:%s/status" % port, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=5) as r:
            t = json.loads(r.read()).get("tasks", {})
        return any(t.get(k) for k in ("running", "waiting_approval", "waiting_user", "queued"))
    except Exception:
        return False  # not running or not answering: a restart cannot interrupt anything


NOTIFY_BACKEND = os.environ.get("FABOS_UPDATES_NOTIFY", "dbus")   # "notify-send" forces the fallback (tests shim it)
URGENCY = {"low": 0, "normal": 1, "critical": 2}


def notify_dbus(title, body, actions, urgency, wait):
    """org.freedesktop.Notifications.Notify on the session bus, then wait (bounded) for ActionInvoked / NotificationClosed.
    Done directly because libnotify 0.8's notify-send drops the buttons on Plasma 6 ("Actions are not supported by this
    notifications server", although GetCapabilities lists actions). Returns the pressed action id or ''."""
    import dbus, dbus.mainloop.glib
    from gi.repository import GLib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    iface = dbus.Interface(bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications"), "org.freedesktop.Notifications")
    acts = []
    for aid, label in actions:
        acts += [aid, label]
    hints = {"urgency": dbus.Byte(URGENCY.get(urgency, 1)), "desktop-entry": "fabos-updates", "category": "device"}
    nid = int(iface.Notify(APP, dbus.UInt32(0), "fabos-updates", title, body, acts, hints, dbus.Int32(-1)))
    result = {"action": "", "closed": False}
    loop = GLib.MainLoop()

    def on_action(i, key):
        if int(i) == nid:
            result["action"] = str(key)
            loop.quit()

    def on_closed(i, reason):
        if int(i) == nid:
            result["closed"] = True
            loop.quit()
    bus.add_signal_receiver(on_action, "ActionInvoked", "org.freedesktop.Notifications")
    bus.add_signal_receiver(on_closed, "NotificationClosed", "org.freedesktop.Notifications")
    if actions:
        GLib.timeout_add_seconds(int(wait), loop.quit)
        loop.run()
    result["id"] = nid
    return result


def notify(title, body, actions=(), urgency="normal", wait=900):
    """Show a notification in this session and return the pressed action id or ''. urgency "critical" = Plasma keeps the
    popup on screen until the user answers (the "finish your update" case, so someone who was away still sees it);
    "normal" popups time out and go to the history. D-Bus directly (notify_dbus); notify-send as the fallback."""
    if NOTIFY_BACKEND != "notify-send":
        try:
            return notify_dbus(title, body, actions, urgency, wait)["action"]
        except Exception as e:  # no python3-dbus / no session bus / no server: fall through to notify-send
            print(time.strftime("%H:%M:%S"), "notify: dbus path failed (%s); using notify-send" % str(e)[:120], flush=True)
    cmd = ["notify-send", "-a", APP, "-i", "fabos-updates", "-u", urgency]
    for aid, label in actions:
        cmd += ["-A", "%s=%s" % (aid, label)]
    cmd += [title, body]
    r = run(["timeout", str(wait)] + cmd, timeout=wait + 10)
    err = (r.stderr or "").strip()
    if r.returncode != 0 or err:
        print(time.strftime("%H:%M:%S"), "notify-send: rc=%s %s" % (r.returncode, err[:200]), flush=True)
    return (r.stdout or "").strip()


def notify_probe(argv):
    """tests/ota-local-vm.sh: show the real 'finish it' popup with its buttons for a few seconds, then close it, and print
    what the server did (id, closed) so the run can assert the D-Bus path works in a live session."""
    hold = int(argv[0]) if argv else 6
    import dbus
    from gi.repository import GLib
    t0 = time.time()
    r = notify_dbus("%s updated (probe)" % DISTRO, "%s 1.0-7 is installed. Restart to finish. (test popup, closes itself)" % DISTRO,
                    (("open", "Open %s" % APP), ("later", "Later")), "critical", 0)
    time.sleep(hold)
    bus = dbus.SessionBus()
    dbus.Interface(bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications"), "org.freedesktop.Notifications").CloseNotification(dbus.UInt32(r["id"]))
    print(json.dumps({"id": r["id"], "shown_for_s": round(time.time() - t0, 1), "buttons": ["Open %s" % APP, "Later"], "closed_by": "probe"}))
    return 0


def open_app(args=()):
    """Launch Fab Updates outside this oneshot unit's cgroup so it survives the unit finishing."""
    r = run(["systemd-run", "--user", "--quiet", "--collect", "--", "fabos-updates"] + list(args), timeout=20)
    if r.returncode != 0:
        subprocess.Popen(["fabos-updates"] + list(args), start_new_session=True)


def session_check(argv):
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(state_home, "fabos")
    os.makedirs(d, exist_ok=True)
    memo_path = os.path.join(d, "updates-notify.json")
    try:
        memo = json.load(open(memo_path))
    except (OSError, ValueError):
        memo = {}
    if memo.get("boot") != boot_id():
        memo = {"boot": boot_id(), "seen": {}}
    seen = memo.setdefault("seen", {})
    log = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)

    def remember(key):
        seen[key] = int(time.time())
        with open(memo_path, "w") as f:
            json.dump(memo, f)

    st = pending()
    # 1. services that run pre-update code: restart them now if idle (a running task is never interrupted)
    if st["agent_restart"]:
        if agent_busy():
            log("agent: code updated but a task is running; will retry on the next check")
        else:
            r = run(["systemctl", "--user", "try-restart", "fabos-agent.service"], timeout=60)
            log("agent: try-restart ->", r.returncode, (r.stderr or "").strip()[:120])
    if st["voice_restart"]:
        r = run(["systemctl", "--user", "try-restart", "fabos-voiced.service"], timeout=60)
        log("voice: try-restart ->", r.returncode, (r.stderr or "").strip()[:120])
    # 2. "installed — finish it": once per boot for a given version and answer (restart / log out), only in a graphical
    #    session. Keyed on the answer, not on the number of journal lines: unattended-upgrades installs in minimal steps
    #    (one dpkg run per package, in its own order), so one Fab OS update can produce several records — fabos-updates' own
    #    step (the first one that knows the trigger) and one per later package with files under /usr/lib/fabos. The user
    #    hears "restart to finish" once; only an escalation (log out -> restart) or a new version speaks again.
    if st["session_active"] and (st["needs_restart"] or st["needs_logout"]):
        key = "installed:%s:%s" % (st["version"], "restart" if st["needs_restart"] else "logout")
        if key not in seen:
            title, body = summary_text(st)
            remember(key)
            log("notify:", title, "|", body)
            if notify(title, body, actions=(("open", "Open %s" % APP), ("later", "Later")), urgency="critical") == "open":
                open_app()
    # 3. "available — open Fab Updates": once per offered version per boot
    if st["session_active"] and "--no-available" not in argv:
        up = upgradable()
        if up["fabos"]:
            key = "available:" + hashlib.sha1(" ".join(sorted(up["lines"])).encode()).hexdigest()[:12]
            if key not in seen:
                remember(key)
                body = "%s %s is ready to install — open %s." % (DISTRO, up["fabos_version"], APP)
                if auto_enabled():
                    body += " It also installs by itself in the background."
                log("notify: available", up["fabos_version"], "(%d packages)" % len(up["fabos"]))
                if notify("%s update available" % DISTRO, body, actions=(("open", "Open %s" % APP),)) == "open":
                    open_app(["--check"])
    log("done:", json.dumps({k: st[k] for k in ("needs_restart", "needs_logout", "agent_restart", "voice_restart", "version", "entries")}))
    return 0


def main(argv):
    cmd = argv[0] if argv else "state"
    if cmd == "record":
        return record(argv[1:])
    if cmd == "poke":
        return poke(argv[1:])
    if cmd == "snapshot":
        return snapshot(argv[1:])
    if cmd == "session-check":
        return session_check(argv[1:])
    if cmd == "notify-probe":
        return notify_probe(argv[1:])
    if cmd == "state":
        st = pending()
        st["upgradable"] = upgradable() if "--no-apt" not in argv else None
        st["auto_updates"] = auto_enabled()
        st["channel"] = current_channel()
        st["title"], st["banner"] = summary_text(st)
        print(json.dumps(st, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
        return 0
    print("usage: state.py record|poke|snapshot|session-check|state|notify-probe [seconds]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # maintainer-script and unit callers must never fail because of this file
        print("fabos-updates state.py: %s" % e, file=sys.stderr)
        sys.exit(0)
