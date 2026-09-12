#!/usr/bin/env python3
"""Fab OS Command Center — ask the agent to do things, watch it work, approve risky steps, manage history.

Talks to fabos-agentd over its local HTTP API. PyQt6. Launch: fabos-command-center [--ask]
"""
import json, os, sys, time, urllib.request, urllib.error
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QAction
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QListWidget, QListWidgetItem, QTextBrowser,
                             QLabel, QComboBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QDialog, QFormLayout, QDialogButtonBox, QMessageBox,
                             QInputDialog, QHeaderView, QFrame, QToolBar, QStatusBar, QPlainTextEdit)

RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent")
APP = "Fab OS"
STYLE = """
QWidget { font-family: Inter, 'Noto Sans', sans-serif; font-size: 14px; }
QLineEdit, QPlainTextEdit, QComboBox, QTextBrowser, QListWidget, QTableWidget { background: palette(base); border: 1px solid palette(mid); border-radius: 10px; padding: 6px 10px; selection-background-color: palette(highlight); }
QLineEdit:focus, QPlainTextEdit:focus { border-color: palette(highlight); }
QLineEdit#ask { font-size: 17px; padding: 12px 16px; border-radius: 14px; }
QPushButton { background: palette(button); border: 1px solid palette(mid); border-radius: 10px; padding: 8px 16px; }
QPushButton:hover { background: palette(light); }
QPushButton#primary { background: palette(highlight); color: palette(highlighted-text); font-weight: 600; border: none; }
QPushButton#approve { background: #3FCB7E; color: #0E1116; font-weight: 600; border: none; } QPushButton#deny { background: #F0655D; color: #0E1116; font-weight: 600; border: none; }
QListWidget::item { padding: 8px 10px; border-radius: 8px; } QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QTabBar::tab { padding: 8px 18px; border-radius: 8px; margin-right: 4px; } QTabBar::tab:selected { background: palette(highlight); color: palette(highlighted-text); }
QFrame#banner { background: rgba(224,166,75,0.18); border: 1px solid #E0A64B; border-radius: 12px; } QFrame#qbanner { background: rgba(110,155,255,0.18); border: 1px solid palette(highlight); border-radius: 12px; }
QToolBar { background: transparent; border: none; spacing: 6px; }
QLabel#muted { color: palette(mid); } QLabel#h1 { font-size: 22px; font-weight: 600; }
"""
STATUS_COLORS = {"running": "#6E9BFF", "queued": "#9AA4B2", "waiting_approval": "#E0A64B", "waiting_user": "#E0A64B", "done": "#3FCB7E", "failed": "#F0655D", "cancelled": "#9AA4B2"}


