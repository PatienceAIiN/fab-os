#!/usr/bin/env python3
"""geo.py <shot.png> [title_h]: the frame geometry of the magenta probe window in a live screenshot, for tests/corners-sample.py.
The client is (255,0,255); its bounding box (tolerance 90: the effect's 1 px outline and the corner anti-aliasing tint the
outermost client pixels) is the client rect, and the frame is that rect plus the Aurorae title bar above it (TitleHeight from
FabOSrc, 36; no side or bottom borders: BorderSize=None). Cross-check printed to stderr: walking up from the client's top edge at
the horizontal centre, the pixels stay close to the header colour for about TitleHeight px.
Prints on stdout:  x y w h   (frame geometry)."""
import math
import sys

from PIL import Image

im = Image.open(sys.argv[1]).convert("RGB")
th = int(sys.argv[2]) if len(sys.argv) > 2 else 36
W, H = im.size
px = im.load()
xs, ys = [], []
for y in range(H):
    for x in range(W):
        r, g, b = px[x, y]
        if math.sqrt((r - 255) ** 2 + g * g + (b - 255) ** 2) < 90:
            xs.append(x)
            ys.append(y)
if not xs:
    print("no magenta client in", sys.argv[1], file=sys.stderr)
    sys.exit(1)
x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
cx = (x0 + x1) // 2
head = px[cx, y0 - 2]
n = 0
while y0 - 1 - n >= 0 and math.sqrt(sum((a - b) ** 2 for a, b in zip(px[cx, y0 - 1 - n], head))) < 40:
    n += 1
print("client bbox x %d..%d y %d..%d (%dx%d); header colour %s; header-coloured run above the client %d px (TitleHeight %d)"
      % (x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1, head, n, th), file=sys.stderr)
print(x0, y0 - th, x1 - x0 + 1, y1 - y0 + 1 + th)
