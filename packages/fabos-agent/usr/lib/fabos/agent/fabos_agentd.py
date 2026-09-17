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
  GET  /providers/ollama/models -> [{name,size,parameter_size,quantization,...}] (from Ollama's /api/tags; 503 when it is down)
  GET  /providers/ollama/status -> {installed, running, models, model, model_source, ram_gib, max_parameters_b, install_command}
  POST /providers/ollama/install {confirm: true} -> runs the official installer as root through the polkit path (ADR-0022)
  GET  /system/disk-unlock[?refresh=1] -> {encrypted, device, prompt_at_boot, keyfile_present, keyfile_in_initramfs, consistent, detail,
       available, diagnosis: {switch, prompt_at_boot_expected, agrees, boot_risk, needs_root, reason, repair, items[]} (disk_unlock.sh
       diagnose as the user, cached 30 s), last_request: {action, prompt_at_boot, at, ok, error} (the last change asked for here)}
  POST /system/disk-unlock {prompt_at_boot: bool, passphrase?} -> the Start-up setting "Ask for the disk password when the computer
       starts" (ADR-0021 layout: unencrypted /boot + LUKS2 root). false stores a LUKS key in the initramfs on /boot (anyone who starts
       the computer can use it), true removes it again; disk_unlock.sh runs as root through the polkit path, CRITICAL in the
       activity log; the passphrase reaches the helper on its stdin from a private tmpfs file, never a command line or a log.
  POST /system/disk-unlock {action: "repair", passphrase?} -> make the start-up files match the switch again (disk_unlock.sh repair as
       root, same path; the reply embeds the fresh "diagnosis"); {action: "diagnose"} -> the complete diagnosis as root (pkexec dialog).
  POST /speech/transcribe {audio_b64, format} -> {ok, text, backend}     POST /speech/say {text} -> {ok, audio_b64, format, backend}
       cloud speech through the configured provider (OpenAI or Gemini); other providers answer ok=false so the caller
       falls back to the offline engine (fabos-voice).
  POST /mail/test {provider, address, password?, smtp_host?...} -> {ok, smtp:{ok,detail}, imap:{ok,detail}, latency_ms}
       a real SMTP AUTH + IMAP LOGIN with the user's own account (15 s each); "wrong password / app password required"
       (535, AUTHENTICATIONFAILED) is told apart from "cannot reach". Passwords are never logged.
  POST /mail/oauth/start {provider: gmail}  GET /mail/oauth/status?flow_id=  — "Sign in with Google" (OAuth 2.0 loopback +
       PKCE, XOAUTH2 for SMTP/IMAP); only offered when /etc/fabos/google-oauth.env carries the owner's Desktop client id.
  POST /tasks/{id}/feedback {rating: good|bad}      DELETE /watches/{id}
  GET  /policy  POST /policy/reload                 the administrator policy (/etc/fabos/policy.json, also reloaded on SIGHUP)
  GET  /audit/verify                                checks the HMAC chain over the activity log; POST /audit/export {since, out_dir?} writes JSONL
