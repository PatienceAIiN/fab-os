#!/usr/bin/env python3
"""Generate the Fab OS window decoration: an Aurorae SVG theme, rendered by KWin's Aurorae **v2** engine.

Plasma 6.6 ships two Aurorae plugins in kwin-style-aurorae: `org.kde.kwin.aurorae` (v1, QML; its ThemeProvider only
lists KPackage QML decorations, so an SVG theme never shows up in System Settings) and `org.kde.kwin.aurorae.v2`
(C++/QPainter over KSvg; its DecorationThemeProvider enumerates every `aurorae/themes/<dir>/metadata.desktop` and
lists the theme as `__aurorae__svg__<dir>` under that file's `Name=`). Fab OS selects v2 (kwinrc
`library=org.kde.kwin.aurorae.v2`), so "Fab OS" and "Fab OS Light" are visible and re-selectable in
Settings > Window Decorations; the generated `metadata.desktop` is what makes them appear (ADR-0010).

Aurorae draws the frame and the buttons through KSvg FrameSvg, so every SVG here follows the SYSTEM colour scheme:
the fills use the KSvg `current-color-scheme` stylesheet classes (`ColorScheme-HeaderBackground` for the title bar,
`ColorScheme-Text` for glyphs, `ColorScheme-Highlight` for hover, `ColorScheme-NegativeText` for the close hover)
and KSvg rewrites that stylesheet from the active KColorScheme at render time. The one thing Aurorae cannot take
from the scheme is the caption colour (`[General] ActiveTextColor` in `<theme>rc` is a fixed QColor in v2's
DecorationTheme; there is no [WM] lookup), so two theme directories are produced: `FabOS` (light caption, selected by the dark look-and-feel) and `FabOSLight` (dark
caption, selected by the light look-and-feel). The light variant only carries its rc + metadata; its SVGs are
symlinks to the dark variant's files. Both are listed in Settings > Window Decorations, which is the way back if a
user changes only the colour scheme and gets the wrong caption colour.

The output is generated offline and COMMITTED (packages/fabos-desktop/usr/share/aurorae/themes); the image build does
not run this script. After editing, re-run:  python3 brand/gen/aurorae_theme.py --out packages/fabos-desktop/usr/share/aurorae/themes

Geometry (1x, Aurorae scales with the button-size factor):
  radius 14 on the two top corners, title bar 36 px, buttons 28 px in a 36 px bar, 3 px glyph strokes,
  side/bottom borders 0 (BorderSize=None), 32 px padding on every side that carries NO shadow of its own (see below).

FrameSvg layout of decoration.svg: the corner elements are painted into (leftWidth x topHeight) rectangles where
leftWidth is the width of the `-left` element and topHeight the height of the `-top` element, so the rounded
corner (which needs R px INSIDE the window edge) only fits if `-left` is padding + radius wide. The part of each
element that lies under the client is never shown (KWin renders only the border quads + the shadow).

ONE shadow source (2026-09-16, ADR-0019 amendment; ISO 1.0-5 showed a rectangular shadow with a hard step "before the curve"):
  The shadow of every window is drawn by the KDE-Rounded-Corners KWin effect alone (kwinrc [Round-Corners]
  UseNativeDecorationShadows=false, ShadowSize=40, InactiveShadowSize=36). This frame paints NO shadow: no gradients, no
  masks, no shadow paths; the frame is flat. Until 1.0-5 the frame carried its own 28 px gradient shadow, built for a square
  window (L-shaped corner paths that faded to 0.06 at the box corner while the straight edges stayed at 0.37, and square
  bottom corners), and the effect kept it (`UseNativeDecorationShadows=true`: its `getNativeShadow` only re-interpolates the
  pixels within 2 px of the window box). Two shadow shapes on one window read as a rectangle behind the rounded frame.

  Why the padding and a 1/255 "carrier" fill stay (read in the effect's v0.10.0 sources, src/shaders/shapecorners_shadows.glsl
  and variables.glsl): the effect renders the window into a texture the size of KWin's expandedGeometry, i.e. the frame plus
  the decoration's shadow padding — `bool hasExpandedSize() { return windowTopLeft.x >= 1.0 && windowTopLeft.y >= 1.0; }` and
  `bool isDrawingShadows() { return hasExpandedSize() && (usesNativeShadows || shadowColor.a > 0.0); }` — so with no padding
  there is no room and no shadow at all; and `run()` begins with `if (tex.a == 0.0) { return tex; }`, so a fully transparent
  pixel is returned untouched and the effect's shadow is painted only where the decoration left alpha > 0. Aurorae v2
  (aurorae/v2/decoration.cpp, Plasma 6.6, `updateShadow()`) makes the KDecoration shadow from this frame with the window box
  cut out (`CompositionMode_DestinationOut` of `innerRect`) and `setPadding(m_theme->padding())`, and `paint()` paints the
  same frame offset by (-PaddingLeft, -PaddingTop) into the decoration, so the corner notches (inside the window box, outside
  the arc) are shown as drawn here. The frame therefore fills the padding ring AND the two notches with one uniform fill of
  alpha 1/255 (`fill-opacity:0.004`, black): invisible on its own (a 0.4 % darkening), but every pixel the effect must shade
  is non-zero, so its soft shadow is continuous across the notch and the box corner. Where the effect cannot run (KWin
  without OpenGL compositing) windows have this radius-14 title bar and no shadow.

  Padding 32 (was 28): the effect clamps its ShadowSize to the length of (PaddingLeft, PaddingTop) (Shader.cpp:
  `max_shadow_size = frameOffset.length()`), and the visible reach outside a straight edge is
  ShadowSize - R - sqrt(ShadowSize) at the top and the sides (the shadow centre sits sqrt(ShadowSize) inside) and
  ShadowSize - R at the bottom (`getCustomShadow`). Padding 32 gives a clamp of 45.25, room for any size up to 45; the shipped
  40 reaches 20 px at the top/sides and 26 px at the bottom (0.24 / 0.39 alpha at the edge with ActiveShadowAlpha=128) and is
  the plain background again 8 px out on every corner diagonal; inactive 36 reaches 16 / 22 px. The literal values 24 / 16
  that 1.0-5 wrote for the never-used custom shadow would give 5 px / none. Verified as KWin draws it: tests/corners-live-test.sh
  (KWin's virtual backend inside the image) and, on the booted VM, tests/corners-vm.sh.

Radius 14, not 20 (2026-09-15, ADR-0019): the four corners of every window are cut by the KDE-Rounded-Corners KWin
  effect (kwinrc [Round-Corners] Size=14, circular arcs: UseSquircleShape=false). The effect masks the whole frame
  (decoration included), so the visible top corner is the INTERSECTION of the effect's arc and this frame's arc; the two
  must be the same circle or a sliver of the effect's 1 px outline would show inside this frame's notch (or
  this frame's arc would show inside the effect's). R here therefore equals the effect's Size, and both are 14 — the
  "field" radius of the design scale (docs/design/BRANDING.md), smaller than the 24 of Plasma popups, which stay as they are.
  `mask-*` elements are deliberately absent: in Aurorae they only define KWin's blur region
  (`updateBlur()` -> `setBlurRegion`), never the window shape, and a blur region behind an opaque title bar would only cost
  GPU time.

Usage: aurorae_theme.py --out <dir>   (writes <dir>/FabOS and <dir>/FabOSLight)
"""
import argparse, os

