#!/usr/bin/env node
// Unit tests for the dock's decisions (packages/fabos-desktop/.../in.patienceai.fabos.dock/contents/code/dock-logic.js):
// running state / indicator kind / dot count for every row kind, the tap decision for every state transition
// (launch, activate, minimise, restore, group recent / cycle, starting = ignored, windows elsewhere = bring back),
// middle click, launcher <-> window identity (firefox.desktop vs AppId firefox, org.kde.* ids, query strings, file
// URLs), and the model-facing helpers (readRow / findElsewhere / perform / tap) against a fake TasksModel that records
// every request — so "a tap never opens a second instance of an app that has a window anywhere" is asserted on the
// exact calls the QML makes.
//   node tests/dock-js-test.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/code/dock-logic.js"), "utf8");
const L = {};
vm.runInNewContext(src.replace(/^\.pragma library\s*$/m, ""), L);   // .pragma is a QML directive, not JS
let n = 0;
function t(name, fn) { try { fn(); n++; } catch (e) { console.error("FAIL " + name); throw e; } }
const eq = (a, b, m) => assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)), m);

// ---------------------------------------------------------------- rows
const launcher = { IsLauncher: true, IsWindow: false, IsStartup: false, IsGroupParent: false, IsActive: false, IsMinimized: false, ChildCount: 0, elsewhere: null };
const win = (o) => Object.assign({ IsLauncher: false, IsWindow: true, IsStartup: false, IsGroupParent: false, IsActive: false, IsMinimized: false, ChildCount: 0, elsewhere: null }, o || {});
const group = (children, o) => Object.assign({ IsLauncher: false, IsWindow: false, IsStartup: false, IsGroupParent: true, IsActive: children.some(c => c.IsActive), IsMinimized: children.every(c => c.IsMinimized), ChildCount: children.length, children: children, elsewhere: null }, o || {});
const startup = { IsLauncher: false, IsWindow: false, IsStartup: true, IsGroupParent: false, IsActive: false, IsMinimized: false, ChildCount: 0, elsewhere: null };
const away = (o) => Object.assign({}, launcher, { elsewhere: Object.assign({ row: 3, windows: 1, active: false, minimized: false, child: -1 }, o || {}) });

t("running state: launcher off; window / group / startup on; a launcher whose windows are elsewhere on", () => {
  assert.strictEqual(L.isRunning(launcher), false);
  assert.strictEqual(L.isRunning(win()), true);
  assert.strictEqual(L.isRunning(win({ IsMinimized: true })), true, "a minimised window still runs");
  assert.strictEqual(L.isRunning(group([{}, {}])), true);
  assert.strictEqual(L.isRunning(startup), true, "starting counts as running (marker from the first click)");
  assert.strictEqual(L.isRunning(away()), true, "windows on another desktop / activity keep the marker");
  assert.strictEqual(L.isRunning(Object.assign({}, launcher, { elsewhere: { windows: 0 } })), false);
  assert.strictEqual(L.isRunning(null), false);
  assert.strictEqual(L.isRunning({}), false, "a row without roles (stand-in model) is not running");
});
t("indicator kind: none / running / active / minimized / starting; elsewhere is never active here", () => {
  assert.strictEqual(L.state(launcher), "none");
  assert.strictEqual(L.state(win()), "running");
  assert.strictEqual(L.state(win({ IsActive: true })), "active");
  assert.strictEqual(L.state(win({ IsMinimized: true })), "minimized");
  assert.strictEqual(L.state(win({ IsActive: true, IsMinimized: true })), "minimized", "active + minimised reads as minimised (never the bar for a hidden window)");
  assert.strictEqual(L.state(group([{ IsActive: true }, {}])), "active");
  assert.strictEqual(L.state(group([{}, {}])), "running");
  assert.strictEqual(L.state(group([{ IsMinimized: true }, { IsMinimized: true }])), "minimized");
  assert.strictEqual(L.state(startup), "starting");
  assert.strictEqual(L.state(away()), "running");
  assert.strictEqual(L.state(away({ active: true })), "running", "active on another desktop is not the active window of this one");
  assert.strictEqual(L.state(away({ minimized: true })), "minimized");
});
t("dots: 0 when nothing runs, 1 for one window / a starting app, 2 for several windows (group or elsewhere)", () => {
  eq([L.dotCount(launcher), L.dotCount(win()), L.dotCount(win({ IsMinimized: true })), L.dotCount(startup)], [0, 1, 1, 1]);
  eq([L.dotCount(group([{}])), L.dotCount(group([{}, {}])), L.dotCount(group([{}, {}, {}]))], [1, 2, 2]);
  eq([L.dotCount(away()), L.dotCount(away({ windows: 3 }))], [1, 2]);
  eq([L.windowCount(launcher), L.windowCount(win()), L.windowCount(group([{}, {}, {}])), L.windowCount(startup), L.windowCount(away({ windows: 2 }))], [0, 1, 3, 0, 2]);
  assert.strictEqual(L.windowCount({ IsGroupParent: true, ChildCount: 0, children: [{}, {}] }), 2, "children fill in a missing ChildCount");
});

