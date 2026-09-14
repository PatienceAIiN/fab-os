#!/usr/bin/env python3
"""fabos-agentd — the Fab OS autonomous agent service (per-user systemd service).

AI proposes. Deterministic policy verifies (risk classes + permission mode + approvals).
The OS executes. Everything is recorded (tasks, steps, approvals, watches, activity) in SQLite.

HTTP API on 127.0.0.1:8790 (bearer token in $XDG_RUNTIME_DIR/fabos-agent/token, 0600):
  GET  /health /status /settings /tasks /tasks/{id} /approvals/pending /watches /activity
  POST /tasks {request,title?,mode?,parent_id?}   POST /tasks/{id}/cancel|retry|answer   PATCH/DELETE /tasks/{id}
  parent_id threads a follow-up into an existing chat: the daemon prepends a short context (earlier requests + outcomes)
  to the request, separated by FOLLOWUP_MARK, so the model has continuity and UIs can show only the user's own words.
  POST /approvals/{id} {decision}      PUT  /settings {key: value,...}         POST /secrets {name,value}
  GET  /approvals/pending?task_id=N    (optional filter; every item carries its task_id)
  POST /providers/test {provider, api_key?, base_url?, model?} -> {ok, latency_ms, detail, models_sample?}
       a real, lightweight authenticated call to the provider (its model list); 401/403 = "key rejected",
       network failure = "cannot reach provider". Keys are never logged.
  POST /speech/transcribe {audio_b64, format} -> {ok, text, backend}     POST /speech/say {text} -> {ok, audio_b64, format, backend}
       cloud speech through the configured provider (OpenAI or Gemini); other providers answer ok=false so the caller
       falls back to the offline engine (fabos-voice).
  POST /mail/test {provider, address, password?, smtp_host?...} -> {ok, smtp:{ok,detail}, imap:{ok,detail}, latency_ms}
       a real SMTP AUTH + IMAP LOGIN with the user's own account (15 s each); "wrong password / app password required"
       (535, AUTHENTICATIONFAILED) is told apart from "cannot reach". Passwords are never logged.
  POST /mail/oauth/start {provider: gmail}  GET /mail/oauth/status?flow_id=  — "Sign in with Google" (OAuth 2.0 loopback +
       PKCE, XOAUTH2 for SMTP/IMAP); only offered when /etc/fabos/google-oauth.env carries the owner's Desktop client id.
  POST /tasks/{id}/feedback {rating: good|bad}      DELETE /watches/{id}
Providers: Claude (Anthropic), OpenAI, Google Gemini, DeepSeek, or any OpenAI-compatible chat endpoint (local llama-server).
Mail: the user's OWN account (Gmail, Outlook/Hotmail, Yahoo, Zoho, iCloud presets, or any IMAP/SMTP server) — settings
mail.provider / mail.address / mail.from_name, secret mail_password (an app password where the provider requires one) or
the Google refresh token mail_oauth_refresh. The feedback relay (fabos-feedback) is a separate channel and is not used here.
Every tool step carries a one-sentence "narration" (Indian English) that UIs display and the voice daemon speaks.
FABOS_AGENT_PROVIDER=fake runs a scripted provider for tests.
"""
import base64, hashlib, io, json, os, re, secrets as _secrets, shlex, socket, sqlite3, subprocess, sys, threading, time, uuid, wave, urllib.request, urllib.error, urllib.parse
import smtplib, imaplib, email, email.utils, email.header, datetime as _dt
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone

APP = "Fab OS"
PORT = int(os.environ.get("FABOS_AGENT_PORT", "8790"))
HOME = os.path.expanduser("~")
DATA_DIR = os.environ.get("FABOS_AGENT_DATA", os.path.join(os.environ.get("XDG_DATA_HOME", HOME + "/.local/share"), "fabos"))
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "fabos", "agent")
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent")
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
            self.migrate()

    def migrate(self):
        """Schema additions for databases created by earlier releases (ALTER TABLE only when the column is missing)."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(steps)").fetchall()}
        for col in ("narration", "narration_done"):
            if col not in cols:
                self.db.execute("ALTER TABLE steps ADD COLUMN %s TEXT" % col)
        self.db.commit()

    def columns(self, table):
        return [r["name"] for r in self.db.execute("PRAGMA table_info(%s)" % table).fetchall()]

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

    def step(self, task_id, kind, name="", inp="", out="", risk="", decision="", narration=""):
        cur = self.q("INSERT INTO steps(task_id,ts,kind,name,input,output,risk,decision,narration) VALUES(?,?,?,?,?,?,?,?,?)",
                     task_id, time.time(), kind, name, str(inp)[:20000], str(out)[:40000], risk, decision, narration or None)
        self.q("UPDATE tasks SET updated=? WHERE id=?", time.time(), task_id)
        return cur.lastrowid

    def finish_step(self, step_id, out, narration_done=""):
        """Record a tool step's result together with its spoken confirmation."""
        self.q("UPDATE steps SET output=?, narration_done=? WHERE id=?", str(out)[:40000], narration_done or None, step_id)


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
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string", "description": "working directory (default: home)"}, "timeout_s": {"type": "integer", "default": 120},
                                                       "as_root": {"type": "boolean", "default": False, "description": "run as root for system administration (apt, systemctl, modprobe, sysctl, /etc, disks). CRITICAL risk: requires the user's approval unless their mode is bypass. Do not write sudo in the command."}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file (UTF-8). Returns up to 200 KB.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Create or overwrite a text file (creates parent directories). Use append=true to append.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean", "default": False}}, "required": ["path", "content"]}},
    {"name": "list_dir", "description": "List a directory.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "open_app",
     "description": "Open a desktop application, file or URL in the user's graphical session (e.g. app='kate' (Fab Editor) with args=['/path/file.txt'], app='dolphin' (Fab Files), app='brave-browser' (Brave) with args=['https://...'], app='libreoffice' with args=['--writer'], app='xdg-open' with args=['https://...']). Returns immediately.",
     "input_schema": {"type": "object", "properties": {"app": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, "required": ["app"]}},
    {"name": "type_text",
     "description": "Type text into the currently focused window through the Wayland virtual keyboard, so the user watches it appear. Use it right after open_app when the user asked you to write or compose something (note, letter, mail body, document, code) — see 'Show your work'. Save afterwards with write_file to the same path.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "press_enter": {"type": "boolean", "default": False}, "delay_ms": {"type": "integer", "default": 800, "description": "wait before typing so the window can focus (use 1500 right after open_app)"}}, "required": ["text"]}},
    {"name": "send_email",
     "description": "Send an email from the user's own mail account (Gmail, Outlook, Yahoo, Zoho, iCloud or any IMAP/SMTP account they signed in with under Fab AI Controls → Settings → Mail). If mail is not configured, tell the user to sign in there; never ask for their password yourself.",
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


# ---- catastrophic commands: always CRITICAL, whatever the mode says about HIGH.
# Top-level system directories and the home directory itself: a recursive delete / chmod / chown / truncate here wipes
# the system or the user's data. Ordinary project paths (~/Projects/x, ./build) stay at HIGH.
SYSTEM_DIRS = {"/", "/home", "/root", "/usr", "/etc", "/var", "/boot", "/opt", "/bin", "/sbin", "/lib", "/lib64", "/srv", "/dev", "/proc", "/sys"}
SENSITIVE_PATHS = ("~/.ssh", "~/.gnupg", "~/.config/fabos", "/etc/sudoers")     # credentials, keys and the agent's own secrets
BLOCK_DEVICE = r"/dev/(sd[a-z]|nvme\d|vd[a-z]|mmcblk\d)"
WIPE_CMDS = {"shred", "wipe", "srm"}          # secure-delete tools: irrecoverable, so CRITICAL — but only as the command word, never as a plain word in an argument


def _norm_target(tok):
    """Normalise a path token as bash would (quotes, ~, $HOME). Returns (path, bare_wildcard)."""
    t = tok.strip().strip("'\"")
    if not t or t.startswith("-"):
        return None, False
    t = re.sub(r"^(\$\{HOME\}|\$HOME)", HOME, t)
    if t == "~" or t.startswith("~/"):
        t = HOME + t[1:]
    if t in (".", "..", "./", "../", "*", "./*", ".*"):
        return t.rstrip("/") or t, True
    wildcard = t.endswith("/*")
    if wildcard:
        t = t[:-2] or "/"
    return (os.path.normpath(t) if t.startswith("/") else t), wildcard


def _top_dir(path):
    """'/etc/passwd' -> '/etc'; '/' -> '/'."""
    parts = path.split("/")
    return "/" + parts[1] if len(parts) > 1 and parts[1] else "/"


def _is_fatal_target(tok):
    """True when tok names the home directory, a top-level system directory, the cwd (which defaults to home) or a bare wildcard."""
    path, wildcard = _norm_target(tok)
    if path is None:
        return False
    if wildcard and path in (".", "..", "*", ".*"):
        return True
    if path in (".", "..", "*"):
        return True
    return path == HOME or path in SYSTEM_DIRS


def _segments(command):
    """Split a shell line into simple commands (on ; && || | and newlines) and tokenise each."""
    out = []
    for seg in re.split(r"\|\||&&|[;|\n]", command):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in ("sudo", "doas", "env", "nice", "nohup", "time", "command", "builtin", "exec")):
            toks = toks[1:]
        if toks:
            out.append(toks)
    return out


def catastrophic(command):
    """Deterministic reasons a shell command is CRITICAL whatever the permission mode. Returns the reason or None."""
    c = command
    if re.search(r":\s*\(\s*\)\s*\{|:\s*\|\s*:\s*&", c):
        return "fork bomb"
    if re.search(r">\s*" + BLOCK_DEVICE + r"|\bof=" + BLOCK_DEVICE, c):
        return "writes directly to a disk device"
    if re.search(r"\bhistory\s+-c\b", c):
        return "clears the shell history"
    if re.search(r"\bcrontab\s+-r\b", c):
        return "removes every scheduled job"
    if re.search(r"\bgit\s+push\b[^;&|]*(\s--force(-with-lease)?\b|\s-f\b)", c):
        return "force-push rewrites remote history"
    for toks in _segments(c):
        cmd = os.path.basename(toks[0])
        args = toks[1:]
        flags = [a for a in args if a.startswith("-")]
        targets = [a for a in args if not a.startswith("-")]
        recursive = any(f in ("--recursive", "-R") or (f.startswith("-") and not f.startswith("--") and "r" in f.lower()) for f in flags)
        # secure-delete tools as the command word, via xargs/parallel, or as find's -exec/-ok action ('grep -i wipe notes.txt' is not one)
        if cmd in WIPE_CMDS or (cmd in ("xargs", "parallel") and any(os.path.basename(a) in WIPE_CMDS for a in targets)) or \
                (cmd == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args) and os.path.basename(args[i + 1]) in WIPE_CMDS for i, a in enumerate(args))):
            return "irrecoverable data wipe"
        # copying onto a disk device (tee: any target; cp/mv/rsync/install: the destination) is as final as '> /dev/sda'
        dests = targets if cmd == "tee" else (targets[-1:] if cmd in ("cp", "mv", "rsync", "install") and len(targets) >= 2 else [])
        if any(re.match(BLOCK_DEVICE, d.strip("'\"")) for d in dests):
            return "writes directly to a disk device"
        if cmd == "rm" and recursive and any(_is_fatal_target(t) for t in targets):
            return "recursive delete of the home directory, a system directory or everything (wildcard)"
        if cmd == "find" and (any(a == "-delete" for a in args) or any(a == "-exec" and i + 1 < len(args) and args[i + 1] == "rm" for i, a in enumerate(args))):
            first_pred = next((i for i, a in enumerate(args) if a.startswith("-") or a in ("(", "!")), len(args))
            roots = args[:first_pred] or ["."]           # find's start points come before the first predicate; none = cwd
            if any(_is_fatal_target(r) for r in roots):
                return "recursive delete (find) of the home directory, a system directory or everything"
        if cmd in ("chmod", "chown", "chgrp") and recursive and any(_is_fatal_target(t) for t in (targets[1:] or targets)):
            return "recursive permission change on the home directory or a system directory"
        if cmd == "truncate" and any(f.startswith("-s") for f in flags):
            for t in targets:
                path, _ = _norm_target(t)
                if path and path.startswith("/") and not path.startswith(HOME + os.sep) and path != HOME and _top_dir(path) in SYSTEM_DIRS - {"/"}:
                    return "truncates a system file"
    return None


