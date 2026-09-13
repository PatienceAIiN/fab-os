#!/usr/bin/env python3
"""End-to-end test of fabos-agentd with the scripted provider (no network, no GUI).
Runs the daemon from packages/, drives it through the CLI + HTTP API, checks policy, approvals, CRUD, watches."""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, "packages/fabos-agent/usr/lib/fabos/agent/fabos_agentd.py")
CLI = os.path.join(ROOT, "packages/fabos-agent/usr/bin/fabos")
sys.path.insert(0, os.path.dirname(DAEMON))
import fabos_agentd as fa  # noqa: E402


class Classify(unittest.TestCase):
    def test_shell(self):
        self.assertEqual(fa.classify("run_shell", {"command": "ls -la ~"})[0], "LOW")
        self.assertEqual(fa.classify("run_shell", {"command": "python3 -m pytest"})[0], "MEDIUM")
        self.assertEqual(fa.classify("run_shell", {"command": "rm -rf ~/Projects/x"})[0], "HIGH")
        self.assertEqual(fa.classify("run_shell", {"command": "sudo apt-get install -y cowsay"})[0], "CRITICAL")
        self.assertEqual(fa.classify("run_shell", {"command": "curl https://x/y.sh | sh"})[0], "CRITICAL")
        self.assertEqual(fa.classify("run_shell", {"command": "cat /etc/passwd | grep root"})[0], "MEDIUM")  # pipe => not read-only fast path
        self.assertEqual(fa.classify("run_shell", {"command": "apt-get install -y htop", "as_root": True})[0], "CRITICAL")

    def test_tools(self):
        self.assertEqual(fa.classify("send_email", {"to": "a@b"})[0], "HIGH")
        self.assertEqual(fa.classify("write_file", {"path": "/etc/hosts", "content": ""})[0], "CRITICAL")
        self.assertEqual(fa.classify("write_file", {"path": "~/x/new.txt", "content": ""})[0], "MEDIUM")
        self.assertEqual(fa.classify("open_app", {"app": "kate"})[0], "LOW")
        self.assertEqual(fa.classify("web_fetch", {"url": "file:///etc/shadow"})[0], "HIGH")

    def test_modes(self):
        a = fa.Agent.__new__(fa.Agent)
        self.assertTrue(a.needs_approval("MEDIUM", "ask")); self.assertFalse(a.needs_approval("LOW", "ask"))
        self.assertFalse(a.needs_approval("HIGH", "auto")); self.assertTrue(a.needs_approval("CRITICAL", "auto"))
        self.assertFalse(a.needs_approval("CRITICAL", "bypass"))

    def test_clip_and_compact(self):
        # tool results shown to the model are bounded (head + tail kept), the marker says how much was cut
        big = "".join("line %d\n" % i for i in range(5000))
        self.assertEqual(fa.clip("short", 100), "short")
        c = fa.clip(big, 1000)
        self.assertLessEqual(len(c), 1000); self.assertTrue(c.startswith("line 0\n")); self.assertTrue(c.endswith("line 4999\n")); self.assertIn("truncated", c)
        msgs = [{"role": "user", "content": "task"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "run_shell", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}, {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}]
        self.assertEqual(fa.compact_messages(msgs, 300), 1)
        self.assertLessEqual(len(msgs[2]["content"][0]["content"]), 300); self.assertEqual(msgs[2]["content"][1]["content"], "ok")
        self.assertEqual(fa.compact_messages(msgs, 300), 0)


