#!/usr/bin/env python3
"""Render the committed login/boot artwork: the SDDM greeter backdrop + glyphs and the Plymouth two-step theme images.

Most brand artwork is rendered at .deb build time by brand/gen/make_assets.py (packages/build-debs.sh copies it in).  The
greeter and the boot splash need a handful of *fixed-name* files that the build script does not produce (two-step wants
throbber-NNNN.png / watermark.png / entry.png / bullet.png / lock.png / capslock.png / bgrt-fallback.png, the greeter wants a
pre-blurred backdrop, a default face and white Material glyphs), so they are rendered here once and committed: small,
deterministic, and the theme directories in git are complete on their own — tests/sddm-theme-test.sh and
tests/plymouth-theme-test.sh run straight from the tree, and an OTA package carries everything it needs.

Run inside the assets stage image (python3-pil, fonts-inter, librsvg2-bin):
  podman build --target assets -f image/Containerfile -t fabos:assets .
  podman run --rm -v "$PWD":/work:Z localhost/fabos:assets python3 /work/tools/gen-login-boot-assets.py --root /work

Glyphs are Material Symbols Rounded (Apache-2.0, see ATTRIBUTIONS.md): the vendored copies in brand/gen/material-cache are
used first; a glyph missing there is fetched once and committed with the theme (icons/<name>.svg), so a package build never
needs the network.
"""
import argparse, os, re, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFilter

SS = 4  # supersampling for PIL shapes

def material_path(symbol, cache_dir, theme_icons_dir):
    """Path data of one Material Symbols Rounded glyph (viewBox 0 -960 960 960): vendored cache, then the theme's own copy,
    then one network fetch."""
    for p in (os.path.join(cache_dir, symbol + ".svg"), os.path.join(theme_icons_dir, symbol + ".svg")):
        if os.path.exists(p):
            return re.findall(r'\sd="([^"]+)"', open(p).read())
    import urllib.request
    url = ("https://raw.githubusercontent.com/google/material-design-icons/master/symbols/web/%s/materialsymbolsrounded/%s_48px.svg"
           % (symbol, symbol))
    with urllib.request.urlopen(url, timeout=30) as r:
        data = r.read().decode()
    paths = re.findall(r'\sd="([^"]+)"', data)
    if not paths:
        raise SystemExit("no path data in %s" % url)
    return paths

def glyph_svg(paths, fill="#FFFFFF"):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -960 960 960" width="48" height="48">'
            + "".join('<path fill="%s" d="%s"/>' % (fill, d) for d in paths) + "</svg>\n")

def glyph_png(paths, size, fill="#FFFFFF"):
    """Rasterise a glyph with rsvg-convert (crisp at any size; PIL has no SVG)."""
    with tempfile.TemporaryDirectory() as t:
        svg = os.path.join(t, "g.svg"); png = os.path.join(t, "g.png")
        open(svg, "w").write(glyph_svg(paths, fill))
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", png, svg], check=True)
        return Image.open(png).convert("RGBA")

def rounded(w, h, radius, fill, outline=None, outline_w=0):
    """Anti-aliased rounded rectangle (drawn at SS x, LANCZOS down)."""
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=radius * SS, fill=fill,
                        outline=outline, width=int(outline_w * SS) if outline else 0)
    return img.resize((w, h), Image.LANCZOS)

def circle(size, fill):
    img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size * SS - 1, size * SS - 1], fill=fill)
    return img.resize((size, size), Image.LANCZOS)

