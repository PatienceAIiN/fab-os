#!/usr/bin/env python3
"""Fab OS Updates — check for and install Fab OS + Ubuntu updates, pick the release channel (stable / beta),
and turn automatic updates on or off. Privileged steps run through pkexec + /usr/lib/fabos/updates/helper.sh
(polkit action in.patienceai.fabos.updates). After an update the banner says what is left to do (log out and back in /
restart), from the journal the fabos-postupgrade dpkg trigger writes (state.py, docs/UPDATES.md).
Launch: fabos-updates [--check]      fabos-updates --state (JSON, no window)      fabos-updates --session-check"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state  # noqa: E402  (Qt-free; also used by the systemd units and the dpkg trigger)

HELPER = "/usr/lib/fabos/updates/helper.sh"
ABOUT = "Fab OS™ by Patience AI · fabos.patienceai.in · support@patienceai.in"  # ™: legal/TRADEMARKS.md (claimed, unregistered); never in machine ids
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
QFrame#banner QLabel, QFrame#qbanner QLabel { background: transparent; border: none; }
QToolBar { background: transparent; border: none; spacing: 6px; }
QLabel#muted { color: palette(mid); } QLabel#h1 { font-size: 22px; font-weight: 600; }
"""


def run_app(argv):
    from PyQt6.QtCore import QProcess, Qt
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QCheckBox,
                                 QPlainTextEdit, QFrame, QMessageBox)

    def logout_prompt(what):
        """Plasma's own confirmation screen (org.kde.LogoutPrompt): the user decides, nothing is forced."""
        method = {"logout": "promptLogout", "reboot": "promptReboot"}[what]
        iface = QDBusInterface("org.kde.LogoutPrompt", "/LogoutPrompt", "org.kde.LogoutPrompt", QDBusConnection.sessionBus())
        if iface.isValid():
            reply = iface.call(method)
            return reply.type() != QDBusMessage.MessageType.ErrorMessage
        return False

    class Updates(QWidget):
        def __init__(self, autocheck=False):
            super().__init__()
            self.setWindowTitle("Fab OS Updates")
            self.resize(760, 600)
            v = QVBoxLayout(self)
            v.setContentsMargins(24, 20, 24, 16)
            v.addWidget(QLabel("Fab OS Updates", objectName="h1"))
            v.addWidget(QLabel("Fab OS features and fixes come from Patience AI; security and package updates come from Ubuntu. Both install here.", objectName="muted"))
            # ---- post-install banner: "Update installed. Log out and back in / Restart to finish." (hidden when nothing is pending)
            self.banner = QFrame(objectName="banner")
            bl = QHBoxLayout(self.banner)
            bl.setContentsMargins(14, 10, 14, 10)
            self.banner_text = QLabel("")
            self.banner_text.setWordWrap(True)
            bl.addWidget(self.banner_text, 1)
            self.banner_btn = QPushButton("", objectName="primary")
            self.banner_btn.clicked.connect(self.finish_now)
            bl.addWidget(self.banner_btn)
            self.banner_later = QPushButton("Later")
            self.banner_later.clicked.connect(self.banner.hide)
            bl.addWidget(self.banner_later)
            self.banner.hide()
            v.addWidget(self.banner)
            row = QHBoxLayout()
            row.addWidget(QLabel("Release channel"))
            self.channel = QComboBox()
            self.channel.addItem("Standard (stable)", "stable")
            self.channel.addItem("Beta (early features)", "beta")
            self.channel.setCurrentIndex(1 if state.current_channel() == "beta" else 0)
            self.channel.currentIndexChanged.connect(self.set_channel)
            row.addWidget(self.channel)
            self.auto = QCheckBox("Install updates automatically in the background")
            self.auto.setChecked(state.auto_enabled())
            self.auto.toggled.connect(self.set_auto)
            row.addWidget(self.auto)
            row.addStretch(1)
            v.addLayout(row)
            self.auto_hint = QLabel("", objectName="muted")
            self.auto_hint.setWordWrap(True)
            v.addWidget(self.auto_hint)
            self.status = QLabel("", objectName="muted")
            self.status.setWordWrap(True)
            v.addWidget(self.status)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            v.addWidget(self.log, 1)
            b = QHBoxLayout()
            self.check_btn = QPushButton("Check for updates")
            self.check_btn.clicked.connect(self.check)
            self.install_btn = QPushButton("Install all updates", objectName="primary")
            self.install_btn.clicked.connect(self.install)
            self.install_btn.setEnabled(False)
            b.addWidget(self.check_btn)
            b.addWidget(self.install_btn)
            b.addStretch(1)
            about = QLabel(ABOUT, objectName="muted")
            about.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            b.addWidget(about)
            v.addLayout(b)
            self.proc = None
            self.pending = {}
            self.refresh_hint()
            self.refresh_banner()
            if autocheck:
                self.check()

        # ---- what is left to do after an update
        def refresh_banner(self):
            st = state.pending()
            self.pending = st
            title, text = state.summary_text(st)
            if not text:
                self.banner.hide()
                return
            if st["needs_restart"]:
                self.banner_btn.setText("Restart now")
                detail = "; ".join(r for r in st["restart_reasons"] if r)
            else:
                self.banner_btn.setText("Log out now")
                detail = "desktop components: " + ", ".join(sorted(set(p for r in st["logout_reasons"] for p in r.split(", ") if p)))
            self.banner_text.setText("<b>%s</b><br>%s%s" % (title, text, (" <span style='color:palette(mid)'>(%s)</span>" % detail) if detail else ""))
            self.banner.show()

        def finish_now(self):
            what = "reboot" if self.pending.get("needs_restart") else "logout"
            if not logout_prompt(what):
                QMessageBox.information(self, "Fab OS Updates", "Use the desktop menu to %s." % ("restart" if what == "reboot" else "log out"))

        def refresh_hint(self):
            if state.auto_enabled():
                self.auto_hint.setText("Automatic updates are on: Fab OS and Ubuntu security updates install by themselves once a day (around 06:00, or at the next start when the computer was off). "
                                       "Your session is never closed for that; you are told when a log out or a restart finishes an update.")
            else:
                self.auto_hint.setText("Automatic updates are off: you are told when updates are ready and install them here.")

        # ---- privileged actions through pkexec + helper.sh
        def run(self, *args, done=None):
            if self.proc:
                return
            self.log.clear()
            self.proc = QProcess(self)
            self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.proc.readyReadStandardOutput.connect(lambda: self.log.appendPlainText(bytes(self.proc.readAllStandardOutput()).decode(errors="replace").rstrip()))
            self.proc.finished.connect(lambda code, st: self._finished(code, done))
            self.check_btn.setEnabled(False)
            self.install_btn.setEnabled(False)
            self.status.setText("Working…")
            self.proc.start("pkexec", [HELPER] + list(args))

        def _finished(self, code, done):
            out = self.log.toPlainText()
            self.proc = None
            self.check_btn.setEnabled(True)
            if done:
                done(code, out)

        def check(self):
            def done(code, out):
                if code != 0:
                    self.status.setText("Check failed (exit %d). Are you online?" % code)
                    return
                ups = [l for l in out.splitlines() if "/" in l and "upgradable" in l]
                fab = [l for l in ups if "fabos" in l.split("/")[0]]
                self.status.setText("Up to date." if not ups else "%d update%s available (%d from Fab OS, %d from Ubuntu)." % (len(ups), "s" if len(ups) != 1 else "", len(fab), len(ups) - len(fab)))
                self.install_btn.setEnabled(bool(ups))
                self.refresh_banner()
            self.run("check", done=done)

        def install(self):
            def done(code, out):
                self.refresh_banner()
                if code != 0:
                    self.status.setText("Install finished with errors (exit %d). See the log." % code)
                    return
                st = self.pending
                if st.get("needs_restart"):
                    self.status.setText("All updates installed. Restart to finish.")
                elif st.get("needs_logout"):
                    self.status.setText("All updates installed. Log out and back in to finish.")
                else:
                    self.status.setText("All updates installed. Nothing else to do.")
            self.run("upgrade", done=done)

        def set_channel(self):
            ch = self.channel.currentData()
            self.run("channel", ch, done=lambda code, out: self.status.setText("Channel set to %s. Checking…" % ch) or self.check() if code == 0 else self.status.setText("Could not change channel (exit %d)." % code))

        def set_auto(self, on):
            def done(code, out):
                self.status.setText("Automatic updates %s." % ("enabled" if on else "disabled") if code == 0 else "Could not change setting.")
                self.refresh_hint()
            self.run("auto", "on" if on else "off", done=done)

    app = QApplication(argv)
    app.setApplicationName("Fab OS Updates")
    app.setDesktopFileName("fabos-updates")
    app.setWindowIcon(QIcon.fromTheme("fabos-updates"))
    app.setStyleSheet(STYLE)
    w = Updates(autocheck="--check" in argv)
    w.show()
    if "--screenshot" in argv:  # tests: render offscreen and save (QT_QPA_PLATFORM=offscreen)
        from PyQt6.QtCore import QTimer
        out = argv[argv.index("--screenshot") + 1]
        QTimer.singleShot(400, lambda: (w.grab().save(out), app.quit()))
    return app.exec()


def main():
    if "--state" in sys.argv:
        return state.main(["state"] + [a for a in sys.argv[1:] if a != "--state"])
    if "--session-check" in sys.argv:
        return state.main(["session-check"] + [a for a in sys.argv[1:] if a != "--session-check"])
    return run_app(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
