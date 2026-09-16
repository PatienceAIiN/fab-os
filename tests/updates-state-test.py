#!/usr/bin/env python3
"""Unit test of the over-the-air state machine packages/fabos-updates/usr/lib/fabos/updates/state.py (docs/UPDATES.md).
Runs on the host without root, Qt or a VM: dpkg-query, systemctl, apt, linux-version, notify-send, runuser and
systemd-run are replaced by shims on PATH; the state directory, the fake /run/user tree and XDG_STATE_HOME live in a
temporary directory. Every check prints PASS/FAIL; exit 1 when anything failed.
  tests/updates-state-test.py [--out DIR]     (DIR gets the shim call logs; default build/)"""
import importlib.util, json, os, shutil, stat, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PY = os.path.join(HERE, "packages/fabos-updates/usr/lib/fabos/updates/state.py")
OUT = HERE + "/build"
if "--out" in sys.argv:
    OUT = sys.argv[sys.argv.index("--out") + 1]
os.makedirs(OUT, exist_ok=True)
T = tempfile.mkdtemp(prefix="fabos-updates-test.")
BIN, STATE, RUNUSER_DIR, XSTATE, LOG = (os.path.join(T, d) for d in ("bin", "state", "run-user", "xdg-state", "calls.log"))
for d in (BIN, STATE, RUNUSER_DIR, XSTATE):
    os.makedirs(d)
FAKE = os.path.join(T, "fake")  # the shims read their answers from files under here
os.makedirs(FAKE)
fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  [" + str(detail)[:200] + "]") if (detail and not cond) else ""))
    if not cond:
        fails.append(name)


def shim(name, body):
    p = os.path.join(BIN, name)
    with open(p, "w") as f:
        f.write("#!/bin/sh\nprintf '%s\\n' \"" + name + " $*\" >> \"$FABOS_TEST_LOG\"\n" + body + "\n")
    os.chmod(p, 0o755)


# ---- shims. Answers come from files so the test can change them between steps without rewriting the scripts.
shim("dpkg-query", 'cat "$FABOS_TEST_FAKE/dpkg"')
shim("systemctl", r'''
case "$*" in
  *"show -p ActiveState,ActiveEnterTimestampMonotonic "*)
    unit="${@: -1}"; f="$FABOS_TEST_FAKE/unit-$unit"
    if [ -f "$f" ]; then printf 'ActiveState=active\nActiveEnterTimestampMonotonic=%s\n' "$(cat "$f")"; else printf 'ActiveState=inactive\nActiveEnterTimestampMonotonic=0\n'; fi ;;
  *) exit 0 ;;
esac''')
shim("linux-version", 'cat "$FABOS_TEST_FAKE/kernels"')
shim("apt", 'cat "$FABOS_TEST_FAKE/apt"')
shim("notify-send", 'cat "$FABOS_TEST_FAKE/click" 2>/dev/null; exit 0')
shim("runuser", 'exit 0')
shim("systemd-run", 'exit 0')
shim("timeout", 'shift; exec "$@"')
shim("fabos-updates", 'exit 0')

env = dict(os.environ, PATH=BIN + ":" + os.environ["PATH"], FABOS_UPDATES_STATE_DIR=STATE, FABOS_UPDATES_RUN_USER=RUNUSER_DIR,
           FABOS_UPDATES_RUNUSER=os.path.join(BIN, "runuser"), FABOS_UPDATES_NOTIFY="notify-send",  # never the host's session bus
           XDG_STATE_HOME=XSTATE, XDG_RUNTIME_DIR=os.path.join(T, "xdg-runtime"), FABOS_TEST_FAKE=FAKE, FABOS_TEST_LOG=LOG)
os.makedirs(env["XDG_RUNTIME_DIR"])
for k, v in env.items():
    os.environ[k] = v
spec = importlib.util.spec_from_file_location("state", STATE_PY)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)

PKGS = ["fabos-agent", "fabos-ai", "fabos-branding", "fabos-desktop", "fabos-desktop-meta", "fabos-feedback", "fabos-firstboot",
        "fabos-updates", "fabos-voice", "fabos-welcome"]


def set_dpkg(versions):
    with open(os.path.join(FAKE, "dpkg"), "w") as f:
        for p, v in versions.items():
            f.write("%s %s installed\n" % (p, v))


def set_unit(unit, mono_seconds):
    p = os.path.join(FAKE, "unit-" + unit)
    if mono_seconds is None:
        if os.path.exists(p):
            os.remove(p)
    else:
        open(p, "w").write(str(int(mono_seconds * 1e6)))


def calls():
    try:
        return open(LOG).read()
    except OSError:
        return ""


