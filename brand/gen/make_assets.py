#!/usr/bin/env python3
"""Render every Fab OS brand asset from brand.conf + the SVG mark.

Deterministic (seeded noise only), dependency-light (Pillow; rsvg-convert for the app-tile PNGs). Outputs:
  icons/           fabos.svg + fabos-symbolic.svg (vector) and fabos-<16..1024>.png: the bare mark (ring + three
                   bars) in the accent colour on a transparent background — no tile behind it
  pixmaps/         fabos.png (1024, About page / installer), fabos-face.png (512, default avatar, thin white halo),
                   fabos-logo.png + fabos-logo-dark.png (mark + wordmark lockups, rendered at 2x)
  plymouth/        36 spinner frames (256 px, white) + wordmark (2x) for the boot splash; the script scales them
  wallpapers/      procedural weave wallpapers, light + dark, at 7 sizes from 1280x800 to 3840x2160, anti-aliased
                   (shapes drawn at 2x, LANCZOS) and dithered (+-1/255 seeded noise) so long gradients do not band
  3d/              fabos-mark.glb + fabos-mark.obj (torus + 3 bars)
  icon-theme/      the FabOS icon theme (Material Symbols on colour tiles for apps; the bare mark for "fabos")
  plasma-theme/    KSvg frames for panels/popups (plasma_theme.py)
  meta/            assets.json: conf, font, and a WxH inventory of every raster written
"""
import argparse, glob, json, math, os, random, shlex, struct, sys
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

WALLPAPER_SIZES = [(3840, 2160), (2560, 1440), (2560, 1600), (1920, 1080), (1920, 1200), (1366, 768), (1280, 800)]
QUICK_SIZES = [(1920, 1080), (1366, 768)]
MARK_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024)     # bare-mark PNGs (hicolor takes 16..512, 1024 -> pixmaps)
THEME_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)          # fixed-size dirs of the FabOS icon theme
SPINNER_PX, SPINNER_FRAMES = 256, 36                            # boot-splash frames; 2x the old 128 px
SCREENSHOT = (1280, 720)

def load_conf(path):
    conf = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            conf[k] = shlex.split(v)[0] if v else ""
    return conf

def find_font(font_dirs, family, weight):
    pats = [f"**/{family}-{weight}.ttf", f"**/{family}-{weight}.otf", f"**/{family}*{weight}*.ttf"]
    for d in font_dirs:
        for p in pats:
            hits = sorted(glob.glob(os.path.join(d, p), recursive=True))
            if hits: return hits[0]
    return None

def font(font_dirs, family, weight, size):
    path = find_font(font_dirs, family, weight)
    if path: return ImageFont.truetype(path, size), path
    for fb in ("DejaVuSans-Bold.ttf" if weight == "Bold" else "DejaVuSans.ttf",):
        for d in font_dirs + ["/usr/share/fonts"]:
            hits = glob.glob(os.path.join(d, "**", fb), recursive=True)
            if hits: return ImageFont.truetype(hits[0], size), hits[0]
    return ImageFont.load_default(), "builtin"

def hexrgb(h, a=255):
    h = h.lstrip("#"); return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16), a)

# ---------- the mark ----------
# Geometry (64-unit grid, brand/logo/fabos-mark.svg): ring centre (32,32), stroke 4 centred on r=20 (outer 22, inner 18);
# bars 4 wide at x=23/30/37, 14/20/14 tall, centred on y=32, rx=2; the whole thing rotated -24 deg about the centre.
# `view` is how many grid units the output canvas spans (centred): 64 = the loose SVG canvas, 48 = tight icon framing
# (ring outer edge at 44/48 of the canvas, like a Breeze app icon).
BARS = ((-9, 14), (-2, 20), (5, 14))   # (left edge relative to centre, height)

