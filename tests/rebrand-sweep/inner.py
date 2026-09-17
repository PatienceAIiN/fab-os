#!/usr/bin/env python3
"""Fab OS rebrand sweep — the guest side. Lists every product string a user of THIS machine can see that still names
KDE, Plasma, Kubuntu, Ubuntu or a K-prefixed / renamed upstream app, one line per hit:

    <surface>\t<file>\t<key>\t<text>

Surfaces (all resolved the way the running session resolves them — first file per basename along XDG_DATA_DIRS):
  launcher        .desktop entries the launcher / search / dock show (Name, GenericName, Comment; NoDisplay=false)
  notifier        .desktop entries hidden from the launcher whose Name is still what notifications and the task
                  switcher show for that app (NoDisplay=true) + knotifications6/*.notifyrc [Global] Name/Comment
  session         wayland-sessions / xsessions entries the login screen lists (from SDDM's SessionDir when set)
  kcm             System Settings modules: the Name/Description in the plugin metadata (CBOR inside the .so — what the
                  sidebar shows) and kservices6 / kglobalaccel / servicemenu / solid-action entries
  kded            background services (System Settings > Background Services lists these names)
  krunner         search plugins (Fab Search > Configure lists these)
  applet          Plasma widgets (Add Widgets lists Name + Description)
  kwin            effects, scripts, switchers, decorations (System Settings > Window Management)
  lnf             look-and-feel / global themes, Plasma styles, wallpaper plugins, login themes (Appearance pages)
  knsrc           "Get New…" dialog titles
  identity        os-release PRETTY_NAME / NAME, lsb-release, /etc/issue, kcm-about-distro
  kinfocenter     Info Center external modules and category names
  user-config     the SESSION USER's own ~/.config pins of KDE defaults (ksplashrc Theme, kdeglobals LookAndFeelPackage,
                  plasmarc Theme, kwinrc decoration, cursor theme): what an upgraded 1.0-5/1.0-6 install still carries
Everything found is printed; the host side (tests/rebrand-sweep-vm.sh) applies the allowlist and decides PASS/FAIL.
No Qt, no root: python3 only, so it runs on any installed Fab OS as the logged-in user.
"""
import glob, io, json, os, re, struct, subprocess, sys

FLAG = re.compile(r"KDE|Plasma|Kubuntu|\bUbuntu\b|KWin|\bK[A-Z][a-z]{2,}[A-Za-z]*\b|\bKonsole\b|\bDolphin\b|\bDiscover\b|"
                  r"\bKate\b|\bOkular\b|\bGwenview\b|\bSpectacle\b|\bArk\b|\bKamoso\b|\bBreeze\b|\bKirigami\b|\bSystem Settings\b|"
                  r"\bInfo Cent(er|re)\b|\bSystem Monitor\b|\bKDED?\b|\bKInfoCenter\b")
out = set()


def hit(surface, path, key, text):
    text = str(text).strip()
    if text and FLAG.search(text):
        out.add((surface, path, key, text))


# ---------------------------------------------------------------- environment of the running session (or a sane default)
def session_env():
    env = {}
    try:
        for line in subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=10).stdout.splitlines():
            k, _, v = line.partition("=")
            env[k] = v
    except Exception:
        pass
    return env