def fade(img, factor):
    """Multiply the alpha channel (for muted glyphs)."""
    a = img.split()[3].point(lambda v: int(v * factor)); img = img.copy(); img.putalpha(a); return img

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True)
    ap.add_argument("--font-dir", action="append", default=["/usr/share/fonts"])
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    sys.path.insert(0, os.path.join(root, "brand", "gen"))
    import make_assets as MA                                # draw_mark, weave_wallpaper, wordmark, mark_svg, font, hexrgb
    C = MA.load_conf(os.path.join(root, "brand", "brand.conf"))
    name, vendor = C["DISTRO_NAME"], C["VENDOR_NAME"]
    accent = MA.hexrgb(C["ACCENT"])
    white = (255, 255, 255, 255)
    cache = os.path.join(root, "brand", "gen", "material-cache")

    # ------------------------------------------------------------------ SDDM greeter
    sddm = os.path.join(root, "packages", "fabos-desktop", "usr", "share", "sddm", "themes", "fabos")
    icons_dir = os.path.join(sddm, "icons")
    os.makedirs(icons_dir, exist_ok=True); os.makedirs(os.path.join(sddm, "faces"), exist_ok=True)
    # backdrop: the dark weave wallpaper, blurred and dimmed like GDM's lock shield. 1920x1080 is plenty for a blur that the
    # QML crops (PreserveAspectCrop) to any panel; it must stay under 1 MB (a blur compresses very well).
    wp = MA.weave_wallpaper(1920, 1080, True, C["ACCENT"], C["ACCENT_DARK"], C["BG_DARK"], C["BG_LIGHT"]).convert("RGB")
    wp = wp.filter(ImageFilter.GaussianBlur(42))
    wp = Image.blend(wp, Image.new("RGB", wp.size, MA.hexrgb(C["BG_DARK"])[:3]), 0.45)
    wp.save(os.path.join(sddm, "backdrop.png"), optimize=True, compress_level=9)
    size = os.path.getsize(os.path.join(sddm, "backdrop.png"))
    print("backdrop.png", wp.size, size, "bytes"); assert size < 1_000_000, "backdrop must stay under 1 MB"
    # theme-level default avatar: SDDM uses <theme>/faces/.face.icon before /usr/share/sddm/faces/.face.icon
    MA.draw_mark(256, accent, outline=((255, 255, 255, 190), 1.25)).save(os.path.join(sddm, "faces", ".face.icon"), format="PNG")
    # the mark (build-debs.sh also installs it from the generated assets; committed so the tree is complete)
    open(os.path.join(sddm, "mark.svg"), "w").write(MA.mark_svg(C["ACCENT"], title=name))
    icons = ["visibility", "visibility_off", "power_settings_new", "settings", "keyboard", "keyboard_capslock",
             "arrow_forward", "arrow_back", "check", "lock", "person", "bedtime", "restart_alt", "close"]
    for s in icons:
        paths = material_path(s, cache, icons_dir)
        assert paths, "empty glyph " + s
        open(os.path.join(icons_dir, s + ".svg"), "w").write(glyph_svg(paths))
    print("icons:", ", ".join(icons))

    # ------------------------------------------------------------------ Plymouth two-step
    ply = os.path.join(root, "packages", "fabos-branding", "usr", "share", "plymouth", "themes", "fabos")
    os.makedirs(ply, exist_ok=True)
    for old in os.listdir(ply):
        if re.match(r"throbber-\d+\.png$", old): os.remove(os.path.join(ply, old))
    # throbber: the mark's dashed ring spinning with breathing bars. two-step plays throbber frames at 30 fps, so 48 frames
    # = one turn every 1.6 s. 80 px logical (plymouth scales by the panel's device scale, so 160 px on a HiDPI panel).
    N = 48
    for k in range(N):
        MA.draw_mark(80, white, ring_dash=250, spin=k / float(N), bar_phase=k / float(N)).save(os.path.join(ply, "throbber-%04d.png" % (k + 1)))
    # watermark: the wordmark, bottom centre (WatermarkVerticalAlignment=.96)
    wm, used = MA.wordmark(a.font_dir, name, "by " + vendor, white, 34)
    wm.save(os.path.join(ply, "watermark.png")); print("watermark.png", wm.size, "font", used)
    # where the firmware has no BGRT logo (QEMU/OVMF, legacy BIOS) two-step draws this at the logo position (38.2 % down)
    MA.draw_mark(176, accent).save(os.path.join(ply, "bgrt-fallback.png"))
    # password dialog. two-step draws lock.png left of entry.png, both centred on the dialog height, and the bullets
    # (bullet.png, one per typed character, starting half a bullet in from the entry's LEFT edge) at the entry's vertical
    # centre; the caller's prompt is written below the dialog. So the field is: a label band above the box ("Disk
    # password" left, "Press Enter to unlock" right), the box, and a transparent band of the same height below it, which
    # keeps the bullets centred on the box.
    W, BOX, BAND = 380, 52, 34
    entry = Image.new("RGBA", (W, BAND + BOX + BAND), (0, 0, 0, 0))
    entry.alpha_composite(rounded(W, BOX, 14, (255, 255, 255, 30), outline=(255, 255, 255, 96), outline_w=1.5), (0, BAND))
    f_label, _ = MA.font(a.font_dir, "Inter", "Medium", 15); f_hint, _ = MA.font(a.font_dir, "Inter", "Regular", 13)
    d = ImageDraw.Draw(entry)
    d.text((2, 6), "Disk password", font=f_label, fill=(255, 255, 255, 235))
    hint = "Press Enter to unlock"; hw = int(f_hint.getlength(hint))
    d.text((W - 2 - hw, 8), hint, font=f_hint, fill=(255, 255, 255, 150))
    entry.save(os.path.join(ply, "entry.png"))
    bullet = Image.new("RGBA", (22, 22), (0, 0, 0, 0)); bullet.alpha_composite(circle(10, (255, 255, 255, 235)), (6, 6))
    bullet.save(os.path.join(ply, "bullet.png"))
    lock = Image.new("RGBA", (28 + 14, 28), (0, 0, 0, 0)); lock.alpha_composite(fade(glyph_png(material_path("lock", cache, icons_dir), 28), 0.9), (0, 0))
    lock.save(os.path.join(ply, "lock.png"))
    # Caps Lock badge (two-step shows capslock.png under the prompt while Caps Lock is on)
    f_caps, _ = MA.font(a.font_dir, "Inter", "Medium", 13)
    label = "Caps Lock is on"; lw = int(f_caps.getlength(label))
    cw, ch = 12 + 18 + 8 + lw + 14, 30
    caps = rounded(cw, ch, 15, (255, 255, 255, 36))
    caps.alpha_composite(glyph_png(material_path("keyboard_capslock", cache, icons_dir), 18), (12, (ch - 18) // 2))
    ImageDraw.Draw(caps).text((12 + 18 + 8, (ch - 15) // 2 - 1), label, font=f_caps, fill=(255, 255, 255, 230))
    caps.save(os.path.join(ply, "capslock.png"))
    for n in ("throbber-0001.png", "watermark.png", "bgrt-fallback.png", "entry.png", "bullet.png", "lock.png", "capslock.png"):
        im = Image.open(os.path.join(ply, n)); print("%-20s %dx%d" % (n, im.width, im.height))

if __name__ == "__main__":
    main()
