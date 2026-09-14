#!/usr/bin/env node
// Offline unit test of the Fab OS Snap Assist KWin script with a mocked KWin scripting API (workspace, windows, QTimer,
// callDBus, readConfig). Covers the paths the live test (tests/snap-assist-test.sh) cannot reach: debounce, dialog and
// popup exclusion, other-screen exclusion, programmatic re-entry, and withdrawing an offer when Window View closes or
// never opens. Run: node tests/snap-assist-unit.js
"use strict";
const fs = require("fs"), vm = require("vm"), path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "../packages/fabos-desktop/usr/share/kwin/scripts/fabos-snap-assist/contents/code/main.js"), "utf8");
let failures = 0;
function ok(cond, what) { console.log((cond ? "PASS  " : "FAIL  ") + what); if (!cond) { failures++; } }

function Signal() { this.h = []; }
Signal.prototype.connect = function (f) { this.h.push(f); };
Signal.prototype.emit = function () { for (const f of this.h.slice()) { f.apply(null, arguments); } };

const HALF = { left: { x: 0, y: 0, width: 0.5, height: 1 }, right: { x: 0.5, y: 0, width: 0.5, height: 1 }, quarter: { x: 0, y: 0, width: 0.5, height: 0.5 } };

function makeWorld(opts) {
    const o = Object.assign({ requireWindowView: true, activeEffects: [], seed: true }, opts);
    const world = { now: 1000000, timers: [], calls: [], logs: [], activeEffects: o.activeEffects, uid: 0 };
    function QTimer() { this.singleShot = false; this.interval = 0; this.active = false; this.timeout = new Signal(); world.timers.push(this); }
    QTimer.prototype.start = function () { this.active = true; this.due = world.now + this.interval; };
    QTimer.prototype.stop = function () { this.active = false; };
    world.tick = function (ms) { // advance virtual time, firing due timers in order
        const end = world.now + ms;
        for (;;) {
            const t = world.timers.filter(t => t.active && t.due <= end).sort((a, b) => a.due - b.due)[0];
            if (!t) { break; }
            world.now = t.due;
            if (t.singleShot) { t.active = false; } else { t.due = world.now + t.interval; }
            t.timeout.emit();
        }
        world.now = end;
    };
    const desktop = { id: "d1" }, output = { name: "eDP-1" };
    const ws = {
        windows: [], currentDesktop: desktop, activeWindow: null, windowAdded: new Signal(), windowActivated: new Signal(),
        windowList() { return this.windows.slice(); },
        clientArea() { return { x: 0, y: 0, width: 1024, height: 768 }; },
        slotWindowQuickTileRight() { world.calls.push("tileRight:" + this.activeWindow.internalId); world.tile(this.activeWindow, "right"); },
        slotWindowQuickTileLeft() { world.calls.push("tileLeft:" + this.activeWindow.internalId); world.tile(this.activeWindow, "left"); },
    };
    world.win = function (props) {
        const w = Object.assign({ normalWindow: true, dialog: false, specialWindow: false, popupWindow: false, transient: false, skipTaskbar: false,
            minimized: false, resizeable: true, deleted: false, onAllDesktops: false, desktops: [desktop], output: output, tile: null,
            internalId: "{" + (++world.uid) + "}", quickTileModeChanged: new Signal() }, props || {});
        ws.windows.push(w); ws.windowAdded.emit(w); return w;
    };
    world.tile = function (w, side) { w.tile = side ? { relativeGeometry: HALF[side] } : null; w.quickTileModeChanged.emit(); };
    world.activate = function (w) { ws.activeWindow = w; ws.windowActivated.emit(w); };
    world.offers = () => world.calls.filter(c => c.startsWith("activate:") || c.startsWith("invokeShortcut:"));
    function callDBus(service, objPath, iface, method) {
        const rest = Array.prototype.slice.call(arguments, 4);
        const cb = typeof rest[rest.length - 1] === "function" ? rest.pop() : null;
        world.calls.push(method + ":" + JSON.stringify(rest));
        if (method === "shortcutNames" && cb && o.seed) { cb(["Window Close"]); }
        if (method === "Get" && cb) { cb(world.activeEffects.slice()); }
        if (method === "activate" && cb) { cb(); }
    }
    const sandbox = { workspace: ws, callDBus, QTimer, KWin: { MaximizeArea: 0 }, Date: { now: () => world.now },
        print: m => world.logs.push(m), readConfig: (k, d) => (k === "RequireWindowView" ? o.requireWindowView : d) };
    vm.createContext(sandbox);
    vm.runInContext(SRC, sandbox);
    world.ws = ws;
    return world;
}

