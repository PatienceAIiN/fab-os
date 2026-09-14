#!/usr/bin/env python3
"""Generate the Fab OS Plasma desktop theme: rounded, translucent surfaces for panels, popups, notifications,
tooltips and the system tray (everything Plasma draws through KSvg FrameSvg). Missing elements fall back to Breeze.

9-slice convention: center / top / bottom / left / right / topleft / topright / bottomleft / bottomright,
mask-* (clip region for blur + translucency), hint-*-margin rects whose size sets the content padding.
No shadow elements: QtSvg gradients rendered unpredictably, and Plasma/KWin already draw shadows.
"""
import json, os

INK = (15, 20, 32)  # #0F1420 tinted ink

# Roundness tokens (docs/design/BRANDING.md "Roundness"): popups/dialogs 24, panels/cards 20, tooltips 14.
# The window frame's 20 px title-bar corners come from brand/gen/aurorae_theme.py; controls (12) from the widget style.
RADIUS = {"popup": 24, "panel": 20, "tooltip": 14}


def _rgba(rgb, a):
    return "rgba(%d,%d,%d,%.3f)" % (rgb[0], rgb[1], rgb[2], a)


STYLE = ('<style type="text/css" id="current-color-scheme">.ColorScheme-Text{color:#E6EAF0;}.ColorScheme-Background{color:#0F1420;}'
         '.ColorScheme-ViewBackground{color:#171D2B;}.ColorScheme-Highlight{color:#6E9BFF;}.ColorScheme-ButtonBackground{color:#1F2737;}</style>')


def frame_svg(radius, alpha, margin, border="#2A313B", fill=INK):
    R, M = radius, margin
    c = "__SCHEME_BG__"   # replaced below with class-based currentColor so light/dark follow the system scheme
    # Corner pieces: R x R squares with the OUTER corner rounded (arc sweep chosen so the curve bulges outward).
    paths = {
        "topleft":     "M 0 %d A %d %d 0 0 1 %d 0 L %d %d L 0 %d Z" % (R, R, R, R, R, R, R),
        "topright":    "M %d 0 A %d %d 0 0 1 %d %d L %d %d L %d %d Z" % (2 * R, R, R, 3 * R, R, 3 * R, R, 2 * R, R),
        "bottomleft":  "M 0 %d L %d %d L %d %d A %d %d 0 0 1 0 %d Z" % (2 * R, R, 2 * R, R, 3 * R, R, R, 2 * R),
        "bottomright": "M %d %d L %d %d L %d %d A %d %d 0 0 1 %d %d Z" % (2 * R, 2 * R, 3 * R, 2 * R, 3 * R, 2 * R, R, R, 2 * R, 3 * R),
    }
    # fix bottomright: arc from (3R,2R) to (2R,3R)
    paths["bottomright"] = "M %d %d L %d %d A %d %d 0 0 1 %d %d L %d %d Z" % (2 * R, 2 * R, 3 * R, 2 * R, R, R, 2 * R, 3 * R, 2 * R, 3 * R)
    rects = {"top": (R, 0, R, R), "bottom": (R, 2 * R, R, R), "left": (0, R, R, R), "right": (2 * R, R, R, R), "center": (R, R, R, R)}

    def pieces(prefix, color, dx):
        out = ['<g transform="translate(%d,0)">' % dx]
        def attrs(col):
            return ('class="ColorScheme-Background" style="fill:currentColor;fill-opacity:%.3f"' % alpha) if col == "__SCHEME_BG__" else 'fill="%s"' % col
        for n, d in paths.items():
            out.append('<path id="%s%s" d="%s" %s/>' % (prefix, n, d, attrs(color)))
        for n, (x, y, w, h) in rects.items():
            out.append('<rect id="%s%s" x="%d" y="%d" width="%d" height="%d" %s/>' % (prefix, n, x, y, w, h, attrs(color)))
        out.append("</g>")
        return out
    body = pieces("", c, 0) + pieces("mask-", "#000000", 3 * R + 10)
    hx = 6 * R + 20
    hints = []
    for i, side in enumerate(("top", "bottom", "left", "right")):
        w, h = (1, M) if side in ("top", "bottom") else (M, 1)
        hints.append('<rect id="hint-%s-margin" x="%d" y="0" width="%d" height="%d" fill="none"/>' % (side, hx + i * 12, w, h))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"><defs>%s</defs>%s%s</svg>'
            % (hx + 60, 3 * R + 4, STYLE, "".join(body), "".join(hints)))


def write_theme(out, conf):
    root = os.path.join(out, "plasma-theme")
    files = {
        "dialogs/background.svg": frame_svg(RADIUS["popup"], 0.95, 12),
        "translucent/dialogs/background.svg": frame_svg(RADIUS["popup"], 0.84, 12),
        "opaque/dialogs/background.svg": frame_svg(RADIUS["popup"], 1.0, 12),
        "widgets/panel-background.svg": frame_svg(RADIUS["panel"], 0.93, 4),
        "translucent/widgets/panel-background.svg": frame_svg(RADIUS["panel"], 0.76, 4),
        "opaque/widgets/panel-background.svg": frame_svg(RADIUS["panel"], 1.0, 4),
        "widgets/tooltip.svg": frame_svg(RADIUS["tooltip"], 0.96, 8),
        "translucent/widgets/tooltip.svg": frame_svg(RADIUS["tooltip"], 0.9, 8),
        "widgets/background.svg": frame_svg(RADIUS["panel"], 0.93, 10),
        "translucent/widgets/background.svg": frame_svg(RADIUS["panel"], 0.82, 10),
    }
    for rel, svg in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(svg)
    meta = {"KPlugin": {"Authors": [{"Email": "support@patienceai.in", "Name": conf["VENDOR_NAME"]}],
                        "Description": "%s rounded, translucent Plasma surfaces" % conf["DISTRO_NAME"], "Id": "FabOS", "License": "Apache-2.0",
                        "Name": conf["DISTRO_NAME"], "Version": conf["DISTRO_VERSION"], "Website": conf["HOME_URL"]},
            "X-Plasma-API": "5.0", "X-Plasma-FallbackTheme": "default"}
    open(os.path.join(root, "metadata.json"), "w").write(json.dumps(meta, indent=2))
    print("plasma theme:", len(files), "svgs ->", root)


if __name__ == "__main__":
    import sys
    write_theme(sys.argv[1] if len(sys.argv) > 1 else "out", {"VENDOR_NAME": "Patience AI", "DISTRO_NAME": "Fab OS", "DISTRO_VERSION": "1.0", "HOME_URL": "https://fabos.patienceai.in/"})
