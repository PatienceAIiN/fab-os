#!/usr/bin/env python3
"""Pull a Figma file's frames (PNG @2x), colour/type styles and top-level node names for the Fab OS UI revamp.
Usage: FIGMA_TOKEN=figd_xxx python3 design/tina/pull.py [FILE_KEY] [NODE_IDS...]  -> design/tina/export/"""
import json, os, sys, urllib.request
KEY = sys.argv[1] if len(sys.argv) > 1 else "zSOuPXkqig1VIJJf86uPkt"
TOKEN = os.environ.get("FIGMA_TOKEN") or sys.exit("set FIGMA_TOKEN (Figma → Settings → Personal access tokens)")
OUT = os.path.join(os.path.dirname(__file__), "export"); os.makedirs(OUT, exist_ok=True)
def api(path):
    req = urllib.request.Request("https://api.figma.com/v1" + path, headers={"X-Figma-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=120) as r: return json.loads(r.read())
doc = api("/files/%s?depth=2" % KEY)
json.dump({"name": doc["name"], "lastModified": doc["lastModified"], "styles": doc.get("styles", {})}, open(os.path.join(OUT, "file.json"), "w"), indent=1)
frames = [(n["id"], n["name"]) for page in doc["document"]["children"] for n in page.get("children", []) if n["type"] in ("FRAME", "COMPONENT", "COMPONENT_SET", "SECTION")]
json.dump(frames, open(os.path.join(OUT, "frames.json"), "w"), indent=1)
print(doc["name"], "-", len(frames), "top-level frames")
ids = sys.argv[2:] or [f[0] for f in frames[:40]]
imgs = api("/images/%s?ids=%s&format=png&scale=2" % (KEY, ",".join(ids)))["images"]
for fid, url in imgs.items():
    if not url: continue
    name = dict(frames).get(fid, fid).replace("/", "_").replace(" ", "_")
    urllib.request.urlretrieve(url, os.path.join(OUT, "%s_%s.png" % (fid.replace(":", "-"), name))); print("saved", name)
styles = api("/files/%s/styles" % KEY).get("meta", {}).get("styles", [])
json.dump(styles, open(os.path.join(OUT, "styles.json"), "w"), indent=1); print(len(styles), "published styles")