// 1. Offer, pick, programmatic re-entry, pair complete
{
    const W = makeWorld();
    const w1 = W.win(), w2 = W.win(), w3 = W.win();
    W.tile(w1, "left"); W.tick(300);
    const offers = W.offers();
    ok(offers.length === 1 && offers[0] === 'activate:[["{2}","{3}"]]', "tiling w1 left offers exactly the two other windows through activate(as)");
    ok(W.logs.some(l => /offering 2 window\(s\) for the right half via activate/.test(l)), "offer logged");
    W.activate(w2);
    ok(W.calls.includes("tileRight:{2}"), "activating w2 (the pick) quick-tiles it to the right");
    ok(W.offers().length === 1, "the programmatic tile of w2 does not trigger a second offer");
    W.tick(5000);
    W.tile(w3, "left"); W.tick(300);
    ok(W.offers().length === 1, "tiling w3 left while w2 holds the right half offers nothing (pair complete)");
}
// 2. Debounce: two manual tilings within 3 s produce one offer
{
    const W = makeWorld();
    const w1 = W.win(); W.win(); W.win();
    W.tile(w1, "left"); W.tick(300);
    const w3 = W.ws.windows[2];
    W.tile(w3, "right"); W.tile(w3, null); W.tick(50); W.tile(w3, "left"); W.tick(300);   // w3 re-tiled 0.6 s after the first offer
    ok(W.offers().length === 1, "second tiling within 3 s is debounced");
    W.tick(4000); W.tile(w3, null); W.tick(50); W.tile(w3, "left"); W.tick(300);
    ok(W.offers().length === 2, "after 3 s a new offer is made");
}
// 3. Dialogs, popups, minimized and other-screen windows are neither offered nor trigger offers
{
    const W = makeWorld();
    const w1 = W.win(), d = W.win({ dialog: true }), p = W.win({ popupWindow: true }), m = W.win({ minimized: true }), far = W.win({ output: { name: "HDMI-1" } });
    W.tile(d, "left"); W.tick(300);
    ok(W.offers().length === 0, "a tiled dialog triggers no offer");
    W.tile(w1, "left"); W.tick(300);
    ok(W.offers().length === 0 && !W.logs.some(l => /offering/.test(l)), "only dialogs/popups/minimized/other-screen windows remain: nothing is offered");
    const w2 = W.win();
    W.tick(4000); W.tile(w1, null); W.tick(50); W.tile(w1, "left"); W.tick(300);
    ok(W.offers().length === 1 && W.offers()[0] === 'activate:[["{6}"]]', "with one normal window left, only it is offered (" + [d, p, m, far].length + " others skipped)");
    ok(w2.internalId === "{6}", "sanity: the offered handle is w2");
}
// 4. Quarter tiles and right halves
{
    const W = makeWorld();
    const w1 = W.win(); W.win();
    W.tile(w1, "quarter"); W.tick(300);
    ok(W.offers().length === 0, "a quarter tile is not a half: no offer");
    W.tile(w1, "right"); W.tick(300);
    ok(W.logs.some(l => /for the left half/.test(l)), "tiling right offers the left half");
    W.activate(W.ws.windows[1]);
    ok(W.calls.includes("tileLeft:{2}"), "the pick is tiled to the left");
}
// 5. Withdraw: Window View never opens (RequireWindowView=true) -> a later activation does nothing
{
    const W = makeWorld({ activeEffects: [] });
    const w1 = W.win(), w2 = W.win();
    W.tile(w1, "left"); W.tick(300); W.tick(2500);
    ok(W.logs.some(l => /offer withdrawn/.test(l)), "offer withdrawn when Window View never appears");
    W.activate(w2);
    ok(!W.calls.some(c => c.startsWith("tileRight")), "activating a window after the withdrawal tiles nothing");
}
// 6. Withdraw: Window View opened, then closed without a pick
{
    const W = makeWorld({ activeEffects: ["windowview"] });
    const w1 = W.win(), w2 = W.win();
    W.tile(w1, "left"); W.tick(300); W.tick(900);
    ok(!W.logs.some(l => /offer withdrawn/.test(l)), "offer stays open while Window View is showing");
    W.activeEffects.length = 0; W.tick(900);
    ok(W.logs.some(l => /offer withdrawn/.test(l)), "offer withdrawn once Window View closes");
    W.activate(w2);
    ok(!W.calls.some(c => c.startsWith("tileRight")), "no tiling after the user dismissed Window View");
}
// 7. RequireWindowView=false keeps the pick open without the effect; expires after 8 s
{
    const W = makeWorld({ requireWindowView: "false", activeEffects: [] });
    const w1 = W.win(), w2 = W.win();
    W.tile(w1, "left"); W.tick(300); W.tick(3000);
    ok(!W.logs.some(l => /offer withdrawn/.test(l)), "RequireWindowView=false: offer not withdrawn without the effect");
    W.tick(6000); W.activate(w2);
    ok(!W.calls.some(c => c.startsWith("tileRight")), "an offer expires after 8 s");
}
// 8. No QStringList seed available -> falls back to the Expose shortcut
{
    const W = makeWorld({ seed: false });
    const w1 = W.win(); W.win();
    W.tile(w1, "left"); W.tick(300);
    ok(W.offers()[0] === 'invokeShortcut:["Expose"]' && W.logs.some(l => /via expose/.test(l)), "without a QStringList seed the Expose shortcut is used");
}
console.log(failures ? failures + " failure(s)" : "all snap-assist unit checks passed");
process.exit(failures ? 1 : 0);
