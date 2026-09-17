#!/usr/bin/env python3
"""Fab AI Controls — chat with the Fab OS agent: ask it to do things, follow up, watch it work live, approve risky steps.

A two-column chat app on top of fabos-agentd's local HTTP API (PyQt6), laid out after docs/design/CHAT-UI-BRIEF.md:
  * sidebar 282 px: "New chat" pill, search, chats grouped Today / Yesterday / Earlier (a chat = a root task + its
    follow-ups), and a bottom group (Clear conversations · Appearance follows system · Settings · About Fab OS)
  * main column: empty state (Fab AI mark, "Try asking" / "What I can do" / "Keep in mind"), then the conversation —
    your requests as pills on the right, the agent's answers as plain text on the left with an action row (copy, good,
    bad, speak, edit, retry), and a LIVE action timeline per turn (one row per tool step: app icon / keyboard / terminal /
    file / globe / mail / bell / question / eye, a spinner while it runs, a check or cross when it finishes, the agent's
    spoken narration in italics underneath; type_text is revealed with a typewriter effect)
  * composer: a two-row rounded card — the text row, then the permission-mode chip, the microphone (fabos-voice
    listen-once) and the filled Send / Stop button
Everything follows the system colour scheme through QPalette; radii/spacing from the Fab OS design tokens; Inter.
Responsiveness: every daemon call of the window runs on ONE worker thread (ApiQueue; Settings > Save and the connection
checks on their own short-lived workers) and its result is applied in place by
a callback on the GUI thread — a slow or hung daemon reply can never freeze the window; polls coalesce (never stack) and
slow down to 6 s / 12 s while the window is hidden or minimised. Tickers run only while shown (docs/LOW-RAM.md).
Launch: fabos-command-center [--ask] [--prefill TEXT] [--settings] [--task ID]   (the executable keeps its historical name)
"""
import datetime, http.client, json, math, os, queue, re, shutil, socket, subprocess, sys, threading, time, urllib.parse
from PyQt6.QtCore import (Qt, QTimer, QSize, QPropertyAnimation, QVariantAnimation, QEasingCurve, QRectF, QEvent, QPointF, QPoint, QProcess, QThread,
                          QObject, pyqtSignal, pyqtProperty, QStandardPaths)
from PyQt6.QtGui import (QFont, QIcon, QImage, QImageReader, QPixmap, QPainter, QColor, QPalette, QPen, QBrush, QTextDocument, QTextCursor, QTextBlockFormat,
                         QTextCharFormat, QTextFormat, QGuiApplication, QAction, QFontMetrics, QPainterPath, QKeyEvent)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
                             QTextBrowser, QPlainTextEdit, QLabel, QComboBox, QTabWidget, QDialog, QFormLayout, QFrame, QScrollArea, QSizePolicy, QToolButton,
                             QCheckBox, QStackedWidget, QMenu, QGraphicsOpacityEffect, QStyle, QFileDialog)

APP_NAME = "Fab AI Controls"
DESKTOP_ID = "fabos-command-center"           # executable / desktop-file / icon id: unchanged so shortcuts and docks keep working
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent")
FOLLOWUP_MARK = "\n\nFollow-up request:\n"    # must match fabos_agentd.FOLLOWUP_MARK
SUPERSEDED = "(superseded) "
ACTIVE = ("queued", "running", "waiting_approval", "waiting_user")
# design tokens (px): radii control 12 · field 14 · card 20 · popup / pills 24 · small cards 8; spacing grid 12/16/20
R_CONTROL, R_FIELD, R_CARD, R_PANEL, R_POPUP, R_SMALL = 12, 14, 20, 20, 24, 8
SP, SP2, SP3 = 12, 16, 20
SIDEBAR_W = 282
POLL_LIST_MS, POLL_THREAD_MS = 4000, 1500          # chat list / open chat, while the window is visible
POLL_LIST_HIDDEN_MS, POLL_THREAD_HIDDEN_MS = 12000, 6000   # while the window is hidden or minimised (nobody is looking)
API_TIMEOUT = 5                  # s — the daemon is local. Every call of the window runs on ApiQueue (one worker thread); the GUI thread never waits on a socket
TYPEWRITER_MS = 25               # ms per character when a typed text is revealed in the action timeline
KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 1     # sidebar list items: their reconcile key ("h:Today" / "c:<root id>")
# provider ids -> short labels (the daemon's PROVIDERS table is the source of truth for the long labels)
PROVIDER_LABELS = {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI", "deepseek": "DeepSeek", "local": "Local model", "ollama": "Ollama", "fake": "Test provider"}
PROVIDER_ORDER = ["claude", "gemini", "openai", "deepseek", "local", "ollama"]
NO_KEY = ("local", "ollama")                 # providers on this computer: endpoint instead of an API key (ADR-0022)
PROVIDER_FULL = {"claude": "Anthropic (Claude)", "gemini": "Google Gemini", "openai": "OpenAI", "deepseek": "DeepSeek", "local": "Local model", "ollama": "Ollama (on this computer)"}
PROVIDER_DEFAULTS = {"claude": ("claude-opus-5", None), "gemini": ("gemini-2.5-pro", "https://generativelanguage.googleapis.com/v1beta/openai"),
                     "openai": ("gpt-4.1", "https://api.openai.com/v1"), "deepseek": ("deepseek-chat", "https://api.deepseek.com/v1"), "local": ("local", "http://127.0.0.1:8080/v1"),
                     "ollama": ("", "http://127.0.0.1:11434/v1")}   # empty model = the first model Ollama has installed (the agent picks it)
PROVIDER_HELP = {"claude": "Paste an API key from your Anthropic account.", "gemini": "Paste an API key from Google AI Studio.", "openai": "Paste an API key from your OpenAI account.",
                 "deepseek": "Paste an API key from the DeepSeek platform.", "local": "Runs on this computer (llama-server or any OpenAI-compatible endpoint). No account, no key needed.",
                 "ollama": "Runs the models you pull with Ollama on this computer — larger or smaller, your choice. No account, no key. Leave Model empty for the first installed model, or type one such as llama3.2 or qwen2.5:7b."}
# semantic status colours (used for tiny risk/status dots and the check-mark animation only; every surface and text comes from the palette)
RISK_COLORS = {"LOW": "#3FCB7E", "MEDIUM": "#6E9BFF", "HIGH": "#E0A64B", "CRITICAL": "#F0655D"}
GREEN, AMBER, RED = "#34C759", "#FF9500", "#F0655D"
STATUS_TEXT = {"queued": "Queued", "running": "Working", "waiting_approval": "Needs your approval", "waiting_user": "Needs your answer",
               "done": "Done", "failed": "Failed", "cancelled": "Stopped"}
VOICE_UNAVAILABLE = "Voice is not available on this machine"
VOICE_TEST_LINE = "Namaste, I am Fab. Tell me what to do."


# ----------------------------------------------------------------------------- daemon API
class AgentOffline(Exception):
    pass


_INFLIGHT = {}     # threading.get_ident() -> the http.client connection that thread is waiting on (ApiQueue.stop() shuts it down)


def api(method, path, body=None, timeout=API_TIMEOUT):
    """Call fabos-agentd. Returns the JSON reply ({"error":..., "http":...} on HTTP errors); raises AgentOffline when unreachable.
    http.client rather than urllib so the connection is known while the call is in flight (_INFLIGHT): a worker that must
    stop (the window is closing) has its socket shut down from the GUI thread and returns at once instead of running out
    the timeout — see ApiQueue.stop()."""
    ident = threading.get_ident()
    try:
        token = open(os.path.join(RUN, "token")).read().strip()
        port = int(open(os.path.join(RUN, "port")).read().strip())
    except (OSError, ValueError) as e:
        raise AgentOffline(str(e))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    _INFLIGHT[ident] = conn
    try:
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
        if r.status >= 400:
            try:
                return {"error": json.loads(raw).get("error", "HTTP Error %d: %s" % (r.status, r.reason)), "http": r.status}
            except Exception:
                return {"error": "HTTP Error %d: %s" % (r.status, r.reason), "http": r.status}
        return json.loads(raw)
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise AgentOffline(str(e) or e.__class__.__name__)
    finally:
        _INFLIGHT.pop(ident, None)
        conn.close()


def _shutdown_inflight(ident):
    """Shuts down the socket of the api() call the thread `ident` is waiting on (if any), from another thread: that call
    returns at once with AgentOffline instead of running out its timeout. Used by ApiQueue.stop() and the one-off workers'
    abort() so a closing window or dialog never waits on a daemon that accepted the connection and is not answering."""
    conn = _INFLIGHT.get(ident) if ident is not None else None
    sock = getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class ApiWorker(QThread):
    """One daemon call off the GUI thread (the provider check may take up to 15 s, the mail check 45 s). done(result) or
    done({"offline": msg}); nothing is emitted after abort(), which also makes run() return within one socket round trip."""
    done = pyqtSignal(object)

    def __init__(self, method, path, body=None, timeout=20, parent=None):
        super().__init__(parent)
        self.args = (method, path, body, timeout)
        self._ident = None
        self._aborted = False

    def abort(self):
        """From the GUI thread (the dialog is closing): the call in flight returns at once and its result is dropped."""
        self._aborted = True
        _shutdown_inflight(self._ident)

    def run(self):
        self._ident = threading.get_ident()
        if self._aborted:
            return
        try:
            r = api(*self.args)
        except AgentOffline as e:
            r = {"offline": str(e), "error": "agent service offline"}
        if not self._aborted:
            self.done.emit(r)


class ApiJobWorker(QThread):
    """Several daemon calls in order, off the GUI thread: done(results) with one entry per call made ({"offline": msg} for
    the call that could not reach the daemon, after which the job stops). With gate_first the later calls run only when the
    first one came back without an error (Settings > Save: PUT /settings, then one POST /secrets per changed key — the
    same sequence that used to run on the GUI thread and froze the dialog for up to API_TIMEOUT per call). abort() as in
    ApiWorker: no further call starts, the one in flight returns at once, nothing is emitted."""
    done = pyqtSignal(object)

    def __init__(self, calls, gate_first=True, parent=None):
        super().__init__(parent)
        self.calls = list(calls)
        self.gate_first = gate_first
        self._ident = None
        self._aborted = False

    def abort(self):
        self._aborted = True
        _shutdown_inflight(self._ident)

    def run(self):
        self._ident = threading.get_ident()
        results = []
        for i, (method, path, body) in enumerate(self.calls):
            if self._aborted:
                return
            try:
                r = api(method, path, body)
            except AgentOffline as e:
                results.append({"offline": str(e), "error": "agent service offline"})
                break
            results.append(r)
            if i == 0 and self.gate_first and (not isinstance(r, dict) or r.get("error")):
                break
        if not self._aborted:
            self.done.emit(results)


class ApiQueue(QThread):
    """The window's single daemon-call worker. A job is a list of calls [(method, path, body), ...] run here one after the
    other, OFF the GUI thread; the results (one per call, or {"offline": msg} for the call that could not reach the daemon,
    after which the job stops) come back through `done`, a queued signal delivered on the GUI thread. A job with a `key`
    replaces a queued job with the same key that has not started yet, so a slow daemon never piles up polls behind each
    other — the newest state is what the window shows. `inflight` (GUI-thread counter) is what flush_api() waits on.
    A call in flight is bounded by API_TIMEOUT; nothing here ever blocks the widgets. stop() ends the worker within one
    socket round trip even against a daemon that accepts the connection and never answers (see there)."""
    done = pyqtSignal(object, object)          # job, results (None when the job was replaced before it ran)

    def __init__(self):
        super().__init__()
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._pending = {}                     # key -> queued job (not started)
        self._stopping = False
        self._ident = None                     # the worker thread's ident once it runs (its in-flight connection is _INFLIGHT[_ident])
        self.inflight = 0
        self.start()

    def submit(self, calls, cb=None, key=None, err=None):
        job = {"calls": list(calls), "cb": cb, "err": err, "key": key, "cancelled": False}
        with self._lock:
            if key is not None:
                old = self._pending.pop(key, None)
                if old is not None:
                    old["cancelled"] = True
                self._pending[key] = job
        self.inflight += 1
        self._q.put(job)
        return job

    def stop(self):
        """Ends the worker: no further call of the current job is started, and the call in flight (if any) has its socket
        shut down so it returns at once. Without this a job of three calls against a daemon that accepts TCP and never
        answers would hold the thread for 3 x API_TIMEOUT, longer than closeEvent() waits — and Qt aborts the process when
        a running QThread is destroyed."""
        self._stopping = True
        self._q.put(None)
        _shutdown_inflight(self._ident)

    def run(self):
        self._ident = threading.get_ident()
        while True:
            job = self._q.get()
            if job is None or self._stopping:
                return
            with self._lock:
                if job["key"] is not None and self._pending.get(job["key"]) is job:
                    del self._pending[job["key"]]
                cancelled = job["cancelled"]
            if cancelled:
                self.done.emit(job, None)
                continue
            results = []
            for method, path, body in job["calls"]:
                if self._stopping:
                    return
                try:
                    results.append(api(method, path, body))
                except AgentOffline as e:
                    results.append({"offline": str(e), "error": "agent service offline"})
                    break
            self.done.emit(job, results)


def user_text(request):
    """What the user typed (a follow-up's request also carries a context prefix the daemon added)."""
    return (request or "").rsplit(FOLLOWUP_MARK, 1)[-1].strip()


def ts_clock(t):
    return time.strftime("%H:%M", time.localtime(t)) if t else ""


def day_group(t):
    d = datetime.date.fromtimestamp(t or 0)
    today = datetime.date.today()
    return "Today" if d == today else ("Yesterday" if d == today - datetime.timedelta(days=1) else "Earlier")


def mem_total_gib():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


# ----------------------------------------------------------------------------- icons: Material-style glyphs from inline SVG
# 24x24 grid, 2px round strokes (Material Symbols "outlined" feel), all drawn for Fab OS. Rendered through Qt's SVG image
# plugin into a QIcon in the current palette colour, so icons follow the colour scheme like everything else.
GLYPHS = {
    "send": '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="{c}" stroke="none"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    "retry": '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
    "delete": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "settings": '<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/><path d="M4 12h2"/><path d="M10 12h10"/><circle cx="8" cy="12" r="2"/><path d="M4 18h10"/><path d="M18 18h2"/><circle cx="16" cy="18" r="2"/>',
    "expand": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "collapse": '<path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/><path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/>',
    "more": '<circle cx="12" cy="5" r="1.8" fill="{c}" stroke="none"/><circle cx="12" cy="12" r="1.8" fill="{c}" stroke="none"/><circle cx="12" cy="19" r="1.8" fill="{c}" stroke="none"/>',
    "add": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M18 15l-6-6-6 6"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "close": '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "flag": '<path d="M4 22V4"/><path d="M4 4h12l-1.5 4L16 12H4"/>',
    "sparkle": '<path d="M12 2l2.2 6.8L21 11l-6.8 2.2L12 20l-2.2-6.8L3 11l6.8-2.2z" fill="{c}" stroke="none"/>',
    "tools": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9l-3.8 3.8z"/>',
    "question": '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "bell": '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    "warning": '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/><path d="M9 21h6"/>',
    "waveform": '<path d="M4 10v4"/><path d="M8 7v10"/><path d="M12 4v16"/><path d="M16 7v10"/><path d="M20 10v4"/>',
    "speaker": '<path d="M11 5L6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    "thumb-up": '<path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3z"/><path d="M7 10l4.5-7a2.5 2.5 0 0 1 2.4 3.1L13 10h6a2 2 0 0 1 2 2.3l-1.4 7A2 2 0 0 1 17.6 21H7"/>',
    "thumb-down": '<path d="M17 14V3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-3z"/><path d="M17 14l-4.5 7a2.5 2.5 0 0 1-2.4-3.1L11 14H5a2 2 0 0 1-2-2.3l1.4-7A2 2 0 0 1 6.4 3H17"/>',
    "chat": '<path d="M21 12a8 8 0 0 1-8 8H8l-5 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/><path d="M8 11h8"/><path d="M8 14h5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.9 4.9l1.4 1.4"/><path d="M17.7 17.7l1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M4.9 19.1l1.4-1.4"/><path d="M17.7 6.3l1.4-1.4"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "keyboard": '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01"/><path d="M10 10h.01"/><path d="M14 10h.01"/><path d="M18 10h.01"/><path d="M8 14h8"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3"/><path d="M12 15h5"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h6"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18"/><path d="M12 3a14 14 0 0 0 0 18"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "eye": '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "apps": '<rect x="4" y="4" width="6" height="6" rx="1.5"/><rect x="14" y="4" width="6" height="6" rx="1.5"/><rect x="4" y="14" width="6" height="6" rx="1.5"/><rect x="14" y="14" width="6" height="6" rx="1.5"/>',
    "bulb": '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 1 4 12.7V17H8v-2.3A7 7 0 0 1 12 2z"/>',
    "bolt": '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
    "minus": '<path d="M5 12h14"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M21 16l-5-5-8 8"/><path d="M3 18l4-4 3 3"/>',
    "save": '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "wallpaper": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8"/><path d="M12 16v4"/><path d="M7 13l3-3 3 3 4-4"/>',
}
_ICON_CACHE = {}


def glyph_pixmap(name, color, size=20, dpr=2.0):
    key = (name, color.name(QColor.NameFormat.HexArgb), size, dpr)
    if key not in _ICON_CACHE:
        px_size = int(size * dpr)
        c = color.name()
        body = GLYPHS[name].replace("{c}", c)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round" stroke-opacity="%.2f" fill-opacity="%.2f">%s</svg>'
               % (px_size, px_size, c, color.alphaF(), color.alphaF(), body)).encode()
        img = QImage()
        if not img.loadFromData(svg, "svg"):     # no SVG plugin: draw a neutral dot so the button still has a target
            img = QImage(px_size, px_size, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(px_size * 0.3, px_size * 0.3, px_size * 0.4, px_size * 0.4))
            p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        _ICON_CACHE[key] = pm
    return _ICON_CACHE[key]


def glyph_icon(name, color, size=20):
    ic = QIcon()
    ic.addPixmap(glyph_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    dim = QColor(color)
    dim.setAlphaF(0.35)
    ic.addPixmap(glyph_pixmap(name, dim, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def dot_pixmap(color, size=10, dpr=2.0):
    """A filled circle (status dot)."""
    key = ("dot", color.name(QColor.NameFormat.HexArgb), size, dpr)
    if key not in _ICON_CACHE:
        px = int(size * dpr)
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(1, 1, px - 2, px - 2))
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        _ICON_CACHE[key] = pm
    return _ICON_CACHE[key]


# ----------------------------------------------------------------------------- palette-derived style
def rgba(c, a):
    return "rgba(%d,%d,%d,%.2f)" % (c.red(), c.green(), c.blue(), a)


def is_dark(pal):
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def build_style(pal):
    """The whole stylesheet is derived from the active QPalette: dark and light schemes both work, nothing is hard-coded."""
    text, win, base, alt, hi, hit = (pal.color(r) for r in (QPalette.ColorRole.Text, QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                                                             QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText))
    dark = is_dark(pal)
    line = rgba(text, 0.12 if dark else 0.12)
    hover = rgba(text, 0.06)
    tint4 = rgba(text, 0.045)
    tint8 = rgba(text, 0.08)
    card = base.name()
    muted = rgba(text, 0.62)
    return """
QWidget { font-family: Inter, 'Noto Sans', sans-serif; font-size: 14px; color: %(text)s; }
QMainWindow, QWidget#root { background: %(win)s; }
QToolTip { background: %(alt)s; color: %(text)s; border: 1px solid %(line)s; border-radius: 8px; padding: 6px 10px; }
QFrame#card { background: transparent; border: none; }
QFrame#sidebar { background: transparent; border: none; border-right: 1px solid %(line)s; }
QFrame#header { background: transparent; border: none; }
QFrame#hairline { background: %(line)s; border: none; max-height: 1px; min-height: 1px; }
QLabel#title { font-size: 16px; font-weight: 600; }
QLabel#subtitle, QLabel#muted, QLabel#groupHeader { color: %(muted)s; font-size: 12px; }
QLabel#groupHeader { font-weight: 500; letter-spacing: 0.3px; padding: 10px 12px 4px 12px; }
QLabel#rowTitle { font-size: 14px; font-weight: 400; }
QLabel#emptyTitle { font-size: 32px; font-weight: 600; }
QLabel#colTitle { font-size: 18px; font-weight: 600; }
QLabel#hint { color: %(muted)s; font-size: 13px; }
QLabel#chipText { color: %(muted)s; font-size: 12.5px; }
QLabel#offline { background: %(warnbg)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 8px 12px; }
QLabel#toast { background: %(alt)s; color: %(text)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 9px 16px; font-size: 13.5px; }
QLabel#emptyCard, QPushButton#emptyCard { background: %(tint4)s; border: none; border-radius: %(rsmall)dpx; padding: 12px 16px; font-size: 14px; text-align: left; color: %(text)s; }
QPushButton#emptyCard:hover { background: %(tint8)s; }
QToolButton#icon { background: transparent; border: none; border-radius: %(rctl)dpx; padding: 0; }
QToolButton#icon:hover { background: %(hover)s; }
QToolButton#icon:pressed { background: %(press)s; }
QToolButton#icon:checked { background: %(hisoft)s; }
QToolButton#iconAccent { background: %(hi)s; border: none; border-radius: 18px; padding: 0; }
QToolButton#iconAccent:hover { background: %(hihover)s; }
QToolButton#iconAccent:disabled { background: %(senddim)s; }
QToolButton#micBtn { background: transparent; border: 1px solid %(line)s; border-radius: 18px; padding: 0; }
QToolButton#micBtn:hover { background: %(hover)s; }
QToolButton#micBtn:disabled { border-color: %(tint4)s; }
QToolButton#chip { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 4px 10px 4px 8px; color: %(muted)s; font-size: 12.5px; }
QToolButton#chip:hover { background: %(hover)s; }
QToolButton#chip::menu-indicator { image: none; }
QToolButton#modeChip { background: transparent; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 5px 10px 5px 8px; color: %(text)s; font-size: 13px; }
QToolButton#modeChip:hover { background: %(hover)s; }
QToolButton#modeChip::menu-indicator { image: none; }
QToolButton#providerChip { background: transparent; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 5px 12px 5px 8px; color: %(text)s; font-size: 13px; }
QToolButton#providerChip:hover { background: %(hover)s; }
QToolButton#sideItem { background: transparent; border: none; border-radius: %(rctl)dpx; padding: 0 12px; color: %(text)s; font-size: 14px; text-align: left; min-height: 48px; max-height: 48px; }
QToolButton#sideItem:hover { background: %(hover)s; }
QPushButton#newChatPill { background: %(hi)s; color: %(hit)s; border: none; border-radius: %(rctl)dpx; padding: 0 16px; font-weight: 600; font-size: 14px; text-align: left; min-height: 36px; max-height: 36px; }
QPushButton#newChatPill:hover { background: %(hihover)s; }
QPushButton#primary { background: %(hi)s; color: %(hit)s; border: none; border-radius: %(rctl)dpx; padding: 9px 18px; font-weight: 600; }
QPushButton#primary:hover { background: %(hihover)s; }
QPushButton#primary:disabled { background: %(hidim)s; color: %(hit)s; }
QPushButton#ghost { background: transparent; color: %(text)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 9px 18px; }
QPushButton#ghost:hover { background: %(hover)s; }
QPushButton#pillFilled { background: %(hi)s; color: %(hit)s; border: none; border-radius: 18px; padding: 8px 20px; font-weight: 600; min-height: 20px; }
QPushButton#pillFilled:hover { background: %(hihover)s; }
QPushButton#pillOutline { background: transparent; color: %(text)s; border: 1px solid %(line)s; border-radius: 18px; padding: 8px 20px; min-height: 20px; }
QPushButton#pillOutline:hover { background: %(hover)s; }
QPushButton#check { background: %(alt)s; color: %(text)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 8px 16px; font-weight: 500; }
QPushButton#check:hover { background: %(hover)s; }
QPushButton#check:disabled { color: %(muted)s; }
QToolButton#disclosure { background: transparent; border: none; border-radius: 8px; padding: 4px 8px 4px 4px; color: %(muted)s; font-size: 12.5px; font-weight: 600; letter-spacing: 0.3px; }
QToolButton#disclosure:hover { background: %(hover)s; color: %(text)s; }
QLineEdit { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rfield)dpx; padding: 8px 12px; selection-background-color: %(hi)s; selection-color: %(hit)s; }
QLineEdit:focus { border-color: %(hi)s; }
QLineEdit#search { padding-left: 34px; border-radius: %(rctl)dpx; background: %(tint4)s; }
QFrame#composer { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rpopup)dpx; }
QFrame#composer[focused="true"] { border-color: %(hi)s; }
QPlainTextEdit#ask { background: transparent; border: none; font-size: 15px; padding: 2px 4px; selection-background-color: %(hi)s; selection-color: %(hit)s; }
QPlainTextEdit#editBox { background: transparent; border: none; font-size: 15px; selection-background-color: %(hi)s; selection-color: %(hit)s; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: %(rctl)dpx; margin: 2px 0; padding: 0; }
QListWidget::item:selected { background: %(tint8)s; }
QListWidget::item:hover:!selected { background: %(hover)s; }
QListWidget::item:disabled { background: transparent; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(scrollh)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QFrame#userPill { background: %(tint8)s; border: none; border-radius: %(rpopup)dpx; }
QFrame#userPill QLabel { font-size: 15px; }
QFrame#editCard { background: %(tint8)s; border: none; border-radius: %(rpopup)dpx; }
QFrame#assistantBubble { background: transparent; border: none; }
QFrame#errorBubble { background: %(errbg)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#infoBubble { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#questionBubble { background: %(hisoft)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QTextBrowser#md { background: transparent; border: none; font-size: 15px; selection-background-color: %(hi)s; selection-color: %(hit)s; }
QFrame#steps { background: transparent; border: none; border-left: 1px solid %(line)s; margin-left: 8px; }
QLabel#stepTitle { font-size: 13.5px; font-weight: 500; }
QLabel#stepTitle[done="true"] { font-weight: 400; }
QLabel#narration { color: %(muted)s; font-size: 12.5px; font-style: italic; }
QLabel#raw { font-family: 'JetBrains Mono', monospace; font-size: 12px; background: %(codebg)s; border-radius: 8px; padding: 6px 8px; color: %(text)s; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 8px 16px; border-radius: %(rctl)dpx; margin-right: 6px; color: %(muted)s; background: transparent; }
QTabBar::tab:selected { background: %(hisoft)s; color: %(text)s; font-weight: 600; }
QComboBox { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 7px 12px; min-height: 22px; }
QComboBox:focus { border-color: %(hi)s; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; selection-background-color: %(hisoft)s; selection-color: %(text)s; padding: 4px; outline: none; }
QMenu { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 8px; }
QMenu::item:selected { background: %(hisoft)s; }
QMenu::indicator { width: 16px; height: 16px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 6px; border: 1.5px solid %(mutedline)s; background: %(alt)s; }
QCheckBox::indicator:checked { background: %(hi)s; border-color: %(hi)s; }
QLabel#riskBadge { border-radius: 9px; padding: 2px 8px; font-size: 11.5px; font-weight: 600; }
QLabel#sectionTitle { font-size: 12px; font-weight: 600; letter-spacing: 0.4px; color: %(muted)s; padding-top: 6px; }
QLabel#checkResult { font-size: 13px; }
QFrame#cloudHint { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; }
QLabel#hintText { color: %(text)s; font-size: 12.5px; }
QPushButton#linkBtn { background: transparent; border: none; color: %(hi)s; font-weight: 600; font-size: 12.5px; padding: 3px 8px; border-radius: 8px; }
QPushButton#linkBtn:hover { background: %(hisoft)s; }
QFrame#imageCard { background: %(alt)s; border: 1px solid %(line)s; border-radius: 16px; }
QFrame#imageCard:hover { border-color: %(hi)s; }
QLabel#imageCaption { font-size: 13px; }
QLabel#imageMeta { color: %(muted)s; font-size: 11.5px; }
QDialog#imageViewer { background: #0A0D14; }
QDialog#imageViewer QLabel { color: #F4F6FA; }
QLabel#viewerMeta { color: rgba(244, 246, 250, 0.55); font-size: 12px; }
QLabel#viewerToast { color: #F4F6FA; font-size: 13px; font-weight: 500; }
QPushButton#viewerBtn { background: rgba(255, 255, 255, 0.10); color: #F4F6FA; border: none; border-radius: 12px; padding: 8px 14px; font-size: 13px; font-weight: 500; }
QPushButton#viewerBtn:hover { background: rgba(255, 255, 255, 0.18); }
QPushButton#viewerBtn:disabled { color: rgba(244, 246, 250, 0.40); background: rgba(255, 255, 255, 0.05); }
QToolButton#viewerIcon { background: transparent; border: none; border-radius: 12px; }
QToolButton#viewerIcon:hover { background: rgba(255, 255, 255, 0.14); }
""" % dict(text=text.name(), win=win.name(), card=card, alt=alt.name(), hi=hi.name(), hit=hit.name(), line=line, hover=hover, muted=muted, tint4=tint4, tint8=tint8,
           press=rgba(text, 0.12), hisoft=rgba(hi, 0.16 if dark else 0.14), hihover=hi.lighter(112).name() if dark else hi.darker(108).name(),
           hidim=rgba(hi, 0.35), senddim=rgba(hi, 0.40), scroll=rgba(text, 0.18), scrollh=rgba(text, 0.30), errbg=rgba(QColor(RED), 0.14), warnbg=rgba(QColor("#E0A64B"), 0.16),
           codebg=rgba(text, 0.08), mutedline=rgba(text, 0.35), rctl=R_CONTROL, rfield=R_FIELD, rcard=R_CARD, rpopup=R_POPUP, rsmall=R_SMALL)


def fade_in(widget, ms=200):
    """Opacity 0 → 1 on a widget (new assistant text streaming in). The effect is removed afterwards so text stays crisp."""
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _done():
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:               # the widget vanished before the fade ended (chat switched): nothing to do
            pass
    anim.finished.connect(_done)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def shake(widget, ms=320, amplitude=8):
    """A horizontal shake (rejected key / failed check): QPropertyAnimation on pos, back to where the layout put it."""
    start = widget.pos()
    anim = QPropertyAnimation(widget, b"pos", widget)
    anim.setDuration(ms)
    anim.setKeyValueAt(0.0, start)
    for i, k in enumerate((0.15, 0.3, 0.45, 0.6, 0.75, 0.9)):
        anim.setKeyValueAt(k, start + QPoint(int(amplitude * (1 if i % 2 == 0 else -1) * (1 - k * 0.6)), 0))
    anim.setKeyValueAt(1.0, start)
    anim.setEasingCurve(QEasingCurve.Type.OutQuad)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._shake_anim = anim
    return anim


def polish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def discard(widget):
    """Remove a widget for good: hidden and out of its layout at once, deleted by Qt on the next event-loop pass. The parent
    keeps ownership — never setParent(None) here: that hands a live C++ subtree to Python's garbage collector, which may
    tear its wrappers down at any moment; a later palette / style-sheet pass then reaches a Python override on a wrapper
    that is already gone, and PyQt aborts the whole app on that exception."""
    widget.hide()
    par = widget.parentWidget()
    if par is not None and par.layout() is not None:
        par.layout().removeWidget(widget)
    widget.deleteLater()


# ----------------------------------------------------------------------------- small widgets
class IconButton(QToolButton):
    """An action as an icon with a tooltip (no words). accent=True gives the round accent button (send / stop / allow).
    dim=0.6 draws the icon at 60 % and 100 % on hover (the message action rows)."""

    def __init__(self, glyph, tooltip, size=32, accent=False, icon_size=20, parent=None, dim=0.85, object_name=None, color=None):
        super().__init__(parent)
        self.glyph, self.accent, self._icon_size, self.dim = glyph, accent, icon_size, dim
        self.fixed_color = QColor(color) if color else None     # a glyph colour that ignores the palette (the image viewer's dark scrim)
        self._hovered = False
        self.setObjectName(object_name or ("iconAccent" if accent else "icon"))
        self.setToolTip(tooltip)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(tooltip)
        self.refresh_icon()

    def set_glyph(self, glyph, tooltip=None):
        self.glyph = glyph
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        self.refresh_icon()

    def refresh_icon(self):
        pal = self.palette()
        col = QColor(self.fixed_color or (pal.color(QPalette.ColorRole.HighlightedText) if self.accent else pal.color(QPalette.ColorRole.Text)))
        if not self.accent:
            col.setAlphaF(1.0 if (self._hovered or self.isChecked()) else self.dim)
        elif not self.isEnabled():
            col.setAlphaF(0.4)                 # the disabled Send: 40 % glyph on the 40 % accent disc (build_style senddim)
        self.setIcon(glyph_icon(self.glyph, col, self._icon_size))

    def enterEvent(self, e):
        self._hovered = True
        self.refresh_icon()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.refresh_icon()
        super().leaveEvent(e)

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.EnabledChange):
            self.refresh_icon()
        super().changeEvent(e)


class MicButton(IconButton):
    """The composer microphone: an outlined circle; while listening an accent ring pulses outwards."""

    def __init__(self, parent=None):
        super().__init__("mic", "Speak your request", size=36, icon_size=18, parent=parent, object_name="micBtn")
        self._pulse = 0.0
        self.listening = False
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(1100)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._pulse = float(v)
        self.update()

    def set_listening(self, on):
        self.listening = on
        if on:
            self.anim.start()
        else:
            self.anim.stop()
            self._pulse = 0.0
        self.set_glyph("waveform" if on else "mic", "Listening… click to stop" if on else "Speak your request")
        self.update()

    def paintEvent(self, e):
        if self.listening:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            hi = QColor(self.palette().color(QPalette.ColorRole.Highlight))
            r = self.rect().center()
            for phase in (0.0, 0.5):
                k = (self._pulse + phase) % 1.0
                c = QColor(hi)
                c.setAlphaF(0.55 * (1 - k))
                pen = QPen(c)
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                rad = 14 + 6 * k
                p.drawEllipse(QPointF(r.x() + 0.5, r.y() + 0.5), rad, rad)
            fill = QColor(hi)
            fill.setAlphaF(0.16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawEllipse(QPointF(r.x() + 0.5, r.y() + 0.5), 17.5, 17.5)
            p.end()
        super().paintEvent(e)


class Switch(QCheckBox):
    """A real QCheckBox drawn as a Material switch (track + sliding knob), colours from the palette."""

    def __init__(self, tooltip="", parent=None):
        super().__init__(parent)
        self._pos = 1.0 if self.isChecked() else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(46, 26)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(160)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v):
        self._pos = float(v)
        self.update()

    def _animate(self, on):
        self.anim.stop()
        self.anim.setStartValue(self._pos)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def setChecked(self, on):          # programmatic changes jump without animating
        super().setChecked(on)
        self.anim.stop()
        self._set_pos(1.0 if on else 0.0)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        on = pal.color(QPalette.ColorRole.Highlight)
        off = QColor(pal.color(QPalette.ColorRole.Text))
        off.setAlphaF(0.28)
        track = QColor(int(off.red() * (1 - self._pos) + on.red() * self._pos), int(off.green() * (1 - self._pos) + on.green() * self._pos),
                       int(off.blue() * (1 - self._pos) + on.blue() * self._pos), int(off.alpha() * (1 - self._pos) + 255 * self._pos))
        if not self.isEnabled():
            track.setAlphaF(track.alphaF() * 0.5)
        r = QRectF(1, 3, 44, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, 10, 10)
        knob_x = 3 + self._pos * (46 - 3 - 3 - 16)
        knob = pal.color(QPalette.ColorRole.HighlightedText) if self._pos > 0.5 else pal.color(QPalette.ColorRole.Base)
        p.setBrush(knob)
        p.drawEllipse(QRectF(knob_x, 5, 16, 16))
        if self.hasFocus():
            pen = QPen(on)
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.75, 0.75, 44.5, 24.5), 12.5, 12.5)
        p.end()


