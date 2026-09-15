#!/usr/bin/env python3
"""crops.py <out-dir> <scheme> <x> <y> <w> <h>: one picture to LOOK at — the four frame corners of the probe window, 80x80 px around
each box corner at 4x (nearest neighbour, so every pixel stays a pixel): row 1 the active window, row 2 inactive, row 3 the bare
reference. Writes <out-dir>/<scheme>-corners.png."""
import sys

from PIL import Image, ImageDraw

out, scheme = sys.argv[1], sys.argv[2]
x, y, w, h = map(int, sys.argv[3:7])
R, S, GAP = 40, 4, 12
corners = {"TL": (x, y), "TR": (x + w - 1, y), "BL": (x, y + h - 1), "BR": (x + w - 1, y + h - 1)}
rows = ["active", "inactive", "ref"]
tile = 2 * R * S
sheet = Image.new("RGB", (4 * tile + 5 * GAP, len(rows) * (tile + 18) + GAP), (40, 40, 40))
d = ImageDraw.Draw(sheet)
for ri, state in enumerate(rows):
    im = Image.open("%s/%s-%s.png" % (out, scheme, state)).convert("RGB")
    for ci, (name, (cx, cy)) in enumerate(corners.items()):
        crop = im.crop((cx - R, cy - R, cx + R, cy + R)).resize((tile, tile), Image.NEAREST)
        ox, oy = GAP + ci * (tile + GAP), GAP + ri * (tile + 18)
        sheet.paste(crop, (ox, oy + 14))
        d.text((ox, oy), "%s %s" % (state, name), fill=(230, 230, 230))
path = "%s/%s-corners.png" % (out, scheme)
sheet.save(path)
print("crops: %s (%dx%d)" % (path, sheet.width, sheet.height))
