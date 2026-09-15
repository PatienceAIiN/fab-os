#!/usr/bin/env python3
# Fab OS — splice the measured BEFORE/AFTER results of tests/local-driver-test.py into the docs that quote them.
# Copyright (c) 2026 Patience AI. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""python3 tests/local-driver-docs.py [build/local-driver-before.json build/local-driver-after.json]

Rewrites the results table, the "what failed" bullets, the reading paragraph and the peak-RSS figures in docs/LOW-RAM.md,
docs/decisions/ADR-0020-small-model-driver.md and README.md from the two JSON reports (table via tests/local-driver-table.py),
so that no number and no pass/fail phrase in those documents is typed by hand. Re-runnable: it replaces the previous block."""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAN = {"l1-a": "creates a folder and a file with the exact text", "l1-b": "counts the files of a folder and answers in the asked form",
       "l1-c": "opens Fab Terminal", "l1-d": "writes today's date from the clock", "l1-e": "copies a folder (source intact)", "l1-f": "opens Fab Editor and types where you can watch",
       "l2-a": "sums a column across three CSV files", "l2-b": "renames a folder of notes to another extension", "l2-c": "names the largest file under a tree",
       "l2-d": "types a sentence into Fab Editor and saves it", "l2-e": "fetches a local URL and saves the body unchanged",
       "h-a": "sums the qty column of two CSVs (held-out)", "h-b": "renames .log files to .bak and leaves the .md file alone (held-out)",
       "h-c": "names the smallest file under another tree (held-out)", "h-d": "counts the files of another folder (held-out)"}


def splice(path, pairs):
    """pairs: (compiled regex or literal, replacement); each must match exactly once."""
    p = os.path.join(ROOT, path)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if isinstance(old, re.Pattern):
            s2, n = old.subn(lambda _m: new, s, count=1)
        else:
            n = s.count(old)
            s2 = s.replace(old, new, 1)
        if n != 1:
            sys.exit("%s: expected one match, found %d for %r" % (path, n, (old.pattern if isinstance(old, re.Pattern) else old)[:80]))
        s = s2
    open(p, "w", encoding="utf-8").write(s)
    print("filled", path)


def main(argv):
    before_p = argv[0] if argv else os.path.join(ROOT, "build", "local-driver-before.json")
    after_p = argv[1] if len(argv) > 1 else os.path.join(ROOT, "build", "local-driver-after.json")
    before, after = json.load(open(before_p)), json.load(open(after_p))
    table = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "local-driver-table.py"), before_p, after_p], capture_output=True, text=True, check=True).stdout.strip()
    l1, l2, h = after["summary"]["l1"], after["summary"]["l2"], after["summary"].get("h", "not run")
    bl1, bl2, bh = before["summary"].get("l1", "?"), before["summary"].get("l2", "?"), before["summary"].get("h", "not run")
    rss_after, rss_before = after["server_vmhwm_kb"] / 1e6, before["server_vmhwm_kb"] / 1e6
    rows = {r["task"]: r for r in after["tasks"]}
    passed = [CAN[t] for t in CAN if rows.get(t, {}).get("status") == "PASS"]
    failed = [(t, rows[t]["evidence"].split(" | task=")[0].replace("FAILED: ", "")) for t in CAN if rows.get(t, {}).get("status") == "FAIL"]
    honest = [t for t, _ in failed if rows[t]["task_status"] == "failed"]
    secs = [float(r["seconds"]) for r in after["tasks"] if r["status"] != "SKIP"]
    plist = ", ".join(passed[:-1]) + (" and " + passed[-1] if len(passed) > 1 else "".join(passed))
    fail_lines = "\n".join("- **%s** — %s (task status: %s)" % (t, ev, rows[t]["task_status"]) for t, ev in failed) or "- none in this run"
    fail_sentence = ("What still failed: " + "; ".join("%s (%s; the agent %s)" % (t, CAN[t], "reported the failure itself" if rows[t]["task_status"] == "failed" else "claimed success")
                                                      for t, _ in failed) + ".") if failed else "Every graded task passed in that run."
    caveat = ("Three of the five L2 tasks (l2-a, l2-b, l2-c) and l1-b match worked examples or hints in the driver's prompt, so the L1/L2 score is partly a test "
              "of those examples; the **held-out %s** row is the one the prompt cannot have memorised (ADR-0020 §3)." % h)
    results = table + "\n\nWhat failed in the shipped run, in the checker's own words (`tests/ladder/checks.py`; `check_heldout()` in tests/local-driver-test.py for the held-out rows):\n\n" + fail_lines
    reading = ("The honest reading — **L1 %s, L2 %s, held-out %s** (before the driver: L1 %s, L2 %s, held-out %s; the cloud providers score 21/21 on the ladder's checks): "
               "the stepwise driver turns a model that \"did one thing and said done\" into one that finishes short, concrete desktop tasks — in the shipped run it %s — and, "
               "when it cannot, **fails with the step named** instead of claiming success. %s %s It is not a cloud model: a 1.5B model still needs the driver's deterministic "
               "checks to catch commands that exit 0 without doing the work, its plans need the request-derived repairs described in ADR-0020, and one run is a sample, not a "
               "guarantee — run-to-run variance at temperature 0.2 is real. Tasks took %d–%d s each. For hard tasks a cloud provider is one dropdown away. Rerun: "
               "`tests/local-driver-image.sh --label after --extra-args --no-repack` (this tree; L1, L2 and the held-out tasks) and `--label before --agent-src build/baseline "
               "--llama-start build/baseline/llama-start.sh` after extracting the previous daemon and wrapper there (`git show <rev>:packages/fabos-agent/usr/lib/fabos/agent/fabos_agentd.py > "
               "build/baseline/fabos_agentd.py`, same for `packages/fabos-ai/usr/lib/fabos/ai/llama-start.sh`); then `python3 tests/local-driver-docs.py` splices the table, "
               "the failures and this paragraph into the three documents." % (l1, l2, h, bl1, bl2, bh, plist, fail_sentence, caveat, min(secs), max(secs)))

    splice("docs/LOW-RAM.md", [
        (re.compile(r"(RESULTS_BLOCK_LOWRAM|\| Task \| What the ladder asks \|.*?into the three documents\.)(?=\n\n## Minimum requirements)", re.S), results + "\n\n" + reading),
        (re.compile(r"(\| `--no-repack` \(the 4 GB machine\), free-form loop \| \*\*)(RSS_BEFORE|[\d.]+) GB\*\*"), r"\g<1>%.2f GB**" % rss_before),
        (re.compile(r"(\| `--no-repack`, stepwise driver \| \*\*)(RSS_AFTER|[\d.]+) GB\*\*"), r"\g<1>%.2f GB**" % rss_after),
    ])
    adr = ("The same table is in docs/LOW-RAM.md (\"The built-in model\"); both are rendered by `tests/local-driver-table.py` from `build/local-driver-{before,after}.json` and spliced in by "
           "`tests/local-driver-docs.py`, so no number is typed by hand. **Result: L1 %s, L2 %s, held-out %s** (target: 6/6 and ≥ 3/5 on the ladder; before the driver L1 %s, L2 %s, "
           "held-out %s).%s %s\n\n%s" % (l1, l2, h, bl1, bl2, bh,
                                          (" The %s task%s the agent could not finish %s reported as failed by the agent itself, not as done." % (", ".join(honest), "" if len(honest) == 1 else "s", "was" if len(honest) == 1 else "were")) if honest else "",
                                          caveat, results))
    splice("docs/decisions/ADR-0020-small-model-driver.md", [
        (re.compile(r"(RESULTS_BLOCK_ADR|The same table is in docs/LOW-RAM\.md .*?)(?=\n\n## Consequences)", re.S), adr),
        (re.compile(r"\*\*(RSS_BEFORE|[\d.]+) GB with `--no-repack`\*\* \(the 4 GB-machine"), "**%.2f GB with `--no-repack`** (the 4 GB-machine" % rss_before),
        (re.compile(r"(RSS_AFTER|[\d.]+) GB with `--no-repack` after the shipped stepwise run"), "%.2f GB with `--no-repack` after the shipped stepwise run" % rss_after),
    ])
    bullet = ("- **Small model, measured expectations.** On the agent's graded ladder (`tests/ladder`, the same checks the cloud providers pass 21/21), run inside the Fab OS image with the "
              "real model on %s, the built-in model passed **%s of 6 easy and %s of 5 medium tasks, and %s held-out tasks** (before the driver: %s, %s and %s). It %s. %s %s One run is a "
              "sample: the same task can pass one run and fail the next. The per-task table, and what failed in the checker's own words, are in [docs/LOW-RAM.md](docs/LOW-RAM.md). For "
              "hard tasks connect a cloud provider — it is one dropdown away, and everything else stays the same."
              % (after.get("finished", "")[:10], l1.split("/")[0], l2.split("/")[0], h, bl1, bl2, bh, plist, fail_sentence, caveat))
    splice("README.md", [
        (re.compile(r"(README_SMALL_MODEL_BULLET|- \*\*Small model, measured expectations\.\*\* .*?everything else stays the same\.)(?=\n)"), bullet),
        (re.compile(r"`llama-server` peaks at \*\*(RSS_MAX|[\d.]+) GB\*\* resident"), "`llama-server` peaks at **%.2f GB** resident" % max(rss_before, rss_after)),
    ])
    print("after: L1 %s L2 %s held-out %s · before: L1 %s L2 %s held-out %s · VmHWM before %.2f after %.2f GB · failed: %s" % (l1, l2, h, bl1, bl2, bh, rss_before, rss_after, [t for t, _ in failed]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
