#!/usr/bin/env python3
"""Pixel probes for the frames rendered by main.qml (400x300 FrameSvgItem; geometry from brand/gen/aurorae_theme.py: padding 32,
radius 14 since ADR-0019 — it must equal the KWin effect's [Round-Corners] Size). Prints every probe and a final "RESULT PASS|FAIL".

Since 2026-09-16 (ADR-0019 amendment) the frame is FLAT: the shadow of a window is drawn by the KDE-Rounded-Corners effect alone,
and the frame only reserves the padding with a uniform alpha-1/255 "carrier" (the effect shades only pixels with alpha > 0 and only
inside the padding the decoration reserves). Pass conditions, per frame:
  every pixel of the padding ring (outside the window box) has 0 < alpha <= CARRIER_MAX   -> no shadow gradient anywhere, no hole
  every pixel of the two notches (inside the box, outside the arc) has 0 < alpha <= CARRIER_MAX -> the effect's shadow can cross the notch
  the padding is UNIFORM: max - min alpha over the ring <= 1/255                          -> no gradient, no step, no L-path remnant
  title bar pixels alpha == 1 (mid, edges, inside the arc)                                 -> the bar itself is intact
  client area alpha == 0                                                                    -> nothing is painted under the client
  a radius-14 arc: a pixel 2 px outside the arc on the corner diagonal is carrier, 2 px inside is opaque
"""
import math
import sys
from PyQt6.QtGui import QImage

P, R, W, H = 32, 14, 400, 300
LW = P + R
CARRIER_MAX = 2.5 / 255           # the carrier is 1/255; allow one rounding step
ok = True


def notch_pixels():
    """Integer pixels inside the window box, outside the radius-R arc of the two top corners, at least 1.5 px from the arc."""
    # arc centres (frame coordinates): TL (P+R, P+R) from path "M P TOPH V LW A R R 0 0 1 LW P"; TR (W-P-R, P+R) from
    # "M x0 P A R R 0 0 1 x0+R LW" with x0 = W - LW. Pixel centres are at +0.5.
    pts = []
    for cx, xs in ((P + R, range(P, P + R)), (W - P - R, range(W - P - R, W - P))):
        for x in xs:
            for y in range(P, P + R):
                if math.hypot(x + 0.5 - cx, y + 0.5 - (P + R)) > R + 1.5:
                    pts.append((x, y))
    return pts


for f in sys.argv[1:]:
    im = QImage(f)
    if im.isNull():
        print("cannot read", f); ok = False; continue
    print(f, im.width(), im.height(), "alpha channel:", im.hasAlphaChannel())
    a = lambda x, y: im.pixelColor(x, y).alphaF()
    # --- whole padding ring
    ring = [(x, y) for x in range(W) for y in range(H) if x < P or x >= W - P or y < P or y >= H - P]
    vals = [a(x, y) for x, y in ring]
    lo, hi = min(vals), max(vals)
    good = 0 < lo and hi <= CARRIER_MAX and (hi - lo) <= 1.01 / 255
    ok = ok and good
    print("  %-38s %6d px  alpha min=%.4f max=%.4f  %s" % ("padding ring: uniform carrier only", len(ring), lo, hi, "ok" if good else "BAD"))
    # --- the two notches
    npx = notch_pixels()
    nv = [a(x, y) for x, y in npx]
    good = 0 < min(nv) and max(nv) <= CARRIER_MAX
    ok = ok and good
    print("  %-38s %6d px  alpha min=%.4f max=%.4f  %s" % ("notches: carrier, not a hole", len(npx), min(nv), max(nv), "ok" if good else "BAD"))
    # --- point probes
    probes = [
        ("carrier above top edge", (W // 2, P - 1), lambda v: 0 < v <= CARRIER_MAX),
        ("carrier left of edge", (P - 1, H // 2), lambda v: 0 < v <= CARRIER_MAX),
        ("carrier below bottom edge", (W // 2, H - P), lambda v: 0 < v <= CARRIER_MAX),
        ("carrier at outer padding corner", (0, 0), lambda v: 0 < v <= CARRIER_MAX),
        ("carrier at box corner (TL)", (P - 1, P - 1), lambda v: 0 < v <= CARRIER_MAX),
        ("carrier at bottom-right box corner", (W - P, H - P), lambda v: 0 < v <= CARRIER_MAX),
        ("notch top-left corner", (P, P), lambda v: 0 < v <= CARRIER_MAX),
        ("notch top-right corner", (W - P - 1, P), lambda v: 0 < v <= CARRIER_MAX),
        ("arc: 2 px outside on the diagonal (TL)", (P + 1, P + 1), lambda v: 0 < v <= CARRIER_MAX),
        ("arc: 2 px inside on the diagonal (TL)", (P + 6, P + 6), lambda v: v >= 0.99),
        ("title bar top edge", (W // 2, P + 1), lambda v: v >= 0.99),
        ("title bar middle", (W // 2, P + 18), lambda v: v >= 0.99),
        ("title bar left edge", (P + 1, P + 30), lambda v: v >= 0.99),
        ("title bar right edge", (W - P - 2, P + 30), lambda v: v >= 0.99),
        ("arc start on the top edge (TL)", (P + R, P + 1), lambda v: v >= 0.99),
        ("client area", (W // 2, H // 2), lambda v: v == 0.0),
        ("client area under the side", (P + 2, H // 2), lambda v: v == 0.0),
    ]
    for label, (x, y), cond in probes:
        v = a(x, y)
        good = cond(v)
        ok = ok and good
        print("  %-38s (%3d,%3d) alpha=%.4f  %s" % (label, x, y, v, "ok" if good else "BAD"))
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