Security (docs/ENTERPRISE.md, SECURITY.md): root only through `pkexec rootexec` (polkit, the user's own password in every mode);
run_shell inside bubblewrap when available; the agent's secrets, token and history are unreadable to tools; children get an
allowlisted session environment (never the daemon's own, which holds the provider key) and tool commands no ssh/gpg agent;
the activity log is a tamper-evident HMAC chain keyed from systemd-creds; every user setting is clamped to the administrator's
policy.json.
Providers: Claude (Anthropic), OpenAI, Google Gemini, DeepSeek, Ollama (the user's own install, ADR-0022), or any OpenAI-compatible chat
endpoint (local llama-server). Images (ADR-0021): the generate_image tool draws with the user's OpenAI or Gemini key (or an OpenAI-compatible
local image endpoint, setting images.local_endpoint) and saves PNGs under ~/Pictures/Fab OS; other providers get a plain "cannot generate images".
Mail: the user's OWN account (Gmail, Outlook/Hotmail, Yahoo, Zoho, iCloud presets, or any IMAP/SMTP server) — settings
mail.provider / mail.address / mail.from_name, secret mail_password (an app password where the provider requires one) or
the Google refresh token mail_oauth_refresh. The feedback relay (fabos-feedback) is a separate channel and is not used here.
Every tool step carries a one-sentence "narration" (Indian English) that UIs display and the voice daemon speaks.
Drivers (ADR-0020): cloud providers run the free-form tool loop; the built-in `local` model (or setting agent.driver=stepwise)
runs PLAN -> one tool per turn -> VERIFY -> FINISH with a compact prompt written for a 1.5B model. /status carries `driver`
and `network` ({online, target, checked, age_s}: the LAST probe's result, never a new one). The probe — one HTTPS HEAD to the provider
host or 1.1.1.1:443, 2 s timeout, no payload — runs only when a stepwise task names a web page or URL (legal/PRIVACY.md).
FABOS_AGENT_PROVIDER=fake runs a scripted provider for tests.
"""
import base64, hashlib, hmac, io, json, os, re, secrets as _secrets, shlex, shutil, signal, socket, sqlite3, subprocess, sys, threading, time, uuid, wave, urllib.request, urllib.error, urllib.parse
import http.client, smtplib, imaplib, email, email.utils, email.header, ssl, datetime as _dt
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
MODE_ORDER = ("ask", "auto", "bypass")          # least to most permissive; policy mode_max allows a prefix of this list
POLICY_FILE = os.environ.get("FABOS_POLICY_FILE", "/etc/fabos/policy.json")
POLICY_KEYS = ("mode_max", "providers_allowed", "cloud_allowed", "tools_denied", "hosts_allowed", "audit_export_dir", "require_password_for_root", "sandbox_network")
MANAGED_MSG = "Managed by your organisation"


class Policy:
    """Administrator policy from /etc/fabos/policy.json (root-owned 0644; absent = no restriction, i.e. today's behaviour).
    Loaded at start and on SIGHUP (or POST /policy/reload); every user setting is clamped to it; /status carries it under
    "policy" so UIs can show a "Managed by your organisation" line. Keys (all optional):
      mode_max                  ask|auto|bypass: the most permissive mode a user may pick (default bypass = unrestricted)
      providers_allowed         provider ids the user may select ([] = all)
      cloud_allowed             false: only the local model (and the test provider) may be used; cloud speech is off too
      tools_denied              tool names the agent may never call (removed from the model's tool list, refused at the gate)
      hosts_allowed             hosts web_fetch, mail and provider endpoints may reach ("example.com" also matches its
                                subdomains, "*.example.com" only the subdomains; [] = any host)
      audit_export_dir          directory `fabos audit export` writes JSONL files to (must be writable by the user)
      require_password_for_root true (default): root steps go through pkexec only; false additionally allows an
                                administrator-installed `sudo -n` rule for rootexec when pkexec is absent (kiosks)
      sandbox_network           true (default): run_shell keeps the network inside its sandbox; false = --unshare-net
    Unknown keys are reported, not fatal. A malformed file is reported in /status and treated as absent on purpose: a broken
    policy file must not lock the user out of their own computer, and the visible error gets it fixed."""

    def __init__(self, path=None):
        self.path = path or POLICY_FILE
        self.data, self.error, self.loaded_at, self.unknown = {}, "", 0.0, []
        self.load()

    def load(self):
        self.data, self.error, self.unknown = {}, "", []
        self.loaded_at = time.time()
        try:
            with open(self.path) as f:
                raw = json.load(f)
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            LOG("policy file ignored:", self.path, self.error)
            return False
        if not isinstance(raw, dict):
            self.error = "policy.json must contain a JSON object"
            return False
        self.unknown = sorted(k for k in raw if k not in POLICY_KEYS)
        d = {}
        if raw.get("mode_max") in MODE_ORDER:
            d["mode_max"] = raw["mode_max"]
        elif "mode_max" in raw:
            self.error = "mode_max must be ask|auto|bypass (ignored)"
        for key in ("providers_allowed", "tools_denied", "hosts_allowed"):
            if isinstance(raw.get(key), list):
                d[key] = [str(x).strip().lower() for x in raw[key] if str(x).strip()]
        for key in ("cloud_allowed", "require_password_for_root", "sandbox_network"):
            if isinstance(raw.get(key), bool):
                d[key] = raw[key]
        if isinstance(raw.get("audit_export_dir"), str) and raw["audit_export_dir"].startswith("/"):
            d["audit_export_dir"] = raw["audit_export_dir"]
        self.data = d
        LOG("policy loaded:", self.path, json.dumps(d, sort_keys=True))
        return True

    @property
    def managed(self):
        return bool(self.data)

    def mode_max(self):
        return self.data.get("mode_max", "bypass")

    def clamp_mode(self, mode):
        """The effective permission mode: never more permissive than mode_max."""
        mode = mode if mode in MODE_ORDER else "auto"
        return mode if MODE_ORDER.index(mode) <= MODE_ORDER.index(self.mode_max()) else self.mode_max()

    def cloud_allowed(self):
        return self.data.get("cloud_allowed", True)

    def provider_allowed(self, kind):
        if kind == "fake":
            return True
        if not self.cloud_allowed() and kind != "local":
            return False
        allowed = self.data.get("providers_allowed") or []
        return not allowed or kind in allowed

    def tool_denied(self, name):
        return name in (self.data.get("tools_denied") or [])

    def host_allowed(self, host):
        allowed = self.data.get("hosts_allowed") or []
        if not allowed:
            return True
        h = (host or "").lower().rstrip(".").split(":")[0]
        if not h:
            return False
        for a in allowed:
            if a.startswith("*."):
                if h.endswith(a[1:]):
                    return True
            elif h == a or h.endswith("." + a):
                return True
        return False

    def require_host(self, host, what):
        """Raise a clear, user-facing error when policy forbids the host."""
        if not self.host_allowed(host):
            raise RuntimeError("%s: %s may not reach %s. Allowed hosts: %s." % (MANAGED_MSG, what, host or "(no host)", ", ".join(self.data.get("hosts_allowed") or [])))

    def require_password_for_root(self):
        return self.data.get("require_password_for_root", True)

    def sandbox_network(self):
        return self.data.get("sandbox_network", True)

    def audit_export_dir(self):
        return self.data.get("audit_export_dir")

    def status(self):
        s = {"managed": self.managed, "path": self.path, "loaded_at": self.loaded_at, "error": self.error, "unknown_keys": self.unknown}
        s.update({k: self.data.get(k) for k in POLICY_KEYS})
        s["mode_max"] = self.mode_max()
        s["cloud_allowed"] = self.cloud_allowed()
        s["require_password_for_root"] = self.require_password_for_root()
        s["sandbox_network"] = self.sandbox_network()
        return s

    def prompt_line(self):
        """One system-prompt sentence so the model does not attempt what the organisation forbids."""
        if not self.managed:
            return ""
        parts = ["This computer is managed by an organisation"]
        if self.mode_max() != "bypass":
            parts.append("the permission mode is limited to %s" % self.mode_max())
        if self.data.get("tools_denied"):
            parts.append("these tools are not available: %s" % ", ".join(self.data["tools_denied"]))
        if self.data.get("hosts_allowed"):
            parts.append("only these hosts may be reached (web, mail, AI endpoints): %s" % ", ".join(self.data["hosts_allowed"]))
        if not self.cloud_allowed():
            parts.append("cloud AI providers are disabled; only the local model runs")
        return "; ".join(parts) + ". Do not try to work around these limits; say so and stop."


POLICY = Policy()


def audit_hmac(key, prev_hmac, rid, ts, actor, kind, task_id, detail):
    """One link of the activity log's chain: HMAC-SHA256(key, previous row's hmac || canonical row)."""
    msg = (prev_hmac or "") + "|" + json.dumps([rid, ts, actor, kind, task_id, detail], separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key or b"", msg.encode("utf-8"), hashlib.sha256).hexdigest()


# ----------------------------------------------------------------------------- storage
class Store:
    def __init__(self, path, audit_key=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # the audit chain's key (bytes). main() hands over the systemd-creds 'audit_key'; a Store opened without one (tests,
        # tools) still chains its rows with an empty key so the structure is identical and `audit_verify` still runs.
        self.audit_key = audit_key if isinstance(audit_key, bytes) else (audit_key or "").encode()
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
        if "hmac" not in {r["name"] for r in self.db.execute("PRAGMA table_info(activity)").fetchall()}:
            self.db.execute("ALTER TABLE activity ADD COLUMN hmac TEXT")      # rows written before this column stay unsigned (legacy)
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
        """Append one audit row and seal it: hmac = HMAC-SHA256(audit_key, previous row's hmac || this row). Rows are never
        updated afterwards; `audit_verify` recomputes the chain. Callers keep details short (commands/outputs are capped
        before they get here) and never pass secrets."""
        detail = (detail or "")[:4000]
        with self.lock:
            prev = self.one("SELECT hmac FROM activity ORDER BY id DESC LIMIT 1")
            ts = time.time()
            rid = self.q("INSERT INTO activity(ts,actor,kind,task_id,detail) VALUES(?,?,?,?,?)", ts, actor, kind, task_id, detail).lastrowid
            self.q("UPDATE activity SET hmac=? WHERE id=?", audit_hmac(self.audit_key, (prev or {}).get("hmac"), rid, ts, actor, kind, task_id, detail), rid)
            return rid

    def audit_verify(self):
        """Walk the chain in id order. Rows from before the chain existed carry no hmac and count as unsigned; the first signed
        row anchors the chain; an unsigned row after that, or any hmac that does not recompute, is a break. Truncation of the
        newest rows is not detectable from the database alone: compare `head` with the head recorded in the last export."""
        rows = self.all("SELECT id, ts, actor, kind, task_id, detail, hmac FROM activity ORDER BY id")
        prev_h, signed, unsigned, first_bad = "", 0, 0, None
        for r in rows:
            if r["hmac"] is None:
                if signed and first_bad is None:
                    first_bad = r["id"]
                unsigned += 1
                continue
            if first_bad is None and audit_hmac(self.audit_key, prev_h, r["id"], r["ts"], r["actor"], r["kind"], r["task_id"], r["detail"]) != r["hmac"]:
                first_bad = r["id"]
            prev_h = r["hmac"]
            signed += 1
        return {"ok": first_bad is None, "rows": len(rows), "signed": signed, "unsigned": unsigned, "first_bad": first_bad, "head": prev_h}

    def audit_export(self, since=None, out_dir=None):
        """Write the activity rows since `since` (epoch seconds) as JSON Lines into out_dir (policy audit_export_dir by default,
        else ~/.local/share/fabos/audit). The first line is a header with the chain's verification result and head so a
        collector can detect truncation between exports. Returns {path, rows, verify}."""
        target = out_dir or POLICY.audit_export_dir() or os.path.join(DATA_DIR, "audit")
        if not os.path.isdir(target):
            if target.startswith(DATA_DIR + os.sep):
                os.makedirs(target, mode=0o700, exist_ok=True)
            else:
                raise RuntimeError("audit export directory %s does not exist; the administrator creates it writable for the user (docs/ENTERPRISE.md)" % target)
        if not os.access(target, os.W_OK):
            raise RuntimeError("audit export directory %s is not writable by this user" % target)
        since = float(since or 0)
        rows = self.all("SELECT id, ts, actor, kind, task_id, detail, hmac FROM activity WHERE ts >= ? ORDER BY id", since)
        ver = self.audit_verify()
        name = "fabos-audit-%s-%s-%s.jsonl" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), socket.gethostname()[:32], os.environ.get("USER", str(os.getuid())))
        path = os.path.join(target, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "fabos-audit-export", "generated": datetime.now(timezone.utc).isoformat(), "host": socket.gethostname(),
                                "user": os.environ.get("USER", ""), "uid": os.getuid(), "since": since, "rows": len(rows), "chain_ok": ver["ok"], "chain_head": ver["head"],
                                "chain_first_bad": ver["first_bad"]}, ensure_ascii=False) + "\n")
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        self.activity("user", "audit_export", None, "%d rows since %s -> %s (chain %s)" % (len(rows), int(since), path, "ok" if ver["ok"] else "BROKEN"))
        return {"path": path, "rows": len(rows), "verify": ver}

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
     "description": "Run a shell command on this computer (bash). Use for anything the OS can do: inspect files, run programs, git, compilers, tests, package managers (privileged commands need approval). Returns stdout, stderr and exit code. The command runs in a sandbox: the system is read-only, the home directory is writable except ~/.ssh, ~/.gnupg and the agent's own configuration, and every process it started ends with the command. Start long-running programs and GUI apps with open_app instead.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string", "description": "working directory (default: home)"}, "timeout_s": {"type": "integer", "default": 120},
                                                       "as_root": {"type": "boolean", "default": False, "description": "run as root for system administration (apt, systemctl, modprobe, sysctl, /etc, disks). CRITICAL risk: requires the user's approval unless their mode is bypass. Do not write sudo in the command."}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file (UTF-8). Returns up to 200 KB.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Create or overwrite a text file (creates parent directories). Use append=true to append.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean", "default": False}}, "required": ["path", "content"]}},
    {"name": "list_dir", "description": "List a directory.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "open_app",
     "description": "Open a desktop application, file or URL in the user's graphical session (e.g. app='kate' (Fab Editor) with args=['/path/file.txt'], app='dolphin' (Fab Files), app='firefox' (Firefox) with args=['https://...'], app='libreoffice' with args=['--writer'], app='xdg-open' with args=['https://...']). Returns immediately.",
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
    {"name": "generate_image",
     "description": "Generate an image (a picture, drawing, illustration, logo, poster, wallpaper, icon) from a text prompt with the user's AI provider and save it as a PNG under ~/Pictures/Fab OS/. Returns the saved path, width, height and the provider used. Use it whenever the user asks you to draw, generate, create or make an image; then tell the user the path. If it answers that the provider cannot generate images, repeat that message to the user and stop — never try to draw with shell tools.",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string", "description": "what the image shows, in one or two sentences"},
                                                       "size": {"type": "string", "default": "1024x1024", "description": "WIDTHxHEIGHT: 1024x1024 (square), 1536x1024 (landscape) or 1024x1536 (portrait)"},
                                                       "n": {"type": "integer", "default": 1, "minimum": 1, "maximum": 4}}, "required": ["prompt"]}},
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
    # the start-up disk unlock surface (disk_unlock.sh, its keyfile, crypttab, the cryptsetup initramfs hook, the initramfs itself):
    # a task that touches any of it is changing whether the computer asks for the disk password
    (r"disk_unlock\.sh|luks-unlock\.key|/etc/crypttab|cryptsetup-initramfs|cryptroot/keyfiles|\bupdate-initramfs\b", "CRITICAL", "start-up disk unlock (LUKS key in the initramfs)"),
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
    if tool == "generate_image":
        return "MEDIUM", "creates an image file"
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


ROOTEXEC = "/usr/lib/fabos/agent/rootexec"
POLKIT_ACTION = "in.patienceai.fabos.rootexec"


def root_argv(aid, policy=None):
    """How the daemon reaches root: `pkexec rootexec <authz-id>` under the polkit action in.patienceai.fabos.rootexec
    (allow_active=auth_admin_keep: the user types their own password in the system dialog, remembered for five minutes) —
    in EVERY mode, bypass included. Fab OS ships no sudoers rule for rootexec any more. Only when the administrator's policy
    sets require_password_for_root=false AND pkexec is absent does the daemon fall back to `sudo -n rootexec`, i.e. to a rule
    the administrator installed themselves (unattended kiosks)."""
    policy = policy or POLICY
    if shutil.which("pkexec") or policy.require_password_for_root():
        return ["pkexec", ROOTEXEC, aid]
    return ["sudo", "-n", ROOTEXEC, aid]


# ---- Start-up: "Ask for the disk password when the computer starts" (Fab AI Controls › Settings › General). The root helper
# disk_unlock.sh {status|off|on} does the work (a LUKS keyfile in the initramfs on the unencrypted /boot, ADR-0021 layout); the
# daemon only classifies it (CRITICAL), reaches root the one way it has (pkexec rootexec, the user's own password in the system
# dialog) and keeps the passphrase off every command line and log. FABOS_DISK_UNLOCK_HELPER lets the tests point at a stub.
DISK_UNLOCK_HELPER = os.environ.get("FABOS_DISK_UNLOCK_HELPER", "/usr/lib/fabos/agent/disk_unlock.sh")
DISK_UNLOCK_RISK = "CRITICAL"
DISK_UNLOCK_TIMEOUT = 900        # s: update-initramfs -u -k all on a slow disk, twice when a failure rolls back
DISK_UNLOCK_MAX_PASSPHRASE = 512


def disk_unlock_status(helper=None):
    """`disk_unlock.sh status` as the user (no root needed: crypttab and findmnt are world-readable; whether the key is inside
    a root-only initrd is reported as null then). Runs through bash — the one interpreter the AppArmor profile lets the daemon
    exec unconfined. Never raises; a missing helper reads as not encrypted + available=false."""
    helper = helper or DISK_UNLOCK_HELPER
    if not os.path.isfile(helper):
        return {"encrypted": False, "prompt_at_boot": False, "available": False, "risk": DISK_UNLOCK_RISK,
                "error": "the disk unlock helper is not installed (%s)" % helper}
    try:
        r = subprocess.run(["bash", helper, "status"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        if not isinstance(out, dict):
            raise ValueError("not an object")
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
        return {"encrypted": False, "prompt_at_boot": False, "available": True, "risk": DISK_UNLOCK_RISK, "error": "disk unlock status failed: %s" % e}
    out.setdefault("available", True)
    out["risk"] = DISK_UNLOCK_RISK
    return out


DISK_UNLOCK_DIAG_TTL = 30        # s: GET /system/disk-unlock serves the last `diagnose` this long (it lists initrds: a second or two)
_du_diag_cache = {"at": 0.0, "diag": None}
_du_diag_lock = threading.Lock()
DISK_UNLOCK_LAST_REQUEST = "disk_unlock.last_request"    # settings key: the last change asked for here and how it ended (the UI shows a
                                                          # failed one in red — a dismissed polkit dialog used to be easy to miss)


def disk_unlock_diagnose(helper=None):
    """`disk_unlock.sh diagnose` as the user: the real boot-time state item by item (see the helper's header). Root-only items read
    "unknown" unless the record the last root run left still matches the file. Never raises."""
    helper = helper or DISK_UNLOCK_HELPER
    if not os.path.isfile(helper):
        return {"encrypted": False, "available": False, "items": [], "prompt_at_boot_expected": None, "agrees": None, "needs_root": False,
                "error": "the disk unlock helper is not installed (%s)" % helper}
    try:
        r = subprocess.run(["bash", helper, "diagnose"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        if not isinstance(out, dict) or "items" not in out:
            raise ValueError("not a diagnosis")
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
        return {"encrypted": False, "available": True, "items": [], "prompt_at_boot_expected": None, "agrees": None, "needs_root": False,
                "error": "disk unlock diagnose failed: %s" % e}
    out.setdefault("available", True)
    return out


def disk_unlock_diagnosis(force=False, fresh=None):
    """The cached diagnosis (DISK_UNLOCK_DIAG_TTL); force re-runs it; fresh=<dict> stores what a root run just returned."""
    with _du_diag_lock:
        if fresh is not None:
            _du_diag_cache.update(at=time.time(), diag=fresh)
            return fresh
        if not force and _du_diag_cache["diag"] is not None and time.time() - _du_diag_cache["at"] < DISK_UNLOCK_DIAG_TTL:
            return _du_diag_cache["diag"]
    d = disk_unlock_diagnose()
    with _du_diag_lock:
        _du_diag_cache.update(at=time.time(), diag=d)
    return d


def disk_unlock_forget():
    with _du_diag_lock:
        _du_diag_cache.update(at=0.0, diag=None)


def disk_unlock_apply(agent, prompt_at_boot, passphrase=None, helper=None, action=None):
    """Runs `disk_unlock.sh on|off|repair|diagnose` as root through Tools.run_as_root — pkexec rootexec with a one-time record, the
    user's own password in the polkit dialog, the same path as every as_root step. For `off` (and `repair`, which re-runs it) the
    passphrase is written to a private file in the agent's runtime directory (tmpfs, 0600 inside the 0700 directory that
    protected_path hides from every tool) and handed to the helper as its STDIN by a shell redirection: it is on no command line, in
    no authorization record, in no log (the record and the activity rows carry only the file's path). The file is overwritten and
    removed the moment the helper returns. A `diagnose` reply (the bare diagnosis object) is wrapped as {ok, action, diagnosis}."""
    helper = helper or DISK_UNLOCK_HELPER
    action = action or ("on" if prompt_at_boot else "off")
    cmd = shlex.quote(helper) + " " + action
    pw_path = None
    if action in ("off", "repair") and passphrase:
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        os.chmod(RUN_DIR, 0o700)
        pw_path = os.path.join(RUN_DIR, "disk-unlock-" + uuid.uuid4().hex)
        fd = os.open(pw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(passphrase + "\n")
        cmd += " < " + shlex.quote(pw_path)
    try:
        r = agent.tools.run_as_root(None, cmd, "/", DISK_UNLOCK_TIMEOUT)
    finally:
        if pw_path:
            try:
                with open(pw_path, "r+b") as f:
                    f.write(b"\0" * (len(passphrase) + 1))
            except OSError:
                pass
            try:
                os.remove(pw_path)
            except FileNotFoundError:
                pass
    if not isinstance(r, dict):
        return {"ok": False, "prompt_at_boot": None, "error": "root execution gave no result", "risk": DISK_UNLOCK_RISK}
    if r.get("error"):
        return {"ok": False, "prompt_at_boot": None, "error": r["error"], "risk": DISK_UNLOCK_RISK}
    out = None
    for line in reversed((r.get("stdout") or "").strip().splitlines()):
        if line.startswith("{"):
            try:
                out = json.loads(line)
                break
            except ValueError:
                continue
    if not isinstance(out, dict):
        err = ((r.get("stderr") or "").strip().splitlines() or ["the helper gave no result"])[-1]
        out = {"ok": r.get("exit_code") == 0, "prompt_at_boot": None, "error": None if r.get("exit_code") == 0 else err[:300]}
    if action == "diagnose" and "items" in out:          # the bare diagnosis: wrap it
        out = {"ok": True, "action": "diagnose", "prompt_at_boot": out.get("prompt_at_boot"), "error": None, "diagnosis": out}
    out["exit_code"] = r.get("exit_code")
    out["risk"] = DISK_UNLOCK_RISK
    if out.get("ok") is not True and not out.get("error"):
        out["error"] = "the helper failed (exit %s)" % r.get("exit_code")
    if isinstance(out.get("diagnosis"), dict):
        out["diagnosis"].setdefault("available", True)
    return out


def protected_path(path):
    """Paths NO tool may read, list or write even when the step was approved: the agent's secrets (provider keys, mail
    password, audit key) and its runtime directory (API token, root authorization records). The classifier already makes
    ~/.config/fabos CRITICAL; this is the hard stop behind it. Symlinks are resolved first."""
    try:
        rp = os.path.realpath(os.path.expanduser(str(path)))
    except (TypeError, ValueError):
        return True
    for base in (os.path.join(CONF_DIR, "secrets"), RUN_DIR):
        b = os.path.realpath(base)
        if rp == b or rp.startswith(b + os.sep):
            return True
    return False


def check_protected(path):
    if protected_path(path):
        raise PermissionError("refused: %s holds the agent's own secrets or session token; no tool may read or write it, whatever the approval" % path)


# ----------------------------------------------------------------------------- the environment handed to children
# The daemon's own environment is not the user's: the unit loads EnvironmentFile /etc/fabos/agent.env and
# ~/.config/fabos/agent.env (ANTHROPIC_API_KEY is a documented key source), systemd adds its bookkeeping, and the session
# manager (`systemctl --user show-environment`) may hold whatever the user exported. A child — a shell step inside
# bubblewrap, a watch command, an application — therefore gets an ALLOWLIST of desktop-session variables, and on top of
# that a deny pattern: a name that smells like a credential is dropped even when its prefix is allowed. Without this the
# tmpfs over ~/.config/fabos would be theatre: `env` inside the sandbox would print the provider key.
ENV_ALLOW = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ", "TZDIR", "TERM", "COLORTERM", "TMPDIR", "HOSTNAME",
             "EDITOR", "VISUAL", "PAGER", "BROWSER", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
             "DESKTOP_SESSION", "XMODIFIERS", "INPUT_METHOD", "SSH_AUTH_SOCK"}
ENV_ALLOW_PREFIX = ("XDG_", "LC_", "QT_", "KDE_", "GTK_", "GDK_", "XCURSOR_", "PIPEWIRE_", "PULSE_", "MOZ_", "ELECTRON_", "SDL_",
                    "PLASMA_", "KWIN_", "SAL_", "LIBVA_", "MESA_", "__GL", "VDPAU_", "GBM_", "WLR_")
ENV_DENY = re.compile(r"API_?KEY|TOKEN|SECRET|PASSW|PASSPHRASE|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY|OAUTH|COOKIE|^FABOS_|^ANTHROPIC_|^OPENAI_|"
                      r"^GEMINI_|^GOOGLE_|^DEEPSEEK_|^AWS_|^AZURE_|^GH_|^GITHUB_|^HF_|^INVOCATION_ID$|^JOURNAL_STREAM$|^MANAGERPID$|"
                      r"^CREDENTIALS_DIRECTORY$|^NOTIFY_SOCKET$|^LISTEN_", re.I)
# the user's key agents: applications launched for the user keep them (a launcher would), tool commands never see them
AGENT_SOCKET_VARS = ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_AGENT_LAUNCHER", "GPG_AGENT_INFO", "GNUPGHOME", "GPG_TTY")


def env_allowed(name):
    if ENV_DENY.search(name):
        return False
    return name in ENV_ALLOW or name.startswith(ENV_ALLOW_PREFIX)


def clean_env(*sources, drop=()):
    """Merge environment mappings (later sources win) keeping only allowed names; `drop` removes names on top."""
    env = {}
    for src in sources:
        for k, v in src.items():
            if isinstance(k, str) and isinstance(v, str) and env_allowed(k) and k not in drop:
                env[k] = v
    return env


def session_environment():
    """The desktop session's variables as the user manager holds them (`systemctl --user show-environment`); {} when absent."""
    out = {}
    try:
        text = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=5).stdout
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    except Exception:
        pass
    return out


# run_shell sandbox (bubblewrap). Hidden = replaced by an empty tmpfs; read-only = visible but not writable; masked = a
# socket file replaced by /dev/null.
def sandbox_runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or os.path.dirname(RUN_DIR)


def sandbox_hidden():
    """Directories replaced by an empty tmpfs: the user's keys, a wallet, the agent's own configuration and runtime dir, and
    the key agents' socket directories inside $XDG_RUNTIME_DIR (gnupg: gpg-agent + its ssh socket; gcr: GNOME keyring's
    ssh agent; keyring: gnome-keyring control) — the runtime dir itself stays bound for Wayland, D-Bus and PipeWire."""
    rt = sandbox_runtime_dir()
    return [os.path.expanduser("~/.ssh"), os.path.expanduser("~/.gnupg"), os.path.dirname(CONF_DIR), os.path.expanduser("~/.local/share/kwalletd"), RUN_DIR,
            os.path.join(rt, "gnupg"), os.path.join(rt, "gcr"), os.path.join(rt, "keyring")]


def sandbox_masked(env=None):
    """Socket files replaced by /dev/null inside the sandbox: Ubuntu's ssh-agent.socket ($XDG_RUNTIME_DIR/openssh_agent) and
    whatever SSH_AUTH_SOCK names in the session (KDE's ssh-agent, a manual ssh-agent under /tmp). Paths that do not exist or
    already sit inside a hidden directory are skipped."""
    rt = sandbox_runtime_dir()
    cands = [os.path.join(rt, "openssh_agent")]
    s = (env if env is not None else os.environ).get("SSH_AUTH_SOCK")
    if s and s.startswith("/"):
        cands.append(s)
    hidden = sandbox_hidden()
    out = []
    for p in cands:
        p = os.path.realpath(p) if os.path.exists(p) else p
        if not os.path.exists(p) or os.path.isdir(p) or p in out:
            continue
        if any(p == h or p.startswith(h + os.sep) for h in hidden):
            continue
        out.append(p)
    return out


def sandbox_readonly():
    return [DATA_DIR]          # the agent's history and audit chain: a task may read them, never rewrite them


def sandbox_available():
    """bwrap is installed and can create namespaces here (one probe, cached by the Agent). FABOS_AGENT_SANDBOX=0 disables it
    (tests exercise the fallback that way)."""
    if os.environ.get("FABOS_AGENT_SANDBOX", "1").lower() in ("0", "false", "no", "off"):
        return False
    if not shutil.which("bwrap"):
        return False
    try:
        r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "--die-with-parent", "/bin/true"],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def sandbox_argv(cwd, network=True, env=None):
    """The bubblewrap prefix for run_shell: the whole system read-only; the home directory writable EXCEPT the user's keys
    (~/.ssh, ~/.gnupg), a wallet if one exists, and the agent's own configuration (empty tmpfs over each); the agent's
    history read-only; the agent's runtime directory (API token, root authorization records) hidden — so a task cannot
    approve its own steps through the API; /tmp shared with the session; a fresh /dev (plus the GPU nodes) and /proc; the
    session runtime dir kept so Wayland/D-Bus/PipeWire clients work, minus the key agents' socket directories (tmpfs) and
    socket files (/dev/null over them; `env` is the session environment that names SSH_AUTH_SOCK); own PID namespace;
    killed with the daemon. What the runtime dir still exposes is the desktop itself: the Wayland socket, the session
    D-Bus, PipeWire — the same as any application the user starts."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    a = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--bind", "/tmp", "/tmp"]
    if os.path.isdir("/var/tmp"):
        a += ["--bind", "/var/tmp", "/var/tmp"]
    if os.path.isdir("/dev/dri"):
        a += ["--dev-bind", "/dev/dri", "/dev/dri"]
    if os.path.isdir(HOME):
        a += ["--bind", HOME, HOME]
    if runtime and os.path.isdir(runtime):
        a += ["--bind", runtime, runtime]
    for p in sandbox_readonly():
        if os.path.isdir(p):
            a += ["--ro-bind", p, p]
    for p in sandbox_hidden():
        if os.path.isdir(p):
            a += ["--tmpfs", p]
    for p in sandbox_masked(env):
        a += ["--ro-bind", "/dev/null", p]
    a += ["--unshare-pid", "--die-with-parent", "--chdir", cwd if os.path.isdir(cwd) else HOME]
    if not network:
        a += ["--unshare-net"]
    return a


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
        """Root execution through /usr/lib/fabos/agent/rootexec, started with pkexec (polkit action in.patienceai.fabos.rootexec,
        auth_admin_keep). The policy gate has already allowed this CRITICAL step; the user now authenticates in the system
        dialog — in every mode, bypass included — and rootexec runs only the command recorded here: a one-time record (owned by
        the user, private, 10-minute validity, id + sha256 of the command inside) that rootexec consumes and re-checks."""
        authz_dir = os.path.join(RUN_DIR, "authz")
        os.makedirs(authz_dir, mode=0o700, exist_ok=True)
        os.chmod(authz_dir, 0o700)
        aid = uuid.uuid4().hex
        digest = hashlib.sha256(command.encode()).hexdigest()
        rec = {"id": aid, "command": command, "command_sha256": digest, "cwd": cwd if cwd.startswith("/") else "/", "timeout_s": timeout,
               "task_id": task_id, "created": time.time(), "uid": os.getuid()}
        p = os.path.join(authz_dir, aid + ".json")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f)
        argv = root_argv(aid)
        self.store.activity("agent", "root_exec_requested", task_id, "via %s sha256=%s cmd=%s" % (argv[0], digest[:16], command[:300]))
        try:
            # the password dialog may stay open for a while: allow five minutes on top of the command's own timeout
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 15 + (300 if argv[0] == "pkexec" else 0), stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            self.store.activity("agent", "root_exec_refused", task_id, "%s is not installed" % argv[0])
            return {"error": "root execution unavailable: %s is not installed" % argv[0]}
        except subprocess.TimeoutExpired:
            self.store.activity("agent", "root_exec_refused", task_id, "timeout waiting for authentication or the command")
            return {"error": "timeout after %ss (root)" % timeout}
        finally:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        if r.returncode != 0 and not r.stdout.strip().startswith("{"):
            err = r.stderr.strip()
            if argv[0] == "pkexec" and r.returncode == 126:
                why = "you dismissed the password dialog"
            elif argv[0] == "pkexec" and r.returncode == 127:
                why = "not authorised (%s)" % (err.splitlines()[-1] if err else "wrong password, or no authentication agent is running in this session")
            else:
                why = err or "%s refused" % argv[0]
            self.store.activity("agent", "root_exec_refused", task_id, "%s: %s" % (command[:200], why[:200]))
            return {"error": "root execution refused: %s" % why}
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {"exit_code": r.returncode, "stdout": r.stdout[-30000:], "stderr": r.stderr[-10000:]}
        self.store.activity("agent", "root_exec", task_id, "exit=%s sha256=%s cmd=%s" % (out.get("exit_code", out.get("error")), digest[:16], command[:300]))
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
        # Sandbox: bubblewrap when it works here (see sandbox_argv), else the plain shell with the step marked sandbox=none.
        # Inside the sandbox every process the command started ends with it (own PID namespace): background servers do not
        # survive the step — long-running programs go through open_app, which runs in the session.
        # Environment: the allowlisted session variables only (Agent.tool_env) — never the daemon's own environment, which
        # carries the provider key from agent.env — and without the user's ssh/gpg agent sockets, sandboxed or not.
        import tempfile
        sandbox = self.agent.sandbox_name()
        senv = self.agent.session_env()
        argv = (sandbox_argv(cwd, POLICY.sandbox_network(), env=senv) if sandbox == "bwrap" else []) + ["bash", "-lc", inp["command"]]
        fo = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace"); fe = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        p = subprocess.Popen(argv, cwd=cwd, stdout=fo, stderr=fe, text=True, env=self.agent.tool_env(senv), start_new_session=True)
        self.agent.procs.setdefault(task_id, set()).add(p)
        try:
            p.wait(timeout=to)
            fo.seek(0); fe.seek(0); out, err = fo.read(), fe.read()
            if task_id in self.agent.cancel:
                return {"error": "cancelled by user"}
            return {"exit_code": p.returncode, "stdout": out[-30000:], "stderr": err[-10000:], "sandbox": sandbox}
        except subprocess.TimeoutExpired:
            kill_tree(p)
            return {"error": "timeout after %ss (process killed)" % to}
        finally:
            self.agent.procs.get(task_id, set()).discard(p)
            fo.close(); fe.close()

    def t_read_file(self, task_id, inp):
        p = os.path.expanduser(inp["path"])
        check_protected(p)
        with open(p, "r", errors="replace") as f:
            data = f.read(200000)
        return {"path": p, "content": data, "truncated": os.path.getsize(p) > 200000}

    def t_write_file(self, task_id, inp):
        p = os.path.expanduser(inp["path"])
        check_protected(p)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a" if inp.get("append") else "w") as f:
            f.write(inp["content"])
        return {"path": p, "bytes": len(inp["content"].encode())}

    def t_list_dir(self, task_id, inp):
        p = os.path.expanduser(inp["path"])
        check_protected(p)
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
        POLICY.require_host(cfg["imap_host"], "mail (IMAP)")
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
        POLICY.require_host(urllib.parse.urlparse(inp["url"]).hostname, "web_fetch")
        req = urllib.request.Request(inp["url"], headers={"User-Agent": "FabOS-agent/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read(2_000_000)
        text = raw.decode("utf-8", errors="replace")
        if "html" in ct:
            text = _strip_html(text)
        return {"url": inp["url"], "content_type": ct, "text": text[:100000]}

    def t_generate_image(self, task_id, inp):
        """generate_image (ADR-0021): the picture is made by the user's image provider (image_provider) and saved as a PNG under
        ~/Pictures/Fab OS/<yyyy-mm-dd>-<slug>-<n>.png. The prompt goes to that provider and nowhere else (legal/PRIVACY.md)."""
        prompt = " ".join(str(inp.get("prompt") or "").split())
        if not prompt:
            raise RuntimeError("generate_image needs a prompt that describes the picture")
        size = image_size(inp.get("size"))
        try:
            n = max(1, min(int(inp.get("n") or 1), 4))
        except (TypeError, ValueError):
            n = 1
        kind, model, blobs = generate_images(self.store, prompt, size, n)
        saved = save_images(blobs, prompt, size)
        self.store.activity("agent", "image_generated", task_id, "%s/%s %dx%d -> %s%s" % (kind, model, saved[0]["width"], saved[0]["height"], saved[0]["path"],
                                                                                          (" (+%d more)" % (len(saved) - 1)) if len(saved) > 1 else ""))
        out = {"path": saved[0]["path"], "width": saved[0]["width"], "height": saved[0]["height"], "provider": kind, "model": model, "prompt": prompt,
               "count": len(saved), "folder": images_dir()}
        if len(saved) > 1:
            out["paths"] = [x["path"] for x in saved]
        return out

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
- Applications: every installed app (system, Flatpak, user) is available to you the moment it is installed. Use list_apps to discover names, launch commands and supported file types, open_app to launch them (kate = Fab Editor, dolphin = Fab Files, konsole = Fab Terminal, firefox = Firefox for the web), and their CLI or D-Bus interfaces via run_shell (KDE apps: qdbus6 / kdialog / kioclient). Installed now ({app_count} apps): {app_names}.
- System administration (packages, services, kernel modules, sysctl, disks, files under /etc or /usr) is done with run_shell(as_root=true). It is CRITICAL risk: the user approves it unless their mode is bypass. Never put sudo in the command; as_root already runs it as root. Verify the result afterwards (e.g. systemctl is-active, dpkg -s, lsmod).
- Every tool call passes a deterministic policy check (risk LOW/MEDIUM/HIGH/CRITICAL against the user's permission mode). A denied call returns an error: respect it, explain, and find an allowed way or stop.
- Images: when the user asks you to draw, generate, create or make an image, a picture, a logo, a poster, a wallpaper or an icon, call generate_image with a clear prompt (and the size they want) and tell them the path it returns — the file is under ~/Pictures/Fab OS/. If the tool answers that the provider cannot generate images, say exactly that (which key to add in Settings) and stop; never paint with shell tools instead.
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
    managed = POLICY.prompt_line()
    return system + ("\n- " + persona if persona else "") + ("\n- " + managed if managed else "")


# ---- narration: one human sentence per tool step (Indian English), filled deterministically at insert time and on
# completion. UIs show it under the step; the voice daemon speaks it. Never includes raw commands unless ui.show_raw.
def _base(path):
    return os.path.basename(str(path or "").rstrip("/")) or str(path or "")


def _app_name(inp):
    app = str(inp.get("app") or "").split("/")[-1]
    return {"kate": "Fab Editor", "dolphin": "Fab Files", "konsole": "Fab Terminal", "xdg-open": "the default app", "open": "the default app",
            "firefox": "Firefox", "firefox-esr": "Firefox", "libreoffice": "LibreOffice", "vlc": "VLC", "plasma-discover": "Fab Software", "gwenview": "Fab Photos",
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
        return "Running a command for you." if not inp.get("as_root") else "Running an administrator command for you; the system will ask for your password."
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
    if name == "generate_image":
        return "Generating the image now."
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
    if name == "generate_image":
        return "Done, the image is saved in Pictures."
    return "Done."


def approval_narration(name, inp):
    """Spoken while the step waits for the user's permission."""
    inp = inp if isinstance(inp, dict) else {}
    summary = {"run_shell": "run a command as administrator" if inp.get("as_root") else "run a command", "write_file": "write the file %s" % _base(inp.get("path")),
               "read_file": "read %s" % _base(inp.get("path")), "list_dir": "look inside %s" % _base(inp.get("path")), "open_app": "open %s" % _app_name(inp),
               "type_text": "type into the focused window", "send_email": "send a mail to %s" % (inp.get("to") or "someone"), "check_email": "check your mail",
               "schedule_watch": "set up a background watch", "web_fetch": "fetch %s" % _host(inp.get("url")), "notify_user": "show a notification",
               "ask_user": "ask you a question", "list_apps": "look up installed apps", "generate_image": "generate an image and save it in Pictures"}.get(name, (name or "do something").replace("_", " "))
    return "This needs your permission: %s. Shall I go ahead?" % summary


# Provider presets. All non-Claude providers speak the OpenAI-compatible chat API with tool calling.
PROVIDERS = {
    "claude": {"label": "Anthropic (Claude)", "secret": "claude_api_key", "model": "claude-opus-5", "help": "Paste an API key from your Anthropic account."},
    "gemini": {"label": "Google Gemini", "secret": "gemini_api_key", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-pro",
               "help": "Paste an API key from Google AI Studio."},
    "openai": {"label": "OpenAI", "secret": "openai_api_key", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1", "help": "Paste an API key from your OpenAI account."},
    "deepseek": {"label": "DeepSeek", "secret": "deepseek_api_key", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "help": "Paste an API key from the DeepSeek platform."},
    "local": {"label": "Local model", "secret": "local_api_key", "base_url": "http://127.0.0.1:8080/v1", "model": "local", "no_key": True,
              "help": "Runs on this computer (llama-server or any OpenAI-compatible endpoint). No account, no key needed."},
    # Ollama (ADR-0022): the user's own Ollama install on this computer, OpenAI-compatible at /v1, no key. NOT bundled — its installer
    # downloads about 1 GB and Ollama is not in the Ubuntu archive; `fabos ollama install` prints the official command and runs it only
    # with --yes through the polkit root path. model "" = the largest installed model that fits this machine's RAM (ollama_pick_model).
    "ollama": {"label": "Ollama (on this computer)", "secret": "ollama_api_key", "base_url": "http://127.0.0.1:11434/v1", "model": "", "no_key": True,
               "help": "Uses the Ollama you installed yourself (ollama.com; `fabos ollama install`). Small and large models, GPU when Ollama finds one. No key needed."},
}
SECRET_NAMES = ("claude_api_key", "openai_api_key", "gemini_api_key", "deepseek_api_key", "local_api_key", "ollama_api_key", "mail_password", "mail_oauth_refresh")
# Providers whose key is optional (a loopback server): the agent runs without a stored secret.
NO_KEY_PROVIDERS = tuple(k for k, v in PROVIDERS.items() if v.get("no_key"))
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


# ----------------------------------------------------------------------------- small-model driver (ADR-0020)
# The built-in 1.5B model cannot carry the 60-turn free-form conversation the cloud models run: measured on the graded
# ladder it calls one tool and declares the task done. So the `local` provider (or the setting agent.driver=stepwise) runs
# every task as PLAN (strict-JSON plan, schema enforced by llama-server's response_format) -> EXECUTE one tool call per turn,
# the model seeing only the current step, the results so far (clipped) and the remaining steps -> VERIFY each step
# (deterministic check + a yes/no self-check; up to STEP_RETRIES retries with the error shown) -> FINISH (one short summary).
# Cloud providers keep the free-form loop unchanged; agent.driver=freeform forces it for the local model too.
# save_result (driver-only) writes the previous tool call's output to a file verbatim, so the model never retypes data.
STEP_TOOLS = ("run_shell", "write_file", "save_result", "read_file", "list_dir", "open_app", "type_text", "web_fetch", "generate_image", "send_email", "check_email", "notify_user", "reply")
# Driver-only tool (never offered to the free-form loop): a 1.5B model mangles data it has to retype inside JSON arguments
# (measured: the fetched {"ok": true, "app": "Fab OS"} came back as "ok\napp=Fab OS"). save_result names a path; the driver writes
# the previous tool call's output there byte for byte through the real write_file tool (same risk gate, same record).
SAVE_RESULT_TOOL = {"name": "save_result",
                    "description": "Write the output of the previous tool call (stdout of run_shell, the text web_fetch or read_file returned) to a file exactly as it is. Use it instead of retyping data.",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}
PLAN_MAX_STEPS = 8
STEP_RETRIES = 2                                          # attempts after the first, each shown the previous attempt's error
RESULT_LIMIT_STEP, RESULT_LIMIT_EARLIER = 1500, 300       # chars of the last / earlier verified results in a stepwise turn
CHECK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 120}},
                "required": ["ok", "reason"], "additionalProperties": False}
# The desktop's browser executable (ADR-0018: Firefox, Mozilla's own build) — every prompt and check below derives from this one name.
BROWSER = "firefox"
# Network probe for the local prompt's "Internet:" line: one HTTPS HEAD, 2 s, cached 60 s — run ONLY when a stepwise task names a web
# page or URL (a copy, a count or a note never touches the network; legal/PRIVACY.md). /status reports the cached value, never probes.
NET_PROBE_HOST, NET_PROBE_PORT, NET_CACHE_S, NET_TIMEOUT_S = "1.1.1.1", 443, 60, 2.0
_net = {"online": None, "target": "", "checked": 0.0}
_net_lock = threading.Lock()

# Written for a 1.5B model: short, explicit schemas, worked examples, the verification rule, the show-your-work rule. Kept
# under 900 tokens (measured with llama-server /tokenize in the image; tests/agent-test.py bounds the character count).
LOCAL_SYSTEM_PROMPT = """You are the {app} agent on this Linux desktop (KDE Plasma, Wayland), working as user {user}. Home: {home}. Date: {date}. Internet: {net}.
You get ONE step of a plan at a time. Do exactly that step with ONE tool call, then stop. Rules:
- Never count, add or compute in your head: run a command and use its output. Paths are absolute; ~ means {home}.
- Copy means cp and the source stays: never mv, rm or delete anything unless the task itself asks to move, rename or delete it.
- Never say a step is done before its result is verified. If the last result shows an error, change the command or the arguments and try again.
- Show your work: text the user will read (a note, a letter, a mail body) is typed where they can watch it: open_app kate with the file path, then type_text the text, then write_file the same text to the same path.
Tools and their JSON arguments:
- run_shell {{"command": "<bash>"}} -> {{"exit_code", "stdout", "stderr"}}. Files, folders, copy, rename, count, sum, dates.
- write_file {{"path": "...", "content": "..."}} creates folders and writes the content exactly (nothing added).
- save_result {{"path": "..."}} writes the previous tool call's output (stdout, fetched text, typed text) to that file unchanged; never retype data.
- read_file {{"path"}} · list_dir {{"path"}} · web_fetch {{"url"}} -> the page text (needs internet) · open_app {{"app": "kate|konsole|dolphin|{browser}|libreoffice", "args": ["/path"]}} · type_text {{"text": "...", "delay_ms": 1500}} types into the window opened in the previous step · generate_image {{"prompt": "..."}} makes a picture and saves the PNG itself (never draw with commands) · notify_user {{"message"}} · send_email {{"to", "subject", "body"}}.
Worked examples (step -> the one call):
- save the word hi into ~/Documents/a.txt -> write_file {{"path": "{home}/Documents/a.txt", "content": "hi\\n"}}
- count the regular files in /tmp/x -> run_shell {{"command": "find /tmp/x -maxdepth 1 -type f | wc -l"}}
- total of the column amount in a.csv and b.csv -> run_shell {{"command": "python3 -c \\"import csv,sys; t=sum(float(r['amount']) for f in sys.argv[1:] for r in csv.DictReader(open(f))); print(int(t) if t==int(t) else round(t,2))\\" a.csv b.csv"}}
- rename every .txt in ~/x to .md (same base names) -> run_shell {{"command": "for f in ~/x/*.txt; do mv \"$f\" \"${{f%.txt}}.md\"; done"}}
- the largest file under /tmp/x, name only, into ~/big.txt -> run_shell {{"command": "find /tmp/x -type f -printf '%s %f\\n' | sort -n | tail -1 | cut -d' ' -f2- > {home}/big.txt"}}
- open Fab Editor and type hello -> open_app {{"app": "kate"}} ; then the next step -> type_text {{"text": "hello", "delay_ms": 1500}}
- save what the previous step fetched or printed into ~/Documents/out.json -> save_result {{"path": "{home}/Documents/out.json"}}
- draw a blue circle -> generate_image {{"prompt": "a blue circle"}}
Apps: Fab Editor = kate, Fab Terminal = konsole, Fab Files = dolphin, browser = {browser}. Permission mode: {mode}."""

PLAN_SYSTEM = """You plan a desktop task for the {app} agent as a short numbered list of steps; each step is done by ONE tool call. Home: {home}. The task's files and folders are on this computer.
Tools: run_shell (a bash command: files, folders, copy, rename, count, sum, dates, curl), write_file (create a text file with exact content the user gave), save_result (write the previous step's output to a file exactly as it is), read_file, list_dir, web_fetch (fetch a URL's text), generate_image (a picture, drawing, logo, poster or wallpaper from a description; it saves the PNG itself), open_app (kate = Fab Editor, konsole = Fab Terminal, dolphin = Fab Files, {browser} = the browser, libreoffice), type_text (type into the app opened in the previous step), send_email, notify_user, reply (the final answer text, only when the user asked a question or for a report line).
Rules: as few steps as possible; ONE run_shell step when a command does the whole job, including writing a computed value into a file with '>' (dates: date +%F; copying: cp, the source stays; a column total in CSV files: python3's csv module by column name). To count or list the files of a folder use list_dir: it returns the total and the names. Use web_fetch only when the task names a web page or URL; never invent a URL. An image the user asks for is ONE generate_image step, never a command. Tool names are not shell commands. If the user names the tools or the order (open X, then type Y), plan exactly those steps in that order. Each step does one thing: web_fetch and read_file only return text, so saving what they return is a separate save_result step right after. write_file is for text the user gave literally; save_result for output a previous step produced. Show your work: when the user asks to WRITE or COMPOSE text they will read (a note, a letter, a mail body, a message, a document, code) or wants to watch it typed: open_app kate with the file path, then type_text, then write_file the same text to that path. Pure file or system operations (copy, rename, count, a value into a file) need no window. No step for checking: verification is automatic. Never create a file the task does not name; a count, a question or a report line ends with a reply step, not a file. Do not invent facts, values or paths. Do not answer the task yourself.
Example — "How big is ~/Pictures? Reply SIZE: <bytes>" -> {{"steps": [{{"tool": "run_shell", "goal": "print the total size of ~/Pictures in bytes"}}, {{"tool": "reply", "goal": "SIZE: the number printed"}}]}}
Return only JSON: {{"steps": [{{"tool": "...", "goal": "what the step must achieve, in words, with the exact paths and values — never a command"}}]}}"""

CHECK_SYSTEM = ("You verify one step of a desktop task. Answer as JSON {\"ok\": true|false, \"reason\": \"...\"}: ok=true when the tool result shows the "
                "step's goal was achieved (exit_code 0 and the expected output or file), ok=false only when the result shows an error, a wrong value or that nothing happened.")

FINISH_SYSTEM = ("You write the closing message of the {app} agent to the user: one or two short sentences in plain, warm English saying what was done "
                 "and where the outputs are. Only facts from the step results below; never invent, never add offers or questions.")


PLAN_CLAUSE_RE = re.compile(r"\s[—–]\s|,?\s(then|after that|afterwards|finally)\s", re.I)     # ';' is a sentence break below, not counted twice


def plan_max_steps(request):
    """How many steps a plan for this request may have: two more than the request has sentences (split at . ! ? ;) plus its explicit
    clause breaks (an em dash, "then", "finally"), at least 3, at most PLAN_MAX_STEPS — enforced through the JSON schema, so a one-line
    task cannot come back as a seven-step story (measured), while a one-sentence task that lists four things is not squeezed into three."""
    text = " ".join((request or "").split())
    n = len([s for s in re.split(r"[.!?;]+\s", text) if s.strip()])
    return max(3, min(PLAN_MAX_STEPS, 2 + n + len(PLAN_CLAUSE_RE.findall(text))))


def plan_schema(allowed, max_steps=PLAN_MAX_STEPS):
    return {"type": "object", "additionalProperties": False, "required": ["steps"],
            "properties": {"steps": {"type": "array", "minItems": 1, "maxItems": max_steps,
                                     "items": {"type": "object", "additionalProperties": False, "required": ["tool", "goal"],
                                               "properties": {"tool": {"type": "string", "enum": list(allowed)}, "goal": {"type": "string", "maxLength": 240}}}}}}


def driver_name(store, kind, prov=None):
    """'stepwise' or 'freeform' for a provider kind: the setting agent.driver wins, else local => stepwise, ollama => stepwise when the
    chosen model has no native tool calling or fewer than OLLAMA_FREEFORM_MIN_B parameters (ADR-0022; the provider object carries what
    /api/show said), everything else free-form."""
    s = (store.setting("agent.driver", "") or "").strip().lower()
    if s in ("stepwise", "freeform"):
        return s
    if kind == "local":
        return "stepwise"
    if kind == "ollama":
        if prov is not None:
            return "stepwise" if (getattr(prov, "text_tools", False) or (getattr(prov, "param_b", None) or 0) < OLLAMA_FREEFORM_MIN_B) else "freeform"
        return "stepwise"                 # /status without a live provider: the conservative answer for a small local model
    return "freeform"


def _private_host(host):
    return (not host or host in ("localhost",) or host.startswith(("127.", "10.", "192.168.", "0.", "::1", "fe80:"))
            or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host) is not None)


