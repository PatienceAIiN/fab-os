import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// Headless driver for the dock. Loaded by one Loader line appended to a COPY of main.qml (tests/desktop-applets-qml-test.sh).
// Sizes the window like the floating dock panel, checks the launcher rows, drives hover through dock.hoveredIndex
// (the scales 1.6 / 1.3 / 1.1 and the re-flow), the launch bounce, the context menu and the magnify switch, renders
// /out/dock-{idle,hover}.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
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
    function grab(item, file) { h.grabs++; if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item); item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + r.image.width + "x" + r.image.height); h.grabs-- }) }
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
        dock.cfg.magnify = true
        dock.cfg.magnification = "strong"
        stage7.start()
    } }
    Timer { id: stage7; interval: 400; onTriggered: {
        check(near(dock.itemAt(2).s, 1.9) && dock.baseSize === Math.min(48, Math.floor(dock.avail / 1.9)), "strong: hovered 1.9, resting " + dock.baseSize)
        dock.cfg.magnification = "normal"
        dock.hoveredIndex = -1
        done.start()
    } }
    Timer { id: done; interval: 600; repeat: true; onTriggered: {
        if (h.grabs > 0) return
        console.log("HARNESS DONE failures=" + h.failures)
        stop(); killer.connectSource("pkill -x plasmawindowed")
    } }
    P5Support.DataSource { id: killer; engine: "executable" }
}
