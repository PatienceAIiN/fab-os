#!/usr/bin/env python3
"""Fab OS Updates — check for and install Fab OS + Ubuntu updates, pick the release channel (stable / beta),
and turn automatic updates on or off. Privileged steps run through pkexec + /usr/lib/fabos/updates/helper.sh
(polkit action in.patienceai.fabos.updates). Launch: fabos-updates [--check]"""
import os, subprocess, sys
from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QCheckBox, QPlainTextEdit, QMessageBox)

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
QToolBar { background: transparent; border: none; spacing: 6px; }
QLabel#muted { color: palette(mid); } QLabel#h1 { font-size: 22px; font-weight: 600; }
"""


def current_channel():
    try:
        for line in open("/etc/apt/sources.list.d/fabos.sources"):
            if line.startswith("Suites:"):
                return "beta" if "beta" in line else "stable"
    except OSError:
        pass
    return "stable"


def auto_enabled():
    try:
        return 'Unattended-Upgrade "1"' in open("/etc/apt/apt.conf.d/20auto-upgrades").read()
    except OSError:
        return False


class Updates(QWidget):
    def __init__(self, autocheck=False):
        super().__init__()
        self.setWindowTitle("Fab OS Updates")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        v.addWidget(QLabel("Fab OS Updates", objectName="h1"))
        v.addWidget(QLabel("Fab OS features and fixes come from Patience AI; security and package updates come from Ubuntu. Both install here.", objectName="muted"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Release channel"))
        self.channel = QComboBox()
        self.channel.addItem("Standard (stable)", "stable")
        self.channel.addItem("Beta (early features)", "beta")
        self.channel.setCurrentIndex(1 if current_channel() == "beta" else 0)
        self.channel.currentIndexChanged.connect(self.set_channel)
        row.addWidget(self.channel)
        self.auto = QCheckBox("Install updates automatically in the background")
        self.auto.setChecked(auto_enabled())
        self.auto.toggled.connect(self.set_auto)
        row.addWidget(self.auto)
        row.addStretch(1)
        v.addLayout(row)
        self.status = QLabel("", objectName="muted")
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
        if autocheck:
            self.check()

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
        self.run("check", done=done)

    def install(self):
        def done(code, out):
            self.status.setText("All updates installed." if code == 0 else "Install finished with errors (exit %d). See the log." % code)
            if "fabos-" in out and code == 0:
                self.status.setText(self.status.text() + " Fab OS components updated; log out and back in to apply desktop changes.")
        self.run("upgrade", done=done)

    def set_channel(self):
        ch = self.channel.currentData()
        self.run("channel", ch, done=lambda code, out: self.status.setText("Channel set to %s. Checking…" % ch) or self.check() if code == 0 else self.status.setText("Could not change channel (exit %d)." % code))

    def set_auto(self, on):
        self.run("auto", "on" if on else "off", done=lambda code, out: self.status.setText("Automatic updates %s." % ("enabled" if on else "disabled") if code == 0 else "Could not change setting."))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Fab OS Updates")
    app.setDesktopFileName("fabos-updates")
    app.setWindowIcon(QIcon.fromTheme("fabos-updates"))
    app.setStyleSheet(STYLE)
    w = Updates(autocheck="--check" in sys.argv)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