def net_probe_target(store=None):
    """The configured provider's host when it is a public one, else 1.1.1.1 (the local model lives on the loopback)."""
    host = None
    if store is not None:
        kind = os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude")
        if kind == "claude":
            host = urllib.parse.urlparse(ClaudeProvider.API).hostname
        elif kind in PROVIDERS and PROVIDERS[kind].get("base_url"):
            host = urllib.parse.urlparse(store.setting(kind + ".base_url", PROVIDERS[kind]["base_url"]) or "").hostname
    return NET_PROBE_HOST if _private_host(host) else host


def _net_probe(host):
    """True when a TLS connection to host:443 could be made (any HTTP answer, even an error, means the network is there)."""
    try:
        c = http.client.HTTPSConnection(host, NET_PROBE_PORT, timeout=NET_TIMEOUT_S)
        c.request("HEAD", "/", headers={"User-Agent": "FabOS-agent/1.0"})
        c.getresponse()
        c.close()
        return True
    except (ssl.SSLError, http.client.HTTPException):
        return True                              # the socket connected; only the certificate, the protocol or the HTTP answer disagreed (RemoteDisconnected, BadStatusLine)
    except (OSError, socket.timeout):
        return False                             # DNS failure, unreachable, refused, timed out
    except Exception:
        return True                              # a malformed HTTP answer still came over the network


def network_cached():
    """The LAST probe's result without touching the network: {"online": bool|None, "target": "host:443", "checked": epoch, "age_s": n}
    (online None = never probed). What /status reports — the UIs poll it every few seconds, and a poll must never be a network request."""
    with _net_lock:
        d = {k: _net[k] for k in ("online", "target", "checked")}
    d["age_s"] = int(time.time() - d["checked"]) if d["checked"] else None
    return d


def network_status(store=None):
    """Probe once (one HTTPS HEAD, NET_TIMEOUT_S, no payload) unless the cached value is younger than NET_CACHE_S, and return it.
    Called ONLY at the start of a stepwise task whose request names a web page or URL (the only tasks web_fetch can appear in);
    a managed computer's hosts_allowed list binds the probe as it binds web_fetch (no probe when the target is not allowed)."""
    d = network_cached()
    if d["online"] is not None and d["age_s"] is not None and d["age_s"] < NET_CACHE_S:
        return d
    host = net_probe_target(store)
    if not POLICY.host_allowed(host):
        return d
    online = _net_probe(host)
    with _net_lock:
        _net.update(online=online, target="%s:%d" % (host, NET_PROBE_PORT), checked=time.time())
    return network_cached()


NET_SKIPPED = {"online": None, "target": "", "checked": 0.0, "age_s": None, "skipped": True}     # the task names no web page or URL: no probe


def local_system_prompt(mode, net):
    online = net.get("online")
    line = ("ONLINE — web_fetch works, but only for tasks that name a web page or URL; the clock, files, folders and apps are local and never need it"
            if online else "OFFLINE right now (no web_fetch; the clock, files, folders and apps still work)" if online is False
            else "not checked — this task names no web page or URL, so nothing in it needs the internet; the clock, files, folders and apps are local" if net.get("skipped")
            else "unknown")
    return LOCAL_SYSTEM_PROMPT.format(app=APP, user=os.environ.get("USER", "user"), home=HOME, date=datetime.now().strftime("%Y-%m-%d"), net=line, mode=mode, browser=BROWSER)


def parse_plan(text, allowed):
    """(steps, None) from the model's plan text, or (None, why). Strict JSON first, then the first {...} block in the text."""
    text = (text or "").strip()
    doc = None
    try:
        doc = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                doc = json.loads(m.group(0))
            except ValueError:
                doc = None
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "no steps"
    if len(steps) > PLAN_MAX_STEPS:
        return None, "more than %d steps" % PLAN_MAX_STEPS
    out = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            return None, "step %d is not an object" % (i + 1)
        tool = str(s.get("tool") or "").strip()
        goal = " ".join(str(s.get("goal") or "").split())
        if tool not in allowed:
            return None, "step %d uses unknown tool %r (allowed: %s)" % (i + 1, tool, ", ".join(allowed))
        if not goal:
            return None, "step %d has no goal" % (i + 1)
        out.append({"tool": tool, "goal": goal[:300]})
    return out, None


def step_check(tool, inp, out, err):
    """Deterministic verification of a finished tool call: (ok, one-line detail). Cheap and local: exit codes, files on disk,
    processes running; never trusts the model's words."""
    if err or (isinstance(out, dict) and out.get("error")):
        return False, str((out or {}).get("error") if isinstance(out, dict) else out)[:300]
    out = out if isinstance(out, dict) else {}
    if tool == "run_shell":
        code = out.get("exit_code")
        if code != 0:
            return False, "exit code %s: %s" % (code, " ".join(((out.get("stderr") or out.get("stdout") or "")[-300:]).split()))
        so = (out.get("stdout") or "").strip()
        return True, "exit 0" + (", output: %s" % " ".join(so[:120].split()) if so else ", no output")
    if tool == "write_file":
        p = os.path.expanduser(str(inp.get("path") or ""))
        if not os.path.isfile(p):
            return False, "the file %s does not exist afterwards" % p
        try:
            with open(p, errors="replace") as f:
                data = f.read()
        except OSError as e:
            return False, str(e)
        if not inp.get("append") and data != (inp.get("content") or ""):
            return False, "the file content differs from what was written"
        return True, "%s exists, %d bytes" % (p, len(data.encode()))
    if tool == "open_app":
        name = os.path.basename(str(inp.get("app") or "").split()[0]) if inp.get("app") else ""
        if name and name not in ("xdg-open", "open"):
            time.sleep(1.0)                          # setsid -f detached it: find the application by name, not by pid
            for args in (["pgrep", "-x", name], ["pgrep", "-f", name]):
                if subprocess.run(args, capture_output=True).returncode == 0:
                    return True, "%s is running" % name
            return False, "%s is not running after open_app (is the name right? kate, konsole, dolphin, %s)" % (name, BROWSER)
        return True, "launched"
    if tool == "type_text":
        n = int(out.get("typed_chars") or 0)
        return n > 0, "typed %d characters" % n
    if tool == "web_fetch":
        text = (out.get("text") or "").strip()
        return bool(text), ("fetched %d characters: %s" % (len(text), " ".join(text[:300].split())) if text else "empty response")
    if tool == "read_file":
        return "content" in out, "read %d characters" % len(out.get("content") or "")
    if tool == "list_dir":
        return "entries" in out, "%s entries" % out.get("total")
    if tool == "send_email":
        return bool(out.get("sent")), "sent to %s" % out.get("to") if out.get("sent") else "not sent"
    if tool == "check_email":
        return "messages" in out, "%s messages" % out.get("count")
    if tool == "generate_image":
        p = os.path.expanduser(str(out.get("path") or ""))
        if not p or not os.path.isfile(p):
            return False, "no image file was saved"
        return True, "image saved: %s (%sx%s, %s)" % (p, out.get("width"), out.get("height"), out.get("provider"))
    return True, "done"


GOAL_PATH_RE = re.compile(r"(?<![\w:/])(~/[\w.@+-]+(?:/[\w.@+-]+)*|/[\w.@+-]+(?:/[\w.@+-]+)+)")   # ~/big.txt, ~/Ladder/one/date.txt, /tmp/ladder/notes — not URLs, not /tmp alone, not bare names
NO_FILES_RE = re.compile(r"\b(do not|don't|never|without) (create|creating|change|changing|modify|modifying|write|writing|touch|touching|alter|altering)\b[^.]{0,40}\bfiles?\b")
ANSWER_RE = re.compile(r"(how many|how much|what is|which |tell me|end your (reply|answer)|reply with|answer with|report the|\?)")
WEB_WORDS_RE = re.compile(r"(https?://|www\.|\b(web|url|page|site|website|online|internet|download|fetch|http)\b)")
OPEN_WORDS_RE = re.compile(r"\b(open|opens|typ(e|es|ed|ing)|window|editor|terminal|browser|app|application|watch|show|screen)\b")
# The owner's show-your-work rule in the SYSTEM_PROMPT's own words: text the user will READ (a note, a letter, a mail body, a message,
# a document, code) that the request asks to write, compose or draft is typed in Fab Editor where they can watch, then saved. A verb and a
# content noun together — "write the total into total.txt" or "create a file whose content is X" are file operations and stay windowless.
COMPOSE_RE = re.compile(r"\b(write|writes|writing|compose|composes|composing|draft|drafts|drafting|pen|jot down)\b(?:(?!\binto\b)[^.;]){0,60}?"
                        r"\b(note|notes|letter|letters|mail|e-?mail|message|memo|document|essay|poem|story|paragraph|summary|report|reply|body|code|script|program)\b")