def clear_calls():
    open(LOG, "w").close()


def notifies(c):
    """notify-send invocations only (the timeout shim logs the same argv once more)."""
    return [l for l in c.splitlines() if l.startswith("notify-send ")]


running = os.uname().release
open(os.path.join(FAKE, "kernels"), "w").write(running + "\n")
open(os.path.join(FAKE, "apt"), "w").write("Listing...\n")
mono0 = state.mono_now()

# ---- 1. snapshot (fresh install / image build) ---------------------------------------------------------------
set_dpkg({p: "1.0-6" for p in PKGS})
state.snapshot([])
vers = open(state.VERSIONS).read().split("\n")
check("snapshot writes one line per installed fabos package", len([l for l in vers if l]) == len(PKGS), vers)
check("snapshot is world-readable (the user side reads it)", stat.S_IMODE(os.stat(state.VERSIONS).st_mode) == 0o644)

# ---- 2. record with nothing changed: no journal --------------------------------------------------------------
state.record(["--force"])
check("record with unchanged versions writes no journal", not os.path.exists(state.JOURNAL))

# ---- 3. the 1.0-6 -> 1.0-7 run: every package changes; a fake graphical session gets poked ------------------------
sess_dir = os.path.join(RUNUSER_DIR, str(os.getuid()))
os.makedirs(sess_dir)
open(os.path.join(sess_dir, "bus"), "w").close()
clear_calls()
set_dpkg({p: "1.0-7" for p in PKGS})
state.record(["--force"])
entries = state.journal_entries()
check("record appends one journal entry", len(entries) == 1, entries)
e = entries[0]
check("entry lists every changed package", sorted(e["packages"]) == sorted(PKGS), e["packages"])
check("entry classes: reboot (branding), session (desktop/agent), agent, voice", e["classes"] == {"reboot", "session", "agent", "voice"}, e["classes"])
check("entry carries the new version and this boot id", e.get("version") == "1.0-7" and e.get("boot") == state.boot_id(), e)
check("last-upgrade mirrors the newest entry", open(state.LAST).read().strip() == open(state.JOURNAL).read().strip().splitlines()[-1])
c = calls()
check("record pokes the session: systemctl --user daemon-reload + start fabos-update-notify.service via runuser",
      "daemon-reload" in c and "start --no-block fabos-update-notify.service" in c and c.count("runuser") >= 2, c)

# ---- 4. pending(): a session and an agent that started BEFORE the record -----------------------------------------
set_unit("graphical-session.target", mono0 - 500)
set_unit("fabos-agent.service", mono0 - 400)
set_unit("fabos-voiced.service", None)
st = state.pending()
check("needs_restart (fabos-branding changed) with a 'boot screen' reason", st["needs_restart"] and any("boot screen" in r for r in st["restart_reasons"]), st)
check("needs_logout (session older than the update)", st["needs_logout"] and st["logout_reasons"], st)
check("agent_restart (daemon older than the update); no voice restart (not running)", st["agent_restart"] and not st["voice_restart"], st)
title, text = state.summary_text(st)
check("banner text: 'Restart to finish' names the version", "1.0-7" in text and text.endswith("Restart to finish."), (title, text))
check("banner title mentions the distro name placeholder/name", title.endswith("updated"), title)

# ---- 5. the agent restarted later, the user logged out and back in: only the restart remains ---------------------
set_unit("graphical-session.target", state.mono_now() + 10)
set_unit("fabos-agent.service", state.mono_now() + 10)
st = state.pending()
check("after a new login: needs_logout clears, agent_restart clears, needs_restart stays", not st["needs_logout"] and not st["agent_restart"] and st["needs_restart"], st)

# ---- 6. a reboot (different boot id): nothing pending -----------------------------------------------------------
real_boot_id = state.boot_id
state.boot_id = lambda: "00000000-0000-0000-0000-000000000000"
st = state.pending()
check("after a reboot: nothing pending, no entries counted", not st["needs_restart"] and not st["needs_logout"] and st["entries"] == 0, st)
check("banner empty when nothing is pending", state.summary_text(st) == ("", ""))
state.boot_id = real_boot_id

# ---- 7. a desktop-only update: session class only -> 'Log out and back in' -----------------------------------------
set_unit("graphical-session.target", mono0 - 500)
set_unit("fabos-agent.service", state.mono_now() + 10)
# start from a clean journal so the earlier reboot-class entry does not mask this case
os.remove(state.JOURNAL)
v = {p: "1.0-7" for p in PKGS}; v["fabos-desktop"] = "1.0-8"
set_dpkg(v)
state.record(["--force"])
e = state.journal_entries()[-1]
check("desktop-only record: packages=[fabos-desktop], classes={session}", e["packages"] == ["fabos-desktop"] and e["classes"] == {"session"}, e)
st = state.pending()
title, text = state.summary_text(st)
check("desktop-only: banner says 'Log out and back in to finish.'", st["needs_logout"] and not st["needs_restart"] and text.endswith("Log out and back in to finish."), (st, text))

