#!/usr/bin/env python3
"""Visible-extent measurement for the Fab OS dock (tests/desktop-applets-qml-test.sh, step harness-dock).

Reads the per-icon grabs the dock harness (tests/dock-qml-harness/Driver.qml) writes to <dir>:
  raw-<slot>-<name>.png    every dock item's icon box at tileScale 1.0 (the full resting size)
  norm-<slot>-<name>.png   the same at the shipped tileScale (in.patienceai.fabos.dock/contents/config/main.xml)
  breeze-<name>.png        Breeze-only app icons at the same box size — the reference glyph extent
The visible extent of an icon is the larger side of its alpha bounding box (alpha > 16) divided by the SLOT size
(the raw box = the dock's resting icon size; the Breeze references by their own box, which the dock scales to the
slot). Prints the table, the exact tile factor (mean Breeze extent / mean Fab OS tile extent), checks that the shipped
tileScale matches it within 0.02 and that every normalised dock item lies within ±6 % of the mean extent.
  measure.py <dir> [main.xml]
Exit 0 when both checks hold, 1 otherwise; 2 when the grabs are missing.
  measure.py indicators <dock-rest.png> <geometry.json>
Second mode (see indicators() below): the running / active indicator pixels and the row's centring in the resting render.
"""
import glob, os, re, statistics, sys
from PIL import Image

LIMIT = 0.06          # ±6 % spread allowed after normalisation
TILE_MIN = 0.95       # a raw extent at or above this is a full-bleed Fab OS tile


def extent(path):
    img = Image.open(path).convert("RGBA")
    box = min(img.width, img.height)
    bbox = img.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    if not bbox or box == 0:
        return 0.0, (0, 0), box
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return max(w, h) / box, (w, h), box


