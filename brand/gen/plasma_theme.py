#!/usr/bin/env python3
"""Generate the Fab OS Plasma desktop theme: rounded, translucent surfaces for panels, popups, notifications,
tooltips and the system tray (everything Plasma draws through KSvg FrameSvg). Missing elements fall back to Breeze.

Element naming follows Plasma's 9-slice convention: center/top/bottom/left/right/<corner>, mask-* (clip for
blur/translucency), shadow-* (soft outer shadow) and hint-*-margin rects whose size sets the content padding.
QtSvg has no filter support, so shadows are gradients, not blurs.
"""
import json, os

INK = (14, 17, 22)        # #0E1116 base surface
BORDER = "#3A4557"


def _rgba(rgb, a):
    return "rgba(%d,%d,%d,%.3f)" % (rgb[0], rgb[1], rgb[2], a)


def frame_svg(radius, alpha, margin, shadow, fill=INK, border=BORDER):
    R, S, M = radius, shadow, margin
    W = 3 * R
    p = []  # svg body
    fillc = _rgba(fill, alpha)

    def corner(name, x, y, kind):
        # kind: tl, tr, bl, br — square with one rounded outer corner
        d = {"tl": "M%d,%d h%d v%d h-%d v-%d a%d,%d 0 0 1 %d,-%d z" % (x + R, y, 0, R, R, 0, R, R, R, R),
             "tr": "M%d,%d a%d,%d 0 0 1 %d,%d v%d h-%d v-%d z" % (x, y, R, R, R, R, 0, R, R),
             "bl": "M%d,%d h%d v%d a%d,%d 0 0 1 -%d,-%d z" % (x, y, R, R, R, R, R, R),
             "br": "M%d,%d h%d v%d a%d,%d 0 0 1 -%d,%d h-%d z" % (x, y, R, 0, R, R, R, R, 0)}[kind]
        return d
    # Proper corner paths (outer corner rounded, inner edges straight)
    paths = {
        "topleft": "M{x},{y2} A{R},{R} 0 0 1 {x2},{y} L{x2},{y2} Z".format(x=0, y=0, x2=R, y2=R, R=R),
        "topright": "M{x},{y} A{R},{R} 0 0 1 {x2},{y2} L{x},{y2} Z".format(x=2 * R, y=0, x2=3 * R, y2=R, R=R),
        "bottomleft": "M{x},{y} L{x2},{y} L{x2},{y2} A{R},{R} 0 0 1 {x},{y} Z".format(x=0, y=2 * R, x2=R, y2=3 * R, R=R),
        "bottomright": "M{x},{y} L{x2},{y} A{R},{R} 0 0 1 {x},{y2} Z".format(x=2 * R, y=2 * R, x2=3 * R, y2=3 * R, R=R),
    }
    rects = {"top": (R, 0, R, R), "bottom": (R, 2 * R, R, R), "left": (0, R, R, R), "right": (2 * R, R, R, R), "center": (R, R, R, R)}

    def piece(prefix, color, stroke=None):
        out = []
        for n, d in paths.items():
            out.append('<path id="%s%s" d="%s" fill="%s"%s/>' % (prefix, n, d, color, (' stroke="%s" stroke-width="1"' % stroke) if stroke else ""))
        for n, (x, y, w, h) in rects.items():
            out.append('<rect id="%s%s" x="%d" y="%d" width="%d" height="%d" fill="%s"/>' % (prefix, n, x, y, w, h, color))
        return out
    # base surface (offset by shadow size so shadow pieces can sit around it)
    body = ['<g transform="translate(%d,%d)">' % (S, S)] + piece("", fillc) + ['</g>']
    # 1px inner border ring as an overlay path around the whole frame (drawn on the corner/edge pieces via stroke would double; keep subtle)
    # masks (black, same geometry)
    body += ['<g transform="translate(%d,%d)">' % (S + W + 10, S)] + piece("mask-", "#000") + ['</g>']
    # shadow pieces: gradients fading outward
    sx = 0
    sy = S + 2 * W + 20
    defs = []
    for n, (gx1, gy1, gx2, gy2) in {"top": (0, 1, 0, 0), "bottom": (0, 0, 0, 1), "left": (1, 0, 0, 0), "right": (0, 0, 1, 0)}.items():
        defs.append('<linearGradient id="sg-%s" x1="%s" y1="%s" x2="%s" y2="%s"><stop offset="0" stop-color="#000" stop-opacity="0.45"/><stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>' % (n, gx1, gy1, gx2, gy2))
    for n, (cx, cy) in {"topleft": (1, 1), "topright": (0, 1), "bottomleft": (1, 0), "bottomright": (0, 0)}.items():
        defs.append('<radialGradient id="sg-%s" cx="%s" cy="%s" r="1"><stop offset="0" stop-color="#000" stop-opacity="0.45"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>' % (n, cx, cy))
    shadow = []
    geo = {"topleft": (0, 0), "top": (S, 0), "topright": (S + R, 0), "left": (0, S), "center": (S, S), "right": (S + R, S), "bottomleft": (0, S + R), "bottom": (S, S + R), "bottomright": (S + R, S + R)}
    for n, (x, y) in geo.items():
        w = S if n in ("topleft", "left", "bottomleft", "topright", "right", "bottomright") else R
        h = S if n in ("topleft", "top", "topright", "bottomleft", "bottom", "bottomright") else R
        fill_ = "url(#sg-%s)" % n if n != "center" else "rgba(0,0,0,0.45)"
        shadow.append('<rect id="shadow-%s" x="%d" y="%d" width="%d" height="%d" fill="%s"/>' % (n, sx + x, sy + y, w, h, fill_))
    hints = []
    hx = S + 2 * W + 40
    for i, side in enumerate(("top", "bottom", "left", "right")):
        w, h = (1, M) if side in ("top", "bottom") else (M, 1)
        hints.append('<rect id="hint-%s-margin" x="%d" y="%d" width="%d" height="%d" fill="none"/>' % (side, hx + i * 30, 0, w, h))
        sw, sh = (1, S) if side in ("top", "bottom") else (S, 1)
        hints.append('<rect id="shadow-hint-%s-margin" x="%d" y="%d" width="%d" height="%d" fill="none"/>' % (side, hx + i * 30, 40, sw, sh))
        hints.append('<rect id="hint-%s-inset" x="%d" y="%d" width="%d" height="%d" fill="none"/>' % (side, hx + i * 30, 80, sw, sh))
    total = max(hx + 140, sx + 2 * S + R + 10)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"><defs>%s</defs>%s%s%s</svg>' % (
        total, sy + 2 * S + R + 10, "".join(defs), "".join(body), "".join(shadow), "".join(hints))
    return svg


