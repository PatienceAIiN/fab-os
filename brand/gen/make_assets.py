#!/usr/bin/env python3
"""Render every Fabric OS brand asset from brand.conf + the SVG mark.

Deterministic, dependency-light (Pillow only). Outputs:
  icons/           hicolor PNG icons (16..512) + fabric-os.svg copies
  pixmaps/         logo + wordmark PNGs (light/dark)
  plymouth/        spinner frames (rotating ring) + wordmark for the boot splash
  wallpapers/      procedural weave wallpapers (dark + light) at 3 sizes
  sddm/            greeter background
  3d/              fabric-mark.glb + fabric-mark.obj (torus + 3 bars), CC0/Apache-2.0
"""
import argparse, glob, json, math, os, shlex, struct, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
def draw_mark(size, color, rot_deg=-24, ring_dash=None, spin=0.0, bar_phase=0.0, ss=4):
    """Ring + three woven bars, as in brand/logo/fabric-mark.svg (64-unit grid)."""
    S = size * ss; u = S / 64.0
    img = Image.new("RGBA", (S, S), (0,0,0,0)); d = ImageDraw.Draw(img)
    cx = cy = 32*u; r = 20*u; w = 4*u
    box = [cx-r, cy-r, cx+r, cy+r]
    if ring_dash is None:
        d.ellipse(box, outline=color, width=int(w))
    else:
        start = (spin*360) % 360; d.arc(box, start, start+ring_dash, fill=color, width=int(w))
        # round caps
        for ang in (start, start+ring_dash):
            a = math.radians(ang); px, py = cx + r*math.cos(a), cy + r*math.sin(a)
            d.ellipse([px-w/2, py-w/2, px+w/2, py+w/2], fill=color)
    for i,(x,h0) in enumerate(((23,14),(30,20),(37,14))):
        # breathing bars for animation frames (bar_phase 0 = static)
        amp = 3*u*math.sin(bar_phase*2*math.pi + i*0.9) if bar_phase else 0
        h = h0*u + (amp if i != 1 else -amp)
        y = cy - h/2
        d.rounded_rectangle([x*u, y, x*u+4*u, y+h], radius=2*u, fill=color)
    img = img.rotate(-rot_deg, resample=Image.BICUBIC, center=(cx,cy))
    return img.resize((size,size), Image.LANCZOS)

