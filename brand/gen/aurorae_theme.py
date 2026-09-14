#!/usr/bin/env python3
"""Generate the Fab OS window decoration: an Aurorae (KWin SVG decoration engine) theme.

Aurorae draws the frame and the buttons through KSvg FrameSvg, so every SVG here follows the SYSTEM colour scheme:
the fills use the KSvg `current-color-scheme` stylesheet classes (`ColorScheme-HeaderBackground` for the title bar,
`ColorScheme-Text` for glyphs, `ColorScheme-Highlight` for hover, `ColorScheme-NegativeText` for the close hover)
and KSvg rewrites that stylesheet from the active KColorScheme at render time. The one thing Aurorae cannot take
from the scheme is the caption colour (`[General] ActiveTextColor` in `<theme>rc` is a fixed colour), so two theme
directories are produced: `FabOS` (light caption, selected by the dark look-and-feel) and `FabOSLight` (dark
caption, selected by the light look-and-feel). The light variant only carries its rc + metadata; its SVGs are
symlinks to the dark variant's files.

Geometry (1x, Aurorae scales with the button-size factor):
  radius 20 on the two top corners, title bar 36 px, buttons 28 px in a 36 px bar, 3 px glyph strokes,
  shadow padding 28 px on every side (gradients only: QtSvg has no filters), side/bottom borders 0 (BorderSize=None).

FrameSvg layout of decoration.svg: the corner elements are painted into (leftWidth x topHeight) rectangles where
leftWidth is the width of the `-left` element and topHeight the height of the `-top` element, so the rounded
corner (which needs R px INSIDE the window edge) only fits if `-left` is padding + radius wide. The part of each
element that lies under the client is never shown (KWin renders only the border quads + the shadow).

Usage: aurorae_theme.py --out <dir>   (writes <dir>/FabOS and <dir>/FabOSLight)
"""
import argparse, os

P, R, TH = 28, 20, 36            # shadow padding, corner radius, title-bar height
LW, TOPH, BH = P + R, P + TH, P  # frame border thicknesses: left/right, top, bottom
BW, BHT = 2 * LW + 8, TOPH + 8 + BH   # one 9-slice block: 104 x 100
BTN = 28                          # button element size

STYLE = ('<style type="text/css" id="current-color-scheme">'
         '.ColorScheme-Text{color:#e6eaf0;}.ColorScheme-Background{color:#0f1420;}.ColorScheme-HeaderBackground{color:#0f1420;}'
         '.ColorScheme-Highlight{color:#6e9bff;}.ColorScheme-HighlightedText{color:#ffffff;}.ColorScheme-NegativeText{color:#ff6b6b;}'
         '</style>')


def cls(name, opacity=1.0, extra=""):
    o = "" if opacity >= 1 else ";fill-opacity:%.3f" % opacity
    return 'class="ColorScheme-%s" style="fill:currentColor%s%s"' % (name, o, extra)


# ---------- decoration.svg ----------
def stops(t0, a):
    """Shadow falloff from the window edge (offset t0) to the outer edge of the padding (offset 1)."""
    return "".join('<stop offset="%.4f" stop-color="#000" stop-opacity="%.3f"/>' % (t, v) for t, v in
                   ((t0, a), (t0 + 0.35 * (1 - t0), a * 0.42), (t0 + 0.7 * (1 - t0), a * 0.12), (1.0, 0.0)))


def gradients(s, a):
    """Gradient set for one prefix (suffix s, shadow strength a) in block-local coordinates."""
    g = []
    lin = lambda i, x1, y1, x2, y2, t0: g.append('<linearGradient id="%s%s" gradientUnits="userSpaceOnUse" x1="%d" y1="%d" x2="%d" y2="%d">%s</linearGradient>' % (i, s, x1, y1, x2, y2, stops(t0, a)))
    rad = lambda i, cx, cy, r, t0: g.append('<radialGradient id="%s%s" gradientUnits="userSpaceOnUse" cx="%d" cy="%d" r="%d">%s</radialGradient>' % (i, s, cx, cy, r, stops(t0, a)))
    lin("shL", P, 0, 0, 0, 0)                       # left band: strong at x=P, fades to x=0
    lin("shR", BW - P, 0, BW, 0, 0)                 # right band
    lin("shT", 0, P, 0, 0, 0)                       # top band
    lin("shB", 0, TOPH + 8, 0, BHT, 0)              # bottom band: strong at the window bottom edge
    rad("shTL", LW, LW, LW, R / float(LW))          # top-left: centred on the arc centre, window edge at R
    rad("shTR", BW - LW, LW, LW, R / float(LW))
    rad("shBL", P, TOPH + 8, P, 0)                  # bottom corners are square: centred on the window corner
    rad("shBR", BW - P, TOPH + 8, P, 0)
    return "".join(g)


