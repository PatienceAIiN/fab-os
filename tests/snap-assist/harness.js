// Test driver for fabos-snap-assist, loaded into a real kwin_wayland (--virtual) by tests/snap-assist-test.sh.
// Waits for three normal windows, tiles the first to the left (as a user would with Meta+Left), then activates the
// second (what Window View does when the user clicks a thumbnail) and prints what happened. The assertions are in
// the shell wrapper, on these "harness:" lines and the script's own "fabos-snap-assist:" lines.
"use strict";
function rect(g) { return g ? [g.x, g.y, g.width, g.height].map(function (v) { return Math.round(v * 1000) / 1000; }).join(",") : "none"; }
function tileOf(w) { return typeof w.tile === "undefined" ? "no-tile-api" : rect(w.tile ? w.tile.relativeGeometry : null); }
function later(ms, fn) { var t = new QTimer(); t.singleShot = true; t.interval = ms; t.timeout.connect(fn); t.start(); }
function normals() {
    return workspace.windowList().filter(function (w) { return w.normalWindow && !w.dialog && !w.skipTaskbar && !w.minimized; });
}
var tries = 0, poll = new QTimer();
poll.interval = 1000;
poll.timeout.connect(function () {
    tries++;
    var wins = normals();
    if (wins.length < 3 && tries < 60) { if (tries % 5 === 0) { print("harness: waiting, windows=" + wins.length); } return; }
    poll.stop();
    print("harness: windows=" + wins.length + " " + wins.map(function (w) { return w.resourceClass + "=" + String(w.internalId); }).join(" "));
    print("harness: internalId type=" + typeof wins[0].internalId + " string=" + String(wins[0].internalId));
    var w1 = wins[0], w2 = wins[1];
    workspace.activeWindow = w1;
    workspace.slotWindowQuickTileLeft();                       // the user tiles w1 to the left half
    later(2500, function () {
        print("harness: w1 tile=" + tileOf(w1) + " frame=" + rect(w1.frameGeometry));
        callDBus("org.kde.KWin", "/Effects", "org.freedesktop.DBus.Properties", "Get", "org.kde.kwin.Effects", "activeEffects",
                 function (v) { print("harness: activeEffects=" + String(v)); });
        later(1500, function () {
            workspace.activeWindow = w2;                       // the user picks w2 in Window View
            later(2500, function () {
                print("harness: w2 tile=" + tileOf(w2) + " frame=" + rect(w2.frameGeometry));
                // w1 holds the left half and w2 the right: tiling a third window left must NOT offer anything (pair complete)
                var w3 = wins[2];
                workspace.activeWindow = w3;
                workspace.slotWindowQuickTileLeft();
                later(1500, function () { print("harness: DONE"); });
            });
        });
    });
});
poll.start();
print("harness: started");
// Marshalling probe for the premise the script rests on: org.kde.KGlobalAccel.shortcut(as actionId) must reject a plain
// JS array (KWin sends it as "av"; the error lands in the kwin log) and accept a QStringList borrowed from a D-Bus reply.
callDBus("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel", "shortcut", ["kwin", "Expose", "", ""],
         function (r) { print("harness: probe-array reply=" + String(r)); });
callDBus("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component", "shortcutNames", function (names) {
    print("harness: probe-borrowed typeof=" + typeof names + " length=" + names.length);
    names.length = 0; names.push("kwin"); names.push("Expose"); names.push(""); names.push("");
    callDBus("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel", "shortcut", names,
             function (r) { print("harness: probe-list reply=" + JSON.stringify(r)); });
});