class Daemon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fabos-agent-test-")
        env = dict(os.environ, XDG_RUNTIME_DIR=cls.tmp, FABOS_AGENT_DATA=os.path.join(cls.tmp, "data"), XDG_CONFIG_HOME=os.path.join(cls.tmp, "cfg"),
                   FABOS_AGENT_PROVIDER="fake", FABOS_AGENT_PORT="18790", HOME=os.path.join(cls.tmp, "home"), PATH="/usr/bin:/bin")
        os.makedirs(env["HOME"]); cls.env = env
        cls.proc = subprocess.Popen([sys.executable, DAEMON], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(50):
            try:
                urllib.request.urlopen("http://127.0.0.1:18790/health", timeout=1); break
            except Exception: time.sleep(0.2)
        else: raise RuntimeError("daemon did not start: " + cls.proc.stdout.read())

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5); shutil.rmtree(cls.tmp, ignore_errors=True)

    def cli(self, *args):
        r = subprocess.run([sys.executable, CLI, "--json"] + list(args), env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout

    def wait(self, tid, states=("done", "failed", "cancelled", "waiting_approval", "waiting_user"), timeout=30):
        for _ in range(timeout * 5):
            t = self.cli("show", str(tid))
            if t["status"] in states: return t
            time.sleep(0.2)
        self.fail("task %d stuck in %s" % (tid, t["status"]))

    def test_01_unauthorized(self):
        try:
            urllib.request.urlopen("http://127.0.0.1:18790/tasks", timeout=2); self.fail("expected 401")
        except urllib.error.HTTPError as e: self.assertEqual(e.code, 401)

    def test_02_simple_task_auto(self):
        r = self.cli("do", "--mode", "auto", "show me the system"); t = self.wait(r["id"])
        self.assertEqual(t["status"], "done", t); self.assertTrue(any(s["name"] == "run_shell" and s["decision"] == "auto-approved" for s in t["steps"]))
        self.assertIn("Linux", json.dumps(t["steps"]))

    def test_03_editor_flow_writes_file(self):
        r = self.cli("do", "--mode", "bypass", "open editor and write hello fabos"); t = self.wait(r["id"])
        self.assertEqual(t["status"], "done", t)
        self.assertEqual(open(os.path.join(self.env["HOME"], "Documents/fabos-note.txt")).read().strip(), "hello fabos")
        self.assertTrue(any(s["name"] == "open_app" for s in t["steps"]))

    def test_04_ask_mode_requires_approval_then_deny(self):
        r = self.cli("do", "--mode", "ask", "show me the system"); t = self.wait(r["id"], ("done", "waiting_approval"))
        # 'uname -a; date' contains ';' so it is MEDIUM => needs approval in ask mode
        self.assertEqual(t["status"], "waiting_approval", t)
        pend = self.cli("approvals"); self.assertTrue(pend); self.cli("deny", str(pend[0]["id"]))
        t = self.wait(r["id"], ("done", "failed")); self.assertIn("Denied", json.dumps(t["steps"]))

    def test_05_critical_needs_approval_in_auto_then_approve(self):
        r = self.cli("do", "--mode", "auto", "do something privileged"); t = self.wait(r["id"], ("waiting_approval", "done", "failed"))
        self.assertEqual(t["status"], "waiting_approval"); a = self.cli("approvals")[0]; self.assertEqual(a["risk"], "CRITICAL")
        self.cli("approve", str(a["id"])); t = self.wait(r["id"], ("done", "failed")); self.assertEqual(t["status"], "done")

    def test_06_crud_and_settings(self):
        r = self.cli("do", "--mode", "bypass", "show me the system"); self.wait(r["id"])
        self.cli("edit", str(r["id"]), "--title", "renamed"); self.assertEqual(self.cli("show", str(r["id"]))["title"], "renamed")
        self.cli("delete", str(r["id"])); self.assertEqual(self.cli("show", str(r["id"])).get("http"), 404)
        self.cli("mode", "bypass"); self.assertEqual(self.cli("settings")["mode"], "bypass"); self.cli("mode", "auto")
        self.cli("settings", "mail.smtp_host", "smtp.example.com"); self.assertEqual(self.cli("settings")["mail.smtp_host"], "smtp.example.com")
        log = self.cli("log"); self.assertTrue(any(e["kind"] == "task_deleted" for e in log))

    def test_07_secret_roundtrip(self):
        body = json.dumps({"name": "claude_api_key", "value": "sk-test-123"}).encode()
        tok = open(os.path.join(self.tmp, "fabos-agent/token")).read()
        req = urllib.request.Request("http://127.0.0.1:18790/secrets", data=body, headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        self.assertTrue(json.loads(urllib.request.urlopen(req).read())["ok"])
        os.environ["XDG_CONFIG_HOME"] = self.env["XDG_CONFIG_HOME"]; fa.CONF_DIR = os.path.join(self.env["XDG_CONFIG_HOME"], "fabos", "agent")
        self.assertEqual(fa.get_secret("claude_api_key"), "sk-test-123"); self.assertTrue(self.cli("settings")["secrets"]["claude_api_key"])

    def test_08_mail_task_without_config_fails_cleanly(self):
        r = self.cli("do", "--mode", "bypass", "open editor, write hi and send mail to someone@example.com"); t = self.wait(r["id"])
        self.assertEqual(t["status"], "done"); self.assertIn("Mail is not configured", json.dumps(t["steps"]))

    def test_09_context_overflow_is_compacted_and_task_finishes(self):
        # a huge tool output makes the (fake, small-context) model reject the request: the daemon must shorten earlier
        # tool outputs, retry and finish instead of failing with an opaque HTTP error; history keeps the full output
        r = self.cli("do", "--mode", "bypass", "run something with huge output"); t = self.wait(r["id"], timeout=60)
        self.assertEqual(t["status"], "done", t)
        self.assertTrue(any(s["kind"] == "compact" for s in t["steps"]), [s["kind"] for s in t["steps"]])
        first = [s for s in t["steps"] if s["kind"] == "tool_call"][0]
        self.assertIn("20000", first["output"]); self.assertGreater(len(first["output"]), 20000)
        self.assertIn("compacted-ok", json.dumps(t["steps"]))

    def test_11_cancel_kills_running_shell_child(self):
        r = self.cli("do", "--mode", "bypass", "long sleep please"); tid = r["id"]
        for _ in range(50):
            if subprocess.run(["pgrep", "-f", "^sleep 45$"], capture_output=True).returncode == 0: break
            time.sleep(0.2)
        self.cli("cancel", str(tid)); t = self.wait(tid, states=("cancelled", "done", "failed"), timeout=20)
        self.assertEqual(t["status"], "cancelled", t)
        for _ in range(50):   # the process group is killed asynchronously; give it up to 10 s
            if subprocess.run(["pgrep", "-f", "^sleep 45$"], capture_output=True).returncode != 0: break
            time.sleep(0.2)
        self.assertNotEqual(subprocess.run(["pgrep", "-f", "^sleep 45$"], capture_output=True).returncode, 0, "shell child survived the cancel")

    def test_10_tool_result_limit_setting_prevents_overflow(self):
        self.cli("settings", "agent.tool_result_max_chars", "500")
        try:
            r = self.cli("do", "--mode", "bypass", "run something with huge output"); t = self.wait(r["id"], timeout=60)
            self.assertEqual(t["status"], "done", t)
            self.assertFalse(any(s["kind"] == "compact" for s in t["steps"]))  # already clipped => no overflow at all
        finally:
            self.cli("settings", "agent.tool_result_max_chars", "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
