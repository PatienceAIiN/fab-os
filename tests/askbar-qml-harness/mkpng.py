#!/usr/bin/env python3
"""Write a small original test picture as a real PNG with only the standard library (the image has no python3-pil):
a sky gradient, a green hill, a sun and a red kite — 640x400 by default. Used by tests/askbar-qml-test.sh (the
generate_image step the harness feeds points at it) and tests/ai-controls-render.py.
  mkpng.py OUT.png [WIDTH HEIGHT]
"""
import struct, sys, zlib


def scene(w, h):
    rows = []
    for y in range(h):
        row = bytearray()
        t = y / max(1, h - 1)
        for x in range(w):
            # sky: deep blue at the top to warm orange at the horizon
            r, g, b = int(40 + 200 * t), int(90 + 110 * t), int(190 - 90 * t)
            # sun
            dx, dy = x - w * 0.72, y - h * 0.38
            if dx * dx + dy * dy < (h * 0.09) ** 2:
                r, g, b = 255, 214, 120
            # hill: a broad green curve along the bottom
            hill = h * 0.72 + 0.10 * h * ((x / w - 0.5) ** 2 * 4 - 0.4)
            if y > hill:
                shade = 0.7 + 0.3 * (1 - (y - hill) / (h - hill + 1))
                r, g, b = int(46 * shade), int(150 * shade), int(72 * shade)
            # kite: a red diamond with a short tail
            kx, ky = x - w * 0.34, y - h * 0.34
            if abs(kx) / (w * 0.06) + abs(ky) / (h * 0.11) < 1:
                r, g, b = 224, 60, 52
            if 0 < ky - h * 0.11 < h * 0.16 and abs(kx - (ky - h * 0.11) * 0.25) < 2:
                r, g, b = 240, 240, 240
            row += bytes((r, g, b))
        rows.append(b"\x00" + bytes(row))
    return b"".join(rows)


def chunk(kind, data):
    c = struct.pack(">I", len(data)) + kind + data
    return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def write_png(path, w=640, h=400):
    raw = scene(w, h)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return path


if __name__ == "__main__":
    out = sys.argv[1]
    w, h = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (640, 400)
    write_png(out, w, h)
    print("wrote %s %dx%d" % (out, w, h))