P, R, TH = 32, 14, 36            # shadow padding (carrier only), corner radius (= the KWin effect's [Round-Corners] Size, ADR-0019), title-bar height
LW, TOPH, BH = P + R, P + TH, P  # frame border thicknesses: left/right, top, bottom
BW, BHT = 2 * LW + 8, TOPH + 8 + BH   # one 9-slice block: 100 x 108 (2*LW+8 x TOPH+8+BH)
BTN = 28                          # button element size
CARRIER = 'style="fill:#000;fill-opacity:0.004"'   # alpha 1/255: the KWin effect shades only pixels with alpha > 0 (see docstring)

STYLE = ('<style type="text/css" id="current-color-scheme">'
         '.ColorScheme-Text{color:#e6eaf0;}.ColorScheme-Background{color:#0f1420;}.ColorScheme-HeaderBackground{color:#0f1420;}'
         '.ColorScheme-Highlight{color:#6e9bff;}.ColorScheme-HighlightedText{color:#ffffff;}.ColorScheme-NegativeText{color:#ff6b6b;}'
         '</style>')


def cls(name, opacity=1.0, extra=""):
    o = "" if opacity >= 1 else ";fill-opacity:%.3f" % opacity
    return 'class="ColorScheme-%s" style="fill:currentColor%s%s"' % (name, o, extra)