// ---------------------------------------------------------------- tap decisions
t("tap: a launcher with no window anywhere launches", () => eq(L.tapAction(launcher), { action: "launch", child: -1 }));
t("tap: an app still starting ignores the tap (no second instance from a double click)", () => eq(L.tapAction(startup), { action: "none", child: -1 }));
t("tap: running & not active -> activate; active -> minimise; minimised -> activate (restore)", () => {
  eq(L.tapAction(win()), { action: "activate", child: -1 });
  eq(L.tapAction(win({ IsActive: true })), { action: "minimize", child: -1 });
  eq(L.tapAction(win({ IsMinimized: true })), { action: "activate", child: -1 });
  eq(L.tapAction(win({ IsActive: true, IsMinimized: true })), { action: "activate", child: -1 }, "a minimised window comes back even if the model still calls it active");
});
t("tap: a group with no active window brings back its most recently used window", () => {
  const d1 = new Date("2026-09-16T04:00:00Z"), d2 = new Date("2026-09-16T05:00:00Z"), d0 = new Date("2026-09-16T03:00:00Z");
  eq(L.tapAction(group([{ LastActivated: d1 }, { LastActivated: d2 }, { LastActivated: d0 }])), { action: "recent", child: 1 });
  eq(L.tapAction(group([{ LastActivated: 1000 }, { LastActivated: 3000 }, { LastActivated: 2000 }])), { action: "recent", child: 1 }, "numbers");
  eq(L.tapAction(group([{ LastActivated: "2026-09-16T04:00:00Z" }, { LastActivated: "2026-09-16T06:00:00Z" }])), { action: "recent", child: 1 }, "ISO strings");
  eq(L.tapAction(group([{ LastActivated: { getTime: () => 5 } }, { LastActivated: { getTime: () => 9 } }])), { action: "recent", child: 1 }, "a QDateTime-like object from another realm");
  eq(L.tapAction(group([{ IsMinimized: true }, {}, {}])), { action: "recent", child: 1 }, "without timestamps: the first window that is not minimised");
  eq(L.tapAction(group([{ IsMinimized: true }, { IsMinimized: true }])), { action: "recent", child: 0 }, "all minimised: the first");
  eq(L.tapAction(group([{ LastActivated: "garbage" }, { LastActivated: null }])), { action: "recent", child: 0 }, "unparseable timestamps fall back");
  eq(L.tapAction(group([{ IsMinimized: true, LastActivated: 9 }, { LastActivated: 1 }])), { action: "recent", child: 0 }, "the most recent wins even when minimised (it comes back)");
});
t("tap: a group with the active window cycles to the next one; a one-window group minimises it; an empty group activates", () => {
  eq(L.tapAction(group([{ IsActive: true }, {}, {}])), { action: "cycle", child: 1 });
  eq(L.tapAction(group([{}, {}, { IsActive: true }])), { action: "cycle", child: 0 }, "wraps around");
  eq(L.tapAction(group([{ IsActive: true }])), { action: "minimize", child: 0 });
  eq(L.tapAction(group([{ IsActive: true, IsMinimized: true }, {}])), { action: "recent", child: 1 }, "an active-but-minimised child does not count as active");
  eq(L.tapAction(group([])), { action: "activate", child: -1 });
  eq(L.tapAction({ IsGroupParent: true }), { action: "activate", child: -1 }, "no children array at all");
});
t("tap: a launcher whose windows the filters hide brings that window back (never launches)", () => {
  eq(L.tapAction(away()), { action: "elsewhere", child: -1, elsewhereRow: 3 });
  eq(L.tapAction(away({ row: 5, windows: 2, child: 1 })), { action: "elsewhere", child: 1, elsewhereRow: 5 }, "a group elsewhere: its most recent window");
  eq(L.tapAction(away({ minimized: true })), { action: "elsewhere", child: -1, elsewhereRow: 3 }, "minimised elsewhere: still bring back");
  eq(L.tapAction(Object.assign({}, launcher, { elsewhere: { windows: 0, row: 3 } })), { action: "launch", child: -1 });
  eq(L.tapAction(null), { action: "none", child: -1 });
});
t("middle click is always a new instance; only launch / new bounce the icon", () => {
  eq(L.middleAction(win({ IsActive: true })), { action: "new", child: -1 });
  eq(L.middleAction(launcher), { action: "new", child: -1 });
  eq(["launch", "new", "activate", "minimize", "recent", "cycle", "elsewhere", "none"].map(L.bounces), [true, true, false, false, false, false, false, false]);
});
t("every running state decides something other than launch / none (the owner's report: a running app must never spawn a fresh instance)", () => {
  const running = [win(), win({ IsActive: true }), win({ IsMinimized: true }), group([{}, {}]), group([{ IsActive: true }, {}]), group([{ IsMinimized: true }, { IsMinimized: true }]), away(), away({ windows: 4, child: 2 })];
  for (const r of running) { assert.ok(L.isRunning(r)); assert.notStrictEqual(L.tapAction(r).action, "launch", JSON.stringify(r)); assert.notStrictEqual(L.tapAction(r).action, "new"); }
  assert.strictEqual(L.tapAction(startup).action, "none");
});

