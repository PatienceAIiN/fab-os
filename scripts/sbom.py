#!/usr/bin/env python3
"""scripts/sbom.py — CycloneDX 1.5 software bill of materials of a built Fab OS image.

  scripts/sbom.py [--image localhost/fabos:vm] [--build-id ID] [--out build/sbom-<BUILD_ID>.cdx.json]

Everything comes from the image itself (read-only `podman run`): every dpkg package (name, version, architecture,
source package, homepage) with the licence declared in the first `License:` field of its /usr/share/doc/<pkg>/copyright
(Debian machine-readable format; "unknown" when the file is absent or not machine-readable), plus the components that
are not dpkg packages: the built-in Qwen2.5 model (from /usr/share/fabos/ai-models.json), the Whisper tiny.en weights
(/usr/share/fabos/voice/ggml-tiny.en.bin, sha256 computed in the image) and Firefox (its dpkg entry is annotated with Mozilla as
the supplier and packages.mozilla.org as the distribution when the image carries Mozilla's build, ADR-0018). The BUILD_ID is read from /etc/fabos/release (FABOS_BUILD_ID) unless given. Exit 0 and a one-line summary
with the component count; nothing is downloaded.
"""
import argparse, datetime, json, os, re, subprocess, sys, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Debian licence short names -> SPDX ids (only unambiguous ones; anything else is kept as a free-text name)
SPDX = {"GPL-2": "GPL-2.0-only", "GPL-2+": "GPL-2.0-or-later", "GPL-2.0+": "GPL-2.0-or-later", "GPL-3": "GPL-3.0-only", "GPL-3+": "GPL-3.0-or-later",
        "GPL-3.0+": "GPL-3.0-or-later", "LGPL-2": "LGPL-2.0-only", "LGPL-2+": "LGPL-2.0-or-later", "LGPL-2.0+": "LGPL-2.0-or-later", "LGPL-2.1": "LGPL-2.1-only",
        "LGPL-2.1+": "LGPL-2.1-or-later", "LGPL-3": "LGPL-3.0-only", "LGPL-3+": "LGPL-3.0-or-later", "LGPL-3.0+": "LGPL-3.0-or-later", "Expat": "MIT", "MIT": "MIT",
        "Apache-2.0": "Apache-2.0", "Apache-2": "Apache-2.0", "BSD-2-clause": "BSD-2-Clause", "BSD-3-clause": "BSD-3-Clause", "BSD-3-Clause": "BSD-3-Clause",
        "BSD-2-Clause": "BSD-2-Clause", "ISC": "ISC", "MPL-2.0": "MPL-2.0", "MPL-1.1": "MPL-1.1", "Zlib": "Zlib", "zlib": "Zlib", "CC0-1.0": "CC0-1.0", "CC0": "CC0-1.0",
        "OFL-1.1": "OFL-1.1", "PSF-2": "PSF-2.0", "Python": "PSF-2.0", "Artistic": "Artistic-1.0-Perl", "Artistic-2.0": "Artistic-2.0", "GFDL-1.3+": "GFDL-1.3-or-later",
        "GFDL-1.2+": "GFDL-1.2-or-later", "public-domain": "", "Unlicense": "Unlicense", "BSL-1.0": "BSL-1.0", "Boost-1.0": "BSL-1.0", "OpenSSL": "OpenSSL", "curl": "curl"}

