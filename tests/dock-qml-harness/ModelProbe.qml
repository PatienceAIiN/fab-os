import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import org.kde.taskmanager as TaskManager
import "../code/dock-logic.js" as Logic

// Probe of the REAL libtaskmanager TasksModel with the Fab OS dock's settings. It is a throwaway applet (model-probe.sh
// assembles in.patienceai.fabos.dockprobe with this file as main.qml and the dock's contents/code/dock-logic.js) run by
// plasmawindowed — inside the VM's Plasma session (tests/dock-qml-harness/vm-dock-activate.py) or as the session of a
// virtual kwin_wayland in the image (model-probe.sh). Every second it prints one "ROWS <json>" line: the rows as
// dock-logic.js reads them (readRow) — launcher / window / group, AppId, LauncherUrlWithoutIcon, active, minimised,
// children — plus "elsewhere" for bare launchers whose windows the filtered model hides, and the indicator state / dot
// count / tap decision the dock would draw and take for each. A command file drives taps through the very same code path
// the dock's TaskItem uses (Logic.tap / Logic.middle):
//   echo "tap 3" > $PROBE_CMD      echo "middle 3" > $PROBE_CMD      echo "quit" > $PROBE_CMD
// Filters come from the environment: PROBE_FILTER_DESKTOP=1 / PROBE_FILTER_ACTIVITY=1 (the dock's showOnlyCurrent*);
// PROBE_FILTER_HIDDEN=1 replays v3's `filterHidden: true`, which drops MINIMISED windows (libtaskmanager's "hidden" =
// minimised) — the reported bug. The dock (v4) ships filterHidden off; so does this probe by default. (Skip-taskbar windows
// are dropped by libtaskmanager's filter proxy on its own; TasksModel exposes no filterSkipTaskbar — assigning one fails to load.)
PlasmoidItem {
    id: win
    preferredRepresentation: fullRepresentation
    Layout.minimumWidth: 320; Layout.minimumHeight: 120
    readonly property var roles: TaskManager.AbstractTasksModel
    readonly property string cmdFile: envOr("PROBE_CMD", "/tmp/dock-probe-cmd")
    readonly property bool filterDesktop: envOr("PROBE_FILTER_DESKTOP", "0") === "1"
    readonly property bool filterActivity: envOr("PROBE_FILTER_ACTIVITY", "0") === "1"
    readonly property bool filterHiddenRows: envOr("PROBE_FILTER_HIDDEN", "0") === "1"
    function envOr(name, dflt) { var v = envProbe.env[name]; return v === undefined || v === "" ? dflt : v }
    // no direct env access from QML: one `env` at start fills envProbe.env
    QtObject { id: envProbe; property var env: ({}) }
    P5Support.DataSource {
        id: envRead; engine: "executable"
        onNewData: (source, data) => { disconnectSource(source); var e = {}; String(data["stdout"] || "").split("\n").forEach(function (l) { var i = l.indexOf("="); if (i > 0) e[l.slice(0, i)] = l.slice(i + 1) }); envProbe.env = e; console.log("READY filterDesktop=" + win.filterDesktop + " filterActivity=" + win.filterActivity + " filterHidden=" + win.filterHiddenRows + " activity=" + activityInfo.currentActivity + " cmd=" + win.cmdFile) }
        Component.onCompleted: connectSource("env")
    }
    TaskManager.VirtualDesktopInfo { id: virtualDesktopInfo }
    TaskManager.ActivityInfo { id: activityInfo }
    readonly property var launchers: ["applications:fabos-overview.desktop", "applications:fabos-command-center.desktop", "applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop", "applications:org.kde.kate.desktop", "applications:firefox.desktop", "applications:systemsettings.desktop", "applications:org.kde.discover.desktop"]
    TaskManager.TasksModel {   // exactly the dock's primary model
        id: tasksModel
        virtualDesktop: virtualDesktopInfo.currentDesktop
        activity: activityInfo.currentActivity
        filterByVirtualDesktop: win.filterDesktop
        filterByActivity: win.filterActivity
        filterByScreen: false
        filterNotMinimized: false
        filterMinimized: false
        filterHidden: win.filterHiddenRows
        sortMode: TaskManager.TasksModel.SortManual
        launchInPlace: true
        separateLaunchers: false
        groupMode: TaskManager.TasksModel.GroupApplications
        groupInline: false
        groupingWindowTasksThreshold: -1
        Component.onCompleted: launcherList = win.launchers
    }
    TaskManager.TasksModel {   // exactly the dock's unfiltered second model (windows on every desktop / activity, no launchers)
        id: allTasks
        filterByVirtualDesktop: false; filterByActivity: false; filterByScreen: false
        filterNotMinimized: false; filterMinimized: false; filterHidden: win.filterHiddenRows
        sortMode: TaskManager.TasksModel.SortManual
        launchInPlace: false; separateLaunchers: true
        groupMode: TaskManager.TasksModel.GroupApplications; groupInline: false; groupingWindowTasksThreshold: -1
    }
    function rows() {
        var out = []
        for (var i = 0; i < tasksModel.count; i++) {
            var r = Logic.readRow(tasksModel, win.roles, i)
            if (Logic.isBareLauncher(r)) r.elsewhere = Logic.findElsewhere(allTasks, win.roles, r.LauncherUrlWithoutIcon, r.AppId)
            r.state = Logic.state(r); r.dots = Logic.dotCount(r); r.tap = Logic.tapAction(r).action
            // diagnostics the dock does not decide on: where libtaskmanager says the window lives
            var idx = tasksModel.makeModelIndex(i)
            r.Activities = tasksModel.data(idx, win.roles.Activities); r.VirtualDesktops = tasksModel.data(idx, win.roles.VirtualDesktops)
            r.IsOnAllVirtualDesktops = tasksModel.data(idx, win.roles.IsOnAllVirtualDesktops) === true
            r.IsHidden = tasksModel.data(idx, win.roles.IsHidden) === true; r.SkipTaskbar = tasksModel.data(idx, win.roles.SkipTaskbar) === true
            out.push(r)
        }
        return out
    }
    function windowCount(m) { var n = 0; for (var i = 0; i < m.count; i++) { var idx = m.makeModelIndex(i); if (m.data(idx, win.roles.IsGroupParent) === true) n += m.rowCount(idx); else if (m.data(idx, win.roles.IsWindow) === true) n++ } return n }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: console.log("ROWS " + JSON.stringify({ t: Date.now(), count: tasksModel.count, windows: windowCount(tasksModel), allWindows: windowCount(allTasks), desktop: virtualDesktopInfo.currentDesktop, activity: activityInfo.currentActivity, rows: win.rows() })) }
    P5Support.DataSource { id: quitter; engine: "executable" }
    P5Support.DataSource {
        id: cmd; engine: "executable"
        onNewData: (source, data) => {
            disconnectSource(source)
            var text = String(data["stdout"] || "").trim()
            if (!text) return
            text.split("\n").forEach(function (line) {
                var p = line.trim().split(/\s+/)
                if (p[0] === "quit") { console.log("QUIT"); quitter.connectSource("pkill -x plasmawindowed"); return }
                var i = parseInt(p[1], 10)
                if (p[0] === "tap") { var a = Logic.tap(tasksModel, win.roles, i, allTasks); console.log("TAP " + i + " -> " + a) }
                else if (p[0] === "middle") { var b = Logic.middle(tasksModel, i); console.log("MIDDLE " + i + " -> " + b) }
                else if (p[0] === "desktop") { virtualDesktopInfo.requestActivate(p[1]); console.log("DESKTOP " + p[1] + " -> ok") }
            })
        }
    }
    Timer { interval: 400; running: true; repeat: true; onTriggered: cmd.connectSource("sh -c 'if [ -f " + win.cmdFile + " ]; then cat " + win.cmdFile + "; rm -f " + win.cmdFile + "; fi'") }
}
