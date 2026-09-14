#!/bin/sh
# Fab OS: per-machine desktop tune, run once per user before KWin starts (Plasma sources every env script here first).
# On machines with < 3.5 GB RAM it turns the blur effect off in the user's kwinrc; see /usr/lib/fabos/lowram-tune and
# docs/LOW-RAM.md. This file is sourced, so it must never exit and must stay quick: the work is one short subprocess.
[ -x /usr/lib/fabos/lowram-tune ] && /usr/lib/fabos/lowram-tune >/dev/null 2>&1 || true
