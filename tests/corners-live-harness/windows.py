#!/usr/bin/env python3
"""Three Wayland windows for the live shadow probe (driven by /tmp/go-* files from inner.sh, stages reported as /tmp/stage-*):
bg: frameless, maximised, light gradient — the "wallpaper" (KWin's virtual backend alone paints black; a black shadow on black
    cannot be measured); it stays square (kwinrc DisableRoundMaximize=true) and has no decoration shadow (Aurorae drops the
    shadow of a maximised window).
fg: 600x400 magenta client with the server-side (Aurorae) decoration — KWin centres it (kwinrc Placement=Centered).
steal: a small green window that takes the focus, so fg becomes inactive for the third screenshot."""
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

app = QApplication(sys.argv)


class BG(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#e4e9f4"))
        g.setColorAt(1, QColor("#93a6cc"))
        p.fillRect(self.rect(), g)


def solid(w, rgb):
    w.setAutoFillBackground(True)
    pal = w.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(*rgb))
    w.setPalette(pal)


bg = BG()
bg.setWindowFlags(Qt.WindowType.FramelessWindowHint)
bg.setWindowTitle("bg")
bg.showMaximized()
fg = QWidget()
fg.setWindowTitle("Fab corners probe")
solid(fg, (255, 0, 255))
fg.resize(600, 400)
st = QWidget()
st.setWindowTitle("steal")
solid(st, (0, 255, 0))
st.resize(160, 100)
state = {"fg": False, "st": False}


def mark(name):
    open("/tmp/stage-" + name, "w").close()
    print("stage", name, "bg", bg.frameGeometry().getRect(), "fg", fg.frameGeometry().getRect(), "fg client", fg.size().width(), fg.size().height(), flush=True)


def tick():
    if not state["fg"] and os.path.exists("/tmp/go-fg"):
        fg.show()
        state["fg"] = True
        QTimer.singleShot(1500, lambda: mark("fg"))
    if not state["st"] and os.path.exists("/tmp/go-steal"):
        st.show()
        state["st"] = True
        QTimer.singleShot(1500, lambda: mark("steal"))


QTimer.singleShot(1500, lambda: mark("bg"))
t = QTimer()
t.timeout.connect(tick)
t.start(300)
app.exec()
