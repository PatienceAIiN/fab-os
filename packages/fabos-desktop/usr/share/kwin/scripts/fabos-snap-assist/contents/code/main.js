// Fab OS Snap Assist — KWin script. Copyright 2026 Patience AI. Apache-2.0.
//
// When the user tiles a window into the left or right half of the screen (drag to a side edge, Meta+Left/Right),
// the other normal windows on the same screen and desktop are shown in KWin's Window View so one can be picked;
// the picked window is tiled into the remaining half. Nothing happens when the other half is already filled,
// when there is nothing else to offer, when a window was tiled by this script itself, or within 3 s of the last offer.
//
// KWin 6.6 API used (verified against the shipped kwin_wayland / libkwin, see tests/snap-assist-test.sh):
//   workspace.windowAdded / windowList / windowActivated / activeWindow / currentDesktop / slotWindowQuickTile{Left,Right}
//   window.quickTileModeChanged, window.tile.relativeGeometry, window.internalId, window.output.name, QTimer
//   D-Bus: org.kde.KWin /org/kde/KWin/Effect/WindowView1 org.kde.KWin.Effect.WindowView1.activate(as handles)
//          org.kde.KWin /Effects org.freedesktop.DBus.Properties.Get(org.kde.kwin.Effects, activeEffects)
//          org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.{shortcutNames, invokeShortcut}
//
// Config (kwinrc [Script-fabos-snap-assist]): RequireWindowView=true — a pick counts only while Window View is open;
// set to false on machines whose compositor cannot load the effect so the next activated window still fills the half.
"use strict";

var DEBOUNCE_MS = 3000;   // no second offer within this window
var PICK_MS = 8000;       // an offer waits at most this long for the user's pick
var SETTLE_MS = 250;      // let the tile assignment settle before reading it
var POLL_MS = 400;        // how often the open offer checks whether Window View is still showing
var EPS = 0.02;           // tolerance on relative tile geometry (halves may be resized by the user)
var requireWindowView = String(readConfig("RequireWindowView", true)) !== "false";

var lastOffer = 0;
var programmatic = false; // true while this script tiles a window itself
var pending = null;       // {side, ids, until, screen, seen}: an offer waiting for the user's pick
var stringList = null;    // a QStringList-backed sequence (see offer(); a plain JS array would be sent as "av")

function log(m) { print("fabos-snap-assist: " + m); }

// KWin's callDBus marshals a JS array as "av", which WindowView1.activate(as) rejects. A QStringList that arrives in a
// D-Bus reply is wrapped as a mutable sequence that keeps its type on the way out, so borrow one at start-up and reuse it.
callDBus("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component", "shortcutNames",
         function (names) { if (names && typeof names.length === "number") { stringList = names; } });

function isCandidate(w) {
    return !!w && w.normalWindow && !w.dialog && !w.specialWindow && !w.popupWindow && !w.transient
        && !w.skipTaskbar && !w.minimized && w.resizeable && !w.deleted;
}

function onCurrentDesktop(w) {
    if (w.onAllDesktops) { return true; }
    var cur = workspace.currentDesktop;
    var list = w.desktops || [];
    for (var i = 0; i < list.length; i++) {
        if (list[i] === cur || (cur && list[i] && list[i].id === cur.id)) { return true; }
    }
    return false;
}

function screenOf(w) { return (w.output && w.output.name) || ""; }

// "left" | "right" for a window that occupies a full-height half; null otherwise (quarters, top/bottom, floating).
function halfOf(w) {
    var g = null;
    if (typeof w.tile !== "undefined") {
        if (!w.tile) { return null; }
        g = w.tile.relativeGeometry;
    } else { // older API without Window.tile: compare the frame with the maximize area
        var a = workspace.clientArea(KWin.MaximizeArea, w), f = w.frameGeometry;
        if (!a || !f || a.width <= 0 || a.height <= 0) { return null; }
        g = { x: (f.x - a.x) / a.width, y: (f.y - a.y) / a.height, width: f.width / a.width, height: f.height / a.height };
    }
    if (!g || Math.abs(g.height - 1) > EPS || Math.abs(g.y) > EPS || g.width > 1 - EPS) { return null; }
    if (Math.abs(g.x) < EPS) { return "left"; }
    if (Math.abs(g.x + g.width - 1) < EPS) { return "right"; }
    return null;
}

