#!/usr/bin/env python3
"""Sample pixels of a rendered PNG with the standard library only (8-bit RGB / RGBA, non-interlaced — what Qt writes).
Used by tests/askbar-qml-test.sh to prove the generated-image thumbnail is really painted and upright: the test picture
(mkpng.py) has a blue sky at the top and a green hill at the bottom.
  pngcheck.py RENDER.png X Y W H      -> prints "top r g b" / "bottom r g b" and exits 0 when the top row is blue-ish and
                                          the bottom row green-ish inside that rectangle (1 otherwise)
"""
import struct, sys, zlib


def read_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, meta = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            meta = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, depth, ctype, _c, _f, interlace = meta
    assert depth == 8 and interlace == 0 and ctype in (2, 6), "unsupported PNG (depth %d, type %d)" % (depth, ctype)
    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, bpp, rows


def pixel(img, x, y):
    w, h, bpp, rows = img
    x, y = max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))
    o = x * bpp
    return rows[y][o], rows[y][o + 1], rows[y][o + 2]


def average(img, x0, y0, x1, y1):
    n, r, g, b = 0, 0, 0, 0
    for y in range(int(y0), int(y1)):
        for x in range(int(x0), int(x1)):
            pr, pg, pb = pixel(img, x, y)
            r, g, b, n = r + pr, g + pg, b + pb, n + 1
    return (r // max(1, n), g // max(1, n), b // max(1, n))


if __name__ == "__main__":
    img = read_png(sys.argv[1])
    x, y, w, h = (int(float(v)) for v in sys.argv[2:6])
    # sample bands inset from the edges (the corners are rounded, the kite / sun sit mid-frame)
    top = average(img, x + w * 0.35, y + 4, x + w * 0.65, y + 4 + max(2, h * 0.06))
    bottom = average(img, x + w * 0.35, y + h - 4 - max(2, h * 0.06), x + w * 0.65, y + h - 4)
    print("top %d %d %d" % top)
    print("bottom %d %d %d" % bottom)
    sky = top[2] > top[0] + 40 and top[2] > top[1]                # blue sky
    hill = bottom[1] > bottom[0] + 30 and bottom[1] > bottom[2] + 30   # green hill
    print("thumbnail %s" % ("upright and painted" if sky and hill else "NOT as expected"))
    sys.exit(0 if sky and hill else 1)
