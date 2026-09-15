#!/usr/bin/env node
// Dry run of the look-and-feel layout script (Plasma JS, not node) against a stub of plasmashell's 6.6 scripting API:
// `node --check` only parses it; this executes it and records every panel, widget and config write, then asserts the
// Fab OS layout invariants (top bar contents and order, Fab OS clock instead of the stock one, the system tray's
// extraItems/knownItems/hiddenItems, quick settings + dock present, no icontasks / showdesktop, kickoff zero-width,
// Firefox pinned, the ask-bar strip untouched, clock size = bar size).
// The stub exposes exactly the globals the 6.6 engine registers (shell/scripting/scriptengine.cpp): there is NO
// `containments()` — a script using it throws, which is how the old tray-defaults.js failed silently at login.
//   node tests/layout-js-dry-run.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const file = path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js");
const src = fs.readFileSync(file, "utf8");
// arrays born in the vm context have another Array prototype: compare structurally through JSON
const _dse = assert.deepStrictEqual; assert.deepStrictEqual = (a, b, m) => _dse(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)), m);

// what the 6.6 tray's PlasmoidRegistry writes at init, before the layout script can touch the widget
const TRAY_AT_INIT = ["org.kde.kupapplet", "org.kde.plasma.vault", "org.kde.kscreen", "org.kde.plasma.battery", "org.kde.plasma.bluetooth", "org.kde.plasma.brightness",
  "org.kde.plasma.cameraindicator", "org.kde.plasma.clipboard", "org.kde.plasma.devicenotifier", "org.kde.plasma.keyboardlayout", "org.kde.plasma.manage-inputmethod",
  "org.kde.plasma.mediacontroller", "org.kde.plasma.networkmanagement", "org.kde.plasma.notifications", "org.kde.plasma.volume", "org.kde.plasma.printmanager",
  "org.kde.plasma.keyboardindicator", "org.kde.plasma.weather"];

const log = { panels: [], desktops: [] };
class Widget {
  constructor(type, owner) {
    this.type = type; this.owner = owner; this.currentConfigGroup = []; this.config = {};
    if (type === "org.kde.plasma.systemtray") { this.config["General:extraItems"] = TRAY_AT_INIT.slice(); this.config["General:knownItems"] = TRAY_AT_INIT.slice(); }
  }
  key(k) { return this.currentConfigGroup.join("/") + ":" + k; }
  writeConfig(k, v) { this.config[this.key(k)] = v; }
  readConfig(k, def) { return this.key(k) in this.config ? this.config[this.key(k)] : def; }
}
class Panel {
  constructor() { this.widgets = []; log.panels.push(this); }
  addWidget(type) { const w = new Widget(type, this); this.widgets.push(w); return w; }
}
class Desktop {
  constructor() { this.screen = 0; this.config = {}; this.widgets = []; this.currentConfigGroup = []; log.desktops.push(this); }
  writeConfig(k, v) { this.config[this.currentConfigGroup.join("/") + ":" + k] = v; }
  addWidget(type, x, y, w, h) { const wd = new Widget(type, this); wd.geometry = [x, y, w, h]; this.widgets.push(wd); return wd; }
}
const api = {
  Panel, gridUnit: 18,
  desktopsForActivity: () => [new Desktop()],
  desktops: () => log.desktops, panels: () => log.panels,
  currentActivity: () => "activity-1",
  screenGeometry: () => ({ x: 0, y: 0, width: 1920, height: 1080 }),
  print: () => {},
};
vm.runInNewContext(src, api, { filename: "org.kde.plasma.desktop-layout.js" });
assert.ok(!src.includes("containments("), "no containments() call: the 6.6 scripting API has no such global");

