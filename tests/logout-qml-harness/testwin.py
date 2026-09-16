#!/usr/bin/env python3
"""Two plain Qt windows for the leave screen's kwin step (tests/logout-screen-test.sh): the same "Test Editor" shows
draft.txt with Qt's [*] modified placeholder set (the compositor sees "draft.txt* — Test Editor") and notes.txt clean
("notes.txt — Test Editor"). Kate and every other Qt/KDE editor produce their titles through this exact mechanism
(KMainWindow::setCaption appends [*] and calls setWindowModified), so the leave screen's unsaved heuristic is checked
against what real windows carry, not against strings typed into the test. PyQt6.QtWidgets is in the image."""
import sys

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow

app = QApplication(sys.argv)
app.setApplicationDisplayName("Test Editor")
QGuiApplication.setDesktopFileName("fabos-testwin")   # the harness ships fabos-testwin.desktop so TasksModel resolves a name and an icon
wins = []
for title, modified, x in (("draft.txt[*]", True, 40), ("notes.txt[*]", False, 500)):
    w = QMainWindow()
    w.setWindowTitle(title)
    w.setWindowModified(modified)
    w.setCentralWidget(QLabel("unsaved draft" if modified else "saved notes"))
    w.resize(420, 300)
    w.move(x, 60)
    w.show()
    wins.append(w)
print("testwin: two windows up", flush=True)
sys.exit(app.exec())
