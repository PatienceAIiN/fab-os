#!/usr/bin/env node
// Dry run of the look-and-feel layout script (Plasma JS, not node) against a stub of plasmashell's scripting API:
// `node --check` only parses it; this executes it and records every panel, widget and config write, then asserts the
// Fab OS layout invariants (top bar contents and order, quick settings + dock present, no icontasks, Brave not Firefox,
// the ask-bar strip untouched, clock size = bar size table).
//   node tests/layout-js-dry-run.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const file = path.join(__dirname, "..", "packages/fabos-desktop/usr/share/plasma/look-and-feel/in.patienceai.fabos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js");
const src = fs.readFileSync(file, "utf8");

const log = { panels: [], desktops: [] };
class Widget {
  constructor(type, owner) { this.type = type; this.owner = owner; this.currentConfigGroup = []; this.config = {}; }
  writeConfig(k, v) { this.config[this.currentConfigGroup.join("/") + ":" + k] = v; }
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
  currentActivity: () => "activity-1",
  screenGeometry: () => ({ x: 0, y: 0, width: 1920, height: 1080 }),
  print: () => {},
};
vm.runInNewContext(src, api, { filename: "org.kde.plasma.desktop-layout.js" });

const [top, dock] = log.panels;
assert.strictEqual(log.panels.length, 2, "two panels");
assert.strictEqual(top.location, "top"); assert.strictEqual(dock.location, "bottom");
const topTypes = top.widgets.map(w => w.type);
assert.deepStrictEqual(topTypes, ["org.kde.plasma.appmenu", "org.kde.plasma.panelspacer", "org.kde.plasma.digitalclock", "org.kde.plasma.panelspacer", "org.kde.plasma.systemtray", "in.patienceai.fabos.quicksettings"], "top bar order: menu · clock centred · tray · quick settings at the corner");
const dockTypes = dock.widgets.map(w => w.type);
assert.deepStrictEqual(dockTypes, ["org.kde.plasma.kickoff", "in.patienceai.fabos.dock", "org.kde.plasma.showdesktop"], "dock: start button · Fab OS dock · peek desktop");
assert.ok(!topTypes.concat(dockTypes).includes("org.kde.plasma.icontasks"), "icontasks replaced");
const clock = top.widgets[2], quick = top.widgets[5], tasks = dock.widgets[1];
const BAR = quick.config["General:barSize"];
assert.strictEqual(BAR, "medium");
assert.strictEqual(clock.config["Appearance:fontSize"], { small: 12, medium: 13, large: 15 }[BAR], "clock px follows the bar size table");
assert.strictEqual(clock.config["Appearance:fontFamily"], "Inter"); assert.strictEqual(clock.config["Appearance:dateDisplayFormat"], "BesideTime");
assert.strictEqual(quick.config["General:magnify"], true); assert.strictEqual(quick.config["General:magnification"], undefined, "the bar has no magnification entry: the strength is the dock's own");
assert.strictEqual(tasks.config["General:magnify"], quick.config["General:magnify"], "dock and bar share the magnify default");
assert.strictEqual(tasks.config["General:magnification"], "normal");
const launchers = tasks.config["General:launchers"];
assert.strictEqual(launchers.length, 8);
assert.ok(launchers.includes("applications:brave-browser.desktop") && !launchers.join().includes("firefox"), "Brave replaces Firefox");
assert.strictEqual(launchers[0], "applications:fabos-overview.desktop"); assert.strictEqual(launchers[1], "applications:fabos-command-center.desktop");
assert.ok(!src.includes("firefox"), "no firefox anywhere in the layout");
assert.strictEqual(top.height, 36, "top bar 2 gridUnits"); assert.strictEqual(dock.height, Math.round(18 * 4.0), "dock 4 gridUnits: 40 px resting icons, 64 px when hovered at 1.6x");
assert.strictEqual(dock.floating, true); assert.strictEqual(dock.hiding, "dodgewindows");
const d = log.desktops[0];
assert.strictEqual(d.widgets.length, 1); assert.strictEqual(d.widgets[0].type, "in.patienceai.fabos.askbar");
assert.deepStrictEqual(d.widgets[0].geometry, [0, Math.round(1080 * 0.30), 1920, 150], "ask-bar strip untouched");
assert.ok(src.includes('d.addWidget("in.patienceai.fabos.askbar", 0, Math.round(sh * 0.30), sw, 150)'), "ask-bar addWidget line byte-identical");
console.log("layout-js-dry-run: OK — " + log.panels.length + " panels, " + (top.widgets.length + dock.widgets.length) + " panel widgets, " + Object.keys(clock.config).length + " clock keys, " + launchers.length + " launchers");
