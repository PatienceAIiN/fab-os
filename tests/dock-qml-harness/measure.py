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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv))