// ---------------------------------------------------------------- identity
t("desktopBase: applications: URLs, file URLs, query strings, case and the .desktop suffix", () => {
  assert.strictEqual(L.desktopBase("applications:firefox.desktop"), "firefox");
  assert.strictEqual(L.desktopBase("applications:org.kde.konsole.desktop"), "org.kde.konsole");
  assert.strictEqual(L.desktopBase("applications:org.kde.dolphin.desktop?iconData=abc"), "org.kde.dolphin");
  assert.strictEqual(L.desktopBase("file:///usr/local/share/applications/org.kde.kate.desktop"), "org.kde.kate");
  assert.strictEqual(L.desktopBase("Firefox.DESKTOP"), "firefox");
  assert.strictEqual(L.desktopBase("firefox"), "firefox");
  assert.strictEqual(L.desktopBase(""), ""); assert.strictEqual(L.desktopBase(undefined), ""); assert.strictEqual(L.desktopBase(null), "");
});
t("sameApp: pinned launcher vs the window libtaskmanager resolved (Firefox, Fab Terminal / Konsole, Fab Files / Dolphin, LibreOffice)", () => {
  assert.ok(L.sameApp("applications:firefox.desktop", "firefox.desktop", "applications:firefox.desktop", "firefox"), "URL agrees");
  assert.ok(L.sameApp("applications:firefox.desktop", "", "", "firefox"), "launcher URL vs window AppId only");
  assert.ok(L.sameApp("", "firefox", "applications:firefox.desktop", ""), "launcher AppId vs window URL only");
  assert.ok(L.sameApp("applications:org.kde.konsole.desktop", "org.kde.konsole.desktop", "file:///usr/local/share/applications/org.kde.konsole.desktop", "org.kde.konsole"), "the rebranded override file (Fab Terminal) is the same id");
  assert.ok(L.sameApp("applications:org.kde.dolphin.desktop", "org.kde.dolphin", "applications:org.kde.dolphin.desktop?activity=1", "org.kde.dolphin"));
  assert.ok(!L.sameApp("applications:libreoffice-writer.desktop", "libreoffice-writer", "applications:libreoffice-startcenter.desktop", "libreoffice-startcenter"), "Writer and the Start Center are different launchers");
  assert.ok(!L.sameApp("applications:firefox.desktop", "firefox", "applications:org.kde.konsole.desktop", "org.kde.konsole"));
  assert.ok(!L.sameApp("", "", "", ""), "nothing to compare is not a match");
  assert.ok(!L.sameApp("applications:firefox.desktop", "", "", ""), "one side empty is not a match");
  assert.ok(L.sameApp("applications:libreoffice-writer.desktop", "libreoffice-writer.desktop", "applications:libreoffice-writer.desktop", "libreoffice-writer"), "LibreOffice Writer: its own desktop file");
});
t("sameApp last resort: a bare id equals the last segment of a reverse-DNS id (firefox ~ org.mozilla.firefox; a window libtaskmanager could not resolve)", () => {
  assert.ok(L.sameApp("applications:firefox.desktop", "firefox.desktop", "", "org.mozilla.firefox"), "firefox.desktop pinned, the window reports org.mozilla.firefox");
  assert.ok(L.sameApp("applications:org.kde.konsole.desktop", "org.kde.konsole.desktop", "file:///usr/bin/konsole", "konsole"), "the round-7 VM row dump: LauncherUrl file:///usr/bin/konsole, AppId konsole, pin org.kde.konsole.desktop");
  assert.ok(L.sameApp("applications:org.kde.dolphin.desktop", "", "file:///usr/bin/dolphin", "dolphin"));
  assert.ok(!L.sameApp("applications:org.kde.kate.desktop", "org.kde.kate.desktop", "file:///usr/bin/konsole", "konsole"), "Fab Editor's pin does not claim an unresolved Konsole window");
  assert.ok(!L.sameApp("applications:org.kde.konsole.desktop", "org.kde.konsole", "", "org.gnome.Console"), "two reverse-DNS ids must be equal, never segment-matched");
  eq([L.idsMatch("firefox", "org.mozilla.firefox"), L.idsMatch("org.mozilla.firefox", "firefox"), L.idsMatch("firefox", "firefox"), L.idsMatch("org.kde.kate", "org.kde.konsole"), L.idsMatch("kate", "konsole"), L.idsMatch("", "x"), L.idsMatch("konsole", "org.kde.konsole.desktop".slice(0, -8))],
     [true, true, true, false, false, false, true]);
});

