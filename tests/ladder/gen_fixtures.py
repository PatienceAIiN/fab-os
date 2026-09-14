#!/usr/bin/env python3
# Fab OS — deterministic fixture generator for the graded agent ladder test (tests/agent-ladder-vm.sh).
# Copyright (c) 2026 Patience AI. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Build the ladder fixtures and the expected-values contract.

Everything here is deterministic (fixed seed, no clock, no network): running it twice produces byte-identical
files, so tests/ladder/expected.json can be committed and the harness can abort if the fixtures ever drift.

  python3 tests/ladder/gen_fixtures.py --out build/ladder-fixtures
    -> build/ladder-fixtures/ladder/...      the files scp'd into the VM as /tmp/ladder (the agent sees these)
    -> build/ladder-fixtures/expected.json   the answer key (scp'd into /tmp/ladder-check, NOT visible as a fixture)

  python3 tests/ladder/gen_fixtures.py --out DIR --write-expected tests/ladder/expected.json
    also refreshes the committed contract.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import random
import shutil

SEED = 20260914
HERE = os.path.dirname(os.path.abspath(__file__))

# --- access.log: counts are chosen explicitly so the top-3 ranking is unambiguous (no ties anywhere near the top)
TOP3 = [("203.0.113.7", 61), ("198.51.100.23", 47), ("192.0.2.44", 33)]
OTHER_IPS = ["198.51.100.5", "203.0.113.19", "192.0.2.8", "198.51.100.77", "203.0.113.55", "192.0.2.120",
             "198.51.100.9", "203.0.113.201", "192.0.2.33", "198.51.100.140", "203.0.113.88", "192.0.2.201"]
OTHER_COUNTS = [29, 25, 22, 19, 16, 13, 11, 9, 7, 5, 2, 1]          # sum 159; 61+47+33+159 = 300 lines
PATHS = ["/", "/index.html", "/api/status", "/assets/app.js", "/docs/install", "/favicon.ico", "/api/tasks", "/about"]
CODES = [200, 200, 200, 200, 304, 404, 500]

# --- notes/: three fact notes the agent must actually read, plus filler so the file count is not guessable
FACTS = {
    "fact_alpha.txt": "Project Alpha\nThe Alpha release shipped with a build size of 4472 kilobytes.\n",
    "fact_beta.txt": "Project Beta\nThe Beta team lead is Rosalind Farrier.\n",
    "fact_gamma.txt": "Project Gamma\nGamma runs on port 9312 under the codename Tungsten.\n",
}
FILLER = ["Weekly sync: nothing to report this week.\n",
          "Reminder: the coffee machine is on the second floor.\n",
          "Backlog grooming moved to Thursday morning.\n",
          "The office plants were watered.\n",
          "Parking permits renew automatically.\n"]
REPORT_TOKENS = ["4472", "Rosalind Farrier", "9312", "Tungsten"]     # must all appear in the L3c report

HELLO_TEXT = "Hello from Fab OS"
TYPED_SENTENCE = "Fab OS agents type twelve careful words into the editor without mistakes"   # exactly 12 words
DOC_TEXT = "Ladder test document"
HI_NOTE_TEXT = "hi"            # L2-f: the note typed in Fab Editor, saved as ~/Ladder/hi-note.txt, then mailed
REGIONS = ["north", "south", "east", "west"]
PRODUCTS = ["fabric", "loom", "thread", "needle", "shuttle"]
QUARTERS = [("sales-q1.csv", datetime.date(2026, 1, 5), 40), ("sales-q2.csv", datetime.date(2026, 4, 6), 44),
            ("sales-q3.csv", datetime.date(2026, 7, 6), 36)]
ARCHIVE_BYTES = 65536        # archive.dat is deliberately the largest file under /tmp/ladder


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as f:
        f.write(data)


def gen_access_log(rnd, path):
    """300 request lines whose per-IP counts are fixed by construction, then shuffled deterministically."""
    ips = []
    for ip, n in TOP3:
        ips += [ip] * n
    for ip, n in zip(OTHER_IPS, OTHER_COUNTS):
        ips += [ip] * n
    assert len(ips) == 300, len(ips)
    rnd.shuffle(ips)
    t0 = datetime.datetime(2026, 3, 12, 6, 0, 0)
    lines = []
    for i, ip in enumerate(ips):
        ts = (t0 + datetime.timedelta(seconds=i * 7 + (i % 5))).strftime("%d/%b/%Y:%H:%M:%S +0000")
        lines.append('%s - - [%s] "GET %s HTTP/1.1" %d %d\n' % (ip, ts, PATHS[i % len(PATHS)], CODES[i % len(CODES)], 180 + (i * 37) % 9000))
    write(path, "".join(lines))
    return {"lines": len(lines), "top3": [[ip, n] for ip, n in TOP3],
            "fourth": [OTHER_IPS[0], OTHER_COUNTS[0]], "distinct_ips": 3 + len(OTHER_IPS)}


def gen_sales(rnd, out_dir):
    files, grand = {}, 0
    for name, start, nrows in QUARTERS:
        rows, total = [], 0
        for i in range(nrows):
            amount = rnd.randrange(120, 9800)
            total += amount
            rows.append({"date": (start + datetime.timedelta(days=i * 2)).isoformat(),
                         "region": REGIONS[(i + len(name)) % len(REGIONS)],
                         "product": PRODUCTS[i % len(PRODUCTS)], "amount": amount})
        p = os.path.join(out_dir, name)
        os.makedirs(out_dir, exist_ok=True)
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["date", "region", "product", "amount"], lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        files[name] = {"rows": nrows, "total": total}
        grand += total
    return {"files": files, "grand_total": grand, "rows": sum(q["rows"] for q in files.values())}


def gen_notes(out_dir):
    names = []
    for name, body in FACTS.items():
        write(os.path.join(out_dir, name), body)
        names.append(name)
    for i, body in enumerate(FILLER, 1):
        name = "note_%02d.txt" % i
        write(os.path.join(out_dir, name), "Note %d\n%s" % (i, body))
        names.append(name)
    return sorted(names)


def gen_archive(path):
    """Deterministic filler, byte-for-byte reproducible (no RNG), sized so it is clearly the largest fixture."""
    chunk = b""
    i = 0
    while len(chunk) < ARCHIVE_BYTES:
        chunk += b"FABOS-LADDER-ARCHIVE-%06d-PADDING\n" % i
        i += 1
    write(path, chunk[:ARCHIVE_BYTES])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="staging directory (fixtures land in <out>/ladder)")
    ap.add_argument("--write-expected", help="also write the answer key to this path (the committed contract)")
    a = ap.parse_args()
    rnd = random.Random(SEED)
    stage = os.path.abspath(a.out)
    fx = os.path.join(stage, "ladder")
    if os.path.isdir(fx):
        shutil.rmtree(fx)
    os.makedirs(fx, exist_ok=True)

    access = gen_access_log(rnd, os.path.join(fx, "access.log"))
    sales = gen_sales(rnd, fx)
    notes = gen_notes(os.path.join(fx, "notes"))
    gen_archive(os.path.join(fx, "archive.dat"))
    for name in ("broken_script.py", "check_broken.py"):
        shutil.copyfile(os.path.join(HERE, name), os.path.join(fx, name))

    sizes = {}
    for root, _dirs, files in os.walk(fx):
        for n in files:
            p = os.path.join(root, n)
            sizes[os.path.relpath(p, fx)] = os.path.getsize(p)
    largest = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))

    expected = {
        "seed": SEED,
        "generated_by": "tests/ladder/gen_fixtures.py",
        "fixture_dir_in_vm": "/tmp/ladder",
        "hello_text": HELLO_TEXT,
        "typed_sentence": TYPED_SENTENCE,
        "doc_text": DOC_TEXT,
        "hi_note_text": HI_NOTE_TEXT,
        "ctx": {"a": "alpha", "b": "beta"},
        "trigger_word": "fired",
        "notes": {"count": len(notes), "files": notes, "fact_files": sorted(FACTS), "facts": FACTS},
        "report_tokens": REPORT_TOKENS,
        "sales": sales,
        "access_log": access,
        "largest": {"name": os.path.basename(largest[0]), "relpath": largest[0], "size": largest[1]},
        "sizes": dict(sorted(sizes.items())),
        "sha256": {rel: sha256(os.path.join(fx, rel)) for rel in sorted(sizes)},
    }
    blob = json.dumps(expected, indent=1, sort_keys=True) + "\n"
    write(os.path.join(stage, "expected.json"), blob)
    if a.write_expected:
        write(a.write_expected, blob)
    print("fixtures: %s (%d files, %d bytes)" % (fx, len(sizes), sum(sizes.values())))
    print("grand_total=%d notes=%d access_lines=%d largest=%s(%d)" %
          (sales["grand_total"], len(notes), access["lines"], expected["largest"]["name"], expected["largest"]["size"]))


if __name__ == "__main__":
    main()
