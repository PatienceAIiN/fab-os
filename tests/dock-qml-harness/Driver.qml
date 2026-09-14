import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// Headless driver for the dock. Loaded by one Loader line appended to a COPY of main.qml (tests/desktop-applets-qml-test.sh).
// Sizes the window like the floating dock panel, checks the launcher rows, drives hover through dock.hoveredIndex
// (the scales 1.6 / 1.3 / 1.1 and the re-flow), the launch bounce, the context menu and the magnify switch, renders
// /out/dock-{idle,hover}.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
// Under the virtual kwin_wayland it finishes with a REAL pointer (fakeinput.py, see the quick-settings driver): the
// window goes fullscreen, the pointer moves over icon 3 then 4 (hoveredIndex and the scales follow), leaves, and
// left-clicks the Overview launcher (TapHandler -> activate -> "launch" + bounce).
Item {
    id: h
    property var dock: null
    property var row: null
    property var tasksModel: null
    property var repeater: null
    property bool realBackend: true
    property int failures: 0
    property int grabs: 0
    property var backdrops: ({})
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function near(a, b, eps) { return Math.abs(a - b) <= (eps || 0.02) }
    function rows() { return repeater.count }
    Component { id: backdrop; Rectangle { z: -1; anchors.fill: parent; radius: 20; color: Kirigami.Theme.backgroundColor } }
    function grab(item, file) { h.grabs++; if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item); item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- }) }
    property int idleWidth: 0
    property real idleCentre3: 0

    // ---- real pointer (wayland session only); same protocol as tests/quicksettings-qml-harness/Driver.qml
    readonly property bool wayland: Qt.platform.pluginName === "wayland"
    readonly property string injector: Qt.resolvedUrl("fakeinput.py").toString().replace(/^file:\/\//, "")
    readonly property string python: "PYTHONHOME=/usr /tmp/fabos-pointer"
    property var afterPointer: null
    P5Support.DataSource { id: pointer; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("POINTER exit=" + data["exit code"] + " " + String(data["stdout"] || "").trim() + " " + String(data["stderr"] || "").trim().slice(0, 300) + " t=" + Date.now() % 100000); settle.start() } }
    Connections { target: dock; function onHoveredIndexChanged() { console.log("INFO hoveredIndex -> " + dock.hoveredIndex + " t=" + Date.now() % 100000) } }
    P5Support.DataSource { id: shell; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("SHELL exit=" + data["exit code"] + " " + String(data["stdout"] || "").trim().slice(0, 200)) } }
    Timer { id: settle; interval: 600; onTriggered: { var f = h.afterPointer; h.afterPointer = null; if (f) f() } }
    function movePointer(x, y, then) { h.afterPointer = then; pointer.connectSource(h.python + " " + h.injector + " move " + Math.round(x) + " " + Math.round(y)) }
    function clickPointer(x, y, then) { h.afterPointer = then; pointer.connectSource(h.python + " " + h.injector + " click " + Math.round(x) + " " + Math.round(y)) }
    function screenPos(item, fx, fy) { return item.mapToItem(null, item.width * fx, item.height * fy) }   // fullscreen window at 0,0
    function centreOf(i) { var it = dock.itemAt(i); return it.mapToItem(dock, it.width / 2, 0).x }
    onTasksModelChanged: if (dock && row && tasksModel) startTimer.start()
    Timer { id: startTimer; interval: 900; onTriggered: h.stage1() }
    // Offscreen there is no windowing backend: libtaskmanager's WindowTasksModel has zero columns, so the task filter
    // proxy rejects every row (launchers included). The visual and input checks then run against this stand-in model
    // with the same role names; the real TasksModel path is covered by the kwin_wayland run of the same harness.
    ListModel {
        id: fakeTasks
        ListElement { display: "Overview"; decoration: "fabos-overview"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:fabos-overview.desktop"; AppName: "Overview"; GenericName: "" }
        ListElement { display: "Fab AI Controls"; decoration: "fabos"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:fabos-command-center.desktop"; AppName: "Fab AI Controls"; GenericName: "" }
        ListElement { display: "Documents — Files"; decoration: "system-file-manager"; IsLauncher: false; IsWindow: true; IsActive: true; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: true; IsClosable: true; IsMinimizable: true; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.dolphin.desktop"; AppName: "Files"; GenericName: "File Manager" }
        ListElement { display: "Terminal"; decoration: "utilities-terminal"; IsLauncher: false; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: true; ChildCount: 3; IsStartup: false; IsDemandingAttention: false; HasLauncher: true; IsClosable: true; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.konsole.desktop"; AppName: "Terminal"; GenericName: "" }
        ListElement { display: "Editor"; decoration: "kate"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.kate.desktop"; AppName: "Editor"; GenericName: "" }
        ListElement { display: "Brave"; decoration: "internet-web-browser"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:brave-browser.desktop"; AppName: "Brave"; GenericName: "Web Browser" }
        ListElement { display: "Settings"; decoration: "systemsettings"; IsLauncher: false; IsWindow: true; IsActive: false; IsMinimized: true; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: true; HasLauncher: true; IsClosable: true; IsMinimizable: true; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:systemsettings.desktop"; AppName: "Settings"; GenericName: "" }
        ListElement { display: "Software"; decoration: "plasmadiscover"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.discover.desktop"; AppName: "Software"; GenericName: "" }
    }

    function stage1() {
        dock.autoSync = false   // no plasmashell here: keep the write-back command, do not run it
        var win = dock.Window.window
        if (win) { win.width = 760; win.height = 70 }   // the 4-gridUnit floating dock minus its panel margins
        stage1b.start()
    }
    Timer { id: stage1b; interval: 500; onTriggered: {
        console.log("INFO dock.height=" + dock.height + " avail=" + dock.avail + " baseSize=" + dock.baseSize + " tasksModel.count=" + dock.count + " launcherCount=" + tasksModel.launcherCount + " launcherList=" + tasksModel.launcherList.length + " backend=" + (dock.count > 0 ? "real" : "none (offscreen)"))
        if (dock.count === 0) {
            h.realBackend = false
            console.log("INFO no windowing backend here: the TasksModel chain has no rows; switching the Repeater to the stand-in model for the visual checks")
            repeater.model = fakeTasks
        } else {
            check(tasksModel.launcherCount === 8 && tasksModel.launcherCount === dock.cfg.launchers.length, "all 8 configured launchers are rows of the real TasksModel (" + tasksModel.launcherCount + ")")
            check(dock.count >= tasksModel.launcherCount, "windows of this session appear too (" + (dock.count - tasksModel.launcherCount) + " window row(s): plasmawindowed itself)")
        }
        check(rows() === (h.realBackend ? dock.count : 8), "rows to render: " + rows())
        check(dock.baseSize === Math.min(48, Math.floor(dock.avail / dock.peak)), "resting icon = floor(available / peak) = " + dock.baseSize)
        check(dock.peak === 1.6 && dock.near === 1.3 && dock.far === 1.1, "normal magnification 1.6 / 1.3 / 1.1")
        var it0 = dock.itemAt(0)
        check(it0 && it0.width === dock.baseSize && near(it0.s, 1), "idle items rest at 1.0 (width " + (it0 ? it0.width : -1) + ")")
        check(it0 && it0.mainText.length > 0 && it0.running === false && it0.pinned === true, "row 0 is a pinned launcher named '" + (it0 ? it0.mainText : "") + "'")
        if (!h.realBackend) { var it2 = dock.itemAt(2), it3 = dock.itemAt(3), it6 = dock.itemAt(6); check(it2.running && it3.running && it6.running && it3.childCount === 3, "running dot for a window, a 3-window group and a minimised window") }
        check(it0 && it0.model.IsLauncher === true && it0.model.IsWindow !== true, "IsLauncher role read through the delegate")
        row.forceLayout()   // a swapped-in model is positioned on the next polish; measure the resting geometry a moment later
        stage1c.start()
    } }
    Timer { id: stage1c; interval: 250; onTriggered: {
        var b = dock.baseSize, expReserve = (Math.round(b * 1.6) - b) + 2 * (Math.round(b * 1.3) - b) + 2 * (Math.round(b * 1.1) - b)
        check(dock.restingWidth === rows() * b + (rows() - 1) * dock.gap && row.implicitWidth === dock.restingWidth, "resting row width = count x resting size + gaps (" + dock.restingWidth + " = row " + row.implicitWidth + ")")
        check(dock.reserve === expReserve && dock.appletWidth === dock.restingWidth + dock.reserve && dock.appletWidth > row.implicitWidth, "applet width (Layout hints) = resting row + one magnified group's growth (" + dock.reserve + " px reserve)")
        h.idleWidth = dock.appletWidth
        h.idleCentre3 = centreOf(3)
        check(Math.abs(h.idleCentre3 - (dock.width / 2 - dock.restingWidth / 2 + 3 * (b + dock.gap) + b / 2)) <= 1, "resting centre of icon 3 measured after layout (" + h.idleCentre3.toFixed(1) + ")")
        grab(dock, "/out/dock-idle.png")
        dock.hoveredIndex = 3
        stage2.start()
    } }
    Timer { id: stage2; interval: 450; onTriggered: {
        var s = []; for (var i = 0; i < rows(); i++) s.push(dock.itemAt(i).s.toFixed(2))
        console.log("INFO scales after hover(3): " + s.join(" "))
        check(near(dock.itemAt(3).s, 1.6), "hovered icon 1.6")
        check(near(dock.itemAt(2).s, 1.3) && near(dock.itemAt(4).s, 1.3), "neighbours 1.3")
        check(near(dock.itemAt(1).s, 1.1) && near(dock.itemAt(5).s, 1.1), "second neighbours 1.1")
        check(near(dock.itemAt(0).s, 1.0) && near(dock.itemAt(6).s, 1.0), "others 1.0")
        var overlap = false, widthsOk = true
        for (var j = 0; j < rows(); j++) {
            var a = dock.itemAt(j)
            if (a.width !== Math.round(dock.baseSize * a.s)) widthsOk = false
            if (j + 1 < rows()) { var b = dock.itemAt(j + 1); if (a.x + a.width > b.x + 0.5) overlap = true }
        }
        check(widthsOk, "item width = resting size x scale (row re-flows)")
        check(!overlap, "no overlap between neighbours while magnified")
        check(dock.appletWidth === h.idleWidth, "applet width unchanged while magnified (" + dock.appletWidth + "): the fit panel never resizes on hover")
        check(row.implicitWidth <= dock.appletWidth && row.implicitWidth === dock.restingWidth + dock.reserve, "magnified row fills exactly the reserved width (" + row.implicitWidth + " of " + dock.appletWidth + ")")
        check(Math.abs(centreOf(3) - h.idleCentre3) <= 1, "hovered icon keeps its centre (" + h.idleCentre3.toFixed(1) + " -> " + centreOf(3).toFixed(1) + "): the pointer stays over the same icon")
        var g = dock.itemAt(3).children[0]   // the Kirigami.Icon
        check(dock.itemAt(3).height - dock.dotSpace >= Math.round(dock.baseSize * 1.6) - 1, "magnified icon fits the panel height (" + Math.round(dock.baseSize * 1.6) + " <= " + (dock.itemAt(3).height - dock.dotSpace) + ")")
        grab(dock, "/out/dock-hover.png")
        dock.itemAt(0).launchBounce()
        stage3.start()
    } }
    Timer { id: stage3; interval: 150; onTriggered: {
        check(dock.itemAt(0).bounce > 1.05, "launch bounce grows (" + dock.itemAt(0).bounce.toFixed(2) + ")")
        stage4.start()
    } }
    Timer { id: stage4; interval: 400; onTriggered: {
        check(near(dock.itemAt(0).bounce, 1.0, 0.01), "bounce settles at 1.0")
        dock.hoveredIndex = -1
        dock.itemAt(1).openMenu()
        stage5.start()
    } }
    Timer { id: stage5; interval: 400; onTriggered: {
        check(near(dock.itemAt(3).s, 1.0), "leaving resets scales")
        if (h.realBackend) console.log("INFO context menu under kwin: menuOpen=" + dock.itemAt(1).menuOpen + " (a QMenu popup needs a pointer grab the virtual session has not got; asserted offscreen)")
        else check(dock.itemAt(1).menuOpen === true, "context menu opened (Pin/Unpin, New Window, Close, Configure)")
        dock.itemAt(1).closeMenu()
        dock.cfg.magnify = false
        dock.hoveredIndex = 2
        stage6.start()
    } }
    Timer { id: stage6; interval: 400; onTriggered: {
        check(near(dock.itemAt(2).s, 1.0) && dock.baseSize === Math.min(48, dock.avail), "magnify off: no scaling, icons fill the height (" + dock.baseSize + ")")
        check(dock.reserve === 0 && dock.appletWidth === dock.restingWidth, "magnify off: no reserve, applet width = resting row")
        check(dock.lastSync.indexOf("qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '") === 0 && dock.lastSync.indexOf("in.patienceai.fabos.quicksettings") > 0 && dock.lastSync.indexOf("writeConfig(\"magnify\", false)") > 0 && dock.lastSync.indexOf("magnification") < 0, "switch changed on the dock -> write-back of magnify=false to the quick settings queued")
        dock.cfg.magnify = true
        dock.cfg.magnification = "strong"
        stage7.start()
    } }
    Timer { id: stage7; interval: 400; onTriggered: {
        check(near(dock.itemAt(2).s, 1.9) && dock.baseSize === Math.min(48, Math.floor(dock.avail / 1.9)), "strong: hovered 1.9, resting " + dock.baseSize)
        check(dock.lastSync.indexOf("writeConfig(\"magnify\", true)") > 0, "switch back on -> write-back of magnify=true queued (magnification stays local: " + (dock.lastSync.indexOf("magnification") < 0) + ")")
        dock.cfg.magnification = "normal"
        dock.hoveredIndex = -1
        if (h.wayland && h.realBackend) stageD0.start(); else done.start()
    } }

    // ---- real pointer stages (kwin session with the real TasksModel)
    Timer { id: stageD0; interval: 300; onTriggered: {
        shell.connectSource("PYTHONHOME=/usr nohup /tmp/fabos-pointer " + h.injector + " hold 120 >/tmp/xdg/hold.log 2>&1 &")   // one device for the whole phase
        dock.Window.window.visibility = Window.FullScreen   // scene coordinates = screen coordinates
        // Keep the dock's real proportions inside the fullscreen window: the row in a 70 px strip along the top edge, items
        // 70 px tall (a 400 px tall item would put its tooltip window under the pointer and steal the pointer focus).
        row.anchors.centerIn = undefined; row.anchors.top = dock.top; row.anchors.horizontalCenter = dock.horizontalCenter; row.height = 70
        for (var i = 0; i < rows(); i++) dock.itemAt(i).height = 70
        stageD1.start()
    } }
    Timer { id: stageD1; interval: 1200; onTriggered: {
        console.log("INFO pointer test: dock " + dock.width + "x" + dock.height + " baseSize " + dock.baseSize + " rows " + rows() + " visibility " + dock.Window.window.visibility)
        movePointer(dock.width / 2, dock.height - 40, function() {   // warm-up: enter the window somewhere neutral (below the strip)
        var p3 = screenPos(dock.itemAt(3), 0.5, 0.7)
        movePointer(p3.x, p3.y, function() {
            check(dock.hoveredIndex === 3 && near(dock.itemAt(3).s, 1.6) && near(dock.itemAt(2).s, 1.3) && near(dock.itemAt(4).s, 1.3), "real pointer from the empty dock area onto icon 3: hoveredIndex 3, scales 1.6 / 1.3 (" + dock.hoveredIndex + ", " + dock.itemAt(3).s.toFixed(2) + ")")
            var p4 = screenPos(dock.itemAt(4), 0.5, 0.7)
            movePointer(p4.x, p4.y, function() {
                check(dock.hoveredIndex === 4 && near(dock.itemAt(4).s, 1.6) && near(dock.itemAt(3).s, 1.3), "pointer moved to icon 4 (re-flowed row): hoveredIndex 4 (" + dock.hoveredIndex + ")")
                movePointer(dock.width / 2, dock.height - 40, function() {
                    check(dock.hoveredIndex === -1 && near(dock.itemAt(4).s, 1.0), "pointer left the dock: scales back to 1.0")
                    var p0 = screenPos(dock.itemAt(0), 0.5, 0.7)
                    clickPointer(p0.x, p0.y, function() {
                        check(dock.itemAt(0).lastAction === "launch", "real left click on the Overview launcher: TapHandler -> activate -> \"" + dock.itemAt(0).lastAction + "\"")
                        done.start()
                    })
                })
            })
        }) })
    } }
    Timer { id: done; interval: 600; repeat: true; onTriggered: {
        if (h.grabs > 0) return
        console.log("HARNESS DONE failures=" + h.failures)
        stop(); killer.connectSource("pkill -x plasmawindowed")
    } }
    P5Support.DataSource { id: killer; engine: "executable" }
}
