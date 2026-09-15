#!/usr/bin/env python3
"""Pixel sampler for tests/corners-vm.sh (host side, Pillow): decides from two screenshots — the desktop with a window placed at a
known frame geometry, and the same desktop without it (reference) — whether the window has four rounded corners of the given
radius and ONE soft, rounded shadow (ADR-0019 + its 2026-09-16 amendment: the KDE-Rounded-Corners effect draws the shadow, the
Aurorae frame is flat), instead of the rectangular shadow the owner saw on 1.0-5 ("a shadow of a sharp edge like a rectangle;
before the curve a visible sharp edge").

  corners-sample.py <shot.png> <ref.png> <x> <y> <w> <h> <radius>      -> prints every value, "RESULT PASS|FAIL", exit 0/1
  corners-sample.py --selftest                                          -> synthetic screenshots: rounded+soft shadow must PASS,
                                                                           rectangular shadow and square corners must FAIL

dev(p) below is the RGB distance between the shot and the reference at pixel p: 0 = the background shows through untouched,
larger = more shadow (or window). Checks, each printed with its values:
  1. corners (per corner): the frame's corner pixel is NOT the window colour (distance > 8; a square corner gives exactly the
     window colour) and is closer to its diagonal outside neighbour than to the pixel `radius` px inside; for the top corners
     the inside pixel equals the title bar's mid-top pixel (the geometry landed). Threshold 8 is a noise floor, not a contrast
     requirement (next to a Fab Dark window the shadowed dark wallpaper can be within ~30 of the window colour).
  2. diagonal (per corner, k = 1..12 px outward from the box corner along the diagonal; the first 8 are the ones the owner's
     rectangle showed up in): a ROUND shadow is weaker on the diagonal than beside the straight edge at the same k (the diagonal
     point is farther from the arc), so dev(diag_k) <= dev(edge_k) + 8 for every k and, right at the corner (k = 1, 2), clearly so:
     dev(diag_k) <= 0.8 * dev(edge_k) + 4 — a square-cornered radial shadow gives the SAME value on both, which is the rectangle
     test proper; it reaches the background: dev(diag_12) <= 10 (the effect's bottom shadow is biased downwards and its last few
     percent die out between 8 and 12 px); and it is soft: adjacent steps <= 12 and never rising by more than 6.
  2b. "before the curve" (per top corner along the top edge and down each side, per bottom corner along the bottom edge, 2 px
     outside, t = 0..radius+8 from the box corner inward): one smooth ramp — adjacent steps <= 12 and no drop > 6 while moving
     away from the corner. The old frame's shadow stopped where the arc began, which showed as a step here.
  3. edges (per edge, midpoint, k = 2..6 px outside): a soft gradient — adjacent steps <= 12, never rising by more than 6.
     A hard step (the frame's old 0.37 band starting right at the edge, or a border line) shows as a jump.
  4. shadow present: at 2 px outside the bottom or the top edge dev >= 6 in at least one place (the effect's shadow exists at
     all; with UseNativeDecorationShadows=false and a flat frame it is the only shadow there is).
The self-test models the effect's getCustomShadow (ShadowSize 45, alpha 0.5) for the passing case and the 1.0-5 frame's
square-cornered 0.37 band for the failing one; a square window (no rounding) must fail check 1.
"""
import math
import sys

STEP_MAX, RISE_MAX, BG_MAX, DIAG_SLACK, PRESENT_MIN = 12, 6, 10, 8, 6


def dist(a, b):
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(a[:3], b[:3])))


