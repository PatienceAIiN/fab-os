#!/usr/bin/env python3
"""fabric-agentd — the Fab OS autonomous agent service (per-user systemd service).

AI proposes. Deterministic policy verifies (risk classes + permission mode + approvals).
The OS executes. Everything is recorded (tasks, steps, approvals, watches, activity) in SQLite.

HTTP API on 127.0.0.1:8790 (bearer token in $XDG_RUNTIME_DIR/fabric-agent/token, 0600):
  GET  /health /status /settings /tasks /tasks/{id} /approvals/pending /watches /activity
  POST /tasks {request,title?,mode?}   POST /tasks/{id}/cancel|retry|answer   PATCH/DELETE /tasks/{id}
  POST /approvals/{id} {decision}      PUT  /settings {key: value,...}         POST /secrets {name,value}
  DELETE /watches/{id}
Providers: Claude (Anthropic SDK) or any OpenAI-compatible chat endpoint (e.g. llama-server).
FABRIC_AGENT_PROVIDER=fake runs a scripted provider for tests.
"""
import json, os, re, shlex, sqlite3, subprocess, sys, threading, time, uuid, urllib.request, urllib.error
import smtplib, imaplib, email, email.utils, email.header, datetime as _dt
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone

APP = "Fab OS"
PORT = int(os.environ.get("FABRIC_AGENT_PORT", "8790"))
HOME = os.path.expanduser("~")
DATA_DIR = os.environ.get("FABRIC_AGENT_DATA", os.path.join(os.environ.get("XDG_DATA_HOME", HOME + "/.local/share"), "fabric"))
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "fabric", "agent")
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabric-agent")
DB_PATH = os.path.join(DATA_DIR, "agent.db")


