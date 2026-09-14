#!/usr/bin/env python3
"""fabos-feedback — send feedback or a bug report about Fab OS to Patience AI (replaces KDE's user-feedback and
bug-report flows, which pointed at KDE). GUI (PyQt6) by default; --cli for terminals and scripts.

  fabos-feedback                         open the dialog
  fabos-feedback --type bug --prefill "text"
  fabos-feedback --cli --type feedback --subject "..." --message "..." [--email you@x]
"""
import argparse, json, os, platform, socket, subprocess, sys

SOCK = os.environ.get("FABOS_FEEDBACK_SOCK", "/run/fabos/feedback.sock")
ABOUT = "Fab OS by Patience AI · fabos.patienceai.in · support@patienceai.in"


def system_info(include_logs=False):
    info = {}
    try:
        for line in open("/etc/os-release"):
            if line.startswith("PRETTY_NAME="):
                info["os"] = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    info["kernel"] = platform.release()
    info["arch"] = platform.machine()
    for key, cmd in (("plasma", ["plasmashell", "--version"]), ("fabos_release", ["cat", "/etc/fabos/release"]), ("memory", ["free", "-h"])):
        try:
            info[key] = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip().replace("\n", " | ")[:300]
        except Exception:
            pass
    try:
        info["agent"] = subprocess.run(["fabos", "status", "--brief"], capture_output=True, text=True, timeout=5).stdout.strip()[:200]
    except Exception:
        pass
    if include_logs:
        try:
            info["journal_tail"] = subprocess.run(["journalctl", "--user", "-n", "40", "--no-pager", "-p", "warning"], capture_output=True, text=True, timeout=8).stdout[-3000:]
        except Exception:
            pass
    return info


def send(report):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(45)
    s.connect(SOCK)
    s.sendall(json.dumps(report).encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data or b'{"ok": false, "error": "no reply from relay"}')


def gui(args):
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout, QFormLayout, QComboBox, QLineEdit, QPlainTextEdit, QCheckBox, QDialogButtonBox, QLabel, QMessageBox
    app = QApplication(sys.argv)
    app.setApplicationName("Fab OS Feedback")
    app.setDesktopFileName("fabos-feedback")
    app.setWindowIcon(QIcon.fromTheme("fabos-feedback"))
    app.setStyleSheet("QWidget{font-family:Inter,'Noto Sans';font-size:14px} QLineEdit,QPlainTextEdit,QComboBox{border:1px solid palette(mid);border-radius:10px;padding:8px} QPushButton{border-radius:10px;padding:8px 16px} QLabel#muted{color:palette(mid)}")
    d = QDialog()
    d.setWindowTitle("Send feedback to Patience AI")
    d.setMinimumWidth(560)
    lay = QVBoxLayout(d)
    f = QFormLayout()
    kind = QComboBox()
    kind.addItems(["bug", "feedback", "error"])
    kind.setCurrentText(args.type)
    f.addRow("Type", kind)
    subject = QLineEdit(args.subject or "")
    f.addRow("Subject", subject)
    email = QLineEdit(args.email or "")
    email.setPlaceholderText("optional, if you want a reply")
    f.addRow("Your email", email)
    lay.addLayout(f)
    msg = QPlainTextEdit(args.prefill or args.message or "")
    msg.setPlaceholderText("What happened? What did you expect?")
    msg.setMinimumHeight(180)
    lay.addWidget(msg)
    logs = QCheckBox("Include recent system warnings (journal) to help debugging")
    lay.addWidget(logs)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    send_btn = bb.addButton("Send", QDialogButtonBox.ButtonRole.AcceptRole)

    def do_send():
        if not msg.toPlainText().strip():
            QMessageBox.warning(d, "Fab OS", "Please write a message first.")
            return
        try:
            r = send({"type": kind.currentText(), "subject": subject.text(), "message": msg.toPlainText(), "email": email.text(), "system": system_info(logs.isChecked())})
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        if r.get("ok"):
            QMessageBox.information(d, "Fab OS", "Thank you. Your report was sent to Patience AI (via %s)." % r.get("via"))
            d.accept()
        else:
            QMessageBox.critical(d, "Fab OS", "Could not send the report: %s\n\nThe feedback channel needs /etc/fabos/feedback.env to be configured." % r.get("error"))
    send_btn.clicked.connect(do_send)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    about = QLabel(ABOUT, objectName="muted")
    about.setWordWrap(True)
    lay.addWidget(about)
    d.show()
    sys.exit(app.exec())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cli", action="store_true")
    ap.add_argument("--type", default="feedback", choices=["bug", "feedback", "error"])
    ap.add_argument("--subject", default="")
    ap.add_argument("--message", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--prefill", default="")
    ap.add_argument("--logs", action="store_true")
    a = ap.parse_args()
    if a.cli:
        text = a.message or a.prefill or sys.stdin.read()
        r = send({"type": a.type, "subject": a.subject, "message": text, "email": a.email, "system": system_info(a.logs)})
        print(json.dumps(r))
        sys.exit(0 if r.get("ok") else 1)
    gui(a)


if __name__ == "__main__":
    main()