// ---------------------------------------------------------------- a fake TasksModel (the QML-facing helpers)
const A = { AppId: 1, LauncherUrlWithoutIcon: 2, IsLauncher: 3, IsWindow: 4, IsStartup: 5, HasLauncher: 6, IsGroupParent: 7, IsActive: 8, IsMinimized: 9, IsDemandingAttention: 10, ChildCount: 11, LastActivated: 12 };
const NAME = {}; for (const k in A) NAME[A[k]] = k; NAME[0] = "display";
function fakeModel(rows) {
  const calls = [];
  const m = {
    rows, calls, get count() { return rows.length }, rowCount(idx) { return idx ? ((rows[idx.row].children || []).length) : rows.length },
    makeModelIndex(i, c) { return { row: i, child: (c === undefined ? -1 : c) } },
    data(idx, role) { const r = idx.child >= 0 ? rows[idx.row].children[idx.child] : rows[idx.row]; return r ? r[NAME[role]] : undefined },
    requestActivate(idx) { calls.push(["activate", idx.row, idx.child]) },
    requestToggleMinimized(idx) { calls.push(["toggleMinimized", idx.row, idx.child]) },
    requestNewInstance(idx) { calls.push(["newInstance", idx.row, idx.child]) },
  };
  return m;
}
const R = {
  launcher: (id, o) => Object.assign({ display: id, AppId: id + ".desktop", LauncherUrlWithoutIcon: "applications:" + id + ".desktop", IsLauncher: true, IsWindow: false, IsStartup: false, HasLauncher: false, IsGroupParent: false, IsActive: false, IsMinimized: false, IsDemandingAttention: false, ChildCount: 0 }, o || {}),
  window: (id, o) => Object.assign({ display: id + " window", AppId: id, LauncherUrlWithoutIcon: "applications:" + id + ".desktop", IsLauncher: false, IsWindow: true, IsStartup: false, HasLauncher: true, IsGroupParent: false, IsActive: false, IsMinimized: false, IsDemandingAttention: false, ChildCount: 0 }, o || {}),
  group: (id, children, o) => Object.assign({ display: id, AppId: id, LauncherUrlWithoutIcon: "applications:" + id + ".desktop", IsLauncher: false, IsWindow: false, IsStartup: false, HasLauncher: true, IsGroupParent: true, IsActive: children.some(c => c.IsActive), IsMinimized: children.every(c => c.IsMinimized), IsDemandingAttention: false, ChildCount: children.length, children }, o || {}),
  startup: (id) => ({ display: id, AppId: id, LauncherUrlWithoutIcon: "applications:" + id + ".desktop", IsLauncher: false, IsWindow: false, IsStartup: true, HasLauncher: true, IsGroupParent: false, IsActive: false, IsMinimized: false, IsDemandingAttention: false, ChildCount: 0 }),
};

