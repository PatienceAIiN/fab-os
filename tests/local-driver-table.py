#!/usr/bin/env python3
# Fab OS — render the BEFORE/AFTER table of tests/local-driver-test.py runs as Markdown (for docs/LOW-RAM.md and ADR-0020).
# Copyright (c) 2026 Patience AI. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""python3 tests/local-driver-table.py build/local-driver-before.json build/local-driver-after.json

Every cell comes from the JSON the harness wrote (PASS/FAIL decided by tests/ladder/checks.py, seconds, the tools that ran, the
task's own status) — nothing is typed by hand."""
import json
import sys

TASKS = [("l1-a", "create ~/Ladder/one/hello.txt with the exact text"), ("l1-b", "count the files in /tmp/ladder/notes, answer FILE COUNT: n"),
         ("l1-c", "open Fab Terminal and leave it running"), ("l1-d", "today's date (ISO) as the first line of ~/Ladder/one/date.txt"),
         ("l1-e", "copy /tmp/ladder/notes to ~/Ladder/notes-copy"), ("l1-f", "open Fab Editor, type hello (open_app then type_text)"),
         ("l2-a", "sum the amount column of three CSVs into ~/Ladder/total.txt"), ("l2-b", "rename every .txt in ~/Ladder/notes-copy to .md"),
         ("l2-c", "largest file under /tmp/ladder, base name into ~/Ladder/largest.txt"), ("l2-d", "open Fab Editor, type a sentence, save it as ~/Ladder/typed.txt"),
         ("l2-e", "web_fetch the daemon's /health, save the JSON unchanged"), ("l2-f", "type a hi note in Fab Editor and mail it (needs a mail account)"),
         # held-out (tests/local-driver-test.py HELDOUT): the L2 shapes whose idioms are worked examples in the executor prompt, with other names — in no prompt
         ("h-a", "held-out: sum the qty column of two CSVs into ~/Ladder/qty-total.txt"), ("h-b", "held-out: rename every .log in ~/Ladder/logs-copy to .bak (a .md file stays)"),
         ("h-c", "held-out: smallest file under /tmp/heldout/tree, base name into ~/Ladder/smallest.txt"), ("h-d", "held-out: count the files in /tmp/heldout/logs, answer FILE COUNT: n")]


def cell(row):
    if not row:
        return "—"
    if row["status"] == "SKIP":
        return "SKIP (optional)"
    return "**%s** %ss · %s · task %s" % (row["status"], row["seconds"], row["tools"], row["task_status"])


def main(paths):
    runs = []
    for p in paths:
        with open(p) as f:
            d = json.load(f)
        runs.append((d["label"], d, {r["task"]: r for r in d["tasks"]}))
    head = "| Task | What the ladder asks |" + "".join(" %s (%s) |" % (lab, d.get("finished", "")[:10]) for lab, d, _ in runs)
    print(head)
    print("|---|---|" + "---|" * len(runs))
    for name, what in TASKS:
        if name.startswith("h-") and not any(name in rows for _, _, rows in runs):
            continue                                                           # a run without the held-out tasks (before they existed): no empty rows
        print("| %s | %s |" % (name, what) + "".join(" %s |" % cell(rows.get(name)) for _, _, rows in runs))
    print("| **Score** | L1 of 6 · L2 of 5 · held-out of 4 |" + "".join(" **L1 %s · L2 %s · held-out %s** |" % (d["summary"].get("l1", "—"), d["summary"].get("l2", "—"), d["summary"].get("h", "not run")) for _, d, _ in runs))
    print("| llama-server peak RSS (`VmHWM`) | after the whole run |" + "".join(" %.2f GB |" % (d.get("server_vmhwm_kb", 0) / 1e6) for _, d, _ in runs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["build/local-driver-before.json", "build/local-driver-after.json"]))
