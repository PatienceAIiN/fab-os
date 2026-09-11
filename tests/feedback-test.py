#!/usr/bin/env python3
"""Tests the feedback relay + client end to end against a fake Brevo endpoint (no real mail sent)."""
import json, os, subprocess, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY = os.path.join(ROOT, "packages/fabos-feedback/usr/lib/fabos/feedback/fabos_feedback_relay.py")
CLIENT = os.path.join(ROOT, "packages/fabos-feedback/usr/lib/fabos/feedback/fabos_feedback.py")
received = []


class FakeBrevo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        received.append({"headers": dict(self.headers), "body": body})
        code = 201 if self.headers.get("api-key") == "test-key" else 401
        out = json.dumps({"messageId": "<fake@brevo>"} if code == 201 else {"message": "Key not found"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class Feedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fabos-feedback-")
        cls.http = HTTPServer(("127.0.0.1", 0), FakeBrevo)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.envfile = os.path.join(cls.tmp, "feedback.env")
        with open(cls.envfile, "w") as f:
            f.write('BREVO_API_KEY=test-key\nBREVO_SENDER_EMAIL=support@example.com\nBREVO_SENDER_NAME="Patience AI"\nFEEDBACK_TO=info@patienceai.in\n')
        cls.sock = os.path.join(cls.tmp, "feedback.sock")
        cls.env = dict(os.environ, FABOS_FEEDBACK_ENV=cls.envfile, FABOS_FEEDBACK_SOCK=cls.sock, BREVO_API_URL="http://127.0.0.1:%d/v3/smtp/email" % cls.http.server_port)
        cls.proc = subprocess.Popen([sys.executable, RELAY], env=cls.env, stderr=subprocess.PIPE, text=True)
        for _ in range(50):
            if os.path.exists(cls.sock):
                break
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.http.shutdown()

    def client(self, *args):
        return subprocess.run([sys.executable, CLIENT, "--cli"] + list(args), env=self.env, capture_output=True, text=True, timeout=30)

    def test_bug_report_reaches_brevo_with_system_info(self):
        r = self.client("--type", "bug", "--subject", "Kate did not open", "--message", "Agent said it opened kate but nothing appeared", "--email", "tester@example.com")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"]) and self.assertEqual(out["via"], "brevo-api")
        body = received[-1]["body"]
        self.assertEqual(body["to"], [{"email": "info@patienceai.in"}])
        self.assertEqual(body["sender"]["email"], "support@example.com")
        self.assertTrue(body["subject"].startswith("[Fab OS bug] Kate did not open"))
        self.assertIn("kernel:", body["textContent"])
        self.assertEqual(body["replyTo"]["email"], "tester@example.com")

    def test_empty_message_rejected(self):
        r = self.client("--type", "feedback", "--message", "   ")
        self.assertEqual(r.returncode, 1)
        self.assertIn("empty report", r.stdout)

    def test_unconfigured_channel_fails_cleanly(self):
        with open(self.envfile, "w") as f:
            f.write("# nothing configured\n")
        try:
            r = self.client("--type", "feedback", "--message", "hello")
            self.assertEqual(r.returncode, 1)
            self.assertIn("not configured", r.stdout)
        finally:
            with open(self.envfile, "w") as f:
                f.write('BREVO_API_KEY=test-key\nBREVO_SENDER_EMAIL=support@example.com\n')


if __name__ == "__main__":
    unittest.main(verbosity=2)