ENV = session_env()
XDG = [d for d in (ENV.get("XDG_DATA_DIRS") or os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
LANGS = [l for l in (ENV.get("LANGUAGE") or os.environ.get("LANGUAGE") or "").split(":") if l]
print("# XDG_DATA_DIRS=" + ":".join(XDG), file=sys.stderr)
print("# LANGUAGE=" + ":".join(LANGS), file=sys.stderr)


def resolve(sub, pattern="*.desktop"):
    """first file per basename along XDG_DATA_DIRS -> {basename: path}"""
    seen = {}
    for d in XDG:
        for p in sorted(glob.glob(os.path.join(d, sub, pattern))):
            seen.setdefault(os.path.basename(p), p)
    return seen


def read_desktop(path):
    """[Desktop Entry] (or [Global]) group only -> dict; keys keep their [locale] suffix"""
    ent = {}; grp = None
    try:
        for line in io.open(path, encoding="utf-8", errors="replace"):
            line = line.rstrip("\n")
            if line.startswith("["):
                grp = line.strip()
                continue
            if grp not in ("[Desktop Entry]", "[Global]") or "=" not in line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            ent[k.strip()] = v.strip()
    except OSError:
        pass
    return ent


def visible_value(ent, key):
    """what KService shows: the first LANGUAGE variant present, else the plain key"""
    for l in LANGS:
        if (key + "[" + l + "]") in ent:
            return ent[key + "[" + l + "]"]
    return ent.get(key, "")


# ---------------------------------------------------------------- 1. desktop entries
for b, p in sorted(resolve("applications").items()):
    e = read_desktop(p)
    if e.get("Hidden") == "true":
        continue
    hidden = e.get("NoDisplay") == "true"
    surface = "notifier" if hidden else "launcher"
    for key in ("Name", "GenericName", "Comment"):
        if hidden and key != "Name":
            continue
        hit(surface, p, key, visible_value(e, key))

# ---------------------------------------------------------------- 2. notifyrc app names
for b, p in sorted(resolve("knotifications6", "*.notifyrc").items()):
    e = read_desktop(p)
    for key in ("Name", "Comment"):
        hit("notifier", p, key, visible_value(e, key))

# ---------------------------------------------------------------- 3. login-screen sessions
sess_dirs = []
for conf in sorted(glob.glob("/etc/sddm.conf.d/*.conf")) + ["/etc/sddm.conf"]:
    try:
        for line in open(conf):
            if line.strip().startswith("SessionDir="):
                sess_dirs += line.split("=", 1)[1].strip().split(",")
    except OSError:
        pass
if not sess_dirs:
    sess_dirs = ["/usr/local/share/wayland-sessions", "/usr/share/wayland-sessions", "/usr/share/xsessions"]
for d in sess_dirs:
    for p in sorted(glob.glob(os.path.join(d.strip(), "*.desktop"))):
        e = read_desktop(p)
        if e.get("Hidden") == "true" or e.get("NoDisplay") == "true":
            continue
        for key in ("Name", "Comment"):
            hit("session", p, key, visible_value(e, key))


# ---------------------------------------------------------------- 4. Qt plugin metadata (KCMs, kded, krunner, kwin, applets): CBOR after "QTMETADATA !"
def cbor(data, pos):
    ib = data[pos]; mt, ai = ib >> 5, ib & 0x1f; pos += 1
    if ai < 24: val = ai
    elif ai == 24: val = data[pos]; pos += 1
    elif ai == 25: val = struct.unpack(">H", data[pos:pos + 2])[0]; pos += 2
    elif ai == 26: val = struct.unpack(">I", data[pos:pos + 4])[0]; pos += 4
    elif ai == 27: val = struct.unpack(">Q", data[pos:pos + 8])[0]; pos += 8
    elif ai == 31: val = None                      # indefinite length
    else: raise ValueError("cbor ai %d" % ai)
    if mt == 0: return val, pos
    if mt == 1: return -1 - val, pos
    if mt in (2, 3):
        if val is None:
            chunks = b""
            while data[pos] != 0xff:
                c, pos = cbor(data, pos); chunks += c if isinstance(c, bytes) else c.encode()
            return (chunks.decode("utf-8", "replace") if mt == 3 else chunks), pos + 1
        raw = data[pos:pos + val]; pos += val
        return (raw.decode("utf-8", "replace") if mt == 3 else raw), pos
    if mt == 4:
        arr = []
        if val is None:
            while data[pos] != 0xff:
                x, pos = cbor(data, pos); arr.append(x)
            return arr, pos + 1
        for _ in range(val):
            x, pos = cbor(data, pos); arr.append(x)
        return arr, pos
    if mt == 5:
        m = {}
        if val is None:
            while data[pos] != 0xff:
                k, pos = cbor(data, pos); v, pos = cbor(data, pos); m[k] = v
            return m, pos + 1
        for _ in range(val):
            k, pos = cbor(data, pos); v, pos = cbor(data, pos); m[k] = v
        return m, pos
    if mt == 6:
        return cbor(data, pos)                     # tag: ignore
    if mt == 7:
        if ai == 20: return False, pos
        if ai == 21: return True, pos
        if ai in (22, 23): return None, pos
        if ai == 25: return struct.unpack(">e", data[pos - 2:pos])[0], pos
        if ai == 26: return struct.unpack(">f", data[pos - 4:pos])[0], pos
        if ai == 27: return struct.unpack(">d", data[pos - 8:pos])[0], pos
        return None, pos
    raise ValueError("cbor mt %d" % mt)


def plugin_metadata(path):
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    # Qt 6 puts the metadata in an ELF note named "qt-project!" (name + NUL, padded to 12 bytes, then the 4-byte header
    # version/major/minor/patch, then CBOR); the older ".qtmetadata" section starts with "QTMETADATA !" + 4 header bytes.
    i = data.find(b"qt-project!\x00")
    start = i + 12 + 4 if i >= 0 else -1
    if start < 0:
        i = data.find(b"QTMETADATA !")
        start = i + 16 if i >= 0 else -1
    if start < 0:
        return None
    try:
        m, _ = cbor(data, start)
    except Exception:
        return None
    md = m.get(4) if isinstance(m, dict) else None   # QtPluginMetaDataKeys::MetaData
    return md if isinstance(md, dict) else None


def kplugin_names(md):
    kp = md.get("KPlugin") if isinstance(md, dict) else None
    if not isinstance(kp, dict):
        return {}
    res = {}
    for key in ("Name", "Description"):
        for l in LANGS:
            if key + "[" + l + "]" in kp:
                res[key] = kp[key + "[" + l + "]"]; break
        else:
            if key in kp:
                res[key] = kp[key]
    return res


LIB = "/usr/lib/x86_64-linux-gnu/qt6/plugins"
PLUGIN_SURFACES = [
    ("kcm", LIB + "/plasma/kcms/**/*.so"), ("kcm", LIB + "/kf6/kcms/**/*.so"),
    ("kded", LIB + "/kf6/kded/*.so"), ("krunner", LIB + "/kf6/krunner/*.so"),
    ("kwin", LIB + "/kwin/effects/plugins/*.so"), ("kwin", LIB + "/org.kde.kdecoration3/*.so"), ("kwin", LIB + "/org.kde.kdecoration3.kcm/*.so"),
    ("applet", LIB + "/plasma/applets/*.so"), ("kinfocenter", LIB + "/plasma/kcms/kinfocenter/*.so"),
]
parsed = {}
for surface, pat in PLUGIN_SURFACES:
    for p in sorted(glob.glob(pat, recursive=True)):
        if p.endswith(".fabos-orig") or p.endswith(".disabled-by-fabos"):
            continue
        md = plugin_metadata(p)
        if not md:
            continue
        parsed[surface] = parsed.get(surface, 0) + 1
        for key, val in kplugin_names(md).items():
            hit(surface, p, "KPlugin." + key, val)
print("# plugins with readable metadata: " + ", ".join("%s=%d" % kv for kv in sorted(parsed.items())), file=sys.stderr)

# ---------------------------------------------------------------- 5. KPackage metadata (applets, kwin, look-and-feel, styles, wallpapers, login themes) + service files
PKG_SURFACES = [
    ("applet", "plasma/plasmoids/*/metadata.json"), ("applet", "plasma/plasmoids/*/metadata.desktop"),
    ("kwin", "kwin/effects/*/metadata.json"), ("kwin", "kwin/scripts/*/metadata.json"), ("kwin", "kwin/tabbox/*/metadata.json"),
    ("kwin", "kwin/tabbox/*/metadata.desktop"), ("kwin", "aurorae/themes/*/metadata.desktop"),
    ("lnf", "plasma/look-and-feel/*/metadata.json"), ("lnf", "plasma/desktoptheme/*/metadata.json"), ("lnf", "plasma/desktoptheme/*/metadata.desktop"),
    ("lnf", "plasma/wallpapers/*/metadata.json"), ("lnf", "plasma/shells/*/metadata.json"), ("lnf", "wallpapers/*/metadata.json"),
    ("lnf", "plasma/layout-templates/*/metadata.json"), ("lnf", "sddm/themes/*/metadata.desktop"),
    ("kinfocenter", "plasma/kinfocenter/externalmodules/*.desktop"), ("kinfocenter", "plasma/kinfocenter/categories/*.desktop"),
    ("kcm", "kservices6/*.desktop"), ("kcm", "kglobalaccel/*.desktop"), ("kcm", "kio/servicemenus/*.desktop"),
    ("kcm", "solid/actions/*.desktop"), ("kcm", "kf6/searchproviders/*.desktop"),
]
for surface, pat in PKG_SURFACES:
    seen = {}
    for d in XDG:
        for p in sorted(glob.glob(os.path.join(d, pat))):
            seen.setdefault(os.path.relpath(p, d), p)
    for rel, p in sorted(seen.items()):
        if p.endswith(".json"):
            try:
                j = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            for key, val in kplugin_names(j).items():
                hit(surface, p, "KPlugin." + key, val)
        else:
            e = read_desktop(p)
            if e.get("Hidden") == "true" or e.get("NoDisplay") == "true":
                continue
            for key in ("Name", "Comment", "GenericName"):
                hit(surface, p, key, visible_value(e, key))

# ---------------------------------------------------------------- 6. "Get New…" titles, identity, Info Center labels
for p in sorted(glob.glob("/usr/share/knsrcfiles/*.knsrc")):
    for line in io.open(p, encoding="utf-8", errors="replace"):
        if line.startswith("Name="):
            hit("knsrc", p, "Name", line[5:])
for p, keys in (("/etc/os-release", ("NAME", "PRETTY_NAME", "HOME_URL")), ("/etc/lsb-release", ("DISTRIB_ID", "DISTRIB_DESCRIPTION"))):
    try:
        for line in open(p):
            k, _, v = line.strip().partition("=")
            if k in keys:
                hit("identity", p, k, v.strip('"'))
    except OSError:
        pass
for p in ("/etc/issue", "/etc/issue.net", "/etc/xdg/kcm-about-distrorc"):
    try:
        hit("identity", p, "text", open(p, errors="replace").read().replace("\n", " "))
    except OSError:
        pass

# ---------------------------------------------------------------- 7. the session user's own config pins (what an upgraded install keeps)
CFG = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
PINS = [("ksplashrc", "KSplash", "Theme"), ("kdeglobals", "KDE", "LookAndFeelPackage"), ("kdeglobals", "General", "ColorScheme"),
        ("kdeglobals", "Icons", "Theme"), ("plasmarc", "Theme", "name"), ("kwinrc", "org.kde.kdecoration2", "theme"),
        ("kwinrc", "org.kde.kdecoration2", "library"), ("kcminputrc", "Mouse", "cursorTheme"), ("kscreenlockerrc", "Greeter", "Theme")]


def kread(path, group, key):
    cur = None
    try:
        for line in io.open(path, encoding="utf-8", errors="replace"):
            line = line.rstrip("\n")
            if line.startswith("["):
                cur = line.strip("[]")
            elif cur == group and line.startswith(key + "="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return None


for f, g, k in PINS:
    v = kread(os.path.join(CFG, f), g, k)
    if v is not None and re.search(r"breeze|kde|plasma|oxygen", v, re.I):
        out.add(("user-config", os.path.join(CFG, f), "[%s] %s" % (g, k), v))

for surface, path, key, text in sorted(out):
    print("%s\t%s\t%s\t%s" % (surface, path, key, text))
print("# hits=%d" % len(out), file=sys.stderr)