def draw_mark(size, color, rot_deg=-24, ring_dash=None, spin=0.0, bar_phase=0.0, ss=4, view=48, outline=None):
    """Ring + three woven bars, supersampled ss x and LANCZOS-downsampled. ring_dash/spin/bar_phase animate the
    boot spinner. outline=(rgba, width_units) draws a thin halo behind the mark for contrast over wallpapers."""
    S = size * ss; u = S / float(view); cx = cy = S / 2.0; r = 20 * u; w = 4 * u
    def shapes(d, col, grow):
        ww = w + 2 * grow; ro = r + ww / 2
        box = [cx - ro, cy - ro, cx + ro, cy + ro]
        if ring_dash is None:
            d.ellipse(box, outline=col, width=int(round(ww)))
        else:
            start = (spin * 360) % 360; d.arc(box, start, start + ring_dash, fill=col, width=int(round(ww)))
            for ang in (start, start + ring_dash):   # round caps
                a = math.radians(ang); px, py = cx + r * math.cos(a), cy + r * math.sin(a)
                d.ellipse([px - ww / 2, py - ww / 2, px + ww / 2, py + ww / 2], fill=col)
        for i, (dx, h0) in enumerate(BARS):
            amp = 3 * u * math.sin(bar_phase * 2 * math.pi + i * 0.9) if bar_phase else 0   # breathing bars (frames only)
            h = h0 * u + (amp if i != 1 else -amp); x = cx + dx * u; y = cy - h / 2
            d.rounded_rectangle([x - grow, y - grow, x + 4 * u + grow, y + h + grow], radius=2 * u + grow, fill=col)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if outline:
        halo = Image.new("RGBA", (S, S), (0, 0, 0, 0)); shapes(ImageDraw.Draw(halo), outline[0], outline[1] * u); img.alpha_composite(halo)
    fg = Image.new("RGBA", (S, S), (0, 0, 0, 0)); shapes(ImageDraw.Draw(fg), color, 0); img.alpha_composite(fg)
    img = img.rotate(-rot_deg, resample=Image.BICUBIC, center=(cx, cy))
    return img.resize((size, size), Image.LANCZOS)