# ---- 8. a new kernel waiting -> restart, even with no Fab OS entry -------------------------------------------------
open(os.path.join(FAKE, "kernels"), "w").write("%s\n%s\n" % (running, running + "-newer"))
st = state.pending()
check("newer installed kernel -> needs_restart with a kernel reason", st["needs_restart"] and any("kernel" in r for r in st["restart_reasons"]), st)
open(os.path.join(FAKE, "kernels"), "w").write(running + "\n")

# ---- 9. session-check: one notification per event, agent restarted when idle ---------------------------------------
os.remove(state.JOURNAL)
set_dpkg({p: "1.0-9" for p in PKGS})
state.record(["--force"])
set_unit("graphical-session.target", mono0 - 500)
set_unit("fabos-agent.service", mono0 - 400)
set_unit("fabos-voiced.service", mono0 - 300)
open(os.path.join(FAKE, "click"), "w").write("")  # the notification is dismissed without pressing the button
clear_calls()
state.session_check([])
c = calls()
check("session-check restarts the idle agent and voice services (try-restart, never restart/plasmashell)",
      "try-restart fabos-agent.service" in c and "try-restart fabos-voiced.service" in c and "plasmashell" not in c, c)
check("session-check shows exactly one 'installed' notification with an Open button",
      len(notifies(c)) == 1 and "Restart to finish" in c and "-A open=Open" in c and "-A later=Later" in c and "-u critical" in notifies(c)[0], c)
clear_calls()
state.session_check([])
check("second session-check in the same boot: no repeated notification", "notify-send" not in calls(), calls())
memo = json.load(open(os.path.join(XSTATE, "fabos", "updates-notify.json")))
check("memo remembers the event under this boot id", memo.get("boot") == state.boot_id() and any(k.startswith("installed:") for k in memo.get("seen", {})), memo)

# ---- 10. 'update available' notification once per offered version; button opens the app ------------------------------
open(os.path.join(FAKE, "apt"), "w").write("Listing...\nfabos-desktop/loom 1.0-10 all [upgradable from: 1.0-9]\nfabos-agent/loom 1.0-10 all [upgradable from: 1.0-9]\n")
open(os.path.join(FAKE, "click"), "w").write("open\n")
clear_calls()
state.session_check([])
c = calls()
check("available: one notification naming the offered version, app opened with --check on the button",
      len(notifies(c)) == 1 and "1.0-10" in c and "systemd-run --user --quiet --collect -- fabos-updates --check" in c, c)
clear_calls()
state.session_check([])
check("available: not repeated for the same offer", "notify-send" not in calls(), calls())
up = state.upgradable()
check("upgradable() parses apt list output", up["total"] == 2 and up["fabos"] == ["fabos-desktop", "fabos-agent"] and up["fabos_version"] == "1.0-10", up)

# ---- 11. the JSON the banner and the VM test read ----------------------------------------------------------------
r = subprocess.run([sys.executable, STATE_PY, "state", "--no-apt"], capture_output=True, text=True, env=env)
try:
    j = json.loads(r.stdout)
except ValueError:
    j = {}
check("`state.py state` prints JSON with banner/title/needs_* keys", r.returncode == 0 and all(k in j for k in ("banner", "title", "needs_restart", "needs_logout", "changed", "version")), r.stdout[:300] + r.stderr[:300])
check("JSON banner matches summary_text", j.get("banner", "") == state.summary_text(state.pending())[1], j.get("banner"))

# ---- 12. robustness: a broken journal line is skipped; main() never raises ---------------------------------------------
with open(state.JOURNAL, "a") as f:
    f.write("garbage line without keys\nmono=notanumber packages=x\n")
check("journal parser skips malformed lines", len(state.journal_entries()) == 1)
r = subprocess.run([sys.executable, STATE_PY, "no-such-command"], capture_output=True, text=True, env=env)
check("unknown subcommand: usage on stderr, exit 2", r.returncode == 2 and "usage" in r.stderr)

shutil.copy(LOG, os.path.join(OUT, "updates-state-test.calls.log")) if os.path.exists(LOG) else None
shutil.rmtree(T, ignore_errors=True)
print("UPDATES STATE TEST: %s (%d failed)" % ("PASS" if not fails else "FAIL", len(fails)))
sys.exit(1 if fails else 0)