def tile(size, fg, bg, radius_frac=0.25, ss=4):
    S = size*ss; img = Image.new("RGBA",(S,S),(0,0,0,0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle([0,0,S-1,S-1], radius=int(S*radius_frac), fill=bg)
    m = draw_mark(size, fg, ss=ss).resize((S,S), Image.LANCZOS)
    img.alpha_composite(m); return img.resize((size,size), Image.LANCZOS)

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
    mark = draw_mark(int(size_px*1.6), fg)
    wm, _ = wordmark(font_dirs, name, "by " + vendor, fg, size_px)
    gap = size_px//3
    W = mark.width + gap + wm.width; H = max(mark.height, wm.height)
    img = Image.new("RGBA",(W,H),(0,0,0,0))
    img.alpha_composite(mark, (0, (H-mark.height)//2)); img.alpha_composite(wm, (mark.width+gap, (H-wm.height)//2))
    return img

# ---------- wallpaper ----------
def weave_wallpaper(W, H, dark=True, accent="#3B6EF5", accent2="#6E9BFF", bg="#0E1116"):
    base = hexrgb(bg) if dark else hexrgb("#F6F7F9")
    img = Image.new("RGBA",(W,H), base)
    glow = Image.new("RGBA",(W,H),(0,0,0,0)); g = ImageDraw.Draw(glow)
    blobs = [(0.72,0.28,0.55,accent,150 if dark else 90),(0.18,0.78,0.45,accent2,110 if dark else 60),
             (0.50,0.95,0.60,"#1F9D57",45 if dark else 30),(0.05,0.10,0.35,accent2,70 if dark else 40)]
    for fx,fy,fr,col,alpha in blobs:
        r = fr*W*0.45; cx,cy = fx*W, fy*H
        g.ellipse([cx-r,cy-r*0.7,cx+r,cy+r*0.7], fill=hexrgb(col, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(W*0.09))
    img.alpha_composite(glow)
    # subtle woven texture: two families of diagonal threads
    weave = Image.new("RGBA",(W,H),(0,0,0,0)); wd = ImageDraw.Draw(weave)
    step = max(6, W//160); a = 14 if dark else 10
    col1 = (255,255,255,a) if dark else (0,0,0,a); col2 = (0,0,0,a) if dark else (255,255,255,a+6)
    for k in range(-H, W+H, step):
        wd.line([(k,0),(k+H,H)], fill=col1, width=1)
        wd.line([(k,H),(k+H,0)], fill=col2, width=1)
    weave = weave.filter(ImageFilter.GaussianBlur(0.6))
    img.alpha_composite(weave)
    # vignette
    vig = Image.new("L",(W,H),0); vd = ImageDraw.Draw(vig)
    vd.ellipse([-W*0.2,-H*0.3,W*1.2,H*1.3], fill=255); vig = vig.filter(ImageFilter.GaussianBlur(W*0.12))
    shade = Image.new("RGBA",(W,H),(0,0,0,0) if not dark else (0,0,0,90))
    shade.putalpha(Image.eval(vig, lambda v: int((255-v)*(0.55 if dark else 0.15))))
    img.alpha_composite(shade)
    return img.convert("RGB")

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
        f.write("# Fabric OS mark — CC0-1.0 / Apache-2.0\no fabric_mark\n")
        for v in V: f.write("v %.5f %.5f %.5f\n"%v)
        for n in N: f.write("vn %.5f %.5f %.5f\n"%n)
        for k in range(0,len(I),3): f.write("f %d//%d %d//%d %d//%d\n"%(I[k]+1,I[k]+1,I[k+1]+1,I[k+1]+1,I[k+2]+1,I[k+2]+1))
    pos=b"".join(struct.pack("<fff",*v) for v in V); nor=b"".join(struct.pack("<fff",*n) for n in N)
    idx=b"".join(struct.pack("<I",i) for i in I)
    def pad4(b, c=b"\x00"): return b + c*((4-len(b)%4)%4)
    bin_ = pad4(pos)+pad4(nor)+pad4(idx)
    mins=[min(v[k] for v in V) for k in range(3)]; maxs=[max(v[k] for v in V) for k in range(3)]
    c=[int(color[1:3],16)/255,int(color[3:5],16)/255,int(color[5:7],16)/255,1.0]
    gltf={"asset":{"version":"2.0","generator":"fabric-os make_assets.py","copyright":"2026 Patience AI, CC0-1.0 OR Apache-2.0"},
      "scene":0,"scenes":[{"nodes":[0]}],"nodes":[{"mesh":0,"name":"FabricMark"}],
      "meshes":[{"primitives":[{"attributes":{"POSITION":0,"NORMAL":1},"indices":2,"material":0}]}],
      "materials":[{"name":"FabricInk","pbrMetallicRoughness":{"baseColorFactor":c,"metallicFactor":0.2,"roughnessFactor":0.45}}],
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

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",required=True); ap.add_argument("--conf",default=os.path.join(os.path.dirname(__file__),"..","brand.conf"))
    ap.add_argument("--font-dir",action="append",default=["/usr/share/fonts"]); ap.add_argument("--quick",action="store_true",help="skip 4K wallpapers")
    a=ap.parse_args(); C=load_conf(a.conf); out=a.out
    for d in ("icons","pixmaps","plymouth","wallpapers","sddm","3d","meta"): os.makedirs(os.path.join(out,d),exist_ok=True)
    ink=hexrgb(C["INK"]); white=(255,255,255,255); accent=hexrgb(C["ACCENT"]); light=hexrgb(C["BG_LIGHT"]); dark=hexrgb(C["BG_DARK"])
    name=C["DISTRO_NAME"]; vendor=C["VENDOR_NAME"]
    # icons: tile on light for hicolor apps, plus a monochrome symbolic
    for s in (16,22,24,32,48,64,128,256,512):
        tile(s, ink, light).save(os.path.join(out,"icons",f"fabric-os-{s}.png"))
    draw_mark(512, white).save(os.path.join(out,"icons","fabric-os-symbolic-white.png"))
    draw_mark(512, ink).save(os.path.join(out,"icons","fabric-os-symbolic-dark.png"))
    # pixmaps / lockups
    logo_lockup(a.font_dir, name, vendor, ink, 72).save(os.path.join(out,"pixmaps","fabric-os-logo.png"))
    logo_lockup(a.font_dir, name, vendor, white, 72).save(os.path.join(out,"pixmaps","fabric-os-logo-dark.png"))
    tile(128, ink, light).save(os.path.join(out,"pixmaps","fabric-os.png"))
    wm,used=wordmark(a.font_dir, name, "by "+vendor, white, 56); wm.save(os.path.join(out,"plymouth","wordmark.png"))
    wordmark(a.font_dir, name, None, white, 40)[0].save(os.path.join(out,"sddm","wordmark.png"))
    # plymouth spinner frames: 36 frames, dashed ring rotating + breathing bars
    for k in range(36):
        draw_mark(128, white, ring_dash=250, spin=k/36.0, bar_phase=k/36.0).save(os.path.join(out,"plymouth",f"spinner-{k:02d}.png"))
    draw_mark(128, white).save(os.path.join(out,"plymouth","mark.png"))
    # wallpapers
    sizes=[(1920,1080),(2560,1440)] + ([] if a.quick else [(3840,2160)])
    for (W,H) in sizes:
        weave_wallpaper(W,H,True,C["ACCENT"],C["ACCENT_DARK"],C["BG_DARK"]).save(os.path.join(out,"wallpapers",f"dark-{W}x{H}.png"), optimize=True)
        weave_wallpaper(W,H,False,C["ACCENT"],C["ACCENT_DARK"],C["BG_DARK"]).save(os.path.join(out,"wallpapers",f"light-{W}x{H}.png"), optimize=True)
    wp=Image.open(os.path.join(out,"wallpapers","dark-1920x1080.png")); wp.resize((400,225),Image.LANCZOS).save(os.path.join(out,"wallpapers","screenshot.png"))
    wp.save(os.path.join(out,"sddm","background.png"))
    # 3D
    parts=[torus(20.0,2.0)]
    for (x,h) in ((25,14),(32,20),(39,14)): parts.append(rounded_bar(x-32, 4.0, float(h), 4.0, 2.0))
    write_gltf(os.path.join(out,"3d","fabric-mark.glb"), os.path.join(out,"3d","fabric-mark.obj"), parts, C["INK"])
    json.dump({"font_used":used,"conf":C},open(os.path.join(out,"meta","assets.json"),"w"),indent=1)
    print("assets rendered to",out,"| font:",used)

if __name__=="__main__": main()
