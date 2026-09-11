#!/usr/bin/env python3
"""fabos-feedback-relay — system service that delivers Fab OS feedback / bug reports to Patience AI.

Runs as root via systemd socket activation on /run/fabos/feedback.sock so the mail credentials in
/etc/fabos/feedback.env (root-only) are never readable by user processes. One JSON object per connection:
  {"type": "bug"|"feedback"|"error", "subject": str, "message": str, "email": str|"", "system": {...}}
Reply: {"ok": true, "via": "brevo-api"|"smtp"} or {"ok": false, "error": "..."}.
Delivery: Brevo transactional API (BREVO_API_KEY) or SMTP (SMTP_HOST/PORT/USER/PASS). Accepts the same variable
names as Patience AI's website .env so that file can be dropped in unchanged.
"""
import json, os, socket, sys, smtplib, ssl, urllib.request, urllib.error, time
from email.message import EmailMessage

ENV_PATH = os.environ.get("FABOS_FEEDBACK_ENV", "/etc/fabos/feedback.env")
SOCK_PATH = os.environ.get("FABOS_FEEDBACK_SOCK", "/run/fabos/feedback.sock")
BREVO_URL = os.environ.get("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load_env():
    cfg = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def deliver(report, cfg):
    to = cfg.get("FEEDBACK_TO", "info@patienceai.in")
    sender_email = cfg.get("FEEDBACK_SENDER_EMAIL") or cfg.get("BREVO_SENDER_EMAIL") or cfg.get("SMTP_FROM") or cfg.get("SMTP_USER")
    sender_name = cfg.get("FEEDBACK_SENDER_NAME") or cfg.get("BREVO_SENDER_NAME") or "Fab OS"
    kind = report.get("type", "feedback")
    subject = "[Fab OS %s] %s" % (kind, (report.get("subject") or report.get("message", "")[:60] or "no subject").strip())
    sysinfo = report.get("system") or {}
    body = "%s\n\n--- reporter ---\nemail: %s\n\n--- system ---\n%s\n" % (report.get("message", ""), report.get("email") or "(not given)",
                                                                          "\n".join("%s: %s" % (k, v) for k, v in sysinfo.items()))
    if not sender_email:
        raise RuntimeError("feedback channel not configured on this system (no sender in %s)" % ENV_PATH)
    if cfg.get("BREVO_API_KEY"):
        payload = {"sender": {"email": sender_email, "name": sender_name}, "to": [{"email": to}], "subject": subject, "textContent": body,
                   "tags": ["fabos", kind]}
        if report.get("email") and "@" in report["email"]:
            payload["replyTo"] = {"email": report["email"]}
        req = urllib.request.Request(BREVO_URL, data=json.dumps(payload).encode(), method="POST",
                                     headers={"api-key": cfg["BREVO_API_KEY"], "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read() or b"{}")
            return {"ok": True, "via": "brevo-api", "message_id": resp.get("messageId")}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if not cfg.get("SMTP_HOST"):
                raise RuntimeError("Brevo API error %s: %s" % (e.code, detail))
            log("brevo api failed (%s), falling back to smtp: %s" % (e.code, detail))
    if cfg.get("SMTP_HOST"):
        msg = EmailMessage()
        msg["From"] = "%s <%s>" % (sender_name, sender_email)
        msg["To"] = to
        msg["Subject"] = subject
        if report.get("email") and "@" in report["email"]:
            msg["Reply-To"] = report["email"]
        msg.set_content(body)
        port = int(cfg.get("SMTP_PORT") or 587)
        if cfg.get("SMTP_SECURE", "false").lower() == "true" or port == 465:
            srv = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=30, context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30)
            srv.starttls(context=ssl.create_default_context())
        with srv:
            if cfg.get("SMTP_USER"):
                srv.login(cfg["SMTP_USER"], cfg.get("SMTP_PASS", ""))
            srv.send_message(msg)
        return {"ok": True, "via": "smtp"}
    raise RuntimeError("feedback channel not configured (no BREVO_API_KEY or SMTP_HOST in %s)" % ENV_PATH)


def handle(conn):
    conn.settimeout(20)
    data = b""
    while len(data) < 200_000:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
        if data.rstrip().endswith(b"}"):
            try:
                json.loads(data)
                break
            except ValueError:
                continue
    try:
        report = json.loads(data or b"{}")
        if not report.get("message", "").strip():
            raise RuntimeError("empty report")
        result = deliver(report, load_env())
        log("delivered %s report via %s" % (report.get("type"), result.get("via")))
    except Exception as e:
        result = {"ok": False, "error": str(e)}
        log("delivery failed:", e)
    try:
        conn.sendall(json.dumps(result).encode())
    finally:
        conn.close()


def main():
    if os.environ.get("LISTEN_FDS"):
        srv = socket.socket(fileno=3)  # systemd socket activation
    else:
        os.makedirs(os.path.dirname(SOCK_PATH), exist_ok=True)
        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK_PATH)
        os.chmod(SOCK_PATH, 0o666)
        srv.listen(8)
    log("fabos-feedback-relay ready (env=%s)" % ENV_PATH)
    idle_exit = time.time() + 600
    srv.settimeout(30)
    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            if os.environ.get("LISTEN_FDS") and time.time() > idle_exit:
                return  # let systemd re-activate on demand
            continue
        idle_exit = time.time() + 600
        handle(conn)


if __name__ == "__main__":
    main()
