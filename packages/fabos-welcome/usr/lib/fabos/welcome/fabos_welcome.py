#!/usr/bin/env python3
"""Welcome to Fab OS — first-run experience (runs once per user via XDG autostart; `fabos-welcome` re-opens it).

Pages: Welcome → Appearance (Fab Light / Fab Dark, applied live) → Privacy (what Fab OS does and does not send)
→ AI (optional: open Fab AI Controls settings, or keep AI off) → Finish (links to Language, Keyboard, Network).
Every control performs the real action or opens the real settings module; nothing is simulated."""
import os, subprocess, sys

MARK = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "fabos", "welcome-done")
# Autostarted at every login: when the wizard is already done, leave before importing Qt (the /usr/bin/fabos-welcome
# wrapper normally catches this even earlier, without starting Python). docs/LOW-RAM.md
if __name__ == "__main__" and os.path.exists(MARK) and "--again" not in sys.argv and "--force" not in sys.argv:
    sys.exit(0)

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QPushButton, QCheckBox, QButtonGroup)

ABOUT = "Fab OS™ by Patience AI · fabos.patienceai.in · support@patienceai.in"  # ™: legal/TRADEMARKS.md (claimed, unregistered); never in machine ids
# AI providers offered on the AI page (ids = fabos-agentd PROVIDERS). The offline model comes first and is preselected
# when the machine has enough memory for it; the cloud ones only need an API key, pasted in Fab AI Controls › Settings.
PROVIDERS = [("local", "Built-in offline model (no account, runs on this computer)"), ("claude", "Anthropic (Claude)"), ("gemini", "Google Gemini"),
             ("openai", "OpenAI"), ("deepseek", "DeepSeek")]


def mem_total_gib():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0
STYLE = "QWidget{font-family:Inter,'Noto Sans';font-size:14px} QLabel#h1{font-size:26px;font-weight:700} QLabel#muted{color:palette(mid)} QPushButton{border-radius:10px;padding:8px 16px}"