def api(method, path, body=None):
    token = open(os.path.join(RUN, "token")).read().strip()
    port = open(os.path.join(RUN, "port")).read().strip()
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:300], "http": e.code}


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def ts(t):
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else ""


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(APP + " — Settings"); self.setMinimumWidth(560)
        s = api("GET", "/settings"); self.s = s
        lay = QVBoxLayout(self); tabs = QTabWidget(); lay.addWidget(tabs)
        # --- permissions
        w = QWidget(); f = QFormLayout(w); self.mode = QComboBox(); self.mode.addItems(["ask", "auto", "bypass"]); self.mode.setCurrentText(s.get("mode", "auto"))
        f.addRow("Permission mode", self.mode)
        f.addRow("", QLabel("ask = approve every MEDIUM+ step · auto = approve only CRITICAL steps (sudo, disk, accounts) · bypass = never ask", objectName="muted"))
        self.max_turns = QLineEdit(s.get("agent.max_turns", "60")); f.addRow("Max steps per task", self.max_turns); tabs.addTab(w, "Permissions")
        # --- AI provider (System-Wide AI switch + 4 presets)
        w = QWidget(); f = QFormLayout(w)
        self.ai_enabled = QComboBox(); self.ai_enabled.addItems(["ON", "OFF"]); self.ai_enabled.setCurrentText("ON" if s.get("ai.enabled", "true") == "true" else "OFF"); f.addRow("System-Wide AI", self.ai_enabled)
        f.addRow("", QLabel("OFF stops the agent from accepting tasks and pauses background watches. Normal desktop use is unaffected.", objectName="muted"))
        self.provider = QComboBox()
        self.prov_ids = ["claude", "openai", "gemini", "local"]
        for pid, label in (("claude", "Claude (Anthropic)"), ("openai", "OpenAI"), ("gemini", "Google Gemini"), ("local", "Local model (llama-server / OpenAI-compatible, offline)")):
            self.provider.addItem(label, pid)
        self.provider.setCurrentIndex(max(0, self.prov_ids.index(s.get("provider", "claude")) if s.get("provider", "claude") in self.prov_ids else 0)); f.addRow("Active provider", self.provider)
        self.pfields = {}
        defaults = {"claude": ("claude-opus-5", None), "openai": ("gpt-4.1", "https://api.openai.com/v1"), "gemini": ("gemini-2.5-pro", "https://generativelanguage.googleapis.com/v1beta/openai"), "local": ("local", "http://127.0.0.1:8080/v1")}
        for pid in self.prov_ids:
            model, url = defaults[pid]
            m = QLineEdit(s.get(pid + ".model", model)); f.addRow("%s model" % pid.capitalize(), m)
            u = None
            if url:
                u = QLineEdit(s.get(pid + ".base_url", url)); f.addRow("%s endpoint" % pid.capitalize(), u)
            k = QLineEdit(); k.setEchoMode(QLineEdit.EchoMode.Password)
            k.setPlaceholderText("stored securely — paste to replace" if s["secrets"].get(pid + "_api_key") else ("optional" if pid == "local" else "API key (paste to set)"))
            f.addRow("%s API key" % pid.capitalize(), k); self.pfields[pid] = (m, u, k)
        f.addRow("", QLabel("Keys are encrypted with systemd-creds, never displayed again, never sent anywhere except the provider you chose. Local model = fully offline.", objectName="muted"))
        tabs.addTab(w, "AI provider")
        # --- mail
        w = QWidget(); f = QFormLayout(w); self.m = {}
        for key, label, default in (("mail.user", "Account (login / address)", ""), ("mail.from", "From address (optional)", ""), ("mail.imap_host", "IMAP host", ""), ("mail.imap_port", "IMAP port", "993"),
                                    ("mail.smtp_host", "SMTP host", ""), ("mail.smtp_port", "SMTP port", "587"), ("mail.smtp_security", "SMTP security (starttls/ssl/none)", "starttls")):
            e = QLineEdit(s.get(key, default)); self.m[key] = e; f.addRow(label, e)
        self.mail_pw = QLineEdit(); self.mail_pw.setEchoMode(QLineEdit.EchoMode.Password); self.mail_pw.setPlaceholderText("stored" if s["secrets"].get("mail_password") else "password or app password"); f.addRow("Password", self.mail_pw)
        tabs.addTab(w, "Mail")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); bb.accepted.connect(self.save); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def save(self):
        body = {"mode": self.mode.currentText(), "agent.max_turns": self.max_turns.text() or "60", "provider": self.provider.currentData(),
                "ai.enabled": "true" if self.ai_enabled.currentText() == "ON" else "false"}
        for pid, (m, u, k) in self.pfields.items():
            body[pid + ".model"] = m.text()
            if u is not None: body[pid + ".base_url"] = u.text()
        for k, e in self.m.items(): body[k] = e.text()
        api("PUT", "/settings", body)
        for pid, (m, u, k) in self.pfields.items():
            if k.text(): api("POST", "/secrets", {"name": pid + "_api_key", "value": k.text()})
        if self.mail_pw.text(): api("POST", "/secrets", {"name": "mail_password", "value": self.mail_pw.text()})
        self.accept()