def touches_sensitive(text):
    """True when text references the SSH / GnuPG directories, the agent's own configuration or the sudoers file."""
    if not text:
        return False
    t = str(text)
    pats = [r"(~|\$\{?HOME\}?|/home/[^/\s'\"]+|%s)/\.ssh(/|\b)" % re.escape(HOME), r"(~|\$\{?HOME\}?|/home/[^/\s'\"]+|%s)/\.gnupg(/|\b)" % re.escape(HOME),
            r"(~|\$\{?HOME\}?|/home/[^/\s'\"]+|%s)/\.config/fabos(/|\b)" % re.escape(HOME), r"/etc/sudoers(\.d)?(/|\b)"]
    return any(re.search(p, t) for p in pats)


def classify(tool, inp):
    """Deterministic risk classification. Returns (RISK, reason)."""
    if tool in ("write_file", "type_text", "run_shell"):
        probe = inp.get("path") if tool == "write_file" else (inp.get("text") if tool == "type_text" else inp.get("command"))
        if touches_sensitive(probe):
            return "CRITICAL", "touches credentials or the agent's own configuration (~/.ssh, ~/.gnupg, ~/.config/fabos, /etc/sudoers)"
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
        if inp.get("as_root"):
            return "CRITICAL", "runs as root"
        fatal = catastrophic(c)
        if fatal:
            return "CRITICAL", fatal
        worst, why = "MEDIUM", "runs a command"
        for pat, risk, reason in DANGER:
            if re.search(pat, c, re.I) and RISK.index(risk) > RISK.index(worst):
                worst, why = risk, reason
        if worst == "MEDIUM" and READ_ONLY.match(c) and not re.search(r"[|>;&]|\$\(|\s-(delete|exec|execdir|ok|okdir)\b", c):
            return "LOW", "read-only command"
        return worst, why
    return "HIGH", "unknown tool"