def LOG(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


RISK = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
# minimum risk that needs the user's approval in each permission mode; None = never ask
MODES = {"ask": "MEDIUM", "auto": "CRITICAL", "bypass": None}


# ----------------------------------------------------------------------------- storage
class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, request TEXT NOT NULL, status TEXT NOT NULL,
              mode TEXT, created REAL, updated REAL, result TEXT, error TEXT, parent_id INTEGER, cost_in INTEGER DEFAULT 0, cost_out INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS steps(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, ts REAL, kind TEXT, name TEXT, input TEXT, output TEXT, risk TEXT, decision TEXT);
            CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, step_id INTEGER, tool TEXT, input TEXT, risk TEXT, reason TEXT,
              status TEXT, created REAL, decided REAL);
            CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, kind TEXT, spec TEXT, interval_s INTEGER, next_run REAL,
              status TEXT, created REAL, last_run REAL, last_result TEXT, hits INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, kind TEXT, task_id INTEGER, detail TEXT);
            CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, question TEXT, answer TEXT, created REAL, answered REAL);
            """)

    def q(self, sql, *args):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def one(self, sql, *args):
        r = self.q(sql, *args).fetchone()
        return dict(r) if r else None

    def all(self, sql, *args):
        return [dict(r) for r in self.q(sql, *args).fetchall()]

    def setting(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", key)
        return r["value"] if r else default

    def set_setting(self, key, value):
        self.q("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, str(value))

    def activity(self, actor, kind, task_id=None, detail=""):
        self.q("INSERT INTO activity(ts,actor,kind,task_id,detail) VALUES(?,?,?,?,?)", time.time(), actor, kind, task_id, detail[:4000])

    def step(self, task_id, kind, name="", inp="", out="", risk="", decision=""):
        cur = self.q("INSERT INTO steps(task_id,ts,kind,name,input,output,risk,decision) VALUES(?,?,?,?,?,?,?,?)",
                     task_id, time.time(), kind, name, str(inp)[:20000], str(out)[:40000], risk, decision)
        self.q("UPDATE tasks SET updated=? WHERE id=?", time.time(), task_id)
        return cur.lastrowid


# ----------------------------------------------------------------------------- secrets (systemd-creds user scope; 0600 file fallback)
def secret_path(name):
    return os.path.join(CONF_DIR, "secrets", name + ".cred")


def set_secret(name, value):
    os.makedirs(os.path.dirname(secret_path(name)), exist_ok=True)
    p = secret_path(name)
    try:
        subprocess.run(["systemd-creds", "--user", "encrypt", "--name=" + name, "-", p], input=value.encode(), check=True, capture_output=True, timeout=20)
        os.chmod(p, 0o600)
        if os.path.exists(p + ".plain"):
            os.remove(p + ".plain")
        return "systemd-creds"
    except Exception as e:
        with open(p + ".plain", "w") as f:
            f.write(value)
        os.chmod(p + ".plain", 0o600)
        return "plain-0600 (systemd-creds unavailable: %s)" % type(e).__name__


def get_secret(name):
    p = secret_path(name)
    if os.path.exists(p):
        try:
            r = subprocess.run(["systemd-creds", "--user", "decrypt", "--name=" + name, p, "-"], check=True, capture_output=True, timeout=20)
            return r.stdout.decode().strip()
        except Exception as e:
            LOG("secret decrypt failed", name, e)
    if os.path.exists(p + ".plain"):
        return open(p + ".plain").read().strip()
    return None


def has_secret(name):
    return os.path.exists(secret_path(name)) or os.path.exists(secret_path(name) + ".plain")


def del_secret(name):
    for p in (secret_path(name), secret_path(name) + ".plain"):
        if os.path.exists(p):
            os.remove(p)


# ----------------------------------------------------------------------------- tool definitions (Anthropic JSON schema; converted for other providers)
TOOLS = [
    {"name": "run_shell",
     "description": "Run a shell command on this computer (bash). Use for anything the OS can do: inspect files, run programs, git, compilers, tests, package managers (privileged commands need approval). Returns stdout, stderr and exit code. Start long-running GUI apps with open_app instead.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string", "description": "working directory (default: home)"}, "timeout_s": {"type": "integer", "default": 120}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file (UTF-8). Returns up to 200 KB.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Create or overwrite a text file (creates parent directories). Use append=true to append.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean", "default": False}}, "required": ["path", "content"]}},
    {"name": "list_dir", "description": "List a directory.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "open_app",
     "description": "Open a desktop application, file or URL in the user's graphical session (e.g. app='kate' with args=['/path/file.txt'], app='dolphin', app='xdg-open' with args=['https://...']). Returns immediately.",
     "input_schema": {"type": "object", "properties": {"app": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, "required": ["app"]}},
    {"name": "type_text",
     "description": "Type text into the currently focused window through the Wayland virtual keyboard. Prefer write_file + open_app when the goal is to put text in a document; use this only when typing into a live app is required.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "press_enter": {"type": "boolean", "default": False}, "delay_ms": {"type": "integer", "default": 800, "description": "wait before typing so the window can focus"}}, "required": ["text"]}},
    {"name": "send_email",
     "description": "Send an email from the user's configured mail account (SMTP). Requires mail settings; if missing, tell the user to configure Mail in Command Center settings.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "cc": {"type": "string"}, "attachments": {"type": "array", "items": {"type": "string"}}}, "required": ["to", "subject", "body"]}},
    {"name": "check_email",
     "description": "Search the user's inbox (IMAP) and return recent message summaries. Filters: from_contains, subject_contains, since_hours, unseen_only, limit.",
     "input_schema": {"type": "object", "properties": {"from_contains": {"type": "string"}, "subject_contains": {"type": "string"}, "since_hours": {"type": "integer", "default": 48}, "unseen_only": {"type": "boolean", "default": False}, "limit": {"type": "integer", "default": 10}, "include_body": {"type": "boolean", "default": True}}}},
    {"name": "schedule_watch",
     "description": "Keep tracking something in the background after this task ends and notify the user (optionally starting a follow-up task) when it happens. kind='email_reply' watches the inbox for a reply matching from_contains/subject_contains; kind='command' re-runs a shell command until its output matches the 'expect' regex. The event is logged in history.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["email_reply", "command"]}, "from_contains": {"type": "string"}, "subject_contains": {"type": "string"}, "command": {"type": "string"}, "expect": {"type": "string"}, "interval_minutes": {"type": "integer", "default": 5}, "expires_hours": {"type": "integer", "default": 72}, "notify_message": {"type": "string"}, "followup_task": {"type": "string", "description": "optional natural-language task to run automatically when the watch fires (the matched content is appended)"}}, "required": ["kind", "notify_message"]}},
    {"name": "web_fetch", "description": "Fetch a URL (GET) and return the text content (HTML tags stripped, up to 100 KB).",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "notify_user", "description": "Show a desktop notification to the user (also logged in history).",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "message": {"type": "string"}}, "required": ["message"]}},
    {"name": "ask_user",
     "description": "Ask the user a question and wait for the answer (pauses the task). Use ONLY when the task is genuinely ambiguous or needs information you cannot obtain yourself. Otherwise decide and proceed.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "list_apps",
     "description": "List applications installed on this computer right now (system packages, Flatpaks, user apps) with their launch command, description and file types they open. Newly installed apps appear here immediately. Optional query filters by name/keyword/mime type.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 40}}}},
]

APP_DIRS = ["/usr/share/applications", "/usr/local/share/applications", "/var/lib/flatpak/exports/share/applications",
            os.path.join(HOME, ".local/share/applications"), os.path.join(HOME, ".local/share/flatpak/exports/share/applications")]


def installed_apps():
    """Scan .desktop entries (the OS's own app registry) so the agent sees every installed app instantly."""
    apps = {}
    for d in APP_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".desktop") or fn in apps:
                continue
            try:
                ent = {}
                sec = None
                with open(os.path.join(d, fn), errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("["):
                            sec = line
                            continue
                        if sec != "[Desktop Entry]" or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k in ("Name", "Comment", "Exec", "MimeType", "Categories", "Keywords", "NoDisplay", "Hidden", "Type", "TryExec", "GenericName"):
                            ent.setdefault(k, v)
                if ent.get("Type", "Application") != "Application" or ent.get("NoDisplay") == "true" or ent.get("Hidden") == "true" or not ent.get("Exec"):
                    continue
                apps[fn] = {"id": fn[:-8], "name": ent.get("Name", fn), "generic": ent.get("GenericName", ""), "comment": ent.get("Comment", ""),
                            "exec": re.sub(r"\s%[a-zA-Z]", "", ent["Exec"]).strip(), "mime": ent.get("MimeType", "").strip(";"), "categories": ent.get("Categories", "").strip(";"),
                            "keywords": ent.get("Keywords", "").strip(";")}
            except OSError:
                continue
    return list(apps.values())

# Deterministic risk patterns for shell commands: (regex, risk, reason)
DANGER = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b", "HIGH", "recursive delete"),
    (r"\b(mkfs|dd\s+if=|wipefs|fdisk|sfdisk|parted|cryptsetup)\b", "CRITICAL", "disk or partition operation"),
    (r"\bsudo\b|\bpkexec\b|\bdoas\b|(^|[;&|]\s*)su\s", "CRITICAL", "privileged execution"),
    (r"\b(apt|apt-get|dpkg|snap|flatpak)\s+(install|remove|purge|upgrade|dist-upgrade)\b", "HIGH", "package change"),
    (r"\bsystemctl\s+(disable|mask|stop|start|enable|restart)\b", "HIGH", "service change"),
    (r"(^|[;&|]\s*|\bsudo\s+)(passwd|chpasswd|useradd|userdel|usermod|visudo)\b", "CRITICAL", "account change"),
    (r"\bchmod\s+([0-7]*7[0-7]*|a\+w|o\+w)\b", "HIGH", "world-writable permissions"),
    (r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh\b", "CRITICAL", "pipe download to shell"),
    (r">\s*/etc/|>\s*/usr/|>\s*/boot/|\bchown\s+-R\s+/", "CRITICAL", "system file modification"),
    (r"\b(git\s+push|scp|rsync\s+[^\n]*:|ssh\s|sftp|ftp)\b", "HIGH", "external data transfer"),
    (r"\bcrontab\b|~?/\.ssh/|\.gnupg|/etc/shadow|\.env\b|api[_-]?key|token", "HIGH", "credentials or persistence"),
    (r"\b(shutdown|reboot|poweroff|halt)\b|systemctl\s+(poweroff|reboot|suspend)", "HIGH", "power state"),
    (r"\b(kill\s+-9\s+-1|killall5|pkill\s+-9\s+\.)", "HIGH", "mass process kill"),
    (r"\bxdg-mime\s+default|\bgsettings\s+set|\bkwriteconfig", "MEDIUM", "settings change"),
]
READ_ONLY = re.compile(r"^\s*(ls|cat|head|tail|less|grep|rg|find|fd|wc|stat|file|du|df|ps|top|htop|free|uname|whoami|id|date|env|printenv|echo|which|type|pwd|tree|"
                       r"git\s+(status|log|diff|show|branch)|systemctl\s+(status|is-active|is-enabled|list-units)|journalctl|ip\s+(a|addr|link|route)|ss|"
                       r"dpkg\s+-[lLs]|apt\s+(list|search|show)|apt-cache|flatpak\s+(list|search|info)|python3?\s+--version|node\s+--version|cargo\s+--version)\b")


def classify(tool, inp):
    """Deterministic risk classification. Returns (RISK, reason)."""
    if tool in ("read_file", "list_dir", "notify_user", "ask_user", "list_apps"):
        return "LOW", "read-only or user-facing"
    if tool == "check_email":
        return "MEDIUM", "reads personal mail"
    if tool == "web_fetch":
        u = inp.get("url", "")
        return ("MEDIUM", "network fetch") if u.startswith(("http://", "https://")) else ("HIGH", "non-http URL")
    if tool == "open_app":
        return "LOW", "opens an application in the session"
    if tool == "type_text":
        return "MEDIUM", "types into the focused window"
    if tool == "write_file":
        p = os.path.abspath(os.path.expanduser(inp.get("path", "")))
        if not p.startswith(HOME + os.sep):
            return "CRITICAL", "writes outside the home directory"
        if os.path.exists(p) and not inp.get("append"):
            return "MEDIUM", "overwrites an existing file"
        return "MEDIUM", "creates a file"
    if tool == "send_email":
        return "HIGH", "external communication"
    if tool == "schedule_watch":
        return "MEDIUM", "background job with notifications"
    if tool == "run_shell":
        c = inp.get("command", "")
        worst, why = "MEDIUM", "runs a command"
        for pat, risk, reason in DANGER:
            if re.search(pat, c, re.I) and RISK.index(risk) > RISK.index(worst):
                worst, why = risk, reason
        if worst == "MEDIUM" and READ_ONLY.match(c) and not re.search(r"[|>;&]|\$\(", c):
            return "LOW", "read-only command"
        return worst, why
    return "HIGH", "unknown tool"


def notify(title, message, urgency="normal"):
    for cmd in (["notify-send", "-a", APP, "-i", "fabric-os", "-u", urgency, title, message],
                ["kdialog", "--title", title, "--passivepopup", message, "8"]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except FileNotFoundError:
            continue
    return False


def _strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", s)).strip()


class Tools:
    def __init__(self, store, agent):
        self.store, self.agent = store, agent

    def run(self, task_id, name, inp):
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return {"error": "unknown tool " + name}, True
        try:
            return fn(task_id, inp), False
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e)}, True

    def t_run_shell(self, task_id, inp):
        cwd = os.path.expanduser(inp.get("cwd") or HOME)
        to = min(int(inp.get("timeout_s") or 120), 1800)
        try:
            r = subprocess.run(["bash", "-lc", inp["command"]], cwd=cwd, capture_output=True, text=True, timeout=to, env=self.agent.session_env())
            return {"exit_code": r.returncode, "stdout": r.stdout[-30000:], "stderr": r.stderr[-10000:]}
        except subprocess.TimeoutExpired:
            return {"error": "timeout after %ss" % to}

    def t_read_file(self, task_id, inp):
        p = os.path.expanduser(inp["path"])
        with open(p, "r", errors="replace") as f:
            data = f.read(200000)
        return {"path": p, "content": data, "truncated": os.path.getsize(p) > 200000}

    def t_write_file(self, task_id, inp):
        p = os.path.expanduser(inp["path"])
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a" if inp.get("append") else "w") as f:
            f.write(inp["content"])
        return {"path": p, "bytes": len(inp["content"].encode())}

    def t_list_dir(self, task_id, inp):
        p = os.path.expanduser(inp["path"])
        ents = []
        for n in sorted(os.listdir(p))[:500]:
            fp = os.path.join(p, n)
            ents.append({"name": n, "dir": os.path.isdir(fp), "size": os.path.getsize(fp) if os.path.isfile(fp) else None})
        return {"path": p, "entries": ents}

    def t_open_app(self, task_id, inp):
        app = inp["app"]
        args = [os.path.expanduser(a) for a in (inp.get("args") or [])]
        cmd = ["xdg-open", args[0]] if app in ("xdg-open", "open") and args else [app] + args
        p = subprocess.Popen(["setsid", "-f"] + cmd, env=self.agent.session_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HOME)
        time.sleep(0.6)
        return {"launched": " ".join(shlex.quote(c) for c in cmd), "pid": p.pid}

    def t_type_text(self, task_id, inp):
        time.sleep(min(int(inp.get("delay_ms") or 800), 10000) / 1000)
        env = self.agent.session_env()
        r = subprocess.run(["wtype", inp["text"]], env=env, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {"error": "wtype failed: " + (r.stderr.strip() or "no virtual keyboard available in this session")}
        if inp.get("press_enter"):
            subprocess.run(["wtype", "-k", "Return"], env=env, timeout=10)
        return {"typed_chars": len(inp["text"])}

    # ---- mail
    def _mail(self):
        s = self.store
        cfg = {k: s.setting("mail." + k) for k in ("imap_host", "imap_port", "smtp_host", "smtp_port", "user", "from", "smtp_security")}
        cfg["password"] = get_secret("mail_password")
        if not (cfg["user"] and cfg["password"] and (cfg["smtp_host"] or cfg["imap_host"])):
            raise RuntimeError("Mail is not configured. Ask the user to fill Settings → Mail in Fab OS Command Center (IMAP/SMTP host, user, password).")
        return cfg

    def t_send_email(self, task_id, inp):
        cfg = self._mail()
        msg = EmailMessage()
        sender = cfg["from"] or cfg["user"]
        msg["From"] = sender
        msg["To"] = inp["to"]
        msg["Subject"] = inp["subject"]
        if inp.get("cc"):
            msg["Cc"] = inp["cc"]
        msg["Message-ID"] = email.utils.make_msgid(domain=sender.split("@")[-1])
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg.set_content(inp["body"])
        for a in inp.get("attachments") or []:
            ap = os.path.expanduser(a)
            with open(ap, "rb") as f:
                msg.add_attachment(f.read(), maintype="application", subtype="octet-stream", filename=os.path.basename(ap))
        port = int(cfg["smtp_port"] or 587)
        sec = (cfg["smtp_security"] or ("ssl" if port == 465 else "starttls")).lower()
        if sec == "ssl":
            srv = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=60)
        else:
            srv = smtplib.SMTP(cfg["smtp_host"], port, timeout=60)
            srv.ehlo()
            if sec == "starttls":
                srv.starttls()
                srv.ehlo()
        with srv:
            srv.login(cfg["user"], cfg["password"])
            srv.send_message(msg)
        self.store.activity("agent", "email_sent", task_id, "to=%s subject=%s" % (inp["to"], inp["subject"]))
        return {"sent": True, "message_id": msg["Message-ID"], "to": inp["to"]}

    def _imap_search(self, cfg, from_contains=None, subject_contains=None, since_hours=48, unseen_only=False, limit=10, include_body=True, seen_uids=None):
        M = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg["imap_port"] or 993))
        M.login(cfg["user"], cfg["password"])
        M.select("INBOX", readonly=True)
        since = (datetime.now(timezone.utc) - _dt.timedelta(hours=int(since_hours or 48))).strftime("%d-%b-%Y")
        crit = ["SINCE", since]
        if unseen_only:
            crit.append("UNSEEN")
        if from_contains:
            crit += ["FROM", '"%s"' % from_contains]
        if subject_contains:
            crit += ["SUBJECT", '"%s"' % subject_contains]
        typ, data = M.uid("search", None, *crit)
        uids = data[0].split() if data and data[0] else []
        out = []
        for uid in reversed(uids[-int(limit or 10) * 3:]):
            if seen_uids is not None and uid.decode() in seen_uids:
                continue
            typ, md = M.uid("fetch", uid, "(RFC822)" if include_body else "(RFC822.HEADER)")
            if typ != "OK" or not md or not md[0]:
                continue
            m = email.message_from_bytes(md[0][1])

            def dec(h):
                return str(email.header.make_header(email.header.decode_header(m.get(h, "")))) if m.get(h) else ""
            body = ""
            if include_body:
                for part in m.walk():
                    if part.get_content_type() == "text/plain" and not part.get_filename():
                        body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                        break
                if not body:
                    for part in m.walk():
                        if part.get_content_type() == "text/html":
                            body = _strip_html(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace"))
                            break
            out.append({"uid": uid.decode(), "from": dec("From"), "to": dec("To"), "subject": dec("Subject"), "date": m.get("Date", ""),
                        "message_id": m.get("Message-ID", ""), "in_reply_to": m.get("In-Reply-To", ""), "body": body[:4000]})
            if len(out) >= int(limit or 10):
                break
        M.logout()
        return out

    def t_check_email(self, task_id, inp):
        cfg = self._mail()
        if not cfg["imap_host"]:
            raise RuntimeError("IMAP host not configured (Settings → Mail).")
        msgs = self._imap_search(cfg, inp.get("from_contains"), inp.get("subject_contains"), inp.get("since_hours", 48),
                                 inp.get("unseen_only", False), inp.get("limit", 10), inp.get("include_body", True))
        return {"count": len(msgs), "messages": msgs}

    def t_schedule_watch(self, task_id, inp):
        spec = {k: inp.get(k) for k in ("from_contains", "subject_contains", "command", "expect", "notify_message", "followup_task")}
        spec["created"] = time.time()
        spec["expires"] = time.time() + 3600 * int(inp.get("expires_hours") or 72)
        spec["seen_uids"] = []
        if inp["kind"] == "email_reply":
            cfg = self._mail()  # validates configuration now
            try:
                spec["seen_uids"] = [m["uid"] for m in self._imap_search(cfg, spec.get("from_contains"), spec.get("subject_contains"), 24, False, 50, False)]
            except Exception as e:
                LOG("watch baseline failed", e)
        iv = max(1, int(inp.get("interval_minutes") or 5)) * 60
        cur = self.store.q("INSERT INTO watches(task_id,kind,spec,interval_s,next_run,status,created) VALUES(?,?,?,?,?,?,?)",
                           task_id, inp["kind"], json.dumps(spec), iv, time.time() + iv, "active", time.time())
        self.store.activity("agent", "watch_created", task_id, "%s every %ss: %s" % (inp["kind"], iv, spec.get("notify_message")))
        return {"watch_id": cur.lastrowid, "kind": inp["kind"], "interval_s": iv, "expires_in_hours": int(inp.get("expires_hours") or 72)}

    def t_web_fetch(self, task_id, inp):
        req = urllib.request.Request(inp["url"], headers={"User-Agent": "FabOS-agent/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read(2_000_000)
        text = raw.decode("utf-8", errors="replace")
        if "html" in ct:
            text = _strip_html(text)
        return {"url": inp["url"], "content_type": ct, "text": text[:100000]}

    def t_notify_user(self, task_id, inp):
        ok = notify(inp.get("title") or APP, inp["message"])
        self.store.activity("agent", "notify", task_id, inp["message"])
        return {"notified": ok}

    def t_ask_user(self, task_id, inp):
        return self.agent.ask_user(task_id, inp["question"])

    def t_list_apps(self, task_id, inp):
        q = (inp.get("query") or "").lower().strip()
        apps = installed_apps()
        if q:
            apps = [a for a in apps if q in (a["name"] + " " + a["generic"] + " " + a["comment"] + " " + a["keywords"] + " " + a["mime"] + " " + a["exec"] + " " + a["categories"]).lower()]
        return {"count": len(apps), "apps": apps[:int(inp.get("limit") or 40)]}


# ----------------------------------------------------------------------------- providers
SYSTEM_PROMPT = """You are the {app} agent: the autonomous operator built into this Linux desktop (Ubuntu-based, KDE Plasma 6, Wayland), running as the logged-in user inside their graphical session. The user gives you a goal in plain language; you carry it out end to end with the tools (files, shell, applications, email, web, background watches) and report the outcome.

How to work:
- Act autonomously. Plan briefly, then execute step by step and verify results (read files back, check exit codes, list processes). Do not stop half-way and do not ask the user things you can find out yourself. Use ask_user only for genuinely missing information such as a credential you must not guess.
- To put text into a document: write_file with the content, then open_app the editor on that file (kate is installed). Use type_text only when you must type into a live application.
- For coding tasks: create a project under ~/Projects/<name>, write the code and tests, run them with run_shell, fix failures, then summarise what was built and how it was verified.
- For email: send_email to send; check_email to read. To wait for a reply after sending, call schedule_watch(kind="email_reply", from_contains=<recipient address>, ...) so the user is notified and, if asked, a follow-up task runs automatically. Then finish; never poll in a loop.
- Applications: every installed app (system, Flatpak, user) is available to you the moment it is installed. Use list_apps to discover names, launch commands and supported file types, open_app to launch them, and their CLI or D-Bus interfaces via run_shell (KDE apps: qdbus6 / kdialog / kioclient). Installed now ({app_count} apps): {app_names}.
- Every tool call passes a deterministic policy check (risk LOW/MEDIUM/HIGH/CRITICAL against the user's permission mode). A denied call returns an error: respect it, explain, and find an allowed way or stop.
- Never fabricate results. Report exactly what happened, including partial failures. Keep the final message short: what was done, where outputs are, what the user should look at.
- Current user: {user}. Home: {home}. Date/time: {now}. Permission mode: {mode}."""

# Provider presets. All non-Claude providers speak the OpenAI-compatible chat API with tool calling.
PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "secret": "claude_api_key", "model": "claude-opus-5"},
    "openai": {"label": "OpenAI", "secret": "openai_api_key", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1"},
    "gemini": {"label": "Google Gemini", "secret": "gemini_api_key", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-pro"},
    "local": {"label": "Local model (llama-server / any OpenAI-compatible)", "secret": "local_api_key", "base_url": "http://127.0.0.1:8080/v1", "model": "local"},
}
SECRET_NAMES = ("claude_api_key", "openai_api_key", "gemini_api_key", "local_api_key", "mail_password")


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key, model, fallbacks=True):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.fallbacks = fallbacks

    def step(self, system, messages, tools, on_usage=None):
        kw = dict(model=self.model, max_tokens=16000,
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  messages=messages, tools=tools)
        # Server-side refusal fallbacks (Opus 5 / Fable): re-run declined requests on a fallback model inside the same call.
        if self.fallbacks and self.model.startswith(("claude-opus-5", "claude-fable")):
            kw["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kw["extra_body"] = {"fallbacks": "default"}
        try:
            resp = self.client.messages.create(**kw)
        except self.anthropic.BadRequestError as e:
            if "fallbacks" in str(e) or "anthropic-beta" in str(e):
                kw.pop("extra_headers", None)
                kw.pop("extra_body", None)
                resp = self.client.messages.create(**kw)
            else:
                raise
        d = resp.model_dump()
        if on_usage and d.get("usage"):
            on_usage(d["usage"].get("input_tokens", 0) or 0, d["usage"].get("output_tokens", 0) or 0)
        content = [b for b in d["content"] if b.get("type") in ("text", "tool_use", "thinking", "redacted_thinking")]
        for b in content:
            b.pop("citations", None)
        return {"content": content, "stop_reason": d.get("stop_reason"), "stop_details": d.get("stop_details")}


class OpenAICompatProvider:
    """Any /v1/chat/completions endpoint with tool calling (llama-server, vLLM, other vendors)."""
    name = "openai-compatible"

    def __init__(self, base_url, api_key, model):
        self.base, self.key, self.model = base_url.rstrip("/"), api_key or "none", model

    def step(self, system, messages, tools, on_usage=None):
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                if isinstance(m["content"], str):
                    msgs.append({"role": "user", "content": m["content"]})
                    continue
                for b in m["content"]:
                    if b["type"] == "tool_result":
                        msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])})
                    elif b["type"] == "text":
                        msgs.append({"role": "user", "content": b["text"]})
            else:
                text = "".join(b.get("text", "") for b in m["content"] if b["type"] == "text")
                calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}} for b in m["content"] if b["type"] == "tool_use"]
                am = {"role": "assistant", "content": text or None}
                if calls:
                    am["tool_calls"] = calls
                msgs.append(am)
        body = {"model": self.model, "messages": msgs, "temperature": 0.2,
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]}
        req = urllib.request.Request(self.base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
        ch = d["choices"][0]["message"]
        content = []
        if ch.get("content"):
            content.append({"type": "text", "text": ch["content"]})
        for tc in ch.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc["function"].get("arguments")}
            content.append({"type": "tool_use", "id": tc.get("id") or "call_" + uuid.uuid4().hex[:12], "name": tc["function"]["name"], "input": args})
        if on_usage and d.get("usage"):
            on_usage(d["usage"].get("prompt_tokens", 0), d["usage"].get("completion_tokens", 0))
        return {"content": content, "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"}