SAVE_VERBATIM_RE = re.compile(r"\b(unchanged|exactly as|as[- ]is|verbatim|without (any )?changes?|what (it|you) (got|returned|fetched)|the (json|text|body|output) you get)\b")
# An image request (ADR-0021): a drawing verb, or a make/create verb whose DIRECT OBJECT is an image noun — the verb, an optional
# "me", an optional article or count, at most three plain modifier words ("a cute cat picture", "a 1920x1080 wallpaper"), then the
# noun. No free gap between verb and noun (reviewed 2026-09-16: "create a folder named pictures", "make a list of the images",
# "give me the number of pictures", "generate a report of the photos" all matched and the stepwise plan got a generate_image step
# in front of the user's real task). A modifier that names a container or a measure (folder, list, number, count, report, thumbnail,
# copy, backup, "of", "in", "for") ends the match; so does a noun used as a compound ("icon-sized"), followed by file/folder/viewer/
# named/called/from/under/inside, by a location ("the pictures in ~/Pictures"), or by an edit adjective ("make the logo bigger").
# "Take a screenshot" and "open the picture folder" are not requests to generate one; "make an image of", "a picture of", "draw" are.
IMAGE_NOUN_RE = r"(image|picture|photo|drawing|logo|poster|wallpaper|illustration|icon|banner|artwork|sticker)"
IMAGE_RE = re.compile(r"\b(draw|sketch|paint|illustrate)\b(?!\s+(up|out)\b)"
                      r"|\b(generate|make|create|design|render|produce|give me|i (want|need))\b\s+(me\s+)?(an?\s+|the\s+|some\s+|\d+\s+)?"
                      r"(?:(?!\b(folders?|lists?|numbers?|counts?|reports?|thumbnails?|copy|copies|backups?|viewers?|of|in|for|from|to|into|named|called)\b)[\w-]+\s+){0,3}"
                      + IMAGE_NOUN_RE + r"s?\b(?!-)(?!\s+(files?|folders?|viewers?|named|called|from|under|inside|in\s+(~|/|(the\s+folder|my)\b)"
                      r"|bigger|smaller|larger|wider|taller|transparent|brighter|darker|sharper|blurr\w*|gr[ae]yscale|round\b))"
                      r"|\b(a|an|the) (picture|image|drawing|illustration|photo) of\b", re.I)
# The desktop's brand names -> the executable open_app must start. When the task names exactly one of them, an open_app step
# that starts something else is a failed step (measured: "Open the Fab Terminal" opened dolphin).
APP_NAMES = {"konsole": ("fab terminal", "terminal", "konsole"), "kate": ("fab editor", "text editor", "kate"),
             "dolphin": ("fab files", "file manager", "dolphin"), BROWSER: ("browser", BROWSER)}


def expected_app(request):
    """The one executable the request's app name points at ('konsole' for 'Open the Fab Terminal'), or None when the request
    names none or several."""
    low = " ".join((request or "").lower().split())
    hits = [exe for exe, names in APP_NAMES.items() if any(n in low for n in names)]
    return hits[0] if len(hits) == 1 else None
GOAL_DELETE_RE = re.compile(r"\b(delete|remove|rm|erase|clean|clear|unlink|trash|empty|purge|move|mv|rename)\b", re.I)
# The user's own words that ask for something to be moved, renamed, deleted or replaced. A request without any of them (a copy,
# a count, a sum) never justifies an mv/rm in a shell step — measured on "Copy the folder /tmp/ladder/notes to ~/Ladder/notes-copy":
# the plan's second step, "Rename the copied folder", ran `mv /tmp/ladder/notes ~/Ladder/notes-copy` and the user's source folder
# was gone (the ladder's check still passed, which is why this is a deterministic guard and not a check).
CHANGE_WORDS_RE = re.compile(r"\b(delete|deletes|deleting|remove|removes|removing|rm|erase|erasing|clean|cleans|cleaning|clear|clears|clearing|unlink|trash|"
                             r"empty|empties|purge|move|moves|moving|mv|rename|renames|renaming|replace|replaces|replacing|overwrite|overwrites|overwriting|"
                             r"tidy|sort out|organi[sz]e|get rid of|throw away|discard)\b")
# The command words that move, rename or delete, as the FIRST word of a simple command (at the start, after ; & | ( ` { $( or the
# keywords do/then/else, optionally behind sudo, xargs, nice, time, command or env) — `echo rename` is not a command; plus find's
# -delete / -exec[dir] mv|rm, rsync's --delete / --remove-source-files, and Python's shutil.rmtree/move and os.remove/unlink/rename/rmdir.
# A word list, not a parser: the common forms (ADR-0020 says so); the risk classifier and the sandbox stay in front of everything else.
DESTRUCTIVE_CMD_RE = re.compile(r"(?:^|[;&|(`{]|\$\(|\b(?:do|then|else)\s)\s*(?:sudo\s+(?:-\S+\s+)*|xargs\s+(?:-\S+\s+|\d+\s+)*|nice\s+(?:-n\s*\d+\s+)?|time\s+|command\s+|env\s+(?:\S+=\S*\s+)*)?"
                                r"(mv|rm|rmdir|shred|unlink|rename)(?=\s|$)"
                                r"|\s(-delete|--delete(?:-(?:before|after|during|delay|excluded))?|--remove-source-files)(?=\s|$)"
                                r"|-exec(?:dir)?\s+(mv|rm)\s"
                                r"|\b(shutil\.(?:rmtree|move)|os\.(?:remove|unlink|rename|renames|replace|rmdir|removedirs))\s*\(")
# A plan step whose own goal renames, moves or deletes (dropped when the request never asks for that).
PLAN_CHANGE_RE = re.compile(r"\b(rename|renames|renaming|move|moves|moving|delete|deletes|deleting|remove|removes|removing|erase|purge|trash|rm|mv)\b")
# "Rename every file that ends in .txt inside ~/x so it ends in .md": the two extensions and the folders, for a deterministic
# post-condition (no *.txt may remain) — measured: `sed -i 's/.txt/.md/g'` on the files' CONTENTS exited 0, renamed nothing, and
# the model's self-check said yes.
RENAME_EXT_RE = re.compile(r"\brenam\w*\b")
EXT_RE = re.compile(r"(?<![\w/])\.([a-z0-9]{1,5})\b")


def destructive_command_reason(request, command):
    """Why this shell command may not run for this request, or None: it moves, renames or deletes (mv, rm, rmdir, shred, unlink,
    rename, find -delete / -exec rm) while the user's words never asked for anything to be moved, renamed, deleted or replaced."""
    low = " ".join((request or "").lower().split())
    if CHANGE_WORDS_RE.search(low):
        return None
    m = DESTRUCTIVE_CMD_RE.search(command or "")
    if not m:
        return None
    word = next(g for g in m.groups() if g)
    return ("the task never asks to move, rename or delete anything, so `%s` may not run here (it would change or lose the user's files);"
            " do the step with cp, mkdir, cat or a > redirect instead" % word)


def rename_expectation(request):
    """(old_ext, new_ext, [folders]) when the request asks to rename files from one extension to another inside folders it names
    (and they exist), else None. The rename step's deterministic post-condition: no file with old_ext may remain there."""
    low = " ".join((request or "").lower().split())
    if not RENAME_EXT_RE.search(low):
        return None
    exts = []
    for m in EXT_RE.finditer(low):
        e = "." + m.group(1)
        if e not in exts:
            exts.append(e)
    if len(exts) != 2:
        return None
    folders = [p for p in goal_paths(request) if os.path.isdir(os.path.expanduser(p))]
    if not folders:
        return None
    return exts[0], exts[1], folders


def rename_leftovers(exp):
    """Files still ending in the old extension inside the expectation's folders (names only, sorted)."""
    old, _new, folders = exp
    left = []
    for d in folders:
        fp = os.path.expanduser(d)
        try:
            names = sorted(os.listdir(fp))
        except OSError:
            continue
        left += [n for n in names if n.lower().endswith(old) and os.path.isfile(os.path.join(fp, n))]
    return left


def rename_hint(exp):
    old, new, folders = exp
    return "rename each one with mv, e.g. for f in %s/*%s; do mv \"$f\" \"${f%%%s}%s\"; done" % (folders[0], old, old, new)


def goal_paths(goal):
    """Filesystem paths named in a step goal, in order, de-duplicated (trailing punctuation stripped)."""
    out = []
    for m in GOAL_PATH_RE.findall(goal or ""):
        p = m.rstrip(".,;:)\"'")
        if p and p not in out:
            out.append(p)
    return out


def missing_goal_paths(goal):
    """Paths the step's goal names that do not exist afterwards — a cheap, deterministic 'did the command really do it' check for
    shell steps (exit 0 alone proves little: `date +%F` prints the date and writes nothing). Skipped for steps that delete, move or rename."""
    if GOAL_DELETE_RE.search(goal or ""):
        return []
    return [p for p in goal_paths(goal) if not os.path.exists(os.path.expanduser(p))]


def plan_reject_reason(request, plan):
    """Why a parsed plan must be planned again, or None: every step reaches for the web (web_fetch, or a goal that names a URL or
    web_fetch) while the task names no web page or URL — the small model's measured habit of inventing an API for local files."""
    low = " ".join((request or "").lower().split())
    if WEB_WORDS_RE.search(low):
        return None
    webby = [s for s in plan if s["tool"] == "web_fetch" or re.search(r"(https?://|www\.|\bweb_fetch\b|\bcurl\b|\bapi\b)", s["goal"].lower())]
    if webby and len(webby) == len(plan):
        return "every step uses the web, but the task names no web page or URL — the files are on this computer: use run_shell, list_dir, read_file, write_file or save_result on the paths the task gives"
    return None


def plan_sanity(request, plan, images_ready=True):
    """Deterministic repairs of a parsed plan from the product rules, returned as notes. Show your work (owner's rule): when the
    user asks to TYPE something and the plan opens an application, a type_text step must follow the open_app step — the small
    model regularly plans the open_app and forgets the typing. images_ready: image_capability(store)["ready"] — with no image
    provider configured a generate_image step is only put in when the plan itself tries to draw (see the image block)."""
    notes = []
    low = " ".join((request or "").lower().split())
    # A step that repeats an earlier step's goal word for word does nothing new (measured: "copy the folder A to B" planned as
    # run_shell and again as save_result, which then wrote 0 bytes into the copied folder three times and failed a task whose
    # work was done). The first occurrence stays, later repeats go; never empties the plan.
    seen, keep = set(), []
    for i, s in enumerate(plan):
        key = " ".join(s["goal"].lower().split()).rstrip(".!")
        if key in seen:
            notes.append("dropped step %d [%s]: it repeats an earlier step's goal (%s)" % (i + 1, s["tool"], s["goal"][:80]))
            continue
        seen.add(key)
        keep.append(s)
    if keep and len(keep) < len(plan):
        plan[:] = keep
    # The request never asks to move, rename or delete: a step whose own goal would is dropped — measured: "Copy the folder A to B"
    # planned as cp, then "Rename the copied folder to B", which ran mv on the source. Never empties the plan.
    if not CHANGE_WORDS_RE.search(low):
        keep = [s for s in plan if s["tool"] == "reply" or not PLAN_CHANGE_RE.search(s["goal"].lower())]
        if keep and len(keep) < len(plan):
            for i, s in enumerate(plan):
                if s not in keep:
                    notes.append("dropped step %d [%s]: it would rename, move or delete, which the task never asks for (%s)" % (i + 1, s["tool"], s["goal"][:80]))
            plan[:] = keep
    compose = COMPOSE_RE.search(low) is not None
    tools = [s["tool"] for s in plan]
    if compose and "open_app" not in tools and "write_file" in tools and len(plan) + 2 <= PLAN_MAX_STEPS:
        # Show your work (owner's rule): text the user asked to have written for them is typed where they can watch, then saved —
        # the small model plans the bare write_file; the window and the typing go in front of it.
        i = tools.index("write_file")
        paths = goal_paths(plan[i]["goal"]) or goal_paths(request)
        plan.insert(i, {"tool": "open_app", "goal": "open Fab Editor (kate)%s so the user can watch the text being written" % ((" with the file path " + paths[0]) if paths else "")})
        plan.insert(i + 1, {"tool": "type_text", "goal": "type the text the user asked for into the editor window opened in the previous step"})
        notes.append("added open_app and type_text before write_file: the user asked for text they will read, so it is typed where they can watch (show your work)")
        tools = [s["tool"] for s in plan]
    if re.search(r"\btyp(e|es|ed|ing)\b", low) and "open_app" in tools and "type_text" not in tools and len(plan) < PLAN_MAX_STEPS:
        i = tools.index("open_app")
        plan.insert(i + 1, {"tool": "type_text", "goal": "type the text the user asked for into the window opened in the previous step"})
        notes.append("added a type_text step after open_app: the user asked to type")
    # "Do not create or change any file": the user's own words — no write_file / save_result step may stay (measured: the model
    # planned save_result into the very folder it was told to leave alone). Never empties the plan.
    if NO_FILES_RE.search(low):
        keep = [s for s in plan if s["tool"] not in ("write_file", "save_result")]
        if keep and len(keep) < len(plan):
            notes.append("dropped %d file-writing step(s): the task says not to create or change files" % (len(plan) - len(keep)))
            plan[:] = keep
    # No URL or web word in the task: web_fetch has no business in the plan (measured: "Fetch the content of the 'list.txt' file" for
    # a local folder). No open/type/window word AND nothing to write or compose for the user to read (a pure file or system
    # operation: copy, count, a computed value into a file): neither have open_app / type_text. Never empties the plan.
    for tools_out, keep_if, why in (({"web_fetch"}, WEB_WORDS_RE.search(low), "the task names no web page or URL"),
                                    ({"open_app", "type_text"}, OPEN_WORDS_RE.search(low) or compose, "the task never asks to open an app, to type, or to write text the user will read")):
        if not keep_if:
            keep = [s for s in plan if s["tool"] not in tools_out]
            if keep and len(keep) < len(plan):
                notes.append("dropped %d %s step(s): %s" % (len(plan) - len(keep), "/".join(sorted(tools_out)), why))
                plan[:] = keep
    # "save the JSON you get back, unchanged": a write_file right after web_fetch/read_file would make the model retype the data
    # (measured: {"ok": true, "app": "Fab OS"} became "ok\napp=Fab OS") — the driver's save_result copies it instead.
    if SAVE_VERBATIM_RE.search(low):
        for i in range(1, len(plan)):
            if plan[i]["tool"] == "write_file" and plan[i - 1]["tool"] in ("web_fetch", "read_file"):
                plan[i]["tool"] = "save_result"
                notes.append("step %d saves the fetched text with save_result instead of retyping it: the task says to keep it unchanged" % (i + 1))
    # An image request is ONE generate_image step (ADR-0021): the small model plans `convert`/`python3 -c PIL` drawings instead, or a
    # write_file of an SVG. Such steps go; a generate_image step is put first when the plan has none. Never empties the plan.
    # When no image provider is configured (images_ready=False) the step is only put in when the plan itself tries to draw — the
    # model and the word list then agree it is an image, and the step fails once with the friendly "add a key" error instead of
    # painting with shell tools; a plan without a drawing step is left alone, so a word-list misfire cannot sink an unrelated task.
    if IMAGE_RE.search(low):
        tools = [s["tool"] for s in plan]
        if "generate_image" not in tools:
            drawing = [s for s in plan if s["tool"] in ("run_shell", "write_file", "save_result", "open_app", "type_text")
                       and re.search(r"\b(draw|paint|render|convert|magick|pil|pillow|svg|png|jpe?g|image|picture|circle|logo|poster|wallpaper|icon|canvas)\b", s["goal"].lower())]
            if drawing or images_ready:
                keep = [s for s in plan if s not in drawing]
                if drawing:
                    notes.append("dropped %d step(s) that would draw with commands or files: an image is made with generate_image" % len(drawing))
                keep.insert(0, {"tool": "generate_image", "goal": "generate the image the user described: %s" % " ".join(request.split())[:160]})
                plan[:] = keep[:PLAN_MAX_STEPS]
                notes.append("added a generate_image step first: the task asks for an image%s" % ("" if images_ready else " (no image provider is configured: the step will say what to add)"))
            else:
                notes.append("the task reads like an image request but no image provider is configured and the plan does not draw: left as planned")
    # The task asks for an answer (a count, a question, "end your reply with ...") and the plan never replies: add the reply step.
    if ANSWER_RE.search(low) and "reply" not in [s["tool"] for s in plan] and len(plan) < PLAN_MAX_STEPS:
        plan.append({"tool": "reply", "goal": "answer the user in exactly the form the task asks, using the results above"})
        notes.append("added a reply step: the task asks for an answer")
    return notes


def render_result(tool, out):
    """A tool result as the small model sees it: plain text, not the JSON envelope (the full JSON stays in the task history)."""
    if not isinstance(out, dict):
        return str(out)
    if out.get("error"):
        return "error: " + str(out["error"])
    if tool == "run_shell":
        so, se = (out.get("stdout") or "").strip(), (out.get("stderr") or "").strip()
        return "exit_code %s\nstdout: %s%s" % (out.get("exit_code"), so or "(empty)", ("\nstderr: " + se) if se else "")
    if tool in ("web_fetch", "read_file"):
        return (out.get("text") if tool == "web_fetch" else out.get("content")) or "(empty)"
    if tool == "list_dir":
        ents = out.get("entries") or []
        nd = sum(1 for e in ents if e.get("dir"))
        return "%s entries in %s (%d files, %d folders): %s" % (out.get("total"), out.get("path"), len(ents) - nd, nd, ", ".join(e["name"] + ("/" if e.get("dir") else "") for e in ents))
    if tool == "write_file":
        return "wrote %s bytes to %s" % (out.get("bytes"), out.get("path"))
    if tool == "open_app":
        return "launched: " + str(out.get("launched"))
    if tool == "type_text":
        return "typed %s characters" % out.get("typed_chars")
    if tool == "generate_image":
        return "image saved to %s (%sx%s, made by %s)" % (out.get("path"), out.get("width"), out.get("height"), out.get("provider"))
    return json.dumps(out)


def raw_result(tool, out, inp=None):
    """The data a tool call produced, verbatim — what save_result writes: stdout of run_shell, the page text of web_fetch, the
    content of read_file, the names list_dir returned (one per line), the text type_text put on screen (so "type it, then save
    it" saves exactly what was typed); None when the tool has no data output or failed."""
    if not isinstance(out, dict) or out.get("error"):
        return None
    if tool == "type_text":
        return (str((inp or {}).get("text") or "") + "\n") if out.get("typed_chars") else None
    if tool == "run_shell":
        return out.get("stdout") or ""
    if tool == "web_fetch":
        return out.get("text") or ""
    if tool == "read_file":
        return out.get("content") or ""
    if tool == "list_dir":
        return "".join(e["name"] + "\n" for e in out.get("entries") or [])
    return None


def task_paths_line(request):
    """'Paths named in the task (use them exactly): ~/Ladder/one/date.txt = /home/u/Ladder/one/date.txt; /tmp/ladder/notes' or ''.
    A 1.5B model drops directory components from paths it retypes; spelling them out (expanded) is cheap insurance."""
    paths = goal_paths(request)
    if not paths:
        return ""
    parts = []
    for p in paths[:6]:
        full = os.path.expanduser(p)
        s = "%s = %s" % (p, full) if p.startswith("~") else p
        parent = os.path.dirname(full)
        if not os.path.exists(full) and parent and not os.path.isdir(parent):      # a fact about the disk, refreshed every turn
            s += " (its folder %s does not exist yet: mkdir -p it first; write_file and save_result create it)" % parent
        parts.append(s)
    return "Paths named in the task (use them exactly): " + "; ".join(parts)


def stepwise_turn_text(request, plan, idx, results, error=None):
    """The one user message of an EXECUTE turn: the task (with its paths spelled out), the plan with the current step marked, the
    verified results so far (last one clipped to RESULT_LIMIT_STEP, earlier ones to RESULT_LIMIT_EARLIER), the previous attempt's
    error, the order."""
    lines = ["Task: " + " ".join(request.split())]
    pl = task_paths_line(request)
    if pl:
        lines.append(pl)
    lines.append("Plan:")
    for i, s in enumerate(plan):
        mark = "done" if i < idx else ("NOW" if i == idx else "later")
        lines.append("  %d. [%s] %s  (%s)" % (i + 1, s["tool"], s["goal"], mark))
    for i in range(idx):
        r = results.get(i)
        if r:
            lines.append("Result of step %d (%s): %s" % (i + 1, r["tool"], clip(r["text"], RESULT_LIMIT_STEP if i == idx - 1 else RESULT_LIMIT_EARLIER)))
    if error:
        lines.append("Your previous attempt at this step failed: %s" % " ".join(str(error).split())[:600])
        lines.append("Fix it: use a different command or different arguments, then try again.")
    step = plan[idx]
    if step["tool"] == "reply":
        lines.append("Step %d of %d: %s" % (idx + 1, len(plan), step["goal"]))
        lines.append("Write the reply text now, using only the results above. No tool call.")
    else:
        lines.append("Step %d of %d — do it now with ONE %s call: %s" % (idx + 1, len(plan), step["tool"], step["goal"]))
    return "\n".join(lines)


def provider_complete(prov, system, user, schema=None, max_tokens=600, on_usage=None):
    """One plain completion (plan, self-check, summary). Providers with `complete` enforce the JSON schema server-side; the
    others get the schema in the prompt and the caller parses/retries."""
    if hasattr(prov, "complete"):
        return prov.complete(system, user, schema=schema, max_tokens=max_tokens, on_usage=on_usage)
    if schema:
        user = user + "\n\nAnswer with one JSON object matching this schema and nothing else: " + json.dumps(schema)
    resp = prov.step(system, [{"role": "user", "content": user}], [], on_usage, max_tokens=max_tokens)
    return "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")


def kill_tree(p, grace=2.0):
    """Terminate a Popen started with start_new_session=True together with everything it spawned: SIGTERM to the whole
    process group, then SIGKILL to whatever is left of the group after `grace` seconds — checked on the GROUP (killpg with
    signal 0), not only on the leader, so a descendant that outlived a dead leader is still taken down. With the bubblewrap
    sandbox the leader is bwrap and the command lives in its PID namespace, which the kernel tears down with bwrap's init."""
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        p.terminate()
    try:
        p.wait(grace)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.killpg(p.pid, 0)            # probe: does the group still have members?
        except ProcessLookupError:
            return
        except PermissionError:
            break
        time.sleep(0.05)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
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

    def step(self, system, messages, tools, on_usage=None, **opts):
        kw = dict(model=self.model, max_tokens=int(opts.get("max_tokens") or 16000),
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  messages=messages)
        if tools:
            kw["tools"] = tools
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


