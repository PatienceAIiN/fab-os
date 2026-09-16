.pragma library
// Fab OS dock: the decisions, as plain JavaScript (no QML types), so tests/dock-js-test.js can run every state
// transition under node. The QML (main.qml / TaskItem.qml) only reads the model and executes what these return.
//
// Vocabulary. A "row" is a plain object with the TasksModel roles the dock needs:
//   IsLauncher IsWindow IsStartup IsGroupParent IsActive IsMinimized IsDemandingAttention ChildCount
//   children:  [{ IsActive, IsMinimized, LastActivated }]   (a group parent's windows, in model order)
//   elsewhere: null | { row, windows, active, minimized, child }   the same app in the UNFILTERED model when this row is
//              a bare launcher: its windows exist but the dock's model hides them (another virtual desktop or activity
//              with "only the current desktop / activity" on, or a window libtaskmanager did not merge with the pin).
//              `row` / `child` locate the window to bring back, `child` -1 for a lone window.
//
// Tap semantics (Windows 11 / macOS): a launcher with no window anywhere launches; a running app that is not active
// comes forward (a group: its most recently used window); the active window minimises; a minimised window comes back;
// a group with an active window cycles to its next window; an app still starting ignores the tap (never two instances
// from a double click); windows elsewhere are activated there (KWin switches desktop / activity) — never a fresh
// instance. Middle click is always a new instance. The indicator ("running") is on for IsWindow || IsGroupParent ||
// IsStartup and for a launcher whose windows are elsewhere, so it never drops while any window of the app exists.

function b(v) { return v === true }

// ---------------------------------------------------------------- running state
function isRunning(row) {
    if (!row) return false
    if (b(row.IsWindow) || b(row.IsGroupParent) || b(row.IsStartup)) return true
    return !!(row.elsewhere && row.elsewhere.windows > 0)
}

// "none" | "starting" | "active" | "minimized" | "running"  — the indicator kind for an item
function state(row) {
    if (!isRunning(row)) return "none"
    if (b(row.IsWindow) || b(row.IsGroupParent)) {
        if (b(row.IsActive) && !b(row.IsMinimized)) return "active"
        return b(row.IsMinimized) ? "minimized" : "running"
    }
    if (b(row.IsStartup)) return "starting"
    var e = row.elsewhere   // a bare launcher whose windows are on another desktop / activity: never "active" here
    return e.minimized ? "minimized" : "running"
}

// how many windows this item stands for: a group's children, one window, the windows elsewhere, 0 while starting / none
function windowCount(row) {
    if (!row) return 0
    if (b(row.IsGroupParent)) return Math.max(1, row.ChildCount || (row.children ? row.children.length : 0) || 0)
    if (b(row.IsWindow)) return 1
    if (b(row.IsStartup)) return 0
    return row.elsewhere && row.elsewhere.windows > 0 ? row.elsewhere.windows : 0
}

// how many dots under the icon: 0 (nothing runs), 1, or 2 for an app with several windows
function dotCount(row) {
    if (!isRunning(row)) return 0
    return windowCount(row) > 1 ? 2 : 1
}

// ---------------------------------------------------------------- group helpers
function activeChild(children) {
    if (!children) return -1
    for (var i = 0; i < children.length; i++) if (b(children[i].IsActive) && !b(children[i].IsMinimized)) return i
    return -1
}
// the most recently used window of a group: highest LastActivated (a QDateTime / ms number / ISO string all compare
// through Date); without timestamps the first window that is not minimised, else the first
function mostRecentChild(children) {
    if (!children || children.length === 0) return -1
    var best = -1, bestT = -Infinity
    for (var i = 0; i < children.length; i++) {
        var t = toMillis(children[i].LastActivated)
        if (t > bestT) { bestT = t; best = i }
    }
    if (best >= 0 && bestT > -Infinity) return best
    for (var k = 0; k < children.length; k++) if (!b(children[k].IsMinimized)) return k
    return 0
}
function toMillis(v) {
    if (v === undefined || v === null || v === "") return -Infinity
    if (typeof v === "number") return isNaN(v) ? -Infinity : v
    if (v instanceof Date) return isNaN(v.getTime()) ? -Infinity : v.getTime()
    if (typeof v === "object" && typeof v.getTime === "function") { var g = v.getTime(); return isNaN(g) ? -Infinity : g }
    var d = new Date(v); var t = d.getTime()
    return isNaN(t) ? -Infinity : t
}