# Runs INSIDE the image (python3 is part of every profile) and prints one JSON document.
INNER = r'''
import json, os, re, hashlib, subprocess
out = {"packages": [], "release": {}, "extras": {}}
q = subprocess.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\t${source:Package}\t${Homepage}\t${Description}\n"], capture_output=True, text=True).stdout
for line in q.splitlines():
    f = line.split("\t")
    if len(f) < 6:
        continue
    name, ver, arch, src, home, desc = f[0], f[1], f[2], f[3] or f[0], f[4], f[5]
    lic, fmt = "unknown", ""
    base = name.split(":")[0]
    for cand in (base, src.split(" ")[0]):
        p = "/usr/share/doc/%s/copyright" % cand
        if os.path.isfile(p):
            try:
                with open(p, errors="replace") as fh:
                    text = fh.read(200000)
            except OSError:
                continue
            fmt = "dep5" if text.lstrip().startswith("Format:") else "text"
            m = re.search(r"^License:\s*(.+)$", text, re.M)
            if m:
                lic = m.group(1).strip()
            break
    out["packages"].append({"name": name, "version": ver, "arch": arch, "source": src, "homepage": home, "description": desc, "license": lic, "copyright_format": fmt})
try:
    with open("/etc/fabos/release") as fh:
        for l in fh:
            if "=" in l and not l.startswith("#"):
                k, v = l.strip().split("=", 1); out["release"][k] = v
except OSError:
    pass
try:
    with open("/usr/lib/os-release") as fh:
        for l in fh:
            if "=" in l:
                k, v = l.strip().split("=", 1); out["release"]["os." + k] = v.strip('"')
except OSError:
    pass
try:
    out["extras"]["ai_models"] = json.load(open("/usr/share/fabos/ai-models.json"))
except Exception as e:
    out["extras"]["ai_models_error"] = str(e)
for path in ("/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf", "/usr/share/fabos/voice/ggml-tiny.en.bin"):
    if os.path.isfile(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        out["extras"][path] = {"bytes": os.path.getsize(path), "sha256": h.hexdigest()}
print(json.dumps(out))
'''


def spdx_or_name(lic):
    if not lic or lic == "unknown":
        return {"license": {"name": "unknown (no machine-readable licence field in the package's copyright file)"}}
    first = re.split(r"\s+(?:and|or)\s+|,|\s+with\s+", lic, maxsplit=1)[0].strip()
    sid = SPDX.get(first)
    if sid:
        return {"license": {"id": sid, "name": lic} if lic != first else {"id": sid}}
    return {"license": {"name": lic}}