def frame_block(prefix, s, dx):
    """The nine `prefix-*` elements of one 9-slice frame, translated by dx."""
    yb = TOPH + 8                                   # y of the window's bottom edge (top of the bottom border)
    hb = cls("HeaderBackground")
    keep = 'style="fill:#000;fill-opacity:0"'      # invisible bounds keeper so element sizes are exact
    e = {}
    e["topleft"] = ('<rect x="0" y="0" width="%d" height="%d" fill="url(#shTL%s)"/>' % (LW, LW, s) +
                    '<rect x="0" y="%d" width="%d" height="%d" fill="url(#shL%s)"/>' % (LW, P, TOPH - LW, s) +
                    '<path d="M%d %d V%d A%d %d 0 0 1 %d %d V%d Z" %s/>' % (P, TOPH, LW, R, R, LW, P, TOPH, hb))
    e["top"] = ('<rect x="%d" y="0" width="8" height="%d" fill="url(#shT%s)"/>' % (LW, P, s) +
                '<rect x="%d" y="%d" width="8" height="%d" %s/>' % (LW, P, TH, hb))
    x0 = BW - LW                                    # left edge of the right column
    e["topright"] = ('<rect x="%d" y="0" width="%d" height="%d" fill="url(#shTR%s)"/>' % (x0, LW, LW, s) +
                     '<rect x="%d" y="%d" width="%d" height="%d" fill="url(#shR%s)"/>' % (BW - P, LW, P, TOPH - LW, s) +
                     '<path d="M%d %d A%d %d 0 0 1 %d %d V%d H%d Z" %s/>' % (x0, P, R, R, x0 + R, LW, TOPH, x0, hb))
    e["left"] = ('<rect x="0" y="%d" width="%d" height="8" %s/>' % (TOPH, LW, keep) +
                 '<rect x="0" y="%d" width="%d" height="8" fill="url(#shL%s)"/>' % (TOPH, P, s))
    e["center"] = '<rect x="%d" y="%d" width="8" height="8" %s/>' % (LW, TOPH, keep)
    e["right"] = ('<rect x="%d" y="%d" width="%d" height="8" %s/>' % (x0, TOPH, LW, keep) +
                  '<rect x="%d" y="%d" width="%d" height="8" fill="url(#shR%s)"/>' % (BW - P, TOPH, P, s))
    e["bottomleft"] = ('<rect x="0" y="%d" width="%d" height="%d" fill="url(#shBL%s)"/>' % (yb, P, BH, s) +
                       '<rect x="%d" y="%d" width="%d" height="%d" fill="url(#shB%s)"/>' % (P, yb, R, BH, s))
    e["bottom"] = '<rect x="%d" y="%d" width="8" height="%d" fill="url(#shB%s)"/>' % (LW, yb, BH, s)
    e["bottomright"] = ('<rect x="%d" y="%d" width="%d" height="%d" fill="url(#shBR%s)"/>' % (BW - P, yb, P, BH, s) +
                        '<rect x="%d" y="%d" width="%d" height="%d" fill="url(#shB%s)"/>' % (x0, yb, R, BH, s))
    body = "".join('<g id="%s-%s">%s</g>' % (prefix, n, e[n]) for n in
                   ("topleft", "top", "topright", "left", "center", "right", "bottomleft", "bottom", "bottomright"))
    return '<g transform="translate(%d 0)">%s</g>' % (dx, body)


def decoration_svg():
    defs = STYLE + gradients("", 0.38) + gradients("i", 0.20)
    blocks = frame_block("decoration", "", 0) + frame_block("decoration-inactive", "i", BW + 8)
    # maximized: square, only the center element is used (Aurorae disables the borders and stretches it)
    mx = 2 * (BW + 8)
    blocks += '<g id="decoration-maximized-center"><rect x="%d" y="0" width="8" height="8" %s/></g>' % (mx, cls("HeaderBackground"))
    blocks += '<g id="decoration-maximized-inactive-center"><rect x="%d" y="0" width="8" height="8" %s/></g>' % (mx + 16, cls("HeaderBackground"))
    w = mx + 32
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d"><defs>%s</defs>%s</svg>\n'
            % (w, BHT, w, BHT, defs, blocks))


# ---------- buttons ----------
# glyphs: stroked paths in a 28-unit box, 3 px stroke, round caps/joins; bold and unmistakable at 1x.
GLYPHS = {
    "minimize": '<path d="M8.5 14H19.5"/>',                                                   # thick rounded bar
    "maximize": '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/>',                     # bold rounded square
    "restore": ('<path d="M11.5 8H17.5A2.5 2.5 0 0 1 20 10.5V16.5"/>'                         # back square (open)
                '<rect x="7.5" y="10.5" width="10" height="10" rx="2.5"/>'),                   # front square
    "close": '<path d="M9 9L19 19M19 9L9 19"/>',                                               # bold rounded X
}
STROKE = 'fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"'


def glyph(kind, color, opacity):
    o = "" if opacity >= 1 else ";stroke-opacity:%.3f" % opacity
    return '<g class="ColorScheme-%s" style="stroke:currentColor%s" %s>%s</g>' % (color, o, STROKE, GLYPHS[kind])


def disc(color, opacity):
    return '<circle cx="14" cy="14" r="13" %s/>' % cls(color, opacity)


