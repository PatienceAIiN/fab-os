#!/usr/bin/env python3
"""Idle sampler for tests/perf-vm.sh — runs INSIDE the guest, standard library only.

    python3 sample.py [DURATION_S]      (default 60)

One JSON object on stdout with, over the sample window:
  system_cpu_busy_pct_all_cores / _one_core   from /proc/stat (busy = everything but idle + iowait)
  tasks_per_s                                 /proc/stat `processes` (forks AND threads created, system-wide)
  ctxt_switches_per_s, interrupts_per_s, timer_interrupts_per_s (LOC)   — the wake-up proxies for power: a laptop
                                              that is woken 2000 times a second cannot sit in a deep C-state
  meminfo_kb                                  MemTotal / MemAvailable / MemFree / SwapTotal / SwapFree / Cached at the end
  procs                                       every process alive at the end: cpu_pct (of ONE core: utime+stime delta),
                                              wakeups_per_s (voluntary + nonvoluntary context switches summed over the
                                              process's threads), rss_kb / pss_kb from /proc/PID/smaps_rollup, `new`
                                              when it did not exist at the start
  top15_cpu, top15_pss                        the two rankings the report tables are built from
The sampler's own PID is excluded. Percentages are of one core, so 2 vCPUs can sum to 200.
"""
import json
import os
import sys
import time

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
CLK = os.sysconf("SC_CLK_TCK")
ME = os.getpid()


def read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return b""


def stat_all():
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        st = read("/proc/%d/stat" % pid)
        if not st:
            continue
        i, j = st.find(b"("), st.rfind(b")")
        comm = st[i + 1:j].decode(errors="replace")
        f = st[j + 2:].split()
        try:
            ppid = int(f[1])
            ticks = int(f[11]) + int(f[12])          # utime + stime of the whole process (all threads)
        except (IndexError, ValueError):
            continue
        sw = 0                                       # context switches of every thread = how often it was woken
        try:
            for t in os.listdir("/proc/%d/task" % pid):
                for line in read("/proc/%d/task/%s/status" % (pid, t)).splitlines():
                    if line.startswith(b"voluntary_ctxt_switches") or line.startswith(b"nonvoluntary_ctxt_switches"):
                        sw += int(line.split()[1])
        except OSError:
            pass
        out[pid] = (comm, ppid, ticks, sw)
    return out


def cpu_total():
    v = [int(x) for x in read("/proc/stat").splitlines()[0].split()[1:]]
    return sum(v), v[3] + v[4]                        # total, idle + iowait


def stat_field(name):
    for line in read("/proc/stat").splitlines():
        if line.startswith(name.encode()):
            return int(line.split()[1])
    return 0


def irqs():
    tot = loc = 0
    for line in read("/proc/interrupts").decode(errors="replace").splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        nums = []
        for p in parts[1:]:
            if p.isdigit():
                nums.append(int(p))
            else:
                break
        s = sum(nums)
        tot += s
        if parts[0].rstrip(":") == "LOC":
            loc = s
    return tot, loc


def meminfo():
    m = {}
    for line in read("/proc/meminfo").decode(errors="replace").splitlines():
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0])
    return {k: m.get(k, 0) for k in ("MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree", "Cached")}


def smaps(pid):
    rss = pss = 0
    for line in read("/proc/%d/smaps_rollup" % pid).decode(errors="replace").splitlines():
        if line.startswith("Rss:"):
            rss = int(line.split()[1])
        elif line.startswith("Pss:"):
            pss = int(line.split()[1])
    return rss, pss


def cmdline(pid):
    return read("/proc/%d/cmdline" % pid).replace(b"\0", b" ").decode(errors="replace").strip()[:120]


def pids():
    return {int(d) for d in os.listdir("/proc") if d.isdigit()}


def comm_of(pid):
    st = read("/proc/%d/stat" % pid)
    i, j = st.find(b"("), st.rfind(b")")
    if i < 0 or j < 0:
        return "?", 0
    try:
        return st[i + 1:j].decode(errors="replace"), int(st[j + 2:].split()[1])
    except (IndexError, ValueError):
        return st[i + 1:j].decode(errors="replace"), 0


t0 = time.monotonic(); p0 = stat_all(); c0 = cpu_total(); f0 = stat_field("processes"); x0 = stat_field("ctxt"); i0 = irqs()
# who forks while we wait: every 100 ms the new PIDs are attributed "comm (parent comm)". Processes only (threads are not
# in /proc's top level), and a process that lives < 100 ms can be missed — a lower bound that names the spawners.
known = pids(); spawns = {}
end = t0 + DUR
while time.monotonic() < end:
    time.sleep(0.1)
    cur = pids()
    for pid in cur - known:
        c, pp = comm_of(pid)
        pc = comm_of(pp)[0] if pp else "?"
        key = "%s (%s)" % (c, pc)
        spawns[key] = spawns.get(key, 0) + 1
    known = cur
t1 = time.monotonic(); p1 = stat_all(); c1 = cpu_total(); f1 = stat_field("processes"); x1 = stat_field("ctxt"); i1 = irqs()
el = t1 - t0
procs = []
for pid, (comm, ppid, ticks, sw) in p1.items():
    if pid == ME:
        continue
    ot = p0.get(pid)
    d = ticks - (ot[2] if ot else 0)
    dsw = sw - (ot[3] if ot else 0)
    rss, pss = smaps(pid)
    procs.append({"pid": pid, "ppid": ppid, "comm": comm, "cmd": cmdline(pid), "cpu_pct": round(d / CLK / el * 100, 2),
                  "wakeups_per_s": round(dsw / el, 1), "rss_kb": rss, "pss_kb": pss, "new": ot is None})
procs.sort(key=lambda p: (-p["cpu_pct"], -p["pss_kb"]))
tot = c1[0] - c0[0]
busy = tot - (c1[1] - c0[1])
ncpu = os.cpu_count() or 1
out = {"elapsed_s": round(el, 1), "ncpu": ncpu, "clk_tck": CLK,
       "system_cpu_busy_pct_all_cores": round(busy / tot * 100, 2) if tot else None,
       "system_cpu_busy_pct_one_core": round(busy / tot * 100 * ncpu, 2) if tot else None,
       "tasks_per_s": round((f1 - f0) / el, 2), "ctxt_switches_per_s": round((x1 - x0) / el, 1),
       "interrupts_per_s": round((i1[0] - i0[0]) / el, 1), "timer_interrupts_per_s": round((i1[1] - i0[1]) / el, 1),
       "loadavg": read("/proc/loadavg").decode().split()[:3], "meminfo_kb": meminfo(),
       "spawns_seen": dict(sorted(spawns.items(), key=lambda kv: -kv[1])), "spawns_seen_total": sum(spawns.values()),
       "top15_cpu": procs[:15], "top15_pss": sorted(procs, key=lambda p: -p["pss_kb"])[:15], "procs": procs}
print(json.dumps(out))