function others(tiled) {
    var out = [], all = workspace.windowList();
    for (var i = 0; i < all.length; i++) {
        var w = all[i];
        if (w !== tiled && isCandidate(w) && onCurrentDesktop(w) && screenOf(w) === screenOf(tiled)) { out.push(w); }
    }
    return out;
}

function showWindowView(handles) {
    if (stringList) {
        stringList.length = 0;
        for (var i = 0; i < handles.length; i++) { stringList.push(handles[i]); }
        callDBus("org.kde.KWin", "/org/kde/KWin/Effect/WindowView1", "org.kde.KWin.Effect.WindowView1", "activate",
                 stringList, function () { log("window view shown"); });
        return "activate";
    }
    // No QStringList to borrow: show Window View for the whole desktop instead (the tiled window is in it too).
    callDBus("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component", "invokeShortcut", "Expose",
             function () { log("window view shown (all windows)"); });
    return "expose";
}

// While an offer is open, watch Window View: once it has been seen and closes again without a pick, or if it never
// opens at all, the offer is withdrawn so an unrelated later click does not tile anything.
function watch(p) {
    var ticks = 0, t = new QTimer();
    t.interval = POLL_MS;
    t.timeout.connect(function () {
        if (pending !== p || Date.now() > p.until) { t.stop(); if (pending === p) { pending = null; } return; }
        ticks++;
        callDBus("org.kde.KWin", "/Effects", "org.freedesktop.DBus.Properties", "Get", "org.kde.kwin.Effects", "activeEffects",
                 function (list) {
                     if (pending !== p) { return; }
                     var open = ("," + String(list) + ",").indexOf(",windowview,") >= 0;
                     if (open) { p.seen = true; return; }
                     if (p.seen || (requireWindowView && ticks >= 5)) { pending = null; t.stop(); log("offer withdrawn"); }
                 });
    });
    t.start();
}

function offer(tiled, side) {
    var list = others(tiled);
    if (!list.length) { return; }
    var free = side === "left" ? "right" : "left";
    for (var i = 0; i < list.length; i++) {
        if (halfOf(list[i]) === free) { return; } // the pair is already complete
    }
    var now = Date.now();
    if (now - lastOffer < DEBOUNCE_MS) { return; }
    lastOffer = now;
    var ids = {}, handles = [];
    for (var j = 0; j < list.length; j++) {
        var id = String(list[j].internalId);
        ids[id] = true; handles.push(id);
    }
    pending = { side: free, ids: ids, until: now + PICK_MS, screen: screenOf(tiled), seen: false };
    var how = showWindowView(handles);
    log("offering " + handles.length + " window(s) for the " + free + " half via " + how);
    watch(pending);
}

function onTileChanged(w) {
    if (programmatic) { return; }
    pending = null; // any manual re-tiling supersedes an open offer
    var t = new QTimer();
    t.singleShot = true;
    t.interval = SETTLE_MS;
    t.timeout.connect(function () {
        if (!isCandidate(w)) { return; }
        var side = halfOf(w);
        if (side) { offer(w, side); }
    });
    t.start();
}

function hook(w) {
    if (!w || !w.normalWindow) { return; }
    w.quickTileModeChanged.connect(function () { onTileChanged(w); });
}

workspace.windowActivated.connect(function (w) {
    if (!pending || !w) { return; }
    var p = pending;
    pending = null;
    if (Date.now() > p.until || !p.ids[String(w.internalId)] || screenOf(w) !== p.screen) { return; }
    if (halfOf(w) === p.side) { return; }
    programmatic = true;
    try {
        if (p.side === "right") { workspace.slotWindowQuickTileRight(); } else { workspace.slotWindowQuickTileLeft(); }
    } finally {
        programmatic = false;
    }
    log("tiled the picked window to the " + p.side);
});

workspace.windowAdded.connect(hook);
workspace.windowList().forEach(hook);
