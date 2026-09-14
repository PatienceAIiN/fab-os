#!/usr/bin/env python3
"""Fab AI Controls — chat with the Fab OS agent: ask it to do things, follow up, watch it work, approve risky steps.

A ChatGPT-style desktop app on top of fabos-agentd's local HTTP API (PyQt6):
  * left sidebar: New chat, search, chats grouped Today / Yesterday / Earlier (a chat = a root task + its follow-ups)
  * main pane: the chat as bubbles (your requests right/accent, the agent's answers left/surface), tool steps folded into
    one "Worked: N actions" chip per turn, typing indicator + fade-in while a task runs
  * bottom: rounded composer = follow-up bar of the selected chat; the send button turns into STOP while a task runs
Everything follows the system colour scheme through QPalette; radii/spacing from the Fab OS design tokens.
Launch: fabos-command-center [--ask] [--prefill TEXT] [--settings] [--task ID]   (the executable keeps its historical name)
"""
import datetime, json, math, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from PyQt6.QtCore import (Qt, QTimer, QSize, QPropertyAnimation, QVariantAnimation, QEasingCurve, QRectF, QEvent, QPointF, pyqtSignal)
from PyQt6.QtGui import (QFont, QIcon, QImage, QPixmap, QPainter, QColor, QPalette, QPen, QBrush, QTextDocument, QTextCursor, QTextBlockFormat,
                         QTextCharFormat, QTextFormat, QGuiApplication, QAction, QFontMetrics)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
                             QTextBrowser, QLabel, QComboBox, QTabWidget, QDialog, QFormLayout, QFrame, QScrollArea, QSizePolicy, QToolButton,
                             QCheckBox, QStackedWidget, QMenu, QGraphicsOpacityEffect, QStyle)

APP_NAME = "Fab AI Controls"
DESKTOP_ID = "fabos-command-center"           # executable / desktop-file / icon id: unchanged so shortcuts and docks keep working
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabos-agent")
FOLLOWUP_MARK = "\n\nFollow-up request:\n"    # must match fabos_agentd.FOLLOWUP_MARK
SUPERSEDED = "(superseded) "
ACTIVE = ("queued", "running", "waiting_approval", "waiting_user")
# design tokens (px): radii control 12 · field 14 · card 20 · panel 20 · popup 24; spacing grid 12/16
R_CONTROL, R_FIELD, R_CARD, R_PANEL, R_POPUP = 12, 14, 20, 20, 24
SP, SP2 = 12, 16
POLL_LIST_MS, POLL_THREAD_MS = 4000, 1500
API_TIMEOUT = 5                  # s — the daemon is local; calls run on the GUI thread, so a hung daemon must not freeze the UI for long
KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 1     # sidebar list items: their reconcile key ("h:Today" / "c:<root id>")
PROVIDER_LABELS = {"claude": "Claude", "openai": "OpenAI", "gemini": "Gemini", "local": "Local model", "fake": "Test provider"}
# semantic status colours (used for tiny risk/status dots only; all surfaces and text come from the palette)
RISK_COLORS = {"LOW": "#3FCB7E", "MEDIUM": "#6E9BFF", "HIGH": "#E0A64B", "CRITICAL": "#F0655D"}
STATUS_TEXT = {"queued": "Queued", "running": "Working", "waiting_approval": "Needs your approval", "waiting_user": "Needs your answer",
               "done": "Done", "failed": "Failed", "cancelled": "Stopped"}


# ----------------------------------------------------------------------------- daemon API
class AgentOffline(Exception):
    pass


def api(method, path, body=None):
    """Call fabos-agentd. Returns the JSON reply ({"error":..., "http":...} on HTTP errors); raises AgentOffline when unreachable."""
    try:
        token = open(os.path.join(RUN, "token")).read().strip()
        port = open(os.path.join(RUN, "port")).read().strip()
    except OSError as e:
        raise AgentOffline(str(e))
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read()).get("error", str(e)), "http": e.code}
        except Exception:
            return {"error": str(e), "http": e.code}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AgentOffline(str(e))


def user_text(request):
    """What the user typed (a follow-up's request also carries a context prefix the daemon added)."""
    return (request or "").rsplit(FOLLOWUP_MARK, 1)[-1].strip()


def ts_clock(t):
    return time.strftime("%H:%M", time.localtime(t)) if t else ""


def day_group(t):
    d = datetime.date.fromtimestamp(t or 0)
    today = datetime.date.today()
    return "Today" if d == today else ("Yesterday" if d == today - datetime.timedelta(days=1) else "Earlier")


# ----------------------------------------------------------------------------- icons: Material-style glyphs from inline SVG
# 24x24 grid, 2px round strokes (Material Symbols "outlined" feel). Rendered through Qt's SVG image plugin into a QIcon
# in the current palette colour, so icons follow the colour scheme like everything else.
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
}
_ICON_CACHE = {}


