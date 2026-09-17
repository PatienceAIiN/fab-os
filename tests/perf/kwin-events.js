// Fab OS perf probe (tests/perf-vm.sh): report window events as session-bus method calls.
// print() in KWin scripts is gated by the kwin_scripting log category, so the script calls a method nobody implements on
// the bus driver under our own interface; the call fails (UnknownMethod, ignored) but
// `busctl --user monitor --json=short --match "interface=in.patienceai.fabos.perf"` sees it with a timestamp, and the
// payload carries KWin's own Date.now() (the guest's realtime clock, the same one tests/perf/launch.py reads).
function report(msg) { callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus", "in.patienceai.fabos.perf", "event", msg + " t=" + Date.now()); }
function tag(w) { return "class=" + String(w ? w.resourceClass : "-").replace(/\s+/g, "_") + " caption=" + String(w ? w.caption : "-").replace(/\s+/g, "_"); }
report("loaded windows=" + workspace.windowList().filter(function (w) { return w.normalWindow; }).length);
workspace.windowAdded.connect(function (w) { report("added " + tag(w)); });
workspace.windowActivated.connect(function (w) { report("activated " + tag(w)); });
