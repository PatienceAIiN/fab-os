#!/usr/bin/env python3
# Fab OS — the graded ladder's L1/L2 tasks against a RUNNING fabos-agentd that talks to the built-in local model.
# Copyright (c) 2026 Patience AI. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Measure what the small-model driver really does: the ladder's own task texts (parsed out of tests/agent-ladder-vm.sh so
they cannot drift), the ladder's own checks (tests/ladder/checks.py imported as a module, answer key from
tests/ladder/expected.json), a fresh ~/Ladder, PASS/FAIL per task decided by the checker — never by what the agent says.

Plus four HELD-OUT tasks (level "h", HELDOUT below) of the same shapes as the L2 tasks whose idioms appear as worked examples in
the driver's executor prompt (a CSV column sum, a rename by extension, a largest/smallest file, a file count) — a different column
name, extension, tree and folder, generated here and named in no prompt, so the table has rows the prompt cannot have memorised.

Runs anywhere a daemon (FABOS_AGENT_PROVIDER=local) and a llama-server /v1 endpoint are reachable; the intended
place is the Fab OS image, driven by tests/local-driver-image.sh (which also starts both processes and a wtype shim, since a
container has no Wayland seat). Standard library only.

  python3 tests/local-driver-test.py --home /tmp/ldr/home --label after [--levels 1,2,h] [--only l1-a] [--driver stepwise|freeform|default]
      [--daemon http://127.0.0.1:8790] [--token-file $XDG_RUNTIME_DIR/fabos-agent/token] [--base-url http://127.0.0.1:8081/v1]
      [--server-pid PID] [--out build/local-driver-after.json] [--timeout-scale 1.0]

Exit 0 when every selected non-optional task passed, 1 otherwise, 3 on a setup problem.
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LADDER_SH = os.path.join(ROOT, "tests", "agent-ladder-vm.sh")
LADDER_DIR = os.path.join(ROOT, "tests", "ladder")
TIMEOUTS = {1: 120, 2: 240, "h": 240}          # T1 / T2 in tests/agent-ladder-vm.sh; held-out tasks get T2
ANSWER = "yes, go ahead"

# ---------------------------------------------------------------- held-out tasks: L2 shapes, different names — in no prompt, not in the ladder
HELDOUT_FIX = "/tmp/heldout"
HELDOUT = [
    ("h-a", "The files /tmp/heldout/stock-a.csv and /tmp/heldout/stock-b.csv each have a column called qty. Add the qty column up across both files "
            "and write only the total, as a plain integer with no separators, into ~/Ladder/qty-total.txt"),
    ("h-b", "Rename every file that ends in .log inside ~/Ladder/logs-copy so it ends in .bak instead. Keep the base names and the file contents unchanged."),
    ("h-c", "Find the single smallest file anywhere under /tmp/heldout/tree and write just its file name (the base name, no directory path) into ~/Ladder/smallest.txt"),
    ("h-d", "Count how many files are in the folder /tmp/heldout/logs (regular files only). Do not create or change any file. "
            "End your reply with a line in exactly this form: FILE COUNT: <number>"),
]
HELDOUT_WHAT = {"h-a": "held-out: sum the qty column of two CSVs into ~/Ladder/qty-total.txt", "h-b": "held-out: rename every .log in ~/Ladder/logs-copy to .bak (a .md file stays)",
                "h-c": "held-out: smallest file under /tmp/heldout/tree, base name into ~/Ladder/smallest.txt", "h-d": "held-out: count the files in /tmp/heldout/logs, answer FILE COUNT: n"}


def prepare_heldout():
    """Deterministic fixtures under /tmp/heldout (seeded, byte-for-byte reproducible); returns the answer key."""
    import csv
    import hashlib
    import random
    rnd = random.Random(20260915)
    shutil.rmtree(HELDOUT_FIX, ignore_errors=True)
    os.makedirs(os.path.join(HELDOUT_FIX, "logs"))
    key = {"qty_total": 0, "logs": [], "logs_sha": {}, "smallest": None, "tree_files": []}
    for name, nrows in (("stock-a.csv", 23), ("stock-b.csv", 31)):
        with open(os.path.join(HELDOUT_FIX, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["sku", "warehouse", "qty"], lineterminator="\n")
            w.writeheader()
            for i in range(nrows):
                q = rnd.randrange(3, 480)
                key["qty_total"] += q
                w.writerow({"sku": "SKU-%04d" % (1000 + i * 7), "warehouse": ("north", "south", "east")[i % 3], "qty": q})
    for i in range(1, 6):
        body = "".join("%s line %d of app-%02d: request %d served in %d ms\n" % ("2026-03-%02d" % (10 + i), j, i, 100 * i + j, rnd.randrange(3, 900)) for j in range(1, 6 + i))
        p = os.path.join(HELDOUT_FIX, "logs", "app-%02d.log" % i)
        with open(p, "w") as f:
            f.write(body)
        key["logs"].append("app-%02d.log" % i)
        key["logs_sha"]["app-%02d.log" % i] = hashlib.sha256(body.encode()).hexdigest()
    with open(os.path.join(HELDOUT_FIX, "logs", "notes.md"), "w") as f:              # must survive the rename untouched
        f.write("# Notes\nThese logs come from the staging box.\n")
    key["logs"].append("notes.md")
    key["logs_sha"]["notes.md"] = hashlib.sha256(open(os.path.join(HELDOUT_FIX, "logs", "notes.md"), "rb").read()).hexdigest()
    tree = {"readme.txt": 120, "a/big.bin": 4096, "a/b/mid.txt": 300, "c/tiny.cfg": 11, "c/d/data.json": 900}         # tiny.cfg is the unique smallest
    for rel, size in tree.items():
        p = os.path.join(HELDOUT_FIX, "tree", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(bytes((i * 7 + len(rel)) % 251 for i in range(size)))
        key["tree_files"].append(os.path.basename(rel))
    key["smallest"] = "tiny.cfg"
    return key


def check_heldout(name, key, home, task, numbers):
    """(ok, evidence) for a held-out task — decided from the disk and the answer key, never from the agent's words (except the FILE COUNT line h-d asks for)."""
    ladder = os.path.join(home, "Ladder")

    def read(p):
        try:
            with open(p, errors="replace") as f:
                return f.read()
        except OSError as e:
            return None if isinstance(e, FileNotFoundError) else ""
    if name == "h-a":
        text = read(os.path.join(ladder, "qty-total.txt"))
        if text is None:
            return False, "~/Ladder/qty-total.txt was not created"
        if str(key["qty_total"]) not in numbers(text):
            return False, "qty-total.txt %r does not contain the exact total %d" % (" ".join(text.split())[:120], key["qty_total"])
        return True, "qty-total.txt contains the exact total %d (recomputed from the two CSVs)" % key["qty_total"]
    if name == "h-b":
        d = os.path.join(ladder, "logs-copy")
        if not os.path.isdir(d):
            return False, "~/Ladder/logs-copy is gone"
        got = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))
        want = sorted([n[:-4] + ".bak" for n in key["logs"] if n.endswith(".log")] + ["notes.md"])
        left = [n for n in got if n.endswith(".log")]
        if left:
            return False, "%d .log files are still there: %s" % (len(left), left[:6])
        if got != want:
            return False, "renamed set is %s, expected %s (notes.md must stay)" % (got[:12], want[:12])
        import hashlib
        for n in got:
            src = n[:-4] + ".log" if n.endswith(".bak") else n
            if hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest() != key["logs_sha"][src]:
                return False, "the content of %s changed" % n
        return True, "all %d logs renamed to .bak, 0 .log left, notes.md untouched, contents unchanged" % (len(got) - 1)
    if name == "h-c":
        text = read(os.path.join(ladder, "smallest.txt"))
        if text is None:
            return False, "~/Ladder/smallest.txt was not created"
        mentioned = sorted({n for n in key["tree_files"] if n in text})
        if mentioned != [key["smallest"]]:
            return False, "smallest.txt names %s, expected only %s (content: %r)" % (mentioned or "nothing", key["smallest"], " ".join(text.split())[:120])
        return True, "smallest.txt names exactly %s (11 bytes, smallest of %d files)" % (key["smallest"], len(key["tree_files"]))
    if name == "h-d":
        real = sorted(n for n in os.listdir(os.path.join(HELDOUT_FIX, "logs")) if os.path.isfile(os.path.join(HELDOUT_FIX, "logs", n)))
        if real != sorted(key["logs"]):
            return False, "/tmp/heldout/logs was changed: %s" % real
        said = task.get("result") or ""
        if not re.search(r"FILE COUNT:\s*%d([^0-9]|$)" % len(real), said):
            return False, "agent text has no 'FILE COUNT: %d' (said: %s)" % (len(real), " ".join(said[:160].split()))
        return True, "agent answered 'FILE COUNT: %d'; /tmp/heldout/logs really contains %d files and is unchanged" % (len(real), len(real))
    return False, "no such held-out task"


def die(msg, code=3):
    print("SETUP FAILED: " + msg, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- the ladder's own task texts
def ladder_tasks(expected, daemon_url):
    """{name: (level, text)} for every `run_task lN-x $TN auto "..."` line of the ladder script, variables substituted."""
    subs = {"$HELLO": expected["hello_text"], "$SENTENCE": expected["typed_sentence"], "$MAIL_TO": os.environ.get("MAIL_TO", "friend@example.com")}
    out = {}
    with open(LADDER_SH, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'^\s*run_task\s+(l[12]-[a-f])\s+\$T(\d)\s+auto\s+"(.*)"\s*$', line)
            if not m:
                continue
            text = m.group(3)
            for k, v in subs.items():
                text = text.replace(k, v)
            text = text.replace("http://127.0.0.1:8790/", daemon_url.rstrip("/") + "/")     # L2-e fetches the daemon's own /health
            out[m.group(1)] = (int(m.group(2)), text)
    for want in ("l1-a", "l1-b", "l1-c", "l1-d", "l1-e", "l1-f", "l2-a", "l2-b", "l2-c", "l2-d", "l2-e", "l2-f"):
        if want not in out:
            die("could not find the task text for %s in %s" % (want, LADDER_SH))
    return out


# ---------------------------------------------------------------- fixtures + checker
def prepare_fixtures(home):
    stage = os.path.join(home, ".ladder-stage")
    shutil.rmtree(stage, ignore_errors=True)
    r = subprocess.run([sys.executable, os.path.join(LADDER_DIR, "gen_fixtures.py"), "--out", stage], capture_output=True, text=True)
    if r.returncode != 0:
        die("gen_fixtures.py failed: " + r.stderr)
    with open(os.path.join(stage, "expected.json")) as f:
        exp = json.load(f)
    with open(os.path.join(LADDER_DIR, "expected.json")) as f:
        committed = json.load(f)
    if exp != committed:
        die("tests/ladder/expected.json does not match what gen_fixtures.py produces now (fixture drift)")
    fix = exp["fixture_dir_in_vm"]                       # /tmp/ladder: the task texts name this path
    shutil.rmtree(fix, ignore_errors=True)
    shutil.copytree(os.path.join(stage, "ladder"), fix)
    shutil.rmtree(os.path.join(home, "Ladder"), ignore_errors=True)
    os.makedirs(os.path.join(home, "Ladder"))               # the ladder's own "5/5 fresh scratch space" step: mkdir -p ~/Ladder before the tasks
    return exp, fix, os.path.join(stage, "expected.json")


def load_checks(expected_path, home):
    os.environ["LADDER_EXPECTED"] = expected_path
    os.environ["HOME"] = home                            # checks.py resolves ~ at import time
    sys.path.insert(0, LADDER_DIR)
    import checks                                        # noqa: E402  (tests/ladder/checks.py)
    return checks


def run_check(checks, name):
    """(ok, evidence) — the checker prints one line and exits 0 on success; anything else is a FAIL with its reason."""
    fn = checks.CHECKS.get(name)
    if not fn:
        return False, "no such check " + name
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        try:
            fn()
        except SystemExit as e:
            code = int(e.code or 0)
        except Exception as e:                           # a crash inside the checker is a FAIL, never a PASS
            code = 1
            buf.write("checker crashed: %s: %s" % (type(e).__name__, e))
    out = " ".join(buf.getvalue().split())
    return code == 0 and bool(out), out[:300]


def pgrep(pattern, exact=False):
    r = subprocess.run(["pgrep", "-a"] + (["-x"] if exact else []) + [pattern], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[:2]


def step_order(task, *want):
    names = [s.get("name") for s in task.get("steps") or [] if s.get("kind") == "tool_call" and (s.get("decision") or "") not in ("denied", "expired")]
    pos = []
    for t in want:
        if t not in names:
            return False, "tool %s never ran (sequence: %s)" % (t, " > ".join(names) or "none")
        pos.append(names.index(t))
    if pos != sorted(pos):
        return False, "wrong order: wanted %s, sequence: %s" % (" then ".join(want), " > ".join(names))
    return True, "order ok: %s (sequence: %s)" % (" then ".join(want), " > ".join(names))


# ---------------------------------------------------------------- daemon API
class Daemon:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                d = json.loads(raw)
            except ValueError:
                d = {"error": raw}
            d["http"] = e.code
            return d

    def run_task(self, text, timeout_s, log):
        t0 = time.time()
        r = self.call("POST", "/tasks", {"request": text, "mode": "auto", "title": "local-driver " + text[:40]})
        tid = r.get("id")
        if not tid:
            return {"status": "create_failed", "error": json.dumps(r)[:300], "seconds": 0, "steps": [], "approved": 0}
        log("    task #%d created" % tid)
        approved = 0
        status = "running"
        while True:
            t = self.call("GET", "/tasks/%d" % tid)
            status = t.get("status") or "?"
            if status in ("done", "failed", "cancelled"):
                break
            for a in self.call("GET", "/approvals/pending?task_id=%d" % tid) or []:
                if isinstance(a, dict) and str(a.get("task_id")) == str(tid):
                    self.call("POST", "/approvals/%d" % a["id"], {"decision": "approved"})
                    approved += 1
                    log("    approval #%s %s %s -> approved: %s" % (a["id"], a.get("risk"), a.get("tool"), " ".join((a.get("input") or "")[:120].split())))
            qs = [q for q in t.get("questions") or [] if not q.get("answer")]
            if qs:
                log("    agent asked: %s -> answering: %s" % (qs[-1]["question"][:160].replace("\n", " "), ANSWER))
                self.call("POST", "/tasks/%d/answer" % tid, {"text": ANSWER})
            if time.time() - t0 > timeout_s:
                log("    TIMEOUT after %ds — cancelling" % timeout_s)
                self.call("POST", "/tasks/%d/cancel" % tid)
                status = "timeout"
                time.sleep(2)
                t = self.call("GET", "/tasks/%d" % tid)
                break
            time.sleep(2)
        t["status"] = status
        t["seconds"] = round(time.time() - t0, 1)
        t["approved"] = approved
        return t


def tool_counts(task):
    c = {}
    for s in task.get("steps") or []:
        if s.get("kind") == "tool_call":
            c[s.get("name") or "?"] = c.get(s.get("name") or "?", 0) + 1
    return " ".join("%s:%d" % kv for kv in sorted(c.items())) or "none"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", required=True, help="the HOME the daemon runs with (fixtures and ~/Ladder live there)")
    ap.add_argument("--label", default="run")
    ap.add_argument("--levels", default="1,2,h", help="ladder levels 1 and/or 2, plus h for the held-out tasks")
    ap.add_argument("--only", help="one task or a comma list, e.g. l1-d,l2-e")
    ap.add_argument("--driver", default="default", choices=["default", "stepwise", "freeform"], help="sets agent.driver on the daemon before the run")
    ap.add_argument("--daemon", default="http://127.0.0.1:%s" % os.environ.get("FABOS_AGENT_PORT", "8790"))
    ap.add_argument("--token-file", default=os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent", "token"))
    ap.add_argument("--base-url", default="http://127.0.0.1:8081/v1", help="the llama-server endpoint the daemon's local provider should use")
    ap.add_argument("--server-pid", type=int, default=0, help="llama-server pid: its VmHWM is recorded at the end")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "local-driver-%s.json"))
    ap.add_argument("--timeout-scale", type=float, default=1.0)
    a = ap.parse_args()
    out_path = a.out % a.label if "%s" in a.out else a.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    levels = {x.strip() if x.strip() == "h" else int(x) for x in a.levels.split(",") if x.strip()}

    try:
        with open(a.token_file) as f:
            token = f.read().strip()
    except OSError as e:
        die("cannot read the daemon token: %s" % e)
    d = Daemon(a.daemon, token)
    st = d.call("GET", "/status")
    if st.get("http"):
        die("daemon /status: %s" % st)
    # the daemon talks to the llama-server we were told about; drivers: stepwise (small-model), freeform (the cloud loop), default (provider decides)
    settings = {"local.base_url": a.base_url}
    settings["agent.driver"] = "" if a.driver == "default" else a.driver
    r = d.call("PUT", "/settings", settings)
    if r.get("http"):
        die("PUT /settings failed: %s" % r)
    try:
        with urllib.request.urlopen(a.base_url.rstrip("/") + "/models", timeout=10) as m:
            models = json.loads(m.read())
    except Exception as e:
        die("llama-server not reachable at %s: %s" % (a.base_url, e))
    st = d.call("GET", "/status")
    print("== %s: daemon %s provider=%s driver=%s network=%s sandbox=%s model=%s" % (
        a.label, a.daemon, st.get("provider"), (st.get("driver") or "n/a"), json.dumps(st.get("network")), st.get("sandbox"),
        ",".join(x.get("id", "?") for x in models.get("data", [])[:2])))

    expected, fix, expected_path = prepare_fixtures(a.home)
    checks = load_checks(expected_path, a.home)
    ok, ev = run_check(checks, "fixtures")
    print("   fixtures: %s" % ev)
    if not ok:
        die("fixtures not intact")
    tasks = ladder_tasks(expected, a.daemon)
    notes_n = expected["notes"]["count"]
    heldout_key = prepare_heldout() if "h" in levels else None

    rows = []
    log = lambda s: print(s, flush=True)   # noqa: E731

    def verdict(name, level, status, evidence, t, note=""):
        rows.append({"task": name, "level": level, "status": status, "evidence": evidence[:600], "seconds": t.get("seconds", 0), "steps": len(t.get("steps") or []),
                     "tools": tool_counts(t), "task_status": t.get("status"), "approved": t.get("approved", 0),
                     "result": " ".join(((t.get("result") or t.get("error") or ""))[:400].split()), "note": note})
        print(">>> %s: %s — %s" % (status, name, evidence[:300]), flush=True)

    for name in ("l1-a", "l1-b", "l1-c", "l1-d", "l1-e", "l1-f", "l2-a", "l2-b", "l2-c", "l2-d", "l2-e", "l2-f"):
        level, text = tasks[name]
        if level not in levels or (a.only and name not in a.only.split(",")):
            continue
        if name == "l2-f" and not (os.environ.get("MAIL_TO") and os.environ.get("MAIL_APP_PASSWORD")):
            rows.append({"task": name, "level": level, "status": "SKIP", "evidence": "optional: needs MAIL_ADDRESS + MAIL_APP_PASSWORD + MAIL_TO (the user's own mail account)",
                         "seconds": 0, "steps": 0, "tools": "none", "task_status": "", "approved": 0, "result": "", "note": "optional"})
            print(">>> SKIP: l2-f — optional mail task (no credentials)", flush=True)
            continue
        if name == "l1-c":
            subprocess.run(["pkill", "-x", "konsole"], capture_output=True)
            time.sleep(1)
        if name == "l2-b" and not os.path.isdir(os.path.join(a.home, "Ladder", "notes-copy")):
            # the ladder's own precondition line: `test -d ~/Ladder/notes-copy || cp -r /tmp/ladder/notes ~/Ladder/notes-copy`
            shutil.copytree(os.path.join(fix, "notes"), os.path.join(a.home, "Ladder", "notes-copy"))
            print("    precondition: seeded ~/Ladder/notes-copy (l1-e did not leave one), as tests/agent-ladder-vm.sh does", flush=True)
        print("\n=== %s [auto · local · %s]: %s" % (name, a.label, text), flush=True)
        t = d.run_task(text, int(TIMEOUTS[level] * a.timeout_scale), log)
        print("    -> status=%s in %ss steps=%d tools=[%s] approvals=+%d" % (t["status"], t["seconds"], len(t.get("steps") or []), tool_counts(t), t["approved"]), flush=True)
        for s in t.get("steps") or []:                      # what the model actually did, so a FAIL can be diagnosed from the log alone
            if s.get("kind") in ("tool_call", "verify", "assistant") or (s.get("kind") == "error"):
                body = s.get("input") if s.get("kind") == "tool_call" else s.get("output")
                print("       %-9s %-16s %s" % (s.get("kind"), s.get("name") or "", " ".join(str(body or "")[:220].split())), flush=True)
        print("    result: %s" % " ".join(((t.get("result") or t.get("error") or ""))[:400].split()), flush=True)
        if name == "l1-a":
            ok, ev = run_check(checks, "l1a")
        elif name == "l1-b":
            ok, ev = run_check(checks, "notes-count")
            if ok:
                said = t.get("result") or ""
                if re.search(r"FILE COUNT:\s*%d([^0-9]|$)" % notes_n, said):
                    ev = "agent answered 'FILE COUNT: %d'; %s" % (notes_n, ev)
                else:
                    ok, ev = False, "agent text has no 'FILE COUNT: %d' (said: %s); %s" % (notes_n, " ".join(said[:160].split()), ev)
        elif name == "l1-c":
            procs = pgrep("konsole", exact=True)
            ok, ev = bool(procs), "terminal running: %s" % ("|".join(procs) or "no konsole process")
        elif name == "l1-d":
            ok, ev = run_check(checks, "l1d")
        elif name == "l1-e":
            ok, ev = run_check(checks, "l1e")
        elif name == "l1-f":
            ok, ev = step_order(t, "open_app", "type_text")
            if ok:
                procs = pgrep("kate")
                ok = bool(procs)
                ev += " | " + ("a Fab Editor window is running: " + "|".join(procs) if procs else "no kate process")
        elif name == "l2-a":
            ok, ev = run_check(checks, "l2a")
        elif name == "l2-b":
            ok, ev = run_check(checks, "l2b")
        elif name == "l2-c":
            ok, ev = run_check(checks, "l2c")
        elif name == "l2-d":
            ok, ev = run_check(checks, "l2d")
            if ok:
                tools = tool_counts(t)
                if "open_app" in tools or "type_text" in tools:
                    ev += " | tools=" + tools
                else:
                    ok, ev = False, "file is right but the editor was never opened (tools=%s) | %s" % (tools, ev)
        elif name == "l2-e":
            ok, ev = run_check(checks, "l2e")
        else:
            ok, ev = False, "not graded here"
        verdict(name, level, "PASS" if ok else "FAIL", ev if ok else ev + " | task=" + str(t["status"]), t)

    for name, text in HELDOUT if "h" in levels else []:
        if a.only and name not in a.only.split(","):
            continue
        if name == "h-b" and not os.path.isdir(os.path.join(a.home, "Ladder", "logs-copy")):
            shutil.copytree(os.path.join(HELDOUT_FIX, "logs"), os.path.join(a.home, "Ladder", "logs-copy"))          # the same precondition shape as l2-b
            print("    precondition: seeded ~/Ladder/logs-copy from /tmp/heldout/logs", flush=True)
        print("\n=== %s [auto · local · %s · held-out]: %s" % (name, a.label, text), flush=True)
        t = d.run_task(text, int(TIMEOUTS["h"] * a.timeout_scale), log)
        print("    -> status=%s in %ss steps=%d tools=[%s] approvals=+%d" % (t["status"], t["seconds"], len(t.get("steps") or []), tool_counts(t), t["approved"]), flush=True)
        for st_ in t.get("steps") or []:
            if st_.get("kind") in ("tool_call", "verify", "assistant", "error"):
                body = st_.get("input") if st_.get("kind") == "tool_call" else st_.get("output")
                print("       %-9s %-16s %s" % (st_.get("kind"), st_.get("name") or "", " ".join(str(body or "")[:220].split())), flush=True)
        print("    result: %s" % " ".join(((t.get("result") or t.get("error") or ""))[:400].split()), flush=True)
        ok, ev = check_heldout(name, heldout_key, a.home, t, checks.numbers)
        verdict(name, "h", "PASS" if ok else "FAIL", ev if ok else ev + " | task=" + str(t["status"]), t, note="held-out")

    rss_kb = 0
    if a.server_pid:
        try:
            with open("/proc/%d/status" % a.server_pid) as f:
                rss_kb = int([ln for ln in f if ln.startswith("VmHWM:")][0].split()[1])
        except (OSError, IndexError, ValueError):
            pass
    summary = {}
    for lv in sorted(levels, key=str):
        core = [r for r in rows if r["level"] == lv and r["status"] != "SKIP"]
        summary["h" if lv == "h" else "l%d" % lv] = "%d/%d" % (sum(1 for r in core if r["status"] == "PASS"), len(core))
    report = {"label": a.label, "driver": a.driver, "status": st, "server_vmhwm_kb": rss_kb, "levels": sorted(levels, key=str), "tasks": rows, "summary": summary,
              "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model_ids": [x.get("id") for x in models.get("data", [])]}
    with open(out_path, "w") as f:
        json.dump(report, f, indent=1)
    print("\n### %s: %s%s — %s" % (a.label, " ".join(("held-out %s" % v) if k == "h" else "L%s %s" % (k[1:], v) for k, v in sorted(summary.items())),
                                    (" · llama-server VmHWM %.2f GB" % (rss_kb / 1e6)) if rss_kb else "", out_path))
    failed = [r for r in rows if r["status"] == "FAIL"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