def glyph_pixmap(name, color, size=20, dpr=2.0):
    key = (name, color.name(QColor.NameFormat.HexArgb), size, dpr)
    if key not in _ICON_CACHE:
        px_size = int(size * dpr)
        c = color.name()
        body = GLYPHS[name].replace("{c}", c)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (px_size, px_size, c, body)).encode()
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
    line = rgba(text, 0.10 if dark else 0.12)
    hover = rgba(text, 0.07)
    card = base.name()
    muted = rgba(text, 0.62)
    return """
QWidget { font-family: Inter, 'Noto Sans', sans-serif; font-size: 14px; color: %(text)s; }
QMainWindow, QWidget#root { background: %(win)s; }
QToolTip { background: %(alt)s; color: %(text)s; border: 1px solid %(line)s; border-radius: 8px; padding: 6px 10px; }
QFrame#card { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#sidebar { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#header { background: transparent; border: none; }
QLabel#title { font-size: 16px; font-weight: 600; }
QLabel#subtitle, QLabel#muted, QLabel#groupHeader { color: %(muted)s; font-size: 12px; }
QLabel#groupHeader { font-weight: 600; letter-spacing: 0.4px; padding: 10px 12px 4px 12px; }
QLabel#rowTitle { font-size: 13.5px; font-weight: 500; }
QLabel#rowSub { color: %(muted)s; font-size: 11.5px; }
QLabel#greeting { font-size: 22px; font-weight: 600; }
QLabel#hint { color: %(muted)s; font-size: 13px; }
QLabel#chipText { color: %(muted)s; font-size: 12.5px; }
QLabel#offline { background: %(warnbg)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 8px 12px; }
QToolButton#icon { background: transparent; border: none; border-radius: %(rctl)dpx; padding: 0; }
QToolButton#icon:hover { background: %(hover)s; }
QToolButton#icon:pressed { background: %(press)s; }
QToolButton#icon:checked { background: %(hisoft)s; }
QToolButton#iconAccent { background: %(hi)s; border: none; border-radius: 18px; padding: 0; }
QToolButton#iconAccent:hover { background: %(hihover)s; }
QToolButton#iconAccent:disabled { background: %(hidim)s; }
QToolButton#chip { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 4px 10px 4px 8px; color: %(muted)s; font-size: 12.5px; }
QToolButton#chip:hover { background: %(hover)s; }
QToolButton#chip::menu-indicator { image: none; }
QPushButton#newChat { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 9px 14px; font-weight: 500; text-align: left; }
QPushButton#newChat:hover { background: %(hover)s; }
QPushButton#primary { background: %(hi)s; color: %(hit)s; border: none; border-radius: %(rctl)dpx; padding: 9px 18px; font-weight: 600; }
QPushButton#primary:hover { background: %(hihover)s; }
QPushButton#ghost { background: transparent; color: %(text)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 9px 18px; }
QPushButton#ghost:hover { background: %(hover)s; }
QLineEdit { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rfield)dpx; padding: 8px 12px; selection-background-color: %(hi)s; selection-color: %(hit)s; }
QLineEdit:focus { border-color: %(hi)s; }
QLineEdit#search { padding-left: 34px; }
QFrame#composer { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rpopup)dpx; }
QFrame#composer[focused="true"] { border-color: %(hi)s; }
QLineEdit#ask { background: transparent; border: none; font-size: 15px; padding: 8px 4px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: %(rctl)dpx; margin: 1px 6px; padding: 0; }
QListWidget::item:selected { background: %(hisoft)s; }
QListWidget::item:hover:!selected { background: %(hover)s; }
QListWidget::item:disabled { background: transparent; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(scrollh)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QFrame#userBubble { background: %(hi)s; border: 1px solid %(hi)s; border-radius: %(rcard)dpx; }
QFrame#userBubble QLabel { color: %(hit)s; font-size: 14.5px; }
QFrame#assistantBubble { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#errorBubble { background: %(errbg)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#infoBubble { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QFrame#questionBubble { background: %(hisoft)s; border: 1px solid %(line)s; border-radius: %(rcard)dpx; }
QTextBrowser#md { background: transparent; border: none; font-size: 14.5px; selection-background-color: %(hi)s; selection-color: %(hit)s; }
QFrame#steps { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rfield)dpx; }
QLabel#stepLabel { font-size: 13px; }
QLabel#raw { font-family: 'JetBrains Mono', monospace; font-size: 12px; background: %(codebg)s; border-radius: 8px; padding: 6px 8px; color: %(text)s; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 8px 16px; border-radius: %(rctl)dpx; margin-right: 6px; color: %(muted)s; background: transparent; }
QTabBar::tab:selected { background: %(hisoft)s; color: %(text)s; font-weight: 600; }
QComboBox { background: %(alt)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 6px 12px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; selection-background-color: %(hisoft)s; selection-color: %(text)s; padding: 4px; }
QMenu { background: %(card)s; border: 1px solid %(line)s; border-radius: %(rctl)dpx; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 8px; }
QMenu::item:selected { background: %(hisoft)s; }
QMenu::indicator { width: 16px; height: 16px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 6px; border: 1.5px solid %(mutedline)s; background: %(alt)s; }
QCheckBox::indicator:checked { background: %(hi)s; border-color: %(hi)s; }
QLabel#riskBadge { border-radius: 9px; padding: 2px 8px; font-size: 11.5px; font-weight: 600; }
""" % dict(text=text.name(), win=win.name(), card=card, alt=alt.name(), hi=hi.name(), hit=hit.name(), line=line, hover=hover, muted=muted,
           press=rgba(text, 0.12), hisoft=rgba(hi, 0.16 if dark else 0.14), hihover=hi.lighter(112).name() if dark else hi.darker(108).name(),
           hidim=rgba(hi, 0.35), scroll=rgba(text, 0.18), scrollh=rgba(text, 0.30), errbg=rgba(QColor("#F0655D"), 0.14), warnbg=rgba(QColor("#E0A64B"), 0.16),
           codebg=rgba(text, 0.08), mutedline=rgba(text, 0.35), rctl=R_CONTROL, rfield=R_FIELD, rcard=R_CARD, rpopup=R_POPUP)


def fade_in(widget, ms=260):
    """Opacity 0 → 1 on a widget (new assistant text streaming in). The effect is removed afterwards so text stays crisp."""
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


# ----------------------------------------------------------------------------- small widgets
class IconButton(QToolButton):
    """An action as an icon with a tooltip (no words). accent=True gives the round accent button (send / stop / allow)."""

    def __init__(self, glyph, tooltip, size=32, accent=False, icon_size=20, parent=None):
        super().__init__(parent)
        self.glyph, self.accent, self._icon_size = glyph, accent, icon_size
        self.setObjectName("iconAccent" if accent else "icon")
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
        col = pal.color(QPalette.ColorRole.HighlightedText) if self.accent else pal.color(QPalette.ColorRole.Text)
        if not self.accent:
            col = QColor(col)
            col.setAlphaF(0.85)
        self.setIcon(glyph_icon(self.glyph, col, self._icon_size))

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_icon()
        super().changeEvent(e)


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
        track = QColor(off)
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
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        self.phase += 0.16
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
            "ask_user": "Asked you a question", "list_apps": "Looked up installed apps", "context": "Tidied earlier notes to fit the model's memory"}


def parse_input(raw):
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except Exception:
        return {"raw": raw}


PENDING = {"run_shell": "run a command", "read_file": "read a file", "write_file": "write a file", "list_dir": "look inside a folder", "open_app": "open an app",
           "type_text": "type into an app", "send_email": "send an email", "check_email": "check the inbox", "schedule_watch": "set up a background watch",
           "web_fetch": "read a web page", "notify_user": "send you a notification", "ask_user": "ask you a question", "list_apps": "look up installed apps"}


