import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// Headless driver for the dock. Loaded by one Loader line appended to a COPY of main.qml (tests/desktop-applets-qml-test.sh).
// Sizes the window like the floating dock panel, checks the unified row (start button · tasks · peek at ONE size),
// drives hover through dock.hoveredIndex (the scales 1.6 / 1.3 / 1.1 and the re-flow), the launch bounce, the context
// menu and the magnify switch, renders /out/dock-{idle,hover}.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
// Offscreen it then MEASURES: every item's icon box is grabbed to /out/measure/raw-<slot>-<name>.png at tileScale 1.0
// and to norm-<slot>-<name>.png at the shipped tileScale, plus a set of Breeze-only app icons at the same box size as
// the reference (breeze-<name>.png); tests/dock-qml-harness/measure.py reads the alpha bounding boxes and computes the
// tile factor and the spread (dock-uniform.png is the normalised idle dock).
// Under the virtual kwin_wayland it finishes with a REAL pointer (fakeinput.py, see the quick-settings driver): the
// window goes fullscreen, the pointer moves over slot 4 then 5 (hoveredIndex and the scales follow), leaves, and
// left-clicks the Overview launcher (TapHandler -> activate -> "launch" + bounce).
Item {
    id: h
    property var dock: null
    property var row: null
    property var tasksModel: null
    property var repeater: null
    property var startItem: null
    property var peekItem: null
    property bool realBackend: true
    property int failures: 0
    property int grabs: 0
    property var backdrops: ({})
    property var missingLaunchers: []
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function near(a, b, eps) { return Math.abs(a - b) <= (eps || 0.02) }
    function rows() { return repeater.count }
    function task(i) { return dock.itemAt(dock.startCount + i) }   // task i of the model -> its item in the unified row
    Component { id: backdrop; Rectangle { z: -1; anchors.fill: parent; radius: 20; color: Kirigami.Theme.backgroundColor } }
    function grab(item, file) { h.grabs++; if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item); item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- }) }
    function grabRaw(item, file) { h.grabs++; item.grabToImage(function(r) { r.saveToFile(file); console.log("MEASURE " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- }) }
    property int idleWidth: 0
    property real idleCentre: 0
    property real shippedTileScale: 0

    // ---- real pointer (wayland session only); same protocol as tests/quicksettings-qml-harness/Driver.qml
    readonly property bool wayland: Qt.platform.pluginName === "wayland"
    readonly property string injector: Qt.resolvedUrl("fakeinput.py").toString().replace(/^file:\/\//, "")
    readonly property string python: "PYTHONHOME=/usr /tmp/fabos-pointer"
    property var afterPointer: null
    P5Support.DataSource { id: pointer; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("POINTER exit=" + data["exit code"] + " " + String(data["stdout"] || "").trim() + " " + String(data["stderr"] || "").trim().slice(0, 300) + " t=" + Date.now() % 100000); settle.start() } }
    Connections { target: dock; function onHoveredIndexChanged() { console.log("INFO hoveredIndex -> " + dock.hoveredIndex + " t=" + Date.now() % 100000) } }
    P5Support.DataSource { id: shell; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("SHELL exit=" + data["exit code"] + " " + String(data["stdout"] || "").trim().slice(0, 200)) } }
    P5Support.DataSource { id: missing; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); h.missingLaunchers = String(data["stdout"] || "").trim().split("\n").filter(function (l) { return l.length > 0 }); console.log("INFO launcher desktop files missing in this image: " + (h.missingLaunchers.length ? h.missingLaunchers.join(" ") : "none")) } }
    Timer { id: settle; interval: 600; onTriggered: { var f = h.afterPointer; h.afterPointer = null; if (f) f() } }
    function movePointer(x, y, then) { h.afterPointer = then; pointer.connectSource(h.python + " " + h.injector + " move " + Math.round(x) + " " + Math.round(y)) }
    function clickPointer(x, y, then) { h.afterPointer = then; pointer.connectSource(h.python + " " + h.injector + " click " + Math.round(x) + " " + Math.round(y)) }
    function screenPos(item, fx, fy) { return item.mapToItem(null, item.width * fx, item.height * fy) }   // fullscreen window at 0,0
    function centreOf(i) { var it = dock.itemAt(i); return it.mapToItem(dock, it.width / 2, 0).x }
    onPeekItemChanged: if (dock && row && tasksModel && repeater && startItem && peekItem) startTimer.start()
    Timer { id: startTimer; interval: 900; onTriggered: h.stage1() }
    // Offscreen there is no windowing backend: libtaskmanager's WindowTasksModel has zero columns, so the task filter
    // proxy rejects every row (launchers included). The visual and input checks then run against this stand-in model
    // with the same role names; the real TasksModel path is covered by the kwin_wayland run of the same harness.
    // Eight default launchers (Fab OS tiles, Firefox among them) plus one window of an app with a Breeze icon only.
    ListModel {
        id: fakeTasks
        ListElement { display: "Overview"; decoration: "fabos-overview"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:fabos-overview.desktop"; AppName: "Overview"; GenericName: "" }
        ListElement { display: "Fab AI Controls"; decoration: "fabos-command-center"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:fabos-command-center.desktop"; AppName: "Fab AI Controls"; GenericName: "" }
        ListElement { display: "Documents — Files"; decoration: "org.kde.dolphin"; IsLauncher: false; IsWindow: true; IsActive: true; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: true; IsClosable: true; IsMinimizable: true; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.dolphin.desktop"; AppName: "Files"; GenericName: "File Manager" }
        ListElement { display: "Terminal"; decoration: "utilities-terminal"; IsLauncher: false; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: true; ChildCount: 3; IsStartup: false; IsDemandingAttention: false; HasLauncher: true; IsClosable: true; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.konsole.desktop"; AppName: "Terminal"; GenericName: "" }
        ListElement { display: "Editor"; decoration: "kate"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.kate.desktop"; AppName: "Editor"; GenericName: "" }
        ListElement { display: "Firefox"; decoration: "firefox"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:firefox.desktop"; AppName: "Firefox"; GenericName: "Web Browser" }
        ListElement { display: "Settings"; decoration: "preferences-system"; IsLauncher: false; IsWindow: true; IsActive: false; IsMinimized: true; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: true; HasLauncher: true; IsClosable: true; IsMinimizable: true; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:systemsettings.desktop"; AppName: "Settings"; GenericName: "" }
        ListElement { display: "Software"; decoration: "plasmadiscover"; IsLauncher: true; IsWindow: false; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: false; IsMinimizable: false; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.discover.desktop"; AppName: "Software"; GenericName: "" }
        ListElement { display: "KDevelop"; decoration: "kdevelop"; IsLauncher: false; IsWindow: true; IsActive: false; IsMinimized: false; IsGroupParent: false; ChildCount: 0; IsStartup: false; IsDemandingAttention: false; HasLauncher: false; IsClosable: true; IsMinimizable: true; CanLaunchNewInstance: true; LauncherUrlWithoutIcon: "applications:org.kde.kdevelop.desktop"; AppName: "KDevelop"; GenericName: "" }
    }
    // Breeze-only app icons (no FabOS tile of that name), requested the way the dock requests every non-tile icon (at
    // dock.stdIconSize, then scaled to the box): their glyph ratio is the visible extent every dock item is normalised to
    readonly property var breezeNames: ["kdevelop", "akregator", "konqueror", "kdenlive", "okteta", "kolourpaint", "ktorrent", "kmymoney", "kompare"]
    Row {
        id: refRow
        visible: false
        x: 0; y: 300   // outside the 70 px window; grabToImage renders the item itself, not the viewport
        spacing: 4
        Repeater {
            id: refRep
            model: h.breezeNames
            delegate: Kirigami.Icon { required property string modelData; width: h.dock ? h.dock.stdIconSize : 64; height: width; source: modelData }
        }
    }
    function itemName(i) {
        if (dock.startCount && i === 0) return "start-here"
        var it = dock.itemAt(i)
        if (it === peekItem) return "user-desktop"
        var d = it.model ? it.model.decoration : ""
        return (typeof d === "string" && d.length ? d : "window").replace(/[^A-Za-z0-9.-]/g, "_")
    }

    function stage1() {
        dock.autoSync = false   // no plasmashell here: keep the write-back command, do not run it
        var win = dock.Window.window
        if (win) { win.width = 760; win.height = 70 }   // the 4-gridUnit floating dock minus its panel margins
        var files = dock.cfg.launchers.map(function (u) { return String(u).replace(/^applications:/, "") })
        missing.connectSource("for f in " + files.join(" ") + "; do test -f /usr/share/applications/$f || echo $f; done")
        stage1b.start()
    }
    Timer { id: stage1b; interval: 500; onTriggered: {
        console.log("INFO dock.height=" + dock.height + " avail=" + dock.avail + " baseSize=" + dock.baseSize + " tasks=" + repeater.count + " row count=" + dock.count + " launcherCount=" + tasksModel.launcherCount + " launcherList=" + tasksModel.launcherList.length + " backend=" + (repeater.count > 0 ? "real" : "none (offscreen)"))
        if (repeater.count === 0) {
            h.realBackend = false
            console.log("INFO no windowing backend here: the TasksModel chain has no rows; switching the Repeater to the stand-in model for the visual checks")
            repeater.model = fakeTasks
        } else {
            // libtaskmanager keeps a launcher row even when its desktop file is absent from the image (generic icon), so the count is the full list
            check(tasksModel.launcherCount === dock.cfg.launchers.length, "all " + dock.cfg.launchers.length + " configured launchers are rows of the real TasksModel (" + tasksModel.launcherCount + "; desktop files absent from this image: " + (h.missingLaunchers.length ? h.missingLaunchers.join(" ") : "none") + ")")
            check(repeater.count >= tasksModel.launcherCount, "windows of this session appear too (" + (repeater.count - tasksModel.launcherCount) + " window row(s): plasmawindowed itself)")
        }
        check(rows() === (h.realBackend ? repeater.count : 9), "task rows to render: " + rows())
        check(dock.count === rows() + 2 && dock.startCount === 1 && dock.peekCount === 1, "unified row = start + " + rows() + " tasks + peek = " + dock.count)
        check(dock.baseSize === Math.min(48, Math.floor(dock.avail / dock.peak)), "resting icon = floor(available / peak) = " + dock.baseSize)
        check(dock.peak === 1.6 && dock.near === 1.3 && dock.far === 1.1, "normal magnification 1.6 / 1.3 / 1.1")
        check(startItem.visible && startItem.slot === 0 && dock.itemAt(0) === startItem && startItem.width === dock.baseSize && startItem.icon === "start-here" && near(startItem.s, 1), "start button is slot 0 at the icons' resting size (" + startItem.width + " px, launcher tile)")
        check(peekItem.visible && peekItem.slot === dock.count - 1 && dock.itemAt(dock.count - 1) === peekItem && peekItem.width === dock.baseSize && peekItem.tileBackground === true, "peek is the last slot at the same size, drawn as a neutral tile")
        var t0 = task(0)
        check(t0 && t0.width === dock.baseSize && near(t0.s, 1), "idle items rest at 1.0 (width " + (t0 ? t0.width : -1) + ")")
        check(t0 && t0.mainText.length > 0 && t0.running === false && t0.pinned === true, "first task is a pinned launcher named '" + (t0 ? t0.mainText : "") + "'")
        if (!h.realBackend) { check(task(2).running && task(3).running && task(6).running && task(3).childCount === 3 && task(8).running, "running dot for a window, a 3-window group, a minimised window and the Breeze-icon window") }
        check(t0 && t0.model.IsLauncher === true && t0.model.IsWindow !== true, "IsLauncher role read through the delegate")
        check(dock.tileMapReady && dock.isTileLauncher("applications:org.kde.dolphin.desktop") && dock.isTileIcon("firefox") && !dock.isTileLauncher("applications:org.kde.kdevelop.desktop") && !dock.isTileIcon("kdevelop"), "tile probe ran: Files' desktop file and the firefox icon name are Fab OS tiles, KDevelop is not")
        if (!h.realBackend) check(task(0).isTile && task(2).isTile && task(5).isTile && task(8).isTile === false, "tile detection per row: Overview, Files, Firefox tiles; KDevelop (Breeze) full box")
        row.forceLayout()   // a swapped-in model is positioned on the next polish; measure the resting geometry a moment later
        stage1c.start()
    } }
    Timer { id: stage1c; interval: 250; onTriggered: {
        var b = dock.baseSize, n = dock.count, expReserve = (Math.round(b * 1.6) - b) + 2 * (Math.round(b * 1.3) - b) + 2 * (Math.round(b * 1.1) - b)
        check(dock.restingWidth === n * b + (n - 1) * dock.gap && row.implicitWidth === dock.restingWidth, "resting row width = count x resting size + gaps (" + dock.restingWidth + " = row " + row.implicitWidth + ")")
        check(dock.reserve === expReserve && dock.appletWidth === dock.restingWidth + dock.reserve && dock.appletWidth > row.implicitWidth, "applet width (Layout hints) = resting row + one magnified group's growth (" + dock.reserve + " px reserve)")
        h.idleWidth = dock.appletWidth
        h.idleCentre = centreOf(4)
        check(Math.abs(h.idleCentre - (dock.width / 2 - dock.restingWidth / 2 + 4 * (b + dock.gap) + b / 2)) <= 1, "resting centre of slot 4 measured after layout (" + h.idleCentre.toFixed(1) + ")")
        grab(dock, "/out/dock-idle.png")
        dock.hoveredIndex = 4
        stage2.start()
    } }
    Timer { id: stage2; interval: 450; onTriggered: {
        var s = []; for (var i = 0; i < dock.count; i++) s.push(dock.itemAt(i).s.toFixed(2))
        console.log("INFO scales after hover(4): " + s.join(" "))
        check(near(dock.itemAt(4).s, 1.6), "hovered icon 1.6")
        check(near(dock.itemAt(3).s, 1.3) && near(dock.itemAt(5).s, 1.3), "neighbours 1.3")
        check(near(dock.itemAt(2).s, 1.1) && near(dock.itemAt(6).s, 1.1), "second neighbours 1.1")
        check(near(dock.itemAt(0).s, 1.0) && near(dock.itemAt(1).s, 1.0) && near(dock.itemAt(7).s, 1.0), "others 1.0 (the start button among them)")
        var overlap = false, widthsOk = true
        for (var j = 0; j < dock.count; j++) {
            var a = dock.itemAt(j)
            if (a.width !== Math.round(dock.baseSize * a.s)) widthsOk = false
            if (j + 1 < dock.count) { var nb = dock.itemAt(j + 1); if (a.x + a.width > nb.x + 0.5) overlap = true }
        }
        check(widthsOk, "item width = resting size x scale (row re-flows)")
        check(!overlap, "no overlap between neighbours while magnified")
        check(dock.appletWidth === h.idleWidth, "applet width unchanged while magnified (" + dock.appletWidth + "): the fit panel never resizes on hover")
        check(row.implicitWidth <= dock.appletWidth && row.implicitWidth === dock.restingWidth + dock.reserve, "magnified row fills exactly the reserved width (" + row.implicitWidth + " of " + dock.appletWidth + ")")
        check(Math.abs(centreOf(4) - h.idleCentre) <= 1, "hovered icon keeps its centre (" + h.idleCentre.toFixed(1) + " -> " + centreOf(4).toFixed(1) + "): the pointer stays over the same icon")
        check(dock.itemAt(4).height - dock.dotSpace >= Math.round(dock.baseSize * 1.6) - 1, "magnified icon fits the panel height (" + Math.round(dock.baseSize * 1.6) + " <= " + (dock.itemAt(4).height - dock.dotSpace) + ")")
        grab(dock, "/out/dock-hover.png")
        task(0).launchBounce()
        stage3.start()
    } }
    Timer { id: stage3; interval: 150; onTriggered: {
        check(task(0).bounce > 1.05, "launch bounce grows (" + task(0).bounce.toFixed(2) + ")")
        stage4.start()
    } }
    Timer { id: stage4; interval: 400; onTriggered: {
        check(near(task(0).bounce, 1.0, 0.01), "bounce settles at 1.0")
        dock.hoveredIndex = -1
        task(1).openMenu()
        stage5.start()
    } }
    Timer { id: stage5; interval: 400; onTriggered: {
        check(near(dock.itemAt(4).s, 1.0), "leaving resets scales")
        if (h.realBackend) console.log("INFO context menu under kwin: menuOpen=" + task(1).menuOpen + " (a QMenu popup needs a pointer grab the virtual session has not got; asserted offscreen)")
        else check(task(1).menuOpen === true, "context menu opened (Pin/Unpin, New Window, Close, Configure)")
        task(1).closeMenu()
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
        startItem.launchBounce()
        check(dock.startCommand.indexOf("activateLauncherMenu") > 0 && dock.peekCommand.indexOf("Show Desktop") > 0, "start opens the launcher menu, peek invokes KWin's Show Desktop")
        if (!h.realBackend) stageM0.start(); else if (h.wayland) stageD0.start(); else done.start()
    } }

    // ---- icon-extent measurement (offscreen, stand-in model): raw at tileScale 1.0, then at the shipped value
    Timer { id: stageM0; interval: 400; onTriggered: {
        h.shippedTileScale = dock.cfg.tileScale
        console.log("INFO shipped tileScale = " + h.shippedTileScale + "; measuring at 1.0 first")
        dock.cfg.tileScale = 1.0
        refRow.visible = true
        stageM1.start()
    } }
    Timer { id: stageM1; interval: 600; onTriggered: {
        check(task(0).glyph.width === dock.baseSize && startItem.glyph.width === dock.baseSize && peekItem.glyph.width === dock.baseSize, "raw pass: every icon box is the full resting size (" + dock.baseSize + ")")
        for (var i = 0; i < dock.count; i++) grabRaw(dock.itemAt(i).glyph, "/out/measure/raw-" + (i < 10 ? "0" : "") + i + "-" + itemName(i) + ".png")
        for (var k = 0; k < refRep.count; k++) grabRaw(refRep.itemAt(k), "/out/measure/breeze-" + h.breezeNames[k] + ".png")
        stageM2.start()
    } }
    Timer { id: stageM2; interval: 700; onTriggered: {
        dock.cfg.tileScale = h.shippedTileScale
        stageM3.start()
    } }
    Timer { id: stageM3; interval: 600; onTriggered: {
        check(task(0).glyph.width === Math.round(dock.baseSize * h.shippedTileScale) && task(8).glyph.width === dock.baseSize && startItem.glyph.width === Math.round(dock.baseSize * h.shippedTileScale) && peekItem.glyph.width === Math.round(dock.baseSize * h.shippedTileScale), "normalised pass: tiles (apps, start, peek) at tileScale " + h.shippedTileScale + " = " + task(0).glyph.width + " px, the Breeze icon keeps the full box " + task(8).glyph.width)
        for (var i = 0; i < dock.count; i++) grabRaw(dock.itemAt(i).glyph, "/out/measure/norm-" + (i < 10 ? "0" : "") + i + "-" + itemName(i) + ".png")
        grab(dock, "/out/dock-uniform.png")
        refRow.visible = false
        done.start()
    } }

    // ---- real pointer stages (kwin session with the real TasksModel)
    Timer { id: stageD0; interval: 300; onTriggered: {
        shell.connectSource("PYTHONHOME=/usr nohup /tmp/fabos-pointer " + h.injector + " hold 120 >/tmp/xdg/hold.log 2>&1 &")   // one device for the whole phase
        dock.Window.window.visibility = Window.FullScreen   // scene coordinates = screen coordinates
        // Keep the dock's real proportions inside the fullscreen window: the row in a 70 px strip along the top edge, items
        // 70 px tall (a 400 px tall item would put its tooltip window under the pointer and steal the pointer focus).
        row.anchors.centerIn = undefined; row.anchors.top = dock.top; row.anchors.horizontalCenter = dock.horizontalCenter; row.height = 70
        for (var i = 0; i < dock.count; i++) dock.itemAt(i).height = 70
        stageD1.start()
    } }
    Timer { id: stageD1; interval: 1200; onTriggered: {
        console.log("INFO pointer test: dock " + dock.width + "x" + dock.height + " baseSize " + dock.baseSize + " slots " + dock.count + " visibility " + dock.Window.window.visibility)
        movePointer(dock.width / 2, dock.height - 40, function() {   // warm-up: enter the window somewhere neutral (below the strip)
        var p4 = screenPos(dock.itemAt(4), 0.5, 0.7)
        movePointer(p4.x, p4.y, function() {
            check(dock.hoveredIndex === 4 && near(dock.itemAt(4).s, 1.6) && near(dock.itemAt(3).s, 1.3) && near(dock.itemAt(5).s, 1.3), "real pointer from the empty dock area onto slot 4: hoveredIndex 4, scales 1.6 / 1.3 (" + dock.hoveredIndex + ", " + dock.itemAt(4).s.toFixed(2) + ")")
            var p5 = screenPos(dock.itemAt(5), 0.5, 0.7)
            movePointer(p5.x, p5.y, function() {
                check(dock.hoveredIndex === 5 && near(dock.itemAt(5).s, 1.6) && near(dock.itemAt(4).s, 1.3), "pointer moved to slot 5 (re-flowed row): hoveredIndex 5 (" + dock.hoveredIndex + ")")
                movePointer(dock.width / 2, dock.height - 40, function() {
                    check(dock.hoveredIndex === -1 && near(dock.itemAt(5).s, 1.0), "pointer left the dock: scales back to 1.0")
                    var p1 = screenPos(task(0), 0.5, 0.7)
                    clickPointer(p1.x, p1.y, function() {
                        check(task(0).lastAction === "launch", "real left click on the Overview launcher: TapHandler -> activate -> \"" + task(0).lastAction + "\"")
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