class CommandCenter(QMainWindow):
    def __init__(self, focus_ask=False, prefill=""):
        super().__init__()
        self.setWindowTitle(APP + " Command Center"); self.setWindowIcon(QIcon.fromTheme("fabos-command-center")); self.resize(1180, 740); self.current = None
        root = QWidget(); self.setCentralWidget(root); v = QVBoxLayout(root); v.setContentsMargins(18, 14, 18, 10); v.setSpacing(12)
        # ask bar
        row = QHBoxLayout(); self.ask = QLineEdit(objectName="ask"); self.ask.setPlaceholderText("Ask me to do…   e.g. open editor, write hi and send mail to someone@example.com, then tell me when they reply")
        self.ask.returnPressed.connect(self.submit); row.addWidget(self.ask, 1)
        self.mode = QComboBox(); self.mode.addItems(["ask", "auto", "bypass"]); self.mode.setToolTip("Permission mode for new tasks"); self.mode.currentTextChanged.connect(lambda m: api("PUT", "/settings", {"mode": m})); row.addWidget(self.mode)
        go = QPushButton("Do it", objectName="primary"); go.clicked.connect(self.submit); row.addWidget(go); v.addLayout(row)
        # toolbar
        tb = QToolBar(); tb.setMovable(False); v.addWidget(tb)
        for text, fn in (("Cancel", self.cancel_task), ("Retry", self.retry_task), ("Edit…", self.edit_task), ("Delete", self.delete_task), ("Settings…", self.settings), ("Report a problem…", self.report_problem), ("Refresh", self.refresh)):
            act = QAction(text, self); act.triggered.connect(fn); tb.addAction(act)
        tb.addSeparator(); self.ai_switch = QPushButton("System-Wide AI: …"); self.ai_switch.setCheckable(True); self.ai_switch.clicked.connect(self.toggle_ai); tb.addWidget(self.ai_switch)
        # main split
        split = QSplitter(); v.addWidget(split, 1)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.addWidget(QLabel("History", objectName="muted"))
        self.list = QListWidget(); self.list.currentItemChanged.connect(self.select); lv.addWidget(self.list); split.addWidget(left)
        right = QTabWidget(); split.addWidget(right); split.setSizes([360, 820])
        # task tab
        tw = QWidget(); tv = QVBoxLayout(tw); tv.setContentsMargins(0, 0, 0, 0)
        self.banner = QFrame(objectName="banner"); bl = QHBoxLayout(self.banner); self.banner_text = QLabel(); self.banner_text.setWordWrap(True); bl.addWidget(self.banner_text, 1)
        b1 = QPushButton("Approve", objectName="approve"); b1.clicked.connect(lambda: self.decide("approved")); b2 = QPushButton("Deny", objectName="deny"); b2.clicked.connect(lambda: self.decide("denied")); bl.addWidget(b1); bl.addWidget(b2); self.banner.hide(); tv.addWidget(self.banner)
        self.qbanner = QFrame(objectName="qbanner"); ql = QHBoxLayout(self.qbanner); self.q_text = QLabel(); self.q_text.setWordWrap(True); ql.addWidget(self.q_text, 1); self.q_edit = QLineEdit(); self.q_edit.setPlaceholderText("your answer"); self.q_edit.returnPressed.connect(self.answer); ql.addWidget(self.q_edit, 1)
        qb = QPushButton("Answer", objectName="primary"); qb.clicked.connect(self.answer); ql.addWidget(qb); self.qbanner.hide(); tv.addWidget(self.qbanner)
        self.detail = QTextBrowser(); self.detail.setOpenExternalLinks(True); tv.addWidget(self.detail, 1); right.addTab(tw, "Task")
        # watches / activity tabs
        self.watches = QTableWidget(0, 6); self.watches.setHorizontalHeaderLabels(["id", "task", "kind", "status", "interval", "last result"]); self.watches.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        ww = QWidget(); wv = QVBoxLayout(ww); wv.setContentsMargins(0, 0, 0, 0); wv.addWidget(self.watches); wb = QPushButton("Stop selected watch"); wb.clicked.connect(self.unwatch); wv.addWidget(wb); right.addTab(ww, "Watches")
        self.activity = QTableWidget(0, 5); self.activity.setHorizontalHeaderLabels(["time", "actor", "event", "task", "detail"]); self.activity.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch); right.addTab(self.activity, "Activity log")
        self.setStatusBar(QStatusBar()); self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(2000); self.refresh()
        if prefill: self.ask.setText(prefill); self.ask.setFocus(); self.ask.setCursorPosition(len(prefill))
        if focus_ask: self.ask.setFocus()

    # ---- actions
    def submit(self):
        text = self.ask.text().strip()
        if not text: return
        r = api("POST", "/tasks", {"request": text})
        if "id" in r: self.ask.clear(); self.refresh(select_id=r["id"])
        else: QMessageBox.warning(self, APP, r.get("error", "failed"))

    def sel_id(self):
        it = self.list.currentItem(); return it.data(Qt.ItemDataRole.UserRole) if it else None

    def cancel_task(self):
        if self.sel_id(): api("POST", "/tasks/%d/cancel" % self.sel_id()); self.refresh()

    def retry_task(self):
        if self.sel_id():
            r = api("POST", "/tasks/%d/retry" % self.sel_id()); self.refresh(select_id=r.get("id"))

    def delete_task(self):
        tid = self.sel_id()
        if tid and QMessageBox.question(self, APP, "Delete task #%d and its history?" % tid) == QMessageBox.StandardButton.Yes:
            api("DELETE", "/tasks/%d" % tid); self.current = None; self.refresh()

    def edit_task(self):
        tid = self.sel_id()
        if not tid: return
        t = api("GET", "/tasks/%d" % tid)
        text, ok = QInputDialog.getMultiLineText(self, "Edit task #%d" % tid, "Request (saving re-runs it as a new task):", t.get("request", ""))
        if ok and text.strip():
            api("PATCH", "/tasks/%d" % tid, {"request": text}); r = api("POST", "/tasks/%d/retry" % tid); self.refresh(select_id=r.get("id"))

    def settings(self):
        if SettingsDialog(self).exec(): self.refresh()

    def toggle_ai(self):
        api("PUT", "/settings", {"ai.enabled": "true" if self.ai_switch.isChecked() else "false"}); self.refresh()

    def report_problem(self):
        import subprocess
        pre = ""
        if self.current and (self.current.get("error") or self.current.get("status") == "failed"):
            pre = "Task #%d (%s) failed: %s\nRequest: %s" % (self.current["id"], self.current["title"], self.current.get("error") or "", self.current["request"][:500])
        subprocess.Popen(["fabos-feedback", "--type", "bug", "--prefill", pre])

    def decide(self, decision):
        for a in (self.current or {}).get("approvals", []):
            if a["status"] == "pending": api("POST", "/approvals/%d" % a["id"], {"decision": decision})
        self.refresh()

    def answer(self):
        if self.current and self.q_edit.text().strip():
            api("POST", "/tasks/%d/answer" % self.current["id"], {"text": self.q_edit.text()}); self.q_edit.clear(); self.refresh()

    def unwatch(self):
        r = self.watches.currentRow()
        if r >= 0: api("DELETE", "/watches/%s" % self.watches.item(r, 0).text()); self.refresh()

    # ---- refresh
    def refresh(self, select_id=None):
        try:
            st = api("GET", "/status"); tasks = api("GET", "/tasks?limit=200")
        except Exception as e:
            self.statusBar().showMessage("fabos-agentd not running: %s  (systemctl --user start fabos-agent)" % e); return
        if isinstance(st, dict) and "mode" in st:
            self.mode.blockSignals(True); self.mode.setCurrentText(st["mode"]); self.mode.blockSignals(False)
            on = st.get("ai_enabled", True); self.ai_switch.setChecked(bool(on)); self.ai_switch.setText("System-Wide AI: %s" % ("ON" if on else "OFF"))
            self.ai_switch.setStyleSheet("background:rgba(63,203,126,0.2);color:#1F9D57;font-weight:600" if on else "background:rgba(240,101,93,0.2);color:#C6362F;font-weight:600")
            self.ask.setEnabled(bool(on)); self.ask.setPlaceholderText("Ask me to do…   e.g. open editor, write hi and send mail to someone@example.com, then tell me when they reply" if on else "System-Wide AI is OFF — turn it on to give the agent tasks")
            msg = "mode: %s · provider: %s%s · mail: %s · pending approvals: %d · active watches: %d" % (st["mode"], st["provider"], "" if st["provider_ready"] else " (NOT CONFIGURED — Settings…)", "ready" if st["mail_ready"] else "not configured", st["pending_approvals"], st["active_watches"])
            self.statusBar().showMessage(msg)
        cur = select_id or self.sel_id(); self.list.blockSignals(True); self.list.clear()
        for t in tasks if isinstance(tasks, list) else []:
            it = QListWidgetItem("#%d  %s\n%s · %s" % (t["id"], t["title"], t["status"].replace("_", " "), ts(t["updated"]))); it.setData(Qt.ItemDataRole.UserRole, t["id"])
            it.setToolTip(t.get("result") or t.get("error") or ""); self.list.addItem(it)
            if t["id"] == cur: self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if cur: self.show_task(cur)
        ws = api("GET", "/watches"); self.watches.setRowCount(0)
        for w in ws if isinstance(ws, list) else []:
            r = self.watches.rowCount(); self.watches.insertRow(r)
            for c, val in enumerate((w["id"], w["task_id"], w["kind"], w["status"], "%ds" % w["interval_s"], w["last_result"] or "")): self.watches.setItem(r, c, QTableWidgetItem(str(val)))
        acts = api("GET", "/activity?limit=300"); self.activity.setRowCount(0)
        for e in acts if isinstance(acts, list) else []:
            r = self.activity.rowCount(); self.activity.insertRow(r)
            for c, val in enumerate((ts(e["ts"]), e["actor"], e["kind"], e["task_id"] or "", (e["detail"] or "")[:200])): self.activity.setItem(r, c, QTableWidgetItem(str(val)))

    def select(self, it, prev=None):
        if it: self.show_task(it.data(Qt.ItemDataRole.UserRole))

    def show_task(self, tid):
        t = api("GET", "/tasks/%d" % tid)
        if "id" not in t: return
        self.current = t; col = STATUS_COLORS.get(t["status"], "#9AA4B2")
        pal = self.palette(); base = pal.base().color().name(); mid = pal.mid().color().name(); alt = pal.alternateBase().color().name()
        h = ['<div style="font-size:18px;font-weight:600">#%d %s <span style="color:%s;font-size:13px">● %s</span></div>' % (t["id"], esc(t["title"]), col, t["status"].replace("_", " ")),
             '<div style="color:%s;margin:4px 0 12px">%s · mode %s · tokens in/out %s/%s</div><div style="background:%s;border-radius:10px;padding:10px;margin-bottom:12px">%s</div>' % (mid, ts(t["created"]), esc(t.get("mode") or "default"), t["cost_in"], t["cost_out"], alt, esc(t["request"]))]
        for s in t["steps"]:
            k = s["kind"]
            if k == "tool_call":
                rc = {"LOW": "#3FCB7E", "MEDIUM": "#6E9BFF", "HIGH": "#E0A64B", "CRITICAL": "#F0655D"}.get(s["risk"], "#9AA4B2")
                try: inp = json.loads(s["input"] or "{}")
                except Exception: inp = {"raw": s["input"]}
                summary = inp.get("command") or inp.get("path") or inp.get("to") or inp.get("url") or inp.get("app") or inp.get("question") or inp.get("message") or inp.get("kind") or ""
                h.append('<div style="margin:6px 0"><span style="color:#9AA4B2">%s</span> <b>%s</b> <span style="color:%s">%s</span> <span style="color:#9AA4B2">%s</span><div style="font-family:JetBrains Mono,monospace;font-size:12px;margin-left:70px">%s</div>' % (ts(s["ts"]), esc(s["name"]), rc, s["risk"], esc(s["decision"]), esc(str(summary)[:400])))
                if s["output"]:
                    try: out = json.loads(s["output"]); shown = out.get("stdout") or out.get("error") or out.get("content") or json.dumps(out)[:600]
                    except Exception: shown = s["output"][:600]
                    h.append('<div style="font-family:JetBrains Mono,monospace;font-size:12px;color:%s;margin-left:70px;white-space:pre-wrap">%s</div>' % ("#F0655D" if "error" in (s["output"] or "")[:20] else mid, esc(str(shown)[:1200])))
                h.append("</div>")
            elif k == "assistant": h.append('<div style="margin:8px 0;padding:8px 10px;border-left:3px solid %s">%s</div>' % (pal.highlight().color().name(), esc(s["output"])))
            elif k == "final": h.append('<div style="margin:12px 0;padding:10px;border-radius:10px;background:rgba(63,203,126,0.18)"><b>Result</b><br>%s</div>' % esc(s["output"]))
            elif k == "error": h.append('<div style="margin:12px 0;padding:10px;border-radius:10px;background:rgba(240,101,93,0.18)"><b>Error</b><br>%s</div>' % esc(s["output"]))
            elif k in ("question", "answer", "watch_hit"): h.append('<div style="margin:6px 0;color:#E0A64B">%s <b>%s</b>: %s</div>' % (ts(s["ts"]), k, esc(s["output"] or s["input"])))
        self.detail.setHtml("".join(h)); self.detail.verticalScrollBar().setValue(self.detail.verticalScrollBar().maximum())
        pend = [a for a in t["approvals"] if a["status"] == "pending"]
        if pend:
            a = pend[0]; self.banner_text.setText("Approval needed (%s — %s): %s %s" % (a["risk"], a["reason"], a["tool"], a["input"][:300])); self.banner.show()
        else: self.banner.hide()
        qs = [q for q in t["questions"] if not q["answer"]]
        if qs: self.q_text.setText(qs[-1]["question"]); self.qbanner.show()
        else: self.qbanner.hide()


def main():
    app = QApplication(sys.argv); app.setApplicationName(APP + " Command Center"); app.setDesktopFileName("fabos-command-center"); app.setStyleSheet(STYLE)
    f = QFont("Inter", 10); app.setFont(f)
    pre = sys.argv[sys.argv.index("--prefill") + 1] if "--prefill" in sys.argv and len(sys.argv) > sys.argv.index("--prefill") + 1 else ""
    w = CommandCenter(focus_ask="--ask" in sys.argv, prefill=pre); w.show(); sys.exit(app.exec())


if __name__ == "__main__":
    main()