// ---------------------------------------------------------------- the tap
// Returns { action, child } with action one of
//   "launch"    a pinned launcher with no window anywhere         -> requestActivate(row)   (launches, with startup feedback)
//   "elsewhere" a launcher whose windows the model hides          -> unfiltered.requestActivate(elsewhere.row[, child])
//   "none"      the app is still starting                         -> nothing (a second tap must not start a second copy)
//   "activate"  a running window that is not active / minimised   -> requestActivate(row)
//   "minimize"  the active window                                 -> requestToggleMinimized(row)
//   "recent"    a group with no active window                     -> requestActivate(row, child)   (most recently used)
//   "cycle"     a group with an active window                     -> requestActivate(row, child)   (the next one)
function tapAction(row) {
    if (!row) return { action: "none", child: -1 }
    if (b(row.IsGroupParent)) {
        var ch = row.children || [], n = ch.length
        if (n === 0) return { action: "activate", child: -1 }
        var a = activeChild(ch)
        if (a >= 0) return n > 1 ? { action: "cycle", child: (a + 1) % n } : { action: "minimize", child: a }
        return { action: "recent", child: mostRecentChild(ch) }
    }
    if (b(row.IsWindow)) {
        if (b(row.IsActive) && !b(row.IsMinimized)) return { action: "minimize", child: -1 }
        return { action: "activate", child: -1 }
    }
    if (b(row.IsStartup)) return { action: "none", child: -1 }
    var e = row.elsewhere
    if (e && e.windows > 0) return { action: "elsewhere", child: typeof e.child === "number" ? e.child : -1, elsewhereRow: e.row }
    return { action: "launch", child: -1 }
}
function middleAction(row) { return { action: "new", child: -1 } }
// the actions after which the icon bounces (something new is starting)
function bounces(action) { return action === "launch" || action === "new" }

// ---------------------------------------------------------------- launcher <-> window identity
// A pinned launcher is "applications:org.kde.dolphin.desktop"; a window's LauncherUrlWithoutIcon is the desktop file
// libtaskmanager resolved for it (the same string when it matched; a file:// path for a non-menu entry) and its AppId
// the desktop-file id (with or without ".desktop"). Two rows are the same app when either agrees, ignoring case, a
// query string and the ".desktop" suffix; AppId of one against the URL base of the other counts too.
function desktopBase(url) {
    var u = String(url === undefined || url === null ? "" : url)
    if (!u) return ""
    var q = u.indexOf("?"); if (q >= 0) u = u.slice(0, q)
    u = u.slice(u.lastIndexOf("/") + 1)
    u = u.slice(u.lastIndexOf(":") + 1)
    if (u.slice(-8).toLowerCase() === ".desktop") u = u.slice(0, -8)
    return u.toLowerCase()
}
function sameApp(urlA, appIdA, urlB, appIdB) {
    var ba = desktopBase(urlA), bb = desktopBase(urlB)
    if (ba && bb && ba === bb) return true
    var aa = desktopBase(appIdA), ab = desktopBase(appIdB)
    if (aa && ab && aa === ab) return true
    return !!(aa && bb && aa === bb) || !!(ba && ab && ba === ab)
}