t("readRow reads the roles and a group's children (LastActivated kept as given)", () => {
  const d = new Date(1758000000000);
  const m = fakeModel([R.launcher("firefox"), R.group("org.kde.konsole", [{ IsActive: true, IsMinimized: false, LastActivated: d }, { IsActive: false, IsMinimized: true, LastActivated: null }])]);
  const r0 = L.readRow(m, A, 0), r1 = L.readRow(m, A, 1);
  assert.strictEqual(r0.IsLauncher, true); assert.strictEqual(r0.IsWindow, false); assert.strictEqual(r0.AppId, "firefox.desktop"); assert.strictEqual(r0.LauncherUrlWithoutIcon, "applications:firefox.desktop"); eq(r0.children, []); assert.strictEqual(r0.elsewhere, null);
  assert.ok(L.isBareLauncher(r0) && !L.isBareLauncher(r1));
  assert.strictEqual(r1.IsGroupParent, true); assert.strictEqual(r1.ChildCount, 2); assert.strictEqual(r1.children.length, 2); assert.strictEqual(r1.children[0].IsActive, true); assert.strictEqual(r1.children[1].IsMinimized, true); assert.strictEqual(r1.children[0].LastActivated.getTime(), d.getTime());
  const m2 = fakeModel([R.group("x", [{}, {}, {}], { ChildCount: 0 })]);
  assert.strictEqual(L.readRow(m2, A, 0).ChildCount, 3, "ChildCount filled from the children when the role is 0");
});
t("findElsewhere: the same app among the unfiltered model's windows / groups, never a launcher row, null when absent", () => {
  const all = fakeModel([R.launcher("firefox"), R.window("org.kde.dolphin", { IsMinimized: true }), R.group("firefox", [{ IsActive: false, IsMinimized: false, LastActivated: 1 }, { IsActive: false, IsMinimized: false, LastActivated: 7 }]), R.window("org.kde.konsole", { IsActive: true })]);
  eq(L.findElsewhere(all, A, "applications:firefox.desktop", "firefox.desktop"), { row: 2, windows: 2, active: false, minimized: false, child: 1 }, "skips the launcher row, finds the group, most recent child");
  eq(L.findElsewhere(all, A, "applications:org.kde.dolphin.desktop", "org.kde.dolphin.desktop"), { row: 1, windows: 1, active: false, minimized: true, child: -1 });
  eq(L.findElsewhere(all, A, "applications:org.kde.konsole.desktop", ""), { row: 3, windows: 1, active: true, minimized: false, child: -1 });
  assert.strictEqual(L.findElsewhere(all, A, "applications:org.kde.kate.desktop", "org.kde.kate.desktop"), null);
  assert.strictEqual(L.findElsewhere(null, A, "applications:firefox.desktop", "firefox"), null, "no unfiltered model (offscreen harness)");
  assert.strictEqual(L.findElsewhere(fakeModel([]), A, "applications:firefox.desktop", "firefox"), null);
  const noCount = fakeModel([R.window("firefox")]); Object.defineProperty(noCount, "count", { value: undefined });
  eq(L.findElsewhere(noCount, A, "applications:firefox.desktop", ""), { row: 0, windows: 1, active: false, minimized: false, child: -1 }, "falls back to rowCount() without a count property");
});
t("perform: each decision becomes exactly the model request the stock task manager would make", () => {
  let m = fakeModel([R.launcher("a"), R.window("b"), R.group("c", [{}, {}])]);
  const all = fakeModel([R.window("z"), R.group("y", [{}, {}, {}])]);
  assert.strictEqual(L.perform(m, 0, { action: "launch", child: -1 }, all), "launch"); eq(m.calls, [["activate", 0, -1]]); m.calls.length = 0;
  L.perform(m, 1, { action: "activate", child: -1 }, all); eq(m.calls, [["activate", 1, -1]]); m.calls.length = 0;
  L.perform(m, 1, { action: "minimize", child: -1 }, all); eq(m.calls, [["toggleMinimized", 1, -1]]); m.calls.length = 0;
  L.perform(m, 2, { action: "minimize", child: 1 }, all); eq(m.calls, [["toggleMinimized", 2, 1]], "a one-window group minimises the child"); m.calls.length = 0;
  L.perform(m, 2, { action: "recent", child: 1 }, all); eq(m.calls, [["activate", 2, 1]]); m.calls.length = 0;
  L.perform(m, 2, { action: "cycle", child: 0 }, all); eq(m.calls, [["activate", 2, 0]]); m.calls.length = 0;
  L.perform(m, 2, { action: "cycle", child: -1 }, all); eq(m.calls, [["activate", 2, -1]], "no child: the parent"); m.calls.length = 0;
  L.perform(m, 1, { action: "new", child: -1 }, all); eq(m.calls, [["newInstance", 1, -1]]); m.calls.length = 0;
  L.perform(m, 0, { action: "elsewhere", child: -1, elsewhereRow: 0 }, all); eq(m.calls, []); eq(all.calls, [["activate", 0, -1]], "elsewhere goes to the unfiltered model"); all.calls.length = 0;
  L.perform(m, 0, { action: "elsewhere", child: 2, elsewhereRow: 1 }, all); eq(all.calls, [["activate", 1, 2]], "a group elsewhere: its child"); all.calls.length = 0;
  L.perform(m, 0, { action: "elsewhere", child: -1, elsewhereRow: 1 }, null); eq(all.calls, []); eq(m.calls, [], "no unfiltered model: nothing (never a launch instead)");
  L.perform(m, 0, { action: "none", child: -1 }, all); eq(m.calls, []); eq(all.calls, []);
});
t("tap end-to-end: Firefox open on another desktop with 'only the current desktop' on -> the window is activated there, nothing launches", () => {
  const dock = fakeModel([R.launcher("fabos-overview"), R.launcher("firefox"), R.window("org.kde.konsole", { IsActive: true })]);
  const all = fakeModel([R.window("firefox", { IsMinimized: false }), R.window("org.kde.konsole", { IsActive: true })]);
  assert.strictEqual(L.tap(dock, A, 1, all), "elsewhere");
  eq(dock.calls, []); eq(all.calls, [["activate", 0, -1]]);
  assert.ok(!dock.calls.concat(all.calls).some(c => c[0] === "newInstance"));
});
t("tap end-to-end: a pin whose window libtaskmanager did not merge (separate window row) -> that window, not a launch", () => {
  const dock = fakeModel([R.launcher("firefox"), R.window("firefox", { LauncherUrlWithoutIcon: "file:///usr/share/applications/firefox.desktop", HasLauncher: false })]);
  const all = fakeModel([R.window("firefox", { LauncherUrlWithoutIcon: "file:///usr/share/applications/firefox.desktop", HasLauncher: false })]);
  assert.strictEqual(L.tap(dock, A, 0, all), "elsewhere"); eq(all.calls, [["activate", 0, -1]]); eq(dock.calls, []);
});
t("tap end-to-end: a pin whose window did not resolve to a desktop file at all (LauncherUrl file:///usr/bin/konsole, AppId konsole — the VM's row dump) -> that window is activated, nothing launches, the marker is on", () => {
  const unresolved = R.window("konsole", { LauncherUrlWithoutIcon: "file:///usr/bin/konsole", AppId: "konsole", HasLauncher: false });
  const dock = fakeModel([R.launcher("org.kde.konsole"), unresolved]), all = fakeModel([Object.assign({}, unresolved)]);
  const r = L.readRow(dock, A, 0); r.elsewhere = L.findElsewhere(all, A, r.LauncherUrlWithoutIcon, r.AppId);
  eq(r.elsewhere, { row: 0, windows: 1, active: false, minimized: false, child: -1 }); assert.strictEqual(L.state(r), "running"); assert.strictEqual(L.dotCount(r), 1);
  assert.strictEqual(L.tap(dock, A, 0, all), "elsewhere"); eq(dock.calls, []); eq(all.calls, [["activate", 0, -1]]);
  assert.strictEqual(L.findElsewhere(all, A, "applications:org.kde.kate.desktop", "org.kde.kate.desktop"), null, "another app's pin does not claim it");
  assert.strictEqual(L.tap(fakeModel([R.launcher("org.kde.kate")]), A, 0, all), "launch", "Fab Editor still launches");
});
t("tap end-to-end: launcher with no window anywhere -> launch (requestActivate on the launcher row); starting -> nothing", () => {
  const dock = fakeModel([R.launcher("org.kde.kate")]), all = fakeModel([R.window("firefox")]);
  assert.strictEqual(L.tap(dock, A, 0, all), "launch"); eq(dock.calls, [["activate", 0, -1]]); eq(all.calls, []);
  const dock2 = fakeModel([R.startup("org.kde.kate")]);
  assert.strictEqual(L.tap(dock2, A, 0, all), "none"); eq(dock2.calls, []);
  assert.strictEqual(L.tap(fakeModel([R.launcher("org.kde.kate")]), A, 0, null), "launch", "no unfiltered model (harness): launchers still launch");
});
t("tap end-to-end: window activate -> minimise -> restore; group recent / cycle; middle = new instance — and the window count never grows", () => {
  const dock = fakeModel([R.window("org.kde.dolphin"), R.window("org.kde.konsole", { IsActive: true }), R.group("firefox", [{ IsActive: false, IsMinimized: false, LastActivated: 5 }, { IsActive: false, IsMinimized: true, LastActivated: 9 }])]);
  assert.strictEqual(L.tap(dock, A, 0, null), "activate"); eq(dock.calls, [["activate", 0, -1]]); dock.calls.length = 0;
  assert.strictEqual(L.tap(dock, A, 1, null), "minimize"); eq(dock.calls, [["toggleMinimized", 1, -1]]); dock.calls.length = 0;
  dock.rows[1].IsMinimized = true; dock.rows[1].IsActive = false;
  assert.strictEqual(L.tap(dock, A, 1, null), "activate"); eq(dock.calls, [["activate", 1, -1]]); dock.calls.length = 0;
  assert.strictEqual(L.tap(dock, A, 2, null), "recent"); eq(dock.calls, [["activate", 2, 1]], "the most recently used Firefox window (child 1) comes back"); dock.calls.length = 0;
  dock.rows[2].children[1].IsActive = true; dock.rows[2].children[1].IsMinimized = false; dock.rows[2].IsActive = true;
  assert.strictEqual(L.tap(dock, A, 2, null), "cycle"); eq(dock.calls, [["activate", 2, 0]]); dock.calls.length = 0;
  assert.strictEqual(L.middle(dock, 2), "new"); eq(dock.calls, [["newInstance", 2, -1]]); dock.calls.length = 0;
});
t("simulation: from every reachable state of one app, a left tap never issues requestNewInstance or activates a launcher row while a window exists", () => {
  const states = [];
  for (const active of [false, true]) for (const min of [false, true]) states.push(fakeModel([R.window("firefox", { IsActive: active && !min, IsMinimized: min })]));
  for (const k of [1, 2, 3]) for (const a of [-1, 0, k - 1]) {
    const ch = []; for (let i = 0; i < k; i++) ch.push({ IsActive: i === a, IsMinimized: false, LastActivated: i });
    states.push(fakeModel([R.group("firefox", ch)]));
  }
  for (const m of states) {
    const all = fakeModel(m.rows.map(r => Object.assign({}, r)));
    L.tap(m, A, 0, all);
    for (const c of m.calls.concat(all.calls)) { assert.notStrictEqual(c[0], "newInstance"); assert.ok(!(c[0] === "activate" && m.rows[c[1]].IsLauncher), "activate on a launcher = launch"); }
    assert.ok(m.calls.length + all.calls.length === 1, "exactly one request per tap: " + JSON.stringify(m.calls));
  }
  // a hidden-elsewhere window: the launcher row is never activated (that would launch); the unfiltered row is
  const dock = fakeModel([R.launcher("firefox")]), all = fakeModel([R.window("firefox")]);
  L.tap(dock, A, 0, all); eq(dock.calls, []); eq(all.calls, [["activate", 0, -1]]);
});

// ---------------------------------------------------------------- tooltip
t("subText: windows count, starting, app name, elsewhere, generic name / click to open", () => {
  assert.strictEqual(L.subText(group([{}, {}, {}]), "", "Firefox"), "3 windows");
  assert.strictEqual(L.subText(startup, "", ""), "Starting…");
  assert.strictEqual(L.subText(win({ display: "Documents — Files" }), "", "Files"), "Files");
  assert.strictEqual(L.subText(win({ display: "Files" }), "", "Files"), "", "app name equal to the title: nothing");
  assert.strictEqual(L.subText(away(), "Web Browser", "Firefox"), "Open on another desktop");
  assert.strictEqual(L.subText(away({ windows: 2 }), "Web Browser", "Firefox"), "2 windows on other desktops");
  assert.strictEqual(L.subText(launcher, "Web Browser", "Firefox"), "Web Browser");
  assert.strictEqual(L.subText(launcher, "", "Firefox"), "Click to open");
});
t("no user-facing vendor words in the logic file", () => {
  assert.ok(!/ChatGPT|OpenAI|\bGPT\b|SnowUI|Sora|DALL|download/i.test(src));
});

console.log("dock-js-test: " + n + " groups OK");