def fabos_setting(key, value):
    """Store one agent setting through the fabos CLI. fabos-agent is only Recommended, so a missing CLI (FileNotFoundError)
    or a hung daemon (TimeoutExpired) must not raise inside a Qt slot and abort the wizard; returns True when it was written."""
    try:
        return subprocess.run(["fabos", "settings", key, value], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


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
    app.setWindowIcon(QIcon.fromTheme("fabos"))
    app.setStyleSheet(STYLE)
    w = QWizard()
    w.setWindowTitle("Welcome to Fab OS")
    w.setWizardStyle(QWizard.WizardStyle.ModernStyle)
    w.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
    w.resize(760, 520)
    logo = QPixmap("/usr/share/pixmaps/fabos-logo-dark.png" if "Dark" in current_scheme() else "/usr/share/pixmaps/fabos-logo.png")

    # 1 Welcome
    p1 = Page("Welcome to Fab OS™", "A modern desktop operating system by Patience AI. Ubuntu underneath, so every Linux application works. "
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
        # keep the global theme in step so Fab Settings > Quick Settings highlights the matching Fab OS tile
        subprocess.run(["kwriteconfig6", "--file", "kdeglobals", "--group", "KDE", "--key", "LookAndFeelPackage",
                        "in.patienceai.fabos.desktop" if dark.isChecked() else "in.patienceai.fabos.light.desktop"], capture_output=True, timeout=10)
    dark.toggled.connect(lambda _: apply_scheme())
    light.toggled.connect(lambda _: apply_scheme())
    # Accent colour (Material-expressive: one strong accent the user picks; applied live system-wide)
    p2.v.addWidget(QLabel("Accent colour", objectName="muted"))
    accents = QHBoxLayout()
    ACCENTS = [("Indigo", "59,110,245"), ("Violet", "124,92,255"), ("Mint", "31,157,87"), ("Coral", "225,29,72"), ("Amber", "183,121,31")]

    def set_accent(rgb):
        subprocess.run(["kwriteconfig6", "--file", "kdeglobals", "--group", "General", "--key", "AccentColor", rgb], capture_output=True, timeout=10)
        run("plasma-apply-colorscheme", "FabDark" if dark.isChecked() else "FabLight")   # re-applies so every app picks the accent up
    for name, rgb in ACCENTS:
        b = QPushButton(name)
        r, g, bl = rgb.split(",")
        b.setStyleSheet("QPushButton{background:rgb(%s);color:white;font-weight:600;border-radius:14px;padding:8px 14px}" % rgb)
        b.clicked.connect(lambda _, v=rgb: set_accent(v))
        accents.addWidget(b)
    accents.addStretch(1)
    p2.v.addLayout(accents)
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

    # 4 AI — pick the provider; the built-in offline model needs no account and is preselected on machines with enough RAM
    p4 = Page("The Fab OS agent", "Ask it to do anything on this computer — open apps, write files and code, send mail, watch for replies. "
              "Choose how it thinks:")
    mem_ok = mem_total_gib() >= 3.5
    prov_grp = QButtonGroup(p4)
    prov_buttons = {}
    for pid, label in PROVIDERS:
        rb = QRadioButton(label)
        prov_grp.addButton(rb)
        prov_buttons[pid] = rb
        p4.v.addWidget(rb)
        if pid == "local":
            if mem_ok:
                rb.setChecked(True)
            else:
                rb.setEnabled(False)
                rb.setText(label + " — needs 4 GB RAM")
    ai_on = QCheckBox("Keep System-Wide AI on (you can turn it off any time in Fab AI Controls)")
    ai_on.setChecked(True)
    p4.v.addWidget(ai_on)
    row = QHBoxLayout()
    b = QPushButton("Add the API key in Fab AI Controls…")
    b.clicked.connect(lambda: (apply_provider(), run("fabos-command-center", "--settings")))
    row.addWidget(b)
    row.addStretch(1)
    p4.v.addLayout(row)
    note = QLabel("Cloud providers need an API key from your own account; it is stored encrypted on this computer and used only for your requests. "
                  "The built-in model runs fully offline.", objectName="muted")
    note.setWordWrap(True)
    p4.v.addWidget(note)
    p4.v.addStretch(1)
    w.addPage(p4)

    def selected_provider():
        for pid, rb in prov_buttons.items():
            if rb.isChecked() and rb.isEnabled():
                return pid
        return None

    def apply_provider():
        pid = selected_provider()
        if pid:
            fabos_setting("provider", pid)

    # 5 Finish
    p5 = Page("You're ready", "Fab OS is set up. A few things you may want to adjust:")
    extras = QCheckBox("Install proprietary drivers and media codecs (NVIDIA, some Wi-Fi chips, MP4/H.264 playback) — optional, needs your password")
    extras.setChecked(False)
    p5.v.addWidget(extras)
    for label, kcm in (("Language & region", "kcm_regionandlang"), ("Keyboard layout", "kcm_keyboard"), ("Network", "kcm_networkmanagement"), ("Displays", "kcm_kscreen")):
        bb = QPushButton(label)
        bb.clicked.connect(lambda _, k=kcm: run("systemsettings", k))
        p5.v.addWidget(bb, 0, Qt.AlignmentFlag.AlignLeft)
    p5.v.addStretch(1)
    small = QLabel(ABOUT + " · based on Ubuntu 26.04 LTS. Ubuntu is a trademark of Canonical Ltd.; Fab OS is an independent project.", objectName="muted")
    small.setWordWrap(True)
    p5.v.addWidget(small)
    w.addPage(p5)

    def finish():
        fabos_setting("ai.enabled", "true" if ai_on.isChecked() else "false")
        apply_provider()
        if extras.isChecked():
            run("pkexec", "/usr/lib/fabos/firstboot.sh", "extras")
        os.makedirs(os.path.dirname(MARK), exist_ok=True)
        open(MARK, "w").write("done\n")
    w.accepted.connect(finish)
    w.rejected.connect(lambda: (os.makedirs(os.path.dirname(MARK), exist_ok=True), open(MARK, "w").write("skipped\n")))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
