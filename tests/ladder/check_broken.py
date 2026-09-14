#!/usr/bin/env python3
# Fab OS — ladder fixture: objective checker for broken_script.py. Exits 0 only when both bugs are fixed.
# Copyright (c) 2026 Patience AI. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Usage: python3 check_broken.py [PATH_TO_broken_script.py]

Default target is the broken_script.py sitting next to this file, so the copy the agent is given in
/tmp/ladder works as-is. The harness runs a pristine copy of this checker from /tmp/ladder-check with an
explicit path argument, so editing the checker in /tmp/ladder cannot fake a pass.
"""
import importlib.util
import os
import sys

CASES = [
    ("word_count", ("the quick brown fox",), 4),
    ("word_count", ("one  two\tthree\nfour five ",), 5),
    ("word_count", ("",), 0),
    ("word_count", ("   ",), 0),
    ("average", ([2, 4, 6],), 4.0),
    ("average", ([10],), 10.0),
    ("average", ([1, 2, 3, 4],), 2.5),
]


def main():
    path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "broken_script.py")
    if not os.path.isfile(path):
        print("check_broken: no such file: %s" % path)
        return 2
    spec = importlib.util.spec_from_file_location("broken_script_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                                    # noqa: BLE001 - any import-time error is a failure
        print("check_broken: importing %s failed: %s: %s" % (path, type(e).__name__, e))
        return 2
    bad = 0
    for fn, args, want in CASES:
        f = getattr(mod, fn, None)
        if not callable(f):
            print("check_broken: %s() is missing from %s" % (fn, path))
            bad += 1
            continue
        try:
            got = f(*args)
        except Exception as e:                                # noqa: BLE001
            print("check_broken: %s%r raised %s: %s" % (fn, args, type(e).__name__, e))
            bad += 1
            continue
        if got != want:
            print("check_broken: %s%r -> %r, expected %r" % (fn, args, got, want))
            bad += 1
    if bad:
        print("check_broken: %d/%d cases FAILED for %s" % (bad, len(CASES), path))
        return 1
    print("check_broken: all %d cases pass for %s" % (len(CASES), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