# ---------- decoration.svg ----------
def frame_block(prefix, dx):
    """The nine `prefix-*` elements of one 9-slice frame, translated by dx. Active and inactive frames are identical: the
    effect's shadow (not this frame) is what differs between the two states."""
    yb = TOPH + 8                                   # y of the window's bottom edge (top of the bottom border)
    hb = cls("HeaderBackground")
    keep = 'style="fill:#000;fill-opacity:0"'      # invisible bounds keeper so element sizes are exact
    x0 = BW - LW                                    # left edge of the right column
    e = {}
    # Top corners: the carrier covers the whole element (padding ring + notch), the header arc is painted on top of it.
    e["topleft"] = ('<rect x="0" y="0" width="%d" height="%d" %s/>' % (LW, TOPH, CARRIER) +
                    '<path d="M%d %d V%d A%d %d 0 0 1 %d %d V%d Z" %s/>' % (P, TOPH, LW, R, R, LW, P, TOPH, hb))
    e["top"] = ('<rect x="%d" y="0" width="8" height="%d" %s/>' % (LW, P, CARRIER) +
                '<rect x="%d" y="%d" width="8" height="%d" %s/>' % (LW, P, TH, hb))
    e["topright"] = ('<rect x="%d" y="0" width="%d" height="%d" %s/>' % (x0, LW, TOPH, CARRIER) +
                     '<path d="M%d %d A%d %d 0 0 1 %d %d V%d H%d Z" %s/>' % (x0, P, R, R, x0 + R, LW, TOPH, x0, hb))
    # Sides: the carrier over the padding; the R px inside the window box stay fully transparent (BorderSize=None: under the client).
    e["left"] = ('<rect x="0" y="%d" width="%d" height="8" %s/>' % (TOPH, LW, keep) +
                 '<rect x="0" y="%d" width="%d" height="8" %s/>' % (TOPH, P, CARRIER))
    e["center"] = '<rect x="%d" y="%d" width="8" height="8" %s/>' % (LW, TOPH, keep)
    e["right"] = ('<rect x="%d" y="%d" width="%d" height="8" %s/>' % (x0, TOPH, LW, keep) +
                  '<rect x="%d" y="%d" width="%d" height="8" %s/>' % (BW - P, TOPH, P, CARRIER))
    # Bottom: carrier only (the frame has no bottom border; the effect rounds and shades the client's bottom corners).
    e["bottomleft"] = '<rect x="0" y="%d" width="%d" height="%d" %s/>' % (yb, LW, BH, CARRIER)
    e["bottom"] = '<rect x="%d" y="%d" width="8" height="%d" %s/>' % (LW, yb, BH, CARRIER)
    e["bottomright"] = '<rect x="%d" y="%d" width="%d" height="%d" %s/>' % (x0, yb, LW, BH, CARRIER)
    body = "".join('<g id="%s-%s">%s</g>' % (prefix, n, e[n]) for n in
                   ("topleft", "top", "topright", "left", "center", "right", "bottomleft", "bottom", "bottomright"))
    return '<g transform="translate(%d 0)">%s</g>' % (dx, body)


def decoration_svg():
    blocks = frame_block("decoration", 0) + frame_block("decoration-inactive", BW + 8)
    # maximized: square, only the center element is used (Aurorae disables the borders and stretches it)
    mx = 2 * (BW + 8)
    blocks += '<g id="decoration-maximized-center"><rect x="%d" y="0" width="8" height="8" %s/></g>' % (mx, cls("HeaderBackground"))
    blocks += '<g id="decoration-maximized-inactive-center"><rect x="%d" y="0" width="8" height="8" %s/></g>' % (mx + 16, cls("HeaderBackground"))
    w = mx + 32
    # <desc> is ignored by QtSvg/KSvg; it records the facts a reader of the SVG needs (tests/branding-check.sh greps "notch" and "carrier").
    desc = ('<desc>Fab OS window frame for the Aurorae v2 engine. Flat: this frame paints no shadow (no gradients, no masks, no '
            'filters); the shadow of every window comes from the KDE-Rounded-Corners KWin effect alone (kwinrc [Round-Corners] '
            'UseNativeDecorationShadows=false). The 32 px padding and the corner notches inside the window box carry one uniform '
            'alpha-1/255 carrier fill (fill-opacity 0.004), because the effect shades only pixels whose alpha is not 0 and only '
            'inside the padding the decoration reserves (shapecorners_shadows.glsl: hasExpandedSize, tex.a == 0.0 returns tex). '
            'Aurorae paints this frame offset by the padding into the decoration, so the notch is shown as drawn. No mask-* '
            'elements: in Aurorae they only set the blur region, never the window shape (brand/gen/aurorae_theme.py).</desc>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">%s<defs>%s</defs>%s</svg>\n'
            % (w, BHT, w, BHT, desc, STYLE, blocks))


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
    # Border* = 0 and kwinrc BorderSize=None (BorderSizeAuto=false) together give no side or bottom border: Aurorae v2's
    # DecorationTheme::borders() zeroes left/right/bottom for BorderSize::None regardless of these values, and these values
    # keep the frame borderless if a user ever picks another size in the KCM. Padding* is the shadow room described above.
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
    """metadata.desktop: Aurorae v2's DecorationThemeProvider requires the file and shows `Name=` in the decoration KCM
    (kwin-applywindowdecoration also uses it to recognise a theme directory); the other keys are informational."""
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