// ---------------------------------------------------------------- reading a TasksModel
// `m` is a TasksModel (or any object with makeModelIndex / data / rowCount), `A` the AbstractTasksModel role enum.
function readChildren(m, A, i, idx) {
    var out = [], n = m.rowCount(idx)
    for (var c = 0; c < n; c++) {
        var ci = m.makeModelIndex(i, c)
        out.push({ IsActive: b(m.data(ci, A.IsActive)), IsMinimized: b(m.data(ci, A.IsMinimized)), LastActivated: m.data(ci, A.LastActivated) })
    }
    return out
}
function readRow(m, A, i) {
    var idx = m.makeModelIndex(i)
    var row = {
        display: String(m.data(idx, 0) || ""),
        AppId: String(m.data(idx, A.AppId) || ""),
        LauncherUrlWithoutIcon: String(m.data(idx, A.LauncherUrlWithoutIcon) || ""),
        IsLauncher: b(m.data(idx, A.IsLauncher)), IsWindow: b(m.data(idx, A.IsWindow)), IsStartup: b(m.data(idx, A.IsStartup)),
        HasLauncher: b(m.data(idx, A.HasLauncher)), IsGroupParent: b(m.data(idx, A.IsGroupParent)),
        IsActive: b(m.data(idx, A.IsActive)), IsMinimized: b(m.data(idx, A.IsMinimized)), IsDemandingAttention: b(m.data(idx, A.IsDemandingAttention)),
        ChildCount: Number(m.data(idx, A.ChildCount)) || 0, children: [], elsewhere: null
    }
    if (row.IsGroupParent) {
        row.children = readChildren(m, A, i, idx)
        if (row.ChildCount === 0) row.ChildCount = row.children.length
    }
    return row
}
function isBareLauncher(row) { return !!row && b(row.IsLauncher) && !b(row.IsWindow) && !b(row.IsGroupParent) && !b(row.IsStartup) }
// The same app in the unfiltered model `all` (null when there is none or no window of the app exists anywhere):
// { row, windows, active, minimized, child } — `child` the window to bring back (most recent of a group, else -1).
function findElsewhere(all, A, url, appId) {
    if (!all) return null
    var n = all.count !== undefined ? all.count : all.rowCount()
    for (var r = 0; r < n; r++) {
        var idx = all.makeModelIndex(r)
        if (!b(all.data(idx, A.IsWindow)) && !b(all.data(idx, A.IsGroupParent))) continue
        if (!sameApp(url, appId, all.data(idx, A.LauncherUrlWithoutIcon), all.data(idx, A.AppId))) continue
        var row = readRow(all, A, r)
        var windows = row.IsGroupParent ? Math.max(1, row.ChildCount) : 1
        var child = row.IsGroupParent ? mostRecentChild(row.children) : -1
        return { row: r, windows: windows, active: row.IsActive, minimized: row.IsMinimized, child: child }
    }
    return null
}
// Execute a decision on the models. Returns the action name (the QML bounces the icon on "launch" / "new").
function perform(m, i, decision, all) {
    var idx = m.makeModelIndex(i)
    switch (decision.action) {
    case "launch": case "activate": m.requestActivate(idx); break
    case "minimize": m.requestToggleMinimized(decision.child >= 0 ? m.makeModelIndex(i, decision.child) : idx); break
    case "recent": case "cycle": m.requestActivate(decision.child >= 0 ? m.makeModelIndex(i, decision.child) : idx); break
    case "elsewhere":
        if (all && decision.elsewhereRow >= 0) all.requestActivate(decision.child >= 0 ? all.makeModelIndex(decision.elsewhereRow, decision.child) : all.makeModelIndex(decision.elsewhereRow))
        break
    case "new": m.requestNewInstance(idx); break
    default: break
    }
    return decision.action
}
// One left tap on row i: read, decide, execute.
function tap(m, A, i, all) {
    var row = readRow(m, A, i)
    if (isBareLauncher(row)) row.elsewhere = findElsewhere(all, A, row.LauncherUrlWithoutIcon, row.AppId)
    return perform(m, i, tapAction(row), all)
}
// One middle click on row i: a new instance, always.
function middle(m, i) { return perform(m, i, middleAction(null), null) }

// ---------------------------------------------------------------- tooltip second line
function subText(row, genericName, appName) {
    var n = windowCount(row)
    if (b(row.IsGroupParent)) return n + " windows"
    if (b(row.IsStartup)) return "Starting…"
    if (b(row.IsWindow)) return appName && appName !== row.display ? appName : ""
    if (row.elsewhere && row.elsewhere.windows > 0) return n === 1 ? "Open on another desktop" : n + " windows on other desktops"
    return genericName || "Click to open"
}
