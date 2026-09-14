#!/usr/bin/env python3
"""Pixel probes for the frames rendered by main.qml (400x300 FrameSvgItem; geometry from brand/gen/aurorae_theme.py: padding 28,
radius 20). Prints every probe and a final "RESULT PASS|FAIL" line. Pass conditions, per frame:
  notch pixels (inside the window box, outside the arc) alpha == 0        -> corners look rounded on the desktop
  title bar pixels alpha == 1                                              -> the bar itself is intact
  shadow just outside the straight edges alpha > 0.05                      -> the drop shadow still exists
  corner shadow tapers: < 40 % of the edge shadow just above the notch,    -> no hard shadow step where the notch begins
    < 0.10 at the box corner, >= 80 % of the edge value where the arc starts  (luminance <mask> on the corner L paths)
  client area alpha == 0                                                   -> nothing is painted under the client
"""
import sys
from PyQt6.QtGui import QImage

P, R, W, H = 28, 20, 400, 300
LW = P + R
ok = True
for f in sys.argv[1:]:
    im = QImage(f)
    if im.isNull():
        print("cannot read", f); ok = False; continue
    print(f, im.width(), im.height(), "alpha channel:", im.hasAlphaChannel())
    a = lambda x, y: im.pixelColor(x, y).alphaF()
    edge = a(W // 2, P - 1)                       # shadow strength just above the straight top edge (0.37 active / 0.19 inactive)
    probes = [
        ("notch top-left corner", (P + 1, P + 1), lambda v: v == 0.0),
        ("notch top-left inner", (P + 4, P + 4), lambda v: v == 0.0),
        ("notch top-right corner", (W - P - 2, P + 1), lambda v: v == 0.0),
        ("notch top-right inner", (W - P - 5, P + 4), lambda v: v == 0.0),
        ("title bar top edge", (W // 2, P + 1), lambda v: v >= 0.99),
        ("title bar middle", (W // 2, P + 18), lambda v: v >= 0.99),
        ("title bar left edge", (P + 1, P + 30), lambda v: v >= 0.99),
        ("arc inside (P+10,P+10)", (P + 10, P + 10), lambda v: v >= 0.99),
        ("shadow above top edge", (W // 2, P - 1), lambda v: v > 0.05),
        ("shadow left of edge", (P - 1, H // 2), lambda v: v > 0.05),
        ("taper: above the notch (TL)", (P + 4, P - 1), lambda v: v < 0.4 * edge),
        ("taper: above the notch (TR)", (W - P - 5, P - 1), lambda v: v < 0.4 * edge),
        ("taper: left of the notch (TL)", (P - 1, P + 4), lambda v: v < 0.4 * edge),
        ("taper: outside box corner (TL)", (P - 1, P - 1), lambda v: v < 0.10),
        ("taper: outside box corner (TR)", (W - P, P - 1), lambda v: v < 0.10),
        ("taper: full where the arc starts (TL)", (LW - 1, P - 1), lambda v: v >= 0.8 * edge),
        ("taper: full where the arc starts (TR)", (W - LW, P - 1), lambda v: v >= 0.8 * edge),
        ("client area", (W // 2, H // 2), lambda v: v == 0.0),
        ("shadow padding corner", (2, 2), lambda v: v < 0.05),
    ]
    for label, (x, y), cond in probes:
        v = a(x, y)
        good = cond(v)
        ok = ok and good
        print("  %-38s (%3d,%3d) alpha=%.2f  %s" % (label, x, y, v, "ok" if good else "BAD"))
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