def button_svg(kind):
    close = kind == "close"
    states = {
        "active": glyph(kind, "Text", 1.0),
        "inactive": glyph(kind, "Text", 0.55),
        "hover": (disc("NegativeText", 1.0) + glyph(kind, "HighlightedText", 1.0)) if close else (disc("Highlight", 0.22) + glyph(kind, "Highlight", 1.0)),
        "pressed": (disc("NegativeText", 0.8) + glyph(kind, "HighlightedText", 1.0)) if close else (disc("Highlight", 0.38) + glyph(kind, "Highlight", 1.0)),
        "deactivated": glyph(kind, "Text", 0.28),
    }
    states["hover-inactive"] = states["hover"]; states["pressed-inactive"] = states["pressed"]
    states["deactivated-inactive"] = glyph(kind, "Text", 0.22)
    order = ("active", "inactive", "hover", "hover-inactive", "pressed", "pressed-inactive", "deactivated", "deactivated-inactive")
    body = "".join('<g id="%s-center" transform="translate(%d 0)"><rect width="%d" height="%d" style="fill:#000;fill-opacity:0"/>%s</g>'
                   % (st, i * (BTN + 4), BTN, BTN, states[st]) for i, st in enumerate(order))
    w = len(order) * (BTN + 4) - 4
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d"><defs>%s</defs>%s</svg>\n'
            % (w, BTN, w, BTN, STYLE, body))


# ---------- rc + metadata ----------
def rc(active_text, inactive_text):
    return "\n".join([
        "[General]",
        "ActiveTextColor=%s" % active_text, "InactiveTextColor=%s" % inactive_text,
        "UseTextShadow=false", "TitleAlignment=Center", "TitleVerticalAlignment=Center", "Animation=150", "",
        "[Layout]",
        "BorderLeft=0", "BorderRight=0", "BorderBottom=0", "BorderTop=0",
        "TitleEdgeTop=0", "TitleEdgeBottom=0", "TitleEdgeLeft=10", "TitleEdgeRight=10",
        "TitleEdgeTopMaximized=0", "TitleEdgeBottomMaximized=0", "TitleEdgeLeftMaximized=10", "TitleEdgeRightMaximized=10",
        "TitleBorderLeft=8", "TitleBorderRight=8", "TitleHeight=%d" % TH,
        "ButtonWidth=%d" % BTN, "ButtonWidthMenu=%d" % BTN, "ButtonWidthAppMenu=%d" % BTN, "ButtonHeight=%d" % BTN,
        "ButtonSpacing=6", "ButtonMarginTop=%d" % ((TH - BTN) // 2), "ButtonMarginTopMaximized=%d" % ((TH - BTN) // 2), "ExplicitButtonSpacer=8",
        "PaddingLeft=%d" % P, "PaddingRight=%d" % P, "PaddingTop=%d" % P, "PaddingBottom=%d" % P, ""])


def metadata(name, comment):
    return "\n".join([
        "[Desktop Entry]", "Name=%s" % name, "Comment=%s" % comment,
        "X-KDE-PluginInfo-Author=@VENDOR_NAME@", "X-KDE-PluginInfo-Email=support@patienceai.in",
        "X-KDE-PluginInfo-Name=%s" % name.replace(" ", "").replace("@DISTRO_NAME@", "FabOS"), "X-KDE-PluginInfo-Version=@DISTRO_VERSION@",
        "X-KDE-PluginInfo-Website=@HOME_URL@", "X-KDE-PluginInfo-License=Apache-2.0", "X-KDE-PluginInfo-EnabledByDefault=true", ""])


def write_theme(out):
    dark = os.path.join(out, "FabOS"); light = os.path.join(out, "FabOSLight")
    os.makedirs(dark, exist_ok=True); os.makedirs(light, exist_ok=True)
    svgs = {"decoration.svg": decoration_svg()}
    for k in GLYPHS: svgs[k + ".svg"] = button_svg(k)
    for n, s in svgs.items():
        open(os.path.join(dark, n), "w").write(s)
        lp = os.path.join(light, n)
        if os.path.lexists(lp): os.remove(lp)
        os.symlink(os.path.join("..", "FabOS", n), lp)
    open(os.path.join(dark, "FabOSrc"), "w").write(rc("230,234,240", "138,148,166"))
    open(os.path.join(light, "FabOSLightrc"), "w").write(rc("22,23,26", "107,114,128"))
    open(os.path.join(dark, "metadata.desktop"), "w").write(metadata(
        "@DISTRO_NAME@", "@DISTRO_NAME@ window frame: rounded title bar, bold buttons, follows the system colour scheme (light caption for dark schemes)"))
    open(os.path.join(light, "metadata.desktop"), "w").write(metadata(
        "@DISTRO_NAME@ Light", "@DISTRO_NAME@ window frame with a dark caption for light colour schemes"))
    print("aurorae theme: %d svgs -> %s (+ FabOSLight variant)" % (len(svgs), dark))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True, help="directory that will contain FabOS/ and FabOSLight/")
    write_theme(ap.parse_args().out)