def analyse(im, rf, x, y, w, h, r, out=print):
    """im, rf: PIL RGB images (shot, reference). (x, y, w, h): frame geometry. r: radius. Returns True when everything passes."""
    px, rp = im.getpixel, rf.getpixel
    dev = lambda p: dist(px(p), rp(p))
    ok = True
    corner = {"TL": (x, y), "TR": (x + w - 1, y), "BL": (x, y + h - 1), "BR": (x + w - 1, y + h - 1)}
    inside = {"TL": (x + r, y + r), "TR": (x + w - 1 - r, y + r), "BL": (x + r, y + h - 1 - r), "BR": (x + w - 1 - r, y + h - 1 - r)}
    outside = {"TL": (x - 2, y - 2), "TR": (x + w + 1, y - 2), "BL": (x - 2, y + h + 1), "BR": (x + w + 1, y + h + 1)}
    sign = {"TL": (-1, -1), "TR": (1, -1), "BL": (-1, 1), "BR": (1, 1)}
    top = px((x + w // 2, y + 2))
    out("title bar mid-top %s = %s" % ((x + w // 2, y + 2), top))
    # 1. corners
    for k in ("TL", "TR", "BL", "BR"):
        c, i, o = px(corner[k]), px(inside[k]), px(outside[k])
        good = dist(c, i) > 8 and dist(c, o) < dist(c, i)
        if dist(i, o) <= 8:
            out("note: %s inside and outside differ by only %.0f (window colour = shadowed background here), weak verdict" % (k, dist(i, o)))
        if k in ("TL", "TR"):
            good = good and dist(i, top) < 16
        ok = ok and good
        out("corner %s corner%s=%s inside%s=%s outside%s=%s ref=%s  d(corner,inside)=%.0f d(corner,outside)=%.0f  %s"
            % (k, corner[k], c, inside[k], i, outside[k], o, rp(corner[k]), dist(c, i), dist(c, o), "ok" if good else "BAD"))
    # edge midpoints, k px outside (k = 1..12; the edge checks use 2..6, the diagonal comparison uses 1..12)
    mid = {"top": lambda k: (x + w // 2, y - k), "bottom": lambda k: (x + w // 2, y + h - 1 + k),
           "left": lambda k: (x - k, y + h // 2), "right": lambda k: (x + w - 1 + k, y + h // 2)}
    edge_dev = {e: [dev(f(k)) for k in range(1, 13)] for e, f in mid.items()}

    def soft(vals, label, rise_max=RISE_MAX):
        steps = [abs(a - b) for a, b in zip(vals, vals[1:])]
        rises = [b - a for a, b in zip(vals, vals[1:])]
        good = max(steps) <= STEP_MAX and max(rises) <= rise_max
        out("%s dev=%s  max step %.0f (<= %d) max rise %.0f (<= %d)  %s" % (label, " ".join("%.0f" % v for v in vals), max(steps), STEP_MAX, max(rises), rise_max, "ok" if good else "BAD"))
        return good
    # 2. diagonals: 12 px outward from each box corner (the first 8 are the ones that must show the background through; the
    # effect's bottom shadow is biased downwards, so the tail to 12 is where the last few percent die out)
    for k in ("TL", "TR", "BL", "BR"):
        sx, sy = sign[k]; cx, cy = corner[k]
        d = [dev((cx + sx * j, cy + sy * j)) for j in range(1, 13)]
        ename = "top" if k in ("TL", "TR") else "bottom"; e = edge_dev[ename]
        weaker = all(dj <= ej + DIAG_SLACK for dj, ej in zip(d, e))
        # a ROUND shadow is clearly weaker on the diagonal right at the corner (the point is sqrt(2) farther from the arc than the
        # edge point is from the edge); a square-cornered radial shadow gives the same value on both -> the rectangle test proper
        rnd = all(dj <= 0.8 * ej + 4 for dj, ej in zip(d[:2], e[:2]))
        bg = d[-1] <= BG_MAX
        good = soft(d, "diagonal %s (k=1..12 outward)" % k) and weaker and rnd and bg
        out("diagonal %s vs %s edge (k=1..12): %s; corner ratio k=1,2: %.2f %.2f (<= 0.8 + 4/edge) %s; background at k=12: dev %.0f (<= %d) %s"
            % (k, ename, "weaker everywhere" if weaker else "NOT weaker than the edge", d[0] / max(e[0], 1e-6), d[1] / max(e[1], 1e-6),
               "round" if rnd else "SQUARE-CORNERED SHADOW", d[-1], BG_MAX, "ok" if bg else "BAD"))
        ok = ok and good
    # 2b. "before the curve": the shadow 2 px outside the top edge, walking from the box corner inward past the arc start (t = 0..r+8),
    # and the same 2 px outside each side edge walking down from the top corner. It must be one smooth ramp: no step > STEP_MAX and
    # no drop toward the straight part (a shadow that ends where the arc begins reads as a sharp edge before the curve).
    for k, walk, label in (("TL", lambda t: (x + t, y - 2), "top edge from TL corner inward"), ("TR", lambda t: (x + w - 1 - t, y - 2), "top edge from TR corner inward"),
                           ("TL", lambda t: (x - 2, y + t), "left edge from TL corner downward"), ("TR", lambda t: (x + w + 1, y + t), "right edge from TR corner downward"),
                           ("BL", lambda t: (x + t, y + h + 1), "bottom edge from BL corner inward"), ("BR", lambda t: (x + w - 1 - t, y + h + 1), "bottom edge from BR corner inward")):
        prof = [dev(walk(t)) for t in range(0, r + 9)]
        drops = [a - b for a, b in zip(prof, prof[1:])]     # a drop = getting weaker while moving away from the corner
        steps = [abs(v) for v in drops]
        good = max(steps) <= STEP_MAX and max(drops) <= RISE_MAX
        out("along %s (t=0..%d, 2 px out) dev=%s  max step %.0f (<= %d) max drop %.0f (<= %d)  %s"
            % (label, r + 8, " ".join("%.0f" % v for v in prof), max(steps), STEP_MAX, max(drops), RISE_MAX, "ok" if good else "BAD"))
        ok = ok and good
    # 3. edges 2..6
    for e in ("top", "bottom", "left", "right"):
        ok = soft(edge_dev[e][1:6], "edge %s (k=2..6 outside)" % e) and ok
    # 4. shadow present
    present = max(edge_dev["top"][1], edge_dev["bottom"][1])
    good = present >= PRESENT_MIN
    out("shadow present: 2 px outside top/bottom dev = %.0f / %.0f (max >= %d)  %s" % (edge_dev["top"][1], edge_dev["bottom"][1], PRESENT_MIN, "ok" if good else "BAD"))
    ok = ok and good
    out("RESULT", "PASS" if ok else "FAIL")
    return ok


# ---------------------------------------------------------------- self-test with synthetic screenshots
def synth(kind, size=(320, 240), geo=(60, 50, 200, 130), r=14):
    """Background gradient + a window: kind = round_soft (the effect: radius-r corners, soft round shadow), rect_shadow (radius-r
    corners but the old frame's rectangular shadow: a 0.37 band along the straight edges and around the square box corner),
    square (no rounding, soft shadow)."""
    from PIL import Image
    W, H = size
    bg = Image.new("RGB", size)
    for yy in range(H):
        for xx in range(W):
            bg.putpixel((xx, yy), (150 + xx // 8, 160 + yy // 6, 180))
    im = bg.copy()
    x, y, w, h = geo
    win = (30, 36, 52)
    size_a = 45.0; sh = math.sqrt(size_a); rr = r if kind != "square" else 0

    def blend(t):
        s = t * t; return s / (2 * (s - t) + 1)

    def soft_alpha(px_, py_):  # the effect's getCustomShadow, section by section, in window coordinates
        cx0 = px_ - x; cy0 = py_ - y
        if cy0 < rr + sh:
            if cx0 < rr + sh: c = (rr + sh, rr + sh)
            elif cx0 > w - rr - sh: c = (w - rr - sh, rr + sh)
            else: c = (cx0, rr + sh)
        elif cy0 > h - rr:
            if cx0 < rr + sh: c = (rr + sh, h - rr)
            elif cx0 > w - rr - sh: c = (w - rr - sh, h - rr)
            else: c = (cx0, h - rr)
        else:
            c = (rr + sh if cx0 < 0 else w - rr - sh, cy0)
        d = math.hypot(cx0 - c[0], cy0 - c[1])
        return 0.5 * blend(max(0.0, min(1.0, 1 - d / size_a)))

    def rect_alpha(px_, py_):  # the old frame: 0.37 at the box edge fading over 28 px, square corners (radial about the box corner)
        dx = max(x - px_, px_ - (x + w - 1), 0); dy = max(y - py_, py_ - (y + h - 1), 0)
        d = math.hypot(dx, dy)
        return 0.37 * max(0.0, 1 - d / 28.0) ** 1.6

    def inside_window(px_, py_):  # inside the box and, in a corner square, inside that corner's circle
        if not (x <= px_ < x + w and y <= py_ < y + h): return False
        if rr == 0: return True
        cx = x + rr if px_ < x + rr else (x + w - 1 - rr if px_ > x + w - 1 - rr else None)
        cy = y + rr if py_ < y + rr else (y + h - 1 - rr if py_ > y + h - 1 - rr else None)
        if cx is None or cy is None: return True
        return math.hypot(px_ - cx, py_ - cy) <= rr + 0.5
    for yy in range(max(0, y - 40), min(H, y + h + 40)):
        for xx in range(max(0, x - 40), min(W, x + w + 40)):
            if inside_window(xx, yy):
                im.putpixel((xx, yy), win)
            else:
                a = rect_alpha(xx, yy) if kind == "rect_shadow" else soft_alpha(xx, yy)
                b = bg.getpixel((xx, yy))
                im.putpixel((xx, yy), tuple(int(round(v * (1 - a))) for v in b))
    return im, bg


def selftest():
    expect = {"round_soft": True, "rect_shadow": False, "square": False}
    allok = True
    for kind, want in expect.items():
        im, bg = synth(kind)
        lines = []
        got = analyse(im, bg, 60, 50, 200, 130, 14, out=lambda *a: lines.append(" ".join(str(v) for v in a)))
        print("selftest %-12s expected %s got %s  %s" % (kind, "PASS" if want else "FAIL", "PASS" if got else "FAIL", "ok" if got == want else "WRONG"))
        for l in lines:   # the full table for the case that must pass; only the rejecting lines for the cases that must fail
            if got != want or kind == "round_soft" or "BAD" in l or "SQUARE" in l or "NOT weaker" in l: print("    " + l)
        allok = allok and got == want
    print("SELFTEST", "PASS" if allok else "FAIL")
    return allok


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        sys.exit(0 if selftest() else 1)
    if len(sys.argv) != 8:
        print(__doc__); sys.exit(3)
    from PIL import Image
    shot, ref = Image.open(sys.argv[1]).convert("RGB"), Image.open(sys.argv[2]).convert("RGB")
    x, y, w, h, r = map(int, sys.argv[3:8])
    sys.exit(0 if analyse(shot, ref, x, y, w, h, r) else 1)