def friendly_label(step, pending=False):
    """Plain-language description of a tool step. pending=True phrases it as an intention (approval dialogs)."""
    name = step.get("name") or ""
    inp = parse_input(step.get("input"))
    if pending:
        label = PENDING.get(name, (name or "step").replace("_", " "))
        if name == "run_shell" and inp.get("as_root"):
            label = "run a command as administrator"
        elif name == "write_file" and inp.get("append"):
            label = "add to a file"
        if name == "open_app" and inp.get("app"):
            label = "open %s" % str(inp["app"]).split("/")[-1]
        elif name in ("write_file", "read_file") and inp.get("path"):
            label += " (%s)" % os.path.basename(str(inp["path"]).rstrip("/"))
        elif name == "send_email" and inp.get("to"):
            label = "send an email to %s" % inp["to"]
        elif name == "web_fetch" and inp.get("url"):
            host = urllib.parse.urlparse(str(inp["url"])).netloc
            if host:
                label = "read a page on %s" % host
        return label
    label = FRIENDLY.get(name, (name or "step").replace("_", " ").capitalize())
    if name == "run_shell" and inp.get("as_root"):
        label = "Ran a command as administrator"
    elif name == "write_file" and inp.get("append"):
        label = "Added to a file"
    if name == "open_app" and inp.get("app"):
        label = "Opened %s" % str(inp["app"]).split("/")[-1]
    elif name in ("write_file", "read_file") and inp.get("path"):
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
    else:
        out = step.get("output") or ""
        if out[:12].lstrip().startswith('{"error"'):
            label += " — failed"
    return label


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
        self._ideal = 200           # natural single-line width of the text: short answers get a bubble that hugs them
        self.max_w = 640            # widest the bubble may grow (set by the conversation view from its width)

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

    # --- geometry: the layout asks how tall we are for a given width
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
        if int(self.document().textWidth()) != w:       # paint at the width the layout finally gave us
            self.document().setTextWidth(w)

    def wheelEvent(self, e):
        e.ignore()          # let the conversation scroll

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange) and self._md:
            md, self._md = self._md, None
            self.set_markdown(md)
        super().changeEvent(e)


class Bubble(QWidget):
    """One message: a rounded bubble (user = accent/right, assistant = surface/left) with hover action icons underneath."""
    edit_requested = pyqtSignal()
    retry_requested = pyqtSignal()

    def __init__(self, role, parent=None):
        super().__init__(parent)
        self.role = role
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName({"user": "userBubble", "assistant": "assistantBubble", "error": "errorBubble", "info": "infoBubble", "question": "questionBubble"}[role])
        fl = QHBoxLayout(self.frame)
        fl.setContentsMargins(SP2, 10, SP2, 10)
        fl.setSpacing(10)
        self.avatar = None
        if role != "user":
            self.avatar = QLabel()
            self.avatar.setFixedSize(20, 20)
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
            row.addWidget(self.frame, 0)
            row.addStretch(1)
        outer.addLayout(row)
        # actions under the bubble, visible on hover only (space is kept so nothing jumps)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(0)
        self.actions.setContentsMargins(6 if role != "user" else 0, 0, 6, 0)
        self.action_buttons = []
        if role == "user":
            self.actions.addStretch(1)
            self._add_action("edit", "Edit and resend", self.edit_requested.emit)
            self._add_action("retry", "Retry this request", self.retry_requested.emit)
        elif role == "assistant":
            self._add_action("copy", "Copy text", self.copy_text)
            self._add_action("retry", "Retry this request", self.retry_requested.emit)
            self.actions.addStretch(1)
        outer.addLayout(self.actions)
        self._hover(False)
        self.refresh_avatar()

    def _add_action(self, glyph, tip, fn):
        b = IconButton(glyph, tip, size=24, icon_size=14)
        sp = b.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        b.setSizePolicy(sp)
        b.clicked.connect(fn)
        self.actions.addWidget(b)
        self.action_buttons.append(b)

    def _hover(self, on):
        for b in self.action_buttons:
            b.setVisible(on)

    def enterEvent(self, e):
        self._hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover(False)
        super().leaveEvent(e)

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
            wide = max(240, int(w * 1.2))            # the agent may use more of the row than the user's 72 %
            self.frame.setMaximumWidth(wide)
            self.md.set_max_width(wide - (SP2 * 2 + 20 + 10 + 2))

    def copy_text(self):
        QGuiApplication.clipboard().setText(self.text())

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_avatar()
        super().changeEvent(e)


class WorkedChip(QWidget):
    """'Worked: N actions' — collapsed by default; expands to friendly labels (and raw input/output when ui.show_raw is on)."""

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
        self.rows = QVBoxLayout(self.panel)
        self.rows.setContentsMargins(SP, 10, SP, 10)
        self.rows.setSpacing(6)
        self.panel.hide()
        lay.addWidget(self.panel)
        self.step_ids = []
        self.show_raw = False
        self.steps = []
        self._refresh_icon()

    def _refresh_icon(self):
        col = QColor(self.palette().color(QPalette.ColorRole.Text))
        col.setAlphaF(0.7)
        self.button.setIcon(glyph_icon("chevron-up" if self.button.isChecked() else "chevron-down", col, 15))

    def _toggle(self, on):
        self.panel.setVisible(on)
        self._refresh_icon()

    def set_steps(self, steps, show_raw):
        n = len(steps)
        text = "Worked: %d action%s" % (n, "" if n == 1 else "s")
        if self.button.text() != text:
            self.button.setText(text)
        ids = [s["id"] for s in steps]
        changed = ids != self.step_ids or show_raw != self.show_raw or any(a.get("output") != b.get("output") or a.get("decision") != b.get("decision") for a, b in zip(steps, self.steps))
        self.steps, self.step_ids, self.show_raw = [dict(s) for s in steps], ids, show_raw
        if not changed:
            return
        self.setUpdatesEnabled(False)
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for s in steps:
            self.rows.addWidget(self._row(s, show_raw))
        self.setUpdatesEnabled(True)

    def _row(self, s, show_raw):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        risk = s.get("risk") or ""
        dot = '<span style="color:%s">●</span> ' % RISK_COLORS.get(risk, "#9AA4B2") if risk else ""
        lab = QLabel(dot + friendly_label(s) + (' <span style="opacity:0.6;font-size:11px">· %s risk</span>' % risk.lower() if risk else ""))
        lab.setObjectName("stepLabel")
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setWordWrap(True)
        v.addWidget(lab)
        if show_raw and s.get("kind") == "tool_call":
            raw = step_input_summary(s.get("name"), s.get("input"))
            out = step_output_summary(s.get("output"))
            txt = raw + (("\n→ " + out) if out else "")
            if len(txt) > 1500:
                txt = txt[:1500] + " …"
            r = QLabel(txt)
            r.setObjectName("raw")
            r.setWordWrap(True)
            r.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.addWidget(r)
        return w

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._refresh_icon()
        super().changeEvent(e)