TEXT_TOOL_KEYS = ("tool", "name", "function", "tool_name")
TEXT_ARG_KEYS = ("args", "arguments", "input", "parameters", "params")


def parse_text_tool_call(text, names=()):
    """The JSON-in-text tool protocol (ADR-0022): a model without native tool calling answers with ONE JSON object such as
    {"tool": "run_shell", "args": {"command": "ls"}} — also accepted: name/function/tool_name for the tool, arguments/input/parameters
    for the arguments (a dict, or a JSON string holding one), the OpenAI nesting {"function": {"name", "arguments"}}, and the object
    wrapped in a ```json fence or prose. Returns (tool_name, args_dict, (start, end)) for the FIRST object naming a known tool, else None.
    `names` empty = any tool name is accepted."""
    if not text:
        return None
    body = re.sub(r"```[a-zA-Z]*\n?", "", text).replace("```", "")
    dec = json.JSONDecoder()
    pos = 0
    while True:
        i = body.find("{", pos)
        if i < 0:
            return None
        try:
            obj, end = dec.raw_decode(body, i)
        except ValueError:
            pos = i + 1
            continue
        pos = i + 1
        if not isinstance(obj, dict):
            continue
        name, args = None, None
        fn = obj.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            name, args = fn["name"], fn.get("arguments", fn.get("args", fn.get("input")))
        else:
            for k in TEXT_TOOL_KEYS:
                if isinstance(obj.get(k), str):
                    name = obj[k]
                    break
            for k in TEXT_ARG_KEYS:
                if k in obj:
                    args = obj[k]
                    break
        if not name:
            continue
        name = name.strip()
        if names and name not in names:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_raw": args}
        if args is None:
            args = {k: v for k, v in obj.items() if k not in TEXT_TOOL_KEYS + TEXT_ARG_KEYS} if not isinstance(fn, dict) else {}
        if not isinstance(args, dict):
            args = {"_raw": json.dumps(args)}
        return name, args, (i, end)


def text_tool_protocol(tools, required=False):
    """The system-prompt suffix that replaces native tool calling: every tool as one line `name {args...}`, and the answer shape."""
    lines = ["", "TOOLS (this model has no tool-calling API, so you call a tool by answering with ONE JSON object and nothing else):",
             '{"tool": "<name>", "args": {...}}']
    for t in tools:
        props = (t.get("input_schema") or {}).get("properties") or {}
        req = (t.get("input_schema") or {}).get("required") or []
        sig = ", ".join(('"%s": <%s>' % (k, (v or {}).get("type", "value"))) + ("" if k in req else "?") for k, v in props.items())
        lines.append("- %s {%s} — %s" % (t["name"], sig, " ".join((t.get("description") or "").split())[:160]))
    lines.append("Answer with the JSON object only when you call a tool%s. Plain text (no JSON) means you are finished and it is your final message."
                 % (" (a tool call is REQUIRED for this turn)" if required else ""))
    return "\n".join(lines)


