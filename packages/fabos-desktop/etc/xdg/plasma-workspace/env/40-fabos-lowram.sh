#!/bin/sh
# Fab OS: per-machine desktop tune, run once per user before KWin starts (Plasma sources every env script here first).
# On machines with < 3.5 GB RAM it turns the blur effect off in the user's kwinrc; see /usr/lib/fabos/lowram-tune.sh and
# docs/LOW-RAM.md. This file is sourced, so it must never exit and must stay quick: the work is one short subprocess.
# The script is run through `sh` on purpose: it must not depend on an exec bit (packages/build-debs.sh only restores the
# bit for *.sh / *.py under /usr/lib/fabos, which is why the file carries the .sh suffix), and `-r` is the only guard.
[ -r /usr/lib/fabos/lowram-tune.sh ] && sh /usr/lib/fabos/lowram-tune.sh >/dev/null 2>&1 || true