class TypingIndicator(QWidget):
    """Three pulsing dots while the agent works."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.setFixedSize(48, 24)
        self.timer = QTimer(self)
        self.timer.setInterval(60)             # ~17 repaints/s of a 48x24 widget is plenty for three dots (was 25/s); runs only while shown
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        self.phase += 0.24                     # same angular speed as before at the longer tick
        self.update()

    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = self.palette().color(QPalette.ColorRole.Text)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            k = (math.sin(self.phase - i * 0.9) + 1) / 2          # 0..1
            c = QColor(base)
            c.setAlphaF(0.25 + 0.65 * k)
            p.setBrush(c)
            r = 3.2 + 1.3 * k
            p.drawEllipse(QPointF(10 + i * 14, 12 - 2.5 * k), r, r)
        p.end()


class ResultMark(QWidget):
    """Animated status mark: 'busy' = a spinning arc; 'ok' = a circle that draws itself and then a check mark (green);
    'fail' = the same circle then a cross (amber / red). The drawing progress is a Qt property animated over ~500 ms."""

    def __init__(self, size=28, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.state = "idle"
        self._progress = 0.0
        self._spin = 0.0
        self.color = QColor(GREEN)
        self.anim = QPropertyAnimation(self, b"progress", self)
        self.anim.setDuration(320)             # motion token "slow": the check draws itself in 320 ms
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.spinner = QTimer(self)
        self.spinner.setInterval(50)           # 20 repaints/s for a 28 px arc (was 33/s); only while busy
        self.spinner.timeout.connect(self._tick)

    def _get_progress(self):
        return self._progress

    def _set_progress(self, v):
        self._progress = float(v)
        self.update()

    progress = pyqtProperty(float, fget=_get_progress, fset=_set_progress)

    def _tick(self):
        self._spin = (self._spin + 0.15) % (2 * math.pi)     # same angular speed at the longer tick
        self.update()

    def set_state(self, state, animate=True, color=None):
        """state: idle | busy | ok | fail | denied"""
        if state == self.state and state in ("busy", "idle"):
            return
        self.state = state
        self.color = QColor(color or {"ok": GREEN, "fail": AMBER, "denied": RED}.get(state, GREEN))
        self.anim.stop()
        if state == "busy":
            self.spinner.start()
            self._progress = 0.0
        else:
            self.spinner.stop()
            if state in ("ok", "fail", "denied"):
                if animate:
                    self.anim.setStartValue(0.0)
                    self.anim.setEndValue(1.0)
                    self.anim.start()
                else:
                    self._progress = 1.0
            else:
                self._progress = 0.0
        self.setToolTip({"ok": "Done", "fail": "Did not work", "denied": "Not allowed", "busy": "Working…"}.get(state, ""))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height())
        pad = 3
        rect = QRectF(pad, pad, s - 2 * pad, s - 2 * pad)
        pen = QPen()
        pen.setWidthF(max(1.8, s / 13.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if self.state == "busy":
            hi = QColor(self.palette().color(QPalette.ColorRole.Highlight))
            track = QColor(hi)
            track.setAlphaF(0.18)
            pen.setColor(track)
            p.setPen(pen)
            p.drawEllipse(rect)
            pen.setColor(hi)
            p.setPen(pen)
            p.drawArc(rect, int(-math.degrees(self._spin) * 16), -110 * 16)
        elif self.state in ("ok", "fail", "denied"):
            k = self._progress
            pen.setColor(self.color)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            arc = min(1.0, k / 0.62)                    # the circle draws during the first 62 % of the animation
            p.drawArc(rect, 90 * 16, int(-360 * 16 * arc))
            mark = max(0.0, (k - 0.55) / 0.45)          # the check / cross follows, overlapping slightly
            if mark > 0:
                if self.state == "ok":
                    pts = [QPointF(rect.left() + rect.width() * 0.28, rect.top() + rect.height() * 0.53),
                           QPointF(rect.left() + rect.width() * 0.44, rect.top() + rect.height() * 0.69),
                           QPointF(rect.left() + rect.width() * 0.73, rect.top() + rect.height() * 0.36)]
                    self._draw_polyline(p, pts, mark)
                else:
                    c = rect.center()
                    d = rect.width() * 0.19
                    self._draw_polyline(p, [QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d)], min(1.0, mark * 2))
                    if mark > 0.5:
                        self._draw_polyline(p, [QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d)], (mark - 0.5) * 2)
        p.end()

    @staticmethod
    def _draw_polyline(p, pts, frac):
        total = sum(math.hypot(pts[i + 1].x() - pts[i].x(), pts[i + 1].y() - pts[i].y()) for i in range(len(pts) - 1))
        remaining = total * max(0.0, min(1.0, frac))
        path = QPainterPath(pts[0])
        for i in range(len(pts) - 1):
            seg = math.hypot(pts[i + 1].x() - pts[i].x(), pts[i + 1].y() - pts[i].y())
            if remaining >= seg:
                path.lineTo(pts[i + 1])
                remaining -= seg
            else:
                t = remaining / seg if seg else 0
                path.lineTo(QPointF(pts[i].x() + (pts[i + 1].x() - pts[i].x()) * t, pts[i].y() + (pts[i + 1].y() - pts[i].y()) * t))
                break
        p.drawPath(path)


class Toast(QLabel):
    """A short message that fades in above the composer and fades out by itself ("I did not catch that.")."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self.eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.eff)
        self.eff.setOpacity(0.0)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade_out)
        self.anim = None

    def show_message(self, text, ms=2600):
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        if par:
            self.move((par.width() - self.width()) // 2, par.height() - self.height() - 96)
        self.show()
        self.raise_()
        self._animate(1.0)
        self.timer.start(ms)

    def _fade_out(self):
        self._animate(0.0)

    def _animate(self, to):
        if self.anim:
            self.anim.stop()
        self.anim = QPropertyAnimation(self.eff, b"opacity", self)
        self.anim.setDuration(200)
        self.anim.setStartValue(self.eff.opacity())
        self.anim.setEndValue(to)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if to == 0.0:
            self.anim.finished.connect(self.hide)
        self.anim.start()


class RoundedDialog(QDialog):
    """Frameless, palette-following dialog with a radius-20 surface: title, message/body, Cancel + accent Confirm."""

    def __init__(self, parent, title, message="", confirm_text="Confirm", cancel_text="Cancel", radius=R_PANEL, width=420):
        super().__init__(parent)
        self.radius = radius
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 20)
        self.lay.setSpacing(SP)
        self.title = QLabel(title)
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.lay.addWidget(self.title)
        self.message = QLabel(message)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setVisible(bool(message))
        self.lay.addWidget(self.message)
        self.body = QVBoxLayout()
        self.body.setSpacing(SP)
        self.lay.addLayout(self.body)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(10)
        self.buttons.addStretch(1)
        self.cancel_btn = QPushButton(cancel_text)
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        self.confirm_btn = QPushButton(confirm_text)
        self.confirm_btn.setObjectName("primary")
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self.accept)
        self.buttons.addWidget(self.cancel_btn)
        self.buttons.addWidget(self.confirm_btn)
        self.lay.addLayout(self.buttons)

    def showEvent(self, e):
        super().showEvent(e)
        par = self.parentWidget()
        if par:
            self.adjustSize()
            g = par.window().frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def refit(self):
        """Shrink or grow to the content NOW: activate the layout first so the minimum size it imposes is fresh (a bare
        adjustSize() right after hiding a block is clamped by the stale minimum and the dialog stays tall). Then honour
        the layout's height-for-width: word-wrapped labels need more rows at the dialog's real width than sizeHint()
        guesses, and adjustSize() caps a window at 2/3 of the screen — so the content, not the cap, decides the height
        (bounded by the screen), or wrapped text gets clipped."""
        lay = self.layout()
        if lay is not None:
            lay.invalidate()          # activate() is a no-op on a layout that still counts as activated
            lay.activate()
        self.adjustSize()
        if lay is not None and lay.hasHeightForWidth():
            need = lay.totalHeightForWidth(self.width())
            scr = self.screen()
            cap = (scr.availableGeometry().height() - 48) if scr is not None else need
            if need > self.height():
                self.resize(self.width(), max(self.height(), min(need, cap)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        border = QColor(pal.color(QPalette.ColorRole.Text))
        border.setAlphaF(0.14)
        pen = QPen(border)
        pen.setWidthF(1)
        p.setPen(pen)
        p.setBrush(pal.color(QPalette.ColorRole.Window))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
        p.end()

    @staticmethod
    def confirm(parent, title, message, confirm_text="Confirm"):
        d = RoundedDialog(parent, title, message, confirm_text)
        return d.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def info(parent, title, message):
        d = RoundedDialog(parent, title, message, "OK")
        d.cancel_btn.hide()
        d.exec()


class ApprovalDialog(RoundedDialog):
    """The agent wants to do something risky: friendly description + risk level, Allow (accent) / Deny. Non-modal.
    The exact tool input sits behind a "Show details" toggle like every other raw tool input (ui.show_raw opens it), but
    it is open by default for HIGH / CRITICAL risk: nobody should approve a dangerous command without reading it.
    A decision is only ever sent from the two buttons (Escape counts as Deny); a programmatic close() — the approval was
    resolved from the notification or the CLI — posts nothing."""
    decided = pyqtSignal(int, str)

    def __init__(self, parent, approval, show_raw=False):
        super().__init__(parent, "The agent asks for your approval", "", "Allow", "Deny")
        self.approval = approval
        self.setModal(False)
        a = approval
        risk = a.get("risk") or "MEDIUM"
        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel(risk.capitalize() + " risk")
        badge.setObjectName("riskBadge")
        col = QColor(RISK_COLORS.get(risk, "#9AA4B2"))
        badge.setStyleSheet("background:%s; color:%s;" % (rgba(col, 0.22), col.darker(140).name() if not is_dark(self.palette()) else col.name()))
        head.addWidget(badge)
        task = QLabel("in chat “%s”" % ((a.get("title") or "")[:60]))
        task.setObjectName("muted")
        head.addWidget(task, 1)
        self.body.addLayout(head)
        desc = QLabel("The agent wants to: " + friendly_label({"name": a.get("tool"), "input": a.get("input"), "decision": ""}, pending=True)
                      + ("\nWhy it needs approval: " + (a.get("reason") or "") if a.get("reason") else ""))
        desc.setWordWrap(True)
        self.body.addWidget(desc)
        self.details_btn = QToolButton()
        self.details_btn.setObjectName("chip")
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_btn.setCheckable(True)
        self.details_btn.setIconSize(QSize(15, 15))
        self.details_btn.toggled.connect(self._toggle_details)
        drow = QHBoxLayout()
        drow.setContentsMargins(0, 0, 0, 0)
        drow.addWidget(self.details_btn, 0)
        drow.addStretch(1)
        self.body.addLayout(drow)
        self.raw = QLabel(step_input_summary(a.get("tool"), a.get("input"))[:600])
        self.raw.setObjectName("raw")
        self.raw.setWordWrap(True)
        self.raw.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(self.raw)
        self.details_btn.setChecked(bool(show_raw) or risk in ("HIGH", "CRITICAL"))
        self._toggle_details(self.details_btn.isChecked())
        self._decision = None
        self.confirm_btn.clicked.connect(lambda: self._choose("approved"))
        self.cancel_btn.clicked.connect(lambda: self._choose("denied"))

    def _toggle_details(self, on):
        self.raw.setVisible(on)
        self.details_btn.setText("Hide details" if on else "Show details")
        self.details_btn.setToolTip("Hide what exactly will run" if on else "Show what exactly will run")
        col = QColor(self.palette().color(QPalette.ColorRole.Text))
        col.setAlphaF(0.7)
        self.details_btn.setIcon(glyph_icon("chevron-up" if on else "chevron-down", col, 15))
        self.adjustSize()

    def _choose(self, decision):
        if self._decision is None:
            self._decision = decision
            self.decided.emit(self.approval["id"], decision)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:          # Escape = Deny, explicitly (QDialog would reject() silently)
            self.cancel_btn.click()
            return
        super().keyPressEvent(e)


# ----------------------------------------------------------------------------- step helpers (friendly, never the raw command unless asked)
FRIENDLY = {"run_shell": "Ran a command", "read_file": "Read a file", "write_file": "Wrote a file", "list_dir": "Looked inside a folder",
            "open_app": "Opened an app", "type_text": "Typed into an app", "send_email": "Sent an email", "check_email": "Checked the inbox",
            "schedule_watch": "Set up a background watch", "web_fetch": "Read a web page", "notify_user": "Sent you a notification",
            "ask_user": "Asked you a question", "list_apps": "Looked up installed apps", "context": "Tidied earlier notes to fit the model's memory",
            "generate_image": "Created an image"}
PENDING = {"run_shell": "run a command", "read_file": "read a file", "write_file": "write a file", "list_dir": "look inside a folder", "open_app": "open an app",
           "type_text": "type into an app", "send_email": "send an email", "check_email": "check the inbox", "schedule_watch": "set up a background watch",
           "web_fetch": "read a web page", "notify_user": "send you a notification", "ask_user": "ask you a question", "list_apps": "look up installed apps",
           "generate_image": "create an image"}
LIVE = {"run_shell": "Running a command", "read_file": "Reading a file", "write_file": "Writing a file", "list_dir": "Looking inside a folder", "open_app": "Opening an app",
        "type_text": "Typing", "send_email": "Sending an email", "check_email": "Checking the inbox", "schedule_watch": "Setting up a background watch",
        "web_fetch": "Reading a web page", "notify_user": "Sending you a notification", "ask_user": "Asking you a question", "list_apps": "Looking up installed apps",
        "context": "Tidying earlier notes", "generate_image": "Creating an image"}
STEP_GLYPH = {"run_shell": "terminal", "type_text": "keyboard", "write_file": "file", "read_file": "file", "list_dir": "folder", "web_fetch": "globe", "send_email": "mail",
              "check_email": "mail", "notify_user": "bell", "ask_user": "question", "schedule_watch": "eye", "list_apps": "apps", "open_app": "apps", "context": "tools",
              "generate_image": "image"}
# generated images: the generate_image tool answers {"path": "/home/<user>/Pictures/Fab OS/<name>.png", "width", "height", "provider", "prompt"};
# the final text may mention such a path too (~ / $HOME / /home/<user> / file:// forms). A card is shown once per file that exists.
IMAGE_PATH_RE = re.compile(r'(?:file://)?((?:~|\$HOME|/home/[^/\s"\'`<>]+)/Pictures/Fab(?:%20| )OS/[^\n"\'`<>*|]*?\.(?:png|jpe?g))(?!\w)', re.I)
REGENERATE_REQUEST = "regenerate the image with the same prompt"


def expand_home(p):
    p = str(p or "").replace("%20", " ")
    home = os.path.expanduser("~")
    for prefix in ("~/", "$HOME/"):
        if p.startswith(prefix):
            return os.path.join(home, p[len(prefix):])
    return p


def image_paths_in_text(text):
    out, seen = [], set()
    for m in IMAGE_PATH_RE.finditer(str(text or "")):
        p = m.group(1).replace("%20", " ")
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def image_from_step(step):
    """A finished generate_image step -> {path, prompt, provider, width, height}; None for anything else."""
    if (step.get("name") or "") != "generate_image" or step.get("kind", "tool_call") != "tool_call" or step_state(step) != "ok":
        return None
    try:
        out = json.loads(step.get("output") or "{}")
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    path = str(out.get("path") or "")
    if not re.search(r"\.(png|jpe?g)$", path, re.I):
        return None
    inp = parse_input(step.get("input"))
    try:
        w, h = int(out.get("width") or 0), int(out.get("height") or 0)
    except (TypeError, ValueError):
        w, h = 0, 0
    return {"path": path, "prompt": str(out.get("prompt") or inp.get("prompt") or ""), "provider": str(out.get("provider") or ""), "width": w, "height": h}


def task_images(task):
    """Every generated image of a task that exists on disk, in step order, one entry per file (absolute path)."""
    infos, seen = [], set()
    for s in task.get("steps") or []:
        if s.get("kind") == "tool_call":
            info = image_from_step(s)
            if info:
                infos.append(info)
        elif s.get("kind") == "final":
            for p in image_paths_in_text(s.get("output") or ""):
                infos.append({"path": p, "prompt": user_text(task.get("request")), "provider": "", "width": 0, "height": 0})
    out = []
    for info in infos:
        p = os.path.abspath(expand_home(info["path"]))
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        out.append(dict(info, path=p))
    return out


def load_image(path, cap=2048):
    """Decode an image at most `cap` px on its long side (a thumbnail or a viewer never keeps a huge bitmap)."""
    rd = QImageReader(path)
    rd.setAutoTransform(True)
    size = rd.size()
    if size.isValid() and max(size.width(), size.height()) > cap:
        rd.setScaledSize(size.scaled(cap, cap, Qt.AspectRatioMode.KeepAspectRatio))
    return rd.read()


def rounded_pixmap(pix, radius):
    out = QPixmap(pix.size())
    out.setDevicePixelRatio(pix.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, pix.width() / pix.devicePixelRatio(), pix.height() / pix.devicePixelRatio()), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


def pictures_dir():
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return d or os.path.join(os.path.expanduser("~"), "Pictures")
APP_NAMES = {"kate": "Fab Editor", "dolphin": "Fab Files", "konsole": "Fab Terminal", "xdg-open": "the default app", "open": "the default app", "firefox": "Firefox",
             "firefox-esr": "Firefox", "libreoffice": "LibreOffice", "vlc": "VLC", "plasma-discover": "Fab Software", "gwenview": "Fab Photos", "okular": "Fab Documents",
             "kcalc": "Fab Calculator", "spectacle": "Fab Screenshot", "systemsettings": "Fab Settings"}


def parse_input(raw):
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except Exception:
        return {"raw": raw}


def app_display(inp):
    app = str(inp.get("app") or "").split("/")[-1]
    return APP_NAMES.get(app, app or "an app")


def friendly_label(step, pending=False, live=False):
    """Plain-language description of a tool step. pending=True phrases it as an intention (approval dialogs); live=True as
    what is happening right now (the action timeline while the step runs)."""
    name = step.get("name") or ""
    inp = parse_input(step.get("input"))
    if pending or live:
        table = LIVE if live else PENDING
        label = table.get(name, (name or "step").replace("_", " "))
        if name == "run_shell" and inp.get("as_root"):
            label = "Running a command as administrator" if live else "run a command as administrator"
        elif name == "write_file" and inp.get("append"):
            label = "Adding to a file" if live else "add to a file"
        if name == "open_app" and inp.get("app"):
            label = ("Opening %s" if live else "open %s") % app_display(inp)
        elif name in ("write_file", "read_file", "list_dir") and inp.get("path"):
            label += " (%s)" % os.path.basename(str(inp["path"]).rstrip("/"))
        elif name == "send_email" and inp.get("to"):
            label = ("Sending an email to %s" if live else "send an email to %s") % inp["to"]
        elif name == "web_fetch" and inp.get("url"):
            host = urllib.parse.urlparse(str(inp["url"])).netloc
            if host:
                label = ("Reading a page on %s" if live else "read a page on %s") % host
        return label
    label = FRIENDLY.get(name, (name or "step").replace("_", " ").capitalize())
    if name == "run_shell" and inp.get("as_root"):
        label = "Ran a command as administrator"
    elif name == "write_file" and inp.get("append"):
        label = "Added to a file"
    if name == "open_app" and inp.get("app"):
        label = "Opened %s" % app_display(inp)
    elif name in ("write_file", "read_file", "list_dir") and inp.get("path"):
        label += " (%s)" % os.path.basename(str(inp["path"]).rstrip("/"))
    elif name == "send_email" and inp.get("to"):
        label = "Sent an email to %s" % inp["to"]
    elif name == "web_fetch" and inp.get("url"):
        host = urllib.parse.urlparse(str(inp["url"])).netloc
        if host:
            label = "Read a page on %s" % host
    d = step.get("decision") or ""
    if d == "denied":
        label += " — denied"
    elif d == "expired":
        label += " — approval expired"
    elif step_failed(step):
        label += " — failed"
    return label


def step_failed(step):
    out = step.get("output") or ""
    return out[:12].lstrip().startswith('{"error"')


def step_state(step):
    """busy | ok | fail | denied for a tool step from its recorded output / decision."""
    d = step.get("decision") or ""
    if d in ("denied", "expired"):
        return "denied"
    if not (step.get("output") or "").strip():
        return "busy"
    return "fail" if step_failed(step) else "ok"


def step_input_summary(name, raw):
    inp = parse_input(raw)
    for k in ("command", "path", "to", "url", "app", "question", "message", "text", "query", "kind"):
        if inp.get(k):
            v = inp[k]
            if k == "app" and inp.get("args"):
                v = "%s %s" % (v, " ".join(str(x) for x in inp["args"]))
            return "%s: %s" % (k, v)
    return json.dumps(inp)[:600]


def step_output_summary(raw):
    if not raw:
        return ""
    try:
        out = json.loads(raw)
    except Exception:
        return str(raw)
    if isinstance(out, dict):
        for k in ("stdout", "error", "content", "result", "answer", "message"):
            if out.get(k):
                s = str(out[k])
                if k == "stdout" and out.get("stderr"):
                    s += "\n" + str(out["stderr"])
                if k == "stdout" and out.get("exit_code") not in (None, 0):
                    s += "\n[exit %s]" % out["exit_code"]
                return s
    return json.dumps(out)


def step_icon(step, color, size=20):
    """Per-tool icon: the launched app's own theme icon for open_app (falls back to the apps glyph), Material-style glyphs otherwise."""
    name = step.get("name") or ""
    if name == "open_app":
        inp = parse_input(step.get("input"))
        app = str(inp.get("app") or "").split("/")[-1]
        if app and app not in ("xdg-open", "open") and QIcon.hasThemeIcon(app):
            return QIcon.fromTheme(app).pixmap(size, size)
    return glyph_pixmap(STEP_GLYPH.get(name, "tools"), color, size)


# ----------------------------------------------------------------------------- chat pieces
class MarkdownView(QTextBrowser):
    """Markdown rendered by Qt, height follows the content through heightForWidth (like a word-wrapping QLabel, so the
    layout owns the geometry and nothing can oscillate), code blocks monospace on a tinted background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("md")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.document().setDocumentMargin(0)
        self._md = ""
        self._ideal = 200           # natural single-line width of the text: short answers hug their text
        self.max_w = 640            # widest the text may grow (set by the conversation view from its width)

    def set_markdown(self, text):
        if text == self._md:
            return
        self._md = text
        doc = self.document()
        doc.setMarkdown(text or "", QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        self._style_code(doc)
        doc.setTextWidth(-1)
        self._ideal = int(math.ceil(doc.idealWidth())) + 6
        self.updateGeometry()

    def set_max_width(self, w):
        if w != self.max_w:
            self.max_w = max(80, w)
            self.updateGeometry()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        doc = self.document()
        w = max(40, int(w))
        if int(doc.textWidth()) != w:
            doc.setTextWidth(w)
        return int(math.ceil(doc.size().height())) + 2

    def minimumSizeHint(self):
        return QSize(60, 18)

    def sizeHint(self):
        w = max(60, min(self._ideal, self.max_w))
        return QSize(w, self.heightForWidth(w))

    def markdown(self):
        return self._md

    def _style_code(self, doc):
        doc = self.document()                  # always the live document (a cached handle may be stale after a re-parent)
        pal = self.palette()
        tint = QColor(pal.color(QPalette.ColorRole.Text))
        tint.setAlphaF(0.08)
        mono = QFont("JetBrains Mono")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(max(8.0, self.font().pointSizeF() - 0.5 if self.font().pointSizeF() > 0 else 9.5))
        block = doc.begin()
        while block.isValid():
            bf = block.blockFormat()
            if bf.nonBreakableLines() or bf.hasProperty(QTextFormat.Property.BlockCodeLanguage):
                cur = QTextCursor(block)
                nbf = QTextBlockFormat(bf)
                nbf.setBackground(QBrush(tint))
                nbf.setLeftMargin(10)
                nbf.setRightMargin(10)
                prev, nxt = block.previous(), block.next()
                if not (prev.isValid() and prev.blockFormat().nonBreakableLines()):
                    nbf.setTopMargin(6)
                if not (nxt.isValid() and nxt.blockFormat().nonBreakableLines()):
                    nbf.setBottomMargin(6)
                cur.setBlockFormat(nbf)
                cur.select(QTextCursor.SelectionType.BlockUnderCursor)
                cf = QTextCharFormat()
                cf.setFont(mono)
                cur.mergeCharFormat(cf)
            else:
                for fr in block.textFormats():
                    f = fr.format
                    if f.fontFixedPitch() or any("mono" in fam.lower() for fam in (f.fontFamilies() or [])):
                        cur = QTextCursor(doc)
                        cur.setPosition(block.position() + fr.start)
                        cur.setPosition(block.position() + fr.start + fr.length, QTextCursor.MoveMode.KeepAnchor)
                        cf = QTextCharFormat()
                        cf.setFont(mono)
                        cf.setBackground(QBrush(tint))
                        cur.mergeCharFormat(cf)
            block = block.next()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = max(40, self.viewport().width())
        if int(self.document().textWidth()) != w:
            self.document().setTextWidth(w)

    def wheelEvent(self, e):
        e.ignore()          # let the conversation scroll

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange) and self._md:
            md, self._md = self._md, None
            try:
                self.set_markdown(md)          # re-tint code blocks for the new scheme
            except RuntimeError:               # the document is already gone (widget being torn down): nothing to restyle;
                self._md = md                  # an exception must never escape a Qt event handler (PyQt aborts the app)
        try:
            super().changeEvent(e)
        except RuntimeError:                   # same guard for the base call (see discard())
            pass


class GrowingTextEdit(QPlainTextEdit):
    """The composer's text box: grows from one to six lines; Enter sends, Shift+Enter inserts a newline, Escape cancels."""
    submitted = pyqtSignal()
    escaped = pyqtSignal()
    focus_changed = pyqtSignal(bool)

    def __init__(self, object_name="ask", parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.document().setDocumentMargin(2)
        self.viewport().setAutoFillBackground(False)
        self.textChanged.connect(self._fit)
        self.max_lines = 6
        self._fit()

    def _fit(self):
        fm = QFontMetrics(self.font())
        line_h = fm.lineSpacing()
        lines = max(1, min(self.max_lines, int(math.ceil(self.document().size().height()))))
        h = int(lines * line_h) + 10
        if self.maximumHeight() != h:
            self.setFixedHeight(h)

    def text(self):
        return self.toPlainText()

    def setText(self, t):
        self.setPlainText(t)
        self.setCursorPosition(len(t))

    def setCursorPosition(self, n):
        c = self.textCursor()
        c.setPosition(min(n, len(self.toPlainText())))
        self.setTextCursor(c)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
            else:
                self.submitted.emit()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focus_changed.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focus_changed.emit(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


class Message(QWidget):
    """One message. role 'user' = a right-aligned pill (radius 24, 8 % tint) with edit / retry on hover;
    'assistant' = plain text on the left (no bubble) with the action row: copy · good · bad · speak · edit · retry;
    error / info / question = a soft tinted card."""
    edit_requested = pyqtSignal()
    retry_requested = pyqtSignal()
    speak_requested = pyqtSignal(str)
    feedback = pyqtSignal(str)

    def __init__(self, role, parent=None):
        super().__init__(parent)
        self.role = role
        self.rating = None
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName({"user": "userPill", "assistant": "assistantBubble", "error": "errorBubble", "info": "infoBubble", "question": "questionBubble"}[role])
        fl = QHBoxLayout(self.frame)
        fl.setContentsMargins(*((SP3, 12, SP3, 12) if role == "user" else ((0, 4, 0, 4) if role == "assistant" else (SP2, 10, SP2, 10))))
        fl.setSpacing(10)
        self.avatar = None
        if role != "user":
            self.avatar = QLabel()
            self.avatar.setFixedSize(22, 22)
            self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fl.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        if role == "user":
            self.label = QLabel()
            self.label.setWordWrap(True)
            self.label.setTextFormat(Qt.TextFormat.PlainText)
            self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            fl.addWidget(self.label)
            self.md = None
        else:
            self.md = MarkdownView()
            fl.addWidget(self.md, 1)
            self.label = None
        if role == "user":
            row.addStretch(1)
            row.addWidget(self.frame, 0)
        else:
            row.addWidget(self.frame, 1)
        outer.addLayout(row)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(2)
        self.actions.setContentsMargins(30 if role != "user" else 0, 0, 4, 0)
        self.action_buttons = []
        self.buttons = {}
        if role == "user":
            self.actions.addStretch(1)
            self._add_action("edit", "Edit and resend", self.edit_requested.emit)
            self._add_action("retry", "Retry this request", self.retry_requested.emit)
        elif role == "assistant":
            self._add_action("copy", "Copy text", self.copy_text)
            self._add_action("thumb-up", "Good answer", lambda: self.rate("good"))
            self._add_action("thumb-down", "Not a good answer", lambda: self.rate("bad"))
            self._add_action("speaker", "Read this aloud", lambda: self.speak_requested.emit(self.text()))
            self._add_action("edit", "Edit the request that led to this", self.edit_requested.emit)
            self._add_action("retry", "Try again", self.retry_requested.emit)
            self.actions.addStretch(1)
        outer.addLayout(self.actions)
        self._hover(False)
        self.refresh_avatar()

    def _add_action(self, glyph, tip, fn):
        b = IconButton(glyph, tip, size=28, icon_size=20, dim=0.6)
        sp = b.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        b.setSizePolicy(sp)
        b.clicked.connect(fn)
        self.actions.addWidget(b)
        self.action_buttons.append(b)
        self.buttons[glyph] = b

    def _hover(self, on):
        for b in self.action_buttons:
            b.setVisible(on or (self.role == "assistant" and (b.isChecked() or b.glyph == "speaker" and b.isChecked())))

    def enterEvent(self, e):
        self._hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover(False)
        super().leaveEvent(e)

    def rate(self, rating):
        self.rating = None if self.rating == rating else rating
        for g in ("thumb-up", "thumb-down"):
            b = self.buttons[g]
            b.setCheckable(True)
            b.setChecked(self.rating == {"thumb-up": "good", "thumb-down": "bad"}[g])
            b.refresh_icon()
        self.feedback.emit(self.rating or "none")

    def set_speaking(self, on):
        b = self.buttons.get("speaker")
        if b:
            b.setCheckable(True)
            b.setChecked(on)
            b.set_glyph("stop" if on else "speaker", "Stop reading" if on else "Read this aloud")
            b.setVisible(on or self.underMouse())

    def refresh_avatar(self):
        if self.avatar is not None:
            pal = self.palette()
            col = pal.color(QPalette.ColorRole.Highlight) if self.role in ("assistant", "question") else pal.color(QPalette.ColorRole.Text)
            glyph = {"assistant": "sparkle", "error": "warning", "info": "bell", "question": "question"}[self.role]
            self.avatar.setPixmap(glyph_pixmap(glyph, col, 18))

    def set_text(self, text):
        if self.label is not None:
            if self.label.text() != text:
                self.label.setText(text)
        else:
            self.md.set_markdown(text)

    def text(self):
        return self.label.text() if self.label is not None else self.md.markdown()

    def set_max_width(self, w):
        if self.role == "user":
            self.frame.setMaximumWidth(max(160, w))
        else:
            wide = max(240, int(w * 1.38))            # the agent's text uses most of the row; the user's pill ~70 %
            self.frame.setMaximumWidth(wide)
            self.md.set_max_width(wide - (22 + 10 + 6))

    def copy_text(self):
        QGuiApplication.clipboard().setText(self.text())

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_avatar()
        super().changeEvent(e)


Bubble = Message      # historical name


class EditCard(QFrame):
    """Edit-message state (design brief): the user's text inside a rounded card with Cancel (outlined) / Send (filled) pills."""
    cancelled = pyqtSignal()
    submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editCard")
        v = QVBoxLayout(self)
        v.setContentsMargins(SP3, SP2, SP2, SP)
        v.setSpacing(10)
        self.editor = GrowingTextEdit("editBox")
        self.editor.max_lines = 10
        self.editor.submitted.connect(self._send)
        self.editor.escaped.connect(self.cancelled.emit)
        v.addWidget(self.editor)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("pillOutline")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("pillFilled")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.send_btn)
        v.addLayout(row)

    def _send(self):
        t = self.editor.text().strip()
        if t:
            self.submitted.emit(t)

    def start(self, text):
        self.editor.setText(text)
        self.show()
        self.editor.setFocus()

    def text(self):
        return self.editor.text()


class StepRow(QWidget):
    """One row of the live action timeline: tool icon (with the connector line to the next row), title, the agent's
    narration in italics, and a status mark (spinner → animated check / cross). Updated in place; never rebuilt."""

    def __init__(self, step, show_raw, parent=None):
        super().__init__(parent)
        self.step = dict(step)
        self.show_raw = show_raw
        self.state = None
        self.has_next = False
        self._typed = 0
        self._full_text = ""
        self.typewriter = QTimer(self)
        self.typewriter.setInterval(TYPEWRITER_MS)
        self.typewriter.timeout.connect(self._type_tick)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(24, 24)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 2, 0, 0)
        self.title = QLabel()
        self.title.setObjectName("stepTitle")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self.title)
        self.narration = QLabel()
        self.narration.setObjectName("narration")
        self.narration.setWordWrap(True)
        self.narration.hide()
        col.addWidget(self.narration)
        self.raw = QLabel()
        self.raw.setObjectName("raw")
        self.raw.setWordWrap(True)
        self.raw.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.raw.hide()
        col.addWidget(self.raw)
        h.addLayout(col, 1)
        self.mark = ResultMark(20)
        h.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        self.refresh_icon()
        self.update_step(step, show_raw, first=True)

    def refresh_icon(self):
        col = QColor(self.palette().color(QPalette.ColorRole.Text))
        col.setAlphaF(0.85)
        self.icon.setPixmap(step_icon(self.step, col, 20))

    def update_step(self, step, show_raw, first=False):
        changed = first or any(step.get(k) != self.step.get(k) for k in ("output", "decision", "narration", "narration_done")) or show_raw != self.show_raw
        self.step, self.show_raw = dict(step), show_raw
        if not changed:
            return
        state = step_state(step) if step.get("kind") == "tool_call" else "ok"
        name = step.get("name") or ""
        inp = parse_input(step.get("input"))
        if name == "type_text" and inp.get("text"):
            self._full_text = str(inp["text"])[:160] + ("…" if len(str(inp["text"])) > 160 else "")
            if first and state == "busy":
                self._typed = 0
                self.typewriter.start()
                self._render_typed()
            elif not self.typewriter.isActive():
                self._typed = len(self._full_text)
                self._render_typed()
        else:
            self.title.setText(friendly_label(step, live=(state == "busy")))
        self.title.setProperty("done", state != "busy")
        polish(self.title)
        line = step.get("narration_done") if state in ("ok", "fail", "denied") and step.get("narration_done") else step.get("narration")
        self.narration.setText(line or "")
        self.narration.setVisible(bool(line))
        if show_raw and step.get("kind") == "tool_call":
            raw = step_input_summary(name, step.get("input"))
            out = step_output_summary(step.get("output"))
            txt = raw + (("\n→ " + out) if out else "")
            self.raw.setText(txt[:1500] + (" …" if len(txt) > 1500 else ""))
            self.raw.show()
        else:
            self.raw.hide()
        if state != self.state:
            animate = not first or state == "busy"
            self.mark.set_state(state, animate=animate)
            self.state = state

    def _type_tick(self):
        self._typed += 1
        if self._typed >= len(self._full_text):
            self._typed = len(self._full_text)
            self.typewriter.stop()
        self._render_typed()

    def _render_typed(self):
        shown = self._full_text[:self._typed]
        cursor = "▏" if self.typewriter.isActive() else ""
        self.title.setText("Typing: “%s%s”" % (shown, cursor) if self._full_text else "Typing")

    def paintEvent(self, e):
        if self.has_next:
            p = QPainter(self)
            c = QColor(self.palette().color(QPalette.ColorRole.Text))
            c.setAlphaF(0.14)
            pen = QPen(c)
            pen.setWidthF(2)
            p.setPen(pen)
            x = 12
            p.drawLine(x, 4 + 24 + 2, x, self.height())
            p.end()
        super().paintEvent(e)

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_icon()
        super().changeEvent(e)


class ActionFeed(QWidget):
    """'Worked: N actions' — the live action timeline. Opens by itself while the task runs; rows are appended as steps
    arrive and updated in place (spinner → check / cross, narration line), never rebuilt; collapses on request."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.button = QToolButton()
        self.button.setObjectName("chip")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setCheckable(True)
        self.button.setIconSize(QSize(15, 15))
        self.button.toggled.connect(self._toggle)
        head.addWidget(self.button, 0)
        head.addStretch(1)
        lay.addLayout(head)
        self.panel = QFrame()
        self.panel.setObjectName("steps")
        self.rows_layout = QVBoxLayout(self.panel)
        self.rows_layout.setContentsMargins(SP, 6, SP, 6)
        self.rows_layout.setSpacing(0)
        self.panel.hide()
        lay.addWidget(self.panel)
        self.rows = {}              # step id -> StepRow
        self.order = []
        self.show_raw = False
        self._auto_opened = False
        self._refresh_icon()

    def _refresh_icon(self):
        col = QColor(self.palette().color(QPalette.ColorRole.Text))
        col.setAlphaF(0.7)
        self.button.setIcon(glyph_icon("chevron-up" if self.button.isChecked() else "chevron-down", col, 15))

    def _toggle(self, on):
        self.panel.setVisible(on)
        self._refresh_icon()

    def set_steps(self, steps, show_raw, live=False):
        n = len(steps)
        text = ("Working: %d action%s" if live else "Worked: %d action%s") % (n, "" if n == 1 else "s")
        if self.button.text() != text:
            self.button.setText(text)
        if live and not self._auto_opened:
            self._auto_opened = True
            self.button.setChecked(True)
        raw_changed = show_raw != self.show_raw
        self.show_raw = show_raw
        ids = [s["id"] for s in steps]
        # append only: new steps go to the end; existing rows are updated in place; a vanished step (deleted task) is dropped
        for sid in list(self.rows):
            if sid not in ids:
                w = self.rows.pop(sid)
                self.order.remove(sid)
                discard(w)
        for s in steps:
            row = self.rows.get(s["id"])
            if row is None:
                row = StepRow(s, show_raw)
                self.rows[s["id"]] = row
                self.order.append(s["id"])
                self.rows_layout.addWidget(row)
                if live:
                    fade_in(row, 200)
            else:
                row.update_step(s, show_raw)
        for i, sid in enumerate(self.order):
            nxt = i < len(self.order) - 1
            if self.rows[sid].has_next != nxt:
                self.rows[sid].has_next = nxt
                self.rows[sid].update()

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._refresh_icon()
        super().changeEvent(e)


WorkedChip = ActionFeed     # historical name


class ImageCard(QFrame):
    """A generated picture in the chat: rounded thumbnail (radius 12 inside a radius-16 card, ≤ 320 px tall, fitted to the
    content width), the card exactly as wide as the picture so the caption and provider line read as its own, the prompt as
    caption. The whole card is a click target for ImageViewer. `regenerate_requested` relays the viewer's Regenerate — wired
    once, when the viewer is created (a second click on the card while the viewer is open only raises it)."""
    clicked = pyqtSignal()
    regenerate_requested = pyqtSignal()
    PAD_X = 8 + 1 + 1 + 8          # horizontal chrome around the thumbnail: the layout's 8 px margins + the 1 px stylesheet border, each side

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = dict(info)
        self.path = info["path"]
        self.setObjectName("imageCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to enlarge")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._image = load_image(self.path, 1024)          # decoded once, at most 1024 px on the long side
        self._w = 0
        self.viewer = None
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 10)
        v.setSpacing(6)
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(self.thumb)
        self.caption = QLabel(info.get("prompt") or os.path.basename(self.path))
        self.caption.setObjectName("imageCaption")
        self.caption.setWordWrap(True)
        self.caption.setContentsMargins(4, 0, 4, 0)
        v.addWidget(self.caption)
        prov = PROVIDER_LABELS.get(info.get("provider") or "", info.get("provider") or "")
        w, h = (self._image.width(), self._image.height()) if not self._image.isNull() else (info.get("width") or 0, info.get("height") or 0)
        self.meta = QLabel(" · ".join(x for x in (prov, ("%d × %d" % (w, h)) if w and h else "") if x))
        self.meta.setObjectName("imageMeta")
        self.meta.setContentsMargins(4, 0, 4, 0)
        self.meta.setVisible(bool(self.meta.text()))
        v.addWidget(self.meta)
        self.set_max_width(520)

    def set_max_width(self, w):
        w = max(220, int(w))
        if abs(w - self._w) < 8 and self.thumb.pixmap() is not None and not self.thumb.pixmap().isNull():
            return
        self._w = w
        self.setMaximumWidth(w)
        if self._image.isNull():
            self.thumb.setText("This image is no longer at %s" % os.path.basename(self.path))
            return
        scaled = self._image.scaled(w - self.PAD_X, 320, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.thumb.setPixmap(rounded_pixmap(QPixmap.fromImage(scaled), 12))
        self.setMaximumWidth(max(220, min(w, scaled.width() + self.PAD_X)))     # the card hugs the picture (border included): caption + meta sit under it, not beside empty card

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def open_viewer(self):
        """The enlarge viewer (non-modal, one per card). An open viewer is raised, never re-created and never re-wired:
        its Regenerate is connected to the card's `regenerate_requested` exactly once, here, when it is built."""
        if self.viewer is not None:
            try:
                self.viewer.raise_()
                self.viewer.activateWindow()
                return self.viewer
            except RuntimeError:
                self.viewer = None
        self.viewer = ImageViewer(self.info, self.window())
        self.viewer.regenerate_requested.connect(self.regenerate_requested)
        self.viewer.finished.connect(lambda _r: setattr(self, "viewer", None))
        self.viewer.show()
        return self.viewer


class ImageViewer(QDialog):
    """Enlarge viewer for a generated image: a window 80 % of the screen, the picture fitted on a dark scrim (a photo
    viewer's scrim is dark in both colour schemes; every word on it is white), the prompt above, one control row under it —
    Save as · Copy image · Open in Fab Photos · Set as wallpaper · Regenerate · Close (Esc). Controls that depend on a
    binary degrade: missing = disabled with a tooltip naming what is missing; Save as falls back to a copy in ~/Pictures
    when the file dialog cannot be shown."""
    regenerate_requested = pyqtSignal()

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = dict(info)
        self.path = info["path"]
        self.setObjectName("imageViewer")
        self.setWindowTitle(info.get("prompt") or os.path.basename(self.path))
        self.setModal(False)
        screen = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.resize(int(g.width() * 0.8), int(g.height() * 0.8))
        self._image = load_image(self.path, 4096)
        self._pix = QPixmap.fromImage(self._image) if not self._image.isNull() else QPixmap()
        self.procs = []
        v = QVBoxLayout(self)
        v.setContentsMargins(SP3, SP2, SP3, SP2)
        v.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = QLabel(info.get("prompt") or os.path.basename(self.path))
        self.title.setWordWrap(True)
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() + 1)
        f.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(f)
        col.addWidget(self.title)
        prov = PROVIDER_LABELS.get(info.get("provider") or "", info.get("provider") or "")
        size = ("%d × %d" % (self._image.width(), self._image.height())) if not self._image.isNull() else ""
        self.meta = QLabel(" · ".join(x for x in (prov, size, os.path.basename(self.path)) if x))
        self.meta.setObjectName("viewerMeta")
        col.addWidget(self.meta)
        head.addLayout(col, 1)
        self.close_icon = IconButton("close", "Close (Esc)", size=36, icon_size=20, object_name="viewerIcon", color="#F4F6FA")
        self.close_icon.clicked.connect(self.reject)
        head.addWidget(self.close_icon, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.picture.setMinimumSize(120, 80)
        if self._pix.isNull():
            self.picture.setText("This image is no longer at %s" % self.path)
        v.addWidget(self.picture, 1)
        self.toast = QLabel("")
        self.toast.setObjectName("viewerToast")
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast.setFixedHeight(20)
        v.addWidget(self.toast)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.toast.setText(""))
        # the control row: one row when it fits, two rows of three on a narrow viewer (reflowed on resize); the dialog keeps
        # the 80 % size it asked for (no layout minimum pushes it wider)
        self.rows = [QHBoxLayout(), QHBoxLayout()]
        for r in self.rows:
            r.setSpacing(8)
            v.addLayout(r)
        v.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        self.buttons = {}
        self.button_order = []
        have = not self._pix.isNull()
        self.gwenview, self.xdg_open, self.wallpaper_bin = shutil.which("gwenview"), shutil.which("xdg-open"), shutil.which("plasma-apply-wallpaperimage")
        self._button("save", "save", "Save as", self.save_as, have, "Save a copy where you choose", "Waiting for the image")
        self._button("copy", "copy", "Copy image", self.copy_image, have, "Copy the picture to the clipboard", "Waiting for the image")
        self._button("open", "expand", "Open in Fab Photos", self.open_photos, bool(self.gwenview or self.xdg_open),
                     "Open the file in Fab Photos" if self.gwenview else "Open the file in your image viewer", "Fab Photos (gwenview) is not installed")
        self._button("wallpaper", "wallpaper", "Set as wallpaper", self.set_wallpaper, bool(self.wallpaper_bin) and have,
                     "Use this picture as the desktop wallpaper", "plasma-apply-wallpaperimage is not available on this machine" if not self.wallpaper_bin else "Waiting for the image")
        self._button("regenerate", "retry", "Regenerate", self.regenerate, True, "Ask for a new picture from the same prompt", "")
        self._button("close", "close", "Close", self.reject, True, "Esc", "")
        self._rows_used = 0
        self._reflow()

    def _button(self, key, glyph, text, fn, enabled, tip, reason):
        b = QPushButton(text)
        b.setObjectName("viewerBtn")
        b.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        b.setIcon(glyph_icon(glyph, QColor("#F4F6FA") if enabled else QColor(244, 246, 250, 102), 18))
        b.setIconSize(QSize(18, 18))
        b.setEnabled(enabled)
        b.setToolTip(tip if enabled else reason)
        b.clicked.connect(fn)
        self.buttons[key] = b
        self.button_order.append(b)
        return b

    def _reflow(self):
        need = sum(b.sizeHint().width() for b in self.button_order) + 8 * (len(self.button_order) - 1)
        rows = 1 if need <= self.width() - 2 * SP3 else 2
        if rows == self._rows_used:
            return
        self._rows_used = rows
        for r in self.rows:
            while r.count():
                r.takeAt(0)
        per = len(self.button_order) if rows == 1 else (len(self.button_order) + 1) // 2
        for i, b in enumerate(self.button_order):
            r = self.rows[0 if i < per else 1]
            if r.count() == 0:
                r.addStretch(1)
            r.addWidget(b)
        for r in self.rows:
            if r.count():
                r.addStretch(1)

    def flash(self, text):
        self.toast.setText(text)
        self._toast_timer.start(2400)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reflow()
        self._fit()

    def showEvent(self, e):
        super().showEvent(e)
        self._reflow()
        self._fit()

    def _fit(self):
        if self._pix.isNull():
            return
        area = self.picture.contentsRect().size()
        if area.width() < 10 or area.height() < 10:
            return
        self.picture.setPixmap(self._pix.scaled(area, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    # ---- controls
    def save_as(self):
        suggested = os.path.join(pictures_dir(), os.path.basename(self.path))
        try:
            dest, _filter = QFileDialog.getSaveFileName(self, "Save image as", suggested, "Images (*.png *.jpg *.jpeg);;All files (*)")
        except Exception:
            dest = None                                # no file dialog here: a copy in ~/Pictures instead
        if dest is None:
            dest = self._free_name(suggested)
        if not dest:
            return                                     # cancelled
        try:
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            shutil.copyfile(self.path, dest)
            self.flash("Saved to %s" % dest)
        except OSError as e:
            self.flash("Could not save the image (%s)" % e.strerror)

    @staticmethod
    def _free_name(path):
        if not os.path.exists(path):
            return path
        stem, ext = os.path.splitext(path)
        return "%s-%s%s" % (stem, time.strftime("%H%M%S"), ext)

    def copy_image(self):
        if self._image.isNull():
            return
        QGuiApplication.clipboard().setImage(self._image)
        self.flash("Image copied")

    def open_photos(self):
        app = self.gwenview or self.xdg_open
        if not app:
            return
        try:
            self.procs.append(subprocess.Popen([app, self.path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
            self.flash("Opened in Fab Photos" if self.gwenview else "Opened in your image viewer")
        except OSError as e:
            self.flash("Could not open the image (%s)" % e.strerror)

    def set_wallpaper(self):
        if not self.wallpaper_bin:
            return
        p = QProcess(self)
        p.finished.connect(lambda code, _st: self.flash("Wallpaper set" if code == 0 else "Could not set the wallpaper"))
        p.start(self.wallpaper_bin, [self.path])
        self.procs.append(p)
        self.flash("Setting the wallpaper…")

    def regenerate(self):
        self.regenerate_requested.emit()
        self.accept()


class Turn(QWidget):
    """One task of the chat: the request pill (or its edit card), the live action timeline, the agent's messages, status,
    and a card per generated image (a finished generate_image step, or a ~/Pictures/Fab OS/*.png|jpg path in the final text,
    for files that exist — once per file)."""
    edit_requested = pyqtSignal(int, str)
    edit_submitted = pyqtSignal(int, str)
    edit_cancelled = pyqtSignal(int)
    retry_requested = pyqtSignal(int)
    stop_requested = pyqtSignal(int)
    speak_requested = pyqtSignal(str)
    feedback = pyqtSignal(int, str)
    regenerate_requested = pyqtSignal(int)

    def __init__(self, task_id, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.task = None
        self.image_cards = {}           # absolute path -> ImageCard
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.user = Message("user")
        self.user.edit_requested.connect(self.begin_edit)
        self.user.retry_requested.connect(lambda: self.retry_requested.emit(self.task_id))
        self.lay.addWidget(self.user)
        self.edit_card = EditCard()
        self.edit_card.hide()
        self.edit_card.cancelled.connect(self.cancel_edit)
        self.edit_card.submitted.connect(lambda t: self.edit_submitted.emit(self.task_id, t))
        self.lay.addWidget(self.edit_card)
        self.chip = ActionFeed()
        self.chip.hide()
        self.lay.addWidget(self.chip)
        self.step_widgets = {}          # step id -> Message
        self.order = []
        self.status_row = QHBoxLayout()
        self.status_row.setContentsMargins(30, 0, 0, 0)
        self.status_row.setSpacing(8)
        self.typing = TypingIndicator()
        self.typing.hide()
        self.status_row.addWidget(self.typing, 0)
        self.status_label = QLabel()
        self.status_label.setObjectName("chipText")
        self.status_label.hide()
        self.status_row.addWidget(self.status_label, 0)
        self.stop_btn = IconButton("stop", "Stop this task", size=26, icon_size=14)
        self.stop_btn.clicked.connect(lambda: self.stop_requested.emit(self.task_id))
        self.stop_btn.hide()
        self.status_row.addWidget(self.stop_btn, 0)
        self.status_row.addStretch(1)
        self.lay.addLayout(self.status_row)
        self.max_w = 520
        self.editing = False

    def set_max_width(self, w):
        self.max_w = w
        self.user.set_max_width(w)
        for b in self.step_widgets.values():
            b.set_max_width(w)
        for c in self.image_cards.values():
            c.set_max_width(max(240, int(w * 1.38)))

    def _sync_images(self, task, animate):
        wanted = task_images(task)
        keep = {i["path"] for i in wanted}
        for p in list(self.image_cards):
            if p not in keep:
                discard(self.image_cards.pop(p))
        for info in wanted:
            if info["path"] in self.image_cards:
                continue
            card = ImageCard(info)
            card.set_max_width(max(240, int(self.max_w * 1.38)))
            card.clicked.connect(lambda c=card: self.open_image(c))
            card.regenerate_requested.connect(lambda: self.regenerate_requested.emit(self.task_id))   # once per card, not per click
            self.image_cards[info["path"]] = card
            self.lay.insertWidget(self.lay.count() - 1, card)      # before the status row
            if animate:
                fade_in(card)

    def open_image(self, card):
        return card.open_viewer()       # Regenerate is wired on the card (see _sync_images), so repeated clicks add no connections

    def begin_edit(self):
        self.editing = True
        self.user.hide()
        self.edit_card.start(user_text((self.task or {}).get("request")))
        self.edit_requested.emit(self.task_id, self.edit_card.text())

    def cancel_edit(self):
        was = self.editing
        self.editing = False
        self.edit_card.hide()
        self.user.show()
        if was:
            self.edit_cancelled.emit(self.task_id)

    def _message(self, step, animate):
        sid = step["id"]
        b = self.step_widgets.get(sid)
        if b is None:
            kind = step["kind"]
            role = {"assistant": "assistant", "final": "assistant", "error": "error", "question": "question", "answer": "user", "watch_hit": "info"}.get(kind, "info")
            b = Message(role)
            b.set_max_width(self.max_w)
            b.edit_requested.connect(self.begin_edit)
            b.retry_requested.connect(lambda: self.retry_requested.emit(self.task_id))
            b.speak_requested.connect(self.speak_requested.emit)
            b.feedback.connect(lambda r: self.feedback.emit(self.task_id, r))
            self.step_widgets[sid] = b
            self.order.append(sid)
            self.lay.insertWidget(self.lay.count() - 1, b)      # before the status row
            if animate:
                fade_in(b)
        return b

    def update(self, task, show_raw, animate):
        first = self.task is None
        self.task = task
        superseded = (task.get("title") or "").startswith(SUPERSEDED)
        self.user.set_text(user_text(task.get("request")))
        self.user.setToolTip("Edited — this version was replaced" if superseded else ts_clock(task.get("created")))
        if superseded and self.user.frame.graphicsEffect() is None:
            eff = QGraphicsOpacityEffect(self.user.frame)
            eff.setOpacity(0.45)
            self.user.frame.setGraphicsEffect(eff)
        st = task.get("status")
        live = st in ("queued", "running", "waiting_approval", "waiting_user")
        steps = task.get("steps") or []
        tools = [s for s in steps if s["kind"] in ("tool_call", "compact")]
        if tools:
            self.chip.set_steps(tools, show_raw, live=live)
            if self.chip.isHidden():
                self.chip.show()
        else:
            self.chip.hide()
        texts = [s for s in steps if s["kind"] in ("assistant", "final", "error", "question", "answer", "watch_hit")]
        shown = []
        last_text = None
        for s in texts:
            if s["kind"] == "final":
                if not (s.get("output") or "").strip() or (last_text is not None and s["output"].strip() == last_text.strip()):
                    continue
            if s["kind"] in ("assistant", "final"):
                last_text = s.get("output") or ""
            shown.append(s)
        seen = set()
        for s in shown:
            seen.add(s["id"])
            b = self._message(s, animate and not first)
            if s["kind"] == "question":
                body = s.get("output") or s.get("input") or ""
                b.set_text(body + ("\n\n*Answer in the box below.*" if st == "waiting_user" else ""))
            elif s["kind"] == "watch_hit":
                b.set_text("**Background watch fired** (%s)\n\n%s" % (s.get("name") or "watch", step_output_summary(s.get("output"))[:800]))
            elif s["kind"] == "error":
                b.set_text("**Something went wrong**\n\n" + (s.get("output") or s.get("input") or ""))
            else:
                b.set_text(s.get("output") or "")
        for sid in list(self.step_widgets):
            if sid not in seen:
                w = self.step_widgets.pop(sid)
                self.order.remove(sid)
                discard(w)
        self._sync_images(task, animate and not first)
        self.typing.setVisible(st in ("queued", "running"))
        self.stop_btn.setVisible(st in ("queued", "running", "waiting_approval"))
        label = {"queued": "Queued…", "waiting_approval": "Waiting for your approval", "waiting_user": "Waiting for your answer"}.get(st, "")
        if st == "cancelled":
            label = "Stopped"
        elif st in ("done", "failed") and not shown:
            label = STATUS_TEXT.get(st, st)
        self.status_label.setText(label)
        self.status_label.setVisible(bool(label))

    def last_assistant_text(self):
        for sid in reversed(self.order):
            b = self.step_widgets.get(sid)
            if b is not None and b.role == "assistant":
                return b.text()
        return ""

    def assistant_messages(self):
        return [self.step_widgets[s] for s in self.order if self.step_widgets[s].role == "assistant"]


class ConversationView(QScrollArea):
    """The chat body. Updates turns in place; keeps the user's scroll position unless they were at the bottom."""
    edit_requested = pyqtSignal(int, str)
    edit_submitted = pyqtSignal(int, str)
    edit_cancelled = pyqtSignal(int)
    retry_requested = pyqtSignal(int)
    stop_requested = pyqtSignal(int)
    speak_requested = pyqtSignal(str)
    feedback = pyqtSignal(int, str)
    regenerate_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName("chatBody")
        self.vl = QVBoxLayout(self.container)
        self.vl.setContentsMargins(SP2 + 8, SP2, SP2 + 8, SP2)
        self.vl.setSpacing(SP3)
        self.vl.addStretch(1)
        self.setWidget(self.container)
        self.turns = {}
        self.stick = True
        vb = self.verticalScrollBar()
        vb.valueChanged.connect(self._on_scroll)
        vb.rangeChanged.connect(self._on_range)

    def at_bottom(self):
        vb = self.verticalScrollBar()
        return vb.value() >= vb.maximum() - 6

    def _on_scroll(self, _v):
        self.stick = self.at_bottom()

    def _on_range(self, _lo, _hi):
        if self.stick:
            vb = self.verticalScrollBar()
            vb.setValue(vb.maximum())

    def scroll_to_bottom(self):
        self.stick = True
        vb = self.verticalScrollBar()
        vb.setValue(vb.maximum())

    def clear(self):
        self.setUpdatesEnabled(False)
        for t in self.turns.values():
            discard(t)
        self.turns = {}
        self.stick = True
        self.setUpdatesEnabled(True)

    def set_thread(self, tasks, show_raw, animate=True):
        """tasks: full task dicts (with steps) in creation order."""
        was_bottom = self.at_bottom()
        self.stick = was_bottom or self.stick
        animate = animate and bool(self.turns)          # the first render of a chat appears at once; later additions fade in
        self.setUpdatesEnabled(False)
        try:
            ids = [t["id"] for t in tasks]
            for tid in list(self.turns):
                if tid not in ids:
                    w = self.turns.pop(tid)
                    discard(w)
            for i, t in enumerate(tasks):
                turn = self.turns.get(t["id"])
                if turn is None:
                    turn = Turn(t["id"])
                    turn.set_max_width(self._bubble_width())
                    turn.edit_requested.connect(self.edit_requested.emit)
                    turn.edit_submitted.connect(self.edit_submitted.emit)
                    turn.edit_cancelled.connect(self.edit_cancelled.emit)
                    turn.retry_requested.connect(self.retry_requested.emit)
                    turn.stop_requested.connect(self.stop_requested.emit)
                    turn.speak_requested.connect(self.speak_requested.emit)
                    turn.feedback.connect(self.feedback.emit)
                    turn.regenerate_requested.connect(self.regenerate_requested.emit)
                    self.turns[t["id"]] = turn
                    self.vl.insertWidget(i, turn)
                    if animate:
                        fade_in(turn)
                turn.update(t, show_raw, animate)
        finally:
            self.setUpdatesEnabled(True)
        if self.stick:
            QTimer.singleShot(0, self.scroll_to_bottom)

    def _bubble_width(self):
        return int(max(240, self.viewport().width() * 0.70))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self._bubble_width()
        for t in self.turns.values():
            t.set_max_width(w)


# ----------------------------------------------------------------------------- sidebar
class ConvRow(QWidget):
    """Sidebar row (48 px, radius 12): chat glyph + title; delete icon on hover; status dot while the chat is active."""
    delete_requested = pyqtSignal(int)

    def __init__(self, root_id, parent=None):
        super().__init__(parent)
        self.root_id = root_id
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(48)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 6, 0)
        h.setSpacing(10)
        self.glyph = QLabel()
        self.glyph.setFixedSize(20, 20)
        h.addWidget(self.glyph, 0)
        self.title = QLabel()
        self.title.setObjectName("rowTitle")
        h.addWidget(self.title, 1)
        self.delete = IconButton("delete", "Delete this chat", size=26, icon_size=15)
        sp = self.delete.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.delete.setSizePolicy(sp)
        self.delete.clicked.connect(lambda: self.delete_requested.emit(self.root_id))
        self.delete.hide()
        h.addWidget(self.delete, 0, Qt.AlignmentFlag.AlignVCenter)
        self._full_title = ""
        self.meta = ""
        self.active = False
        self.refresh_glyph()

    def refresh_glyph(self):
        pal = self.palette()
        if self.active:
            self.glyph.setPixmap(glyph_pixmap("bolt", pal.color(QPalette.ColorRole.Highlight), 18))
        else:
            col = QColor(pal.color(QPalette.ColorRole.Text))
            col.setAlphaF(0.75)
            self.glyph.setPixmap(glyph_pixmap("chat", col, 18))

    def enterEvent(self, e):
        self.delete.show()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.delete.hide()
        super().leaveEvent(e)

    def update_conv(self, conv):
        root, tasks = conv["root"], conv["tasks"]
        title = user_text(root.get("request")) or root.get("title") or "Chat"
        title = title.replace(SUPERSEDED, "").split("\n")[0]
        self._full_title = title
        self._elide()
        last = tasks[-1]
        live = [t for t in tasks if not (t.get("title") or "").startswith(SUPERSEDED)]
        n = len(live) or len(tasks)
        st = STATUS_TEXT.get(last["status"], last["status"])
        parts = ["%d turn%s" % (n, "" if n == 1 else "s"), ts_clock(conv["updated"]), st]
        if len(live) != len(tasks):
            parts.append("edited")              # a turn was edited and resent; the old version stays in the chat, dimmed
        self.meta = " · ".join(parts)
        self.setToolTip(title + "\n" + self.meta)
        active = last["status"] in ACTIVE
        if active != self.active:
            self.active = active
            self.refresh_glyph()

    def _elide(self):
        fm = QFontMetrics(self.title.font())
        self.title.setText(fm.elidedText(self._full_title, Qt.TextElideMode.ElideRight, max(60, self.width() - 84)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_glyph()
        super().changeEvent(e)


class SideItem(QToolButton):
    """A 48 px row of the sidebar's bottom group: icon 20 + label, radius 12, hover fill."""

    def __init__(self, glyph, label, tooltip="", parent=None):
        super().__init__(parent)
        self.glyph = glyph
        self.setObjectName("sideItem")
        self.setText(label)
        self.setToolTip(tooltip or label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(20, 20))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(48)
        self.refresh_icon()

    def refresh_icon(self):
        col = QColor(self.palette().color(QPalette.ColorRole.Text))
        col.setAlphaF(0.85)
        self.setIcon(glyph_icon(self.glyph, col, 20))

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_icon()
        super().changeEvent(e)


class Sidebar(QFrame):
    selected = pyqtSignal(object)            # root id or None
    new_chat = pyqtSignal()
    delete_requested = pyqtSignal(int)
    clear_all = pyqtSignal()
    open_settings = pyqtSignal()
    open_about = pyqtSignal()
    open_appearance = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDEBAR_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(SP3, SP3, SP3, SP3)
        v.setSpacing(SP)
        self.new_btn = QPushButton("New chat")
        self.new_btn.setObjectName("newChatPill")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setIconSize(QSize(18, 18))
        self.new_btn.setFixedHeight(36)
        self.new_btn.clicked.connect(self.new_chat.emit)
        v.addWidget(self.new_btn)
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search chats")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.rebuild())
        wl.addWidget(self.search)
        self.search_icon = QLabel(wrap)
        self.search_icon.setFixedSize(18, 18)
        v.addWidget(wrap)
        self.list = QListWidget()
        self.list.setSpacing(0)
        self.list.setUniformItemSizes(False)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._on_current)
        v.addWidget(self.list, 1)
        hair = QFrame()
        hair.setObjectName("hairline")
        hair.setFixedHeight(1)
        v.addWidget(hair)
        bottom = QVBoxLayout()
        bottom.setSpacing(2)
        bottom.setContentsMargins(0, 4, 0, 0)
        self.clear_btn = SideItem("delete", "Clear conversations", "Delete every chat and its recorded steps")
        self.clear_btn.clicked.connect(self.clear_all.emit)
        self.appearance_btn = SideItem("sun", "Appearance follows system", "Fab AI Controls uses the system colour scheme — change it in Fab Settings › Colours & Themes")
        self.appearance_btn.clicked.connect(self.open_appearance.emit)
        self.settings_btn = SideItem("settings", "Settings", "AI provider, permission mode, voice, mail")
        self.settings_btn.clicked.connect(self.open_settings.emit)
        self.about_btn = SideItem("info", "About Fab OS")
        self.about_btn.clicked.connect(self.open_about.emit)
        for b in (self.clear_btn, self.appearance_btn, self.settings_btn, self.about_btn):
            bottom.addWidget(b)
        v.addLayout(bottom)
        self.convs = []
        self.rows = {}          # root id -> ConvRow
        self.keys = []          # current ordered keys ("h:Today" / "c:<root>")
        self.current_root = None
        self.refresh_icons()

    def refresh_icons(self):
        pal = self.palette()
        self.new_btn.setIcon(glyph_icon("add", pal.color(QPalette.ColorRole.HighlightedText), 18))
        muted = QColor(pal.color(QPalette.ColorRole.Text))
        muted.setAlphaF(0.55)
        self.search_icon.setPixmap(glyph_pixmap("search", muted, 18))
        self.search_icon.move(11, (self.search.sizeHint().height() - 18) // 2 + 1)
        for b in (self.clear_btn, self.appearance_btn, self.settings_btn, self.about_btn):
            b.refresh_icon()
        for r in self.rows.values():
            r.refresh_glyph()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.search_icon.move(11, (self.search.height() - 18) // 2)

    def set_conversations(self, convs):
        self.convs = convs
        self.rebuild()

    def _filtered(self):
        q = self.search.text().strip().lower()
        if not q:
            return self.convs
        out = []
        for c in self.convs:
            hay = " ".join((t.get("title") or "") + " " + (t.get("request") or "") + " " + (t.get("result") or "") for t in c["tasks"]).lower()
            if q in hay:
                out.append(c)
        return out

    def rebuild(self):
        """Reconcile the list with the current chats IN PLACE — never clear-and-rebuild: rows already at their position
        are kept (hover, scroll and selection survive), a row that moved is re-inserted at its new position, vanished
        rows are removed, new ones are created."""
        convs = self._filtered()
        plan = []                       # (key, kind, value) in display order; key "h:Today" / "c:<root id>"
        group = None
        for c in convs:
            g = day_group(c["updated"])
            if g != group:
                group = g
                plan.append(("h:" + g, "h", g))
            plan.append(("c:%d" % c["root"]["id"], "c", c))
        wanted = {k for k, _, _ in plan}
        self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        try:
            for i, (key, kind, val) in enumerate(plan):
                it = self.list.item(i)
                while it is not None and it.data(KEY_ROLE) not in wanted:      # gone (deleted / filtered out): drop it here
                    self._drop(i)
                    it = self.list.item(i)
                if it is None or it.data(KEY_ROLE) != key:
                    j = self._find(key, i + 1)
                    if j is not None:                                          # moved up (that chat got activity): re-insert
                        it = self.list.takeItem(j)                             # the view destroys its row widget: rebuilt below
                    else:
                        it = QListWidgetItem()
                        it.setData(KEY_ROLE, key)
                    self.list.insertItem(i, it)
                    self._make_widget(it, kind, val)
                if kind == "c":
                    self.rows[val["root"]["id"]].update_conv(val)
            while self.list.count() > len(plan):
                self._drop(self.list.count() - 1)
            self.keys = [k for k, _, _ in plan]
            self._select_row(self.current_root)
        finally:
            self.list.setUpdatesEnabled(True)
            self.list.blockSignals(False)

    def _find(self, key, start):
        for j in range(start, self.list.count()):
            if self.list.item(j).data(KEY_ROLE) == key:
                return j
        return None

    def _drop(self, i):
        it = self.list.takeItem(i)
        rid = it.data(Qt.ItemDataRole.UserRole)
        if rid is not None:
            self.rows.pop(rid, None)

    def _make_widget(self, it, kind, val):
        if kind == "h":
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            lab = QLabel(val)
            lab.setObjectName("groupHeader")
            it.setSizeHint(QSize(0, 30))
            self.list.setItemWidget(it, lab)
        else:
            row = ConvRow(val["root"]["id"])
            row.delete_requested.connect(self.delete_requested.emit)
            it.setData(Qt.ItemDataRole.UserRole, val["root"]["id"])
            it.setSizeHint(QSize(0, 48))
            self.list.setItemWidget(it, row)
            self.rows[val["root"]["id"]] = row

    def _select_row(self, root_id):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == root_id:
                if self.list.currentItem() is not it:
                    self.list.setCurrentItem(it)
                return
        self.list.setCurrentItem(None)

    def select(self, root_id):
        self.current_root = root_id
        self.list.blockSignals(True)
        self._select_row(root_id)
        self.list.blockSignals(False)

    def _on_current(self, it, _prev):
        rid = it.data(Qt.ItemDataRole.UserRole) if it else None
        if rid is not None and rid != self.current_root:
            self.current_root = rid
            self.selected.emit(rid)

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_icons()
        super().changeEvent(e)


# ----------------------------------------------------------------------------- voice (fabos-voice CLI, see the voice contract)
def _stop_process(p, wait_ms=400):
    """Stop a fabos-voice QProcess for good: no more signals from it, killed, and given a moment to exit — Qt must never
    destroy a QProcess that is still running (the finished signal would then land on a deleted wrapper and abort the app)."""
    if p is None:
        return
    try:
        for sig in (p.finished, p.errorOccurred):
            try:
                sig.disconnect()
            except TypeError:
                pass
        if p.state() != QProcess.ProcessState.NotRunning:
            p.kill()
            p.waitForFinished(wait_ms)
    except RuntimeError:              # the wrapper is already gone
        pass


class Voice(QObject):
    """Thin client of the fabos-voice CLI. listen-once records after a chime and prints the transcript (exit 3 = nothing
    heard, exit 4 = no speech-to-text backend); say TEXT speaks (say --test says the test line); status prints one JSON
    line. Everything runs through QProcess so the UI never blocks; when the binary is missing or stt == none the UI shows
    the mic disabled. A failed listen NEVER fails silently: nothing_heard / unavailable carry the CLI's stderr reason
    (voice_failure_text) and the composer shows it in the toast."""
    status_changed = pyqtSignal()
    transcript = pyqtSignal(str)
    nothing_heard = pyqtSignal(str)
    unavailable = pyqtSignal(str)
    listening_changed = pyqtSignal(bool)
    speaking_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bin = shutil.which("fabos-voice")
        self.status = None
        self.listen_proc = None
        self.say_proc = None
        self._status_proc = None
        self.probe()

    def probe(self):
        if not self.bin:
            self.status = {"stt": "none", "tts": "none", "mic": False, "wake": False, "listening": False}
            self.status_changed.emit()
            return
        self.stop_probe()
        p = QProcess(self)
        p.setProgram(self.bin)
        p.setArguments(["status"])
        p.finished.connect(lambda code, _st, p=p: self._probed(p, code))
        self._status_proc = p
        p.start()

    def stop_probe(self):
        p, self._status_proc = self._status_proc, None
        _stop_process(p)

    def shutdown(self):
        """Closing the window: every fabos-voice process is stopped before Qt tears the objects down."""
        self.stop_listening()
        self.stop_speaking()
        self.stop_probe()

    def _probed(self, p, code):
        if p is not self._status_proc:
            return
        self._status_proc = None
        try:
            line = bytes(p.readAllStandardOutput()).decode(errors="replace").strip().splitlines()
            self.status = json.loads(line[-1]) if line else {"stt": "none", "tts": "none"}
        except (ValueError, IndexError):
            self.status = {"stt": "none", "tts": "none"}
        except RuntimeError:              # wrapper deleted underneath us
            return
        if code != 0 and not isinstance(self.status, dict):
            self.status = {"stt": "none", "tts": "none"}
        self.status_changed.emit()

    def stt_available(self):
        return bool(self.bin) and (self.status is None or (self.status or {}).get("stt", "none") != "none")

    def tts_available(self):
        return bool(self.bin) and (self.status is None or (self.status or {}).get("tts", "none") != "none")

    def is_listening(self):
        return self.listen_proc is not None

    def listen(self, timeout=10):
        if not self.stt_available():
            self.unavailable.emit(voice_failure_text(None if not self.bin else 4, "" if self.bin else "fabos-voice is not installed"))
            return False
        if self.listen_proc is not None:
            self.stop_listening()
            return False
        p = QProcess(self)
        p.setProgram(self.bin)
        p.setArguments(["listen-once", "--timeout", str(int(timeout))])
        p.finished.connect(lambda code, _st, p=p: self._listened(p, code))
        p.errorOccurred.connect(lambda _e, p=p: self._listened(p, 127) if p is self.listen_proc else None)    # FailedToStart: no binary
        self.listen_proc = p
        p.start()
        self.listening_changed.emit(True)
        return True

    def stop_listening(self):
        p, self.listen_proc = self.listen_proc, None
        if p is not None:
            _stop_process(p)
            self.listening_changed.emit(False)

    def _listened(self, p, code):
        if p is not self.listen_proc:
            return
        self.listen_proc = None
        self.listening_changed.emit(False)
        try:
            out = bytes(p.readAllStandardOutput()).decode(errors="replace").strip()
            err = bytes(p.readAllStandardError()).decode(errors="replace")
        except RuntimeError:
            return
        if code == 0 and out:
            self.transcript.emit(out)
        elif code == 3 or (code == 0 and not out):
            self.nothing_heard.emit(voice_failure_text(3, err))
        else:
            if code in (4, 127):
                self.status = dict(self.status or {}, stt="none")
                self.status_changed.emit()
            self.unavailable.emit(voice_failure_text(code, err))

    def is_speaking(self):
        return self.say_proc is not None

    def say(self, text, args=None):
        if self.say_proc is not None:
            self.stop_speaking()
            return False
        if not self.tts_available() or not (args or (text or "").strip()):
            return False
        p = QProcess(self)
        p.setProgram(self.bin)
        p.setArguments(args or ["say", text[:4000]])
        p.finished.connect(lambda code, _st, p=p: self._spoken(p, code))
        p.errorOccurred.connect(lambda _e, p=p: self._spoken(p, 4) if p is self.say_proc else None)
        self.say_proc = p
        p.start()
        self.speaking_changed.emit(True)
        return True

    def say_test(self):
        """fabos-voice say --test (the CLI's own test line); an older CLI without --test gets the line as plain text."""
        self._test_fallback = True
        return self.say(VOICE_TEST_LINE, args=["say", "--test"])

    def stop_speaking(self):
        p, self.say_proc = self.say_proc, None
        if p is not None:
            _stop_process(p)
            self.speaking_changed.emit(False)

    def _spoken(self, p, code):
        if p is not self.say_proc:
            return
        self.say_proc = None
        try:
            args = list(p.arguments())
        except RuntimeError:
            args = []
        if code == 2 and getattr(self, "_test_fallback", False) and args == ["say", "--test"]:
            self._test_fallback = False                  # this fabos-voice has no --test yet: say the line as text
            self.speaking_changed.emit(False)
            self.say(VOICE_TEST_LINE)
            return
        self._test_fallback = False
        if code == 4:
            self.status = dict(self.status or {}, tts="none")
            self.status_changed.emit()
        self.speaking_changed.emit(False)


# ----------------------------------------------------------------------------- settings
def voice_failure_text(code, stderr=""):
    """The toast for a failed fabos-voice run. The CLI's own stderr reason wins when it gave one ("No microphone found on
    this computer."); otherwise a plain sentence per exit code: 3 = nothing heard, 4 = no speech backend, 127 / None =
    the binary is missing. Never silent."""
    lines = [ln.strip() for ln in (stderr or "").strip().splitlines() if ln.strip()]
    reason = lines[-1] if lines else ""
    low = reason.lower()
    if any(k in low for k in ("pw-record", "pipewire", "pulse", "parec", "arecord", "connection refused", "no audio")):
        return "No audio session — " + reason
    if code == 3:
        return reason or "Microphone is muted or silent — I heard nothing. Check the input level in Fab Settings › Sound."
    if code in (127, None) or "not installed" in low or "no such file" in low:
        return "Speech engine missing — fabos-voice is not installed; run fabos-voice doctor once it is."
    if code == 4:
        return reason or "Speech engine missing — run fabos-voice doctor"
    return reason or "Voice failed (exit %s) — run fabos-voice doctor" % code


class Disclosure(QWidget):
    """An "Advanced" expander: one chevron row, collapsed by default, that reveals a small form underneath. No height
    animation (cheap, and the dialog simply re-fits); the chevron flips and follows the palette."""
    toggled = pyqtSignal(bool)

    def __init__(self, title="Advanced", parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)
        self.btn = QToolButton()
        self.btn.setObjectName("disclosure")
        self.btn.setCheckable(True)
        self.btn.setText(title)
        self.btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setIconSize(QSize(16, 16))
        v.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.content = QWidget()
        self.content.setVisible(False)
        self.form = QFormLayout(self.content)
        self.form.setContentsMargins(8, 0, 0, 0)
        self.form.setSpacing(8)
        v.addWidget(self.content)
        self.btn.toggled.connect(self._toggle)
        self.refresh_icon()

    def addRow(self, label, widget=None):
        if widget is None:
            self.form.addRow(label)
        else:
            self.form.addRow(label, widget)

    def is_open(self):
        return self.btn.isChecked()

    def set_open(self, on):
        self.btn.setChecked(bool(on))

    def _toggle(self, on):
        self.content.setVisible(on)
        self.refresh_icon()
        self.toggled.emit(on)
        w = self.window()
        if w is not None and w is not self:
            QTimer.singleShot(0, getattr(w, "refit", w.adjustSize))

    def refresh_icon(self):
        c = QColor(self.palette().color(QPalette.ColorRole.Text))
        c.setAlphaF(0.72)
        self.btn.setIcon(glyph_icon("chevron-up" if self.btn.isChecked() else "chevron-down", c, 16))

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_icon()
        super().changeEvent(e)


class WrapLabel(QLabel):
    """A word-wrapped label whose sizeHint is its height at the width it ACTUALLY has. QFormLayout (Qt 6) reserves a
    wrapped field's sizeHint() height — which QLabel computes at a guessed width — and never its heightForWidth() at
    the real column width (measured: a 6-line hint got a 176 px row for 112 px of text; a result label squeezed next
    to a button got a 64 px row for 80 px of text and was clipped). Reporting the height for the laid-out width, and
    asking for a re-layout when that width changes, makes every row exactly as tall as its text."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        pol = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)
        self._laid_width = 0

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != self._laid_width:
            self._laid_width = e.size().width()
            self.updateGeometry()              # the height for this width is only known now

    def sizeHint(self):
        base = super().sizeHint()
        if self._laid_width > 0 and self.text():
            return QSize(base.width(), self.heightForWidth(self._laid_width))
        return base

    def minimumSizeHint(self):
        base = super().minimumSizeHint()
        if self._laid_width > 0 and self.text():
            return QSize(base.width(), self.heightForWidth(self._laid_width))   # never shorter than its text: no clipped rows
        return base


class TabPage(QWidget):
    """One settings tab. QTabWidget::sizeHint() and QStackedLayout::heightForWidth() ask EVERY page directly (bypassing
    size policies and hidden-item rules), so one tall hidden tab would keep the whole dialog tall. A hidden page therefore
    reports no size at all, and the dialog is exactly as tall as the tab on screen (SettingsDialog._fit_tab)."""

    def sizeHint(self):
        return QSize(0, 0) if self.isHidden() else super().sizeHint()

    def minimumSizeHint(self):
        return QSize(0, 0) if self.isHidden() else super().minimumSizeHint()

    def heightForWidth(self, w):
        if self.isHidden():
            return -1
        return super().heightForWidth(w)


MAIL_LABELS = {"gmail": "Gmail", "outlook": "Outlook / Hotmail", "yahoo": "Yahoo Mail", "zoho": "Zoho Mail", "icloud": "iCloud Mail", "other": "Other (IMAP / SMTP)"}
MAIL_ORDER = ["gmail", "outlook", "yahoo", "zoho", "icloud", "other"]
SETTINGS_TABS = {"general": 0, "provider": 1, "voice": 2, "mail": 3}


class DiskUnlockDialog(RoundedDialog):
    """Confirmation for turning the start-up disk password OFF (Settings › General › Start-up). Says plainly what the change
    means and asks for the current passphrase (eye toggle shows it). Nothing runs until Confirm; the daemon then goes through
    the polkit root path, so the system's own password dialog may follow."""
    # U+2060 (word joiner) after the slash: the label must not wrap "/boot" into "/" + "boot"
    TEXT = ("Your files stay encrypted on the drive, but anyone who starts this computer can use it without a password, because the "
            "unlock key is stored in the start-up files on the unencrypted /⁠boot partition. Use this only where the computer itself is secure.")

    FIX_TEXT = ("Fix now stores a fresh unlock key, rebuilds the start-up files of every installed kernel, proves the key is inside and "
                "opens the disk, refreshes the start menu if needed, and checks again. If anything fails half-way it rolls back, so the "
                "computer keeps starting. The same trade-off applies: anyone who starts this computer can use it without a password.")

    def __init__(self, parent, title=None, text=None, confirm=None):
        super().__init__(parent, title or "Stop asking for the disk password?", text or self.TEXT, confirm or "Stop asking", "Cancel", width=460)
        lab = QLabel("Current disk passphrase")
        lab.setObjectName("muted")
        self.body.addWidget(lab)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("The passphrase you type when the computer starts")
        self.eye = IconButton("eye", "Show the passphrase", size=32, icon_size=18)
        self.eye.setCheckable(True)
        self.eye.toggled.connect(self._eye)
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(self.pw, 1)
        h.addWidget(self.eye, 0)
        self.body.addWidget(box)
        self.confirm_btn.setEnabled(False)
        self.pw.textChanged.connect(lambda t: self.confirm_btn.setEnabled(bool(t)))
        self.pw.returnPressed.connect(lambda: self.accept() if self.pw.text() else None)
        self.pw.setFocus()

    def _eye(self, on):
        self.pw.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self.eye.setToolTip("Hide the passphrase" if on else "Show the passphrase")
        self.eye.refresh_icon()

    def passphrase(self):
        return self.pw.text()


class DiskUnlockHowTo(RoundedDialog):
    """The 'How do I…?' page of the Start-up row: docs/HOWTO-disk-password.md (shipped as
    /usr/share/doc/fabos-agent/HOWTO-disk-password.md) rendered in a scrolling view. Read-only, one OK button."""
    PATHS = ("/usr/share/doc/fabos-agent/HOWTO-disk-password.md",
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "share", "doc", "fabos-agent", "HOWTO-disk-password.md"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "..", "..", "docs", "HOWTO-disk-password.md"))
    FALLBACK = ("# How do I stop the disk password prompt at start-up?\n\nFab AI Controls → Settings → General → **Start-up**: the switch "
                "\"Ask for the disk password when the computer starts\". Off stores an unlock key in the start-up files on the unencrypted "
                "/boot partition, so anyone who starts the computer can use it without a password; On asks again. The line under the "
                "switch shows the real start-up state; **Fix now** repairs a mismatch. The switch exists from update 1.0-7 (Fab Updates → "
                "Install). The login screen asks for your *user* password — that is a different thing.\n\n"
                "Terminal: `fabos disk-unlock status | diagnose | off | on | repair`.")

    @classmethod
    def text(cls):
        for p in cls.PATHS:
            try:
                with open(p, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                continue
        return cls.FALLBACK

    def __init__(self, parent):
        super().__init__(parent, "How do I stop the disk password prompt?", "", "OK", "Cancel", width=680)
        self.cancel_btn.hide()
        self.view = QTextBrowser()
        self.view.setObjectName("md")
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.viewport().setAutoFillBackground(False)
        self.view.document().setMarkdown(self.text())
        self.view.setMinimumHeight(420)
        self.body.addWidget(self.view)


class SettingsDialog(RoundedDialog):
    """Settings, kept compact (owner: "remove unnecessary settings things and collapse the rest"). Four tabs whose FIRST
    level holds only what most people touch — General: permission mode, System-Wide AI · AI provider: one dropdown, the
    key, Check connection · Voice: "Hey Fab" on/off, speak replies, Voice check / Test voice · Mail: provider (Gmail
    first), address, Sign in. Everything else sits in a collapsed "Advanced" expander per tab (persona, raw responses,
    step / result limits · model, endpoint, the check requirement · wake-word text, offline-only, cloud voice · sender
    name, SMTP / IMAP servers auto-filled by the preset). An API key or a mail password is saved only after a successful
    check, unless the requirement is unticked."""

    def __init__(self, parent, settings, voice=None, tab=None):
        super().__init__(parent, APP_NAME + " — Settings", "", "Save", "Cancel", radius=R_POPUP, width=620)
        s = settings
        self.s = s
        self.voice = voice
        self.worker = None
        self._workers = []
        self.saved_provider = None
        self.mail_worker = None
        self.save_worker = None            # ApiJobWorker while Save is in flight (buttons disabled, dialog keeps painting)
        self.oauth_flow = None
        self.oauth_timer = QTimer(self)
        self.oauth_timer.setInterval(1500)
        self.oauth_timer.timeout.connect(self._poll_oauth)
        self.doctor_proc = None
        secrets = s.get("secrets") or {}
        self.lay.setSpacing(10)
        tabs = QTabWidget()
        self.tabs = tabs
        self.body.addWidget(tabs)
        tabs.currentChanged.connect(self._fit_tab)     # the dialog is as tall as the CURRENT tab, not the tallest one

        def form(widget):
            f = QFormLayout(widget)
            f.setSpacing(8)
            f.setContentsMargins(0, 8, 0, 0)
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            return f

        def wrap(label):
            """A muted / result label that wraps across the whole field column and is exactly as tall as its text
            (WrapLabel — see there for why a plain word-wrapped QLabel in a QFormLayout is not)."""
            out = WrapLabel(label.text())
            if label.objectName():
                out.setObjectName(label.objectName())
            label.deleteLater()
            return out

        def row(*widgets, stretch_last=False):
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            for i, wd in enumerate(widgets):
                h.addWidget(wd, 1 if (stretch_last and i == len(widgets) - 1) else 0)
            if not stretch_last:
                h.addStretch(1)
            return box

        # --- General: permission mode + System-Wide AI; Advanced: persona, raw responses, limits
        w = TabPage()
        f = form(w)
        self.mode = QComboBox()
        for m, lab in (("ask", "Ask — approve every risky step"), ("auto", "Auto — ask only for critical steps"), ("bypass", "Bypass — never ask")):
            self.mode.addItem(lab, m)
        cur_mode = s.get("mode", "auto")
        self.mode.setCurrentIndex(["ask", "auto", "bypass"].index(cur_mode) if cur_mode in ("ask", "auto", "bypass") else 1)
        f.addRow("Permission mode", self.mode)
        self.ai_switch = Switch("Off stops the agent from taking tasks and pauses background watches")
        self.ai_switch.setChecked(str(s.get("ai.enabled", "true")) == "true")
        self.ai_note = wrap(QLabel("", objectName="muted"))
        self.ai_switch.toggled.connect(self._ai_toggled)
        self._ai_toggled(self.ai_switch.isChecked())
        f.addRow("System-Wide AI", row(self.ai_switch, self.ai_note, stretch_last=True))
        # --- Start-up: ask for the disk password (LUKS). A system setting applied at once through the polkit root path, not on
        # Save; the state comes from GET /system/disk-unlock (async — the switch is disabled until it is known).
        self.boot_prompt = Switch(self.BOOT_PROMPT_ON)
        self.boot_prompt.setChecked(True)
        self.boot_prompt.setEnabled(False)
        self.boot_note = wrap(QLabel("Checking whether this computer's disk is encrypted…", objectName="muted"))
        self.boot_state = None
        self.boot_worker = None
        self.boot_prompt.toggled.connect(self._boot_prompt_toggled)
        # under the switch: the REAL state ("Start-up asks for the disk password: yes/no — checked just now") + the How do I…? link,
        # an amber/red notice when the start-up files disagree with the switch or the last change did not complete, Fix now /
        # Check as administrator, and a Details expander with every diagnose item. All fed by GET /system/disk-unlock's "diagnosis".
        self.boot_real = wrap(QLabel("", objectName="muted"))
        self.boot_real.setTextFormat(Qt.TextFormat.RichText)
        self.boot_real.setOpenExternalLinks(False)
        self.boot_real.linkActivated.connect(self._boot_howto)
        self.boot_notice = wrap(QLabel("", objectName="checkResult"))
        self.boot_notice.setVisible(False)
        self.boot_fix = QPushButton("Fix now")
        self.boot_fix.setObjectName("primary")
        self.boot_fix.setCursor(Qt.CursorShape.PointingHandCursor)
        self.boot_fix.clicked.connect(self._boot_fix)
        self.boot_fix.setVisible(False)
        self.boot_check = QPushButton("Check as administrator")
        self.boot_check.setObjectName("ghost")
        self.boot_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.boot_check.clicked.connect(self._boot_check)
        self.boot_check.setVisible(False)
        self.boot_actions = row(self.boot_fix, self.boot_check)
        self.boot_actions.setVisible(False)
        self.boot_details = Disclosure("Details")
        self.boot_details.setVisible(False)
        # the items live in a capped scroll area, so an open Details keeps the dialog inside its 560 px height budget
        self.boot_items = QWidget()
        self.boot_items_form = QFormLayout(self.boot_items)
        self.boot_items_form.setContentsMargins(0, 0, 0, 0)
        self.boot_items_form.setSpacing(6)
        self.boot_items_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.boot_items_area = QScrollArea()
        self.boot_items_area.setWidgetResizable(True)
        self.boot_items_area.setFrameShape(QFrame.Shape.NoFrame)
        self.boot_items_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.boot_items_area.setMaximumHeight(80)
        self.boot_items_area.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.boot_items_area.viewport().setAutoFillBackground(False)
        self.boot_items_area.setWidget(self.boot_items)
        self.boot_details.addRow(self.boot_items_area)
        self.boot_last_error = None
        boot_box = QWidget()
        bv = QVBoxLayout(boot_box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(4)
        bv.addWidget(row(self.boot_prompt, self.boot_note, stretch_last=True))
        bv.addWidget(self.boot_real)
        bv.addWidget(self.boot_notice)
        bv.addWidget(self.boot_actions)
        bv.addWidget(self.boot_details)
        f.addRow("Start-up", boot_box)
        self._load_boot_prompt()
        adv = Disclosure()
        self.general_adv = adv
        self.persona = QCheckBox("Warm Indian-English colleague, narrates each step")
        self.persona.setChecked(str(s.get("ui.persona", "indian-english")).lower() not in ("off", "none", "false", "", "0"))
        adv.addRow("Persona", self.persona)
        self.show_raw = QCheckBox("Show raw responses (commands, tool output)")
        self.show_raw.setChecked(str(s.get("ui.show_raw", "false")) == "true")
        self.show_raw.toggled.connect(self._raw_toggled)
        adv.addRow("Chat", self.show_raw)
        self.max_turns = QLineEdit(str(s.get("agent.max_turns", "60")))
        self.max_turns.setMaximumWidth(96)
        adv.addRow("Max steps per task", self.max_turns)
        self.result_limit = QLineEdit(str(s.get("agent.tool_result_max_chars", "") or ""))
        self.result_limit.setPlaceholderText("provider default")
        self.result_limit.setMaximumWidth(140)
        adv.addRow("Tool result limit (chars)", self.result_limit)
        f.addRow(adv)
        tabs.addTab(w, "General")

        # --- AI provider: one dropdown, one key field, Check connection; Advanced: model, endpoint, the check requirement
        w = TabPage()
        f = form(w)
        self.provider = QComboBox()
        self.prov_ids = list(PROVIDER_ORDER)
        table = s.get("providers") or {}
        for pid in self.prov_ids:
            self.provider.addItem((table.get(pid) or {}).get("label") or PROVIDER_FULL[pid], pid)
        cur = s.get("provider", "claude")
        self.provider.setCurrentIndex(self.prov_ids.index(cur) if cur in self.prov_ids else 0)
        f.addRow("AI provider", self.provider)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setClearButtonEnabled(True)
        self.remove_btn = IconButton("delete", "Remove the stored API key", size=32, icon_size=16)
        self.remove_btn.clicked.connect(self._remove_key)
        key_row = QWidget()
        kl = QHBoxLayout(key_row)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(6)
        kl.addWidget(self.key, 1)
        kl.addWidget(self.remove_btn, 0)
        f.addRow("API key", key_row)
        self.check_btn = QPushButton("Check connection")
        self.check_btn.setObjectName("check")
        self.check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_btn.clicked.connect(self.check_connection)
        self.mark = ResultMark(28)
        self.check_result = wrap(QLabel(""))
        self.check_result.setObjectName("checkResult")
        f.addRow("", row(self.check_btn, self.mark, self.check_result, stretch_last=True))
        self.help = wrap(QLabel())
        self.help.setObjectName("muted")
        f.addRow("", self.help)
        adv = Disclosure()
        self.provider_adv = adv
        self.model = QLineEdit()
        adv.addRow("Model", self.model)
        self.base_url = QLineEdit()
        self.base_label = QLabel("Endpoint")
        adv.addRow(self.base_label, self.base_url)
        self.require_check = QCheckBox("Require a successful check before saving a key or a mail password")
        self.require_check.setChecked(True)
        self.require_check.toggled.connect(self._update_save_state)
        adv.addRow("", self.require_check)
        note = wrap(QLabel("Keys are encrypted with systemd-creds, never displayed again, and sent only to the provider you chose. The check calls the provider's model list with your key — nothing else."))
        note.setObjectName("muted")
        adv.addRow("", note)
        f.addRow(adv)
        tabs.addTab(w, "AI provider")
        # per-provider edit state (typed key, model, endpoint, last check) so switching the dropdown loses nothing
        self.state = {}
        for pid in self.prov_ids:
            m, u = PROVIDER_DEFAULTS[pid]
            self.state[pid] = {"key": "", "model": s.get(pid + ".model", m), "base_url": s.get(pid + ".base_url", u or ""), "check": None, "checked_key": None,
                               "stored": bool(secrets.get(pid + "_api_key")), "remove": False}
        self.current_pid = None
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.key.textEdited.connect(self._key_edited)
        self.model.textEdited.connect(lambda t: self._set_state("model", t))
        self.base_url.textEdited.connect(lambda t: self._set_state("base_url", t))

        # --- Voice: "Hey Fab" on/off, speak replies, Voice check + Test voice; Advanced: wake-word text, offline only, cloud voice
        w = TabPage()
        f = form(w)
        self.voice_enabled = QCheckBox("Listen for “Hey Fab” — microphone in the chat, spoken narration")
        self.voice_enabled.setChecked(str(s.get("voice.enabled", "true")) == "true")
        f.addRow("Voice", self.voice_enabled)
        self.speak_replies = QCheckBox("Speak the agent's replies and step narration aloud")
        self.speak_replies.setChecked(str(s.get("voice.speak_replies", "true")) == "true")
        f.addRow("", self.speak_replies)
        self.doctor_btn = QPushButton("Voice check")
        self.doctor_btn.setObjectName("check")
        self.doctor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.doctor_btn.setToolTip("Runs fabos-voice doctor: microphone, audio session, speech engines")
        self.doctor_btn.clicked.connect(self.run_doctor)
        self.test_voice_btn = QPushButton("Test voice")
        self.test_voice_btn.setObjectName("check")
        self.test_voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_voice_btn.clicked.connect(self.test_voice)
        self.voice_note = wrap(QLabel())
        self.voice_note.setObjectName("muted")
        f.addRow("", row(self.doctor_btn, self.test_voice_btn, self.voice_note, stretch_last=True))
        self.doctor_box = wrap(QLabel(""))
        self.doctor_box.setObjectName("raw")
        self.doctor_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.doctor_box.setVisible(False)
        f.addRow("", self.doctor_box)
        adv = Disclosure()
        self.voice_adv = adv
        self.wake_word = QLineEdit(str(s.get("voice.wake_word", "hey fab") or "hey fab"))
        adv.addRow("Wake word", self.wake_word)
        self.offline_only = QCheckBox("Offline only — never send audio to the cloud provider")
        self.offline_only.setChecked(str(s.get("voice.offline_only", "false")) == "true")
        adv.addRow("", self.offline_only)
        self.cloud_voice = QLineEdit(str(s.get("voice.cloud_voice", "") or ""))
        self.cloud_voice.setPlaceholderText("the provider's default voice")
        adv.addRow("Cloud voice name", self.cloud_voice)
        f.addRow(adv)
        self._refresh_voice_note()
        if voice is not None:
            voice.status_changed.connect(self._refresh_voice_note)
        tabs.addTab(w, "Voice")

        # --- Mail: provider (Gmail first), address, Sign in; app-password path revealed when Google sign-in is not
        # available; Advanced: sender name, SMTP / IMAP servers (auto-filled by the preset)
        w = TabPage()
        f = form(w)
        self.mail_table = s.get("mail_providers") or {}
        self.mail_ids = [p for p in (s.get("mail_provider_order") or MAIL_ORDER) if p in MAIL_LABELS or p in self.mail_table]
        self.mail_provider = QComboBox()
        for pid in self.mail_ids:
            self.mail_provider.addItem((self.mail_table.get(pid) or {}).get("label") or MAIL_LABELS.get(pid, pid), pid)
        cur = str(s.get("mail.provider", "gmail") or "gmail")
        self.mail_provider.setCurrentIndex(self.mail_ids.index(cur) if cur in self.mail_ids else 0)
        f.addRow("Mail provider", self.mail_provider)
        self.mail_address = QLineEdit(str(s.get("mail.address", "") or ""))
        self.mail_address.setPlaceholderText("you@gmail.com")
        self.mail_address.setClearButtonEnabled(True)
        f.addRow("Address", self.mail_address)
        self.mail_signin_btn = QPushButton("Sign in")
        self.mail_signin_btn.setObjectName("check")
        self.mail_signin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mail_signin_btn.clicked.connect(self.mail_signin)
        self.mail_check_btn = QPushButton("Check connection")
        self.mail_check_btn.setObjectName("check")
        self.mail_check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mail_check_btn.clicked.connect(self.mail_check)
        self.mail_check_btn.setVisible(False)
        self.mail_mark = ResultMark(28)
        self.mail_result = wrap(QLabel(""))
        self.mail_result.setObjectName("checkResult")
        self.mail_pw = QLineEdit()
        self.mail_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_pw.setClearButtonEnabled(True)
        self.mail_pw.textEdited.connect(self._mail_edited)
        self.mail_pw_label = QLabel("App password")
        f.addRow(self.mail_pw_label, self.mail_pw)
        self.mail_hint = wrap(QLabel(""))
        self.mail_hint.setObjectName("muted")
        self.mail_hint.setTextFormat(Qt.TextFormat.RichText)
        f.addRow("", self.mail_hint)
        # the action row comes AFTER the field it checks, so the tab reads Address -> App password -> hint -> Check connection
        f.addRow("", row(self.mail_signin_btn, self.mail_check_btn, self.mail_mark, self.mail_result, stretch_last=True))
        adv = Disclosure()
        self.mail_adv = adv
        self.mail_from_name = QLineEdit(str(s.get("mail.from_name", "") or ""))
        self.mail_from_name.setPlaceholderText("shown to the people you write to (optional)")
        adv.addRow("Sender name", self.mail_from_name)
        self.m = {k: QLineEdit() for k in ("mail.smtp_host", "mail.smtp_port", "mail.imap_host", "mail.imap_port")}
        for k in ("mail.smtp_port", "mail.imap_port"):
            self.m[k].setMaximumWidth(72)
        self.mail_security = QComboBox()
        for sec, lab in (("starttls", "STARTTLS"), ("ssl", "SSL / TLS"), ("none", "None")):
            self.mail_security.addItem(lab, sec)
        adv.addRow("SMTP server", row(self.m["mail.smtp_host"], self.m["mail.smtp_port"], self.mail_security))
        adv.addRow("IMAP server", row(self.m["mail.imap_host"], self.m["mail.imap_port"]))
        self.m["mail.imap_host"].setPlaceholderText("none = sending only")
        self.m["mail.imap_host"].setToolTip("The preset's IMAP server. Type none for an account that only sends — the agent will not read that inbox.")
        for k, e in self.m.items():
            e.textEdited.connect(self._mail_edited)
        self.mail_security.currentIndexChanged.connect(lambda _i: self._mail_edited(""))
        f.addRow(adv)
        tabs.addTab(w, "Mail")
        oauth = s.get("mail_oauth") or {}
        self.mail_state = {"check": None, "checked_sig": None, "oauth_done": False, "stored_pw": bool(secrets.get("mail_password")),
                           "stored_oauth": bool(secrets.get("mail_oauth_refresh")) and str(s.get("mail.auth", "")) == "oauth",
                           "oauth_available": bool(oauth.get("google")), "oauth_why": oauth.get("why") or "", "pw_shown": False, "initial": None}
        self.current_mail = None
        self._mail_fill_advanced(cur, from_store=True)
        self.mail_state["initial"] = self._mail_sig()
        self.mail_provider.currentIndexChanged.connect(self._mail_provider_changed)
        self.mail_address.textEdited.connect(self._mail_edited)
        self._mail_provider_changed(self.mail_provider.currentIndex(), initial=True)

        self.body.addWidget(QLabel(APP_NAME + " by Patience AI · fabos.patienceai.in · support@patienceai.in", objectName="muted"))   # about / author line
        # Save / Cancel as icons with tooltips (the confirm/cancel pair keeps the dialog's shape)
        self.confirm_btn.setText("")
        self.confirm_btn.setToolTip("Save settings")
        self.confirm_btn.setFixedSize(44, 40)
        self.cancel_btn.setText("")
        self.cancel_btn.setToolTip("Discard changes")
        self.cancel_btn.setFixedSize(44, 40)
        pal = self.palette()
        self.confirm_btn.setIcon(glyph_icon("check", pal.color(QPalette.ColorRole.HighlightedText), 18))
        self.cancel_btn.setIcon(glyph_icon("close", pal.color(QPalette.ColorRole.Text), 18))
        self.confirm_btn.setIconSize(QSize(18, 18))
        self.cancel_btn.setIconSize(QSize(18, 18))
        self.confirm_btn.clicked.disconnect()
        self.confirm_btn.clicked.connect(self.save)
        # why Save is blocked, inline next to the buttons (the tooltip alone is easy to miss)
        self.save_note = QLabel("", objectName="muted")
        self.save_note.setWordWrap(True)
        self.save_note.setVisible(False)
        self.buttons.insertWidget(0, self.save_note, 1)
        self._provider_changed(self.provider.currentIndex())
        if tab in SETTINGS_TABS:
            tabs.setCurrentIndex(SETTINGS_TABS[tab])
        self._fit_tab(tabs.currentIndex())
        self._update_save_state()

    def _fit_tab(self, idx):
        """Only the current tab counts for the dialog's size (a QTabWidget otherwise grows to its tallest page)."""
        for i in range(self.tabs.count()):
            pol = QSizePolicy.Policy.Preferred if i == idx else QSizePolicy.Policy.Ignored
            self.tabs.widget(i).setSizePolicy(pol, pol)
        QTimer.singleShot(0, self.refit)

    # ---- general
    def _ai_toggled(self, on):
        self.ai_note.setText("On — the agent takes tasks from the bar, the chat and voice" if on else "Off — no tasks are taken, background watches pause")

    # ---- start-up: the disk password (a LUKS keyfile in the initramfs on the unencrypted /boot; ADR-0021 layout).
    # Applied immediately through POST /system/disk-unlock (root via pkexec, CRITICAL in the activity log), never on Save.
    BOOT_PROMPT_ON = "Ask for the disk password when the computer starts"
    BOOT_PROMPT_OFF = "Starts without asking — the unlock key is stored in the start-up files on the unencrypted /boot partition"
    BOOT_NOT_ENCRYPTED = "Ask for the disk password when the computer starts — not available: this computer's disk is not encrypted, so there is no disk password to ask for."
    BOOT_BUSY_OFF = "Storing the unlock key and rebuilding the start-up files… this takes a minute or two; the system may ask for your password."
    BOOT_BUSY_ON = "Removing the unlock key and rebuilding the start-up files… this takes a minute or two; the system may ask for your password."
    BOOT_BUSY_FIX = "Fixing the start-up files… storing the key again and rebuilding every start-up file; this takes a minute or two; the system may ask for your password."
    BOOT_BUSY_CHECK = "Checking the start-up files as administrator… the system may ask for your password."
    BOOT_MISMATCH_OFF = "The switch is off but the start-up files still ask for the password"
    BOOT_MISMATCH_ON = "The switch is on but the start-up files do not ask for the password"
    BOOT_RISK = "The start-up files may not start the computer"
    BOOT_HOWTO_LINK = ' · <a href="howto" style="color: inherit;">How do I…?</a>'

    def _load_boot_prompt(self, diag_only=False):
        """GET /system/disk-unlock: the switch + note (unless diag_only: after a change, the note keeps the outcome) and the diagnosis."""
        w = ApiWorker("GET", "/system/disk-unlock", None, timeout=150)
        w.done.connect(self._boot_diag_loaded if diag_only else self._boot_prompt_loaded)
        w.finished.connect(w.deleteLater)
        self._workers.append(w)
        self.boot_worker = w
        w.start()

    # ---- the real state under the switch
    @staticmethod
    def _boot_when(iso):
        """'just now' within 90 s of the helper's checked_at (UTC ISO), else the local clock time."""
        try:
            t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
        except (TypeError, ValueError):
            return "just now"
        return "just now" if time.time() - t < 90 else "at " + time.strftime("%H:%M", time.localtime(t))

    @staticmethod
    def _esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _boot_diag_loaded(self, st):
        self.boot_worker = None
        if isinstance(st, dict) and not st.get("offline"):
            self.boot_state = st
            self._boot_show_diagnosis(st)

    def _boot_set_busy(self, busy):
        self.boot_fix.setEnabled(not busy)
        self.boot_check.setEnabled(not busy)

    def _boot_show_diagnosis(self, st):
        """The line under the switch, the notice, the buttons and the Details rows — from GET's diagnosis + last_request."""
        d = st.get("diagnosis") if isinstance(st.get("diagnosis"), dict) else {}
        lr = st.get("last_request") if isinstance(st.get("last_request"), dict) else None
        on = bool(st.get("prompt_at_boot", True))
        if not st.get("encrypted"):
            self.boot_real.setText("This computer's disk is not encrypted, so there is no disk password at start-up. A password asked at start-up is the "
                                   "<b>login screen's</b>, which is a different thing." + self.BOOT_HOWTO_LINK)
            self.boot_notice.setVisible(False)
            self.boot_actions.setVisible(False)
            self.boot_details.setVisible(False)
            QTimer.singleShot(0, self.refit)
            return
        exp = d.get("prompt_at_boot_expected")
        if d.get("boot_risk"):
            real = "the computer may NOT start"
        elif exp is True:
            real = "yes"
        elif exp is False:
            real = "no"
        elif d.get("error"):
            real = "could not be checked"
        else:
            real = "cannot tell without administrator rights"
        self.boot_real.setText("Start-up asks for the disk password: <b>%s</b> — checked %s%s%s" % (
            self._esc(real), self._boot_when(d.get("checked_at")), " as administrator" if d.get("as_root") else "", self.BOOT_HOWTO_LINK))
        lines, colour, fix, check = [], None, False, False
        reason = str(d.get("reason") or "")
        if d.get("boot_risk"):
            lines.append(self.BOOT_RISK + (": " + reason if reason else "")); colour = RED; fix = True
        elif d.get("agrees") is False:
            head = self.BOOT_MISMATCH_OFF if not on else self.BOOT_MISMATCH_ON
            # the helper's reason usually opens with the same sentence: say it once
            lines.append((reason[0].upper() + reason[1:]) if reason.lower().startswith(head.lower()[:40]) else head + (" — " + reason if reason else "")); colour = AMBER
            fix = d.get("switch") in ("on", "off")
        elif d.get("agrees") is None and d.get("needs_root"):
            lines.append("The start-up files are protected: the check needs administrator rights to be complete."); check = True
        elif d.get("agrees") is True and d.get("fails"):
            lines.append("Start-up works as set, but %s failed — see Details%s" % ("one check" if d["fails"] == 1 else "%d checks" % d["fails"], (": " + reason.split(" — but ", 1)[-1]) if " — but " in reason else "."))
            colour = AMBER; fix = d.get("switch") in ("on", "off")
        elif d.get("error"):
            lines.append(str(d["error"])); colour = AMBER
        if d.get("needs_root"):
            check = True
        # the last change asked for here that did NOT complete and is still not the state (a dismissed password dialog was easy to miss)
        err = None
        if self.boot_last_error:
            err = self.boot_last_error
        elif lr and lr.get("ok") is False and (lr.get("prompt_at_boot") is None or lr.get("prompt_at_boot") != on):
            when = time.strftime("%d %b %H:%M", time.localtime(lr["at"])) if isinstance(lr.get("at"), (int, float)) else "earlier"
            what = {"off": "turn the prompt off", "on": "turn the prompt on", "repair": "fix the start-up files"}.get(lr.get("action"), "change the setting")
            err = "The last attempt to %s (%s) did not complete: %s Nothing was changed." % (what, when, str(lr.get("error") or "unknown error").rstrip(".") + ".")
        if err:
            if "dismissed" in err or "not authorised" in err:
                err += " Enter your login password in the system dialog when it appears."
            lines.append(err); colour = RED
        self.boot_notice.setText("\n".join(lines))
        self.boot_notice.setStyleSheet("color: %s;" % colour if colour else "")
        self.boot_notice.setVisible(bool(lines))
        self.boot_fix.setVisible(bool(fix))
        self.boot_check.setVisible(bool(check))
        self.boot_actions.setVisible(bool(fix or check))
        items = d.get("items") if isinstance(d.get("items"), list) else []
        while self.boot_items_form.rowCount():
            self.boot_items_form.removeRow(0)
        marks = {"pass": "✓", "fail": "✗", "unknown": "?", "info": "·"}
        for it in items:
            if not isinstance(it, dict):
                continue
            lab = QLabel("%s %s" % (marks.get(it.get("result"), "·"), it.get("label") or it.get("id")))
            lab.setWordWrap(True)
            if it.get("result") == "fail":
                lab.setStyleSheet("color: %s;" % (RED if d.get("boot_risk") else AMBER))
            det = WrapLabel(str(it.get("detail") or ""))
            det.setObjectName("muted")
            self.boot_items_form.addRow(self._boot_item_block(lab, det))
        self.boot_details.setVisible(bool(items))
        QTimer.singleShot(0, self.refit)

    @staticmethod
    def _boot_item_block(*widgets):
        """One Details entry: the label line and the detail line at full width (a two-column form squeezed the detail)."""
        blk = QWidget()
        v = QVBoxLayout(blk)
        v.setContentsMargins(0, 0, 0, 4)
        v.setSpacing(1)
        for wd in widgets:
            v.addWidget(wd)
        return blk

    def _boot_howto(self, _href=None):
        DiskUnlockHowTo(self).exec()

    def _boot_fix(self):
        """Fix now: the passphrase when the switch is off (a fresh key slot is stored), a plain confirmation when it is on."""
        if self.boot_worker is not None:
            return
        on = bool((self.boot_state or {}).get("prompt_at_boot", True))
        body = {"action": "repair"}
        if not on:
            dlg = DiskUnlockDialog(self, "Fix the start-up files?", DiskUnlockDialog.FIX_TEXT, "Fix now")
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.passphrase():
                return
            body["passphrase"] = dlg.passphrase()
        elif not RoundedDialog.confirm(self, "Fix the start-up files?", "The start-up files still carry an unlock key although the switch is on. Fix now rebuilds them "
                                       "without the key, proves it is gone and removes the key from the disk. The system may ask for your password.", "Fix now"):
            return
        self.boot_last_error = None
        self._apply_boot_action(body, self.BOOT_BUSY_FIX, self._boot_fixed)

    def _boot_check(self):
        if self.boot_worker is not None:
            return
        self._apply_boot_action({"action": "diagnose"}, self.BOOT_BUSY_CHECK, self._boot_checked)

    def _apply_boot_action(self, body, busy_text, done):
        self.boot_prompt.setEnabled(False)
        self._boot_set_busy(True)
        self.boot_note.setText(busy_text)
        w = ApiWorker("POST", "/system/disk-unlock", body, timeout=1260)
        w.done.connect(done)
        w.finished.connect(w.deleteLater)
        self._workers.append(w)
        self.boot_worker = w
        w.start()

    def _boot_fixed(self, r):
        self.boot_worker = None
        self.boot_prompt.setEnabled(True)
        self._boot_set_busy(False)
        ok = isinstance(r, dict) and r.get("ok") is True
        steps = r.get("steps") if isinstance(r, dict) and isinstance(r.get("steps"), list) else []
        if ok:
            state = r.get("prompt_at_boot") if isinstance(r.get("prompt_at_boot"), bool) else bool((self.boot_state or {}).get("prompt_at_boot", True))
            self._set_boot_switch(state)
            self.boot_note.setText((self.BOOT_PROMPT_ON if state else self.BOOT_PROMPT_OFF) + " — fixed: " + str(r.get("detail") or "the start-up files match the setting again."))
        else:
            err = str((r.get("error") or r.get("offline") or "unknown error") if isinstance(r, dict) else r)
            self.boot_last_error = "Fix now did not complete: " + err[:300].rstrip(".") + "."
            self.boot_note.setText((self.BOOT_PROMPT_ON if self.boot_prompt.isChecked() else self.BOOT_PROMPT_OFF) + " — not fixed: " + err[:200])
        if steps:                                  # the helper's steps as they completed, in the Details expander
            while self.boot_items_form.rowCount():
                self.boot_items_form.removeRow(0)
            for s in steps:
                self.boot_items_form.addRow(self._boot_item_block(WrapLabel("→ " + str(s))))
            self.boot_details.setVisible(True)
            self.boot_details.set_open(True)
        self._load_boot_prompt(diag_only=True)

    def _boot_checked(self, r):
        self.boot_worker = None
        self.boot_prompt.setEnabled(True)
        self._boot_set_busy(False)
        st = self.boot_state or {}
        self.boot_note.setText(self.BOOT_PROMPT_ON if bool(st.get("prompt_at_boot", True)) else self.BOOT_PROMPT_OFF)
        if isinstance(r, dict) and r.get("ok") is True and isinstance(r.get("diagnosis"), dict):
            self.boot_last_error = None
            st = dict(st, diagnosis=r["diagnosis"])
            self.boot_state = st
            self._boot_show_diagnosis(st)
            return
        err = str((r.get("error") or r.get("offline") or "unknown error") if isinstance(r, dict) else r)
        self.boot_last_error = "The check as administrator did not run: " + err[:300].rstrip(".") + "."
        self._boot_show_diagnosis(st)

    def _set_boot_switch(self, on):
        """The switch follows the system state without running the toggle handler."""
        self.boot_prompt.blockSignals(True)
        self.boot_prompt.setChecked(on)
        self.boot_prompt.blockSignals(False)

    def _boot_prompt_loaded(self, st):
        self.boot_worker = None
        self.boot_state = st if isinstance(st, dict) else {}
        if not isinstance(st, dict) or st.get("offline") or (st.get("error") and not st.get("encrypted")):
            why = str((st or {}).get("error") or (st or {}).get("offline") or "the agent service did not answer") if isinstance(st, dict) else "the agent service did not answer"
            self._set_boot_switch(True)
            self.boot_prompt.setEnabled(False)
            self.boot_note.setText(self.BOOT_PROMPT_ON + " — unavailable right now: " + why[:160])
            return
        if not st.get("encrypted"):
            self._set_boot_switch(False)
            self.boot_prompt.setEnabled(False)
            self.boot_note.setText(self.BOOT_NOT_ENCRYPTED)
            self._boot_show_diagnosis(st)
            return
        on = bool(st.get("prompt_at_boot", True))
        self._set_boot_switch(on)
        self.boot_prompt.setEnabled(True)
        text = self.BOOT_PROMPT_ON if on else self.BOOT_PROMPT_OFF
        if st.get("consistent") is False and st.get("detail"):
            text += " — " + str(st["detail"])[:160]
        self.boot_note.setText(text)
        self._boot_show_diagnosis(st)

    def _boot_prompt_toggled(self, on):
        if on:                                   # asking again needs nothing
            self._apply_boot_prompt(True, None)
            return
        dlg = DiskUnlockDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.passphrase():
            self._set_boot_switch(True)
            return
        self._apply_boot_prompt(False, dlg.passphrase())

    def _apply_boot_prompt(self, on, passphrase):
        if self.boot_worker is not None:
            return
        body = {"prompt_at_boot": on}
        if not on:
            body["passphrase"] = passphrase
        self.boot_last_error = None
        # up to two update-initramfs runs on a slow disk plus the polkit dialog: a long timeout; the dialog keeps painting
        self._apply_boot_action(body, self.BOOT_BUSY_ON if on else self.BOOT_BUSY_OFF, lambda r, on=on: self._boot_prompt_applied(on, r))

    def _boot_prompt_applied(self, wanted, r):
        self.boot_worker = None
        self.boot_prompt.setEnabled(True)
        self._boot_set_busy(False)
        ok = isinstance(r, dict) and r.get("ok") is True
        if ok:
            self._set_boot_switch(wanted)
            self.boot_note.setText((self.BOOT_PROMPT_ON + " — done: the computer asks for the disk password again.") if wanted
                                   else (self.BOOT_PROMPT_OFF + " — done."))
            if self.boot_state is not None:
                self.boot_state["prompt_at_boot"] = wanted
            self._load_boot_prompt(diag_only=True)             # the real state under the switch follows
            return
        err = str((r.get("error") or r.get("offline") or "unknown error") if isinstance(r, dict) else r)
        # the helper reports the state it left behind (a rollback keeps the old one); without a verdict assume nothing changed
        state = r.get("prompt_at_boot") if isinstance(r, dict) and isinstance(r.get("prompt_at_boot"), bool) else (not wanted)
        self._set_boot_switch(state)
        self.boot_note.setText((self.BOOT_PROMPT_ON if state else self.BOOT_PROMPT_OFF) + " — not changed: " + err[:200])
        # in red under the row too, so a dismissed password dialog is not missed once the info box is gone
        self.boot_last_error = "Turning the prompt %s did not complete: %s Nothing was changed." % ("off" if not wanted else "on", err[:300].rstrip(".") + ".")
        if self.boot_state is not None:
            self._boot_show_diagnosis(self.boot_state)
        RoundedDialog.info(self, "Couldn't change the start-up setting",
                           err[:600] + ("\n\nNothing was changed: the computer still asks for the disk password." if state and not wanted else ""))

    def _raw_toggled(self, on):
        if on and str(self.s.get("ui.show_raw", "false")) != "true":
            if not RoundedDialog.confirm(self, "Show raw responses?", "Chats will show the exact commands the agent runs and their full output, including file contents and anything printed by programs. Turn this on only if you want that level of detail.", "Show raw responses"):
                self.show_raw.blockSignals(True)
                self.show_raw.setChecked(False)
                self.show_raw.blockSignals(False)

    # ---- provider section
    def _set_state(self, k, v):
        if self.current_pid:
            self.state[self.current_pid][k] = v

    def _provider_changed(self, idx):
        pid = self.provider.itemData(idx)
        if self.current_pid == pid:
            return
        self.current_pid = pid
        st = self.state[pid]
        self.key.blockSignals(True)
        self.key.setText(st["key"])
        self.key.blockSignals(False)
        self.model.setText(st["model"])
        self.base_url.setText(st["base_url"])
        local = pid in NO_KEY
        self.base_url.setVisible(local)
        self.base_label.setVisible(local)
        if local and not self.provider_adv.is_open():
            self.provider_adv.set_open(True)          # the endpoint is the one thing Local needs; show it
        if st["remove"]:
            self.key.setPlaceholderText("will be removed when you save")
        else:
            self.key.setPlaceholderText("stored — paste to replace" if st["stored"] else ("optional" if local else "Paste your API key"))
        self.remove_btn.setEnabled(st["stored"] and not st["remove"])
        self.remove_btn.setVisible(not local or st["stored"])
        table = (self.s.get("providers") or {}).get(pid) or {}
        self.help.setText(table.get("help") or PROVIDER_HELP[pid])
        self.check_btn.setText("Check connection" if not local else ("Check Ollama" if pid == "ollama" else "Check the local server"))
        self._show_check(st["check"], animate=False)
        self._update_save_state()

    def _key_edited(self, text):
        st = self.state[self.current_pid]
        st["key"] = text
        if st["checked_key"] != text:
            st["check"] = None                # a different key than the one that was checked: check again
            self._show_check(None, animate=False)
        self._update_save_state()

    def _show_check(self, res, animate=True):
        if res is None:
            self.mark.set_state("idle")
            self.check_result.setText("")
            self.check_result.setStyleSheet("")
            return
        if res.get("ok"):
            self.mark.set_state("ok", animate=animate)
            model = res.get("model") or self.model.text()
            self.check_result.setText("Connected · %s · %d ms" % (model, int(res.get("latency_ms") or 0)))
            self.check_result.setStyleSheet("color: %s;" % GREEN)
        else:
            self.mark.set_state("fail", animate=animate, color=RED if "rejected" in str(res.get("detail", "")) else AMBER)
            detail = str(res.get("detail") or "Check failed")
            text = "Key rejected" if "rejected" in detail else ("Cannot reach provider" if "cannot reach" in detail.lower() else detail[:1].upper() + detail[1:])
            if "rejected" not in detail and "cannot reach" in detail.lower() and res.get("provider") in NO_KEY:
                text = "Cannot reach Ollama on this computer" if res.get("provider") == "ollama" else "Cannot reach the local model server"
            self.check_result.setText(text)
            self.check_result.setStyleSheet("color: %s;" % (RED if "rejected" in detail else AMBER))

    def check_connection(self):
        if self.worker is not None:
            return
        pid = self.current_pid
        st = self.state[pid]
        body = {"provider": pid, "model": self.model.text().strip() or None}
        if st["key"]:
            body["api_key"] = st["key"]              # the typed key is checked before it is saved
        if pid in NO_KEY:
            body["base_url"] = self.base_url.text().strip() or None
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking…")
        self.mark.set_state("busy")
        self.check_result.setText("")
        self.check_result.setStyleSheet("")
        # the worker owns its lifetime (no parent, deleted once its thread has finished) so closing the dialog can never
        # destroy a QThread that is still running; done() below waits for a check that is still in flight
        self.worker = ApiWorker("POST", "/providers/test", body, timeout=20)
        self.worker.done.connect(lambda res, pid=pid, key=st["key"]: self._checked(pid, key, res))
        self.worker.finished.connect(self.worker.deleteLater)
        self._workers.append(self.worker)
        self.worker.start()

    def done(self, r):
        if self.save_worker is not None:      # Save in flight (buttons disabled; Escape lands here): the result decides, not the key
            return
        self.oauth_timer.stop()
        p, self.doctor_proc = self.doctor_proc, None
        _stop_process(p)
        # A connection / mail check still running (up to 20 s / 45 s against a slow provider) is aborted, not waited for:
        # abort() shuts its socket so run() returns within one round trip and emits nothing; the wait below is only Qt's
        # rule that a running QThread must not be destroyed, and it now ends in milliseconds instead of blocking the GUI
        # thread for up to 5 s while the dialog is closing.
        for w in list(self._workers):
            try:
                if w.isRunning():
                    w.abort()
                    w.wait(5000)
            except RuntimeError:          # already deleted
                pass
        self._workers = []
        super().done(r)

    def _checked(self, pid, key, res):
        self.worker = None
        self.check_btn.setEnabled(True)
        self.check_btn.setText("Check connection" if pid not in NO_KEY else ("Check Ollama" if pid == "ollama" else "Check the local server"))
        if not isinstance(res, dict):
            res = {"ok": False, "detail": "unexpected reply"}
        if res.get("offline"):
            res = {"ok": False, "detail": "Cannot reach the agent service"}
        st = self.state[pid]
        st["check"] = res
        st["checked_key"] = key
        if pid == self.current_pid:
            self._show_check(res, animate=True)
            if not res.get("ok"):
                shake(self.key if pid not in NO_KEY else self.base_url)
        self._update_save_state()

    def _blocked_reason(self):
        """Why Save is disabled right now ('' when it is allowed): the provider key, then the mail account."""
        if not self.require_check.isChecked():
            return ""
        st = self.state[self.current_pid]
        if st["check"] is not None and not st["check"].get("ok") and (st["key"] or self.current_pid in NO_KEY or not st["stored"] or st["checked_key"] == st["key"]):
            return "The last connection check failed — fix the key or endpoint and check again."
        if st["key"] and not (st["check"] and st["check"].get("ok") and st["checked_key"] == st["key"]):
            return "Check the connection with this key first (or untick the requirement under Advanced)."
        return self._mail_blocked_reason()

    def _update_save_state(self):
        if getattr(self, "save_worker", None) is not None:      # a save is in flight: nothing may re-enable the button meanwhile
            self.confirm_btn.setEnabled(False)
            return
        why = self._blocked_reason() if self.current_pid else ""
        self.confirm_btn.setEnabled(not why)
        self.confirm_btn.setToolTip(why or "Save settings")
        note = getattr(self, "save_note", None)
        if note is not None:
            note.setText(why)
            note.setVisible(bool(why))

    def _remove_key(self):
        pid = self.current_pid
        if RoundedDialog.confirm(self, "Remove the %s API key?" % PROVIDER_LABELS.get(pid, pid), "The stored key is deleted from this computer. The agent cannot use %s until you paste a new key." % PROVIDER_LABELS.get(pid, pid), "Remove key"):
            st = self.state[pid]
            st["remove"] = True
            st["key"] = ""
            st["check"] = None
            self.key.clear()
            self.key.setPlaceholderText("will be removed when you save")
            self.remove_btn.setEnabled(False)
            self._show_check(None, animate=False)
            self._update_save_state()

    # ---- voice section
    def _refresh_voice_note(self):
        v = self.voice
        if v is None or not v.bin:
            self.voice_note.setText(VOICE_UNAVAILABLE + " (fabos-voice is not installed).")
            self.test_voice_btn.setEnabled(False)
            self.test_voice_btn.setToolTip(VOICE_UNAVAILABLE)
            self.doctor_btn.setEnabled(False)
            self.doctor_btn.setToolTip(VOICE_UNAVAILABLE)
            return
        st = v.status or {}
        parts = ["speech-to-text: %s" % st.get("stt", "…"), "text-to-speech: %s" % st.get("tts", "…"), "microphone: %s" % ("yes" if st.get("mic") else "no")]
        self.voice_note.setText(" · ".join(parts))
        ok = v.tts_available()
        self.test_voice_btn.setEnabled(ok)
        self.test_voice_btn.setToolTip("Says: “%s” (fabos-voice say --test)" % VOICE_TEST_LINE if ok else VOICE_UNAVAILABLE)
        self.doctor_btn.setEnabled(True)

    def test_voice(self):
        if self.voice is not None:
            self.voice.say_test()

    def run_doctor(self):
        """fabos-voice doctor (another track ships the subcommand): its lines land in the monospace box. When the
        installed CLI has no 'doctor' yet, fabos-voice status is shown instead and the box says so."""
        if self.voice is None or not self.voice.bin or self.doctor_proc is not None:
            return
        self.doctor_btn.setEnabled(False)
        self.doctor_btn.setText("Checking…")
        self.doctor_box.setText("Running fabos-voice doctor…")
        self.doctor_box.setVisible(True)
        self._doctor_run(["doctor"])

    def _doctor_run(self, args):
        p = QProcess(self)
        p.setProgram(self.voice.bin)
        p.setArguments(args)
        p.finished.connect(lambda code, _st, p=p, args=args: self._doctor_done(p, code, args))
        p.errorOccurred.connect(lambda _e, p=p, args=args: self._doctor_done(p, 127, args) if p is self.doctor_proc else None)
        self.doctor_proc = p
        p.start()

    def _doctor_done(self, p, code, args):
        if p is not self.doctor_proc:
            return
        self.doctor_proc = None
        try:
            out = bytes(p.readAllStandardOutput()).decode(errors="replace").strip()
            err = bytes(p.readAllStandardError()).decode(errors="replace").strip()
        except RuntimeError:
            return
        if args == ["doctor"] and (code == 2 and ("invalid choice" in err or "unrecognized" in err)):
            # this fabos-voice has no doctor yet: fall back to its status line
            self.doctor_box.setText("fabos-voice doctor is not available in this build — showing fabos-voice status instead…")
            self._doctor_run(["status"])
            return
        lines = [ln for ln in (out + ("\n" + err if err else "")).splitlines() if ln.strip()]
        if args == ["status"]:
            lines = ["(no 'doctor' in this fabos-voice; status only)"] + lines
        if code == 127:
            lines = ["fabos-voice could not be started (%s)" % (self.voice.bin or "not installed")] + lines
        self.doctor_box.setText("\n".join(lines[-14:]) or "fabos-voice printed nothing (exit %s)" % code)
        self.doctor_btn.setEnabled(True)
        self.doctor_btn.setText("Voice check")
        QTimer.singleShot(0, self.refit)

    # ---- mail section
    def _mail_pid(self):
        return self.mail_provider.currentData()

    def _mail_preset(self, pid):
        pre = dict(self.mail_table.get(pid) or {})
        return pre

    def _mail_fill_advanced(self, pid, from_store=False):
        """Advanced server fields follow the preset; stored overrides (set only when they differ from the preset) win."""
        pre = self._mail_preset(pid)
        vals = {"mail.smtp_host": pre.get("smtp_host", ""), "mail.smtp_port": pre.get("smtp_port", 587), "mail.imap_host": pre.get("imap_host", ""),
                "mail.imap_port": pre.get("imap_port", 993)}
        sec = pre.get("smtp_security", "starttls")
        if from_store:
            for k in vals:
                if self.s.get(k):
                    vals[k] = self.s.get(k)
            if self.s.get("mail.smtp_security"):
                sec = self.s.get("mail.smtp_security")
        for k, v in vals.items():
            self.m[k].setText(str(v or ""))
        idx = [self.mail_security.itemData(i) for i in range(self.mail_security.count())]
        self.mail_security.setCurrentIndex(idx.index(sec) if sec in idx else 0)

    def _mail_hint_text(self, pid):
        pre = self._mail_preset(pid)
        steps = pre.get("hint") or []
        if pre.get("app_password"):
            head = "%s wants an <b>app password</b>, not your account password:" % (pre.get("label") or MAIL_LABELS.get(pid, pid))
        elif pid == "other":
            head = "Any IMAP / SMTP account:"
        else:
            head = "Your normal %s password works unless two-factor authentication is on:" % (pre.get("label") or MAIL_LABELS.get(pid, pid))
        text = head + "<br>" + "<br>".join("%d. %s" % (i + 1, st) for i, st in enumerate(steps))
        if pre.get("note"):
            text += "<br><i>%s</i>" % pre["note"]
        return text

    def _mail_provider_changed(self, idx, initial=False):
        pid = self.mail_provider.itemData(idx)
        if pid == self.current_mail:
            return
        self.current_mail = pid
        if not initial:
            self._mail_fill_advanced(pid)
        pre = self._mail_preset(pid)
        google = pid == "gmail" and self.mail_state["oauth_available"]
        self.mail_signin_btn.setText("Sign in with Google" if google else "Sign in")
        self.mail_pw_label.setText("App password" if pre.get("app_password") else "Password")
        self.mail_address.setPlaceholderText({"gmail": "you@gmail.com", "outlook": "you@outlook.com", "yahoo": "you@yahoo.com", "zoho": "you@zohomail.com",
                                              "icloud": "you@icloud.com"}.get(pid, "you@example.com"))
        self.mail_hint.setText(self._mail_hint_text(pid))
        signed_in = initial and self.mail_state["stored_oauth"] and pid == "gmail"
        if signed_in:
            self.mail_mark.set_state("ok", animate=False)
            self.mail_result.setText("Signed in with Google as %s" % (self.mail_address.text() or "your account"))
            self.mail_result.setStyleSheet("color: %s;" % GREEN)
            self.mail_signin_btn.setText("Sign in again")
        elif not initial or self.mail_state["check"] is None:
            self._mail_show_check(None)
        # the app-password path is visible when Google sign-in is not possible for this provider, when a password is
        # stored, or once the user pressed Sign in without OAuth (pw_shown)
        show_pw = (not google and (self.mail_state["stored_pw"] or self.mail_state["pw_shown"] or not initial)) or (google and self.mail_state["pw_shown"])
        if pid == "other" and not initial:
            show_pw = True
        self._mail_set_pw_visible(show_pw)
        self.mail_pw.setPlaceholderText("stored — paste to replace" if self.mail_state["stored_pw"] else ("16-character app password" if pre.get("app_password") else "your mail password"))
        if not initial:
            self._mail_edited("")
        self._update_save_state()

    def _mail_set_pw_visible(self, on):
        on = bool(on)
        for wd in (self.mail_pw, self.mail_pw_label):
            wd.setVisible(on)
        self.mail_hint.setVisible(on and not self._mail_check_ok())
        self.mail_check_btn.setVisible(on)
        self.mail_signin_btn.setVisible(not on or (self._mail_pid() == "gmail" and self.mail_state["oauth_available"]))
        if on:
            self.mail_state["pw_shown"] = True
        QTimer.singleShot(0, self.refit)

    def _mail_sig(self):
        return (self._mail_pid(), self.mail_address.text().strip().lower(), self.mail_pw.text(), self.m["mail.smtp_host"].text().strip(), self.m["mail.smtp_port"].text().strip(),
                self.mail_security.currentData(), self.m["mail.imap_host"].text().strip(), self.m["mail.imap_port"].text().strip())

    def _mail_edited(self, _text):
        if self.mail_state["checked_sig"] != self._mail_sig():
            self.mail_state["check"] = None
            self._mail_show_check(None)
        self._update_save_state()

    def _mail_check_ok(self):
        st = getattr(self, "mail_state", None) or {}
        return bool(st.get("check") and st["check"].get("ok") and st.get("checked_sig") == self._mail_sig())

    def _mail_show_check(self, res, animate=True):
        # the app-password hint is for BEFORE the check: hidden once the sign-in passed, back when the form changes again
        self.mail_hint.setVisible(self.mail_pw.isVisible() and not (res and res.get("ok")))
        QTimer.singleShot(0, self.refit)
        self.mail_result.setToolTip("")
        if res is None:
            self.mail_mark.set_state("idle")
            self.mail_result.setText("")
            self.mail_result.setStyleSheet("")
            return
        if res.get("ok"):
            self.mail_mark.set_state("ok", animate=animate)
            imap = res.get("imap") or {}
            text = "Signed in · SMTP ✓ · %s · %d ms" % ("IMAP ✓" if imap.get("ok") else "IMAP off", int(res.get("latency_ms") or 0))
            if not imap.get("ok") and imap.get("detail"):
                text += "\n" + str(imap["detail"])          # e.g. Outlook: password sign-in for IMAP switched off — sending works
            self.mail_result.setText(text)
            self.mail_result.setStyleSheet("color: %s;" % GREEN)
        else:
            detail = str(res.get("detail") or "Check failed")
            wrong = detail.lower().startswith("wrong password")
            self.mail_mark.set_state("fail", animate=animate, color=RED if wrong else AMBER)
            self.mail_result.setText(detail[:1].upper() + detail[1:])
            self.mail_result.setStyleSheet("color: %s;" % (RED if wrong else AMBER))

    def _mail_blocked_reason(self):
        st = self.mail_state
        sig = self._mail_sig()
        if st["oauth_done"] and not sig[2]:
            return ""
        if sig == st["initial"]:
            return ""
        if st["check"] is not None and st["checked_sig"] == sig:
            return "" if st["check"].get("ok") else "The last mail check failed — fix the address or password and check again."
        if not sig[1]:
            return "Enter the mail address, then Sign in or Check connection."
        return "Check the mail connection first (Settings → Mail), or untick the requirement under AI provider → Advanced."

    def mail_signin(self):
        pid = self._mail_pid()
        if pid == "gmail" and self.mail_state["oauth_available"]:
            if self.mail_worker is not None:
                return
            self.mail_signin_btn.setEnabled(False)
            self.mail_signin_btn.setText("Opening the browser…")
            self.mail_mark.set_state("busy")
            self.mail_result.setText("")
            self.mail_result.setStyleSheet("")
            self.mail_worker = ApiWorker("POST", "/mail/oauth/start", {"provider": "gmail"}, timeout=20)
            self.mail_worker.done.connect(self._oauth_started)
            self.mail_worker.finished.connect(self.mail_worker.deleteLater)
            self._workers.append(self.mail_worker)
            self.mail_worker.start()
            return
        # no Google sign-in for this provider (or this build): the app-password path, with the reason when it applies
        # (short in the label — it shares its row with Check connection; the daemon's full sentence is the tooltip)
        self._mail_set_pw_visible(True)
        if pid == "gmail" and self.mail_state["oauth_why"]:
            self.mail_result.setText("No Google sign-in on this build — use an app password instead.")
            self.mail_result.setToolTip(self.mail_state["oauth_why"])
            self.mail_result.setStyleSheet("")
        self.mail_pw.setFocus()

    def _oauth_started(self, res):
        self.mail_worker = None
        self.mail_signin_btn.setEnabled(True)
        self.mail_signin_btn.setText("Sign in with Google")
        if not isinstance(res, dict) or res.get("offline"):
            self._mail_show_check({"ok": False, "detail": "Cannot reach the agent service"})
            shake(self.mail_signin_btn)
            return
        if not res.get("ok"):
            self.mail_state["oauth_available"] = False
            self.mail_state["oauth_why"] = res.get("detail") or ""
            self.mail_signin_btn.setText("Sign in")
            self.mail_mark.set_state("idle")
            self.mail_signin()
            return
        self.oauth_flow = res
        self.mail_result.setText("Finish signing in in the browser window%s" % ("" if res.get("browser_opened", True) else " — it could not be opened; the address is in the log"))
        self.mail_result.setStyleSheet("")
        self.oauth_timer.start()

    def _poll_oauth(self):
        if not self.oauth_flow or self.mail_worker is not None:
            return
        self.mail_worker = ApiWorker("GET", "/mail/oauth/status?flow_id=" + str(self.oauth_flow.get("flow_id")), None, timeout=10)
        self.mail_worker.done.connect(self._oauth_polled)
        self.mail_worker.finished.connect(self.mail_worker.deleteLater)
        self._workers.append(self.mail_worker)
        self.mail_worker.start()

    def _oauth_polled(self, res):
        self.mail_worker = None
        if not isinstance(res, dict) or res.get("state") == "pending":
            return
        self.oauth_timer.stop()
        self.oauth_flow = None
        if res.get("state") == "done":
            self.mail_state["oauth_done"] = True
            self.mail_state["stored_oauth"] = True
            if res.get("address"):
                self.mail_address.setText(res["address"])
            self.mail_pw.clear()
            self._mail_set_pw_visible(False)
            self.mail_mark.set_state("ok", animate=True)
            self.mail_result.setText("Signed in with Google as %s" % (res.get("address") or "your account"))
            self.mail_result.setStyleSheet("color: %s;" % GREEN)
            self.mail_signin_btn.setText("Sign in again")
        else:
            self._mail_show_check({"ok": False, "detail": res.get("detail") or res.get("error") or "Sign-in did not finish"})
            shake(self.mail_signin_btn)
        self._update_save_state()

    def mail_check(self):
        if self.mail_worker is not None:
            return
        sig = self._mail_sig()
        body = {"provider": sig[0], "address": self.mail_address.text().strip(), "mail.smtp_host": sig[3], "mail.smtp_port": sig[4], "mail.smtp_security": sig[5],
                "mail.imap_host": sig[6], "mail.imap_port": sig[7], "mail.from_name": self.mail_from_name.text().strip()}
        if self.mail_pw.text():
            body["password"] = self.mail_pw.text()          # the typed password is checked before it is saved
        self.mail_check_btn.setEnabled(False)
        self.mail_check_btn.setText("Checking…")
        self.mail_mark.set_state("busy")
        self.mail_result.setText("")
        self.mail_result.setStyleSheet("")
        self.mail_worker = ApiWorker("POST", "/mail/test", body, timeout=45)
        self.mail_worker.done.connect(lambda res, sig=sig: self._mail_checked(sig, res))
        self.mail_worker.finished.connect(self.mail_worker.deleteLater)
        self._workers.append(self.mail_worker)
        self.mail_worker.start()

    def _mail_checked(self, sig, res):
        self.mail_worker = None
        self.mail_check_btn.setEnabled(True)
        self.mail_check_btn.setText("Check connection")
        if not isinstance(res, dict):
            res = {"ok": False, "detail": "unexpected reply"}
        if res.get("offline"):
            res = {"ok": False, "detail": "Cannot reach the agent service"}
        self.mail_state["check"] = res
        self.mail_state["checked_sig"] = sig
        if sig == self._mail_sig():
            self._mail_show_check(res, animate=True)
            if not res.get("ok"):
                detail = str(res.get("detail") or "").lower()
                shake(self.mail_pw if "password" in detail else self.mail_address)
        self._update_save_state()

    # ---- save
    def save(self):
        if self._blocked_reason():
            self._update_save_state()
            shake(self.key if self._blocked_reason() != self._mail_blocked_reason() else (self.mail_pw if self.mail_pw.isVisible() else self.mail_address))
            return
        mode = self.mode.currentData()
        if mode == "bypass" and self.s.get("mode", "auto") != "bypass":
            if not RoundedDialog.confirm(self, "Switch to Bypass mode?", "In Bypass the agent never asks before acting — including administrator commands, deleting files and sending mail. Use it only for tasks you fully trust.", "Use Bypass"):
                return
        pid = self.current_pid
        body = {"mode": mode, "agent.max_turns": self.max_turns.text().strip() or "60", "agent.tool_result_max_chars": self.result_limit.text().strip(), "provider": pid,
                "ai.enabled": "true" if self.ai_switch.isChecked() else "false",
                "ui.show_raw": "true" if self.show_raw.isChecked() else "false", "ui.persona": "indian-english" if self.persona.isChecked() else "off",
                "voice.enabled": "true" if self.voice_enabled.isChecked() else "false", "voice.wake_word": self.wake_word.text().strip() or "hey fab",
                "voice.speak_replies": "true" if self.speak_replies.isChecked() else "false", "voice.offline_only": "true" if self.offline_only.isChecked() else "false",
                "voice.cloud_voice": self.cloud_voice.text().strip()}
        for p, st in self.state.items():
            body[p + ".model"] = st["model"].strip() or PROVIDER_DEFAULTS[p][0]
            if PROVIDER_DEFAULTS[p][1]:
                body[p + ".base_url"] = st["base_url"].strip() or PROVIDER_DEFAULTS[p][1]
        mpid = self._mail_pid()
        pre = self._mail_preset(mpid)
        body["mail.provider"] = mpid
        body["mail.address"] = self.mail_address.text().strip()
        body["mail.from_name"] = self.mail_from_name.text().strip()
        # Advanced server fields are stored only when they differ from the preset, so a later preset change follows through
        for k, pk in (("mail.smtp_host", "smtp_host"), ("mail.smtp_port", "smtp_port"), ("mail.imap_host", "imap_host"), ("mail.imap_port", "imap_port")):
            v = self.m[k].text().strip()
            body[k] = "" if (mpid != "other" and v == str(pre.get(pk, ""))) else v
        sec = self.mail_security.currentData()
        body["mail.smtp_security"] = "" if (mpid != "other" and sec == pre.get("smtp_security")) else sec
        calls = [("PUT", "/settings", body)]
        for p, st in self.state.items():
            if st["remove"]:
                calls.append(("POST", "/secrets", {"name": p + "_api_key", "value": ""}))
            elif st["key"]:
                calls.append(("POST", "/secrets", {"name": p + "_api_key", "value": st["key"]}))
        if self.mail_pw.text():
            calls.append(("POST", "/secrets", {"name": "mail_password", "value": self.mail_pw.text()}))
        # Off the GUI thread, like the connection checks: PUT first, the secrets only after it succeeded (gate_first). The
        # dialog keeps painting while a slow daemon answers; both buttons are disabled until _saved() runs.
        if self.save_worker is not None:
            return
        self.save_worker = ApiJobWorker(calls)
        self.save_worker.done.connect(lambda rs, pid=pid: self._saved(pid, rs))
        self.save_worker.finished.connect(self.save_worker.deleteLater)
        self._workers.append(self.save_worker)
        self.confirm_btn.setText("Saving…")
        self.cancel_btn.setEnabled(False)
        self._update_save_state()
        self.save_worker.start()

    def _saved(self, pid, results):
        """Save's results, on the GUI thread. Offline or a rejected PUT keeps the dialog open (nothing was saved and the
        edits are still in the fields); otherwise the dialog closes as before."""
        self.save_worker = None
        self.confirm_btn.setText("Save")
        self.cancel_btn.setEnabled(True)
        self._update_save_state()
        off = next((r for r in results if isinstance(r, dict) and "offline" in r), None)
        if off is not None:
            RoundedDialog.info(self, "Agent service offline", "Your changes were not saved. Start the service with:  systemctl --user start fabos-agent   and press Save again. (%s)" % off["offline"][:120])
            return
        r = results[0] if results else None
        if not isinstance(r, dict) or r.get("error"):
            RoundedDialog.info(self, "Couldn't save the settings", str(r.get("error", "unknown error") if isinstance(r, dict) else r))
            return
        self.saved_provider = pid
        self.accept()


# ----------------------------------------------------------------------------- main window
EMPTY_COLUMNS = [
    ("bulb", "Try asking", ["Open Fab Files in Downloads", "Write a hi note in Fab Editor and mail it to a friend", "Open Firefox on fabos.patienceai.in"]),
    ("bolt", "What I can do", ["Open and drive apps, type into them", "Read, write and organise your files, run commands", "Send and check mail, fetch the web, keep a watch"]),
    ("shield", "Keep in mind", ["Every step is scored LOW to CRITICAL", "Ask · Auto · Bypass decide when I ask you first", "Nothing leaves this computer except your requests to the AI provider you chose"]),
]


class AIControls(QMainWindow):
    def __init__(self, focus_ask=False, prefill="", task_id=None):
        super().__init__()
        self._restyle = QTimer(self)          # coalesces palette events into one deferred apply_style (see event())
        self._restyle.setSingleShot(True)
        self._restyle.setInterval(0)
        self._restyle.timeout.connect(self.apply_style)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon.fromTheme(DESKTOP_ID))
        self.resize(1180, 760)
        self.setMinimumSize(720, 480)
        self.tasks, self.by_id, self.convs = [], {}, {}
        self.current_root = None
        self.details = {}               # task id -> full task dict
        self.show_raw = False
        self.status = {}
        self.offline = False
        self.approval_dialogs = {}
        self.editing = None             # task id whose request is being edited (inline edit card)
        self.enlarged = False
        self._was_maximized = False
        self.pending_task = task_id     # --task ID: open the app on that conversation once the list is loaded
        self.speaking_message = None
        self._closing = False
        self.api_q = ApiQueue()         # every daemon call of this window runs there (never on the GUI thread)
        self.api_q.done.connect(self._on_api_done)
        self.voice = Voice(self)
        self.voice.status_changed.connect(self.update_voice_buttons)
        self.voice.transcript.connect(self.on_transcript)
        self.voice.nothing_heard.connect(lambda reason: self.toast.show_message(reason or "I did not catch that.", 4200))
        self.voice.unavailable.connect(self.on_voice_unavailable)
        self.voice.listening_changed.connect(self.on_listening)
        self.voice.speaking_changed.connect(self.on_speaking)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # ---- body: sidebar + main column
        body = QHBoxLayout()
        body.setSpacing(0)
        body.setContentsMargins(0, 0, 0, 0)
        self.sidebar = Sidebar()
        self.sidebar.selected.connect(self.select_conversation)
        self.sidebar.new_chat.connect(self.new_chat)
        self.sidebar.delete_requested.connect(self.delete_conversation)
        self.sidebar.clear_all.connect(self.clear_conversations)
        self.sidebar.open_settings.connect(self.open_settings)
        self.sidebar.open_about.connect(self.about)
        self.sidebar.open_appearance.connect(self.open_appearance)
        body.addWidget(self.sidebar)
        self.main = QFrame()
        self.main.setObjectName("card")
        ml = QVBoxLayout(self.main)
        ml.setContentsMargins(SP2, 8, SP2, SP2)
        ml.setSpacing(SP)
        # ---- header (inside the main column: title + status, provider chip, enlarge, settings, System-Wide AI, report)
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(6, 4, 0, 4)
        hl.setSpacing(10)
        self.app_icon = QLabel()
        self.app_icon.setFixedSize(28, 28)
        hl.addWidget(self.app_icon)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        self.title = QLabel(APP_NAME)
        self.title.setObjectName("title")
        self.subtitle = QLabel("Connecting to the agent…")
        self.subtitle.setObjectName("subtitle")
        tcol.addWidget(self.title)
        tcol.addWidget(self.subtitle)
        hl.addLayout(tcol)
        hl.addStretch(1)
        self.provider_chip = QToolButton()
        self.provider_chip.setObjectName("providerChip")
        self.provider_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.provider_chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.provider_chip.setIconSize(QSize(10, 10))
        self.provider_chip.setText("…")
        self.provider_chip.setToolTip("Active AI provider — click to change")
        self.provider_chip.clicked.connect(self.open_settings)
        hl.addWidget(self.provider_chip)
        self.enlarge_btn = IconButton("expand", "Enlarge chat (hide the sidebar, maximise)", size=36)
        self.enlarge_btn.setCheckable(True)
        self.enlarge_btn.toggled.connect(self.toggle_enlarge)
        hl.addWidget(self.enlarge_btn)
        self.settings_btn = IconButton("settings", "Settings", size=36)
        self.settings_btn.clicked.connect(self.open_settings)
        hl.addWidget(self.settings_btn)
        sw = QHBoxLayout()
        sw.setSpacing(8)
        sw.setContentsMargins(8, 0, 4, 0)
        self.ai_label = QLabel("System-Wide AI")
        self.ai_label.setObjectName("muted")
        self.ai_switch = Switch("System-Wide AI — off stops the agent from taking tasks and pauses background watches")
        self.ai_switch.clicked.connect(self.toggle_ai)
        sw.addWidget(self.ai_label)
        sw.addWidget(self.ai_switch)
        hl.addLayout(sw)
        self.report_btn = IconButton("flag", "Report a problem", size=36)
        self.report_btn.clicked.connect(self.report_problem)
        hl.addWidget(self.report_btn)
        ml.addWidget(header)
        self.offline_label = QLabel()
        self.offline_label.setObjectName("offline")
        self.offline_label.setWordWrap(True)
        self.offline_label.hide()
        ml.addWidget(self.offline_label)
        self.stack = QStackedWidget()
        # ---- empty state: Fab AI mark + name + three columns of small cards
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(SP3, SP3, SP3, SP3)
        el.addStretch(3)
        mark_row = QHBoxLayout()
        mark_row.setSpacing(14)
        mark_row.addStretch(1)
        self.greeting_icon = QLabel()
        self.greeting_icon.setFixedSize(44, 44)
        self.greeting_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark_row.addWidget(self.greeting_icon)
        g = QLabel(APP_NAME)
        g.setObjectName("emptyTitle")
        mark_row.addWidget(g)
        mark_row.addStretch(1)
        el.addLayout(mark_row)
        el.addSpacing(40)
        cols = QHBoxLayout()
        cols.setSpacing(40)
        cols.addStretch(1)
        self.column_icons = []
        for glyph, title, cards in EMPTY_COLUMNS:
            col = QVBoxLayout()
            col.setSpacing(12)
            ic = QLabel()
            ic.setFixedSize(24, 24)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.column_icons.append((ic, glyph))
            col.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
            t = QLabel(title)
            t.setObjectName("colTitle")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(t)
            col.addSpacing(8)
            for text in cards:
                if title == "Try asking":
                    c = QPushButton(text)
                    c.setObjectName("emptyCard")
                    c.setCursor(Qt.CursorShape.PointingHandCursor)
                    c.clicked.connect(lambda _c=False, t=text: self.prefill(t))
                else:
                    c = QLabel(text)
                    c.setObjectName("emptyCard")
                    c.setWordWrap(True)
                c.setFixedWidth(276)
                c.setMinimumHeight(48)
                col.addWidget(c)
            col.addStretch(1)
            cols.addLayout(col)
        cols.addStretch(1)
        el.addLayout(cols)
        el.addStretch(4)
        self.stack.addWidget(empty)
        self.view = ConversationView()
        self.view.edit_requested.connect(self.start_edit)
        self.view.edit_submitted.connect(self.submit_edit)
        self.view.edit_cancelled.connect(self._edit_cancelled)
        self.view.retry_requested.connect(self.retry_task)
        self.view.stop_requested.connect(self.stop_task)
        self.view.speak_requested.connect(self.speak)
        self.view.feedback.connect(self.send_feedback)
        self.view.regenerate_requested.connect(self.regenerate_image)
        self.stack.addWidget(self.view)
        ml.addWidget(self.stack, 1)
        # ---- cloud hint chip (built-in model only): above the composer, dismissible for the session
        self._cloud_hint_dismissed = False
        self.cloud_hint = QFrame()
        self.cloud_hint.setObjectName("cloudHint")
        self.cloud_hint.hide()
        chl = QHBoxLayout(self.cloud_hint)
        chl.setContentsMargins(12, 5, 6, 5)
        chl.setSpacing(8)
        self.cloud_hint_icon = QLabel()
        self.cloud_hint_icon.setFixedSize(16, 16)
        self.cloud_hint_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chl.addWidget(self.cloud_hint_icon)
        self.cloud_hint_label = QLabel("Using the built-in model. For the best results use a cloud model")
        self.cloud_hint_label.setObjectName("hintText")
        chl.addWidget(self.cloud_hint_label, 1)
        self.cloud_hint_choose = QPushButton("Choose")
        self.cloud_hint_choose.setObjectName("linkBtn")
        self.cloud_hint_choose.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cloud_hint_choose.setToolTip("Pick a cloud model in Settings › AI provider")
        self.cloud_hint_choose.clicked.connect(lambda: self.open_settings("provider"))
        chl.addWidget(self.cloud_hint_choose, 0)
        self.cloud_hint_close = IconButton("close", "Dismiss for now", size=26, icon_size=14)
        self.cloud_hint_close.clicked.connect(self.dismiss_cloud_hint)
        chl.addWidget(self.cloud_hint_close, 0)
        ml.addWidget(self.cloud_hint)
        # ---- composer: two-row rounded card
        self.composer = QFrame()
        self.composer.setObjectName("composer")
        self.composer.setProperty("focused", False)
        cv = QVBoxLayout(self.composer)
        cv.setContentsMargins(SP3, 14, SP2, 12)
        cv.setSpacing(8)
        self.ask = GrowingTextEdit("ask")
        self.ask.setPlaceholderText("Ask me to do anything…")
        self.ask.submitted.connect(lambda: self.submit(source="enter"))
        self.ask.escaped.connect(self.cancel_edit)
        self.ask.focus_changed.connect(self._ask_focus)
        cv.addWidget(self.ask)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.mode_btn = QToolButton()
        self.mode_btn.setObjectName("modeChip")
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.mode_btn.setIconSize(QSize(16, 16))
        self.mode_btn.setText("Auto")
        self.mode_btn.setToolTip("Permission mode")
        self.mode_menu = QMenu(self)
        self.mode_menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.mode_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.mode_actions = {}
        for m, lab in (("ask", "Ask — approve every risky step"), ("auto", "Auto — ask only for critical steps"), ("bypass", "Bypass — never ask")):
            act = QAction(lab, self)
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, m=m: self.set_mode(m))
            self.mode_menu.addAction(act)
            self.mode_actions[m] = act
        self.mode_btn.clicked.connect(lambda: self.mode_menu.exec(self.mode_btn.mapToGlobal(self.mode_btn.rect().topLeft()) - QPointF(0, self.mode_menu.sizeHint().height() + 6).toPoint()))
        row2.addWidget(self.mode_btn, 0)
        self.hint_label = QLabel("Enter to send · Shift+Enter for a new line")
        self.hint_label.setObjectName("chipText")
        row2.addWidget(self.hint_label, 0)
        row2.addStretch(1)
        self.mic_btn = MicButton()
        self.mic_btn.clicked.connect(self.toggle_listen)
        row2.addWidget(self.mic_btn, 0)
        self.send_btn = IconButton("send", "Send", size=36, accent=True, icon_size=18)
        self.send_btn.clicked.connect(self.submit)
        self._send_tip = "Send"
        row2.addWidget(self.send_btn, 0)
        cv.addLayout(row2)
        ml.addWidget(self.composer)
        self.ask.textChanged.connect(self.update_send_state)   # Send follows the text: disabled (40 %) while the box is empty or whitespace
        body.addWidget(self.main, 1)
        outer.addLayout(body, 1)
        self.toast = Toast(self.main)
        # timers (intervals follow the window's visibility: _retune_polls)
        self.list_timer = QTimer(self)
        self.list_timer.timeout.connect(self.refresh_list)
        self.list_timer.start(POLL_LIST_MS)
        self.thread_timer = QTimer(self)
        self.thread_timer.timeout.connect(self.refresh_thread)
        self.thread_timer.start(POLL_THREAD_MS)
        self.apply_style()
        self.update_voice_buttons()
        self.refresh_list()
        self.update_composer()
        if prefill:
            self.prefill(prefill)
        if focus_ask or prefill:
            self.ask.setFocus()

    # ---- look
    def apply_style(self):
        """Derive the stylesheet from the *application* palette (the system colour scheme). Guarded by a palette signature:
        setStyleSheet itself raises PaletteChange on every widget, so reacting to those would loop forever."""
        pal = QApplication.instance().palette()
        key = tuple(pal.color(r).name(QColor.NameFormat.HexArgb) for r in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase,
                                                                            QPalette.ColorRole.Text, QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText))
        if key == getattr(self, "_style_key", None):
            return
        self._style_key = key
        QApplication.instance().setStyleSheet(build_style(pal))
        hi = pal.color(QPalette.ColorRole.Highlight)
        self.app_icon.setPixmap(glyph_pixmap("sparkle", hi, 26))
        self.greeting_icon.setPixmap(glyph_pixmap("sparkle", hi, 40))
        text = QColor(pal.color(QPalette.ColorRole.Text))
        text.setAlphaF(0.85)
        for lab, glyph in self.column_icons:
            lab.setPixmap(glyph_pixmap(glyph, text, 24))
        self.mode_btn.setIcon(glyph_icon("shield", text, 16))
        self.cloud_hint_icon.setPixmap(glyph_pixmap("info", hi, 16))
        for b in self.findChildren(IconButton):
            b.refresh_icon()
        self.sidebar.refresh_icons()
        self.update_header()

    def event(self, e):
        # The system colour scheme changed (dark <-> light): Qt delivers ApplicationPaletteChange to event() of each
        # top-level widget — it never reaches changeEvent() — so the palette-derived stylesheet is rebuilt from here.
        if e.type() == QEvent.Type.ApplicationPaletteChange:
            self._restyle.start()
        return super().event(e)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.PaletteChange:      # also follows an app palette change; apply_style's signature guard keeps it idempotent
            self._restyle.start()
        elif e.type() == QEvent.Type.WindowStateChange:
            self._retune_polls()
        super().changeEvent(e)

    def closeEvent(self, e):
        """Closing the window ends the polling, the worker and any voice process; nothing keeps running behind a closed window.
        The worker is waited for because Qt must never destroy a running QThread: stop() shuts the in-flight socket and no
        further call of a job starts, so at most one connect/read (bounded by API_TIMEOUT) can remain — the wait below is
        longer than that."""
        self._closing = True
        for t in (self.list_timer, self.thread_timer, self._restyle):
            t.stop()
        self.voice.shutdown()
        self.api_q.stop()
        self.api_q.wait((API_TIMEOUT + 3) * 1000)
        super().closeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self._retune_polls()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._retune_polls()

    def _visible_to_user(self):
        return self.isVisible() and not bool(self.windowState() & Qt.WindowState.WindowMinimized)

    def _retune_polls(self):
        """A chat nobody is looking at (window hidden or minimised) is polled at 6 s and the list at 12 s; visible again ->
        back to 1.5 s / 4 s and one refresh at once. (Wayland gives no reliable 'fully covered' signal, so covered windows
        count as visible.)"""
        vis = self._visible_to_user()
        thread_ms, list_ms = (POLL_THREAD_MS, POLL_LIST_MS) if vis else (POLL_THREAD_HIDDEN_MS, POLL_LIST_HIDDEN_MS)
        if self.thread_timer.interval() != thread_ms:
            self.thread_timer.setInterval(thread_ms)
            self.list_timer.setInterval(list_ms)
            if vis and not self._closing:
                self.refresh_list()
                self.refresh_thread()

    # ---- daemon calls: never on the GUI thread
    def api_async(self, method, path, body=None, cb=None, key=None, err=None):
        """One call on the worker; cb(result) runs on the GUI thread when it is back (not when the daemon was unreachable:
        then set_offline() is shown and err(), if given, runs instead)."""
        return self.api_batch([(method, path, body)], (lambda rs: cb(rs[0])) if cb else None, key, err)

    def api_batch(self, calls, cb=None, key=None, err=None):
        """Several calls as ONE job (one round trip of the worker, results together); key coalesces repeated polls."""
        if self._closing:
            return None
        return self.api_q.submit(calls, cb, key, err)

    def _on_api_done(self, job, results):
        self.api_q.inflight -= 1
        if results is None or self._closing:          # replaced by a newer poll before it ran, or the window is closing
            return
        off = next((r for r in results if isinstance(r, dict) and "offline" in r), None)
        if off is not None:
            self.set_offline(True, off["offline"])
            if job.get("err"):
                job["err"]()
            return
        self.set_offline(False)
        if job.get("cb"):
            job["cb"](results)

    def flush_api(self, timeout_ms=5000):
        """Spin the event loop until every submitted call has come back (or the timeout). Used by the offscreen tests, and
        harmless in the app: it only processes events."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        app = QApplication.instance()
        while self.api_q.inflight > 0 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()
        return self.api_q.inflight == 0

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.main.width() - self.toast.width()) // 2, self.main.height() - self.toast.height() - 96)

    def _ask_focus(self, on):
        self.composer.setProperty("focused", on)
        polish(self.composer)

    # ---- data
    def _build_conversations(self):
        by_id = {t["id"]: t for t in self.tasks}
        self.by_id = by_id

        def root_of(t):
            seen = set()
            while t.get("parent_id") and t["parent_id"] in by_id and t["id"] not in seen:
                seen.add(t["id"])
                t = by_id[t["parent_id"]]
            return t["id"]
        groups = {}
        for t in self.tasks:
            groups.setdefault(root_of(t), []).append(t)
        convs = {}
        for rid, ts in groups.items():
            ts.sort(key=lambda x: x["id"])
            convs[rid] = {"root": by_id[rid] if rid in by_id else ts[0], "tasks": ts, "updated": max(x.get("updated") or 0 for x in ts)}
        self.convs = convs
        return sorted(convs.values(), key=lambda c: -c["updated"])

    def root_of_task(self, tid):
        t = self.by_id.get(tid)
        seen = set()
        while t and t.get("parent_id") in self.by_id and t["id"] not in seen:
            seen.add(t["id"])
            t = self.by_id[t["parent_id"]]
        return t["id"] if t else None

    def latest_task(self):
        c = self.convs.get(self.current_root)
        return c["tasks"][-1] if c else None

    # ---- polling (one job each on the worker; the callbacks below apply the results in place)
    def refresh_list(self):
        """status + chat list + pending approvals in ONE worker job (key "list": a poll still queued is replaced, never stacked)."""
        self.api_batch([("GET", "/status", None), ("GET", "/tasks?limit=300", None), ("GET", "/approvals/pending", None)], self._list_ready, key="list")

    def _list_ready(self, results):
        st, tasks, pend = results
        if isinstance(st, dict) and "mode" in st:
            self.status = st
            self.update_header()
        if isinstance(tasks, list):
            self.tasks = tasks
            convs = self._build_conversations()
            self.sidebar.set_conversations(convs)
            if self.current_root is not None and self.current_root not in self.convs:
                self.new_chat()
            if self.pending_task is not None:              # --task ID: open on that conversation
                root = self.root_of_task(self.pending_task)
                self.pending_task = None
                if root is not None:
                    self.select_conversation(root)
            elif self.current_root is not None:
                self.refresh_thread()                      # a turn just created or a status that moved: fetched at once, nothing when nothing changed
        self.handle_approvals(pend if isinstance(pend, list) else [])
        self.update_composer()

    def refresh_thread(self, force=False):
        """Fetch the open chat's tasks that are active, changed or unknown (nothing at all when nothing moved) as ONE job."""
        if self.offline or self.current_root is None or self.current_root not in self.convs:
            return
        conv = self.convs[self.current_root]
        want = []
        for t in conv["tasks"]:
            cached = self.details.get(t["id"])
            if force or cached is None or t["status"] in ACTIVE or cached.get("updated") != t.get("updated") or cached.get("status") != t.get("status"):
                want.append(t["id"])
        if not want:
            if len(self.view.turns) != len(conv["tasks"]):          # a turn appeared or vanished without a fetch: reconcile the view
                self._show_thread(self.current_root)
            return
        root = self.current_root
        self.api_batch([("GET", "/tasks/%d" % tid, None) for tid in want], lambda rs, root=root: self._thread_ready(root, rs), key="thread")

    def _thread_ready(self, root, results):
        for d in results:
            if isinstance(d, dict) and "id" in d:
                self.details[d["id"]] = d
                t = self.by_id.get(d["id"])
                if t is not None:
                    t["status"], t["updated"] = d["status"], d["updated"]
        if root == self.current_root:                      # the user may have switched chats while the call was out
            self._show_thread(root)

    def _show_thread(self, root):
        conv = self.convs.get(root)
        if conv is None:
            return
        details = [self.details[t["id"]] for t in conv["tasks"] if t["id"] in self.details]
        self.view.set_thread(details, self.show_raw)
        self.update_composer()

    def set_offline(self, off, why=""):
        if off != self.offline:
            self.offline = off
            self.offline_label.setVisible(off)
            self.update_composer()
            self.update_cloud_hint()
        if off:
            self.offline_label.setText("Can't reach the agent service. Start it with:  systemctl --user start fabos-agent   (%s)" % why[:120])
            self.subtitle.setText("Agent service offline")
            self.provider_chip.setText("offline")
            self.provider_chip.setIcon(QIcon(dot_pixmap(QColor(AMBER), 10)))

    def update_header(self):
        st = self.status
        if not st:
            return
        prov = PROVIDER_LABELS.get(st.get("provider"), st.get("provider") or "")
        ready = bool(st.get("provider_ready", True))
        ai_on = bool(st.get("ai_enabled", True))
        parts = ["%s mode" % (st.get("mode") or "auto")]
        counts = st.get("tasks") or {}
        running = sum(counts.get(k, 0) for k in ACTIVE)
        if running:
            parts.append("%d active" % running)
        if not ai_on:
            parts = ["System-Wide AI is off"] + parts
        elif not ready:
            parts.append("provider not configured — open Settings")
        self.subtitle.setText(" · ".join(p for p in parts if p))
        # provider chip: name + green (ready) / amber (not configured) dot
        self.provider_chip.setText("%s · %s" % (prov, "ready" if ready else "not configured"))
        self.provider_chip.setIcon(QIcon(dot_pixmap(QColor(GREEN if ready and ai_on else AMBER), 10)))
        self.provider_chip.setToolTip(("Active AI provider: %s (%s)" % (st.get("provider_label") or prov, st.get("provider_model") or "")) if ready
                                      else "No API key for %s yet — click to add one" % prov)
        self.ai_switch.blockSignals(True)
        self.ai_switch.setChecked(ai_on)
        self.ai_switch.blockSignals(False)
        mode = st.get("mode") or "auto"
        for m, act in self.mode_actions.items():
            act.setChecked(m == mode)
        self.mode_btn.setText(mode.capitalize())
        self.mode_btn.setToolTip({"ask": "Ask — approve every risky step", "auto": "Auto — ask only for critical steps", "bypass": "Bypass — never ask"}.get(mode, mode) + "  (click to change)")
        if "ui_show_raw" in st:
            self._apply_show_raw(str(st.get("ui_show_raw", "")) == "true")
        else:                                              # older daemon without the field: ask once per list poll, off the GUI thread
            self.api_async("GET", "/settings", cb=lambda s: self._apply_show_raw(isinstance(s, dict) and str(s.get("ui.show_raw", "false")) == "true"), key="settings-raw")
        voice_on = str((st.get("voice") or {}).get("enabled", "true")) == "true"
        if voice_on != getattr(self, "_voice_on", True):
            self._voice_on = voice_on
            self.update_voice_buttons()
        self._voice_on = voice_on
        self.update_cloud_hint()

    def update_cloud_hint(self):
        """The chip shows only while the built-in (local) model is the provider, until dismissed for this session."""
        show = self.status.get("provider") == "local" and not self._cloud_hint_dismissed and not self.offline
        if show != self.cloud_hint.isVisible():
            self.cloud_hint.setVisible(show)

    def dismiss_cloud_hint(self):
        self._cloud_hint_dismissed = True
        self.update_cloud_hint()

    def _apply_show_raw(self, new_raw):
        if new_raw != self.show_raw:
            self.show_raw = new_raw
            self.refresh_thread(force=True)

    def update_composer(self):
        latest = self.latest_task()
        busy = bool(latest and latest["status"] in ACTIVE)
        waiting_user = bool(latest and latest["status"] == "waiting_user")
        on = bool(self.status.get("ai_enabled", True)) and not self.offline
        if self.voice.is_listening():
            self.ask.setPlaceholderText("Listening…")
        elif busy and not waiting_user:
            self.send_btn.set_glyph("stop", "Stop this task")
            self._send_tip = "Stop this task"
            self.ask.setPlaceholderText("The agent is working… type your follow-up now, send it when it finishes")
        elif waiting_user:
            self.send_btn.set_glyph("send", "Send your answer")
            self._send_tip = "Send your answer"
            self.ask.setPlaceholderText("Answer the agent's question…")
        else:
            self.send_btn.set_glyph("send", "Send")
            self._send_tip = "Send"
            self.ask.setPlaceholderText("Ask me to do anything…" if self.current_root is None else "Follow up in this chat…")
        if not on:
            self.ask.setPlaceholderText("Agent service offline" if self.offline else "System-Wide AI is off — turn it on to give the agent tasks")
        self.ask.setEnabled(on)
        self.update_send_state()
        self.mic_btn.setEnabled(on and self.voice.stt_available() and getattr(self, "_voice_on", True))

    def update_send_state(self):
        """Send is live only with text in the box (whitespace is not text); Stop, while a task runs, is always live. The mic
        is untouched here. Enter on an empty box is ignored by submit()."""
        latest = self.latest_task()
        stop = bool(latest and latest["status"] in ACTIVE and latest["status"] != "waiting_user")
        on = bool(self.status.get("ai_enabled", True)) and not self.offline
        has_text = bool(self.ask.text().strip())
        live = on and (stop or has_text)
        if self.send_btn.isEnabled() != live:
            self.send_btn.setEnabled(live)
        self.send_btn.setToolTip(self._send_tip if (live or not on) else "Type a request first")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor if live else Qt.CursorShape.ArrowCursor)

    def update_voice_buttons(self):
        ok = self.voice.stt_available() and getattr(self, "_voice_on", True)
        self.mic_btn.setEnabled(ok and not self.offline and bool(self.status.get("ai_enabled", True)))
        self.mic_btn.setToolTip("Speak your request" if ok else (VOICE_UNAVAILABLE if not self.voice.stt_available() else "Voice is turned off in Settings › Voice"))
        tts = self.voice.tts_available()
        for turn in self.view.turns.values():
            for msg in turn.assistant_messages():
                b = msg.buttons.get("speaker")
                if b:
                    b.setEnabled(tts)
                    b.setToolTip("Read this aloud" if tts else VOICE_UNAVAILABLE)

    # ---- actions
    def prefill(self, text):
        self.ask.setText(text)
        self.ask.setFocus()

    def submit(self, _checked=False, source="button"):
        text = self.ask.text().strip()
        latest = self.latest_task()
        busy = bool(latest and latest["status"] in ACTIVE and latest["status"] != "waiting_user")
        if busy and source in ("enter", "voice"):
            # Enter or a voice transcript while the agent works keeps the follow-up in the composer; only the STOP button
            # stops the task (a dictated sentence must never turn into a "Stop this task?" prompt or be sent early).
            if source == "voice" and text:
                self.toast.show_message("The agent is still working — your follow-up is kept in the box; send it when it finishes.")
            return
        if busy:
            self.stop_task(latest["id"])
            return
        if not text:
            return
        self.ask.clear()                                   # the box empties at once; an unreachable daemon puts the text back

        def restore():
            if not self.ask.text().strip():
                self.ask.setText(text)
        if latest and latest["status"] == "waiting_user":
            self.view.stick = True
            self.api_async("POST", "/tasks/%d/answer" % latest["id"], {"text": text}, cb=lambda _r: self.refresh_thread(force=True), err=restore)
            return
        body = {"request": text}
        if self.current_root is not None:
            body["parent_id"] = self.current_root
        parent = self.current_root
        self.api_async("POST", "/tasks", body, cb=lambda r, parent=parent: self._after_create(r, parent), err=restore)

    def stop_task(self, task_id):
        if RoundedDialog.confirm(self, "Stop this task?", "The agent stops what it is doing right now. Anything already done (files written, mail sent) stays as it is.", "Stop"):
            self.api_async("POST", "/tasks/%d/cancel" % task_id, cb=lambda _r: self.refresh_thread(force=True))

    def _after_create(self, r, parent):
        if not isinstance(r, dict) or "id" not in r:
            RoundedDialog.info(self, "Couldn't start the task", (r or {}).get("error", "unknown error"))
            return
        self.view.stick = True
        self.refresh_list()
        root = parent or r["id"]
        if self.current_root != root:
            self.select_conversation(root)
        else:
            self.refresh_thread(force=True)
        self.update_composer()

    def select_conversation(self, root_id):
        if root_id != self.current_root:
            self.view.clear()
            self.current_root = root_id
            self.editing = None
        self.sidebar.select(root_id)
        self.stack.setCurrentWidget(self.view)
        self.refresh_thread(force=True)
        self.update_composer()

    def new_chat(self):
        self.current_root = None
        self.editing = None
        self.view.clear()
        self.sidebar.select(None)
        self.stack.setCurrentIndex(0)
        self.update_composer()
        self.ask.setFocus()

    def start_edit(self, task_id, text=""):
        """Edit a request in place: the turn shows the edit card (Cancel / Send). Only one turn is edited at a time."""
        for tid, turn in self.view.turns.items():
            if tid != task_id and turn.editing:
                turn.cancel_edit()
        self.editing = task_id
        turn = self.view.turns.get(task_id)
        if turn is not None and not turn.editing:
            turn.begin_edit()

    def cancel_edit(self):
        if self.editing is not None:
            turn = self.view.turns.get(self.editing)
            if turn is not None:
                turn.cancel_edit()
        self.editing = None

    def _edit_cancelled(self, task_id):
        if self.editing == task_id:
            self.editing = None

    def submit_edit(self, task_id, text):
        """The edited version threads into the SAME chat — also when the edited turn is the chat's root — so the chat keeps
        its follow-ups and no look-alike duplicate appears; the old turn stays, dimmed as superseded, and the daemon leaves
        superseded turns out of the follow-up context."""
        text = (text or "").strip()
        if not text:
            return
        old = self.details.get(task_id) or self.by_id.get(task_id) or {}
        title = old.get("title") or ""
        parent = self.current_root or old.get("parent_id") or task_id
        calls = []
        if not title.startswith(SUPERSEDED):
            calls.append(("PATCH", "/tasks/%d" % task_id, {"title": (SUPERSEDED + title)[:80]}))
        calls.append(("POST", "/tasks", {"request": text, "parent_id": parent}))

        def created(rs, parent=parent):
            self.cancel_edit()
            self._after_create(rs[-1], parent)
        self.api_batch(calls, created)

    def retry_task(self, task_id):
        parent = self.current_root
        self.api_async("POST", "/tasks/%d/retry" % task_id, cb=lambda r, parent=parent: self._after_create(r, parent))

    def regenerate_image(self, task_id):
        """The image viewer's Regenerate: a follow-up in the same chat — the daemon has the prompt in the task context."""
        parent = self.current_root or self.root_of_task(task_id) or task_id
        self.view.stick = True
        self.api_async("POST", "/tasks", {"request": REGENERATE_REQUEST, "parent_id": parent}, cb=lambda r, parent=parent: self._after_create(r, parent))

    def send_feedback(self, task_id, rating):
        self.api_async("POST", "/tasks/%d/feedback" % task_id, {"rating": rating})

    def delete_conversation(self, root_id):
        conv = self.convs.get(root_id)
        if not conv:
            return
        n = len(conv["tasks"])
        title = (user_text(conv["root"].get("request")) or conv["root"].get("title") or "")[:60]
        if not RoundedDialog.confirm(self, "Delete this chat?", "“%s” and its %d turn%s — including every recorded step — are removed from the history. Running tasks are stopped." % (title, n, "" if n == 1 else "s"), "Delete"):
            return
        if self.current_root == root_id:
            self.new_chat()
        self._delete_tasks(conv["tasks"])

    def clear_conversations(self):
        n = len(self.convs)
        if not n:
            return
        if not RoundedDialog.confirm(self, "Clear all conversations?", "All %d chat%s and every recorded step are removed from the history. Running tasks are stopped. Settings and keys are kept." % (n, "" if n == 1 else "s"), "Clear all"):
            return
        self.new_chat()
        self._delete_tasks([t for conv in self.convs.values() for t in conv["tasks"]])

    def _delete_tasks(self, tasks):
        """All deletions as one worker job (newest first, as before); the list refreshes when they are done."""
        ids = [t["id"] for t in reversed(tasks)]
        for tid in ids:
            self.details.pop(tid, None)
        self.api_batch([("DELETE", "/tasks/%d" % tid, None) for tid in ids], lambda _rs: self.refresh_list())

    def set_mode(self, mode):
        if mode == "bypass" and (self.status.get("mode") != "bypass"):
            if not RoundedDialog.confirm(self, "Switch to Bypass mode?", "In Bypass the agent never asks before acting — including administrator commands, deleting files and sending mail. Use it only for tasks you fully trust.", "Use Bypass"):
                self.update_header()
                return
        self.api_async("PUT", "/settings", {"mode": mode}, cb=lambda _r: self.refresh_list(), err=self.refresh_list)

    def toggle_ai(self, checked):
        if not checked:
            if not RoundedDialog.confirm(self, "Turn System-Wide AI off?", "The agent stops accepting tasks and pauses background watches until you turn it back on. Normal desktop use is unaffected.", "Turn off"):
                self.ai_switch.setChecked(True)
                return
        self.api_async("PUT", "/settings", {"ai.enabled": "true" if checked else "false"}, cb=lambda _r: self.refresh_list(), err=self.refresh_list)

    def toggle_enlarge(self, on):
        self.enlarged = on
        self.sidebar.setVisible(not on)
        self.enlarge_btn.set_glyph("collapse" if on else "expand", "Back to the normal layout" if on else "Enlarge chat (hide the sidebar, maximise)")
        if on:
            self._was_maximized = self.isMaximized()
            if not self.isMaximized():
                self.showMaximized()
        elif not self._was_maximized:
            self.showNormal()

    def open_settings(self, tab=None):
        self.api_async("GET", "/settings", cb=lambda s: self._open_settings_with(s, tab if isinstance(tab, str) else None), key="open-settings")

    def _open_settings_with(self, s, tab):
        if not isinstance(s, dict) or "error" in s:
            return
        dlg = SettingsDialog(self, s, self.voice, tab=tab)
        if dlg.exec():
            self.refresh_list()
            self.refresh_thread(force=True)
            self.update_voice_buttons()

    def open_appearance(self):
        try:
            subprocess.Popen(["systemsettings", "kcm_colors"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            RoundedDialog.info(self, "Appearance follows the system", "Fab AI Controls uses the system colour scheme (Fab Dark / Fab Light). Change it in Fab Settings › Colours & Themes.")

    def about(self):
        version = ""
        for p in ("/etc/fabos-release", "/etc/os-release"):
            try:
                with open(p) as f:
                    for line in f:
                        if line.startswith(("VERSION=", "FABOS_VERSION=")):
                            version = line.split("=", 1)[1].strip().strip('"')
                            break
            except OSError:
                continue
            if version:
                break
        RoundedDialog.info(self, "About Fab OS", "Fab OS by Patience AI%s\nfabos.patienceai.in · support@patienceai.in\n\n%s is the chat for the built-in agent: it does what you ask on this computer, "
                           "shows every step live and asks before anything risky. Apache-2.0; the source is on the project site." % ((" · " + version) if version else "", APP_NAME))

    def report_problem(self):
        pre = ""
        latest = self.latest_task()
        d = self.details.get(latest["id"]) if latest else None
        if d and (d.get("error") or d.get("status") == "failed"):
            pre = "Task #%d (%s) failed: %s\nRequest: %s" % (d["id"], d.get("title") or "", d.get("error") or "", user_text(d.get("request"))[:500])
        try:
            subprocess.Popen(["fabos-feedback", "--type", "bug", "--prefill", pre])
        except OSError:
            RoundedDialog.info(self, "Fab Feedback is not installed", "Install the fabos-feedback package, or write to support@patienceai.in.")

    # ---- voice
    def toggle_listen(self):
        if self.voice.is_listening():
            self.voice.stop_listening()
            return
        if not self.voice.listen(timeout=10):
            self.update_voice_buttons()

    def on_listening(self, on):
        self.mic_btn.set_listening(on)
        self.update_composer()

    def on_transcript(self, text):
        self.ask.setText(text)
        self.submit(source="voice")

    def on_voice_unavailable(self, reason=""):
        """The mic never fails silently: the toast carries fabos-voice's own reason (muted mic, no audio session, missing
        engine) and points at Settings › Voice › Voice check."""
        self.update_voice_buttons()
        self.toast.show_message((reason or VOICE_UNAVAILABLE) + "  ·  Settings › Voice › Voice check", 5200)

    def speak(self, text):
        """Speak icon on an assistant message: starts fabos-voice say; a second click (or another message) stops it."""
        sender_turn = None
        for turn in self.view.turns.values():
            for msg in turn.assistant_messages():
                if msg.text() == text and msg.underMouse():
                    sender_turn = msg
        if self.voice.is_speaking():
            self.voice.stop_speaking()
            if self.speaking_message is not None and self.speaking_message.text() == text:
                return
        if not self.voice.tts_available():
            self.toast.show_message(VOICE_UNAVAILABLE)
            return
        self.speaking_message = sender_turn
        if sender_turn is None:
            for turn in self.view.turns.values():
                for msg in turn.assistant_messages():
                    if msg.text() == text:
                        self.speaking_message = msg
        self.voice.say(text)

    def on_speaking(self, on):
        if self.speaking_message is not None:
            try:
                self.speaking_message.set_speaking(on)
            except RuntimeError:          # the message widget was deleted meanwhile
                pass
        if not on:
            self.speaking_message = None

    # ---- approvals surface as rounded dialogs inside the app (in addition to the desktop notification)
    def handle_approvals(self, pending):
        ids = {a["id"] for a in pending}
        for aid, dlg in list(self.approval_dialogs.items()):
            if aid not in ids:
                dlg.close()
                self.approval_dialogs.pop(aid, None)
        for a in pending:
            if a["id"] in self.approval_dialogs:
                continue
            dlg = ApprovalDialog(self, a, self.show_raw)
            dlg.decided.connect(self.decide)
            dlg.finished.connect(lambda _r, aid=a["id"]: self.approval_dialogs.pop(aid, None))
            self.approval_dialogs[a["id"]] = dlg
            dlg.show()
            break        # one at a time; the next appears on the following poll

    def decide(self, approval_id, decision):
        self.api_async("POST", "/approvals/%d" % approval_id, {"decision": decision}, cb=lambda _r: self.refresh_thread(force=True))


def _arg(name):
    if name in sys.argv and len(sys.argv) > sys.argv.index(name) + 1:
        return sys.argv[sys.argv.index(name) + 1]
    return ""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(DESKTOP_ID)
    app.setFont(QFont("Inter", 10))
    task = _arg("--task")
    try:
        task_id = int(task) if task else None
    except ValueError:
        task_id = None
    w = AIControls(focus_ask="--ask" in sys.argv, prefill=_arg("--prefill"), task_id=task_id)
    w.show()
    if "--settings" in sys.argv:
        tab = _arg("--settings")                   # optional tab: general | provider | voice | mail (the welcome wizard opens Mail)
        tab = tab if tab in SETTINGS_TABS else None
        QTimer.singleShot(300, lambda: w.open_settings(tab))   # straight to Settings (the AI provider tab is where keys go)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