def write_theme(out, conf):
    name = "FabOS"
    root = os.path.join(out, "plasma-theme")
    files = {
        "dialogs/background.svg": frame_svg(18, 0.94, 14, 26),
        "translucent/dialogs/background.svg": frame_svg(18, 0.80, 14, 26),
        "opaque/dialogs/background.svg": frame_svg(18, 1.0, 14, 26),
        "widgets/panel-background.svg": frame_svg(16, 0.92, 6, 18),
        "translucent/widgets/panel-background.svg": frame_svg(16, 0.72, 6, 18),
        "opaque/widgets/panel-background.svg": frame_svg(16, 1.0, 6, 18),
        "widgets/tooltip.svg": frame_svg(12, 0.96, 10, 16),
        "translucent/widgets/tooltip.svg": frame_svg(12, 0.86, 10, 16),
        "widgets/background.svg": frame_svg(16, 0.92, 12, 18),
        "translucent/widgets/background.svg": frame_svg(16, 0.78, 12, 18),
        "widgets/frame.svg": frame_svg(12, 0.6, 8, 0),
    }
    for rel, svg in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(svg)
    meta = {"KPlugin": {"Authors": [{"Email": "support@patienceai.in", "Name": conf["VENDOR_NAME"]}], "Description": "%s rounded, translucent Plasma surfaces" % conf["DISTRO_NAME"],
                        "Id": name, "License": "Apache-2.0", "Name": conf["DISTRO_NAME"], "Version": conf["DISTRO_VERSION"], "Website": conf["HOME_URL"]},
            "X-Plasma-API": "5.0", "X-Plasma-FallbackTheme": "breeze-dark"}
    open(os.path.join(root, "metadata.json"), "w").write(json.dumps(meta, indent=2))
    # colors: inherit the dark Breeze palette (kept in sync with kdeglobals ColorScheme=BreezeDark)
    print("plasma theme:", len(files), "svgs ->", root)


if __name__ == "__main__":
    import sys
    write_theme(sys.argv[1] if len(sys.argv) > 1 else "out", {"VENDOR_NAME": "Patience AI", "DISTRO_NAME": "Fab OS", "DISTRO_VERSION": "1.0", "HOME_URL": "https://patienceai.in/fabos"})