def notify(title, message, urgency="normal"):
    for cmd in (["notify-send", "-a", APP, "-i", "fabos", "-u", urgency, title, message],
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

    def run_as_root(self, task_id, command, cwd, timeout):
        """Root execution through /usr/lib/fabos/agent/rootexec. The policy gate has already allowed this CRITICAL step;
        a one-time authorization record (owned by the user, 10-minute validity) is consumed by the sudoers-whitelisted
        executor, so nothing runs as root that the daemon did not explicitly authorize."""
        authz_dir = os.path.join(RUN_DIR, "authz")
        os.makedirs(authz_dir, mode=0o700, exist_ok=True)
        aid = uuid.uuid4().hex
        rec = {"command": command, "cwd": cwd if cwd.startswith("/") else "/", "timeout_s": timeout, "task_id": task_id, "created": time.time()}
        p = os.path.join(authz_dir, aid + ".json")
        with open(p, "w") as f:
            json.dump(rec, f)
        os.chmod(p, 0o600)
        try:
            r = subprocess.run(["sudo", "-n", "/usr/lib/fabos/agent/rootexec", aid], capture_output=True, text=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return {"error": "timeout after %ss (root)" % timeout}
        finally:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        if r.returncode != 0 and not r.stdout.strip().startswith("{"):
            return {"error": "root execution unavailable: %s" % (r.stderr.strip() or "sudo refused (user not in the sudo group, or the fabos-agent sudoers rule is missing)")}
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {"exit_code": r.returncode, "stdout": r.stdout[-30000:], "stderr": r.stderr[-10000:]}
        self.store.activity("agent", "root_exec", task_id, command[:300])
        return out

    def t_run_shell(self, task_id, inp):
        cwd = os.path.expanduser(inp.get("cwd") or HOME)
        to = min(int(inp.get("timeout_s") or 120), 1800)
        if inp.get("as_root"):
            return self.run_as_root(task_id, inp["command"], cwd, to)
        # own session/process group, registered on the task: cancelling the task kills the whole tree (see Agent.cancel_task).
        # Output goes to temp files, not pipes: a server the agent deliberately leaves running in the background
        # ("python3 -m http.server &") inherits stdout, and with pipes the step would block until its timeout and then
        # kill_tree() would take the server down with it. With files the step ends when bash exits; the background
        # process lives on (it is still in the task's process group, so cancelling the task still stops it).
        import tempfile
        fo = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace"); fe = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        p = subprocess.Popen(["bash", "-lc", inp["command"]], cwd=cwd, stdout=fo, stderr=fe, text=True,
                             env=self.agent.session_env(), start_new_session=True)
        self.agent.procs.setdefault(task_id, set()).add(p)
        try:
            p.wait(timeout=to)
            fo.seek(0); fe.seek(0); out, err = fo.read(), fe.read()
            if task_id in self.agent.cancel:
                return {"error": "cancelled by user"}
            return {"exit_code": p.returncode, "stdout": out[-30000:], "stderr": err[-10000:]}
        except subprocess.TimeoutExpired:
            kill_tree(p)
            return {"error": "timeout after %ss (process killed)" % to}
        finally:
            self.agent.procs.get(task_id, set()).discard(p)
            fo.close(); fe.close()

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
        names = sorted(os.listdir(p))
        ents = []
        for n in names[:500]:
            fp = os.path.join(p, n)
            ents.append({"name": n, "dir": os.path.isdir(fp), "size": os.path.getsize(fp) if os.path.isfile(fp) else None})
        out = {"path": p, "total": len(names), "entries": ents}
        if len(names) > 500:
            out["truncated"] = True
        return out

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

    # ---- mail: the user's own account (presets for Gmail / Outlook / Yahoo / Zoho / iCloud, or any IMAP/SMTP server)
    def _mail(self):
        cfg = mail_config(self.store)
        if not mail_ready(self.store, cfg):
            raise RuntimeError(MAIL_NOT_CONFIGURED)
        return cfg

    def t_send_email(self, task_id, inp):
        cfg = self._mail()
        msg = EmailMessage()
        sender = cfg["address"]
        msg["From"] = email.utils.formataddr((cfg["from_name"], sender)) if cfg.get("from_name") else sender
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
        with smtp_connect(cfg, timeout=60) as srv:
            try:
                smtp_login(srv, cfg)
            except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:
                raise RuntimeError(mail_login_error(cfg, e))
            srv.send_message(msg)
        self.store.activity("agent", "email_sent", task_id, "via=%s to=%s subject=%s" % (cfg["provider"], inp["to"], inp["subject"]))
        return {"sent": True, "message_id": msg["Message-ID"], "to": inp["to"], "via": cfg["provider"]}

    def _imap_search(self, cfg, from_contains=None, subject_contains=None, since_hours=48, unseen_only=False, limit=10, include_body=True, seen_uids=None):
        M = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg["imap_port"] or 993), timeout=60)
        try:
            imap_login(M, cfg)
        except (MailLoginDisabled, imaplib.IMAP4.error) as e:
            why = mail_login_error(cfg, e)
            if why:
                raise RuntimeError(why)
            raise
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
            raise RuntimeError("This mail account has no IMAP server set (Fab AI Controls → Settings → Mail → Advanced), so the inbox cannot be read.")
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
- Show your work. When the user asks you to WRITE or COMPOSE something they will read (a note, a letter, a mail body, a document, code they will look at), do it where they can watch, in this order: (1) open_app the right app first — kate (Fab Editor) with the target file path for notes, text and code; libreoffice --writer for documents; a mail body is composed in Fab Editor too; (2) type_text the content so it appears on screen (delay_ms 1500 right after opening); (3) save with write_file to the same path (there is no keyboard-shortcut tool, so say "saving the file for you"); (4) then do the follow-up — send_email, run the code — and narrate every step in one short sentence. Pure file or system operations (copy, rename, count, install, configure) need no window: do them directly.
- For coding tasks: create a project under ~/Projects/<name>, write the code and tests, run them with run_shell, fix failures, then summarise what was built and how it was verified.
- For email: send_email to send (the user's own account, signed in under Settings → Mail; if it is not configured say so and stop, never ask for a password); check_email to read. To wait for a reply after sending, call schedule_watch(kind="email_reply", from_contains=<recipient address>, ...) so the user is notified and, if asked, a follow-up task runs automatically. Then finish; never poll in a loop.
- Applications: every installed app (system, Flatpak, user) is available to you the moment it is installed. Use list_apps to discover names, launch commands and supported file types, open_app to launch them (kate = Fab Editor, dolphin = Fab Files, konsole = Fab Terminal, brave-browser = Brave for the web), and their CLI or D-Bus interfaces via run_shell (KDE apps: qdbus6 / kdialog / kioclient). Installed now ({app_count} apps): {app_names}.
- System administration (packages, services, kernel modules, sysctl, disks, files under /etc or /usr) is done with run_shell(as_root=true). It is CRITICAL risk: the user approves it unless their mode is bypass. Never put sudo in the command; as_root already runs it as root. Verify the result afterwards (e.g. systemctl is-active, dpkg -s, lsmod).
- Every tool call passes a deterministic policy check (risk LOW/MEDIUM/HIGH/CRITICAL against the user's permission mode). A denied call returns an error: respect it, explain, and find an allowed way or stop.
- Never fabosate results. Report exactly what happened, including partial failures. Keep the final message short: what was done, where outputs are, what the user should look at.
- Current user: {user}. Home: {home}. Date/time: {now}. Permission mode: {mode}."""

# Persona (setting ui.persona, default "indian-english"; "off" disables). Appended to the system prompt so the agent
# talks like a warm colleague and narrates what it is about to do — the same voice the step narration uses.
PERSONA_PROMPTS = {
    "indian-english": ("Speak like a warm, helpful colleague from India using natural Indian English (e.g. 'Sure, I will do that right away', "
                       "'Done, I have opened Fab Files for you', 'Shall I go ahead?'); before every action say in one short sentence what you are "
                       "about to do; after finishing confirm what was done and ask if anything else is needed; keep replies short, human and "
                       "friendly; never robotic, never use markdown tables in spoken-style replies."),
}
PERSONA_DEFAULT = "indian-english"


def persona_prompt(store):
    """The persona sentence for the current ui.persona setting ('' when the persona is off or unknown)."""
    p = (store.setting("ui.persona", PERSONA_DEFAULT) or "").strip().lower()
    if p in ("", "off", "none", "false", "0"):
        return ""
    return PERSONA_PROMPTS.get(p, "")


def build_system_prompt(store, mode, apps):
    names = ", ".join(sorted(a["name"] for a in apps)[:120])
    system = SYSTEM_PROMPT.format(app=APP, user=os.environ.get("USER", "user"), home=HOME, app_count=len(apps), app_names=names,
                                  now=datetime.now().strftime("%Y-%m-%d %H:%M %Z"), mode=mode)
    persona = persona_prompt(store)
    return system + ("\n- " + persona if persona else "")


# ---- narration: one human sentence per tool step (Indian English), filled deterministically at insert time and on
# completion. UIs show it under the step; the voice daemon speaks it. Never includes raw commands unless ui.show_raw.
def _base(path):
    return os.path.basename(str(path or "").rstrip("/")) or str(path or "")


def _app_name(inp):
    app = str(inp.get("app") or "").split("/")[-1]
    return {"kate": "Fab Editor", "dolphin": "Fab Files", "konsole": "Fab Terminal", "xdg-open": "the default app", "open": "the default app",
            "brave-browser": "Brave", "brave": "Brave", "libreoffice": "LibreOffice", "vlc": "VLC", "plasma-discover": "Fab Software", "gwenview": "Fab Photos",
            "okular": "Fab Documents", "kcalc": "Fab Calculator", "spectacle": "Fab Screenshot", "systemsettings": "Fab Settings"}.get(app, app or "the app")


def _host(url):
    try:
        return urllib.parse.urlparse(str(url)).netloc or str(url)
    except Exception:
        return str(url)


def narration_for(name, inp, show_raw=False):
    """What the agent says before a tool step runs."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "open_app":
        return "Opening %s for you now." % _app_name(inp)
    if name == "type_text":
        return "Typing that in now."
    if name == "run_shell":
        cmd = str(inp.get("command") or "").strip().split("\n")[0]
        if show_raw and cmd:
            return "Running: %s" % cmd[:120]
        return "Running a command for you." if not inp.get("as_root") else "Running an administrator command for you."
    if name == "write_file":
        return "Saving the file %s." % _base(inp.get("path"))
    if name == "read_file":
        return "Having a look at %s." % _base(inp.get("path"))
    if name == "list_dir":
        return "Checking the folder %s." % _base(inp.get("path"))
    if name == "web_fetch":
        return "Fetching %s for you." % _host(inp.get("url"))
    if name == "send_email":
        return "Sending the mail to %s." % (inp.get("to") or "the recipient")
    if name == "check_email":
        return "Checking your mail now."
    if name == "notify_user":
        return "Letting you know."
    if name == "ask_user":
        return "I need to ask you something."
    if name == "schedule_watch":
        return "I will keep a watch on that."
    if name == "list_apps":
        return "Checking which apps are installed."
    return "Working on it."


def narration_done_for(name, inp, out, error=False):
    """What the agent says once a tool step has finished (or failed)."""
    inp = inp if isinstance(inp, dict) else {}
    if error:
        reason = (out.get("error") if isinstance(out, dict) else str(out)) or "something went wrong"
        reason = str(reason).strip().split("\n")[0]
        if len(reason) > 90:
            reason = reason[:87] + "…"
        return "Sorry, that did not work: %s" % reason
    if name == "open_app":
        return "Done, %s is open." % _app_name(inp)
    if name == "type_text":
        return "Typed it in."
    if name == "run_shell":
        return "That command finished."
    if name == "write_file":
        return "Saved %s." % _base(inp.get("path"))
    if name == "read_file":
        return "Read %s." % _base(inp.get("path"))
    if name == "list_dir":
        return "Checked %s." % _base(inp.get("path"))
    if name == "web_fetch":
        return "Fetched %s." % _host(inp.get("url"))
    if name == "send_email":
        return "Sent the mail to %s." % (inp.get("to") or "the recipient")
    if name == "check_email":
        return "Checked your mail."
    if name == "notify_user":
        return "Notified you."
    if name == "ask_user":
        return "Thank you for the answer."
    if name == "schedule_watch":
        return "The watch is set; I will tell you when it happens."
    if name == "list_apps":
        return "Got the list of apps."
    return "Done."


def approval_narration(name, inp):
    """Spoken while the step waits for the user's permission."""
    inp = inp if isinstance(inp, dict) else {}
    summary = {"run_shell": "run a command as administrator" if inp.get("as_root") else "run a command", "write_file": "write the file %s" % _base(inp.get("path")),
               "read_file": "read %s" % _base(inp.get("path")), "list_dir": "look inside %s" % _base(inp.get("path")), "open_app": "open %s" % _app_name(inp),
               "type_text": "type into the focused window", "send_email": "send a mail to %s" % (inp.get("to") or "someone"), "check_email": "check your mail",
               "schedule_watch": "set up a background watch", "web_fetch": "fetch %s" % _host(inp.get("url")), "notify_user": "show a notification",
               "ask_user": "ask you a question", "list_apps": "look up installed apps"}.get(name, (name or "do something").replace("_", " "))
    return "This needs your permission: %s. Shall I go ahead?" % summary


# Provider presets. All non-Claude providers speak the OpenAI-compatible chat API with tool calling.
PROVIDERS = {
    "claude": {"label": "Anthropic (Claude)", "secret": "claude_api_key", "model": "claude-opus-5", "help": "Paste an API key from your Anthropic account."},
    "gemini": {"label": "Google Gemini", "secret": "gemini_api_key", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-pro",
               "help": "Paste an API key from Google AI Studio."},
    "openai": {"label": "OpenAI", "secret": "openai_api_key", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1", "help": "Paste an API key from your OpenAI account."},
    "deepseek": {"label": "DeepSeek", "secret": "deepseek_api_key", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "help": "Paste an API key from the DeepSeek platform."},
    "local": {"label": "Local model", "secret": "local_api_key", "base_url": "http://127.0.0.1:8080/v1", "model": "local",
              "help": "Runs on this computer (llama-server or any OpenAI-compatible endpoint). No account, no key needed."},
}
SECRET_NAMES = ("claude_api_key", "openai_api_key", "gemini_api_key", "deepseek_api_key", "local_api_key", "mail_password", "mail_oauth_refresh")
# Providers with cloud speech (transcription / text-to-speech) through the same key; the rest fall back to the offline engine
SPEECH_PROVIDERS = ("openai", "gemini")
INDIAN_ENGLISH_STYLE = "Speak in warm, natural Indian English, like a helpful colleague from India; clear and unhurried."
VOICE_DEFAULTS = {"voice.enabled": "true", "voice.wake_word": "hey fab", "voice.speak_replies": "true", "voice.offline_only": "false", "voice.cloud_voice": ""}
MAX_BODY = 25 * 1024 * 1024      # request bodies (speech audio) are capped at 25 MB
# Characters of a tool result handed back to the model (the full output is always kept in history). Local models have
# small context windows (llama-server default 4-8k tokens), so they get a much tighter default; override with the
# setting agent.tool_result_max_chars.
RESULT_LIMIT_CLOUD, RESULT_LIMIT_LOCAL = 60000, 8000
TRUNCATED_MARK = "\n...[truncated %d chars; the full output is saved in the task history]...\n"


class ContextOverflow(RuntimeError):
    """The provider rejected the request because the conversation no longer fits its context window."""


def clip(text, limit):
    """Bound text to `limit` chars keeping the head and the tail (exit codes and errors usually sit at the end)."""
    if limit <= 0 or len(text) <= limit:
        return text
    mark = TRUNCATED_MARK % (len(text) - limit)
    keep = max(limit - len(mark), 200)
    head = int(keep * 0.7)
    return text[:head] + mark + text[len(text) - (keep - head):]


def compact_messages(messages, limit):
    """Shrink every tool_result already in the conversation to `limit` chars so the next request fits the context.
    Returns the number of results that were shortened."""
    n = 0
    for m in messages:
        if m["role"] != "user" or isinstance(m["content"], str):
            continue
        for b in m["content"]:
            if b.get("type") == "tool_result" and isinstance(b.get("content"), str) and len(b["content"]) > limit:
                b["content"] = clip(b["content"], limit)
                n += 1
    return n



def kill_tree(p, grace=2.0):
    """Terminate a Popen started with start_new_session=True together with everything it spawned."""
    import signal
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        p.terminate()
    try:
        p.wait(grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            p.kill()


class _ClaudeHTTPError(Exception):
    def __init__(self, status, body):
        super().__init__("HTTP %s: %s" % (status, body[:400])); self.status = status; self.body = body


class ClaudeProvider:
    """Anthropic Messages API. Uses the official SDK when it is importable, otherwise a small built-in HTTPS client
    (the SDK's optional native deps such as jiter are not always installed) — identical behaviour either way."""
    name = "claude"
    API = "https://api.anthropic.com/v1/messages"
    result_limit = RESULT_LIMIT_CLOUD

    def __init__(self, api_key, model, fallbacks=True):
        self.key, self.model, self.fallbacks = api_key, model, fallbacks
        self.client = None
        try:
            import anthropic
            self.anthropic = anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
        except Exception as e:                       # ImportError or a missing optional dependency inside the SDK
            LOG("anthropic SDK unavailable (%s) — using the built-in HTTPS client" % e)

    def _http_create(self, kw):
        body = {k: kw[k] for k in ("model", "max_tokens", "system", "messages", "tools") if k in kw}
        body.update(kw.get("extra_body") or {})
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json", "accept": "application/json"}
        headers.update(kw.get("extra_headers") or {})
        data = json.dumps(body).encode()
        for attempt in range(4):
            req = urllib.request.Request(self.API, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                    time.sleep(2 * (attempt + 1)); continue
                raise _ClaudeHTTPError(e.code, text)
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1)); continue
                raise RuntimeError("Cannot reach the Claude API: %s" % e)

    def _create(self, kw):
        if self.client is not None:
            try:
                return self.client.messages.create(**kw).model_dump()
            except self.anthropic.BadRequestError as e:
                raise _ClaudeHTTPError(400, str(e))
        return self._http_create(kw)

    def step(self, system, messages, tools, on_usage=None):
        kw = dict(model=self.model, max_tokens=16000,
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  messages=messages, tools=tools)
        # Server-side refusal fallbacks (Opus 5 / Fable): re-run declined requests on a fallback model inside the same call.
        if self.fallbacks and self.model.startswith(("claude-opus-5", "claude-fable")):
            kw["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kw["extra_body"] = {"fallbacks": "default"}
        try:
            d = self._create(kw)
        except _ClaudeHTTPError as e:
            if e.status == 400 and ("fallbacks" in e.body or "anthropic-beta" in e.body):
                kw.pop("extra_headers", None); kw.pop("extra_body", None)
                d = self._create(kw)
            elif e.status == 401:
                raise RuntimeError("Claude rejected the API key (401). Check Settings → Providers.")
            else:
                raise RuntimeError("Claude API error: %s" % e)
        if on_usage and d.get("usage"):
            on_usage(d["usage"].get("input_tokens", 0) or 0, d["usage"].get("output_tokens", 0) or 0)
        content = [b for b in d["content"] if b.get("type") in ("text", "tool_use", "thinking", "redacted_thinking")]
        for b in content:
            b.pop("citations", None)
        return {"content": content, "stop_reason": d.get("stop_reason"), "stop_details": d.get("stop_details")}


class OpenAICompatProvider:
    """Any /v1/chat/completions endpoint with tool calling (llama-server, vLLM, other vendors)."""
    name = "openai-compatible"
    result_limit = RESULT_LIMIT_CLOUD

    def __init__(self, base_url, api_key, model, name=None, result_limit=None):
        self.base, self.key, self.model = base_url.rstrip("/"), api_key or "none", model
        if name:
            self.name = name
        if result_limit:
            self.result_limit = result_limit

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
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # surface the server's own message (llama-server/vLLM/vendors put it in {"error": {"message": ...}}) instead of a bare "400 Bad Request"
            raw = e.read().decode("utf-8", "replace")[:2000]
            try:
                err = json.loads(raw).get("error", raw)
                msg = err.get("message") if isinstance(err, dict) else str(err)
            except Exception:
                msg = raw
            msg = (msg or str(e)).strip()
            if e.code in (400, 413, 422) and re.search(r"context|too many tokens|maximum.*length|exceed", msg, re.I):
                raise ContextOverflow("%s (HTTP %d from %s)" % (msg, e.code, self.base))
            raise RuntimeError("%s provider error HTTP %d: %s" % (self.name, e.code, msg))
        except urllib.error.URLError as e:
            raise RuntimeError("%s provider unreachable at %s: %s" % (self.name, self.base, e.reason))
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
    """Deterministic scripted provider for tests and offline demos (FABOS_AGENT_PROVIDER=fake)."""
    name = "fake"

    def step(self, system, messages, tools, on_usage=None):
        first = messages[0]["content"]
        full = first if isinstance(first, str) else first[0].get("text", "")
        req = user_text(full)   # follow-ups carry a context prefix; the script keys off the user's words
        n_results = sum(1 for m in messages if m["role"] == "user" and not isinstance(m["content"], str))
        low = req.lower()
        final = None

        def tu(name, inp):
            return {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": name, "input": inp}
        mk = re.search(r"create\s+(\S+\.txt)\s+(?:next to it\s+)?with the word\s+(\w+)", req, re.I)
        if mk:
            # "create X.txt with the word W"; a follow-up like "create b.txt next to it" only works if the context of the
            # earlier turn (its request / touched files) reached the model: the directory is taken from there
            path, word = mk.group(1), mk.group(2)
            if "/" not in path:
                ctx = full.rsplit(FOLLOWUP_MARK, 1)[0] if FOLLOWUP_MARK in full else ""
                m2 = re.search(r"((?:~|/)[\w./-]*/)[\w-]+\.txt", ctx)
                path = (m2.group(1) if m2 else "~/") + path
            plan = [tu("write_file", {"path": path, "content": word + "\n"})]
            final = "Done, I have created %s with the word %s. Anything else?" % (path, word)
        elif re.search(r"\btype\s+['\"]?[^'\"]+?['\"]?\s+into\b", req, re.I) and ("editor" in low or "kate" in low):
            # ladder L1-f "type 'hello' into a new Fab Editor window": show your work = open the app FIRST, then type
            text = re.search(r"\btype\s+['\"]?([^'\"]+?)['\"]?\s+into\b", req, re.I).group(1).strip()
            plan = [tu("open_app", {"app": "kate"}), tu("type_text", {"text": text, "delay_ms": 100})]
            final = "Done, I opened Fab Editor and typed '%s' for you. Anything else?" % text
        elif re.search(r"\bwrite\b.*\bnote\b.*\b(?:send|mail)\b.*\bto\s+[\w.+-]+@[\w-]+\.[\w.]+", req, re.I):
            # ladder L2-f "write a hi note and send it to X": open Fab Editor -> type -> save -> send (that order)
            m = re.search(r"\bwrite\s+(?:a\s+|an\s+)?['\"]?(.+?)['\"]?\s+note\b", req, re.I)
            text = (m.group(1) if m else "hi").strip()
            addr = re.search(r"\bto\s+([\w.+-]+@[\w-]+\.[\w.]+)", req, re.I).group(1)
            path = "~/Documents/fabos-note.txt"
            plan = [tu("open_app", {"app": "kate", "args": [path]}), tu("type_text", {"text": text, "delay_ms": 100}),
                    tu("write_file", {"path": path, "content": text + "\n"}), tu("send_email", {"to": addr, "subject": text[:1].upper() + text[1:], "body": text})]
            final = "Done, I wrote the note in Fab Editor, saved it and sent it to %s. Anything else?" % addr
        elif "editor" in low or "kate" in low:
            m = re.search(r"write\s+['\"]?(.+?)['\"]?(\s+and\b|\s*,|\s*$)", req, re.I)
            text = (m.group(1) if m else "hi").strip()
            plan = [tu("write_file", {"path": "~/Documents/fabos-note.txt", "content": text + "\n"}),
                    tu("open_app", {"app": "kate", "args": ["~/Documents/fabos-note.txt"]}),
                    tu("run_shell", {"command": "sleep 2; pgrep -a kate | head -1; cat ~/Documents/fabos-note.txt"})]
            if "mail" in low:
                em = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", req)
                addr = em.group(0) if em else "someone@example.com"
                plan.append(tu("send_email", {"to": addr, "subject": "Note from Fab OS", "body": text}))
                plan.append(tu("schedule_watch", {"kind": "email_reply", "from_contains": addr, "notify_message": "Reply received to your Fab OS note", "interval_minutes": 2}))
        elif "fail" in low:
            plan = [tu("run_shell", {"command": "exit 3"})]
        elif "long sleep" in low:
            plan = [tu("run_shell", {"command": "sleep 45 && echo finished", "timeout_s": 120})]
        elif "background server" in low:
            # a deliberately backgrounded process that keeps stdout open: the step must return at once and the process must survive
            plan = [tu("run_shell", {"command": "sleep 37 & echo started-bg", "timeout_s": 8})]
        elif "huge output" in low:
            # emulate a small-context model: a tool result longer than 2000 chars makes the "request" overflow
            plan = [tu("run_shell", {"command": "seq 1 20000"}), tu("run_shell", {"command": "echo compacted-ok"})]
            for m in messages:
                if m["role"] == "user" and not isinstance(m["content"], str):
                    for b in m["content"]:
                        if b.get("type") == "tool_result" and len(b.get("content") or "") > 2000:
                            raise ContextOverflow("simulated: request exceeds the available context size")
        elif "as root" in low:
            plan = [tu("run_shell", {"command": "id -u; systemctl is-active sddm; sysctl -n kernel.hostname", "as_root": True})]
        elif "privileged" in low:
            plan = [tu("run_shell", {"command": "sudo -n true"})]
        else:
            plan = [tu("run_shell", {"command": "uname -a; date"})]
        if n_results < len(plan):
            return {"content": [{"type": "text", "text": "Step %d/%d" % (n_results + 1, len(plan))}, plan[n_results]], "stop_reason": "tool_use"}
        return {"content": [{"type": "text", "text": final or "Done: executed %d steps for '%s'." % (len(plan), req[:60])}], "stop_reason": "end_turn"}


# ----------------------------------------------------------------------------- chats (threads of tasks)
# A chat is a root task plus its follow-ups (tasks whose parent_id chains to the root). A follow-up's request carries a
# short context of the earlier turns so the model has continuity; FOLLOWUP_MARK separates it from what the user typed.
FOLLOWUP_MARK = "\n\nFollow-up request:\n"
FOLLOWUP_CONTEXT_TURNS = 3          # how many earlier turns are summarised into a follow-up
FOLLOWUP_CLIP_REQUEST, FOLLOWUP_CLIP_RESULT = 600, 900
# the whole context block is bounded too (same head+tail clip as tool results): cloud models get ~4 000 chars, small local ones ~1 500
FOLLOWUP_LIMIT_CLOUD, FOLLOWUP_LIMIT_LOCAL = 4000, 1500


def followup_limit(store):
    kind = os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude")
    return FOLLOWUP_LIMIT_LOCAL if kind == "local" else FOLLOWUP_LIMIT_CLOUD


def user_text(request):
    """The part of a task request the user actually typed (drops the context prefix of a follow-up)."""
    return (request or "").rsplit(FOLLOWUP_MARK, 1)[-1]


def root_task_id(store, tid):
    """Walk parent_id links up to the root of the chat (a missing parent makes the task itself the root)."""
    seen = set()
    while tid and tid not in seen:
        seen.add(tid)
        t = store.one("SELECT id, parent_id FROM tasks WHERE id=?", tid)
        if not t:
            return None
        if not t["parent_id"] or not store.one("SELECT id FROM tasks WHERE id=?", t["parent_id"]):
            return t["id"]
        tid = t["parent_id"]
    return tid


def chat_tasks(store, root_id):
    """All tasks of a chat (root first, then follow-ups in creation order)."""
    ids, out, frontier = {root_id}, [], [root_id]
    while frontier:
        rows = store.all("SELECT id FROM tasks WHERE parent_id IN (%s) ORDER BY id" % ",".join("?" * len(frontier)), *frontier)
        frontier = [r["id"] for r in rows if r["id"] not in ids]
        ids.update(frontier)
    return store.all("SELECT * FROM tasks WHERE id IN (%s) ORDER BY id" % ",".join("?" * len(ids)), *sorted(ids))


def task_touched(store, task_id):
    """Files and apps a task actually touched (from its recorded tool steps) — the concrete outcome a follow-up needs."""
    touched = []
    for s in store.all("SELECT name, input FROM steps WHERE task_id=? AND kind='tool_call' AND (decision IS NULL OR decision NOT IN ('denied','expired')) ORDER BY id", task_id):
        try:
            inp = json.loads(s["input"] or "{}")
        except ValueError:
            continue
        if s["name"] in ("write_file", "read_file", "list_dir") and inp.get("path"):
            touched.append(("wrote " if s["name"] == "write_file" else "read ") + str(inp["path"]))
        elif s["name"] == "open_app" and inp.get("app"):
            touched.append("opened " + " ".join([str(inp["app"])] + [str(a) for a in (inp.get("args") or [])[:2]]))
        elif s["name"] == "send_email" and inp.get("to"):
            touched.append("mailed " + str(inp["to"]))
    seen, out = set(), []
    for t in touched:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:8]


def followup_request(store, root_id, text, limit=None):
    """Build the request of a follow-up: a short context of the last turns of the chat, then the user's new request.
    The context block is clipped to `limit` chars (FOLLOWUP_LIMIT_CLOUD / _LOCAL by provider) so small models still fit."""
    turns = [t for t in chat_tasks(store, root_id) if not (t["title"] or "").startswith("(superseded)")][-FOLLOWUP_CONTEXT_TURNS:]
    ctx = ["Context from the earlier turns of this chat (for continuity; the new request is at the end):"]
    for t in turns:
        ctx.append("- You were asked: " + clip(user_text(t["request"]).strip(), FOLLOWUP_CLIP_REQUEST).replace("\n", " "))
        outcome = t["result"] or t["error"] or ("(still %s)" % t["status"].replace("_", " ") if t["status"] not in ("done", "failed", "cancelled") else "(no result)")
        ctx.append("  Outcome: " + clip(outcome.strip(), FOLLOWUP_CLIP_RESULT).replace("\n", " "))
        touched = task_touched(store, t["id"])
        if touched:
            ctx.append("  Touched: " + "; ".join(touched))
    block = "\n".join(ctx)
    if limit is None:
        limit = followup_limit(store)
    return clip(block, limit) + FOLLOWUP_MARK + text


# ----------------------------------------------------------------------------- the agent
class Agent:
    def __init__(self, store):
        self.store = store
        self.tools = Tools(store, self)
        self.events = {}
        self.cancel = set()
        self.procs = {}          # task id -> running shell subprocesses (killed on cancel)
        self.answers = {}
        self.sem = threading.Semaphore(int(store.setting("agent.max_parallel", "2")))
        for r in store.all("SELECT id FROM tasks WHERE status IN ('running','waiting_approval','waiting_user')"):
            store.q("UPDATE tasks SET status='failed', error='service restarted while task was running', updated=? WHERE id=?", time.time(), r["id"])
        for r in store.all("SELECT id FROM tasks WHERE status='queued'"):
            self.start(r["id"])

    def session_env(self):
        env = dict(os.environ)
        # inherit the desktop session's environment (theme, display, D-Bus, PATH) so launched apps look and behave like user-launched ones
        try:
            out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "=" in line and not line.startswith(("INVOCATION_ID", "JOURNAL_STREAM", "MANAGERPID")):
                    k, v = line.split("=", 1)
                    env.setdefault(k, v)
        except Exception:
            pass
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
            raise RuntimeError("System-Wide AI is OFF. Turn it on in Fab AI Controls (switch in the header) or run: fabos settings ai.enabled true")
        kind = os.environ.get("FABOS_AGENT_PROVIDER") or self.store.setting("provider", "claude")
        if kind == "fake":
            return FakeProvider()
        if kind == "claude":
            key = get_secret("claude_api_key") or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("No Claude API key configured. Open Fab AI Controls → Settings → AI provider and paste your key, or choose another provider (Gemini, OpenAI, DeepSeek, local model).")
            return ClaudeProvider(key, self.store.setting("claude.model", PROVIDERS["claude"]["model"]), self.store.setting("claude.fallbacks", "true") == "true")
        if kind in PROVIDERS:
            pre = PROVIDERS[kind]
            key = get_secret(pre["secret"])
            if not key and kind != "local":
                raise RuntimeError("No %s API key configured. Open Fab AI Controls → Settings → AI provider." % pre["label"])
            return OpenAICompatProvider(self.store.setting(kind + ".base_url", pre["base_url"]), key, self.store.setting(kind + ".model", pre["model"]),
                                        name=kind, result_limit=RESULT_LIMIT_LOCAL if kind == "local" else RESULT_LIMIT_CLOUD)
        raise RuntimeError("unknown provider " + kind)

    def show_raw(self):
        return self.store.setting("ui.show_raw", "false") == "true"

    def result_limit(self, prov):
        """Max chars of a tool result shown to the model (setting agent.tool_result_max_chars overrides the provider default)."""
        try:
            return int(self.store.setting("agent.tool_result_max_chars") or getattr(prov, "result_limit", RESULT_LIMIT_CLOUD))
        except ValueError:
            return getattr(prov, "result_limit", RESULT_LIMIT_CLOUD)

    def model_step(self, tid, prov, system, messages, usage):
        """One provider call. If the conversation no longer fits the model's context, compact earlier tool outputs and retry
        (twice, progressively harder) instead of failing the task with an opaque HTTP error."""
        limits = (1500, 300)  # chars per earlier tool result after the 1st and 2nd overflow
        for attempt in range(len(limits) + 1):
            try:
                return prov.step(system, messages, TOOLS, usage)
            except ContextOverflow as e:
                if attempt == len(limits):
                    raise RuntimeError("The model's context window is too small for this task even after compacting tool outputs (%s). "
                                       "Use a larger context (llama-server -c) or a smaller agent.tool_result_max_chars." % e)
                limit = limits[attempt]
                n = compact_messages(messages, limit)
                self.store.step(tid, "compact", "context", str(e)[:500], "shortened %d earlier tool outputs to %d chars and retried" % (n, limit))
                LOG("task", tid, "context overflow:", str(e)[:200], "-> compacted", n, "results to", limit)

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
        for p in list(self.procs.get(tid, ())):      # stop whatever the task is running right now (own process groups)
            threading.Thread(target=kill_tree, args=(p,), daemon=True).start()
        for a in self.store.all("SELECT id FROM approvals WHERE task_id=? AND status='pending'", tid):
            self.decide(a["id"], "denied", "system")
        for k, ev in list(self.events.items()):
            if k[0] == "q":
                ev.set()
        self.store.activity("user", "task_cancelled", tid)

    def _gate(self, tid, task, name, inp):
        risk, reason = classify(name, inp)
        mode = self.mode(task)
        narration = narration_for(name, inp, self.show_raw())
        if not self.needs_approval(risk, mode):
            sid = self.store.step(tid, "tool_call", name, json.dumps(inp)[:20000], "", risk, "auto-approved", narration=narration)
            return sid, True, risk, reason
        # while the step waits, its narration asks for permission; once allowed it says what it is doing
        sid = self.store.step(tid, "tool_call", name, json.dumps(inp)[:20000], "", risk, "", narration=approval_narration(name, inp))
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
        if a["status"] == "approved":
            self.store.q("UPDATE steps SET narration=? WHERE id=?", narration, sid)
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
            system = build_system_prompt(self.store, self.mode(task), apps)
            messages = [{"role": "user", "content": task["request"]}]
            final = ""
            limit = self.result_limit(prov)

            def usage(i, o):
                self.store.q("UPDATE tasks SET cost_in=cost_in+?, cost_out=cost_out+? WHERE id=?", i, o, tid)
            try:
                for turn in range(int(self.store.setting("agent.max_turns", "60"))):
                    if tid in self.cancel:
                        raise RuntimeError("cancelled by user")
                    resp = self.model_step(tid, prov, system, messages, usage)
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
                            done_line = "Sorry, that did not work: you did not allow it."
                        else:
                            out, err = self.tools.run(tid, c["name"], inp)
                            done_line = narration_done_for(c["name"], inp, out, error=err)
                        self.store.finish_step(sid, json.dumps(out), done_line)
                        res = {"type": "tool_result", "tool_use_id": c["id"], "content": clip(json.dumps(out), limit)}
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


# ----------------------------------------------------------------------------- provider connection check
PROVIDER_TEST_TIMEOUT = 15


def gemini_native_base(base_url):
    """The stored Gemini endpoint is the OpenAI-compatible one (…/v1beta/openai); the native API sits one level up."""
    b = (base_url or PROVIDERS["gemini"]["base_url"]).rstrip("/")
    return b[:-len("/openai")] if b.endswith("/openai") else b


def _http_json(url, headers=None, data=None, timeout=PROVIDER_TEST_TIMEOUT, method=None):
    """GET/POST returning (status, parsed-json-or-text). Raises urllib errors; the caller maps them to friendly details."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.status, json.loads(raw.decode() or "{}")
        except ValueError:
            return r.status, raw.decode(errors="replace")


def test_provider(store, kind, api_key=None, base_url=None, model=None):
    """A real, lightweight authenticated call (the provider's model list). Returns {ok, latency_ms, detail, models_sample, provider, model}.
    The key is taken from the request when given (so a typed key can be checked before it is saved), else from the stored secret."""
    if kind not in PROVIDERS:
        return {"ok": False, "detail": "unknown provider %s" % kind, "latency_ms": 0}
    pre = PROVIDERS[kind]
    key = (api_key or "").strip() or get_secret(pre["secret"]) or (os.environ.get("ANTHROPIC_API_KEY") if kind == "claude" else None) or ""
    model = (model or store.setting(kind + ".model") or pre["model"]).strip()
    base = (base_url or store.setting(kind + ".base_url") or pre.get("base_url") or "").strip().rstrip("/")
    if not key and kind != "local":
        return {"ok": False, "detail": "no API key", "latency_ms": 0, "provider": kind, "model": model}
    t0 = time.time()
    try:
        if kind == "claude":
            status, body = _http_json("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01", "accept": "application/json"})
        elif kind == "gemini" and "generativelanguage.googleapis.com" in base:
            status, body = _http_json(gemini_native_base(base) + "/models?key=" + urllib.parse.quote(key, safe=""), {"accept": "application/json"})
        else:
            headers = {"accept": "application/json"}
            if key:
                headers["Authorization"] = "Bearer " + key
            status, body = _http_json(base + "/models", headers)
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        try:
            err_body = e.read().decode(errors="replace")[:600]
        except Exception:
            err_body = ""
        if e.code in (401, 403) or (e.code == 400 and re.search(r"api[ _-]?key", err_body, re.I)):
            return {"ok": False, "detail": "key rejected", "http": e.code, "latency_ms": ms, "provider": kind, "model": model}
        if e.code == 404:
            return {"ok": False, "detail": "endpoint not found (check the base URL)", "http": e.code, "latency_ms": ms, "provider": kind, "model": model}
        return {"ok": False, "detail": "provider error HTTP %d" % e.code, "http": e.code, "latency_ms": ms, "provider": kind, "model": model}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        ms = int((time.time() - t0) * 1000)
        why = getattr(e, "reason", None) or e
        if kind == "local":
            return {"ok": False, "detail": "cannot reach provider — the local model server is not running at %s" % base, "latency_ms": ms, "provider": kind, "model": model}
        return {"ok": False, "detail": "cannot reach provider (%s)" % str(why)[:80], "latency_ms": ms, "provider": kind, "model": model}
    ms = int((time.time() - t0) * 1000)
    ids = []
    if isinstance(body, dict):
        for m in (body.get("data") or body.get("models") or []):
            mid = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
            if mid:
                ids.append(str(mid).split("/")[-1])
    out = {"ok": True, "latency_ms": ms, "detail": "Connected", "models_sample": ids[:8], "provider": kind, "model": model}
    if ids and model and model != "local" and not any(model == i or model in i for i in ids):
        out["detail"] = "Connected (the model %s is not in the provider's list — check its name)" % model
    return out


# ----------------------------------------------------------------------------- mail accounts (the user's own Gmail / Outlook / Yahoo / Zoho / iCloud / other)
# Presets checked against the providers' public documentation on 2026-09-15 (docs/decisions/ADR-0014-user-mail-accounts.md).
# "app_password": the provider refuses the normal account password for IMAP/SMTP and wants a generated app password.
MAIL_PROVIDERS = {
    "gmail": {"label": "Gmail", "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_security": "starttls", "imap_host": "imap.gmail.com", "imap_port": 993,
              "domains": ("gmail.com", "googlemail.com"), "app_password": True, "oauth": "google",
              "hint": ("Google Account → Security", "2-Step Verification (turn it on)", "App passwords → create one named Fab OS, paste the 16 characters"), "note": ""},
    "outlook": {"label": "Outlook / Hotmail", "smtp_host": "smtp-mail.outlook.com", "smtp_port": 587, "smtp_security": "starttls", "imap_host": "outlook.office365.com", "imap_port": 993,
                "domains": ("outlook.com", "outlook.in", "hotmail.com", "hotmail.co.uk", "live.com", "live.in", "msn.com"), "app_password": True, "oauth": None,
                "hint": ("Microsoft account → Security", "Advanced security options → two-step verification on", "App passwords → create a new app password"),
                "note": "Microsoft has switched off password sign-in for reading mail (IMAP LOGINDISABLED): the agent can send from this account with the app password but cannot check its inbox."},
    "yahoo": {"label": "Yahoo Mail", "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_security": "ssl", "imap_host": "imap.mail.yahoo.com", "imap_port": 993,
              "domains": ("yahoo.com", "yahoo.in", "yahoo.co.in", "yahoo.co.uk", "ymail.com", "rocketmail.com"), "app_password": True, "oauth": None,
              "hint": ("Yahoo Account Security", "Generate and manage app passwords", "Copy the 16-character password"), "note": ""},
    "zoho": {"label": "Zoho Mail", "smtp_host": "smtp.zoho.com", "smtp_port": 465, "smtp_security": "ssl", "imap_host": "imap.zoho.com", "imap_port": 993,
             "domains": ("zoho.com", "zohomail.com", "zoho.in", "zohomail.in", "zoho.eu"), "app_password": False, "oauth": None,
             "hint": ("Zoho Mail → Settings → Mail accounts → IMAP access on", "Zoho Accounts → Security → App passwords (only with two-factor on)", "Otherwise use your normal Zoho password"), "note": ""},
    "icloud": {"label": "iCloud Mail", "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls", "imap_host": "imap.mail.me.com", "imap_port": 993,
               "domains": ("icloud.com", "me.com", "mac.com"), "app_password": True, "oauth": None,
               "hint": ("account.apple.com → Sign-In and Security", "App-Specific Passwords", "Generate one named Fab OS"), "note": ""},
    "other": {"label": "Other (IMAP / SMTP)", "smtp_host": "", "smtp_port": 587, "smtp_security": "starttls", "imap_host": "", "imap_port": 993,
              "domains": (), "app_password": False, "oauth": None,
              "hint": ("Ask your provider for the SMTP and IMAP server names", "Fill them in under Advanced", "Use your normal mail password"), "note": ""},
}
MAIL_PROVIDER_ORDER = ("gmail", "outlook", "yahoo", "zoho", "icloud", "other")
MAIL_PROVIDER_FIELDS = ("label", "smtp_host", "smtp_port", "smtp_security", "imap_host", "imap_port", "app_password", "oauth", "hint", "note")
MAIL_NO_IMAP = ("none", "-", "off")     # typed into the Advanced IMAP field: "this account has no IMAP" even when the preset has one
MAIL_TEST_TIMEOUT = 15
MAIL_NOT_CONFIGURED = ("Mail is not configured. Ask the user to open Fab AI Controls → Settings → Mail and sign in with their own account "
                       "(Gmail, Outlook, Yahoo, Zoho, iCloud or another IMAP/SMTP account).")


def _int(v, default):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def mail_infer_provider(address):
    """gmail.com -> gmail, hotmail.com -> outlook, … ; anything else -> other."""
    dom = (address or "").rsplit("@", 1)[-1].lower().strip()
    for pid in MAIL_PROVIDER_ORDER:
        if dom and dom in MAIL_PROVIDERS[pid]["domains"]:
            return pid
    return "other"


def mail_config(store, provider=None, address=None, overrides=None):
    """The effective mail account: preset (mail.provider) + Advanced overrides (mail.smtp_host … stored only when they
    differ from the preset) + auth kind (password | oauth). provider / address / overrides come from a /mail/test request
    so a form that is not saved yet can be checked. mail.user is the pre-1.0-2 name of mail.address. An empty override
    means "use the preset"; mail.imap_host in MAIL_NO_IMAP ("none") means "this account has no IMAP" (sending only)."""
    s = store.setting
    ov = overrides or {}
    address = (address if address not in (None, "") else (s("mail.address") or s("mail.user") or "")).strip()
    provider = (provider or s("mail.provider") or "").strip().lower()
    if provider not in MAIL_PROVIDERS:
        provider = mail_infer_provider(address)
    pre = MAIL_PROVIDERS[provider]

    def pick(key):
        for src in (ov.get("mail." + key), ov.get(key), s("mail." + key)):
            if src not in (None, ""):
                return str(src).strip()
        return pre[key]
    cfg = {"provider": provider, "label": pre["label"], "address": address, "from_name": (ov.get("mail.from_name") or ov.get("from_name") or s("mail.from_name") or "").strip(),
           "smtp_host": pick("smtp_host"), "smtp_port": _int(pick("smtp_port"), pre["smtp_port"]), "smtp_security": str(pick("smtp_security")).lower(),
           "imap_host": pick("imap_host"), "imap_port": _int(pick("imap_port"), pre["imap_port"]),
           "auth": str(ov.get("auth") or s("mail.auth") or "password").lower(), "app_password": pre["app_password"], "oauth": pre["oauth"]}
    if cfg["imap_host"].lower() in MAIL_NO_IMAP:
        cfg["imap_host"] = ""
    if cfg["auth"] == "oauth" and not (pre["oauth"] and has_secret("mail_oauth_refresh")):
        cfg["auth"] = "password"
    return cfg


def mail_ready(store, cfg=None):
    cfg = cfg or mail_config(store)
    if not (cfg["address"] and "@" in cfg["address"] and cfg["smtp_host"]):
        return False
    return has_secret("mail_oauth_refresh") if cfg["auth"] == "oauth" else has_secret("mail_password")


def xoauth2_string(user, token):
    return "user=%s\x01auth=Bearer %s\x01\x01" % (user, token)


def smtp_connect(cfg, timeout):
    port, sec = cfg["smtp_port"], cfg["smtp_security"]
    if sec == "ssl" or (sec not in ("starttls", "none") and port == 465):
        return smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=timeout)
    srv = smtplib.SMTP(cfg["smtp_host"], port, timeout=timeout)
    srv.ehlo()
    if sec != "none":
        srv.starttls()
        srv.ehlo()
    return srv


def smtp_login(srv, cfg, password=None):
    if cfg["auth"] == "oauth":
        token = oauth_access_token(cfg)
        srv.auth("XOAUTH2", lambda challenge=None: xoauth2_string(cfg["address"], token), initial_response_ok=True)
    else:
        srv.login(cfg["address"], password if password is not None else (get_secret("mail_password") or ""))


class MailLoginDisabled(RuntimeError):
    """The IMAP server does not take passwords at all (LOGINDISABLED); sending may still work."""


def imap_login_disabled(M, cfg, err=None):
    """True when the IMAP server refuses password logins altogether (capability LOGINDISABLED without a PLAIN/LOGIN SASL
    mechanism, or the NO reply says "Basic authentication is disabled" — Outlook.com since 2024). Not a wrong password:
    no app password can fix it."""
    if cfg["auth"] == "oauth":
        return False
    caps = tuple(str(c).upper() for c in (getattr(M, "capabilities", None) or ()))
    if "LOGINDISABLED" in caps and not any(c in ("AUTH=PLAIN", "AUTH=LOGIN") for c in caps):
        return True
    return bool(err is not None and re.search(r"LOGINDISABLED|basic auth\w* (is )?(disabled|not (supported|enabled))|LOGIN (command )?(is )?(disabled|not supported)", str(err), re.I))


def imap_login(M, cfg, password=None):
    if cfg["auth"] == "oauth":
        token = oauth_access_token(cfg)
        M.authenticate("XOAUTH2", lambda _resp: xoauth2_string(cfg["address"], token).encode())
        return
    if imap_login_disabled(M, cfg):
        raise MailLoginDisabled(_mail_login_disabled(cfg))
    try:
        M.login(cfg["address"], password if password is not None else (get_secret("mail_password") or ""))
    except imaplib.IMAP4.error as e:
        if imap_login_disabled(M, cfg, e):
            raise MailLoginDisabled(_mail_login_disabled(cfg))
        raise


def mail_login_error(cfg, e):
    """A tool-facing sentence for a failed SMTP/IMAP sign-in (instead of the raw smtplib/imaplib repr), or None."""
    where = " — the user fixes it in Fab AI Controls → Settings → Mail (Check connection)."
    if isinstance(e, MailLoginDisabled):
        return str(e)
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return _mail_auth_failure(cfg) + where
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return _mail_auth_failure(cfg, closed=True) + where
    if isinstance(e, imaplib.IMAP4.error) and re.search(r"AUTHENTICATIONFAILED|Invalid credentials|LOGIN failed|authentication failed|\[AUTH\]", str(e), re.I):
        return _mail_auth_failure(cfg) + where
    return None


# ---- "Sign in with Google": OAuth 2.0 for installed apps (loopback redirect + PKCE), XOAUTH2 on IMAP/SMTP afterwards.
# Only offered when the distributor registered a Desktop OAuth client and shipped its id in GOOGLE_OAUTH_ENV; without
# it the UI shows the app-password path and says why. The refresh token is stored like every other secret.
GOOGLE_OAUTH_ENV = os.environ.get("FABOS_GOOGLE_OAUTH_ENV", "/etc/fabos/google-oauth.env")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GOOGLE_SCOPE = "https://mail.google.com/"          # the one scope Google accepts for IMAP/SMTP XOAUTH2
OAUTH_FLOW_TIMEOUT = 600
OAUTH_RESULT_GRACE = 120                    # a finished flow stays pollable this long, then OAuthFlow.flows forgets it
OAUTH_PAGE = ("<!doctype html><html><head><meta charset='utf-8'><title>Fab OS — Mail sign-in</title>"
              "<style>body{font-family:Inter,'Noto Sans',sans-serif;margin:0;display:grid;place-items:center;height:100vh;background:#F5F7FD;color:#232629}"
              "@media(prefers-color-scheme:dark){body{background:#0F1420;color:#FCFCFC}}"
              ".card{max-width:440px;padding:28px 32px;border-radius:20px;border:1px solid rgba(128,128,128,.25)}h1{font-size:20px;margin:0 0 8px}p{margin:0;line-height:1.5}</style></head>"
              "<body><div class='card'><h1>%s</h1><p>%s</p></div></body></html>")
_oauth_cache = {"token": None, "expires": 0.0, "refresh": None}
_oauth_lock = threading.Lock()


def read_env_file(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def google_oauth_client():
    """(client_id, client_secret) from GOOGLE_OAUTH_ENV, or (None, why-not)."""
    env = read_env_file(GOOGLE_OAUTH_ENV)
    cid, sec = env.get("GOOGLE_OAUTH_CLIENT_ID", ""), env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not cid:
        return None, ("Sign in with Google is not set up on this build (%s has no GOOGLE_OAUTH_CLIENT_ID; the distributor registers a Desktop OAuth client, "
                      "see ADR-0014). Use an app password instead." % GOOGLE_OAUTH_ENV)
    return (cid, sec), ""


def mail_oauth_status():
    client, why = google_oauth_client()
    return {"google": bool(client), "why": why, "env": GOOGLE_OAUTH_ENV}


def oauth_access_token(cfg):
    """A live Google access token for XOAUTH2 (refreshed from the stored refresh token, cached until it expires)."""
    if MAIL_PROVIDERS.get(cfg["provider"], {}).get("oauth") != "google":
        raise RuntimeError("Sign in with Google works only for Gmail accounts; use an app password for %s." % cfg["label"])
    refresh = get_secret("mail_oauth_refresh")
    if not refresh:
        raise RuntimeError("Not signed in with Google any more — open Fab AI Controls → Settings → Mail and sign in again.")
    client, why = google_oauth_client()
    if not client:
        raise RuntimeError(why)
    with _oauth_lock:
        if _oauth_cache["token"] and _oauth_cache["refresh"] == refresh and time.time() < _oauth_cache["expires"] - 60:
            return _oauth_cache["token"]
        data = urllib.parse.urlencode({"client_id": client[0], "client_secret": client[1], "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        try:
            _st, body = _http_json(GOOGLE_TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, data, MAIL_TEST_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401):
                raise RuntimeError("Google no longer accepts the saved sign-in (HTTP %d) — sign in again under Settings → Mail." % e.code)
            raise RuntimeError("Google's token service answered HTTP %d." % e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError("Cannot reach Google to refresh the sign-in (%s)." % str(getattr(e, "reason", e))[:80])
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise RuntimeError("Google returned no access token.")
        _oauth_cache.update(token=token, refresh=refresh, expires=time.time() + _int(body.get("expires_in"), 3600))
        return token


def open_in_session(url, env):
    try:
        subprocess.Popen(["xdg-open", url], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


class OAuthFlow:
    """One "Sign in with Google": a loopback HTTP server on 127.0.0.1 (random port) receives Google's redirect, the code is
    exchanged (PKCE) for a refresh token stored with set_secret('mail_oauth_refresh'); the signed-in address is read from
    the Gmail profile. State machine: pending -> done | error (timed out after OAUTH_FLOW_TIMEOUT)."""
    flows = {}

    def __init__(self, store, client, env=None, open_browser=True):
        self.store, self.client = store, client
        self.id = uuid.uuid4().hex[:12]
        self.state = _secrets.token_urlsafe(24)
        self.verifier = _secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        self.result = {"state": "pending", "detail": "Finish signing in in the browser window…"}
        self.created = time.time()
        self.closed = False
        self._lock = threading.Lock()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.srv.daemon_threads = True
        self.redirect = "http://127.0.0.1:%d/" % self.srv.server_address[1]
        params = {"client_id": client[0], "redirect_uri": self.redirect, "response_type": "code", "scope": GOOGLE_SCOPE, "access_type": "offline",
                  "prompt": "consent", "state": self.state, "code_challenge": challenge, "code_challenge_method": "S256"}
        self.url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
        threading.Thread(target=self.srv.serve_forever, daemon=True, name="oauth-" + self.id).start()
        self._timer = threading.Timer(OAUTH_FLOW_TIMEOUT, self._expire)
        self._timer.daemon = True
        self._timer.start()
        OAuthFlow.flows[self.id] = self
        self.browser_opened = bool(open_browser) and open_in_session(self.url, env or os.environ)

    def _expire(self):
        if self.result["state"] == "pending":
            self.result = {"state": "error", "detail": "The sign-in timed out after %d minutes. Press Sign in to try again." % (OAUTH_FLOW_TIMEOUT // 60)}
        self.finish()

    def finish(self):
        """Stop and CLOSE the loopback server (shutdown alone leaves the bound socket open) and forget the flow after
        OAUTH_RESULT_GRACE so the UI's last poll still sees the outcome. Idempotent; the work runs off the handler thread."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
        self._timer.cancel()

        def close():
            self.srv.shutdown()                  # returns once serve_forever() has exited
            self.srv.server_close()              # releases the listening socket (and joins the last handler thread)
            t = threading.Timer(OAUTH_RESULT_GRACE, OAuthFlow.flows.pop, args=(self.id, None))
            t.daemon = True
            t.start()
        threading.Thread(target=close, daemon=True, name="oauth-close-" + self.id).start()

    def _handler(self):
        flow = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/favicon"):
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ok, text = flow._callback(qs)
                body = (OAUTH_PAGE % ("Signed in to Fab OS" if ok else "Sign-in did not finish", text)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if flow.result["state"] != "pending":
                    flow.finish()
        return H

    def _callback(self, qs):
        if self.result["state"] != "pending":
            return self.result["state"] == "done", "This sign-in has already finished. You can close this tab."
        if qs.get("state", [""])[0] != self.state:
            self.result = {"state": "error", "detail": "The reply from the browser did not belong to this sign-in (state mismatch). Try again."}
            return False, self.result["detail"]
        if qs.get("error"):
            self.result = {"state": "error", "detail": "Google reported: %s. Nothing was stored." % qs["error"][0][:80]}
            return False, self.result["detail"]
        code = qs.get("code", [""])[0]
        if not code:
            self.result = {"state": "error", "detail": "Google sent no authorization code."}
            return False, self.result["detail"]
        data = urllib.parse.urlencode({"client_id": self.client[0], "client_secret": self.client[1], "code": code, "code_verifier": self.verifier,
                                       "grant_type": "authorization_code", "redirect_uri": self.redirect}).encode()
        try:
            _st, tok = _http_json(GOOGLE_TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, data, MAIL_TEST_TIMEOUT)
        except urllib.error.HTTPError as e:
            self.result = {"state": "error", "detail": "Google rejected the sign-in code (HTTP %d). Check the client id and secret in %s." % (e.code, GOOGLE_OAUTH_ENV)}
            return False, self.result["detail"]
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.result = {"state": "error", "detail": "Cannot reach Google to finish the sign-in (%s)." % str(getattr(e, "reason", e))[:80]}
            return False, self.result["detail"]
        refresh, access = (tok.get("refresh_token"), tok.get("access_token")) if isinstance(tok, dict) else (None, None)
        if not refresh:
            self.result = {"state": "error", "detail": "Google did not return a refresh token. Remove Fab OS from your Google account's third-party access and sign in again."}
            return False, self.result["detail"]
        address = ""
        try:
            _st, prof = _http_json(GOOGLE_PROFILE_URL, {"Authorization": "Bearer " + (access or "")}, None, MAIL_TEST_TIMEOUT)
            address = (prof.get("emailAddress") or "") if isinstance(prof, dict) else ""
        except Exception as e:                      # the address is a convenience; the sign-in itself succeeded
            LOG("gmail profile lookup failed", type(e).__name__)
        how = set_secret("mail_oauth_refresh", refresh)
        self.store.set_setting("mail.provider", "gmail")
        self.store.set_setting("mail.auth", "oauth")
        if address:
            self.store.set_setting("mail.address", address)
        with _oauth_lock:
            _oauth_cache.update(token=access, refresh=refresh, expires=time.time() + _int(tok.get("expires_in"), 3600))
        self.store.activity("user", "mail_signin", None, "google %s (refresh token via %s)" % (address or "(address unknown)", how))
        self.result = {"state": "done", "detail": "Signed in", "address": address}
        return True, "Signed in as %s. You can close this tab and go back to Fab AI Controls." % (address or "your Google account")

    def status(self):
        return dict(self.result, flow_id=self.id, url=self.url, browser_opened=self.browser_opened)


def _mail_auth_failure(cfg, closed=False):
    """closed=True: the server dropped the TLS connection at AUTH instead of answering 535 (Yahoo does this on 465 and
    587 for a wrong or missing app password); after a successful EHLO that is an authentication failure, not a network one."""
    if closed:
        if cfg["app_password"]:
            return "wrong password — %s closed the connection at sign-in, which it does for a wrong or missing app password" % cfg["label"]
        return "wrong password — the server closed the connection at sign-in (the login was refused)"
    if cfg["app_password"]:
        return "wrong password — %s needs an app password, not your account password" % cfg["label"]
    return "wrong password — the server refused the login"


def _mail_login_disabled(cfg):
    return ("%s has switched off password sign-in for IMAP (LOGINDISABLED) — sending with %s works, reading the inbox does not"
            % (cfg["label"], "the app password" if cfg["app_password"] else "the password"))


def _mail_net_failure(host, port, e):
    why = "timed out" if isinstance(e, (socket.timeout, TimeoutError)) else str(getattr(e, "strerror", None) or e)[:80]
    return "cannot reach %s:%d (%s)" % (host, port, why)


def test_mail(store, provider=None, address=None, password=None, overrides=None):
    """A real sign-in to the SMTP and IMAP servers of the account (both in parallel, MAIL_TEST_TIMEOUT each).
    Returns {ok, detail, smtp:{ok,detail}, imap:{ok,detail}, latency_ms, provider, address, auth}. The password is
    taken from the request when typed (so it can be checked before it is saved), else from the stored secret; it is
    never logged or returned."""
    cfg = mail_config(store, provider, address, overrides)
    base = {"provider": cfg["provider"], "address": cfg["address"], "auth": cfg["auth"], "smtp_host": cfg["smtp_host"], "imap_host": cfg["imap_host"], "latency_ms": 0}

    def early(detail):
        return dict(base, ok=False, detail=detail, smtp={"ok": False, "detail": detail}, imap={"ok": False, "detail": detail})
    if not cfg["address"] or "@" not in cfg["address"]:
        return early("enter the mail address first")
    if not cfg["smtp_host"]:
        return early("no SMTP server for this account — choose a provider or fill in Advanced")
    pw = (password or "").strip() or None
    if pw is not None:
        cfg = dict(cfg, auth="password")             # a typed password always checks the password path
        base["auth"] = "password"
    elif cfg["auth"] == "oauth":
        try:
            oauth_access_token(cfg)                  # refreshes once here; the two logins reuse the cached token
        except RuntimeError as e:
            return early(str(e))
    else:
        pw = get_secret("mail_password")
        if not pw:
            return early("no password stored — type the app password and check again")
    out = {}

    def smtp_check():
        host, port = cfg["smtp_host"], cfg["smtp_port"]
        phase = "connect"
        try:
            with smtp_connect(cfg, MAIL_TEST_TIMEOUT) as srv:
                phase = "auth"                       # EHLO (and STARTTLS) went through: what fails now is the sign-in
                smtp_login(srv, cfg, pw)
            out["smtp"] = {"ok": True, "detail": "signed in at %s:%d" % (host, port)}
        except smtplib.SMTPAuthenticationError as e:
            out["smtp"] = {"ok": False, "detail": _mail_auth_failure(cfg), "code": e.smtp_code}
        except smtplib.SMTPServerDisconnected as e:
            if phase == "auth":
                out["smtp"] = {"ok": False, "detail": _mail_auth_failure(cfg, closed=True), "closed_at_auth": True}
            else:
                out["smtp"] = {"ok": False, "detail": "mail server error: %s" % str(e)[:120]}
        except smtplib.SMTPException as e:
            text = str(e)
            auth = re.search(r"\b53[45]\b|authenticat|credential", text, re.I)
            out["smtp"] = {"ok": False, "detail": _mail_auth_failure(cfg) if auth else "mail server error: %s" % text[:120]}
        except RuntimeError as e:                    # OAuth token problems
            out["smtp"] = {"ok": False, "detail": str(e)}
        except (socket.timeout, TimeoutError, OSError) as e:
            out["smtp"] = {"ok": False, "detail": _mail_net_failure(host, port, e)}

    def imap_check():
        host, port = cfg["imap_host"], cfg["imap_port"]
        if not host:
            out["imap"] = {"ok": False, "skipped": True, "detail": "no IMAP server set — sending works, reading the inbox does not"}
            return
        try:
            M = imaplib.IMAP4_SSL(host, port, timeout=MAIL_TEST_TIMEOUT)
            try:
                imap_login(M, cfg, pw)
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
            out["imap"] = {"ok": True, "detail": "signed in at %s:%d" % (host, port)}
        except MailLoginDisabled as e:               # not a wrong password: the server takes no passwords at all (Outlook.com)
            out["imap"] = {"ok": False, "skipped": True, "login_disabled": True, "detail": str(e)}
        except imaplib.IMAP4.error as e:
            text = str(e)
            auth = re.search(r"AUTHENTICATIONFAILED|Invalid credentials|LOGIN failed|authentication failed|\[AUTH\]|Application-specific password", text, re.I)
            out["imap"] = {"ok": False, "detail": _mail_auth_failure(cfg) if auth else "mail server error: %s" % text[:120]}
        except RuntimeError as e:
            out["imap"] = {"ok": False, "detail": str(e)}
        except (socket.timeout, TimeoutError, OSError) as e:
            out["imap"] = {"ok": False, "detail": _mail_net_failure(host, port, e)}
    t0 = time.time()
    threads = [threading.Thread(target=smtp_check, daemon=True), threading.Thread(target=imap_check, daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(MAIL_TEST_TIMEOUT + 5)
    ms = int((time.time() - t0) * 1000)
    smtp = out.get("smtp") or {"ok": False, "detail": "cannot reach %s:%d (timed out)" % (cfg["smtp_host"], cfg["smtp_port"])}
    imap = out.get("imap") or {"ok": False, "detail": "cannot reach %s:%d (timed out)" % (cfg["imap_host"], cfg["imap_port"])}
    ok = bool(smtp["ok"] and (imap["ok"] or imap.get("skipped")))
    detail = ("Signed in" if imap["ok"] else "Signed in (sending only)") if ok else (smtp["detail"] if not smtp["ok"] else "IMAP: " + imap["detail"])
    return dict(base, ok=ok, detail=detail, smtp=smtp, imap=imap, latency_ms=ms)


# ----------------------------------------------------------------------------- cloud speech (through the configured provider)
SPEECH_TIMEOUT = 30


def _speech_provider(store):
    kind = os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude")
    if kind not in SPEECH_PROVIDERS:
        return None, None, None, {"ok": False, "detail": "no cloud speech for this provider", "backend": "none", "provider": kind}
    key = get_secret(PROVIDERS[kind]["secret"])
    if not key:
        return None, None, None, {"ok": False, "detail": "no API key for %s" % PROVIDERS[kind]["label"], "backend": "none", "provider": kind}
    return kind, key, (store.setting(kind + ".base_url") or PROVIDERS[kind]["base_url"]).rstrip("/"), None


def _speech_error(e, backend):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return {"ok": False, "detail": "key rejected", "http": e.code, "backend": backend}
        return {"ok": False, "detail": "provider error HTTP %d" % e.code, "http": e.code, "backend": backend}
    return {"ok": False, "detail": "cannot reach provider", "backend": backend}


def _multipart(fields, file_field, filename, content, mime):
    boundary = "----FabOS" + uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n" % (boundary, file_field, filename, mime)).encode())
    body.write(content)
    body.write(("\r\n--%s--\r\n" % boundary).encode())
    return body.getvalue(), "multipart/form-data; boundary=" + boundary


def speech_transcribe(store, audio_b64, fmt="wav"):
    """Speech to text with the configured cloud provider. Audio and keys are never logged."""
    kind, key, base, err = _speech_provider(store)
    if err:
        return err
    try:
        audio = base64.b64decode(audio_b64 or "", validate=False)
    except (ValueError, TypeError):
        return {"ok": False, "detail": "audio_b64 is not valid base64", "backend": "none"}
    if not audio:
        return {"ok": False, "detail": "no audio", "backend": "none"}
    fmt = (fmt or "wav").lower().strip(".")
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac", "m4a": "audio/mp4"}.get(fmt, "audio/wav")
    if kind == "openai":
        last = None
        for model in ("gpt-4o-mini-transcribe", "whisper-1"):
            data, ctype = _multipart({"model": model, "response_format": "json", "language": "en"}, "file", "speech." + fmt, audio, mime)
            try:
                _st, body = _http_json(base + "/audio/transcriptions", {"Authorization": "Bearer " + key, "Content-Type": ctype}, data, SPEECH_TIMEOUT)
                text = (body.get("text") if isinstance(body, dict) else str(body)) or ""
                return {"ok": True, "text": text.strip(), "backend": "openai:" + model}
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (400, 404):          # model not available on this account/endpoint: try the fallback
                    continue
                return _speech_error(e, "openai")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                return _speech_error(e, "openai")
        return _speech_error(last, "openai") if last else {"ok": False, "detail": "transcription failed", "backend": "openai"}
    # gemini: generateContent with inline audio
    model = store.setting("speech.gemini_model") or "gemini-2.5-flash"
    payload = {"contents": [{"parts": [{"text": "Transcribe exactly what is said in this audio. Return only the transcript, nothing else."},
                                        {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}}]}]}
    url = "%s/models/%s:generateContent?key=%s" % (gemini_native_base(base), model, urllib.parse.quote(key, safe=""))
    try:
        _st, body = _http_json(url, {"Content-Type": "application/json"}, json.dumps(payload).encode(), SPEECH_TIMEOUT)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return _speech_error(e, "gemini")
    text = ""
    try:
        for part in body["candidates"][0]["content"]["parts"]:
            text += part.get("text", "")
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "detail": "no transcript in the provider's reply", "backend": "gemini:" + model}
    return {"ok": True, "text": text.strip(), "backend": "gemini:" + model}


def pcm_to_wav(pcm, rate=24000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def speech_say(store, text):
    """Text to speech with the configured cloud provider, Indian-English voice instructions. Returns base64 audio."""
    kind, key, base, err = _speech_provider(store)
    if err:
        return err
    text = (text or "").strip()
    if not text:
        return {"ok": False, "detail": "no text", "backend": "none"}
    voice = (store.setting("voice.cloud_voice") or "").strip()
    if kind == "openai":
        payload = {"model": "gpt-4o-mini-tts", "voice": voice or "alloy", "input": text[:4000], "instructions": INDIAN_ENGLISH_STYLE, "response_format": "wav"}
        req = urllib.request.Request(base + "/audio/speech", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=SPEECH_TIMEOUT) as r:
                audio = r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return _speech_error(e, "openai")
        return {"ok": True, "audio_b64": base64.b64encode(audio).decode(), "format": "wav", "backend": "openai:gpt-4o-mini-tts"}
    model = "gemini-2.5-flash-preview-tts"
    payload = {"contents": [{"parts": [{"text": INDIAN_ENGLISH_STYLE + " Say exactly this:\n\n" + text[:4000]}]}],
               "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Kore"}}}}}
    url = "%s/models/%s:generateContent?key=%s" % (gemini_native_base(base), model, urllib.parse.quote(key, safe=""))
    try:
        _st, body = _http_json(url, {"Content-Type": "application/json"}, json.dumps(payload).encode(), SPEECH_TIMEOUT)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return _speech_error(e, "gemini")
    try:
        part = next(p for p in body["candidates"][0]["content"]["parts"] if "inlineData" in p or "inline_data" in p)
        blob = part.get("inlineData") or part.get("inline_data")
        pcm = base64.b64decode(blob["data"])
        mime = blob.get("mimeType") or blob.get("mime_type") or ""
    except (KeyError, IndexError, TypeError, StopIteration, ValueError):
        return {"ok": False, "detail": "no audio in the provider's reply", "backend": "gemini:" + model}
    m = re.search(r"rate=(\d+)", mime)
    rate = int(m.group(1)) if m else 24000
    wav = pcm if mime.startswith("audio/wav") else pcm_to_wav(pcm, rate)
    return {"ok": True, "audio_b64": base64.b64encode(wav).decode(), "format": "wav", "backend": "gemini:" + model}


# ----------------------------------------------------------------------------- HTTP API
def make_handler(store, agent, token):
    class H(BaseHTTPRequestHandler):
        server_version = "fabos-agentd/1.0"

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
            if n > MAX_BODY:
                raise ValueError("request body too large (limit %d MB)" % (MAX_BODY // (1024 * 1024)))
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
                prov = os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude")
                ready = prov == "fake" or prov == "local" or (prov in PROVIDERS and (has_secret(PROVIDERS[prov]["secret"]) or (prov == "claude" and bool(os.environ.get("ANTHROPIC_API_KEY")))))
                mcfg = mail_config(store)
                return self._send(200, {"mode": store.setting("mode", "auto"), "provider": prov, "provider_ready": ready, "ai_enabled": agent.ai_enabled(),
                                        "provider_label": PROVIDERS[prov]["label"] if prov in PROVIDERS else prov,
                                        "provider_model": store.setting(prov + ".model", PROVIDERS[prov]["model"]) if prov in PROVIDERS else "",
                                        "ui_show_raw": store.setting("ui.show_raw", "false") == "true",
                                        "voice": {k[len("voice."):]: store.setting(k, d) for k, d in VOICE_DEFAULTS.items()},
                                        "providers": {k: {"label": v["label"], "has_key": has_secret(v["secret"])} for k, v in PROVIDERS.items()},
                                        "mail_ready": mail_ready(store, mcfg), "mail_provider": mcfg["provider"], "mail_address": mcfg["address"], "mail_auth": mcfg["auth"], "tasks": counts,
                                        "pending_approvals": store.one("SELECT COUNT(*) n FROM approvals WHERE status='pending'")["n"],
                                        "active_watches": store.one("SELECT COUNT(*) n FROM watches WHERE status='active'")["n"],
                                        "latest": store.all("SELECT id,title,status,updated FROM tasks ORDER BY updated DESC LIMIT 3")})
            if p == "/settings":
                s = {r["key"]: r["value"] for r in store.all("SELECT * FROM settings")}
                s.setdefault("mode", "auto")
                s.setdefault("provider", "claude")
                s.setdefault("ai.enabled", "true")
                s.setdefault("ui.show_raw", "false")     # Fab AI Controls: show commands / raw tool output in chats
                s.setdefault("ui.persona", PERSONA_DEFAULT)
                for k, d in VOICE_DEFAULTS.items():
                    s.setdefault(k, d)
                for k, v in PROVIDERS.items():
                    s.setdefault(k + ".model", v["model"])
                    if "base_url" in v:
                        s.setdefault(k + ".base_url", v["base_url"])
                s.setdefault("mail.provider", "gmail")
                s.setdefault("mail.address", s.get("mail.user", ""))
                s.setdefault("mail.from_name", "")
                s.setdefault("mail.auth", "password")
                s["secrets"] = {n: has_secret(n) for n in SECRET_NAMES}
                s["providers"] = {k: {"label": v["label"], "model": v["model"], "base_url": v.get("base_url"), "help": v.get("help", ""), "secret": v["secret"]} for k, v in PROVIDERS.items()}
                s["mail_providers"] = {k: {kk: v[kk] for kk in MAIL_PROVIDER_FIELDS} for k, v in MAIL_PROVIDERS.items()}
                s["mail_provider_order"] = list(MAIL_PROVIDER_ORDER)
                s["mail_oauth"] = mail_oauth_status()
                s["mail_ready"] = mail_ready(store)
                return self._send(200, s)
            if p == "/tasks":
                return self._send(200, store.all("SELECT id,title,status,mode,created,updated,parent_id,cost_in,cost_out,substr(result,1,200) result,error,substr(request,1,400) request FROM tasks ORDER BY id DESC LIMIT ?", int(qs.get("limit", 100))))
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
                if qs.get("task_id"):
                    try:
                        tid = int(qs["task_id"])
                    except ValueError:
                        return self._send(400, {"error": "task_id must be a number"})
                    return self._send(200, store.all("SELECT a.*, t.title FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE a.status='pending' AND a.task_id=? ORDER BY a.id", tid))
                return self._send(200, store.all("SELECT a.*, t.title FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE a.status='pending' ORDER BY a.id"))
            if p == "/mail/oauth/status":
                flow = OAuthFlow.flows.get(qs.get("flow_id", ""))
                if not flow:
                    return self._send(404, {"error": "no such sign-in flow"})
                return self._send(200, flow.status())
            if p == "/watches":
                return self._send(200, store.all("SELECT * FROM watches ORDER BY id DESC LIMIT 200"))
            if p == "/activity":
                return self._send(200, store.all("SELECT * FROM activity ORDER BY id DESC LIMIT ?", int(qs.get("limit", 200))))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            p = self.path.split("?")[0]
            try:
                b = self._body()
            except ValueError as e:
                return self._send(413 if "too large" in str(e) else 400, {"error": str(e)})
            if p == "/providers/test":
                kind = b.get("provider") or store.setting("provider", "claude")
                r = test_provider(store, kind, b.get("api_key"), b.get("base_url"), b.get("model"))
                store.activity("user", "provider_test", None, "%s: %s (%s ms)" % (kind, "ok" if r.get("ok") else r.get("detail"), r.get("latency_ms", 0)))
                return self._send(200, r)
            if p == "/mail/test":
                r = test_mail(store, b.get("provider"), b.get("address"), b.get("password"), b)
                store.activity("user", "mail_test", None, "%s %s: %s (%s ms)" % (r.get("provider"), r.get("address") or "-", "ok" if r.get("ok") else r.get("detail"), r.get("latency_ms", 0)))
                return self._send(200, r)
            if p == "/mail/oauth/start":
                if (b.get("provider") or "gmail") != "gmail":
                    return self._send(200, {"ok": False, "detail": "Sign in with Google is for Gmail accounts; other providers use an app password."})
                client, why = google_oauth_client()
                if not client:
                    return self._send(200, {"ok": False, "detail": why, "configured": False})
                flow = OAuthFlow(store, client, env=agent.session_env(), open_browser=b.get("open_browser", True))
                store.activity("user", "mail_signin_started", None, "google flow %s" % flow.id)
                return self._send(200, dict(flow.status(), ok=True, configured=True))
            if p == "/speech/transcribe":
                r = speech_transcribe(store, b.get("audio_b64"), b.get("format") or "wav")
                return self._send(200, r)
            if p == "/speech/say":
                r = speech_say(store, b.get("text"))
                return self._send(200, r)
            if p == "/tasks":
                if not b.get("request", "").strip():
                    return self._send(400, {"error": "request is required"})
                if not agent.ai_enabled():
                    return self._send(403, {"error": "System-Wide AI is OFF. Turn it on in Fab AI Controls or: fabos settings ai.enabled true"})
                request, parent = b["request"], b.get("parent_id")
                if parent not in (None, "", 0):
                    try:
                        parent = root_task_id(store, int(parent))
                    except (TypeError, ValueError):
                        parent = None
                    if not parent:
                        return self._send(404, {"error": "no such parent task"})
                    request = followup_request(store, parent, b["request"])
                tid = agent.create(request, b.get("title") or b["request"].strip().split("\n")[0][:80], b.get("mode"), parent_id=parent)
                return self._send(201, {"id": tid, "status": "queued", "parent_id": parent})
            m = re.match(r"^/tasks/(\d+)/(cancel|retry|answer|feedback)$", p)
            if m:
                tid, act = int(m.group(1)), m.group(2)
                t = store.one("SELECT * FROM tasks WHERE id=?", tid)
                if not t:
                    return self._send(404, {"error": "no such task"})
                if act == "feedback":
                    rating = b.get("rating")
                    if rating not in ("good", "bad", "none"):
                        return self._send(400, {"error": "rating must be good|bad|none"})
                    store.activity("user", "feedback_" + rating, tid, (b.get("comment") or "")[:500])
                    return self._send(200, {"ok": True, "rating": rating})
                if act == "cancel":
                    agent.cancel_task(tid)
                    return self._send(200, {"id": tid, "status": "cancelled"})
                if act == "retry":
                    nid = agent.create(t["request"], t["title"], t["mode"], parent_id=t["parent_id"])
                    return self._send(201, {"id": nid, "retry_of": tid, "parent_id": t["parent_id"]})
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
                    if b["name"] == "mail_oauth_refresh":
                        store.set_setting("mail.auth", "password")
                    store.activity("user", "secret_removed", None, b["name"])
                    return self._send(200, {"ok": True, "removed": True})
                how = set_secret(b["name"], b["value"])
                if b["name"] == "mail_password":
                    store.set_setting("mail.auth", "password")           # a typed app password takes over from a Google sign-in
                store.activity("user", "secret_set", None, "%s via %s" % (b["name"], how))
                return self._send(200, {"ok": True, "storage": how})
            self._send(404, {"error": "not found"})

        def do_PUT(self):
            if not self._auth():
                return
            if self.path == "/settings":
                try:
                    b = self._body()
                except ValueError as e:
                    return self._send(413 if "too large" in str(e) else 400, {"error": str(e)})
                for k, v in b.items():
                    if k == "mode" and v not in MODES:
                        return self._send(400, {"error": "mode must be ask|auto|bypass"})
                    if k == "provider" and v not in PROVIDERS and v != "fake":
                        return self._send(400, {"error": "provider must be one of " + ", ".join(PROVIDERS)})
                    if k == "mail.provider" and str(v).lower() not in MAIL_PROVIDERS and str(v) != "":       # "" = unset: infer it from the address
                        return self._send(400, {"error": "mail.provider must be one of " + ", ".join(MAIL_PROVIDER_ORDER) + " (or empty to infer it from the address)"})
                    if k == "mail.auth" and str(v).lower() not in ("password", "oauth", ""):
                        return self._send(400, {"error": "mail.auth must be password or oauth (or empty)"})
                    if k in ("secrets", "providers", "mail_providers", "mail_provider_order", "mail_oauth", "mail_ready"):
                        continue
                    if k == "ai.enabled":
                        v = "true" if str(v).lower() in ("true", "1", "on", "yes") else "false"
                        notify(APP, "System-Wide AI is now %s" % ("ON" if v == "true" else "OFF"))
                    if k in ("voice.enabled", "voice.speak_replies", "voice.offline_only", "ui.show_raw"):
                        v = "true" if str(v).lower() in ("true", "1", "on", "yes") else "false"
                    store.set_setting(k, v)
                    store.activity("user", "setting", None, "%s=%s" % (k, v if "pass" not in k else "***"))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_PATCH(self):
            if not self._auth():
                return
            m = re.match(r"^/tasks/(\d+)$", self.path)
            if m:
                try:
                    b = self._body()
                except ValueError as e:
                    return self._send(413 if "too large" in str(e) else 400, {"error": str(e)})
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
    LOG("fabos-agentd listening on 127.0.0.1:%d db=%s mode=%s provider=%s" % (PORT, DB_PATH, store.setting("mode", "auto"),
        os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude")))
    store.activity("system", "service_start", None, "fabos-agentd")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