class Turn(QWidget):
    """One task of the chat: the request bubble, the folded actions chip, the agent's messages, and its live status."""
    edit_requested = pyqtSignal(int, str)
    retry_requested = pyqtSignal(int)

    def __init__(self, task_id, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.task = None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.user = Bubble("user")
        self.user.edit_requested.connect(lambda: self.edit_requested.emit(self.task_id, user_text((self.task or {}).get("request"))))
        self.user.retry_requested.connect(lambda: self.retry_requested.emit(self.task_id))
        self.lay.addWidget(self.user)
        self.chip = WorkedChip()
        self.chip.hide()
        self.lay.addWidget(self.chip)
        self.step_widgets = {}          # step id -> Bubble
        self.order = []                 # step ids in display order
        self.status_row = QHBoxLayout()
        self.status_row.setContentsMargins(6, 0, 0, 0)
        self.status_row.setSpacing(8)
        self.typing = TypingIndicator()
        self.typing.hide()
        self.status_row.addWidget(self.typing, 0)
        self.status_label = QLabel()
        self.status_label.setObjectName("chipText")
        self.status_label.hide()
        self.status_row.addWidget(self.status_label, 0)
        self.status_row.addStretch(1)
        self.lay.addLayout(self.status_row)
        self.max_w = 520

    def set_max_width(self, w):
        self.max_w = w
        self.user.set_max_width(w)
        for b in self.step_widgets.values():
            b.set_max_width(w)

    def _bubble(self, step, animate):
        sid = step["id"]
        b = self.step_widgets.get(sid)
        if b is None:
            kind = step["kind"]
            role = {"assistant": "assistant", "final": "assistant", "error": "error", "question": "question", "answer": "user", "watch_hit": "info"}.get(kind, "info")
            b = Bubble(role)
            b.set_max_width(self.max_w)
            if role == "user":
                b.edit_requested.connect(lambda: self.edit_requested.emit(self.task_id, user_text((self.task or {}).get("request"))))
            b.retry_requested.connect(lambda: self.retry_requested.emit(self.task_id))
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
        steps = task.get("steps") or []
        tools = [s for s in steps if s["kind"] in ("tool_call", "compact")]
        if tools:
            self.chip.set_steps(tools, show_raw)
            if self.chip.isHidden():
                self.chip.show()
        else:
            self.chip.hide()
        texts = [s for s in steps if s["kind"] in ("assistant", "final", "error", "question", "answer", "watch_hit")]
        # the final step repeats the last assistant text: show one bubble, not two
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
            b = self._bubble(s, animate and not first)
            if s["kind"] == "question":
                body = s.get("output") or s.get("input") or ""
                pending = task.get("status") == "waiting_user"
                b.set_text(body + ("\n\n*Answer in the box below.*" if pending else ""))
            elif s["kind"] == "watch_hit":
                b.set_text("**Background watch fired** (%s)\n\n%s" % (s.get("name") or "watch", step_output_summary(s.get("output"))[:800]))
            elif s["kind"] == "error":
                b.set_text("**Something went wrong**\n\n" + (s.get("output") or s.get("input") or ""))
            elif s["kind"] == "answer":
                b.set_text(s.get("output") or "")
            else:
                b.set_text(s.get("output") or "")
        for sid in list(self.step_widgets):
            if sid not in seen:
                w = self.step_widgets.pop(sid)
                self.order.remove(sid)
                w.setParent(None)
                w.deleteLater()
        st = task.get("status")
        self.typing.setVisible(st in ("queued", "running"))
        label = {"queued": "Queued…", "waiting_approval": "Waiting for your approval", "waiting_user": "Waiting for your answer"}.get(st, "")
        if st == "cancelled":
            label = "Stopped"
        elif st in ("done", "failed") and not shown:
            label = STATUS_TEXT.get(st, st)
        self.status_label.setText(label)
        self.status_label.setVisible(bool(label))


class ConversationView(QScrollArea):
    """The chat body. Updates turns in place; keeps the user's scroll position unless they were at the bottom."""
    edit_requested = pyqtSignal(int, str)
    retry_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName("chatBody")
        self.vl = QVBoxLayout(self.container)
        self.vl.setContentsMargins(SP2 + 8, SP2, SP2 + 8, SP2)
        self.vl.setSpacing(SP2 + 4)
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
            t.setParent(None)
            t.deleteLater()
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
                    w.setParent(None)
                    w.deleteLater()
            for i, t in enumerate(tasks):
                turn = self.turns.get(t["id"])
                if turn is None:
                    turn = Turn(t["id"])
                    turn.set_max_width(self._bubble_width())
                    turn.edit_requested.connect(self.edit_requested.emit)
                    turn.retry_requested.connect(self.retry_requested.emit)
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
        return int(max(240, self.viewport().width() * 0.72))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self._bubble_width()
        for t in self.turns.values():
            t.set_max_width(w)


class ConvRow(QWidget):
    """Sidebar row: title, subtitle, delete icon on hover."""
    delete_requested = pyqtSignal(int)

    def __init__(self, root_id, parent=None):
        super().__init__(parent)
        self.root_id = root_id
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 6, 8)
        h.setSpacing(6)
        v = QVBoxLayout()
        v.setSpacing(2)
        v.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setObjectName("rowTitle")
        self.sub = QLabel()
        self.sub.setObjectName("rowSub")
        v.addWidget(self.title)
        v.addWidget(self.sub)
        h.addLayout(v, 1)
        self.delete = IconButton("delete", "Delete this chat", size=26, icon_size=15)
        sp = self.delete.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.delete.setSizePolicy(sp)
        self.delete.clicked.connect(lambda: self.delete_requested.emit(self.root_id))
        self.delete.hide()
        h.addWidget(self.delete, 0, Qt.AlignmentFlag.AlignVCenter)
        self._full_title = ""

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
        self.sub.setText(" · ".join(parts))
        self.setToolTip(title)

    def _elide(self):
        fm = QFontMetrics(self.title.font())
        self.title.setText(fm.elidedText(self._full_title, Qt.TextElideMode.ElideRight, max(60, self.width() - 60)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


class Sidebar(QFrame):
    selected = pyqtSignal(object)            # root id or None
    new_chat = pyqtSignal()
    delete_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(296)
        v = QVBoxLayout(self)
        v.setContentsMargins(SP, SP, SP, SP)
        v.setSpacing(10)
        self.new_btn = QPushButton("New chat")
        self.new_btn.setObjectName("newChat")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setIconSize(QSize(18, 18))
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
        self.convs = []
        self.rows = {}          # root id -> ConvRow
        self.keys = []          # current ordered keys ("h:Today" / "c:<root>")
        self.current_root = None
        self.refresh_icons()

    def refresh_icons(self):
        col = QColor(self.palette().color(QPalette.ColorRole.Text))
        col.setAlphaF(0.85)
        self.new_btn.setIcon(glyph_icon("add", col, 18))
        muted = QColor(col)
        muted.setAlphaF(0.55)
        self.search_icon.setPixmap(glyph_pixmap("search", muted, 18))
        self.search_icon.move(11, (self.search.sizeHint().height() - 18) // 2 + 1)

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
            lab = QLabel(val.upper())
            lab.setObjectName("groupHeader")
            it.setSizeHint(QSize(0, 30))
            self.list.setItemWidget(it, lab)
        else:
            row = ConvRow(val["root"]["id"])
            row.delete_requested.connect(self.delete_requested.emit)
            it.setData(Qt.ItemDataRole.UserRole, val["root"]["id"])
            it.setSizeHint(QSize(0, 56))
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


# ----------------------------------------------------------------------------- settings
class SettingsDialog(RoundedDialog):
    def __init__(self, parent, settings):
        super().__init__(parent, APP_NAME + " — Settings", "", "Save", "Cancel", radius=R_POPUP, width=600)
        s = settings
        self.s = s
        tabs = QTabWidget()
        self.body.addWidget(tabs)
        # --- General
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        self.mode = QComboBox()
        for m, lab in (("ask", "Ask — approve every risky step"), ("auto", "Auto — ask only for critical steps"), ("bypass", "Bypass — never ask")):
            self.mode.addItem(lab, m)
        self.mode.setCurrentIndex(max(0, ["ask", "auto", "bypass"].index(s.get("mode", "auto")) if s.get("mode", "auto") in ("ask", "auto", "bypass") else 1))
        f.addRow("Permission mode", self.mode)
        self.max_turns = QLineEdit(str(s.get("agent.max_turns", "60")))
        f.addRow("Max steps per task", self.max_turns)
        self.show_raw = QCheckBox("Show raw responses (commands and tool output)")
        self.show_raw.setChecked(str(s.get("ui.show_raw", "false")) == "true")
        self.show_raw.toggled.connect(self._raw_toggled)
        f.addRow("", self.show_raw)
        hint = QLabel("Off: the chat shows only friendly summaries like “Ran a command” or “Wrote a file”. On: the exact commands and their output are shown inside “Worked: N actions”.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow("", hint)
        tabs.addTab(w, "General")
        # --- AI provider
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        self.provider = QComboBox()
        self.prov_ids = ["claude", "openai", "gemini", "local"]
        for pid, label in (("claude", "Claude (Anthropic)"), ("openai", "OpenAI"), ("gemini", "Google Gemini"), ("local", "Local model (llama-server / OpenAI-compatible, offline)")):
            self.provider.addItem(label, pid)
        cur = s.get("provider", "claude")
        self.provider.setCurrentIndex(self.prov_ids.index(cur) if cur in self.prov_ids else 0)
        f.addRow("Active provider", self.provider)
        self.pfields = {}
        self.remove_keys = set()
        defaults = {"claude": ("claude-opus-5", None), "openai": ("gpt-4.1", "https://api.openai.com/v1"), "gemini": ("gemini-2.5-pro", "https://generativelanguage.googleapis.com/v1beta/openai"),
                    "local": ("local", "http://127.0.0.1:8080/v1")}
        for pid in self.prov_ids:
            model, url = defaults[pid]
            m = QLineEdit(s.get(pid + ".model", model))
            f.addRow("%s model" % pid.capitalize(), m)
            u = None
            if url:
                u = QLineEdit(s.get(pid + ".base_url", url))
                f.addRow("%s endpoint" % pid.capitalize(), u)
            k = QLineEdit()
            k.setEchoMode(QLineEdit.EchoMode.Password)
            stored = bool((s.get("secrets") or {}).get(pid + "_api_key"))
            k.setPlaceholderText("stored securely — paste to replace" if stored else ("optional" if pid == "local" else "API key (paste to set)"))
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            rl.addWidget(k, 1)
            rm = IconButton("delete", "Remove the stored %s API key" % pid.capitalize(), size=30, icon_size=16)
            rm.setEnabled(stored)
            rm.clicked.connect(lambda _c=False, pid=pid, k=k, rm=rm: self._remove_key(pid, k, rm))
            rl.addWidget(rm, 0)
            f.addRow("%s API key" % pid.capitalize(), row)
            self.pfields[pid] = (m, u, k)
        note = QLabel("Keys are encrypted with systemd-creds, never displayed again, never sent anywhere except the provider you chose. Local model = fully offline.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        f.addRow("", note)
        tabs.addTab(w, "AI provider")
        # --- Mail
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        self.m = {}
        for key, label, default in (("mail.user", "Account (login / address)", ""), ("mail.from", "From address (optional)", ""), ("mail.imap_host", "IMAP host", ""), ("mail.imap_port", "IMAP port", "993"),
                                    ("mail.smtp_host", "SMTP host", ""), ("mail.smtp_port", "SMTP port", "587"), ("mail.smtp_security", "SMTP security (starttls/ssl/none)", "starttls"),
                                    ("mail.transport", "Transport (smtp / brevo)", "smtp"), ("mail.from_name", "Sender name (brevo)", "")):
            e = QLineEdit(str(s.get(key, default)))
            self.m[key] = e
            f.addRow(label, e)
        secrets = s.get("secrets") or {}
        self.mail_pw = QLineEdit()
        self.mail_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_pw.setPlaceholderText("stored" if secrets.get("mail_password") else "password or app password")
        f.addRow("Password", self.mail_pw)
        self.mail_api = QLineEdit()
        self.mail_api.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_api.setPlaceholderText("stored" if secrets.get("mail_api_key") else "Brevo API key (transport brevo)")
        f.addRow("API key", self.mail_api)
        tabs.addTab(w, "Mail")
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

    def _raw_toggled(self, on):
        if on and str(self.s.get("ui.show_raw", "false")) != "true":
            if not RoundedDialog.confirm(self, "Show raw responses?", "Chats will show the exact commands the agent runs and their full output, including file contents and anything printed by programs. Turn this on only if you want that level of detail.", "Show raw responses"):
                self.show_raw.blockSignals(True)
                self.show_raw.setChecked(False)
                self.show_raw.blockSignals(False)

    def _remove_key(self, pid, field, btn):
        if RoundedDialog.confirm(self, "Remove the %s API key?" % pid.capitalize(), "The stored key is deleted from this computer. The agent cannot use %s until you paste a new key." % pid.capitalize(), "Remove key"):
            self.remove_keys.add(pid)
            field.clear()
            field.setPlaceholderText("will be removed when you save")
            btn.setEnabled(False)

    def save(self):
        mode = self.mode.currentData()
        if mode == "bypass" and self.s.get("mode", "auto") != "bypass":
            if not RoundedDialog.confirm(self, "Switch to Bypass mode?", "In Bypass the agent never asks before acting — including administrator commands, deleting files and sending mail. Use it only for tasks you fully trust.", "Use Bypass"):
                return
        body = {"mode": mode, "agent.max_turns": self.max_turns.text().strip() or "60", "provider": self.provider.currentData(),
                "ui.show_raw": "true" if self.show_raw.isChecked() else "false"}
        for pid, (m, u, k) in self.pfields.items():
            body[pid + ".model"] = m.text()
            if u is not None:
                body[pid + ".base_url"] = u.text()
        for k, e in self.m.items():
            body[k] = e.text()
        try:
            r = api("PUT", "/settings", body)
            if not isinstance(r, dict) or r.get("error"):
                RoundedDialog.info(self, "Couldn't save the settings", str(r.get("error", "unknown error") if isinstance(r, dict) else r))
                return
            for pid in self.remove_keys:
                api("POST", "/secrets", {"name": pid + "_api_key", "value": ""})
            for pid, (m, u, k) in self.pfields.items():
                if k.text():
                    api("POST", "/secrets", {"name": pid + "_api_key", "value": k.text()})
            if self.mail_pw.text():
                api("POST", "/secrets", {"name": "mail_password", "value": self.mail_pw.text()})
            if self.mail_api.text():
                api("POST", "/secrets", {"name": "mail_api_key", "value": self.mail_api.text()})
        except AgentOffline as e:          # keep the dialog open: nothing was saved and the edits are still in the fields
            RoundedDialog.info(self, "Agent service offline", "Your changes were not saved. Start the service with:  systemctl --user start fabos-agent   and press Save again. (%s)" % str(e)[:120])
            return
        self.accept()


# ----------------------------------------------------------------------------- main window
class AIControls(QMainWindow):
    def __init__(self, focus_ask=False, prefill=""):
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
        self.editing = None             # task id being edited in the composer
        self.enlarged = False
        self._was_maximized = False
        self._first_paint = True
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(SP, 8, SP, SP)
        outer.setSpacing(8)
        # ---- header
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(6, 4, 6, 4)
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
        outer.addWidget(header)
        # ---- body: sidebar + main card
        body = QHBoxLayout()
        body.setSpacing(SP)
        self.sidebar = Sidebar()
        self.sidebar.selected.connect(self.select_conversation)
        self.sidebar.new_chat.connect(self.new_chat)
        self.sidebar.delete_requested.connect(self.delete_conversation)
        body.addWidget(self.sidebar)
        self.main = QFrame()
        self.main.setObjectName("card")
        ml = QVBoxLayout(self.main)
        ml.setContentsMargins(SP, SP, SP, SP)
        ml.setSpacing(SP)
        self.offline_label = QLabel()
        self.offline_label.setObjectName("offline")
        self.offline_label.setWordWrap(True)
        self.offline_label.hide()
        ml.addWidget(self.offline_label)
        self.stack = QStackedWidget()
        # empty state
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        self.greeting_icon = QLabel()
        self.greeting_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.greeting_icon)
        g = QLabel("What should I do for you?")
        g.setObjectName("greeting")
        g.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(g)
        hint = QLabel("Ask in plain language — open apps, write files and code, send mail, watch for replies.\nEvery step follows your permission mode; risky ones ask first.")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        el.addWidget(hint)
        el.addStretch(1)
        self.stack.addWidget(empty)
        self.view = ConversationView()
        self.view.edit_requested.connect(self.start_edit)
        self.view.retry_requested.connect(self.retry_task)
        self.stack.addWidget(self.view)
        ml.addWidget(self.stack, 1)
        # composer
        self.composer = QFrame()
        self.composer.setObjectName("composer")
        self.composer.setProperty("focused", False)
        cl = QHBoxLayout(self.composer)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(6)
        self.mode_btn = IconButton("shield", "Permission mode", size=36)
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
        cl.addWidget(self.mode_btn)
        self.ask = QLineEdit()
        self.ask.setObjectName("ask")
        self.ask.setPlaceholderText("Ask me to do anything…")
        self.ask.returnPressed.connect(lambda: self.submit(source="enter"))
        self.ask.installEventFilter(self)
        cl.addWidget(self.ask, 1)
        self.cancel_edit_btn = IconButton("close", "Cancel editing", size=32, icon_size=16)
        self.cancel_edit_btn.clicked.connect(self.cancel_edit)
        self.cancel_edit_btn.hide()
        cl.addWidget(self.cancel_edit_btn)
        self.send_btn = IconButton("send", "Send", size=36, accent=True, icon_size=18)
        self.send_btn.clicked.connect(self.submit)
        cl.addWidget(self.send_btn)
        ml.addWidget(self.composer)
        body.addWidget(self.main, 1)
        outer.addLayout(body, 1)
        # timers
        self.list_timer = QTimer(self)
        self.list_timer.timeout.connect(self.refresh_list)
        self.list_timer.start(POLL_LIST_MS)
        self.thread_timer = QTimer(self)
        self.thread_timer.timeout.connect(self.refresh_thread)
        self.thread_timer.start(POLL_THREAD_MS)
        self.apply_style()
        self.refresh_list()
        self.update_composer()
        if prefill:
            self.ask.setText(prefill)
            self.ask.setCursorPosition(len(prefill))
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
        for b in self.findChildren(IconButton):
            b.refresh_icon()
        self.sidebar.refresh_icons()

    def event(self, e):
        # The system colour scheme changed (dark <-> light): Qt delivers ApplicationPaletteChange to event() of each
        # top-level widget — it never reaches changeEvent() — so the palette-derived stylesheet is rebuilt from here.
        if e.type() == QEvent.Type.ApplicationPaletteChange:
            self._restyle.start()
        return super().event(e)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.PaletteChange:      # also follows an app palette change; apply_style's signature guard keeps it idempotent
            self._restyle.start()
        super().changeEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.ask and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.composer.setProperty("focused", e.type() == QEvent.Type.FocusIn)
            self.composer.style().unpolish(self.composer)
            self.composer.style().polish(self.composer)
        if obj is self.ask and e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape and self.editing is not None:
            self.cancel_edit()
            return True
        return super().eventFilter(obj, e)

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

    def latest_task(self):
        c = self.convs.get(self.current_root)
        return c["tasks"][-1] if c else None

    # ---- polling
    def refresh_list(self):
        try:
            st = api("GET", "/status")
            tasks = api("GET", "/tasks?limit=300")
            pend = api("GET", "/approvals/pending")
        except AgentOffline as e:
            self.set_offline(True, str(e))
            return
        self.set_offline(False)
        if isinstance(st, dict) and "mode" in st:
            self.status = st
            self.update_header()
        if isinstance(tasks, list):
            self.tasks = tasks
            convs = self._build_conversations()
            self.sidebar.set_conversations(convs)
            if self.current_root is not None and self.current_root not in self.convs:
                self.new_chat()
        self.handle_approvals(pend if isinstance(pend, list) else [])
        self.update_composer()

    def refresh_thread(self, force=False):
        if self.offline or self.current_root is None or self.current_root not in self.convs:
            return
        conv = self.convs[self.current_root]
        details = []
        try:
            for t in conv["tasks"]:
                cached = self.details.get(t["id"])
                if force or cached is None or t["status"] in ACTIVE or cached.get("updated") != t.get("updated") or cached.get("status") != t.get("status"):
                    d = api("GET", "/tasks/%d" % t["id"])
                    if isinstance(d, dict) and "id" in d:
                        self.details[t["id"]] = d
                        cached = d
                        # keep the list entry in step with the freshest status so the composer / sidebar follow immediately
                        t["status"], t["updated"] = d["status"], d["updated"]
                if cached:
                    details.append(cached)
        except AgentOffline as e:
            self.set_offline(True, str(e))
            return
        self.view.set_thread(details, self.show_raw)
        self.update_composer()

    def set_offline(self, off, why=""):
        if off != self.offline:
            self.offline = off
            self.offline_label.setVisible(off)
            self.update_composer()
        if off:
            self.offline_label.setText("Can't reach the agent service. Start it with:  systemctl --user start fabos-agent   (%s)" % why[:120])
            self.subtitle.setText("Agent service offline")

    def update_header(self):
        st = self.status
        prov = PROVIDER_LABELS.get(st.get("provider"), st.get("provider") or "")
        parts = [prov, "%s mode" % (st.get("mode") or "auto")]
        if not st.get("provider_ready", True):
            parts.append("provider not configured — open Settings")
        counts = st.get("tasks") or {}
        running = sum(counts.get(k, 0) for k in ACTIVE)
        if running:
            parts.append("%d active" % running)
        if not st.get("ai_enabled", True):
            parts = ["System-Wide AI is off"] + parts[1:]
        self.subtitle.setText(" · ".join(p for p in parts if p))
        self.ai_switch.blockSignals(True)
        self.ai_switch.setChecked(bool(st.get("ai_enabled", True)))
        self.ai_switch.blockSignals(False)
        mode = st.get("mode") or "auto"
        for m, act in self.mode_actions.items():
            act.setChecked(m == mode)
        self.mode_btn.setToolTip("Permission mode: %s" % mode)
        raw = str(st.get("ui_show_raw", "")) if "ui_show_raw" in st else None
        if raw is None:
            try:
                raw = str(api("GET", "/settings").get("ui.show_raw", "false"))
            except AgentOffline:
                raw = "false"
        new_raw = raw == "true"
        if new_raw != self.show_raw:
            self.show_raw = new_raw
            self.refresh_thread(force=True)

    def update_composer(self):
        latest = self.latest_task()
        busy = bool(latest and latest["status"] in ACTIVE)
        waiting_user = bool(latest and latest["status"] == "waiting_user")
        on = bool(self.status.get("ai_enabled", True)) and not self.offline
        if self.editing is not None:
            self.send_btn.set_glyph("check", "Send the edited request (replaces the old one)")
            self.ask.setPlaceholderText("Edit your request…")
        elif busy and not waiting_user:
            self.send_btn.set_glyph("stop", "Stop this task")
            self.ask.setPlaceholderText("The agent is working… type your follow-up now, send it when it finishes")
        elif waiting_user:
            self.send_btn.set_glyph("send", "Send your answer")
            self.ask.setPlaceholderText("Answer the agent's question…")
        else:
            self.send_btn.set_glyph("send", "Send")
            self.ask.setPlaceholderText("Ask me to do anything…" if self.current_root is None else "Follow up in this chat…")
        if not on:
            self.ask.setPlaceholderText("Agent service offline" if self.offline else "System-Wide AI is off — turn it on to give the agent tasks")
        self.ask.setEnabled(on)
        self.send_btn.setEnabled(on)
        self.cancel_edit_btn.setVisible(self.editing is not None)

    # ---- actions
    def submit(self, _checked=False, source="button"):
        text = self.ask.text().strip()
        latest = self.latest_task()
        busy = bool(latest and latest["status"] in ACTIVE and latest["status"] != "waiting_user")
        if busy and source == "enter" and self.editing is None:
            return           # Enter while the agent works keeps the typed follow-up; only the STOP button stops the task
        try:
            if self.editing is not None:
                if not text:
                    return
                old = self.details.get(self.editing) or self.by_id.get(self.editing) or {}
                title = old.get("title") or ""
                if not title.startswith(SUPERSEDED):
                    api("PATCH", "/tasks/%d" % self.editing, {"title": (SUPERSEDED + title)[:80]})
                # the edited version threads into the SAME chat — also when the edited turn is the chat's root — so the chat
                # keeps its follow-ups and no look-alike duplicate appears; the old turn stays, dimmed as superseded, and
                # the daemon leaves superseded turns out of the follow-up context
                parent = self.current_root or old.get("parent_id") or self.editing
                body = {"request": text, "parent_id": parent}
                r = api("POST", "/tasks", body)
                self.editing = None
                self.ask.clear()
                self._after_create(r, parent)
                return
            if latest and latest["status"] in ACTIVE and latest["status"] != "waiting_user":
                if RoundedDialog.confirm(self, "Stop this task?", "The agent stops what it is doing right now. Anything already done (files written, mail sent) stays as it is.", "Stop"):
                    api("POST", "/tasks/%d/cancel" % latest["id"])
                    self.refresh_thread(force=True)
                return
            if not text:
                return
            if latest and latest["status"] == "waiting_user":
                api("POST", "/tasks/%d/answer" % latest["id"], {"text": text})
                self.ask.clear()
                self.view.stick = True
                self.refresh_thread(force=True)
                return
            body = {"request": text}
            if self.current_root is not None:
                body["parent_id"] = self.current_root
            r = api("POST", "/tasks", body)
            self.ask.clear()
            self._after_create(r, self.current_root)
        except AgentOffline as e:
            self.set_offline(True, str(e))

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
            self.ask.clear()
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

    def start_edit(self, task_id, text):
        self.editing = task_id
        self.ask.setText(text)
        self.ask.setFocus()
        self.ask.setCursorPosition(len(text))
        self.update_composer()

    def cancel_edit(self):
        self.editing = None
        self.ask.clear()
        self.update_composer()

    def retry_task(self, task_id):
        try:
            r = api("POST", "/tasks/%d/retry" % task_id)
            self._after_create(r, self.current_root)
        except AgentOffline as e:
            self.set_offline(True, str(e))

    def delete_conversation(self, root_id):
        conv = self.convs.get(root_id)
        if not conv:
            return
        n = len(conv["tasks"])
        title = (user_text(conv["root"].get("request")) or conv["root"].get("title") or "")[:60]
        if not RoundedDialog.confirm(self, "Delete this chat?", "“%s” and its %d turn%s — including every recorded step — are removed from the history. Running tasks are stopped." % (title, n, "" if n == 1 else "s"), "Delete"):
            return
        try:
            for t in reversed(conv["tasks"]):
                api("DELETE", "/tasks/%d" % t["id"])
                self.details.pop(t["id"], None)
        except AgentOffline as e:
            self.set_offline(True, str(e))
            return
        if self.current_root == root_id:
            self.new_chat()
        self.refresh_list()

    def set_mode(self, mode):
        if mode == "bypass" and (self.status.get("mode") != "bypass"):
            if not RoundedDialog.confirm(self, "Switch to Bypass mode?", "In Bypass the agent never asks before acting — including administrator commands, deleting files and sending mail. Use it only for tasks you fully trust.", "Use Bypass"):
                self.update_header()
                return
        try:
            api("PUT", "/settings", {"mode": mode})
        except AgentOffline as e:
            self.set_offline(True, str(e))
        self.refresh_list()

    def toggle_ai(self, checked):
        if not checked:
            if not RoundedDialog.confirm(self, "Turn System-Wide AI off?", "The agent stops accepting tasks and pauses background watches until you turn it back on. Normal desktop use is unaffected.", "Turn off"):
                self.ai_switch.setChecked(True)
                return
        try:
            api("PUT", "/settings", {"ai.enabled": "true" if checked else "false"})
        except AgentOffline as e:
            self.set_offline(True, str(e))
        self.refresh_list()

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

    def open_settings(self):
        try:
            s = api("GET", "/settings")
        except AgentOffline as e:
            self.set_offline(True, str(e))
            return
        if not isinstance(s, dict) or "error" in s:
            return
        if SettingsDialog(self, s).exec():
            self.refresh_list()
            self.refresh_thread(force=True)

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
        try:
            api("POST", "/approvals/%d" % approval_id, {"decision": decision})
        except AgentOffline as e:
            self.set_offline(True, str(e))
        self.refresh_thread(force=True)


def open_task_arg(w, args):
    """--task ID: select the chat that contains task ID (follow-ups and retries hang under their root task)."""
    tid = int(args[0]) if args and str(args[0]).isdigit() else 0
    for _ in range(50):
        try:
            t = api("GET", "/tasks/%d" % tid) if tid else {}
        except AgentOffline:
            return
        if not t.get("id"):
            return
        if not t.get("parent_id"):
            break
        tid = int(t["parent_id"])
    w.select_conversation(tid)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(DESKTOP_ID)
    app.setFont(QFont("Inter", 10))
    pre = sys.argv[sys.argv.index("--prefill") + 1] if "--prefill" in sys.argv and len(sys.argv) > sys.argv.index("--prefill") + 1 else ""
    w = AIControls(focus_ask="--ask" in sys.argv, prefill=pre)
    w.show()
    if "--settings" in sys.argv:
        QTimer.singleShot(300, w.open_settings)   # straight to Settings (the AI provider tab is where keys go)
    if "--task" in sys.argv:                      # opened from the ask bar's inline panel: jump to that task's chat
        QTimer.singleShot(300, lambda: open_task_arg(w, sys.argv[sys.argv.index("--task") + 1:][:1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