def main(argv):
    d = argv[1]
    xml = argv[2] if len(argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/config/main.xml")
    raw = sorted(glob.glob(os.path.join(d, "raw-*.png"))); norm = sorted(glob.glob(os.path.join(d, "norm-*.png"))); ref = sorted(glob.glob(os.path.join(d, "breeze-*.png")))
    if len(raw) < 3 or len(norm) != len(raw) or len(ref) < 3:
        print(f"measure: grabs missing in {d} (raw {len(raw)}, norm {len(norm)}, breeze {len(ref)})"); return 2
    ok = True
    print("Breeze reference glyphs (box = icon size):")
    refs = []
    for p in ref:
        e, (w, h), box = extent(p); refs.append(e)
        print(f"  {os.path.basename(p)[7:-4]:<14} box {box:>3} visible {w:>3}x{h:<3} extent {e:.3f}")
    target = statistics.mean(refs)
    print(f"  mean Breeze glyph extent = {target:.3f} (min {min(refs):.3f}, max {max(refs):.3f})")
    print("dock items, raw (tileScale 1.0; box = the slot's resting icon size):")
    tiles = []; slot = 0
    for p in raw:
        e, (w, h), box = extent(p); slot = max(slot, box)
        kind = "tile" if e >= TILE_MIN else "glyph"
        if kind == "tile": tiles.append(e)
        print(f"  {os.path.basename(p)[4:-4]:<28} box {box:>3} visible {w:>3}x{h:<3} extent {e:.3f}  {kind}")
    if not tiles:
        print("measure: no full-bleed tile among the raw grabs"); return 1
    tile = statistics.mean(tiles)
    factor = target / tile
    print(f"  Fab OS tile extent = {tile:.3f}  ->  tileScale factor = Breeze {target:.3f} / tile {tile:.3f} = {factor:.4f}")
    m = re.search(r'name="tileScale"[^>]*>.*?<default>([0-9.]+)</default>', open(xml).read(), re.S)
    shipped = float(m.group(1)) if m else float("nan")
    diff = abs(shipped - factor)
    print(f"  shipped tileScale (main.xml default) = {shipped}  diff {diff:.4f}  {'OK' if diff <= 0.02 else 'MISMATCH (> 0.02)'}")
    if not diff <= 0.02: ok = False
    print(f"dock items, normalised (tileScale {shipped}; extent = visible px / the {slot} px slot):")
    exts = []
    for p in norm:
        _, (w, h), box = extent(p); e = max(w, h) / slot; exts.append(e)
        print(f"  {os.path.basename(p)[5:-4]:<28} box {box:>3} visible {w:>3}x{h:<3} extent {e:.3f}  ({max(w, h)} px of {slot})")
    mean = statistics.mean(exts)
    spread = max(abs(e - mean) / mean for e in exts)
    print(f"  mean {mean:.3f}, min {min(exts):.3f}, max {max(exts):.3f}, spread ±{spread * 100:.1f} % (limit ±{LIMIT * 100:.0f} %)  {'PASS' if spread <= LIMIT else 'FAIL'}")
    if spread > LIMIT: ok = False
    print("dock-icon-measure: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------- running / active indicators + spacing in dock-rest.png
# measure.py indicators <dock-rest.png> <geometry.json>
# geometry.json is the "INDICATORS {...}" line the dock harness prints (tests/dock-qml-harness/Driver.qml): the applet
# size, the indicator band (dotSpace, dotSize, barWidth, barHeight), the scheme's accent + background colours and every
# item's slot, kind (start / launcher / active / group / minimised / running / peek) and centre x. For each item the band
# (the bottom dotSpace rows, ±16 px around the centre) is scanned: a pixel's "accentness" is how far it lies from the
# backdrop towards the accent colour (0 = backdrop, 1 = accent). Expected: launcher / start / peek — no accent pixel at
# all; running — one 6 px dot (a run of about 6 accent pixels, ~28 px of area); group — two dots (two runs); minimised —
# a dimmer dot (blended pixels, no pixel near full accent); active — a run of about 24 px (the bar) with no dot. Also
# checks the row is centred: the leftmost and rightmost non-backdrop columns of the icon band are equally far from the
# applet's edges (within 2 px). Prints one line per item and PASS/FAIL; exit 0 when every item matches.

def _rgb(s):
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 8: s = s[2:]          # #AARRGGBB from QML's String(color)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    raise ValueError("colour " + s)


def _accentness(px, bg, acc):
    # projection of (px - bg) onto (acc - bg), clamped to 0..1; off-axis colours (icon pixels) are penalised by their distance from the axis
    v = [a - b for a, b in zip(acc, bg)]
    d = [p - b for p, b in zip(px, bg)]
    vv = sum(x * x for x in v) or 1
    t = sum(a * b for a, b in zip(d, v)) / vv
    proj = [b + t * x for b, x in zip(bg, v)]
    off = sum((p - q) ** 2 for p, q in zip(px, proj)) ** 0.5
    if off > 40: return 0.0
    return max(0.0, min(1.0, t))


def _runs(mask):
    runs, cur = [], 0
    for m in mask + [False]:
        if m: cur += 1
        elif cur: runs.append(cur); cur = 0
    return runs


def indicators(png, geometry_json):
    import json
    g = json.load(open(geometry_json))
    img = Image.open(png).convert("RGB")
    W, H = img.size
    bg, acc = _rgb(g["background"]), _rgb(g["accent"])
    band_top = H - int(g["dotSpace"])
    ok = True
    print(f"dock-rest.png {W}x{H}; indicator band rows {band_top}..{H - 1}; accent {acc} on backdrop {bg}")
    for it in g["items"]:
        cx = int(round(it["cx"])); x0, x1 = max(0, cx - 16), min(W, cx + 17)
        full = 0; mid = 0; best_run = 0; runs_by_row = []
        for y in range(band_top, H):
            row = []
            for x in range(x0, x1):
                a = _accentness(img.getpixel((x, y)), bg, acc)
                row.append(a > 0.7)
                if a > 0.7: full += 1
                elif a > 0.2: mid += 1
            r = _runs(row)
            if r: runs_by_row.append(r); best_run = max(best_run, max(r))
        kind = it["kind"]
        two = any(len(r) >= 2 and all(3 <= v <= 8 for v in r) for r in runs_by_row)
        if kind in ("start", "launcher", "peek"):
            good = full == 0 and mid == 0
            want = "nothing"
        elif kind == "active":
            good = best_run >= g["barWidth"] - 2 and best_run <= g["barWidth"] + 2 and not two
            want = f"a {g['barWidth']} px bar"
        elif kind == "group":
            good = two and best_run <= g["dotSize"] + 2
            want = "two dots"
        elif kind == "minimised":
            good = full <= 4 and mid >= 8 and best_run <= g["dotSize"] + 2
            want = "one dimmer dot"
        else:  # running
            good = 16 <= full <= 40 and 3 <= best_run <= g["dotSize"] + 2 and not two
            want = "one dot"
        ok = ok and good
        print(f"  slot {it['slot']:>2} {kind:<9} cx {cx:>4}: accent px {full:>3}, blended {mid:>3}, longest run {best_run:>2}, two-dot rows {'yes' if two else 'no '}  -> {'OK  ' if good else 'FAIL'} (expected {want})")
    # symmetry of the visible row: leftmost / rightmost non-backdrop column over the icon rows (inside the backdrop's
    # rounded corners: the rows well above the band, where the corner radius does not reach)
    cols = []
    for x in range(W):
        if any(sum(abs(a - b) for a, b in zip(img.getpixel((x, y)), bg)) > 24 for y in range(max(0, band_top - 38), max(1, band_top - 14), 2)):
            cols.append(x)
    if cols:
        left, right = cols[0], W - 1 - cols[-1]
        sym = abs(left - right) <= 2
        ok = ok and sym
        print(f"  visible row spans x {cols[0]}..{cols[-1]}: {left} px free at the left, {right} px at the right -> {'OK' if sym else 'FAIL'} (centred within 2 px)")
    else:
        ok = False; print("  no icon pixels found");
    print("dock-indicators: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------- the real dock panel in a plasmashell screenshot
# measure.py panel <screen.png> [crop.png]
# screen.png is Spectacle's full-screen capture of the real session (tests/desktop-applets-qml-test.sh, step shot: the
# wallpaper is set to a flat colour first). The bottom strip is scanned: columns that differ from the wallpaper form the
# floating dock panel; inside it, columns that differ from the panel's own colour are the icons. Reports the panel's
# bounds, the icon row's bounds, the room at each end and the row's offset from the panel's centre; PASS when the two
# ends match within 8 px (the 1 px launcher anchor + the panel's 4 px spacing sit at the left) and the row is within
# 6 px of the panel's centre — the old empty-icon launcher took the panel thickness (~64 px) and would fail both.

def _mode(pixels):
    from collections import Counter
    return Counter(pixels).most_common(1)[0][0]


def _differs(a, b, tol):
    return sum(abs(x - y) for x, y in zip(a, b)) > tol


def panel(png, crop=None):
    img = Image.open(png).convert("RGB")
    W, H = img.size
    wall = _mode([img.getpixel((6, y)) for y in range(int(H * 0.15), int(H * 0.85), 3)])
    strip_top = H - 140
    cols = [x for x in range(W) if any(_differs(img.getpixel((x, y)), wall, 30) for y in range(strip_top, H, 2))]
    # the longest run of such columns is the dock panel
    runs, start = [], None
    for x in range(W + 1):
        inside = x < W and x in set(cols) if False else None
    colset = set(cols); best = (0, 0, 0)
    x = 0
    while x < W:
        if x in colset:
            x0 = x
            while x + 1 in colset: x += 1
            if x - x0 > best[0]: best = (x - x0, x0, x)
        x += 1
    if best[0] < 100:
        print(f"panel: no wide non-wallpaper run in the bottom strip (longest {best[0]} px)"); return 1
    p0, p1 = best[1], best[2]
    rows = [y for y in range(strip_top, H) if _differs(img.getpixel(((p0 + p1) // 2, y)), wall, 30) or _differs(img.getpixel((p0 + 6, y)), wall, 30)]
    r0, r1 = rows[0], rows[-1]
    pcol = _mode([img.getpixel((x, y)) for x in range(p0 + 3, p0 + 10) for y in range(r0 + 6, r1 - 6, 2)])
    icon_cols = [x for x in range(p0, p1 + 1) if any(_differs(img.getpixel((x, y)), pcol, 40) for y in range(r0 + 8, r1 - 8, 2))]
    # ignore the panel's own rounded corners: drop columns within 12 px of either edge that only differ near the top/bottom
    icon_cols = [x for x in icon_cols if any(_differs(img.getpixel((x, y)), pcol, 40) for y in range(r0 + 16, r1 - 16, 2))]
    if not icon_cols:
        print(f"panel: found the panel x {p0}..{p1} y {r0}..{r1} but no icon columns inside it"); return 1
    i0, i1 = icon_cols[0], icon_cols[-1]
    left, right = i0 - p0, p1 - i1
    centre_off = ((i0 + i1) / 2) - ((p0 + p1) / 2)
    print(f"screen {W}x{H}, wallpaper {wall}; dock panel x {p0}..{p1} ({p1 - p0 + 1} px) y {r0}..{r1} ({r1 - r0 + 1} px), panel colour {pcol}")
    print(f"icon row x {i0}..{i1} ({i1 - i0 + 1} px): {left} px free at the left, {right} px at the right; row centre {centre_off:+.1f} px from the panel's centre")
    ok = abs(left - right) <= 8 and abs(centre_off) <= 6
    print("dock-panel: " + ("PASS" if ok else "FAIL") + " (ends equal within 8 px, row within 6 px of the panel centre)")
    if crop:
        img.crop((max(0, p0 - 24), max(0, r0 - 16), min(W, p1 + 25), H)).save(crop)
        print("crop written to " + crop)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    if sys.argv[1] == "raw2png":   # kwin-shot.py's raw pixels (QImage format 4/5/6 = RGB32/ARGB32/ARGB32_Premultiplied, BGRA in memory) -> PNG
        import json
        meta = json.load(open(sys.argv[3])); data = open(sys.argv[2], "rb").read()
        w, h, stride, fmt = meta["width"], meta["height"], meta["stride"], meta["format"]
        mode = "BGRA" if fmt in (5, 6) else "BGRX"
        img = Image.frombuffer("RGBA", (w, h), data, "raw", mode, stride, 1).convert("RGB")
        img.save(sys.argv[4]); print(f"raw2png: {w}x{h} (QImage format {fmt}) -> {sys.argv[4]}"); sys.exit(0)
    if sys.argv[1] == "panel":
        if len(sys.argv) not in (3, 4):
            print("measure.py panel <screen.png> [crop.png]"); sys.exit(2)
        sys.exit(panel(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    if sys.argv[1] == "indicators":
        if len(sys.argv) != 4:
            print("measure.py indicators <dock-rest.png> <geometry.json>"); sys.exit(2)
        sys.exit(indicators(sys.argv[2], sys.argv[3]))
    sys.exit(main(sys.argv))
