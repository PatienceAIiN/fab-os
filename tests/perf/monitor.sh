#!/bin/sh
# Session-bus monitor for tests/perf-vm.sh — runs INSIDE the guest, detached (nohup ... &), its pid kept in
# /tmp/perf/monitor.pid so it is stopped by pid, never by a command-line pattern (a `pkill -f` pattern that names
# busctl also matches the shell that carries it). Records every method call the KWin probe (tests/perf/kwin-events.js)
# makes under our interface, one JSON object per line with the bus timestamp; tests/perf/launch.py reads the file.
exec busctl --user monitor --json=short --match "interface='in.patienceai.fabos.perf'" > /tmp/fabos-perf-monitor.jsonl 2> /tmp/fabos-perf-monitor.err