def mark_svg(fill=None, symbolic=False, view=48, title="Fab OS"):
    """The mark as a standalone SVG. fill=colour -> the identity icon; symbolic=True -> monochrome with the KDE
    current-color-scheme stylesheet + class="ColorScheme-Text" so KIconLoader recolours it to the panel text colour."""
    ring = "M32 10a22 22 0 1 0 0 44a22 22 0 1 0 0-44zm0 4a18 18 0 1 1 0 36a18 18 0 1 1 0-36z"
    attrs = 'class="ColorScheme-Text" style="fill:currentColor;fill-opacity:1;stroke:none"' if symbolic else 'fill="%s"' % fill
    shapes = '<path fill-rule="evenodd" d="%s" %s/>' % (ring, attrs) + "".join(
        '<rect x="%d" y="%d" width="4" height="%d" rx="2" %s/>' % (32 + dx, 32 - h // 2, h, attrs) for dx, h in BARS)
    defs = ('<defs id="defs"><style type="text/css" id="current-color-scheme">.ColorScheme-Text{color:#232629;}</style></defs>'
            if symbolic else "")
    o = (64 - view) / 2.0
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%g %g %d %d" width="%d" height="%d" role="img" aria-labelledby="t">'
            '<title id="t">%s</title>%s<g transform="rotate(-24 32 32)">%s</g></svg>\n' % (o, o, view, view, view, view, title, defs, shapes))

def wordmark(font_dirs, text, sub, fg, size_px):
    f1, used = font(font_dirs, "Inter", "Bold", size_px)
    f2, _ = font(font_dirs, "Inter", "Medium", int(size_px*0.42))
    pad = size_px//2
    w1 = int(f1.getlength(text)); w2 = int(f2.getlength(sub)) if sub else 0
    W = max(w1,w2) + pad; H = int(size_px*1.25) + (int(size_px*0.55) if sub else 0) + pad
    img = Image.new("RGBA",(W,H),(0,0,0,0)); d = ImageDraw.Draw(img)
    d.text((pad//2, pad//2), text, font=f1, fill=fg)
    if sub: d.text((pad//2, pad//2 + int(size_px*1.15)), sub, font=f2, fill=fg[:3]+(170,))
    return img, used

def logo_lockup(font_dirs, name, vendor, fg, size_px):
    mark = draw_mark(int(size_px*1.25), fg)
    wm, _ = wordmark(font_dirs, name, "by " + vendor, fg, size_px)
    gap = size_px//3
    W = mark.width + gap + wm.width; H = max(mark.height, wm.height)
    img = Image.new("RGBA",(W,H),(0,0,0,0))
    img.alpha_composite(mark, (0, (H-mark.height)//2)); img.alpha_composite(wm, (mark.width+gap, (H-wm.height)//2))
    return img

# ---------- wallpaper ----------
_NOISE = None
def noise_tile(T=256, seed=0xFAB05):
    """One seeded 256x256 tile of values {0,1,2}; the same every run, so the output stays reproducible."""
    global _NOISE
    if _NOISE is None:
        rng = random.Random(seed); _NOISE = Image.frombytes("L", (T, T), bytes(rng.choice((0, 1, 2)) for _ in range(T * T)))
    return _NOISE

def dither(rgb):
    """Add -1/0/+1 per pixel (same offset on R,G,B): breaks the 8-bit steps of the long soft gradients without visible grain."""
    W, H = rgb.size; n = noise_tile(); T = n.width
    tiled = Image.new("L", (W, H))
    for y in range(0, H, T):
        for x in range(0, W, T): tiled.paste(n, (x, y))
    return ImageChops.add(rgb, Image.merge("RGB", (tiled, tiled, tiled)), 1.0, -1)

def weave_wallpaper(W, H, dark=True, accent="#3B6EF5", accent2="#6E9BFF", bg="#0E1116", bg_light="#F6F7F9"):
    """Soft accent glow field + a fine diagonal weave + vignette. Smooth layers (glow, vignette) are rendered at 1/4 size
    and upsampled bicubic (identical after the blur, 16x cheaper at 4K); the weave threads are drawn at 2x and
    downsampled with LANCZOS so they are anti-aliased at every size; the result is dithered before saving."""
    base = hexrgb(bg) if dark else hexrgb(bg_light)
    img = Image.new("RGBA", (W, H), base)
    q = 4; w4, h4 = max(1, W // q), max(1, H // q)
    glow = Image.new("RGBA", (w4, h4), (0, 0, 0, 0)); g = ImageDraw.Draw(glow)
    blobs = [(0.72, 0.28, 0.55, accent, 150 if dark else 90), (0.18, 0.78, 0.45, accent2, 110 if dark else 60),
             (0.50, 0.95, 0.60, "#1F9D57", 45 if dark else 30), (0.05, 0.10, 0.35, accent2, 70 if dark else 40)]
    for fx, fy, fr, col, alpha in blobs:
        r = fr * w4 * 0.45; cx, cy = fx * w4, fy * h4
        g.ellipse([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], fill=hexrgb(col, alpha))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(w4 * 0.09)).resize((W, H), Image.BICUBIC))
    # woven texture: two families of diagonal threads, thread width follows the resolution (1 px at 1080p, 2 px at 4K)
    ss = 2; W2, H2 = W * ss, H * ss
    step = max(6, W // 160) * ss; lw = max(1, round(W / 1920.0)) * ss
    a1 = 14 if dark else 10; a2 = 14 if dark else 16
    fam = []
    for flip in (False, True):
        m = Image.new("L", (W2, H2), 0); d = ImageDraw.Draw(m)
        for k in range(-H2, W2 + H2, step):
            d.line([(k, H2), (k + H2, 0)] if flip else [(k, 0), (k + H2, H2)], fill=255, width=lw)
        fam.append(m.resize((W, H), Image.LANCZOS))
    for m, col, a in ((fam[0], (255, 255, 255) if dark else (0, 0, 0), a1), (fam[1], (0, 0, 0) if dark else (255, 255, 255), a2)):
        layer = Image.new("RGBA", (W, H), col + (0,)); layer.putalpha(m.point(lambda v, a=a: v * a // 255)); img.alpha_composite(layer)
    # vignette
    vig = Image.new("L", (w4, h4), 0); vd = ImageDraw.Draw(vig)
    vd.ellipse([-w4 * 0.2, -h4 * 0.3, w4 * 1.2, h4 * 1.3], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w4 * 0.12)).resize((W, H), Image.BICUBIC)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0)); shade.putalpha(vig.point(lambda v: int((255 - v) * (0.55 if dark else 0.15))))
    img.alpha_composite(shade)
    return dither(img.convert("RGB"))

# ---------- 3D mesh (glTF 2.0 binary) ----------
def torus(R, r, seg=96, ring=32):
    V=[];N=[];I=[]
    for i in range(seg):
        u = 2*math.pi*i/seg; cu,su = math.cos(u), math.sin(u)
        for j in range(ring):
            v = 2*math.pi*j/ring; cv,sv = math.cos(v), math.sin(v)
            V.append((( R + r*cv)*cu, r*sv, (R + r*cv)*su)); N.append((cv*cu, sv, cv*su))
    for i in range(seg):
        for j in range(ring):
            a=i*ring+j; b=((i+1)%seg)*ring+j; c=((i+1)%seg)*ring+(j+1)%ring; d=i*ring+(j+1)%ring
            I += [a,b,c, a,c,d]
    return V,N,I

def rounded_bar(cx, w, h, depth, radius, seg=12):
    """Capsule-ish bar: box with rounded ends in the XY plane, extruded in Z."""
    V=[];N=[];I=[]
    # build 2D outline of a rounded rect (stadium) then extrude
    pts=[]; hw,hh = w/2, h/2 - radius
    for k in range(seg+1):
        a = math.pi*k/seg; pts.append((cx + hw*math.cos(a)*1.0, hh + radius*math.sin(a)))  # top arc (approx)
    for k in range(seg+1):
        a = math.pi + math.pi*k/seg; pts.append((cx + hw*math.cos(a), -hh + radius*math.sin(a)))
    n=len(pts); z0,z1 = -depth/2, depth/2
    for (x,y) in pts: V.append((x,y,z1)); N.append((0,0,1))
    for (x,y) in pts: V.append((x,y,z0)); N.append((0,0,-1))
    # caps (fan)
    for k in range(1,n-1): I += [0,k,k+1]
    for k in range(1,n-1): I += [n, n+k+1, n+k]
    base=len(V)
    for k in range(n):
        (x,y)=pts[k]; (x2,y2)=pts[(k+1)%n]; nx,ny = (y2-y),(x-x2); L=math.hypot(nx,ny) or 1
        for (px,py,pz) in ((x,y,z1),(x2,y2,z1),(x2,y2,z0),(x,y,z0)):
            V.append((px,py,pz)); N.append((nx/L,ny/L,0))
        b=base+4*k; I += [b,b+1,b+2, b,b+2,b+3]
    return V,N,I

def write_gltf(path_glb, path_obj, parts, color):
    V=[];N=[];I=[]
    for (v,n,i) in parts:
        off=len(V); V+=v; N+=n; I+=[x+off for x in i]
    # rotate whole mark by -24° about Z like the logo
    a=math.radians(-24); ca,sa=math.cos(a),math.sin(a)
    V=[(x*ca - y*sa, x*sa + y*ca, z) for (x,y,z) in V]; N=[(x*ca - y*sa, x*sa + y*ca, z) for (x,y,z) in N]
    with open(path_obj,"w") as f:
        f.write("# Fab OS mark — CC0-1.0 / Apache-2.0\no fabos_mark\n")
        for v in V: f.write("v %.5f %.5f %.5f\n"%v)
        for n in N: f.write("vn %.5f %.5f %.5f\n"%n)
        for k in range(0,len(I),3): f.write("f %d//%d %d//%d %d//%d\n"%(I[k]+1,I[k]+1,I[k+1]+1,I[k+1]+1,I[k+2]+1,I[k+2]+1))
    pos=b"".join(struct.pack("<fff",*v) for v in V); nor=b"".join(struct.pack("<fff",*n) for n in N)
    idx=b"".join(struct.pack("<I",i) for i in I)
    def pad4(b, c=b"\x00"): return b + c*((4-len(b)%4)%4)
    bin_ = pad4(pos)+pad4(nor)+pad4(idx)
    mins=[min(v[k] for v in V) for k in range(3)]; maxs=[max(v[k] for v in V) for k in range(3)]
    c=[int(color[1:3],16)/255,int(color[3:5],16)/255,int(color[5:7],16)/255,1.0]
    gltf={"asset":{"version":"2.0","generator":"fabos make_assets.py","copyright":"2026 Patience AI, CC0-1.0 OR Apache-2.0"},
      "scene":0,"scenes":[{"nodes":[0]}],"nodes":[{"mesh":0,"name":"FabOSMark"}],
      "meshes":[{"primitives":[{"attributes":{"POSITION":0,"NORMAL":1},"indices":2,"material":0}]}],
      "materials":[{"name":"FabOSInk","pbrMetallicRoughness":{"baseColorFactor":c,"metallicFactor":0.2,"roughnessFactor":0.45}}],
      "buffers":[{"byteLength":len(bin_)}],
      "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":len(pos)},
                     {"buffer":0,"byteOffset":len(pad4(pos)),"byteLength":len(nor)},
                     {"buffer":0,"byteOffset":len(pad4(pos))+len(pad4(nor)),"byteLength":len(idx)}],
      "accessors":[{"bufferView":0,"componentType":5126,"count":len(V),"type":"VEC3","min":mins,"max":maxs},
                   {"bufferView":1,"componentType":5126,"count":len(N),"type":"VEC3"},
                   {"bufferView":2,"componentType":5125,"count":len(I),"type":"SCALAR"}]}
    js=pad4(json.dumps(gltf,separators=(",",":")).encode(), b" ")
    total=12+8+len(js)+8+len(bin_)
    with open(path_glb,"wb") as f:
        f.write(b"glTF"+struct.pack("<II",2,total)); f.write(struct.pack("<II",len(js),0x4E4F534A)+js)
        f.write(struct.pack("<II",len(bin_),0x004E4942)+bin_)

# ---------- app icon theme: Google Material Symbols (Apache-2.0) on brand-coloured Fab OS tiles ----------
# icon-theme name(s) -> (material symbol, tile colour). Names cover the Plasma/KDE apps we ship + our own apps.
# The Fab OS identity icon itself ("fabos") is NOT a tile: see build_icon_theme.
ICON_MAP = {
    ("system-file-manager", "org.kde.dolphin", "folder", "inode-directory"): ("folder", "#3B6EF5"),
    ("utilities-terminal", "org.kde.konsole", "terminal"): ("terminal", "#1E242D"),
    ("systemsettings", "preferences-system", "configure", "org.kde.systemsettings"): ("settings", "#5B6472"),
    ("plasmadiscover", "org.kde.discover", "system-software-install", "flatpak-discover"): ("shopping_bag", "#1F9D57"),
    ("kate", "org.kde.kate", "accessories-text-editor", "text-editor"): ("edit_note", "#B7791F"),
    ("firefox", "firefox-esr", "web-browser", "internet-web-browser", "org.mozilla.firefox"): ("public", "#E0642B"),
    ("fabos-command-center",): ("smart_toy", "#6E9BFF"),
    ("fabos-feedback",): ("feedback", "#7C5CFF"),
    ("fabos-overview",): ("grid_view", "#4B3BD6"),          # Task view / Overview button in the dock
    ("user-trash", "trashcan_empty"): ("delete", "#8892A0"),
    ("user-trash-full", "trashcan_full"): ("delete_sweep", "#8892A0"),
    ("org.kde.gwenview", "gwenview", "image-viewer"): ("image", "#2BA9A0"),
    ("org.kde.okular", "okular", "document-viewer"): ("description", "#C6362F"),
    ("kcalc", "org.kde.kcalc", "accessories-calculator"): ("calculate", "#3F7F5F"),
    ("org.kde.ark", "ark", "utilities-file-archiver"): ("folder_zip", "#8A6D3B"),
    ("org.kde.plasma-systemmonitor", "plasma-systemmonitor", "utilities-system-monitor"): ("monitoring", "#3B6EF5"),
    ("kinfocenter", "org.kde.kinfocenter"): ("info", "#5B6472"),
    ("org.kde.spectacle", "spectacle", "accessories-screenshot"): ("photo_camera", "#7C5CFF"),
    ("start-here-kde", "start-here", "start-here-kde-plasma", "start-here-symbolic"): ("__fabos_mark__", "#16171A"),
    ("kwalletmanager", "kwalletmanager5", "org.kde.kwalletmanager5", "wallet-open"): ("lock", "#5B6472"),
    # --- System Settings (KCM) icons ---
    ("preferences-desktop",): ("tune", "#5B6472"),
    ("preferences-desktop-wallpaper",): ("wallpaper", "#3B6EF5"),
    ("preferences-desktop-theme-global", "preferences-desktop-plasma-theme", "preferences-desktop-theme"): ("palette", "#7C5CFF"),
    ("preferences-desktop-theme-applications",): ("widgets", "#7C5CFF"),
    ("preferences-desktop-theme-windowdecorations",): ("web_asset", "#5B6472"),
    ("preferences-desktop-color", "preferences-desktop-display-color"): ("colorize", "#E0642B"),
    ("preferences-desktop-font", "preferences-desktop-font-installer", "kfontview"): ("text_fields", "#5B6472"),
    ("preferences-desktop-icons",): ("apps", "#3B6EF5"),
    ("preferences-desktop-cursors", "preferences-desktop-mouse"): ("mouse", "#5B6472"),
    ("preferences-desktop-effects", "preferences-desktop-animations"): ("animation", "#2BA9A0"),
    ("preferences-system-splash",): ("rocket_launch", "#3B6EF5"),
    ("preferences-system-login",): ("login", "#5B6472"),
    ("preferences-system-tabbox", "preferences-system-windows", "preferences-system-windows-actions"): ("select_window", "#5B6472"),
    ("preferences-desktop-virtual", "preferences-desktop-activities"): ("grid_view", "#5B6472"),
    ("preferences-desktop-display-randr", "lighttable", "xorg"): ("desktop_windows", "#3B6EF5"),
    ("preferences-desktop-sound", "emblem-music-symbolic"): ("volume_up", "#E0642B"),
    ("preferences-desktop-notification-bell",): ("notifications", "#B7791F"),
    ("preferences-desktop-keyboard", "input-keyboard-virtual"): ("keyboard", "#5B6472"),
    ("preferences-desktop-keyboard-shortcut",): ("keyboard_command_key", "#5B6472"),
    ("preferences-desktop-touchpad", "preferences-desktop-touchscreen"): ("touchpad_mouse", "#5B6472"),
    ("preferences-desktop-tablet",): ("stylus_note", "#5B6472"),
    ("preferences-desktop-gaming",): ("sports_esports", "#1F9D57"),
    ("preferences-system-power-management", "battery"): ("battery_charging_full", "#1F9D57"),
    ("preferences-system-network", "network-wired-symbolic"): ("lan", "#3B6EF5"),
    ("network-wireless-symbolic", "network-wireless-hotspot"): ("wifi", "#3B6EF5"),
    ("network-vpn", "knetattach"): ("vpn_key", "#3B6EF5"),
    ("preferences-system-bluetooth",): ("bluetooth", "#3B6EF5"),
    ("preferences-devices-printer", "printer"): ("print", "#5B6472"),
    ("preferences-desktop-thunderbolt",): ("cable", "#5B6472"),
    ("smartphone",): ("smartphone", "#5B6472"),
    ("camera-photo",): ("photo_camera", "#5B6472"),
    ("preferences-system-users", "preferences-desktop-user-password", "system-users"): ("manage_accounts", "#3B6EF5"),
    ("preferences-desktop-accessibility",): ("accessibility_new", "#1F9D57"),
    ("preferences-desktop-locale",): ("language", "#5B6472"),
    ("preferences-system-time",): ("schedule", "#5B6472"),
    ("preferences-desktop-default-applications", "preferences-desktop-filetype-association"): ("app_registration", "#5B6472"),
    ("preferences-desktop-baloo", "plasma-search", "krunner", "baloo"): ("search", "#5B6472"),
    ("preferences-system-session-services", "system-run"): ("play_circle", "#5B6472"),
    ("preferences-security-firewall",): ("security", "#C6362F"),
    ("preferences-smart-status", "drive-harddisk", "drive-removable-media", "media-removable"): ("hard_drive", "#5B6472"),
    ("system-log-out",): ("logout", "#5B6472"),
    ("redshift-status-on",): ("nightlight", "#B7791F"),
    ("ktip", "help-about", "help-contents"): ("help", "#5B6472"),
    ("kup",): ("backup", "#5B6472"),
    ("fabos-updates", "system-software-update", "update-none", "update-low", "update-high", "plasma-discover-updater"): ("system_update", "#1F9D57"),
    ("org.kde.systemmonitor", "utilities-system-monitor-symbolic"): ("monitoring", "#3B6EF5"),
    ("org.kde.elisa", "elisa", "multimedia-player"): ("music_note", "#7C5CFF"),
    ("org.kde.haruna", "haruna", "video-player"): ("play_circle", "#E0642B"),
    ("org.kde.kmail", "kmail", "internet-mail", "mail-client"): ("mail", "#3B6EF5"),
    ("computer", "computer-laptop"): ("computer", "#5B6472"),
    ("user-home", "folder-home"): ("home", "#3B6EF5"),
    ("folder-documents",): ("description", "#3B6EF5"),
    ("folder-download", "folder-downloads"): ("download", "#3B6EF5"),
    ("folder-pictures", "folder-image"): ("image", "#3B6EF5"),
    ("folder-music", "folder-sound"): ("music_note", "#3B6EF5"),
    ("folder-videos", "folder-video"): ("movie", "#3B6EF5"),
    ("folder-desktop",): ("desktop_windows", "#3B6EF5"),
    ("network-workgroup", "network-server"): ("dns", "#5B6472"),
    ("partitionmanager", "org.kde.partitionmanager", "drive-partition"): ("storage", "#5B6472"),
    ("kmenuedit",): ("edit", "#5B6472"),
    ("klipper", "edit-paste"): ("content_paste", "#5B6472"),
    ("plasma-browser-integration",): ("extension", "#5B6472"),
    ("kde", "kde-frameworks", "plasma", "plasmashell"): ("__fabos_mark__", "#16171A"),
    ("preferences-desktop-emoticons",): ("mood", "#B7791F"),
    ("system-user-prompt", "dialog-password"): ("key", "#5B6472"),
    ("tools-report-bug",): ("bug_report", "#C6362F"),
    ("bookmarks-organize",): ("bookmarks", "#5B6472"),
    ("map-globe",): ("public", "#1F9D57"),
    ("jockey", "hwinfo"): ("memory", "#5B6472"),
    ("calamares", "install-fabos", "system-installer"): ("install_desktop", "#3B6EF5"),
}

def fetch_material(symbol, cache_dir):
    """Download one Material Symbols (Rounded, 48px) SVG from Google's repo; cached; returns path data list or None."""
    import urllib.request
    os.makedirs(cache_dir, exist_ok=True); p = os.path.join(cache_dir, symbol + ".svg")
    seed = os.path.join(os.path.dirname(os.path.abspath(__file__)), "material-cache", symbol + ".svg")   # vendored (Apache-2.0) so builds work offline
    if not os.path.exists(p) and os.path.exists(seed):
        import shutil; shutil.copy(seed, p)
    if not os.path.exists(p):
        url = "https://raw.githubusercontent.com/google/material-design-icons/master/symbols/web/%s/materialsymbolsrounded/%s_48px.svg" % (symbol, symbol)
        try:
            with urllib.request.urlopen(url, timeout=30) as r: open(p, "wb").write(r.read())
        except Exception as e:
            print("material fetch failed", symbol, e); return None
    import re as _re
    return _re.findall(r'\sd="([^"]+)"', open(p).read())

def app_icon_svg(paths, color, mark_svg=None):
    """64-unit tile with a soft gradient and a white glyph (or the Fab OS mark for the launcher)."""
    def shade(h, f):
        r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
        return "#%02x%02x%02x" % (min(255, int(r * f)), min(255, int(g * f)), min(255, int(b * f)))
    top, bot = shade(color, 1.18), shade(color, 0.82)
    glyph = ("".join('<path d="%s"/>' % d for d in paths)) if paths else ""
    body = ('<g transform="translate(13 13) scale(0.03958) translate(0 960)" fill="#FFFFFF">%s</g>' % glyph) if paths else \
           '<g transform="translate(8 8) scale(0.75)" fill="#FFFFFF" stroke="#FFFFFF">%s</g>' % (mark_svg or "")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>'
            '<rect width="64" height="64" rx="16" fill="url(#g)"/><rect x="1" y="1" width="62" height="62" rx="15" fill="none" stroke="#FFFFFF" stroke-opacity="0.18"/>%s</svg>') % (top, bot, body)

def build_icon_theme(out, conf, marks):
    """Writes icon-theme/<size>/apps/<name>.png + scalable/apps/<name>.svg; needs rsvg-convert for the tile PNGs.
    `marks` = {size: RGBA image} of the bare accent mark, installed as the "fabos" identity icon (no tile)."""
    import shutil, subprocess
    theme = os.path.join(out, "icon-theme"); cache = os.path.join(out, "material-cache"); sizes = THEME_SIZES
    mark = ('<g transform="rotate(-24 32 32)"><circle cx="32" cy="32" r="20" fill="none" stroke-width="4"/>'
            '<rect x="23" y="25" width="4" height="14" rx="2"/><rect x="30" y="22" width="4" height="20" rx="2"/><rect x="37" y="25" width="4" height="14" rx="2"/></g>')
    have_rsvg = shutil.which("rsvg-convert") is not None; made = 0; fetched = 0
    sdir = os.path.join(theme, "scalable", "apps"); os.makedirs(sdir, exist_ok=True)
    for s in sizes: os.makedirs(os.path.join(theme, "%dx%d" % (s, s), "apps"), exist_ok=True)
    for names, (symbol, color) in ICON_MAP.items():
        paths = None if symbol == "__fabos_mark__" else fetch_material(symbol, cache)
        if symbol != "__fabos_mark__" and not paths: continue
        fetched += 1
        svg = app_icon_svg(paths, color, mark)
        first = names[0]
        open(os.path.join(sdir, first + ".svg"), "w").write(svg)
        for s in sizes:
            d = os.path.join(theme, "%dx%d" % (s, s), "apps"); png = os.path.join(d, first + ".png")
            if have_rsvg: subprocess.run(["rsvg-convert", "-w", str(s), "-h", str(s), "-o", png, os.path.join(sdir, first + ".svg")], check=True)
        for alias in names[1:]:  # aliases as symlinks (relative) so the theme resolves every name KDE asks for
            for s in sizes:
                d = os.path.join(theme, "%dx%d" % (s, s), "apps"); lp = os.path.join(d, alias + ".png")
                if have_rsvg and not os.path.lexists(lp): os.symlink(first + ".png", lp)
            lp = os.path.join(sdir, alias + ".svg")
            if not os.path.lexists(lp): os.symlink(first + ".svg", lp)
        made += 1
    # The identity icon: bare mark, accent colour, transparent background (start button, ask bar, Welcome, About);
    # plus the monochrome symbolic variant KDE recolours for panels. Vector first, PNG for every fixed size.
    open(os.path.join(sdir, "fabos.svg"), "w").write(mark_svg(conf["ACCENT"], title=conf["DISTRO_NAME"]))
    open(os.path.join(sdir, "fabos-symbolic.svg"), "w").write(mark_svg(symbolic=True, title=conf["DISTRO_NAME"]))
    for s in sizes: marks[s].save(os.path.join(theme, "%dx%d" % (s, s), "apps", "fabos.png"))
    # places/preferences names live in apps/ too; KIconLoader searches all listed dirs regardless of Context.
    # scalable/ is listed FIRST so KIconLoader picks the SVG whenever the requested size is within MinSize..MaxSize.
    dirs = ",".join(["scalable/apps"] + ["%dx%d/apps" % (s, s) for s in sizes])
    idx = ["[Icon Theme]", "Name=FabOS", "Comment=%s icons: Google Material Symbols on Fab OS tiles; everything else from Breeze" % conf["DISTRO_NAME"],
           "Inherits=breeze-dark,breeze,hicolor", "FollowsColorScheme=true", "Directories=" + dirs, "",
           "[scalable/apps]", "Size=64", "MinSize=16", "MaxSize=1024", "Type=Scalable", "Context=Applications", ""]
    for s in sizes: idx += ["[%dx%d/apps]" % (s, s), "Size=%d" % s, "Type=Fixed", "Context=Applications", ""]
    open(os.path.join(theme, "index.theme"), "w").write("\n".join(idx))
    print("icon theme: %d icon families (%d glyphs fetched) + fabos/fabos-symbolic, rsvg=%s" % (made, fetched, have_rsvg))

def inventory(out):
    """{relative path: 'WxH'} for every PNG under out/ except the icon theme and the glyph cache (spinner frames collapsed)."""
    inv = {}
    for dp, _, fn in os.walk(out):
        rel = os.path.relpath(dp, out)
        if rel.startswith(("icon-theme", "material-cache")): continue
        for f in sorted(fn):
            if f.endswith(".png"):
                with Image.open(os.path.join(dp, f)) as im: inv[os.path.normpath(os.path.join(rel, f))] = "%dx%d" % im.size
    frames = [k for k in inv if "/spinner-" in k]
    if frames: inv["plymouth/spinner-NN.png x%d" % len(frames)] = inv[frames[0]]
    for k in frames: del inv[k]
    return inv

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",required=True); ap.add_argument("--conf",default=os.path.join(os.path.dirname(__file__),"..","brand.conf"))
    ap.add_argument("--font-dir",action="append",default=["/usr/share/fonts"]); ap.add_argument("--quick",action="store_true",help="only 1920x1080 + 1366x768 wallpapers")
    a=ap.parse_args(); C=load_conf(a.conf); out=a.out
    for d in ("icons","pixmaps","plymouth","wallpapers","sddm","3d","meta"): os.makedirs(os.path.join(out,d),exist_ok=True)
    ink=hexrgb(C["INK"]); white=(255,255,255,255); accent=hexrgb(C["ACCENT"])
    name=C["DISTRO_NAME"]; vendor=C["VENDOR_NAME"]
    # --- the identity mark: accent colour on transparency, no tile. Vector + 16..1024 PNG ---
    marks = {s: draw_mark(s, accent) for s in MARK_SIZES}
    for s, im in marks.items(): im.save(os.path.join(out,"icons",f"fabos-{s}.png"))
    open(os.path.join(out,"icons","fabos.svg"),"w").write(mark_svg(C["ACCENT"], title=name))
    open(os.path.join(out,"icons","fabos-symbolic.svg"),"w").write(mark_svg(symbolic=True, title=name))
    marks[1024].save(os.path.join(out,"pixmaps","fabos.png"))                      # About page logo, installer
    draw_mark(512, accent, outline=((255,255,255,190), 1.25)).save(os.path.join(out,"pixmaps","fabos-face.png"))   # default avatar: thin white halo for contrast over the wallpaper
    # --- lockups (mark + wordmark), rendered at 2x; consumers scale down ---
    logo_lockup(a.font_dir, name, vendor, ink, 144).save(os.path.join(out,"pixmaps","fabos-logo.png"))
    logo_lockup(a.font_dir, name, vendor, white, 144).save(os.path.join(out,"pixmaps","fabos-logo-dark.png"))
    # --- plymouth: 36 spinner frames (dashed ring rotating + breathing bars) at 256 px + the wordmark at 2x; the script scales both to the screen ---
    wm,used=wordmark(a.font_dir, name, "by "+vendor, white, 112); wm.save(os.path.join(out,"plymouth","wordmark.png"))
    for k in range(SPINNER_FRAMES):
        draw_mark(SPINNER_PX, white, ring_dash=250, spin=k/float(SPINNER_FRAMES), bar_phase=k/float(SPINNER_FRAMES)).save(os.path.join(out,"plymouth",f"spinner-{k:02d}.png"))
    # --- wallpapers: light + dark at every common laptop/desktop size; SDDM + KSplash use the dark 4K one ---
    sizes = QUICK_SIZES if a.quick else WALLPAPER_SIZES
    largest = None
    for (W,H) in sizes:
        for dark, tag in ((True,"dark"),(False,"light")):
            wp = weave_wallpaper(W,H,dark,C["ACCENT"],C["ACCENT_DARK"],C["BG_DARK"],C["BG_LIGHT"])
            wp.save(os.path.join(out,"wallpapers",f"{tag}-{W}x{H}.png"), compress_level=9)
            if dark and (largest is None or W*H > largest.width*largest.height): largest = wp
    largest.resize(SCREENSHOT, Image.LANCZOS).save(os.path.join(out,"wallpapers","screenshot.png"), compress_level=9)
    largest.save(os.path.join(out,"sddm","background.png"), compress_level=9)
    # --- 3D ---
    parts=[torus(20.0,2.0)]
    for (x,h) in ((25,14),(32,20),(39,14)): parts.append(rounded_bar(x-32, 4.0, float(h), 4.0, 2.0))
    write_gltf(os.path.join(out,"3d","fabos-mark.glb"), os.path.join(out,"3d","fabos-mark.obj"), parts, C["INK"])
    build_icon_theme(out, C, marks)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import plasma_theme; plasma_theme.write_theme(out, C)
    json.dump({"font_used":used,"conf":C,"rasters":inventory(out)},open(os.path.join(out,"meta","assets.json"),"w"),indent=1)
    print("assets rendered to",out,"| font:",used)

if __name__=="__main__": main()