const [top, dock] = log.panels;
assert.strictEqual(log.panels.length, 2, "two panels");
assert.strictEqual(top.location, "top"); assert.strictEqual(dock.location, "bottom");
const topTypes = top.widgets.map(w => w.type);
assert.deepStrictEqual(topTypes, ["org.kde.plasma.appmenu", "org.kde.plasma.panelspacer", "in.patienceai.fabos.clock", "org.kde.plasma.panelspacer", "org.kde.plasma.systemtray", "in.patienceai.fabos.quicksettings"], "top bar order: menu · Fab OS clock centred · tray · quick settings at the corner");
const dockTypes = dock.widgets.map(w => w.type);
assert.deepStrictEqual(dockTypes, ["org.kde.plasma.kickoff", "in.patienceai.fabos.dock"], "dock: zero-width launcher menu · Fab OS dock (start + apps + peek drawn by the dock itself)");
assert.ok(!topTypes.concat(dockTypes).includes("org.kde.plasma.icontasks"), "icontasks replaced");
assert.ok(!topTypes.concat(dockTypes).includes("org.kde.plasma.digitalclock"), "the stock clock is gone (one size for everything in the bar)");
assert.ok(!topTypes.concat(dockTypes).includes("org.kde.plasma.showdesktop"), "the 32 px stock peek button is gone: the dock draws peek at the icons' size");
const clock = top.widgets[2], tray = top.widgets[4], quick = top.widgets[5], kickoff = dock.widgets[0], tasks = dock.widgets[1];
const BAR = quick.config["General:barSize"];
assert.strictEqual(BAR, "medium");
assert.strictEqual(clock.config["General:barSize"], BAR, "the clock gets the same bar size");
assert.strictEqual(clock.config["General:dateFormat"], "ddd d MMM"); assert.strictEqual(clock.config["General:showDate"], true);
assert.strictEqual(quick.config["General:magnify"], true); assert.strictEqual(quick.config["General:magnification"], undefined, "the bar has no magnification entry: the strength is the dock's own");
assert.strictEqual(quick.config["General:showSpeed"], true, "speed always on");
// tray: the five replaced applets are known but not loaded; notifications loaded but hidden; nothing else lost
const extra = tray.config["General:extraItems"], known = tray.config["General:knownItems"], hidden = tray.config["General:hiddenItems"];
for (const id of ["org.kde.plasma.networkmanagement", "org.kde.plasma.bluetooth", "org.kde.plasma.volume", "org.kde.plasma.battery", "org.kde.plasma.brightness"]) {
  assert.ok(!extra.includes(id), id + " not in extraItems"); assert.ok(known.includes(id), id + " stays in knownItems (else the registry re-adds it)");
}
assert.ok(extra.includes("org.kde.plasma.notifications") && hidden.includes("org.kde.plasma.notifications"), "notifications: loaded (popups) but hidden");
assert.deepStrictEqual(extra, TRAY_AT_INIT.filter(id => !["org.kde.plasma.networkmanagement", "org.kde.plasma.bluetooth", "org.kde.plasma.volume", "org.kde.plasma.battery", "org.kde.plasma.brightness"].includes(id)), "every other default tray applet kept, in order");
assert.deepStrictEqual(known, TRAY_AT_INIT, "knownItems = everything the registry knew");
assert.deepStrictEqual(tray.config["General:shownItems"], []);
// the same function must also work when the registry has written nothing yet (older shell / a template run)
{
  const w = new Widget("org.kde.plasma.systemtray", null); w.config = {};
  const ctx = vm.createContext({ ...api }); vm.runInContext(src.split("\nvar top = new Panel")[0], ctx); ctx.configureTray(w);
  assert.ok(w.config["General:extraItems"].length >= 12 && !w.config["General:extraItems"].includes("org.kde.plasma.volume") && w.config["General:knownItems"].includes("org.kde.plasma.volume"), "fallback list applied when extraItems is empty");
}
// dock
// An empty icon is NOT zero-width in Plasma 6.6's panel (findPositive() substitutes the panel thickness for a 0 minimum
// width — the blank square before the Fab OS button); the 1 x 64 transparent anchor image makes kickoff 1 px wide.
assert.strictEqual(kickoff.config["General:icon"], "/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/images/launcher-anchor.png", "kickoff's icon is the dock's 1 x 64 transparent anchor -> a 1 px compact representation");
assert.ok(fs.existsSync(path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/plasmoids/in.patienceai.fabos.dock/contents/images/launcher-anchor.png")), "the anchor image ships with the dock plasmoid");
assert.strictEqual(kickoff.config["General:menuLabel"], "", "…and no label");
assert.strictEqual(tasks.config["General:magnify"], quick.config["General:magnify"], "dock and bar share the magnify default");
assert.strictEqual(tasks.config["General:magnification"], "normal");
assert.strictEqual(tasks.config["General:showStart"], true); assert.strictEqual(tasks.config["General:showPeek"], true);
const launchers = tasks.config["General:launchers"];
assert.strictEqual(launchers.length, 8);
assert.ok(launchers.includes("applications:firefox.desktop") && !launchers.join().includes("brave"), "Firefox is the pinned browser again (Brave gone)");
assert.strictEqual(launchers[0], "applications:fabos-overview.desktop"); assert.strictEqual(launchers[1], "applications:fabos-command-center.desktop");
assert.ok(!src.toLowerCase().includes("brave"), "no brave anywhere in the layout");
assert.strictEqual(top.height, 36, "top bar 2 gridUnits"); assert.strictEqual(dock.height, Math.round(18 * 4.0), "dock 4 gridUnits: 40 px resting icons, 64 px when hovered at 1.6x");
assert.strictEqual(dock.floating, true); assert.strictEqual(dock.hiding, "dodgewindows");
const d = log.desktops[0];
assert.strictEqual(d.widgets.length, 1); assert.strictEqual(d.widgets[0].type, "in.patienceai.fabos.askbar");
assert.deepStrictEqual(d.widgets[0].geometry, [0, Math.round(1080 * 0.24), 1920, Math.round(1080 * 0.66)], "ask-bar strip untouched (round 4: 24% down, 66% tall)");
assert.ok(src.includes('d.addWidget("in.patienceai.fabos.askbar", 0, Math.round(sh * 0.24), sw, Math.round(sh * 0.66))'), "ask-bar addWidget line byte-identical");
console.log("layout-js-dry-run: OK — " + log.panels.length + " panels, " + (top.widgets.length + dock.widgets.length) + " panel widgets, tray extraItems " + extra.length + "/" + known.length + " known, " + launchers.length + " launchers");