class OpenAICompatProvider:
    """Any /v1/chat/completions endpoint with tool calling (llama-server, vLLM, Ollama, other vendors). With text_tools=True the
    endpoint is used WITHOUT the tools API: the tools are described in the system prompt and the model's JSON answer is parsed
    (parse_text_tool_call) — for Ollama models whose /api/show capabilities lack "tools" (ADR-0022)."""
    name = "openai-compatible"
    result_limit = RESULT_LIMIT_CLOUD

    def __init__(self, base_url, api_key, model, name=None, result_limit=None, text_tools=False, param_b=None):
        self.base, self.key, self.model = base_url.rstrip("/"), api_key or "none", model
        self.text_tools, self.param_b = bool(text_tools), param_b
        if name:
            self.name = name
        if result_limit:
            self.result_limit = result_limit
        # Sampling per request: temperature 0.2 for every provider (as before); the local model also gets top_p 0.9 and
        # repeat_penalty 1.05 (Qwen2.5's own generation_config value; without it the 1.5B model looped ". | . | . | ..." inside
        # tool arguments) — llama-server accepts these llama.cpp fields on /v1/chat/completions; cloud requests keep their shape.
        self.sampling = {"temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05} if self.name == "local" else ({"temperature": 0.2, "top_p": 0.9} if self.name == "ollama" else {"temperature": 0.2})
        self.json_schema_ok = None                 # None = untried; False after the endpoint rejected response_format once

    def _post(self, body):
        req = urllib.request.Request(self.base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
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

    def complete(self, system, user, schema=None, max_tokens=600, on_usage=None):
        """One plain completion. With `schema`, llama-server enforces it through response_format json_schema (grammar-constrained
        sampling; verified on llama.cpp 8681); an endpoint that rejects response_format gets the schema in the prompt instead
        and the caller's parse-and-retry takes over."""
        body = {"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        body.update(self.sampling)
        enforced = bool(schema) and self.json_schema_ok is not False
        if enforced:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}
        elif schema:
            body["messages"][1]["content"] += "\n\nAnswer with one JSON object matching this schema and nothing else: " + json.dumps(schema)
        try:
            d = self._post(body)
        except RuntimeError as e:
            if enforced and re.search(r"response_format|json_schema|schema", str(e), re.I):
                LOG("%s: response_format rejected (%s) — falling back to schema-in-prompt" % (self.name, str(e)[:160]))
                self.json_schema_ok = False
                return self.complete(system, user, schema, max_tokens, on_usage)
            raise
        if enforced:
            self.json_schema_ok = True
        if on_usage and d.get("usage"):
            on_usage(d["usage"].get("prompt_tokens", 0), d["usage"].get("completion_tokens", 0))
        return (d["choices"][0]["message"].get("content") or "").strip()

    def step(self, system, messages, tools, on_usage=None, **opts):
        text_mode = bool(tools) and self.text_tools
        msgs = [{"role": "system", "content": system + (text_tool_protocol(tools, bool(opts.get("tool_choice"))) if text_mode else "")}]
        for m in messages:
            if m["role"] == "user":
                if isinstance(m["content"], str):
                    msgs.append({"role": "user", "content": m["content"]})
                    continue
                for b in m["content"]:
                    if b["type"] == "tool_result":
                        res = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                        if text_mode:          # no tools API: the result travels as a plain user turn
                            msgs.append({"role": "user", "content": "Tool result:\n" + res})
                        else:
                            msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": res})
                    elif b["type"] == "text":
                        msgs.append({"role": "user", "content": b["text"]})
            else:
                text = "".join(b.get("text", "") for b in m["content"] if b["type"] == "text")
                uses = [b for b in m["content"] if b["type"] == "tool_use"]
                if text_mode:
                    msgs.append({"role": "assistant", "content": (text + "\n" if text else "") + "\n".join(json.dumps({"tool": b["name"], "args": b["input"]}) for b in uses) or text or ""})
                    continue
                calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}} for b in uses]
                am = {"role": "assistant", "content": text or None}
                if calls:
                    am["tool_calls"] = calls
                msgs.append(am)
        body = {"model": self.model, "messages": msgs}
        body.update(self.sampling)
        if tools and not text_mode:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
            if opts.get("tool_choice"):
                body["tool_choice"] = opts["tool_choice"]        # "required": the stepwise driver wants exactly a tool call this turn
        if opts.get("max_tokens"):
            body["max_tokens"] = int(opts["max_tokens"])
        d = self._post(body)
        ch = d["choices"][0]["message"]
        content = []
        if text_mode:
            hit = parse_text_tool_call(ch.get("content") or "", [t["name"] for t in tools])
            if hit:
                name, args, (i, j) = hit
                before = (ch.get("content") or "")[:i].strip().strip("`").strip()
                if before and not before.lower().startswith("json"):
                    content.append({"type": "text", "text": before})
                content.append({"type": "tool_use", "id": "call_" + uuid.uuid4().hex[:12], "name": name, "input": args})
                if on_usage and d.get("usage"):
                    on_usage(d["usage"].get("prompt_tokens", 0), d["usage"].get("completion_tokens", 0))
                return {"content": content, "stop_reason": "tool_use"}
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
    """Deterministic scripted provider for tests and offline demos (FABOS_AGENT_PROVIDER=fake). Also scripts the stepwise
    driver (plan / one tool per turn / self-check / summary) for requests that start with "stepwise:"."""
    name = "fake"

    def __init__(self):
        self.calls = {"plan": 0, "check": 0, "finish": 0}

    # ---- stepwise scripts: (tool, goal, input, retry_input) per step of a request
    @staticmethod
    def fake_plan(req):
        low = req.lower()
        m = re.search(r"stepwise: create (\S+) with (\w+) and count it", low)
        if m:
            path, word = m.group(1), m.group(2)
            return [("write_file", "write the word %s into %s" % (word, path), {"path": path, "content": word + "\n"}, None),
                    ("run_shell", "count the words in %s" % path, {"command": "wc -w < " + path}, None),
                    ("reply", "report the word count as WORDS: <n>", None, None)]
        m = re.search(r"stepwise: count files in (\S+)", low)
        if m:
            return [("run_shell", "count the regular files in %s" % m.group(1), {"command": "find %s -maxdepth 1 -type f | wc -l" % m.group(1)}, None),
                    ("reply", "report the count as FILE COUNT: <n>", None, None)]
        if "stepwise: flaky command" in low:
            return [("run_shell", "run the flaky command", {"command": "exit 3"}, {"command": "echo recovered"})]
        if "stepwise: two calls at once" in low:
            return [("run_shell", "say first and second", {"command": "echo first"}, None)]
        if "stepwise: selfcheck no" in low:
            return [("run_shell", "print ok (selfcheck no scenario)", {"command": "echo ok"}, None)]
        if "stepwise: hopeless" in low:
            return [("run_shell", "a command that never succeeds", {"command": "exit 7"}, {"command": "exit 7"})]
        m = re.search(r"stepwise: missing parent (\S+)", low)
        if m:
            path = m.group(1)
            return [("run_shell", "write hi into %s" % path, {"command": "echo hi > %s" % path}, {"command": "mkdir -p %s && echo hi > %s" % (os.path.dirname(path), path)})]
        m = re.search(r"stepwise: fetch and save (\S+)", low)
        if m:
            return [("run_shell", "print the health JSON line", {"command": "echo '{\"ok\": true, \"app\": \"Fab OS\"}'"}, None),
                    ("save_result", "save the output of the previous step to %s exactly as it is" % m.group(1), {"path": m.group(1)}, None)]
        if "stepwise: wrong app" in low:
            return [("open_app", "open the terminal", {"app": "sleep", "args": ["8"]}, {"app": "sleep", "args": ["8"]})]
        if "stepwise: save nothing" in low:
            return [("save_result", "save the previous output to /tmp/x (there is none)", {"path": "/tmp/fabos-save-nothing.txt"}, None)]
        m = re.search(r"stepwise: save empty (\S+)", low)
        if m:                                                                       # the previous command printed nothing: nothing to save
            return [("run_shell", "copy the folder (prints nothing)", {"command": "true"}, None),
                    ("save_result", "save the previous output to %s" % m.group(1), {"path": m.group(1)}, {"path": m.group(1)})]
        m = re.search(r"stepwise: save into folder (\S+)", low)
        if m:                                                                       # the path is a directory
            return [("run_shell", "print x", {"command": "echo x"}, None),
                    ("save_result", "save the previous output to the folder %s" % m.group(1), {"path": m.group(1)}, {"path": m.group(1)})]
        if "stepwise: duplicate step" in low:
            return [("run_shell", "copy the folder a to b", {"command": "echo copied"}, None)]
        m = re.search(r"stepwise: copy folder (\S+) to (\S+)", req, re.I)
        if m:                                                                       # first attempt reaches for mv (measured); the retry copies
            return [("run_shell", "copy the folder %s to %s" % (m.group(1), m.group(2)), {"command": "mv %s %s" % (m.group(1), m.group(2))},
                     {"command": "cp -r %s %s" % (m.group(1), m.group(2))})]
        m = re.search(r"stepwise: rename ext in (\S+)", req, re.I)
        if m:                                                                       # first attempt exits 0 and renames nothing (measured: sed -i on the contents)
            return [("run_shell", "rename each .txt file in %s to .md" % m.group(1), {"command": "true"},
                     {"command": "for f in %s/*.txt; do mv \"$f\" \"${f%%.txt}.md\"; done" % m.group(1)})]
        if "stepwise: folder missing" in low:                                       # the request names a folder that no step creates
            return [("run_shell", "print hello", {"command": "echo hello"}, None)]
        if "stepwise: broken json" in low:
            return [("run_shell", "print pong", {"_raw": "{\"command\": \"echo | . | . | . |"}, {"command": "echo pong"})]
        if "stepwise: no tool" in low:
            return [("run_shell", "print pong", {"command": "echo pong"}, None)]
        m = re.search(r"stepwise: forget the redirect (\S+)", low)
        if m:
            return [("run_shell", "compute the answer and write it to %s" % m.group(1), {"command": "echo 42"}, None)]   # prints, never writes
        if "stepwise: forget the file" in low:
            return [("run_shell", "compute the answer", {"command": "echo 42"}, None)]          # never writes the file the request names
        if "stepwise: type into the editor" in low:
            return [("open_app", "open Fab Editor", {"app": "kate"}, None), ("type_text", "type hello", {"text": "hello", "delay_ms": 100}, None)]
        m = re.search(r"stepwise: draw (.+?)(?: and tell me where you saved it)?$", req, re.I)
        if m:                                                                       # an image task: one generate_image step, then the path in the reply
            return [("generate_image", "generate the image the user described: %s" % m.group(1), {"prompt": m.group(1), "size": "256x256"}, None),
                    ("reply", "tell the user where the image was saved", None, None)]
        m = re.search(r"stepwise: write a short note saying (.+?) and save it as (\S+)", req, re.I)
        if m:                                                                       # show your work: the window (a stand-in process) and the typing come before the file
            text, path = m.group(1), m.group(2)
            return [("open_app", "open Fab Editor with the file path %s" % path, {"app": "sleep", "args": ["8"]}, None),
                    ("type_text", "type the note into the editor", {"text": text, "delay_ms": 100}, None),
                    ("write_file", "save the note to %s" % path, {"path": path, "content": text + "\n"}, None)]
        return [("run_shell", "show the system", {"command": "uname -a"}, None)]

    def _stepwise_turn(self, text, tools=()):
        """An EXECUTE turn of the stepwise driver: return the scripted call for 'Step N of M' of the request on the first line."""
        req = text.split("\n", 1)[0][len("Task: "):]
        if [t["name"] for t in tools] == ["save_result"]:           # the driver offered one tool: with tool_choice=required a real model must call it
            mp = re.search(r'call save_result \{"path": "([^"]+)"\}', text)
            return {"content": [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": "save_result", "input": {"path": mp.group(1) if mp else "~/save.txt"}}], "stop_reason": "tool_use"}
        m = re.search(r"Step (\d+) of (\d+)", text)
        idx = int(m.group(1)) - 1 if m else 0
        plan = self.fake_plan(req)
        last = re.search(r"Result of step \d+ \(run_shell\): exit_code \d+\nstdout: (.*)", text)     # results are rendered as plain text
        last_out = last.group(1).strip() if last else ""
        if idx >= len(plan):
            # a repair step the driver appended (outcome check): save_result when an earlier step produced output, else write_file
            ms = re.search(r"Save the output above to (\S+) exactly", text)
            if ms:
                return {"content": [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": "save_result", "input": {"path": ms.group(1)}}], "stop_reason": "tool_use"}
            mp = re.search(r"Create (\S+) exactly", text)
            return {"content": [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": "write_file",
                                 "input": {"path": mp.group(1) if mp else "~/repair.txt", "content": last_out + "\n"}}], "stop_reason": "tool_use"}
        tool, goal, inp, retry_inp = plan[idx]
        failed = "previous attempt at this step failed" in text
        if tool == "reply":
            img = re.search(r"Result of step \d+ \(generate_image\): image saved to (.+?) \(\d+x\d+,", text)
            if img:
                return {"content": [{"type": "text", "text": "Done, the image is saved at %s." % img.group(1)}], "stop_reason": "end_turn"}
            label = "FILE COUNT" if "FILE COUNT" in goal else "WORDS"
            return {"content": [{"type": "text", "text": "%s: %s" % (label, last_out)}], "stop_reason": "end_turn"}
        if "stepwise: no tool" in req.lower() and not failed:
            return {"content": [{"type": "text", "text": "I would run echo pong now."}], "stop_reason": "end_turn"}     # forgot the tool call
        use = retry_inp if (failed and retry_inp) else inp
        calls = [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": tool, "input": use}]
        if "stepwise: two calls at once" in req.lower():
            calls.append({"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": "run_shell", "input": {"command": "echo second"}})
        return {"content": [{"type": "text", "text": "Doing step %d." % (idx + 1)}] + calls, "stop_reason": "tool_use"}

    def complete(self, system, user, schema=None, max_tokens=600, on_usage=None):
        """Plan (schema with 'steps'), self-check (schema with 'ok') or the closing summary (no schema)."""
        m = re.search(r"Task(?: from the user)?:\s*(.+)", user)
        req = m.group(1).strip() if m else user
        props = (schema or {}).get("properties") or {}
        if "steps" in props:
            self.calls["plan"] += 1
            if "bad plan first" in req.lower() and self.calls["plan"] == 1:
                return "Here is my plan: {\"steps\": [{\"tool\": \"teleport\""          # invalid JSON + unknown tool: the driver must repair
            if "invented api plan first" in req.lower() and self.calls["plan"] == 1:               # the measured habit: an API for local files
                return json.dumps({"steps": [{"tool": "run_shell", "goal": "web_fetch 'https://example.com/api/grand_total'"}, {"tool": "save_result", "goal": "save the API answer"}]})
            steps = [{"tool": t, "goal": g} for t, g, _i, _r in self.fake_plan(req)]
            if "duplicate step" in req.lower():                                                   # the model's habit: the same goal planned twice
                steps = steps + [{"tool": "save_result", "goal": steps[0]["goal"].upper() + "."}]
            if "copy folder" in req.lower():                                                      # the model's habit: a "rename the copied folder" step after the cp (measured: it ran mv on the source)
                steps = steps + [{"tool": "run_shell", "goal": "Rename the copied folder to its final name"}]
            if "write a short note" in req.lower():                                                 # the model's habit: the bare write_file, no window, no typing
                steps = [st for st in steps if st["tool"] == "write_file"]
            if "count files in" in req.lower() and "do not create" in req.lower():   # the model's habit: an answer-only task planned as run_shell + save_result, no reply
                steps = [steps[0], {"tool": "save_result", "goal": "save the count to /tmp/count.txt"}]
            return json.dumps({"steps": steps})
        if "ok" in props:
            self.calls["check"] += 1
            if "selfcheck no" in user.lower() and self.calls["check"] == 1:          # the self-check prompt carries the step goal, not the request
                return json.dumps({"ok": False, "reason": "the output does not show what the step asked for"})
            return json.dumps({"ok": True, "reason": "the result matches the goal"})
        self.calls["finish"] += 1
        return "Done, stepwise finished for '%s'." % req[:50]

    def step(self, system, messages, tools, on_usage=None, **opts):
        first = messages[0]["content"]
        full = first if isinstance(first, str) else first[0].get("text", "")
        if isinstance(full, str) and full.startswith("Task: ") and "\nPlan:\n" in full:
            return self._stepwise_turn(full, tools)
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
        elif IMAGE_RE.search(low):
            # ladder L2-g / ask-bar fixture "draw a cat": ONE generate_image step, then the saved path in the closing line (read from the
            # tool result, never invented)
            m = re.search(r"\b(?:draw|paint|sketch|make|create|generate|design)\b\s+(?:me\s+)?(?:a|an|the|some)?\s*(.+?)(?:\s+and\b|[.,;]|$)", req, re.I)
            subject = " ".join((m.group(1) if m else req).split())
            plan = [tu("generate_image", {"prompt": subject, "size": "512x512"})]
            saved = ""
            for mm in messages:
                if mm["role"] == "user" and not isinstance(mm["content"], str):
                    for b in mm["content"]:
                        if b.get("type") == "tool_result":
                            try:
                                saved = json.loads(b["content"]).get("path") or saved
                            except (ValueError, AttributeError, TypeError):
                                pass
            final = "Done, I drew %s and saved it at %s. Anything else?" % (subject, saved or "~/Pictures/Fab OS")
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
        # security tests (tests/agent-test.py): sandbox hides the keys, protected paths are refused, hosts follow policy
        elif "read my ssh key" in low:
            plan = [tu("run_shell", {"command": "cat ~/.ssh/id_rsa; ls -la ~/.ssh"})]
        elif "read the agent token" in low:
            plan = [tu("read_file", {"path": os.path.join(RUN_DIR, "token")})]
        elif "list the agent secrets" in low:
            plan = [tu("list_dir", {"path": os.path.join(CONF_DIR, "secrets")})]
        elif "show the environment" in low:
            # a shell step prints its environment and probes the ssh-agent socket: no daemon secret, no agent socket may be visible
            plan = [tu("run_shell", {"command": "env | sort; echo SOCK=${SSH_AUTH_SOCK:-unset}; test -S \"$XDG_RUNTIME_DIR/openssh_agent\" && echo AGENT-SOCKET-VISIBLE || echo agent-socket-masked"})]
        elif re.search(r"\bsay hi\b", low):
            plan = [tu("run_shell", {"command": "echo hi"})]
        elif re.search(r"\bfetch\s+(https?://\S+)", low):
            plan = [tu("web_fetch", {"url": re.search(r"\bfetch\s+(https?://\S+)", req, re.I).group(1)})]
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
        self._sandbox = None     # None = not probed yet; "bwrap" | "none"
        self._sandbox_lock = threading.Lock()
        self.sem = threading.Semaphore(int(store.setting("agent.max_parallel", "2")))
        for r in store.all("SELECT id FROM tasks WHERE status IN ('running','waiting_approval','waiting_user')"):
            store.q("UPDATE tasks SET status='failed', error='service restarted while task was running', updated=? WHERE id=?", time.time(), r["id"])
        for r in store.all("SELECT id FROM tasks WHERE status='queued'"):
            self.start(r["id"])

    def session_env(self):
        """The environment for applications launched for the user (open_app, type_text, the OAuth browser): the desktop
        session's variables (theme, display, D-Bus, PATH) so they look and behave like user-launched ones — filtered through
        the allowlist (clean_env): the daemon's own environment carries the provider key from agent.env and must not be
        inherited by anything. The daemon's values win over the session manager's (HOME, XDG_RUNTIME_DIR, PATH are what the
        daemon itself computed its paths from)."""
        env = clean_env(session_environment(), os.environ)
        env.setdefault("XDG_RUNTIME_DIR", os.path.dirname(RUN_DIR))
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_SESSION_TYPE", "wayland")
        env.setdefault("QT_QPA_PLATFORM", "wayland")
        return env

    def tool_env(self, session=None):
        """The environment for tool commands (run_shell, schedule_watch), sandboxed or not: session_env without the user's
        ssh/gpg agent variables — a command must not sign, decrypt or log in with cached keys it cannot read."""
        env = dict(session if session is not None else self.session_env())
        for k in AGENT_SOCKET_VARS:
            env.pop(k, None)
        return env

    def ai_enabled(self):
        return self.store.setting("ai.enabled", "true") == "true"

    def provider(self):
        if not self.ai_enabled():
            raise RuntimeError("System-Wide AI is OFF. Turn it on in Fab AI Controls (switch in the header) or run: fabos settings ai.enabled true")
        kind = os.environ.get("FABOS_AGENT_PROVIDER") or self.store.setting("provider", "claude")
        if not POLICY.provider_allowed(kind):
            raise RuntimeError("%s: the %s provider is not allowed here (%s). Choose an allowed provider in Fab AI Controls → Settings."
                               % (MANAGED_MSG, PROVIDERS.get(kind, {}).get("label", kind),
                                  "cloud AI is disabled" if not POLICY.cloud_allowed() else "allowed: " + ", ".join(POLICY.data.get("providers_allowed") or [])))
        if kind == "fake":
            return FakeProvider()
        if kind == "claude":
            key = get_secret("claude_api_key") or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("No Claude API key configured. Open Fab AI Controls → Settings → AI provider and paste your key, or choose another provider (Gemini, OpenAI, DeepSeek, local model).")
            POLICY.require_host(urllib.parse.urlparse(ClaudeProvider.API).hostname, "the Claude API")
            return ClaudeProvider(key, self.store.setting("claude.model", PROVIDERS["claude"]["model"]), self.store.setting("claude.fallbacks", "true") == "true")
        if kind in PROVIDERS:
            pre = PROVIDERS[kind]
            key = get_secret(pre["secret"])
            if not key and kind not in NO_KEY_PROVIDERS:
                raise RuntimeError("No %s API key configured. Open Fab AI Controls → Settings → AI provider." % pre["label"])
            base = self.store.setting(kind + ".base_url", pre["base_url"])
            POLICY.require_host(urllib.parse.urlparse(base).hostname, "the %s endpoint" % pre["label"])
            if kind == "ollama":
                # ADR-0022: the model is the user's choice or the largest installed one that fits RAM; what /api/show says about it decides
                # the tool protocol (native tools API vs JSON-in-text) and, with its size, the driver (driver_name).
                model = (self.store.setting("ollama.model", "") or "").strip() or ollama_default_model(self.store, base)
                if not model:
                    models, err = ollama_models_cached(base, force=True)
                    raise RuntimeError(err or "Ollama is running at %s but has no models installed. Pull one, e.g. `ollama pull qwen2.5:3b`, or pick another provider." % base)
                tools_ok, param_b = ollama_capabilities(model, base)
                return OpenAICompatProvider(base, key, model, name="ollama", result_limit=RESULT_LIMIT_LOCAL if (param_b or 0) < OLLAMA_FREEFORM_MIN_B else RESULT_LIMIT_CLOUD,
                                            text_tools=(tools_ok is False), param_b=param_b)
            return OpenAICompatProvider(base, key, self.store.setting(kind + ".model", pre["model"]),
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

    def driver_for(self, prov):
        """'stepwise' (small-model driver, ADR-0020) or 'freeform' (the cloud loop) for this provider — see driver_name()."""
        return driver_name(self.store, getattr(prov, "name", ""), prov)

    def model_step(self, tid, prov, system, messages, usage, tools=None, **opts):
        """One provider call. If the conversation no longer fits the model's context, compact earlier tool outputs and retry
        (twice, progressively harder) instead of failing the task with an opaque HTTP error."""
        limits = (1500, 300)  # chars per earlier tool result after the 1st and 2nd overflow
        tools = TOOLS if tools is None else tools
        for attempt in range(len(limits) + 1):
            try:
                return prov.step(system, messages, tools, usage, **opts)
            except ContextOverflow as e:
                if attempt == len(limits):
                    raise RuntimeError("The model's context window is too small for this task even after compacting tool outputs (%s). "
                                       "Use a larger context (llama-server -c) or a smaller agent.tool_result_max_chars." % e)
                limit = limits[attempt]
                n = compact_messages(messages, limit)
                self.store.step(tid, "compact", "context", str(e)[:500], "shortened %d earlier tool outputs to %d chars and retried" % (n, limit))
                LOG("task", tid, "context overflow:", str(e)[:200], "-> compacted", n, "results to", limit)

    def sandbox_name(self):
        """"bwrap" when run_shell runs inside bubblewrap here, else "none" (probed once; recorded on every step)."""
        with self._sandbox_lock:
            if self._sandbox is None:
                self._sandbox = "bwrap" if sandbox_available() else "none"
                LOG("run_shell sandbox:", self._sandbox)
            return self._sandbox

    def tools_for_model(self):
        return [t for t in TOOLS if not POLICY.tool_denied(t["name"])]

    def mode(self, task=None):
        """The effective permission mode: the task's or the user's choice, clamped to the administrator's mode_max."""
        return POLICY.clamp_mode((task or {}).get("mode") or self.store.setting("mode", "auto"))

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
        if POLICY.tool_denied(name):
            # the organisation's policy, not the user's mode: recorded as such, never asked
            reason = "%s: the %s tool is disabled by policy" % (MANAGED_MSG, name)
            sid = self.store.step(tid, "tool_call", name, json.dumps(inp)[:20000], "", risk, "denied-by-policy", narration=narration)
            self.store.activity("policy", "tool_denied", tid, name)
            return sid, False, risk, reason
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
            tools = self.tools_for_model()
            state = {"final": ""}          # the last assistant text so far: saved as the result even when the task fails

            def usage(i, o):
                self.store.q("UPDATE tasks SET cost_in=cost_in+?, cost_out=cost_out+? WHERE id=?", i, o, tid)
            try:
                if self.driver_for(prov) == "stepwise":
                    final = self._run_stepwise(tid, task, prov, usage, tools, state)
                else:
                    final = self._run_freeform(tid, task, prov, usage, tools, state)
                st = "cancelled" if tid in self.cancel else "done"
                self.store.q("UPDATE tasks SET status=?, result=?, updated=? WHERE id=?", st, final, time.time(), tid)
                self.store.step(tid, "final", "", "", final)
                self.store.activity("agent", "task_" + st, tid, final[:500])
                notify(APP + ": task %s" % st, ((task["title"] or "") + " — " + final)[:180])
            except Exception as e:
                msg = str(e) if isinstance(e, RuntimeError) else "%s: %s" % (type(e).__name__, e)
                st = "cancelled" if "cancelled" in msg else "failed"
                self.store.q("UPDATE tasks SET status=?, error=?, result=?, updated=? WHERE id=?", st, msg, state["final"], time.time(), tid)
                self.store.step(tid, "error", "", "", msg)
                self.store.activity("agent", "task_" + st, tid, msg[:500])
                notify(APP + ": task " + st, msg[:200], "critical")
            finally:
                self.cancel.discard(tid)

    def _run_call(self, tid, task, c):
        """Gate, run and record one tool call; returns (out, err, inp)."""
        inp = c["input"] if isinstance(c["input"], dict) else {}
        sid, ok, risk, reason = self._gate(tid, task, c["name"], inp)
        if not ok:
            out, err = {"error": "Denied by user/policy (%s: %s). Do not retry the same action; explain or find an allowed way." % (risk, reason)}, True
            done_line = "Sorry, that did not work: %s." % ("your organisation does not allow it" if reason.startswith(MANAGED_MSG) else "you did not allow it")
        else:
            out, err = self.tools.run(tid, c["name"], inp)
            done_line = narration_done_for(c["name"], inp, out, error=err)
        self.store.finish_step(sid, json.dumps(out), done_line)
        return out, err, inp

    def _run_freeform(self, tid, task, prov, usage, tools, state):
        """The free-form tool loop the cloud providers run: the model sees the whole conversation and decides when it is done."""
        system = build_system_prompt(self.store, self.mode(task), installed_apps())
        messages = [{"role": "user", "content": task["request"]}]
        limit = self.result_limit(prov)
        final = ""
        for turn in range(int(self.store.setting("agent.max_turns", "60"))):
            if tid in self.cancel:
                raise RuntimeError("cancelled by user")
            resp = self.model_step(tid, prov, system, messages, usage, tools)
            content = resp["content"]
            messages.append({"role": "assistant", "content": content})
            for b in content:
                if b["type"] == "text" and b["text"].strip():
                    self.store.step(tid, "assistant", prov.name, "", b["text"])
                    final = state["final"] = b["text"]
            if resp["stop_reason"] == "refusal":
                raise RuntimeError("The model declined this request (%s)." % ((resp.get("stop_details") or {}).get("category") or "policy"))
            calls = [b for b in content if b["type"] == "tool_use"]
            if not calls:
                break
            results = []
            for c in calls:
                if tid in self.cancel:
                    raise RuntimeError("cancelled by user")
                out, err, _inp = self._run_call(tid, task, c)
                res = {"type": "tool_result", "tool_use_id": c["id"], "content": clip(json.dumps(out), limit)}
                if err:
                    res["is_error"] = True
                results.append(res)
            messages.append({"role": "user", "content": results})
        else:
            final += "\n\n(stopped: reached the turn limit)"
        return final

    # ---- the small-model driver (ADR-0020): PLAN -> EXECUTE one tool per turn -> VERIFY -> FINISH
    def _plan(self, tid, prov, request, net, usage, tools):
        allowed = [t for t in STEP_TOOLS if t == "reply" or any(x["name"] == t for x in tools) or (t == "save_result" and any(x["name"] == "write_file" for x in tools))]
        system = PLAN_SYSTEM.format(app=APP, home=HOME, browser=BROWSER)
        pl = task_paths_line(request)
        user = "Task from the user:\n" + request.strip() + ("\n" + pl if pl else "") + "\n\nReturn the JSON plan."
        why = None
        for attempt in range(3):
            ask = user if not why else user + "\n\nYour previous answer was not a valid plan (%s). Return only the JSON object, tools from the list." % why
            text = provider_complete(prov, system, ask, plan_schema(allowed, plan_max_steps(request)), 600, usage)
            plan, why = parse_plan(text, allowed)
            if plan and plan_reject_reason(request, plan):
                why, plan = plan_reject_reason(request, plan), None
            if plan:
                for note in plan_sanity(request, plan, image_capability(self.store)["ready"]):
                    self.store.step(tid, "verify", "plan", "", note)
                self.store.step(tid, "assistant", prov.name, "", "Plan:\n" + "\n".join("%d. [%s] %s" % (i + 1, s["tool"], s["goal"]) for i, s in enumerate(plan)))
                return plan
            self.store.step(tid, "verify", "plan", (text or "")[:2000], "invalid plan (%s)%s" % (why, "; asking again with the error shown" if attempt < 2 else ""))
        raise RuntimeError("The model could not produce a valid plan for this task (%s)." % why)

    def _self_check(self, prov, step, name, inp, res_text, observed, usage, request=""):
        """The model's own yes/no on a verified-looking result (schema-enforced), shown the task, the result AND what the deterministic
        check observed afterwards (exit code, the file's content, the folder's names). Never overrides a deterministic failure."""
        user = "Task: %s\nGoal of the step: %s\nTool call: %s %s\nTool result: %s\nChecked afterwards: %s\nWas the goal achieved?" % (
            " ".join((request or "").split())[:400], step["goal"], name, json.dumps(inp)[:600], clip(res_text, 800), clip(observed or "", 600))
        try:
            text = provider_complete(prov, CHECK_SYSTEM, user, CHECK_SCHEMA, 60, usage)
            m = re.search(r"\{.*\}", text or "", re.S)
            d = json.loads(m.group(0)) if m else {}
            return bool(d.get("ok", True)), str(d.get("reason") or "")[:200]
        except Exception as e:                       # a broken self-check must not fail a step the deterministic check passed
            return True, "self-check unavailable (%s)" % str(e)[:80]

    def _run_stepwise(self, tid, task, prov, usage, tools, state):
        request = task["request"]
        # The probe runs only when the request names a web page or URL — the only requests a web_fetch step can survive plan_sanity in.
        # A copy, a count or a note never makes the agent touch the network (README "Nothing leaves your machine", legal/PRIVACY.md).
        net = network_status(self.store) if WEB_WORDS_RE.search(" ".join(request.lower().split())) else dict(NET_SKIPPED)
        mode = self.mode(task)
        system = local_system_prompt(mode, net)
        self_check = self.store.setting("agent.stepwise_selfcheck", "true") == "true"
        plan = self._plan(tid, prov, request, net, usage, tools)
        exec_tools = [t for t in tools if t["name"] in STEP_TOOLS]
        if any(t["name"] == "write_file" for t in exec_tools):
            exec_tools.append(SAVE_RESULT_TOOL)             # driver-only: the previous output reaches the file verbatim, the model never retypes it
        results, reply_text, repaired = {}, None, []
        rename_exp = rename_expectation(request)            # "rename … .txt … .md inside <folder>": no *.txt may remain afterwards
        last_output = None                                  # {"raw", "from"}: the most recent tool call that produced data, ok or not
        idx = 0
        while idx < len(plan):
            step = plan[idx]
            error, goal_only, short_value, failed_sigs = None, False, False, set()
            for attempt in range(STEP_RETRIES + 1):
                if tid in self.cancel:
                    raise RuntimeError("cancelled by user")
                turn = stepwise_turn_text(request, plan, idx, results, error)
                if step["tool"] == "reply":
                    resp = self.model_step(tid, prov, system, [{"role": "user", "content": turn}], usage, [], max_tokens=300)
                    text = "".join(b.get("text", "") for b in resp["content"] if b["type"] == "text").strip()
                    if text:
                        self.store.step(tid, "assistant", prov.name, "", text)
                        results[idx] = {"tool": "reply", "text": text}
                        reply_text = state["final"] = text
                        break
                    error = "the reply was empty"
                    continue
                # first attempt: the planned tool plus run_shell (the universal fallback); after a step that worked but did not produce the
                # file it names: save_result alone when it printed one short value, else save_result + write_file + run_shell (save what
                # you have); other retries: every tool, the model may change approach
                if attempt == 0:
                    offered = [t for t in exec_tools if t["name"] in (step["tool"], "run_shell")] or exec_tools
                elif goal_only and short_value:
                    # the command printed one short value and wrote nothing: the only sensible move is to save it (the hint names the path)
                    offered = [t for t in exec_tools if t["name"] == "save_result"] or exec_tools
                elif goal_only:
                    offered = [t for t in exec_tools if t["name"] in ("save_result", "write_file", "run_shell")] or exec_tools
                else:
                    offered = exec_tools
                resp = self.model_step(tid, prov, system, [{"role": "user", "content": turn}], usage, offered, tool_choice="required", max_tokens=700)
                content = resp["content"]
                for b in content:
                    if b["type"] == "text" and b["text"].strip():
                        self.store.step(tid, "assistant", prov.name, "", b["text"])
                if resp.get("stop_reason") == "refusal":
                    raise RuntimeError("The model declined this request (%s)." % ((resp.get("stop_details") or {}).get("category") or "policy"))
                calls = [b for b in content if b["type"] == "tool_use"]
                if not calls:
                    error = "no tool call was made; step %d needs one %s call" % (idx + 1, step["tool"])
                    self.store.step(tid, "verify", step["tool"], step["goal"][:300], "failed: " + error)
                    continue
                if len(calls) > 1:
                    self.store.step(tid, "verify", "one-tool-per-turn", "", "ignored %d extra tool call(s); only the first one runs" % (len(calls) - 1))
                c = calls[0]
                if tid in self.cancel:
                    raise RuntimeError("cancelled by user")
                if not isinstance(c["input"], dict) or "_raw" in c["input"]:
                    error = "the tool arguments were not valid JSON (a repeated or unterminated string); write ONE small JSON object, e.g. {\"command\": \"...\"}"
                    self.store.step(tid, "verify", c["name"], step["goal"][:300], "failed: " + error)
                    continue
                t_start = time.time()
                if c["name"] == "save_result":
                    # Deterministic guards before any write: no earlier output, an EMPTY earlier output (a cp/mv that printed nothing —
                    # measured: 0 bytes written into the copied folder, three times), or a path that is a folder.
                    save_to = os.path.expanduser(str(c["input"].get("path") or ""))
                    if not last_output:
                        error = "save_result needs an earlier tool call with output; nothing to save yet — run the command first"
                    elif not last_output["raw"].strip():
                        error = "%s printed nothing, so there is nothing to save yet; if the step's work is already done, no file needs writing — otherwise run a command that prints the value first" % last_output["from"]
                    elif os.path.isdir(save_to):
                        error = "%s is a folder, not a file; save_result needs a file path" % save_to
                    else:
                        error = None
                    if error:
                        self.store.step(tid, "verify", "save_result", step["goal"][:300], "failed: " + error)
                        continue
                    c = {"id": c["id"], "name": "write_file", "input": {"path": str(c["input"].get("path") or ""), "content": last_output["raw"]}}
                    self.store.step(tid, "verify", "save_result", step["goal"][:300], "writing the output of %s (%d chars) to %s, unchanged"
                                    % (last_output["from"], len(last_output["raw"]), c["input"]["path"]))
                if c["name"] == "run_shell":
                    # Deterministic, before the risk gate: the user's words never asked for anything to be moved, renamed or deleted, so
                    # mv/rm may not run (measured: a copy task's second step moved the source folder away).
                    reason = destructive_command_reason(request, str(c["input"].get("command") or ""))
                    if reason:
                        sig = json.dumps(c["input"], sort_keys=True)
                        error = ("you sent exactly the same call again and it was refused the same way; change the command. " if sig in failed_sigs else "") + reason
                        failed_sigs.add(sig)
                        self.store.step(tid, "verify", "run_shell", step["goal"][:300], "failed: " + error + ("" if attempt == STEP_RETRIES else "; retrying with the reason shown"))
                        continue
                out, err, inp = self._run_call(tid, task, c)
                res_text = json.dumps(out)
                raw = raw_result(c["name"], out, inp)
                if raw is not None:
                    last_output = {"raw": raw, "from": "step %d (%s)" % (idx + 1, c["name"])}
                ok, detail = step_check(c["name"], inp, out, err)
                goal_only = False
                if not ok and c["name"] == "generate_image" and err and image_config_error(out):
                    # No provider, no key, or a rejected key: nothing the model can change on a retry. The task ends with the tool's own
                    # sentence (ADR-0021 §5: "say exactly that and stop"), not with three identical attempts and "could not be completed".
                    msg = image_config_error(out)
                    self.store.step(tid, "verify", "generate_image", step["goal"][:300], "failed: " + msg + "; not retried — a setting, not the model, has to change")
                    raise RuntimeError(msg)
                if ok and c["name"] == "open_app":
                    want = expected_app(request)
                    got = os.path.basename(str(inp.get("app") or "").split()[0]) if inp.get("app") else ""
                    if want and got and got != want and got not in ("xdg-open", "open"):
                        ok, detail = False, "the task asks for %s, which is %s — you opened %s; call open_app {\"app\": \"%s\"}" % (
                            next(n for n in APP_NAMES[want] if n.startswith("fab ") or n == want).title(), want, got, want)
                if ok and c["name"] in ("run_shell", "web_fetch", "read_file", "list_dir"):
                    missing = missing_goal_paths(step["goal"])
                    if missing:
                        ok, goal_only = False, True
                        short_value = raw is not None and 0 < len(raw.strip()) <= 200 and raw.strip().count("\n") <= 2
                        where = " and ".join(missing[:3])
                        if c["name"] == "run_shell":
                            hint = "if the output above is the right value, call save_result {\"path\": \"%s\"} to write it there exactly; otherwise fix the command and append ' > %s'" % (missing[0], missing[0])
                        else:
                            hint = "%s only returns text: call save_result {\"path\": \"%s\"} to write the text above there exactly" % (c["name"], missing[0])
                        detail = "%s, but %s named in this step %s not exist afterwards — %s" % (detail, where, "does" if len(missing) == 1 else "do", hint)
                    elif c["name"] == "run_shell":
                        for p in goal_paths(step["goal"]):          # what this step just changed on disk: a small file's content, a folder's names
                            fp = os.path.expanduser(p)
                            try:
                                if os.path.isfile(fp) and os.path.getmtime(fp) >= t_start - 1 and os.path.getsize(fp) <= 2000:
                                    with open(fp, errors="replace") as f:
                                        detail += "; %s now contains: %s" % (p, " ".join(f.read()[:200].split()) or "(empty)")
                                elif os.path.isdir(fp) and os.path.getmtime(fp) >= t_start - 1:
                                    names = sorted(os.listdir(fp))
                                    detail += "; %s now holds %d entries: %s%s" % (p, len(names), ", ".join(names[:12]), ", ..." if len(names) > 12 else "")
                            except OSError:
                                pass
                if ok and c["name"] == "run_shell" and rename_exp and (RENAME_EXT_RE.search(step["goal"].lower()) or rename_exp[0] in step["goal"].lower()):
                    left = rename_leftovers(rename_exp)          # the user's own words: no file may still end in the old extension
                    if left:
                        ok = False
                        detail += "; but %d file%s in %s still end%s in %s (%s): nothing was renamed — %s" % (
                            len(left), "" if len(left) == 1 else "s", " and ".join(rename_exp[2]), "s" if len(left) == 1 else "", rename_exp[0],
                            ", ".join(left[:6]) + (", ..." if len(left) > 6 else ""), rename_hint(rename_exp))
                if ok and self_check and c["name"] not in ("open_app", "type_text"):
                    sc_ok, why = self._self_check(prov, step, c["name"], inp, res_text, detail, usage, request)
                    if not sc_ok:
                        ok, detail = False, "the model's own check says no: " + (why or "no reason given")
                if not ok and c["name"] == "run_shell" and "No such file or directory" in detail:
                    # the classic small-model loop: the same failing redirect three times. Name the missing folder and the fix.
                    for p in goal_paths(step["goal"]) + goal_paths(str(inp.get("command") or "")):
                        parent = os.path.dirname(os.path.expanduser(p))
                        if parent and not os.path.isdir(parent):
                            detail += " — the folder %s does not exist yet: create it first, e.g. mkdir -p %s && <the same command>" % (parent, parent)
                            break
                self.store.step(tid, "verify", c["name"], step["goal"][:300], ("ok: " if ok else "failed: ") + detail + ("" if ok or attempt == STEP_RETRIES else "; retrying with the error shown"))
                if ok:
                    results[idx] = {"tool": c["name"], "text": render_result(c["name"], out)}
                    break
                sig = json.dumps(inp, sort_keys=True)
                error = ("you sent exactly the same call again and it failed the same way; change the command or the arguments. " if sig in failed_sigs else "") + detail
                failed_sigs.add(sig)
            else:
                done = ", ".join("%d. %s" % (i + 1, s["goal"]) for i, s in enumerate(plan) if i in results) or "nothing yet"
                raise RuntimeError("Step %d of %d could not be completed after %d attempts (%s): %s. Done before that: %s"
                                   % (idx + 1, len(plan), STEP_RETRIES + 1, step["goal"][:120], error, done))
            idx += 1
            if idx == len(plan) and not reply_text:
                # OUTCOME CHECK before finishing: every path the user's request names must exist now (deletes/moves excepted).
                # A small model often does the work and never writes the file; one repair step per missing path, at most two.
                if rename_exp and "rename" not in repaired:
                    left = rename_leftovers(rename_exp)
                    if left:                                     # the user's rename is not done: one repair step; the step's own check then decides
                        repaired.append("rename")
                        plan.append({"tool": "run_shell", "goal": "Rename the %d file%s in %s that still end in %s so they end in %s instead (mv each one, same base names): %s"
                                     % (len(left), "" if len(left) == 1 else "s", " and ".join(rename_exp[2]), rename_exp[0], rename_exp[1], ", ".join(left[:6]))})
                        self.store.step(tid, "verify", "outcome", " and ".join(rename_exp[2]), "%d file(s) still end in %s after the plan (%s); adding a run_shell step to rename them"
                                        % (len(left), rename_exp[0], ", ".join(left[:6])))
                for p in missing_goal_paths(request)[:2]:
                    if p in repaired:
                        continue
                    repaired.append(p)
                    if not os.path.splitext(os.path.basename(p))[1]:
                        # It looks like a folder (no extension). No step's output can stand in for a folder, and writing a FILE at its path
                        # would be wrong (measured: three write_file attempts onto the copied folder) — the task fails, honestly.
                        done = ", ".join("%d. %s" % (i + 1, s["goal"]) for i, s in enumerate(plan) if i in results) or "nothing"
                        self.store.step(tid, "verify", "outcome", p, "the task names %s but it does not exist after the plan, and it looks like a folder: nothing can be written there" % p)
                        raise RuntimeError("The task names %s but it does not exist after the plan (it looks like a folder, which no step's output can stand in for). Done: %s" % (p, done))
                    if last_output and last_output["raw"].strip():
                        # an earlier step produced the value: the driver copies it, the model only confirms the path
                        plan.append({"tool": "save_result", "goal": "Save the output above to %s exactly (the task names it and it does not exist yet)" % p})
                    else:
                        plan.append({"tool": "write_file", "goal": "Create %s exactly as the task asks, using the results above (it does not exist yet)" % p})
                    self.store.step(tid, "verify", "outcome", p, "the task names %s but it does not exist after the plan; adding a %s step to create it" % (p, plan[-1]["tool"]))
        if reply_text:
            return reply_text
        lines = ["Task: " + " ".join(request.split()), "Steps done and verified:"]
        for i, s in enumerate(plan):
            lines.append("%d. [%s] %s -> %s" % (i + 1, s["tool"], s["goal"], clip(results[i]["text"], 300) if i in results else "?"))
        try:
            final = provider_complete(prov, FINISH_SYSTEM.format(app=APP), "\n".join(lines) + "\n\nWrite the closing message now.", None, 150, usage).strip()
        except Exception as e:
            LOG("task", tid, "summary call failed:", str(e)[:200])
            final = ""
        if not final:
            final = "Done: %d step%s completed and verified — %s." % (len(plan), "" if len(plan) == 1 else "s", "; ".join(s["goal"] for s in plan)[:400])
        state["final"] = final
        return final


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
                senv = self.agent.session_env()
                prefix = sandbox_argv(HOME, POLICY.sandbox_network(), env=senv) if self.agent.sandbox_name() == "bwrap" else []
                r = subprocess.run(prefix + ["bash", "-lc", spec.get("command") or "true"], capture_output=True, text=True, timeout=120, env=self.agent.tool_env(senv))
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
    if not POLICY.provider_allowed(kind):
        return {"ok": False, "detail": "%s: this provider is not allowed" % MANAGED_MSG, "latency_ms": 0, "provider": kind, "model": model or ""}
    pre = PROVIDERS[kind]
    key = (api_key or "").strip() or get_secret(pre["secret"]) or (os.environ.get("ANTHROPIC_API_KEY") if kind == "claude" else None) or ""
    model = (model or store.setting(kind + ".model") or pre["model"]).strip()
    base = (base_url or store.setting(kind + ".base_url") or pre.get("base_url") or "").strip().rstrip("/")
    if not key and kind not in NO_KEY_PROVIDERS:
        return {"ok": False, "detail": "no API key", "latency_ms": 0, "provider": kind, "model": model}
    if kind == "ollama" and not model:
        model = ollama_default_model(store, base) or ""
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
        if kind == "ollama":
            return {"ok": False, "detail": "cannot reach Ollama at %s — is it installed and running? (`fabos ollama install`, then `ollama serve` or its service)" % base, "latency_ms": ms, "provider": kind, "model": model}
        return {"ok": False, "detail": "cannot reach provider (%s)" % str(why)[:80], "latency_ms": ms, "provider": kind, "model": model}
    ms = int((time.time() - t0) * 1000)
    ids = []
    if isinstance(body, dict):
        for m in (body.get("data") or body.get("models") or []):
            mid = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
            if mid:
                ids.append(str(mid).split("/")[-1])
    out = {"ok": True, "latency_ms": ms, "detail": "Connected", "models_sample": ids[:8], "provider": kind, "model": model}
    if kind == "ollama" and not ids:
        out["detail"] = "Connected, but no models are installed — pull one first, e.g. `ollama pull qwen2.5:3b` (fits 4 GB) or `ollama pull llama3.1:8b` (8 GB)"
    elif ids and model and model != "local" and not any(model == i or model in i for i in ids):
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
    POLICY.require_host(cfg["smtp_host"], "mail (SMTP)")
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
            POLICY.require_host(host, "mail (IMAP)")
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
    if not POLICY.provider_allowed(kind) or not POLICY.cloud_allowed():
        return None, None, None, {"ok": False, "detail": "%s: cloud speech is not allowed" % MANAGED_MSG, "backend": "none", "provider": kind}
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


# ----------------------------------------------------------------------------- images (ADR-0021): generate_image by the user's own provider
IMAGE_PROVIDERS = ("openai", "gemini")           # cloud providers with an image API behind the same key the user already pasted
IMAGE_TIMEOUT = 180
IMAGE_SIZE_RE = re.compile(r"^\s*(\d{2,4})\s*[xX×]\s*(\d{2,4})\s*$")
IMAGE_DEFAULT_SIZE = (1024, 1024)
IMAGE_NO_PROVIDER = "This provider cannot generate images; add an OpenAI or Gemini key in Settings, or a local image endpoint"
IMAGE_KEY_UNREADABLE = "the stored secret exists but could not be decrypted"
# A generate_image failure the model cannot repair by trying again: no provider / no key / a wrong setting / a rejected key. The
# stepwise driver ends the task with this sentence instead of spending its retries (the text is the tool's own error, type prefix off).
IMAGE_CONFIG_ERR_RE = re.compile(r"cannot generate images|images\.provider (is|must be)|images\.local_endpoint is empty|rejected the key|could not be read|" + re.escape(MANAGED_MSG))


def image_config_error(out):
    """The tool's error sentence when a generate_image result is a configuration failure (see IMAGE_CONFIG_ERR_RE), else None."""
    err = str((out or {}).get("error") or "") if isinstance(out, dict) else ""
    if not err or not IMAGE_CONFIG_ERR_RE.search(err):
        return None
    return re.sub(r"^\w*(Error|Exception)\w*: ", "", err, count=1).strip()
IMAGE_MODELS = {"openai": "gpt-image-1", "openai_fallback": "dall-e-3", "gemini": "gemini-2.5-flash-image"}
IMAGE_SETTINGS = {"images.provider": "", "images.local_endpoint": "", "images.local_model": "", "images.openai_model": IMAGE_MODELS["openai"], "images.gemini_model": IMAGE_MODELS["gemini"]}
IMAGE_PROVIDER_CHOICES = ("", "auto", "openai", "gemini", "local")
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def images_dir():
    """~/Pictures/Fab OS — created on first use."""
    return os.path.join(HOME, "Pictures", APP)


def image_slug(prompt, limit=40):
    slug = re.sub(r"[^a-z0-9]+", "-", (prompt or "").lower()).strip("-")
    slug = slug[:limit].rstrip("-")
    return slug or "image"


def image_size(value):
    """(width, height) from 'WxH' (64..4096 each), else the default 1024x1024."""
    m = IMAGE_SIZE_RE.match(str(value or ""))
    if not m:
        return IMAGE_DEFAULT_SIZE
    w, h = int(m.group(1)), int(m.group(2))
    if not (64 <= w <= 4096 and 64 <= h <= 4096):
        return IMAGE_DEFAULT_SIZE
    return w, h


def png_size(data):
    """(width, height) read from a PNG's IHDR, or None when the bytes are not a PNG."""
    if not data or not data.startswith(PNG_SIG) or len(data) < 24:
        return None
    import struct
    w, h = struct.unpack(">II", data[16:24])
    return (w, h) if w and h else None


def image_ext(data):
    if data.startswith(PNG_SIG):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _openai_size(w, h):
    """gpt-image-1 accepts 1024x1024, 1536x1024 and 1024x1536: the nearest one by orientation."""
    if w == h:
        return "1024x1024"
    return "1536x1024" if w > h else "1024x1536"


GEMINI_ASPECTS = ("1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "21:9")     # gemini-2.5-flash-image's imageConfig.aspectRatio values
IMAGEN_ASPECTS = ("1:1", "4:3", "3:4", "16:9", "9:16")                                          # Imagen's parameters.aspectRatio values


def _aspect(w, h, allowed=GEMINI_ASPECTS):
    """The documented aspect ratio nearest to w:h (1536x1024 -> 3:2 for Gemini, 4:3 for Imagen; 1920x1080 -> 16:9)."""
    import math
    want = math.log(w / float(h))
    return min(allowed, key=lambda a: abs(math.log(int(a.split(":")[0]) / float(a.split(":")[1])) - want))


def image_provider(store):
    """Which provider makes the pictures: the setting images.provider when set (openai | gemini | local), else the active provider when it
    can (openai, gemini, fake, or local/ollama with images.local_endpoint), else — 'auto' — the first of OpenAI key, Gemini key, local
    endpoint the user has configured. Returns (kind, detail) with kind None when nothing can make an image (detail = the friendly error).
    No network."""
    want = (store.setting("images.provider", "") or "").strip().lower()
    active = os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude")
    endpoint = (store.setting("images.local_endpoint", "") or "").strip()

    def ok(kind):
        # has_secret, not get_secret: whether a key EXISTS is all the capability needs, and /status asks every few seconds (get_secret
        # spawns systemd-creds); the key itself is read once, in generate_images
        if not POLICY.provider_allowed(kind):
            return False
        if kind == "openai":
            return has_secret("openai_api_key")
        if kind == "gemini":
            return has_secret("gemini_api_key")
        if kind == "local":
            return bool(endpoint)
        return kind == "fake"
    if want and want != "auto":
        if want not in IMAGE_PROVIDER_CHOICES:
            return None, "images.provider must be one of openai, gemini, local (or empty for automatic)"
        if ok(want):
            return want, "images.provider=" + want
        return None, {"openai": "images.provider is openai but no OpenAI key is stored: add it in Settings", "gemini": "images.provider is gemini but no Gemini key is stored: add it in Settings",
                      "local": "images.provider is local but images.local_endpoint is empty: set it to your image server's /v1 URL"}[want]
    if active == "fake":
        return "fake", "the test provider draws a placeholder"
    if active in IMAGE_PROVIDERS and ok(active):
        return active, "the active provider (%s) generates images with the same key" % PROVIDERS[active]["label"]
    if active in ("local", "ollama") and ok("local"):
        return "local", "the local image endpoint " + endpoint
    for kind in ("openai", "gemini", "local"):
        if ok(kind):
            return kind, "the active provider (%s) cannot generate images; using the %s the user configured" % (
                PROVIDERS.get(active, {}).get("label", active), {"openai": "OpenAI key", "gemini": "Gemini key", "local": "local image endpoint"}[kind])
    return None, IMAGE_NO_PROVIDER


def image_capability(store):
    """For /status and the ladder: {provider, ready, detail} without touching the network."""
    kind, detail = image_provider(store)
    return {"provider": kind or "", "ready": bool(kind), "detail": detail}


def _image_post(url, headers, body, timeout=IMAGE_TIMEOUT, label="the image provider"):
    """POST JSON, return the parsed JSON; HTTP errors become RuntimeErrors carrying the status and the provider's own message."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=dict(headers, **{"Content-Type": "application/json", "Accept": "application/json"}), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:1500]
        try:
            err = json.loads(raw).get("error", raw)
            msg = err.get("message") if isinstance(err, dict) else str(err)
        except Exception:
            msg = raw
        if e.code in (401, 403):
            raise ImageHTTPError(e.code, "%s rejected the key (HTTP %d): %s" % (label, e.code, (msg or "").strip()[:300]))
        raise ImageHTTPError(e.code, "%s error HTTP %d: %s" % (label, e.code, (msg or "").strip()[:300]))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError("cannot reach %s at %s: %s" % (label, url.split("?")[0], getattr(e, "reason", None) or e))


class ImageHTTPError(RuntimeError):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _b64_images(items, key):
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get(key):
            try:
                out.append(base64.b64decode(it[key]))
            except (ValueError, TypeError):
                continue
    return out


def openai_images(base, key, model, prompt, size, n):
    """POST {base}/v1/images/generations (gpt-image-1 returns base64 PNG by default; dall-e-* needs response_format=b64_json). When
    gpt-image-1 is refused for this account (a 400/403/404 that names the model or a verification), the call is retried once with dall-e-3."""
    w, h = size
    POLICY.require_host(urllib.parse.urlparse(base).hostname, "the OpenAI image API")
    headers = {"Authorization": "Bearer " + key}

    def call(m, count):
        body = {"model": m, "prompt": prompt, "n": count, "size": _openai_size(w, h) if m.startswith("gpt-image") else {"1024x1024": "1024x1024", "1536x1024": "1792x1024", "1024x1536": "1024x1792"}[_openai_size(w, h)]}
        if m.startswith("dall-e"):
            body["response_format"] = "b64_json"
        return _b64_images(_image_post(base.rstrip("/") + "/images/generations", headers, body, label="OpenAI").get("data"), "b64_json")
    try:
        return model, call(model, n)
    except ImageHTTPError as e:
        low = str(e).lower()
        if model != IMAGE_MODELS["openai_fallback"] and e.status in (400, 403, 404) and re.search(r"model|verif|not (found|available|supported)|access", low):
            LOG("openai images: %s refused (%s) — falling back to %s" % (model, str(e)[:120], IMAGE_MODELS["openai_fallback"]))
            blobs = []
            for _ in range(n):
                blobs += call(IMAGE_MODELS["openai_fallback"], 1)        # dall-e-3 accepts n=1 per request
            return IMAGE_MODELS["openai_fallback"], blobs
        raise


def gemini_images(base, key, model, prompt, size, n):
    """The documented Gemini API shapes for an API key: models/{model}:generateContent with responseModalities IMAGE+TEXT (image-capable
    models such as gemini-2.5-flash-image; the picture comes back as inlineData), or models/imagen-*:predict (Imagen; predictions[].bytesBase64Encoded)."""
    w, h = size
    native = gemini_native_base(base)
    POLICY.require_host(urllib.parse.urlparse(native).hostname, "the Gemini image API")
    headers = {"x-goog-api-key": key}
    if model.startswith("imagen"):
        d = _image_post("%s/models/%s:predict" % (native, model), headers, {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": n, "aspectRatio": _aspect(w, h, IMAGEN_ASPECTS)}}, label="Gemini (Imagen)")
        return model, _b64_images(d.get("predictions"), "bytesBase64Encoded")
    blobs = []
    for _ in range(n):
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": _aspect(w, h)}}}
        d = _image_post("%s/models/%s:generateContent" % (native, model), headers, body, label="Gemini")
        for cand in d.get("candidates") or []:
            for part in ((cand.get("content") or {}).get("parts") or []):
                blob = part.get("inlineData") or part.get("inline_data")
                if isinstance(blob, dict) and blob.get("data"):
                    try:
                        blobs.append(base64.b64decode(blob["data"]))
                    except (ValueError, TypeError):
                        pass
        if not blobs:
            block = (d.get("promptFeedback") or {}).get("blockReason")
            text = " ".join(p.get("text", "") for c in d.get("candidates") or [] for p in ((c.get("content") or {}).get("parts") or []) if p.get("text"))
            raise RuntimeError("Gemini returned no image%s%s" % ((" (blocked: %s)" % block) if block else "", (": " + text[:200]) if text else ""))
    return model, blobs


def local_images(endpoint, key, model, prompt, size, n):
    """Any OpenAI-compatible /v1/images/generations on this computer or the LAN (stable-diffusion.cpp's server, an Automatic1111 with the
    OpenAI-compatible extension, LocalAI): images.local_endpoint is the /v1 base or the full /images/generations URL."""
    w, h = size
    url = endpoint.rstrip("/")
    if not url.endswith("/images/generations"):
        url += "/images/generations"
    POLICY.require_host(urllib.parse.urlparse(url).hostname, "the local image endpoint")
    headers = {"Authorization": "Bearer " + (key or "none")}
    body = {"prompt": prompt, "n": n, "size": "%dx%d" % (w, h), "response_format": "b64_json"}
    if model:
        body["model"] = model
    d = _image_post(url, headers, body, label="the local image endpoint")
    blobs = _b64_images(d.get("data"), "b64_json")
    for it in d.get("data") or []:                # a server that only answers with URLs: fetch them (same host policy)
        if isinstance(it, dict) and it.get("url") and not it.get("b64_json"):
            POLICY.require_host(urllib.parse.urlparse(it["url"]).hostname, "the local image endpoint")
            with urllib.request.urlopen(urllib.request.Request(it["url"], headers={"User-Agent": "FabOS-agent/1.0"}), timeout=60) as r:
                blobs.append(r.read(50_000_000))
    return model or "local", blobs


def fake_images(prompt, size, n):
    """The test provider's placeholder: a real PNG (a filled disc whose colour follows the prompt) drawn with zlib only — so the
    ask-bar fixture, the ladder and the unit tests get a genuine file under ~/Pictures/Fab OS without any network."""
    import struct
    import zlib
    w, h = min(size[0], 1024), min(size[1], 1024)
    low = (prompt or "").lower()
    colour = next((c for name, c in (("blue", (59, 110, 245)), ("red", (220, 60, 60)), ("green", (52, 168, 83)), ("yellow", (250, 204, 21)), ("black", (20, 20, 20)), ("orange", (245, 130, 32)))
                   if name in low), (120, 90, 200))
    cx, cy, r = w / 2.0, h / 2.0, min(w, h) * 0.32
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            inside = (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r
            raw += bytes(colour if inside else (250, 250, 252))

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    png = PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    return "fake-disc", [png] * n


def generate_images(store, prompt, size, n):
    """(provider_kind, model, [image bytes]) for the prompt, or a RuntimeError the user can act on."""
    kind, detail = image_provider(store)
    if not kind:
        raise RuntimeError(detail)
    if kind == "fake":
        model, blobs = fake_images(prompt, size, n)
    elif kind in IMAGE_PROVIDERS:
        key = get_secret(kind + "_api_key")                      # read here, once per picture — never in the capability probe
        if not key:
            raise RuntimeError("the %s key could not be read (%s); add it again in Settings" % (PROVIDERS[kind]["label"], IMAGE_KEY_UNREADABLE))
        if kind == "openai":
            model, blobs = openai_images(store.setting("openai.base_url", PROVIDERS["openai"]["base_url"]), key,
                                         (store.setting("images.openai_model") or IMAGE_MODELS["openai"]).strip(), prompt, size, n)
        else:
            model, blobs = gemini_images(store.setting("gemini.base_url", PROVIDERS["gemini"]["base_url"]), key,
                                         (store.setting("images.gemini_model") or IMAGE_MODELS["gemini"]).strip(), prompt, size, n)
    else:
        model, blobs = local_images(store.setting("images.local_endpoint", ""), get_secret("local_api_key"), (store.setting("images.local_model") or "").strip(), prompt, size, n)
    blobs = [b for b in blobs if b]
    if not blobs:
        raise RuntimeError("%s returned no image data for this prompt" % kind)
    return kind, model, blobs


def save_images(blobs, prompt, size):
    """Write every image to ~/Pictures/Fab OS/<yyyy-mm-dd>-<slug>-<n>.png (a suffix -2, -3… when the name is taken); returns [{path, width, height}]."""
    folder = images_dir()
    os.makedirs(folder, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    slug = image_slug(prompt)
    out = []
    for i, data in enumerate(blobs, 1):
        ext = image_ext(data)
        base = "%s-%s-%d" % (day, slug, i)
        path = os.path.join(folder, base + ext)
        k = 2
        while True:                                           # O_EXCL decides, not a stat: two tasks saving the same slug at once both get a file
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                break
            except FileExistsError:
                path = os.path.join(folder, "%s-%d%s" % (base, k, ext))
                k += 1
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        wh = png_size(data) or size
        out.append({"path": path, "width": wh[0], "height": wh[1]})
    return out


# ----------------------------------------------------------------------------- Ollama (ADR-0022): the user's own install, OpenAI-compatible at /v1
OLLAMA_TIMEOUT = 5
OLLAMA_CACHE_S = 30
OLLAMA_FREEFORM_MIN_B = 7.0             # a model this size or larger WITH native tool calling runs the free-form loop; smaller ones the stepwise driver
# The default model is the largest installed one that fits this machine's RAM (documented in README and ADR-0022):
#   MemTotal up to 4.5 GiB -> up to 3B parameters · up to 8.5 GiB -> 8B · up to 16.5 GiB -> 14B · up to 32.5 GiB -> 34B · more -> 72B
OLLAMA_RAM_TABLE = ((4.5, 3.0), (8.5, 8.0), (16.5, 14.0), (32.5, 34.0), (None, 72.0))
OLLAMA_CLASS_SLACK = 1.10               # "3B" means the 3B class: Ollama reports qwen2.5:3b as 3.1B and llama3.2:3b as 3.2B, qwen3:14b as 14.8B
OLLAMA_INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh"
OLLAMA_INSTALL_TIMEOUT = 1800
_ollama_cache = {"at": 0.0, "base": "", "models": None, "error": ""}
_ollama_lock = threading.Lock()


def ollama_native_base(base_url):
    """The stored endpoint is the OpenAI-compatible one (…:11434/v1); the native API (/api/tags, /api/show) sits one level up."""
    b = (base_url or PROVIDERS["ollama"]["base_url"]).rstrip("/")
    return b[:-3] if b.endswith("/v1") else b


def parse_param_b(text):
    """'7.6B' -> 7.6, '1.5b' -> 1.5, '137M' -> 0.137, else None."""
    m = re.match(r"^\s*([\d.]+)\s*([bBmMkK])\b", str(text or ""))
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v / 1000.0 if m.group(2) in "mM" else (v / 1e6 if m.group(2) in "kK" else v)


def ollama_models(base_url=None, timeout=OLLAMA_TIMEOUT):
    """GET /api/tags -> [{name, size, parameter_size, quantization, family, modified_at, parameter_b}] (largest first). Raises RuntimeError
    with a message the user can act on when Ollama is not running."""
    native = ollama_native_base(base_url)
    POLICY.require_host(urllib.parse.urlparse(native).hostname, "Ollama")
    try:
        with urllib.request.urlopen(urllib.request.Request(native + "/api/tags", headers={"Accept": "application/json"}), timeout=timeout) as r:
            d = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Ollama answered HTTP %d on /api/tags at %s" % (e.code, native))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise RuntimeError("cannot reach Ollama at %s (%s) — is it installed and running? `fabos ollama install` prints the official installer; `ollama serve` or its systemd service starts it"
                           % (native, getattr(e, "reason", None) or e))
    out = []
    for m in d.get("models") or []:
        det = m.get("details") or {}
        out.append({"name": m.get("name") or m.get("model") or "?", "size": int(m.get("size") or 0), "parameter_size": det.get("parameter_size") or "",
                    "quantization": det.get("quantization_level") or "", "family": det.get("family") or "", "modified_at": m.get("modified_at") or "",
                    "parameter_b": parse_param_b(det.get("parameter_size"))})
    out.sort(key=lambda x: (-(x["parameter_b"] or 0), -x["size"], x["name"]))
    return out


def ollama_models_cached(base_url=None, force=False, refresh=True):
    """(models, error) with a 30 s cache. refresh=False never touches the network (the /status poll); force=True ignores the cache."""
    native = ollama_native_base(base_url)
    with _ollama_lock:
        fresh = _ollama_cache["base"] == native and time.time() - _ollama_cache["at"] < OLLAMA_CACHE_S
        if (fresh and not force) or not refresh:
            return (_ollama_cache["models"] if _ollama_cache["base"] == native else None), (_ollama_cache["error"] if _ollama_cache["base"] == native else "")
    try:
        models, err = ollama_models(base_url), ""
    except RuntimeError as e:
        models, err = None, str(e)
    with _ollama_lock:
        _ollama_cache.update(at=time.time(), base=native, models=models, error=err)
    return models, err


def mem_total_bytes():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def ollama_max_params_b(mem_bytes=None):
    """The largest model size (billions of parameters) OLLAMA_RAM_TABLE allows for this much RAM."""
    gib = (mem_bytes if mem_bytes is not None else mem_total_bytes()) / float(2 ** 30)
    for bound, max_b in OLLAMA_RAM_TABLE:
        if bound is None or gib <= bound:
            return max_b
    return OLLAMA_RAM_TABLE[-1][1]


def ollama_pick_model(models, mem_bytes=None):
    """The default model: the largest installed one whose parameter count fits the RAM table; when none fits, the smallest installed one
    (Ollama will still try, and the README says why it may be slow); None without models. Models without a known size count as fitting."""
    if not models:
        return None
    limit = ollama_max_params_b(mem_bytes) * OLLAMA_CLASS_SLACK
    fit = [m for m in models if (m.get("parameter_b") or 0) <= limit]
    pool = fit or sorted(models, key=lambda m: (m.get("parameter_b") or 0, m.get("size") or 0))[:1]
    return max(pool, key=lambda m: (m.get("parameter_b") or 0, m.get("size") or 0))["name"]


def ollama_default_model(store, base_url=None, refresh=True):
    """The setting ollama.model, else the picked default from the (cached) model list ('' when Ollama is down or empty)."""
    chosen = (store.setting("ollama.model", "") or "").strip()
    if chosen:
        return chosen
    models, _err = ollama_models_cached(base_url or store.setting("ollama.base_url", PROVIDERS["ollama"]["base_url"]), refresh=refresh)
    return ollama_pick_model(models) or ""


def ollama_show(model, base_url=None, timeout=OLLAMA_TIMEOUT):
    """POST /api/show {model} -> the model's record (capabilities, details, model_info) or {} when unavailable."""
    native = ollama_native_base(base_url)
    try:
        req = urllib.request.Request(native + "/api/show", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode() or "{}")
        return d if isinstance(d, dict) else {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {}


def ollama_capabilities(model, base_url=None):
    """(tools, parameter_b) for a model: tools True/False from /api/show's capabilities list, None when Ollama did not say (older
    servers) — the provider then uses the native tools API and lets the server complain. parameter_b from details.parameter_size."""
    d = ollama_show(model, base_url)
    caps = d.get("capabilities")
    tools = ("tools" in caps) if isinstance(caps, list) else None
    param_b = parse_param_b((d.get("details") or {}).get("parameter_size"))
    if param_b is None:
        for m in ollama_models_cached(base_url, refresh=False)[0] or []:
            if m["name"] == model:
                param_b = m.get("parameter_b")
    return tools, param_b


def ollama_status(store, refresh=True):
    """One JSON object for `fabos ollama status` and the UIs: installed (the binary), running (/api/tags answers), the models, the
    effective model and how it was chosen, the RAM bound. refresh=False = cached only (never a network call)."""
    base = store.setting("ollama.base_url", PROVIDERS["ollama"]["base_url"])
    models, err = ollama_models_cached(base, refresh=refresh)
    mem = mem_total_bytes()
    chosen = (store.setting("ollama.model", "") or "").strip()
    picked = ollama_pick_model(models)
    return {"installed": bool(shutil.which("ollama")), "running": models is not None, "base_url": base, "native_url": ollama_native_base(base), "error": err,
            "models": models or [], "model": chosen or picked or "", "model_source": "setting ollama.model" if chosen else ("largest installed model that fits %.1f GiB of RAM" % (mem / 2 ** 30) if picked else "none"),
            "ram_gib": round(mem / 2 ** 30, 1), "max_parameters_b": ollama_max_params_b(mem), "install_command": OLLAMA_INSTALL_CMD, "bundled": False,
            "driver": driver_name(store, "ollama") if not chosen and not picked else None}


def parse_since(v):
    """'24h' | '7d' | '90m' | epoch seconds | ISO 8601 date/time | None -> epoch seconds (0 = everything)."""
    if v in (None, "", 0, "0"):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip()
    m = re.match(r"^(\d+)([smhd])$", t)
    if m:
        return time.time() - int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if re.match(r"^\d+(\.\d+)?$", t):
        return float(t)
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("since must be like 24h, 7d, an epoch time or an ISO 8601 date")
    if d.tzinfo is None:
        d = d.astimezone()
    return d.timestamp()


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
                ready = prov == "fake" or prov in NO_KEY_PROVIDERS or (prov in PROVIDERS and (has_secret(PROVIDERS[prov]["secret"]) or (prov == "claude" and bool(os.environ.get("ANTHROPIC_API_KEY")))))
                mcfg = mail_config(store)
                return self._send(200, {"mode": agent.mode(), "mode_setting": store.setting("mode", "auto"), "provider": prov, "provider_ready": ready and POLICY.provider_allowed(prov),
                                        "provider_allowed": POLICY.provider_allowed(prov), "ai_enabled": agent.ai_enabled(),
                                        # "policy": the administrator's /etc/fabos/policy.json as loaded (managed=true when any key is set) — UIs show
                                        # "Managed by your organisation" from it. root_path/sandbox: how root and run_shell are reached on this machine.
                                        "policy": POLICY.status(), "root_path": "polkit" if root_argv("x")[0] == "pkexec" else "sudo", "sandbox": agent.sandbox_name(),
                                        # driver: how this provider's tasks run (ADR-0020); network: the cached online probe (non-blocking here)
                                        "driver": driver_name(store, prov), "network": network_cached(),
                                        "provider_label": PROVIDERS[prov]["label"] if prov in PROVIDERS else prov,
                                        "provider_model": (ollama_default_model(store, refresh=False) if prov == "ollama" else store.setting(prov + ".model", PROVIDERS[prov]["model"])) if prov in PROVIDERS else "",
                                        # images (ADR-0021): which provider would make a picture right now (no network); the ladder SKIPs L2-g on ready=false
                                        "images": image_capability(store),
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
                s.setdefault("agent.driver", "")         # "" = the provider decides (local -> stepwise), or stepwise | freeform (ADR-0020)
                for k, d in VOICE_DEFAULTS.items():
                    s.setdefault(k, d)
                for k, v in PROVIDERS.items():
                    s.setdefault(k + ".model", v["model"])
                    if "base_url" in v:
                        s.setdefault(k + ".base_url", v["base_url"])
                for k, d in IMAGE_SETTINGS.items():      # images (ADR-0021): "" = automatic (the active provider, else the key the user has)
                    s.setdefault(k, d)
                s["images"] = image_capability(store)
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
                s["policy"] = POLICY.status()
                s["mode_effective"] = agent.mode()
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
            if p == "/policy":
                return self._send(200, POLICY.status())
            if p == "/providers/ollama/models":
                # [{name, size, parameter_size, quantization, ...}] from GET http://127.0.0.1:11434/api/tags (ADR-0022); 503 with the reason when Ollama is down
                models, err = ollama_models_cached(store.setting("ollama.base_url", PROVIDERS["ollama"]["base_url"]), force=qs.get("refresh") == "1")
                if models is None:
                    return self._send(503, {"error": err or "Ollama is not reachable", "models": []})
                return self._send(200, models)
            if p == "/providers/ollama/status":
                return self._send(200, ollama_status(store))
            if p == "/system/disk-unlock":
                st = disk_unlock_status()
                st["diagnosis"] = disk_unlock_diagnosis(force=qs.get("refresh") == "1") if st.get("available", True) else None
                try:
                    st["last_request"] = json.loads(store.setting(DISK_UNLOCK_LAST_REQUEST) or "null")
                except ValueError:
                    st["last_request"] = None
                return self._send(200, st)
            if p == "/audit/verify":
                return self._send(200, store.audit_verify())
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
            if p == "/policy/reload":
                POLICY.load()
                store.activity("user", "policy_reload", None, json.dumps(POLICY.status(), default=str)[:1000])
                return self._send(200, POLICY.status())
            if p == "/audit/export":
                since = b.get("since")
                try:
                    since = parse_since(since)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                try:
                    return self._send(200, store.audit_export(since, b.get("out_dir")))
                except (RuntimeError, OSError) as e:
                    return self._send(409, {"error": str(e)})
            if p == "/providers/ollama/install":
                # `fabos ollama install --yes`: the official installer, as root through the polkit path (the user types their password in the
                # system dialog; nothing runs silently). Without confirm=true the command is only shown. Ollama is NOT bundled: ~1 GB download.
                if not b.get("confirm"):
                    return self._send(400, {"ok": False, "error": "confirm=true is required; the command that would run: " + OLLAMA_INSTALL_CMD, "command": OLLAMA_INSTALL_CMD})
                if shutil.which("ollama") and not b.get("force"):
                    return self._send(200, {"ok": True, "already_installed": True, "path": shutil.which("ollama"), "detail": "ollama is already installed; the installer also upgrades — pass force=true to run it again"})
                # A managed computer: the installer is a root download from ollama.com — the same host policy every other outbound
                # endpoint gets (require_host), and with cloud_allowed=false no download from the internet at all.
                refused = None
                if not POLICY.cloud_allowed():
                    refused = "%s: downloading the Ollama installer is not allowed (cloud_allowed is false)" % MANAGED_MSG
                else:
                    try:
                        POLICY.require_host("ollama.com", "the Ollama installer")
                    except RuntimeError as e:
                        refused = str(e)
                if refused:
                    store.activity("user", "ollama_install_refused", None, refused[:300])
                    return self._send(403, {"ok": False, "error": refused, "command": OLLAMA_INSTALL_CMD})
                store.activity("user", "ollama_install_requested", None, OLLAMA_INSTALL_CMD)
                out = agent.tools.run_as_root(None, OLLAMA_INSTALL_CMD, HOME, OLLAMA_INSTALL_TIMEOUT)
                ok = not out.get("error") and out.get("exit_code") == 0
                store.activity("user", "ollama_install_" + ("done" if ok else "failed"), None, (out.get("error") or ("exit %s" % out.get("exit_code")))[:300])
                with _ollama_lock:
                    _ollama_cache.update(at=0.0)
                return self._send(200, {"ok": ok, "exit_code": out.get("exit_code"), "error": out.get("error"), "stdout": (out.get("stdout") or "")[-3000:], "stderr": (out.get("stderr") or "")[-3000:],
                                        "installed": bool(shutil.which("ollama")), "command": OLLAMA_INSTALL_CMD})
            if p == "/system/disk-unlock":
                # The Start-up setting. prompt_at_boot=false needs the current passphrase (one line; it travels on the helper's stdin,
                # see disk_unlock_apply) and stores the unlock key in the initramfs on the unencrypted /boot; true removes it again.
                # CRITICAL: root through the polkit path (the user's password in the system dialog) and audited before and after.
                # Not an agent step, so no approval row — the person clicking the switch is the approver, like the Ollama installer.
                # {action: "repair"|"diagnose"} takes the same root path: repair re-applies the configured direction (the passphrase is
                # needed when the switch is off, since a fresh key slot is added) and comes back with the new diagnosis; diagnose as
                # root reads what the user-level check cannot (the root-only initrds).
                action = b.get("action")
                want = b.get("prompt_at_boot")
                if action is not None:
                    if action not in ("repair", "diagnose"):
                        return self._send(400, {"ok": False, "error": "action must be 'repair' or 'diagnose'", "risk": DISK_UNLOCK_RISK})
                    want = None
                elif not isinstance(want, bool):
                    return self._send(400, {"ok": False, "error": "prompt_at_boot must be true or false", "risk": DISK_UNLOCK_RISK})
                pw = b.get("passphrase")
                if pw is not None and (not isinstance(pw, str) or "\n" in pw or "\r" in pw or "\0" in pw or len(pw) > DISK_UNLOCK_MAX_PASSPHRASE):
                    return self._send(400, {"ok": False, "error": "the passphrase must be one line of at most %d characters" % DISK_UNLOCK_MAX_PASSPHRASE, "risk": DISK_UNLOCK_RISK})
                if want is False and not pw:
                    return self._send(400, {"ok": False, "error": "the current disk passphrase is required to stop asking for it", "risk": DISK_UNLOCK_RISK})
                status = disk_unlock_status()
                if not status.get("encrypted"):
                    return self._send(409, {"ok": False, "risk": DISK_UNLOCK_RISK, "status": status,
                                            "error": status.get("error") or "this computer's disk is not encrypted, so there is no disk password to ask for"})
                if action == "repair" and status.get("prompt_at_boot") is False and not pw:
                    return self._send(400, {"ok": False, "error": "the current disk passphrase is required to fix the start-up files while the switch is off (a fresh unlock key is stored)", "risk": DISK_UNLOCK_RISK})
                if action == "diagnose":
                    pw = None
                store.activity("user", "disk_unlock_requested", None, "risk=%s action=%s prompt_at_boot=%s device=%s via %s" % (DISK_UNLOCK_RISK, action or ("on" if want else "off"), want, status.get("device"), root_argv("x")[0]))
                out = disk_unlock_apply(agent, want, pw, action=action)
                ok = out.get("ok") is True
                store.activity("user", "disk_unlock_" + ("done" if ok else "failed"), None,
                               "risk=%s action=%s prompt_at_boot=%s %s" % (DISK_UNLOCK_RISK, action or ("on" if want else "off"), out.get("prompt_at_boot"), (out.get("error") or out.get("detail") or "")[:300]))
                if action != "diagnose":
                    store.set_setting(DISK_UNLOCK_LAST_REQUEST, json.dumps({"action": action or ("on" if want else "off"), "prompt_at_boot": want, "at": time.time(), "ok": ok,
                                                                          "error": None if ok else (out.get("error") or "")[:300], "exit_code": out.get("exit_code")}))
                if isinstance(out.get("diagnosis"), dict) and out["diagnosis"].get("items") is not None:
                    disk_unlock_diagnosis(fresh=out["diagnosis"])          # the root run's view is the freshest there is
                else:
                    disk_unlock_forget()                                    # the state changed (or may have): the next GET looks again
                return self._send(200, out)
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
                if b.get("mode") and POLICY.clamp_mode(b["mode"]) != b["mode"]:
                    return self._send(403, {"error": "%s: the permission mode is limited to %s (requested %s)" % (MANAGED_MSG, POLICY.mode_max(), b["mode"])})
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
                    if k == "mode" and POLICY.clamp_mode(v) != v:
                        return self._send(403, {"error": "%s: the permission mode is limited to %s" % (MANAGED_MSG, POLICY.mode_max())})
                    if k == "provider" and v not in PROVIDERS and v != "fake":
                        return self._send(400, {"error": "provider must be one of " + ", ".join(PROVIDERS)})
                    if k == "provider" and not POLICY.provider_allowed(v):
                        return self._send(403, {"error": "%s: the %s provider is not allowed (%s)" % (MANAGED_MSG, PROVIDERS.get(v, {}).get("label", v),
                                                                                                    "cloud AI is disabled" if not POLICY.cloud_allowed() else "allowed: " + ", ".join(POLICY.data.get("providers_allowed") or []))})
                    if k in ("policy", "mode_effective"):
                        continue
                    if k == "mail.provider" and str(v).lower() not in MAIL_PROVIDERS and str(v) != "":       # "" = unset: infer it from the address
                        return self._send(400, {"error": "mail.provider must be one of " + ", ".join(MAIL_PROVIDER_ORDER) + " (or empty to infer it from the address)"})
                    if k == "mail.auth" and str(v).lower() not in ("password", "oauth", ""):
                        return self._send(400, {"error": "mail.auth must be password or oauth (or empty)"})
                    if k in ("secrets", "providers", "mail_providers", "mail_provider_order", "mail_oauth", "mail_ready", "images"):
                        continue
                    if k == "images.provider" and str(v).strip().lower() not in IMAGE_PROVIDER_CHOICES:
                        return self._send(400, {"error": "images.provider must be openai, gemini, local or empty (automatic)"})
                    if k == "images.local_endpoint" and str(v).strip() and not str(v).strip().startswith(("http://", "https://")):
                        return self._send(400, {"error": "images.local_endpoint must be an http(s) URL (an OpenAI-compatible /v1 base or its /images/generations)"})
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
    # Audit chain key: a systemd-creds secret (0600 file fallback) generated once; never exposed through the API (not in SECRET_NAMES).
    audit_key = get_secret("audit_key")
    if not audit_key:
        audit_key = _secrets.token_hex(32)
        LOG("audit key generated (%s)" % set_secret("audit_key", audit_key))
    store = Store(DB_PATH, audit_key=audit_key.encode())
    agent = Agent(store)
    Watcher(store, agent).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(store, agent, token))
    srv.daemon_threads = True
    LOG("fabos-agentd listening on 127.0.0.1:%d db=%s mode=%s provider=%s policy=%s" % (PORT, DB_PATH, agent.mode(),
        os.environ.get("FABOS_AGENT_PROVIDER") or store.setting("provider", "claude"), "managed" if POLICY.managed else ("error: " + POLICY.error if POLICY.error else "none")))
    store.activity("system", "service_start", None, "fabos-agentd policy=%s sandbox=%s root=%s" % ("managed" if POLICY.managed else "none", agent.sandbox_name(), root_argv("x")[0]))

    def on_hup(*_a):
        POLICY.load()
        store.activity("system", "policy_reload", None, "SIGHUP: " + json.dumps(POLICY.status(), default=str)[:900])
        LOG("policy reloaded on SIGHUP:", "managed" if POLICY.managed else "none", POLICY.error)
    signal.signal(signal.SIGHUP, on_hup)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