def component_deb(p, distro):
    purl = "pkg:deb/%s/%s@%s?arch=%s" % (distro, p["name"].split(":")[0], p["version"], p["arch"])
    return {"type": "library" if p["name"].startswith("lib") else "application", "bom-ref": purl, "name": p["name"].split(":")[0], "version": p["version"], "purl": purl,
            "description": p["description"][:200], "licenses": [spdx_or_name(p["license"])],
            "properties": [{"name": "fabos:source-package", "value": p["source"]}, {"name": "fabos:copyright-format", "value": p["copyright_format"] or "none"}]
            + ([{"name": "fabos:homepage", "value": p["homepage"]}] if p["homepage"] else [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default="localhost/fabos:vm")
    ap.add_argument("--build-id")
    ap.add_argument("--out")
    a = ap.parse_args()
    r = subprocess.run(["podman", "run", "--rm", "-i", a.image, "python3", "-"], input=INNER, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        sys.exit("podman/python inside the image failed: " + r.stderr[-2000:])
    data = json.loads(r.stdout.strip().splitlines()[-1])
    rel = data["release"]
    build_id = a.build_id or rel.get("FABOS_BUILD_ID") or ("unknown-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    distro = rel.get("os.ID", "fabos")
    comps = [component_deb(p, "ubuntu") for p in sorted(data["packages"], key=lambda p: p["name"])]
    n_deb = len(comps)
    for pkg in data["packages"]:
        if pkg["name"].split(":")[0].startswith("fabos-"):
            ref = "pkg:deb/ubuntu/%s@%s?arch=%s" % (pkg["name"].split(":")[0], pkg["version"], pkg["arch"])
            for c in comps:
                if c["bom-ref"] == ref:
                    c["supplier"] = {"name": "Patience AI", "url": ["https://fabos.patienceai.in/"]}
                    c["licenses"] = [{"license": {"id": "Apache-2.0"}}]
                    c["purl"] = c["bom-ref"] = "pkg:deb/fabos/%s@%s?arch=%s" % (pkg["name"].split(":")[0], pkg["version"], pkg["arch"])
    extras = data["extras"]
    models = (extras.get("ai_models") or {})
    mlist = models.get("models") if isinstance(models, dict) else (models if isinstance(models, list) else [])
    qwen = extras.get("/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf")
    if qwen:
        meta = next((m for m in (mlist or []) if isinstance(m, dict) and "qwen" in json.dumps(m).lower()), {})
        comps.append({"type": "machine-learning-model", "bom-ref": "fabos:model:qwen2.5-1.5b-instruct-q4_k_m", "name": meta.get("name") or "Qwen2.5-1.5B-Instruct (GGUF Q4_K_M)",
                      "version": str(meta.get("version") or "2.5"), "supplier": {"name": "Alibaba Cloud"}, "licenses": [{"license": {"id": "Apache-2.0"}}],
                      "hashes": [{"alg": "SHA-256", "content": qwen["sha256"]}],
                      "externalReferences": [{"type": "distribution", "url": meta.get("url") or "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF"}],
                      "properties": [{"name": "fabos:path", "value": "/usr/share/fabos/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"}, {"name": "fabos:bytes", "value": str(qwen["bytes"])}]})
    wh = extras.get("/usr/share/fabos/voice/ggml-tiny.en.bin")
    if wh:
        comps.append({"type": "machine-learning-model", "bom-ref": "fabos:model:whisper-tiny.en", "name": "Whisper tiny.en (ggml)", "version": "tiny.en",
                      "supplier": {"name": "The ggml authors (conversion); OpenAI (original weights)"}, "licenses": [{"license": {"id": "MIT"}}],
                      "hashes": [{"alg": "SHA-256", "content": wh["sha256"]}],
                      "externalReferences": [{"type": "distribution", "url": "https://huggingface.co/ggerganov/whisper.cpp"}],
                      "properties": [{"name": "fabos:path", "value": "/usr/share/fabos/voice/ggml-tiny.en.bin"}, {"name": "fabos:bytes", "value": str(wh["bytes"])}]})
    firefox = next((p for p in data["packages"] if p["name"] == "firefox"), None)   # Mozilla's build (ADR-0018); Ubuntu's snap shim never reaches an image
    for c in comps:
        if firefox and c["name"] == "firefox":
            c["supplier"] = {"name": "Mozilla Foundation", "url": ["https://www.mozilla.org/firefox/"]}
            c["licenses"] = [{"license": {"id": "MPL-2.0", "name": "MPL-2.0; Mozilla's unmodified official .deb from packages.mozilla.org"}}]
            c["externalReferences"] = [{"type": "distribution", "url": "https://packages.mozilla.org/apt"}]
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": "urn:uuid:" + str(uuid.uuid4()), "version": 1,
           "metadata": {"timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tools": [{"vendor": "Patience AI", "name": "fabos sbom.py", "version": "1.0"}],
                        "component": {"type": "operating-system", "bom-ref": "fabos:os:" + build_id, "name": rel.get("os.NAME", "Fab OS"), "version": rel.get("os.VERSION_ID", "1.0"),
                                      "supplier": {"name": "Patience AI", "url": ["https://fabos.patienceai.in/"]},
                                      "properties": [{"name": "fabos:build-id", "value": build_id}, {"name": "fabos:image", "value": a.image},
                                                     {"name": "fabos:profile", "value": rel.get("FABOS_IMAGE_PROFILE", "unknown")},
                                                     {"name": "fabos:base", "value": "Ubuntu %s (%s)" % (rel.get("os.UBUNTU_CODENAME", ""), rel.get("os.ID_LIKE", ""))}]}},
           "components": comps}
    out = a.out or os.path.join(ROOT, "build", "sbom-%s.cdx.json" % build_id)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(bom, f, indent=1)
    unknown = sum(1 for p in data["packages"] if p["license"] == "unknown")
    print("SBOM %s: %d components (%d dpkg packages, %d with no machine-readable licence, %d non-dpkg: model/weights) -> %s"
          % (build_id, len(comps), n_deb, unknown, len(comps) - n_deb, out))


if __name__ == "__main__":
    main()
