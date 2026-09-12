#!/usr/bin/env python3
"""Welcome to Fab OS — first-run experience (runs once per user via XDG autostart; `fabos-welcome` re-opens it).

Pages: Welcome → Appearance (Fab Light / Fab Dark, applied live) → Privacy (what Fab OS does and does not send)
→ AI (optional: open Command Center settings, or keep AI off) → Finish (links to Language, Keyboard, Network).
Every control performs the real action or opens the real settings module; nothing is simulated."""
import os, subprocess, sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QPushButton, QCheckBox, QButtonGroup)

MARK = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "fabos", "welcome-done")
STYLE = "QWidget{font-family:Inter,'Noto Sans';font-size:14px} QLabel#h1{font-size:26px;font-weight:700} QLabel#muted{color:palette(mid)} QPushButton{border-radius:10px;padding:8px 16px}"


def run(*cmd):
    try:
        subprocess.Popen(list(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False


def current_scheme():
    try:
        out = subprocess.run(["kreadconfig6", "--group", "General", "--key", "ColorScheme"], capture_output=True, text=True, timeout=5).stdout.strip()
        return out or "FabDark"
    except Exception:
        return "FabDark"


class Page(QWizardPage):
    def __init__(self, title, sub):
        super().__init__()
        self.setTitle("")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(36, 28, 36, 20)
        self.v.setSpacing(12)
        h = QLabel(title, objectName="h1")
        self.v.addWidget(h)
        s = QLabel(sub, objectName="muted")
        s.setWordWrap(True)
        self.v.addWidget(s)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Welcome to Fab OS")
    app.setDesktopFileName("fabos-welcome")
    app.setStyleSheet(STYLE)
    w = QWizard()
    w.setWindowTitle("Welcome to Fab OS")
    w.setWizardStyle(QWizard.WizardStyle.ModernStyle)
    w.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
    w.resize(760, 520)
    logo = QPixmap("/usr/share/pixmaps/fabos-logo-dark.png" if "Dark" in current_scheme() else "/usr/share/pixmaps/fabos-logo.png")

    # 1 Welcome
    p1 = Page("Welcome to Fab OS", "A modern desktop operating system by Patience AI. Ubuntu underneath, so every Linux application works. "
              "The built-in agent can do tasks for you when you ask — it is off until you connect an AI provider.")
    lg = QLabel()
    lg.setPixmap(logo.scaledToHeight(96, Qt.TransformationMode.SmoothTransformation))
    p1.v.addWidget(lg)
    p1.v.addStretch(1)
    w.addPage(p1)

    # 2 Appearance
    p2 = Page("Appearance", "Choose how Fab OS looks. You can change this any time in Fab Settings → Colours & Themes.")
    grp = QButtonGroup(p2)
    dark = QRadioButton("Fab Dark — deep ink surfaces, blue accent")
    light = QRadioButton("Fab Light — bright surfaces, blue accent")
    (dark if "Dark" in current_scheme() else light).setChecked(True)
    for b in (dark, light):
        grp.addButton(b)
        p2.v.addWidget(b)

    def apply_scheme():
        name = "FabDark" if dark.isChecked() else "FabLight"
        if not run("plasma-apply-colorscheme", name):
            run("plasma-apply-colorscheme", "BreezeDark" if dark.isChecked() else "BreezeLight")
    dark.toggled.connect(lambda _: apply_scheme())
    light.toggled.connect(lambda _: apply_scheme())
    p2.v.addStretch(1)
    w.addPage(p2)

    # 3 Privacy
    p3 = Page("Privacy", "Fab OS sends nothing about you anywhere. No telemetry, no crash uploads, no usage statistics.")
    for t in ("Ubuntu and Fab OS updates are fetched from their repositories — standard package downloads only.",
              "Cloud AI is used only through a provider you configure yourself, and only for the tasks you give the agent.",
              "Feedback and bug reports go to Patience AI only when you press Send in Fab Feedback."):
        l = QLabel("•  " + t)
        l.setWordWrap(True)
        p3.v.addWidget(l)
    p3.v.addStretch(1)
    w.addPage(p3)

    # 4 AI
    p4 = Page("The Fab OS agent", "Ask it to do anything on this computer — open apps, write files and code, send mail, watch for replies. "
              "It needs an AI provider: Claude, OpenAI, Google Gemini, or a local model (fully offline).")
    ai_on = QCheckBox("Keep System-Wide AI on (you can turn it off any time in Fab Command Center)")
    ai_on.setChecked(True)
    p4.v.addWidget(ai_on)
    row = QHBoxLayout()
    b = QPushButton("Connect an AI provider now…")
    b.clicked.connect(lambda: run("fabos-command-center"))
    row.addWidget(b)
    row.addStretch(1)
    p4.v.addLayout(row)
    p4.v.addStretch(1)
    w.addPage(p4)

    # 5 Finish
    p5 = Page("You're ready", "Fab OS is set up. A few things you may want to adjust:")
    for label, kcm in (("Language & region", "kcm_regionandlang"), ("Keyboard layout", "kcm_keyboard"), ("Network", "kcm_networkmanagement"), ("Displays", "kcm_kscreen")):
        bb = QPushButton(label)
        bb.clicked.connect(lambda _, k=kcm: run("systemsettings", k))
        p5.v.addWidget(bb, 0, Qt.AlignmentFlag.AlignLeft)
    p5.v.addStretch(1)
    small = QLabel("Fab OS by Patience AI · based on Ubuntu 26.04 LTS. Ubuntu is a trademark of Canonical Ltd.; Fab OS is an independent project.", objectName="muted")
    small.setWordWrap(True)
    p5.v.addWidget(small)
    w.addPage(p5)

    def finish():
        subprocess.run(["fabos", "settings", "ai.enabled", "true" if ai_on.isChecked() else "false"], capture_output=True, timeout=10)
        os.makedirs(os.path.dirname(MARK), exist_ok=True)
        open(MARK, "w").write("done\n")
    w.accepted.connect(finish)
    w.rejected.connect(lambda: (os.makedirs(os.path.dirname(MARK), exist_ok=True), open(MARK, "w").write("skipped\n")))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if os.path.exists(MARK) and "--again" not in sys.argv and "--force" not in sys.argv:
        sys.exit(0)
    main()