class FakeProvider:
    """Deterministic scripted provider for tests and offline demos (FABRIC_AGENT_PROVIDER=fake)."""
    name = "fake"

    def step(self, system, messages, tools, on_usage=None):
        first = messages[0]["content"]
        req = first if isinstance(first, str) else first[0].get("text", "")
        n_results = sum(1 for m in messages if m["role"] == "user" and not isinstance(m["content"], str))
        low = req.lower()

        def tu(name, inp):
            return {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": name, "input": inp}
        if "editor" in low or "kate" in low:
            m = re.search(r"write\s+['\"]?(.+?)['\"]?(\s+and\b|\s*,|\s*$)", req, re.I)
            text = (m.group(1) if m else "hi").strip()
            plan = [tu("write_file", {"path": "~/Documents/fabric-note.txt", "content": text + "\n"}),
                    tu("open_app", {"app": "kate", "args": ["~/Documents/fabric-note.txt"]}),
                    tu("run_shell", {"command": "sleep 2; pgrep -a kate | head -1; cat ~/Documents/fabric-note.txt"})]
            if "mail" in low:
                em = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", req)
                addr = em.group(0) if em else "someone@example.com"
                plan.append(tu("send_email", {"to": addr, "subject": "Note from Fab OS", "body": text}))
                plan.append(tu("schedule_watch", {"kind": "email_reply", "from_contains": addr, "notify_message": "Reply received to your Fab OS note", "interval_minutes": 2}))
        elif "fail" in low:
            plan = [tu("run_shell", {"command": "exit 3"})]
        elif "privileged" in low:
            plan = [tu("run_shell", {"command": "sudo -n true"})]
        else:
            plan = [tu("run_shell", {"command": "uname -a; date"})]
        if n_results < len(plan):
            return {"content": [{"type": "text", "text": "Step %d/%d" % (n_results + 1, len(plan))}, plan[n_results]], "stop_reason": "tool_use"}
        return {"content": [{"type": "text", "text": "Done: executed %d steps for '%s'." % (len(plan), req[:60])}], "stop_reason": "end_turn"}


# ----------------------------------------------------------------------------- the agent
class Agent:
    def __init__(self, store):
        self.store = store
        self.tools = Tools(store, self)
        self.events = {}
        self.cancel = set()
        self.answers = {}
        self.sem = threading.Semaphore(int(store.setting("agent.max_parallel", "2")))
        for r in store.all("SELECT id FROM tasks WHERE status IN ('running','waiting_approval','waiting_user')"):
            store.q("UPDATE tasks SET status='failed', error='service restarted while task was running', updated=? WHERE id=?", time.time(), r["id"])
        for r in store.all("SELECT id FROM tasks WHERE status='queued'"):
            self.start(r["id"])

    def session_env(self):
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", os.path.dirname(RUN_DIR))
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_SESSION_TYPE", "wayland")
        env.setdefault("QT_QPA_PLATFORM", "wayland")
        return env

    def ai_enabled(self):
        return self.store.setting("ai.enabled", "true") == "true"

    def provider(self):
        if not self.ai_enabled():
            raise RuntimeError("System-Wide AI is OFF. Turn it on in Fab OS Command Center (top toolbar) or run: fabric settings ai.enabled true")
        kind = os.environ.get("FABRIC_AGENT_PROVIDER") or self.store.setting("provider", "claude")
        if kind == "fake":
            return FakeProvider()
        if kind == "claude":
            key = get_secret("claude_api_key") or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("No Claude API key configured. Open Fab OS Command Center → Settings → AI provider and paste your key, or choose another provider (OpenAI, Gemini, local model).")
            return ClaudeProvider(key, self.store.setting("claude.model", PROVIDERS["claude"]["model"]), self.store.setting("claude.fallbacks", "true") == "true")
        if kind in PROVIDERS:
            pre = PROVIDERS[kind]
            key = get_secret(pre["secret"])
            if not key and kind != "local":
                raise RuntimeError("No %s API key configured. Open Fab OS Command Center → Settings → AI provider." % pre["label"])
            return OpenAICompatProvider(self.store.setting(kind + ".base_url", pre["base_url"]), key, self.store.setting(kind + ".model", pre["model"]))
        raise RuntimeError("unknown provider " + kind)

    def mode(self, task=None):
        return (task or {}).get("mode") or self.store.setting("mode", "auto")

    def needs_approval(self, risk, mode):
        thr = MODES.get(mode, "MEDIUM")
        return thr is not None and RISK.index(risk) >= RISK.index(thr)

    # -- lifecycle
    def create(self, request, title=None, mode=None, parent_id=None, actor="user"):
        cur = self.store.q("INSERT INTO tasks(title,request,status,mode,created,updated,parent_id) VALUES(?,?,?,?,?,?,?)",
                           (title or request.strip().split("\n")[0])[:80], request, "queued", mode, time.time(), time.time(), parent_id)
        tid = cur.lastrowid
        self.store.activity(actor, "task_created", tid, request[:500])
        self.start(tid)
        return tid

    def start(self, tid):
        threading.Thread(target=self._run, args=(tid,), daemon=True, name="task-%d" % tid).start()

    def ask_user(self, task_id, question):
        qid = self.store.q("INSERT INTO questions(task_id,question,created) VALUES(?,?,?)", task_id, question, time.time()).lastrowid
        self.store.q("UPDATE tasks SET status='waiting_user', updated=? WHERE id=?", time.time(), task_id)
        self.store.step(task_id, "question", "ask_user", question)
        notify(APP + " needs your input", question[:200])
        ev = threading.Event()
        self.events[("q", qid)] = ev
        if not ev.wait(3600 * 12) or task_id in self.cancel:
            return {"error": "no answer from user (timed out or cancelled)"}
        self.store.q("UPDATE tasks SET status='running', updated=? WHERE id=?", time.time(), task_id)
        return {"answer": self.answers.pop(qid, "")}

    def answer(self, task_id, text):
        q = self.store.one("SELECT id FROM questions WHERE task_id=? AND answer IS NULL ORDER BY id DESC LIMIT 1", task_id)
        if not q:
            return False
        self.store.q("UPDATE questions SET answer=?, answered=? WHERE id=?", text, time.time(), q["id"])
        self.answers[q["id"]] = text
        self.store.step(task_id, "answer", "user", text)
        self.store.activity("user", "answered", task_id, text[:500])
        ev = self.events.pop(("q", q["id"]), None)
        if ev:
            ev.set()
        return True

    def decide(self, approval_id, decision, actor="user"):
        a = self.store.one("SELECT * FROM approvals WHERE id=?", approval_id)
        if not a or a["status"] != "pending":
            return False
        self.store.q("UPDATE approvals SET status=?, decided=? WHERE id=?", decision, time.time(), approval_id)
        self.store.q("UPDATE steps SET decision=? WHERE id=?", decision, a["step_id"])
        self.store.activity(actor, "approval_" + decision, a["task_id"], "%s %s" % (a["tool"], a["input"][:300]))
        ev = self.events.pop(("a", approval_id), None)
        if ev:
            ev.set()
        return True

    def cancel_task(self, tid):
        self.cancel.add(tid)
        self.store.q("UPDATE tasks SET status='cancelled', updated=? WHERE id=? AND status IN ('queued','running','waiting_approval','waiting_user')", time.time(), tid)
        for a in self.store.all("SELECT id FROM approvals WHERE task_id=? AND status='pending'", tid):
            self.decide(a["id"], "denied", "system")
        for k, ev in list(self.events.items()):
            if k[0] == "q":
                ev.set()
        self.store.activity("user", "task_cancelled", tid)

    def _gate(self, tid, task, name, inp):
        risk, reason = classify(name, inp)
        mode = self.mode(task)
        sid = self.store.step(tid, "tool_call", name, json.dumps(inp)[:20000], "", risk, "")
        if not self.needs_approval(risk, mode):
            self.store.q("UPDATE steps SET decision='auto-approved' WHERE id=?", sid)
            return sid, True, risk, reason
        aid = self.store.q("INSERT INTO approvals(task_id,step_id,tool,input,risk,reason,status,created) VALUES(?,?,?,?,?,?,?,?)",
                           tid, sid, name, json.dumps(inp)[:20000], risk, reason, "pending", time.time()).lastrowid
        self.store.q("UPDATE tasks SET status='waiting_approval', updated=? WHERE id=?", time.time(), tid)
        summary = inp.get("command") or inp.get("to") or inp.get("path") or inp.get("url") or inp.get("app") or ""
        notify("%s wants to %s (%s)" % (APP, name.replace("_", " "), risk), (reason + ": " + str(summary))[:220], "critical" if risk == "CRITICAL" else "normal")
        ev = threading.Event()
        self.events[("a", aid)] = ev
        ev.wait(3600 * 6)
        a = self.store.one("SELECT status FROM approvals WHERE id=?", aid)
        if a["status"] == "pending":
            self.store.q("UPDATE approvals SET status='expired', decided=? WHERE id=?", time.time(), aid)
            self.store.q("UPDATE steps SET decision='expired' WHERE id=?", sid)
        if tid not in self.cancel:
            self.store.q("UPDATE tasks SET status='running', updated=? WHERE id=?", time.time(), tid)
        return sid, (a["status"] == "approved"), risk, reason

    def _run(self, tid):
        with self.sem:
            task = self.store.one("SELECT * FROM tasks WHERE id=?", tid)
            if not task or task["status"] != "queued":
                return
            self.store.q("UPDATE tasks SET status='running', updated=? WHERE id=?", time.time(), tid)
            try:
                prov = self.provider()
            except Exception as e:
                self.store.q("UPDATE tasks SET status='failed', error=?, updated=? WHERE id=?", str(e), time.time(), tid)
                self.store.step(tid, "error", "provider", "", str(e))
                notify(APP + ": task failed", str(e)[:200])
                return
            apps = installed_apps()
            names = ", ".join(sorted(a["name"] for a in apps)[:120])
            system = SYSTEM_PROMPT.format(app=APP, user=os.environ.get("USER", "user"), home=HOME, app_count=len(apps), app_names=names,
                                          now=datetime.now().strftime("%Y-%m-%d %H:%M %Z"), mode=self.mode(task))
            messages = [{"role": "user", "content": task["request"]}]
            final = ""

            def usage(i, o):
                self.store.q("UPDATE tasks SET cost_in=cost_in+?, cost_out=cost_out+? WHERE id=?", i, o, tid)
            try:
                for turn in range(int(self.store.setting("agent.max_turns", "60"))):
                    if tid in self.cancel:
                        raise RuntimeError("cancelled by user")
                    resp = prov.step(system, messages, TOOLS, usage)
                    content = resp["content"]
                    messages.append({"role": "assistant", "content": content})
                    for b in content:
                        if b["type"] == "text" and b["text"].strip():
                            self.store.step(tid, "assistant", prov.name, "", b["text"])
                            final = b["text"]
                    if resp["stop_reason"] == "refusal":
                        raise RuntimeError("The model declined this request (%s)." % ((resp.get("stop_details") or {}).get("category") or "policy"))
                    calls = [b for b in content if b["type"] == "tool_use"]
                    if not calls:
                        break
                    results = []
                    for c in calls:
                        if tid in self.cancel:
                            raise RuntimeError("cancelled by user")
                        inp = c["input"] if isinstance(c["input"], dict) else {}
                        sid, ok, risk, reason = self._gate(tid, task, c["name"], inp)
                        if not ok:
                            out, err = {"error": "Denied by user/policy (%s: %s). Do not retry the same action; explain or find an allowed way." % (risk, reason)}, True
                        else:
                            out, err = self.tools.run(tid, c["name"], inp)
                        self.store.q("UPDATE steps SET output=? WHERE id=?", json.dumps(out)[:40000], sid)
                        res = {"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(out)[:60000]}
                        if err:
                            res["is_error"] = True
                        results.append(res)
                    messages.append({"role": "user", "content": results})
                else:
                    final += "\n\n(stopped: reached the turn limit)"
                st = "cancelled" if tid in self.cancel else "done"
                self.store.q("UPDATE tasks SET status=?, result=?, updated=? WHERE id=?", st, final, time.time(), tid)
                self.store.step(tid, "final", "", "", final)
                self.store.activity("agent", "task_" + st, tid, final[:500])
                notify(APP + ": task %s" % st, ((task["title"] or "") + " — " + final)[:180])
            except Exception as e:
                msg = str(e) if isinstance(e, RuntimeError) else "%s: %s" % (type(e).__name__, e)
                st = "cancelled" if "cancelled" in msg else "failed"
                self.store.q("UPDATE tasks SET status=?, error=?, result=?, updated=? WHERE id=?", st, msg, final, time.time(), tid)
                self.store.step(tid, "error", "", "", msg)
                self.store.activity("agent", "task_" + st, tid, msg[:500])
                notify(APP + ": task " + st, msg[:200], "critical")
            finally:
                self.cancel.discard(tid)


# ----------------------------------------------------------------------------- watches (background tracking)
class Watcher(threading.Thread):
    def __init__(self, store, agent):
        super().__init__(daemon=True, name="watcher")
        self.store, self.agent = store, agent

    def run(self):
        while True:
            time.sleep(20)
            try:
                if not self.agent.ai_enabled():
                    continue
                for w in self.store.all("SELECT * FROM watches WHERE status='active' AND next_run<=?", time.time()):
                    self.check(w)
            except Exception as e:
                LOG("watcher error", e)

    def check(self, w):
        spec = json.loads(w["spec"])
        now = time.time()
        hit = None
        err = None
        try:
            if spec.get("expires") and now > spec["expires"]:
                self.store.q("UPDATE watches SET status='expired', last_run=? WHERE id=?", now, w["id"])
                self.store.activity("watch", "expired", w["task_id"], spec.get("notify_message", ""))
                return
            if w["kind"] == "email_reply":
                cfg = self.agent.tools._mail()
                msgs = self.agent.tools._imap_search(cfg, spec.get("from_contains"), spec.get("subject_contains"), 72, False, 5, True, set(spec.get("seen_uids") or []))
                if msgs:
                    hit = msgs[0]
                    spec["seen_uids"] = (spec.get("seen_uids") or []) + [m["uid"] for m in msgs]
            elif w["kind"] == "command":
                r = subprocess.run(["bash", "-lc", spec.get("command") or "true"], capture_output=True, text=True, timeout=120, env=self.agent.session_env())
                if re.search(spec.get("expect") or ".", r.stdout + r.stderr):
                    hit = {"output": (r.stdout + r.stderr)[-2000:]}
        except Exception as e:
            err = str(e)
        self.store.q("UPDATE watches SET spec=?, last_run=?, next_run=?, last_result=? WHERE id=?", json.dumps(spec), now, now + w["interval_s"],
                     (err or ("hit" if hit else "no change"))[:500], w["id"])
        if hit:
            detail = json.dumps(hit)[:3000]
            self.store.q("UPDATE watches SET hits=hits+1, status=? WHERE id=?", "active" if spec.get("repeat") else "done", w["id"])
            self.store.step(w["task_id"], "watch_hit", w["kind"], "", detail)
            self.store.activity("watch", "hit", w["task_id"], spec.get("notify_message", "") + " | " + detail[:300])
            summary = hit.get("subject") or hit.get("output", "")[:120]
            notify(APP + ": " + (spec.get("notify_message") or "watch fired"), ("From %s — %s" % (hit.get("from"), summary)) if hit.get("from") else str(summary)[:200])
            if spec.get("followup_task"):
                self.agent.create(spec["followup_task"] + "\n\nTriggering event:\n" + detail, title="Follow-up: " + spec["followup_task"][:60], parent_id=w["task_id"], actor="watch")


# ----------------------------------------------------------------------------- HTTP API
def make_handler(store, agent, token):
    class H(BaseHTTPRequestHandler):
        server_version = "fabric-agentd/1.0"

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _auth(self):
            if self.headers.get("Authorization") == "Bearer " + token:
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def do_GET(self):
            p = self.path.split("?")[0]
            qs = dict(x.split("=", 1) for x in self.path.split("?")[1].split("&") if "=" in x) if "?" in self.path else {}
            if p == "/health":
                return self._send(200, {"ok": True, "app": APP})
            if not self._auth():
                return
            if p == "/status":
                counts = {r["status"]: r["n"] for r in store.all("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
                prov = os.environ.get("FABRIC_AGENT_PROVIDER") or store.setting("provider", "claude")
                ready = prov == "fake" or prov == "local" or (prov in PROVIDERS and (has_secret(PROVIDERS[prov]["secret"]) or (prov == "claude" and bool(os.environ.get("ANTHROPIC_API_KEY")))))
                return self._send(200, {"mode": store.setting("mode", "auto"), "provider": prov, "provider_ready": ready, "ai_enabled": agent.ai_enabled(),
                                        "providers": {k: {"label": v["label"], "has_key": has_secret(v["secret"])} for k, v in PROVIDERS.items()},
                                        "mail_ready": bool(store.setting("mail.user") and has_secret("mail_password")), "tasks": counts,
                                        "pending_approvals": store.one("SELECT COUNT(*) n FROM approvals WHERE status='pending'")["n"],
                                        "active_watches": store.one("SELECT COUNT(*) n FROM watches WHERE status='active'")["n"],
                                        "latest": store.all("SELECT id,title,status,updated FROM tasks ORDER BY updated DESC LIMIT 3")})
            if p == "/settings":
                s = {r["key"]: r["value"] for r in store.all("SELECT * FROM settings")}
                s.setdefault("mode", "auto")
                s.setdefault("provider", "claude")
                s.setdefault("ai.enabled", "true")
                for k, v in PROVIDERS.items():
                    s.setdefault(k + ".model", v["model"])
                    if "base_url" in v:
                        s.setdefault(k + ".base_url", v["base_url"])
                s["secrets"] = {n: has_secret(n) for n in SECRET_NAMES}
                return self._send(200, s)
            if p == "/tasks":
                return self._send(200, store.all("SELECT id,title,status,mode,created,updated,parent_id,cost_in,cost_out,substr(result,1,200) result,error FROM tasks ORDER BY id DESC LIMIT ?", int(qs.get("limit", 100))))
            m = re.match(r"^/tasks/(\d+)$", p)
            if m:
                t = store.one("SELECT * FROM tasks WHERE id=?", m.group(1))
                if not t:
                    return self._send(404, {"error": "no such task"})
                t["steps"] = store.all("SELECT * FROM steps WHERE task_id=? ORDER BY id", t["id"])
                t["approvals"] = store.all("SELECT * FROM approvals WHERE task_id=? ORDER BY id", t["id"])
                t["watches"] = store.all("SELECT * FROM watches WHERE task_id=? ORDER BY id", t["id"])
                t["questions"] = store.all("SELECT * FROM questions WHERE task_id=? ORDER BY id", t["id"])
                return self._send(200, t)
            if p == "/approvals/pending":
                return self._send(200, store.all("SELECT a.*, t.title FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE a.status='pending' ORDER BY a.id"))
            if p == "/watches":
                return self._send(200, store.all("SELECT * FROM watches ORDER BY id DESC LIMIT 200"))
            if p == "/activity":
                return self._send(200, store.all("SELECT * FROM activity ORDER BY id DESC LIMIT ?", int(qs.get("limit", 200))))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            p = self.path
            b = self._body()
            if p == "/tasks":
                if not b.get("request", "").strip():
                    return self._send(400, {"error": "request is required"})
                if not agent.ai_enabled():
                    return self._send(403, {"error": "System-Wide AI is OFF. Turn it on in Command Center or: fabric settings ai.enabled true"})
                tid = agent.create(b["request"], b.get("title"), b.get("mode"))
                return self._send(201, {"id": tid, "status": "queued"})
            m = re.match(r"^/tasks/(\d+)/(cancel|retry|answer)$", p)
            if m:
                tid, act = int(m.group(1)), m.group(2)
                t = store.one("SELECT * FROM tasks WHERE id=?", tid)
                if not t:
                    return self._send(404, {"error": "no such task"})
                if act == "cancel":
                    agent.cancel_task(tid)
                    return self._send(200, {"id": tid, "status": "cancelled"})
                if act == "retry":
                    nid = agent.create(t["request"], t["title"], t["mode"])
                    return self._send(201, {"id": nid, "retry_of": tid})
                return self._send(200, {"ok": agent.answer(tid, b.get("text", ""))})
            m = re.match(r"^/approvals/(\d+)$", p)
            if m:
                d = b.get("decision")
                ok = d in ("approved", "denied") and agent.decide(int(m.group(1)), d)
                return self._send(200 if ok else 409, {"ok": bool(ok)})
            if p == "/secrets":
                if b.get("name") not in SECRET_NAMES:
                    return self._send(400, {"error": "unknown secret"})
                if b.get("value") in (None, ""):
                    del_secret(b["name"])
                    store.activity("user", "secret_removed", None, b["name"])
                    return self._send(200, {"ok": True, "removed": True})
                how = set_secret(b["name"], b["value"])
                store.activity("user", "secret_set", None, "%s via %s" % (b["name"], how))
                return self._send(200, {"ok": True, "storage": how})
            self._send(404, {"error": "not found"})

        def do_PUT(self):
            if not self._auth():
                return
            if self.path == "/settings":
                b = self._body()
                for k, v in b.items():
                    if k == "mode" and v not in MODES:
                        return self._send(400, {"error": "mode must be ask|auto|bypass"})
                    if k == "provider" and v not in PROVIDERS and v != "fake":
                        return self._send(400, {"error": "provider must be one of " + ", ".join(PROVIDERS)})
                    if k == "secrets":
                        continue
                    if k == "ai.enabled":
                        v = "true" if str(v).lower() in ("true", "1", "on", "yes") else "false"
                        notify(APP, "System-Wide AI is now %s" % ("ON" if v == "true" else "OFF"))
                    store.set_setting(k, v)
                    store.activity("user", "setting", None, "%s=%s" % (k, v if "pass" not in k else "***"))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_PATCH(self):
            if not self._auth():
                return
            m = re.match(r"^/tasks/(\d+)$", self.path)
            if m:
                b = self._body()
                t = store.one("SELECT * FROM tasks WHERE id=?", m.group(1))
                if not t:
                    return self._send(404, {"error": "no such task"})
                store.q("UPDATE tasks SET title=COALESCE(?,title), request=COALESCE(?,request), mode=COALESCE(?,mode), updated=? WHERE id=?",
                        b.get("title"), b.get("request"), b.get("mode"), time.time(), t["id"])
                store.activity("user", "task_edited", t["id"], json.dumps(b)[:300])
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            if not self._auth():
                return
            m = re.match(r"^/tasks/(\d+)$", self.path)
            if m:
                tid = int(m.group(1))
                agent.cancel_task(tid)
                for tbl in ("steps", "approvals", "watches", "questions"):
                    store.q("DELETE FROM %s WHERE task_id=?" % tbl, tid)
                store.q("DELETE FROM tasks WHERE id=?", tid)
                store.activity("user", "task_deleted", tid)
                return self._send(200, {"ok": True})
            m = re.match(r"^/watches/(\d+)$", self.path)
            if m:
                store.q("UPDATE watches SET status='cancelled' WHERE id=?", m.group(1))
                store.activity("user", "watch_cancelled", None, m.group(1))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
    return H


def main():
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    os.makedirs(CONF_DIR, exist_ok=True)
    tok_path = os.path.join(RUN_DIR, "token")
    if os.path.exists(tok_path):
        token = open(tok_path).read().strip()
    else:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        fd = os.open(tok_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, token.encode())
        os.close(fd)
    with open(os.path.join(RUN_DIR, "port"), "w") as f:
        f.write(str(PORT))
    store = Store(DB_PATH)
    agent = Agent(store)
    Watcher(store, agent).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(store, agent, token))
    srv.daemon_threads = True
    LOG("fabric-agentd listening on 127.0.0.1:%d db=%s mode=%s provider=%s" % (PORT, DB_PATH, store.setting("mode", "auto"),
        os.environ.get("FABRIC_AGENT_PROVIDER") or store.setting("provider", "claude")))
    store.activity("system", "service_start", None, "fabric-agentd")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
