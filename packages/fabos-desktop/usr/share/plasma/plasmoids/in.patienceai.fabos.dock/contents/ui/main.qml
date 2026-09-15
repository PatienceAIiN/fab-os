import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.taskmanager as TaskManager

// Fab OS dock: the libtaskmanager TasksModel (the backend of the stock task manager — launchers from this applet's
// config, one icon per application, activity / virtual-desktop filters) rendered as a Row of icons with macOS-like
// magnification: the hovered icon scales to `peak`, its neighbours to `near` / `far` (160 ms OutCubic), and because
// each item's width follows its scale the row re-flows and icons never overlap. Resting icons are sized so the
// magnified one still fits the panel height (a panel clips its applets). Scale only — no shaders, no per-icon effects,
// and nothing animates continuously: every motion here is a Behavior (magnify, running-dot width) or a bounded run
// (launch bounce, a few startup pulses), so an idle dock costs no frames at all.
//
// ONE size for everything in the dock: the Fab OS start button (first) and "peek at the desktop" (last) are items of
// this row (ExtraItem.qml) — not separate panel applets, whose icons fill the panel (64 px kickoff) or sit at a fixed
// 32 px (showdesktop) beside 40 px resting task icons. The stock launcher applet is still in the panel, zero-width
// (layout.js gives it no icon and no label), so the Meta key and plasmashell's activateLauncherMenu() keep working;
// the start button calls the same method. Visible extents are normalised too: FabOS app icons are full-bleed rounded
// tiles while Breeze app icons keep a margin inside their box, so tiles (detected through the launcher's desktop file:
// its Icon= resolves in /usr/share/icons/FabOS/scalable/apps) are drawn at `tileScale` of the box — and so are the
// start button (the theme's launcher tile) and peek (a neutral tile behind its glyph): every item is a tile of one
// visible extent, equal to a Breeze app icon's glyph. tileScale was measured from the offscreen render
// (tests/dock-qml-harness/measure.py over the harness's per-icon grabs; dock-uniform.png).
//
// The applet's own width does NOT follow the hover: it is the resting row plus the room one fully magnified group
// needs (`reserve`), so the floating "fit" panel keeps its length while the pointer moves (no per-frame panel resize)
// and the centred row grows into that reserve. With the row centred and the growth symmetric about the hovered icon,
// a hovered interior icon keeps its centre where it rested — the pointer stays over the same icon.
PlasmoidItem {
    id: dock
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Layout.minimumWidth: dock.appletWidth
    Layout.preferredWidth: dock.appletWidth
    Layout.fillHeight: true

    // ---------------------------------------------------------------- settings
    readonly property var cfg: Plasmoid.configuration      // reachable from other files (the harness)
    readonly property bool magnify: Plasmoid.configuration.magnify
    readonly property string magnification: Plasmoid.configuration.magnification
    readonly property real peak: magnification === "subtle" ? 1.3 : (magnification === "strong" ? 1.9 : 1.6)
    readonly property real near: magnification === "subtle" ? 1.15 : (magnification === "strong" ? 1.45 : 1.3)
    readonly property real far: magnification === "subtle" ? 1.05 : (magnification === "strong" ? 1.15 : 1.1)
    readonly property real tileScale: Plasmoid.configuration.tileScale > 0 ? Plasmoid.configuration.tileScale : 1.0
    readonly property int dotSpace: 5                       // room under the icon for the running indicator
    readonly property int avail: Math.max(24, Math.round(dock.height) - dotSpace)
    readonly property int baseSize: Math.min(Plasmoid.configuration.maxIconSize, magnify ? Math.max(20, Math.floor(avail / peak)) : avail)
    readonly property int gap: 2
    readonly property int startCount: Plasmoid.configuration.showStart ? 1 : 0
    readonly property int peekCount: Plasmoid.configuration.showPeek ? 1 : 0
    readonly property int count: repeater.count + startCount + peekCount   // every icon in the row (tasks + start + peek)
    readonly property int restingWidth: count * baseSize + Math.max(0, count - 1) * gap
    // extra width of one magnified group (hovered + 2 near + 2 far), in the same rounding TaskItem uses for its width
    readonly property int reserve: magnify ? (Math.round(baseSize * peak) - baseSize) + 2 * (Math.round(baseSize * near) - baseSize) + 2 * (Math.round(baseSize * far) - baseSize) : 0
    readonly property int appletWidth: restingWidth + reserve   // constant while the pointer moves: the panel's length never follows a hover

    // ---------------------------------------------------------------- shared "magnify on hover" switch
    // The quick-settings applet in the top bar carries the same switch and pushes it here through plasmashell's
    // scripting API; when it is changed on THIS applet's page the dock writes it back the same way, so the two pages
    // never disagree. A write of an unchanged value does not emit a change on either side, so there is no ping-pong.
    property bool autoSync: true            // harness sets false (no plasmashell in the container): commands are recorded, not run
    property string lastSync: ""
    property string lastCommand: ""
    onMagnifyChanged: syncTimer.restart()
    Timer { id: syncTimer; interval: 300; onTriggered: { dock.lastSync = dock.syncCommand(dock.magnify); if (dock.autoSync) shell.connectSource(dock.lastSync) } }
    P5Support.DataSource { id: shell; engine: "executable"; onNewData: (source, data) => disconnectSource(source) }
    function syncScript(on) {
        return "var ps = panels(); for (var i = 0; i < ps.length; i++) { var qs = ps[i].widgets(\"in.patienceai.fabos.quicksettings\");"
             + " for (var j = 0; j < qs.length; j++) { qs[j].currentConfigGroup = [\"General\"]; qs[j].writeConfig(\"magnify\", " + (on ? "true" : "false") + ") } }"
    }
    function syncCommand(on) { return "qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '" + syncScript(on) + "'" }
    function run(cmd) { dock.lastCommand = cmd; if (dock.autoSync) shell.connectSource(cmd) }
    // the start button toggles the launcher menu (the zero-width kickoff beside the dock); peek = KWin's Show Desktop
    readonly property string startCommand: "qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.activateLauncherMenu"
    readonly property string peekCommand: "qdbus6 org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.invokeShortcut 'Show Desktop'"

    // ---------------------------------------------------------------- which launchers are Fab OS tiles
    // One shell probe at start (sh + awk + ls): every desktop file's Icon= and the FabOS theme's app tiles; a task whose
    // launcher desktop file names a tile icon — or whose own icon name is one — is drawn at tileScale. Windows without
    // a match (Breeze / hicolor icons) keep the full box. The harness asserts the probe's result.
    property var tileMap: ({})              // desktop file basename -> the tile's icon name
    property var tileNames: ({})            // FabOS tile icon name -> true (a task whose icon NAME is a tile counts too: no desktop file needed)
    property bool tileMapReady: false
    // Why tiles are drawn from their SVG and not through the icon loader: KIconLoader never scales a fixed-size PNG — at
    // the dock's 40 px resting box it centres the theme's 32 px PNG (measured: 32 px visible in a 40 px box) and steps
    // 32 -> 48 -> 64 while the dock magnifies. The SVG rendered at exactly the box size is full-bleed and smooth.
    // Every other icon (Breeze / hicolor) is requested at Breeze's native scalable size (64, inside apps/48's 48..256
    // range) and scaled to the box by the item, so it magnifies smoothly too and keeps its own glyph margin.
    readonly property string tileDir: "/usr/share/icons/FabOS/scalable/apps/"
    readonly property int stdIconSize: 64
    readonly property int tilePeakPx: Math.max(16, Math.round(dock.baseSize * dock.peak * dock.tileScale))   // a tile's raster size: its magnified peak, scaled down at rest
    function tileSource(name) { return "file://" + dock.tileDir + name + ".svg" }
    property bool autoProbe: true           // harness sets false
    readonly property string probeCommand: "sh -c 'ls /usr/share/icons/FabOS/scalable/apps 2>/dev/null; echo ---; awk -F= \"/^Icon=/ && !seen[FILENAME]++ { n = FILENAME; sub(/.*\\\\//, \\\"\\\", n); print n \\\"\\\\t\\\" \\$2 }\" /usr/share/applications/*.desktop /usr/local/share/applications/*.desktop \"$HOME\"/.local/share/applications/*.desktop 2>/dev/null'"
    P5Support.DataSource { id: probe; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); dock.applyTileProbe(String(data["stdout"] || "")) } }
    function applyTileProbe(text) {
        var parts = text.split("\n---\n"), tiles = {}, map = {}
        var names = (parts[0] || "").split("\n")
        for (var i = 0; i < names.length; i++) { var n = names[i].trim(); if (n.slice(-4) === ".svg") tiles[n.slice(0, -4)] = true }
        var lines = (parts[1] || "").split("\n")
        for (var k = 0; k < lines.length; k++) { var f = lines[k].split("\t"); if (f.length === 2 && tiles[f[1].trim()]) map[f[0].trim()] = f[1].trim() }
        dock.tileMap = map; dock.tileNames = tiles; dock.tileMapReady = true
    }
    function isTileIcon(dec) { return typeof dec === "string" && dock.tileNames[dec] === true }   // model.decoration is the icon name for launchers
    function tileNameFor(url, dec) {   // "applications:org.kde.dolphin.desktop" or a file URL -> tileMap lookup by basename; else the icon name itself
        var u = String(url || "")
        if (u) {
            var base = u.slice(u.lastIndexOf("/") + 1); base = base.slice(base.lastIndexOf(":") + 1)
            var q = base.indexOf("?"); if (q >= 0) base = base.slice(0, q)
            if (typeof dock.tileMap[base] === "string") return dock.tileMap[base]
        }
        return dock.isTileIcon(dec) ? dec : ""
    }
    function isTileLauncher(url) { return dock.tileNameFor(url, "") !== "" }
    Component.onCompleted: if (dock.autoProbe) probe.connectSource(dock.probeCommand)

    // ---------------------------------------------------------------- hover state (the harness sets hoveredIndex directly)
    // Set and cleared by each item's own HoverHandler only. There is deliberately NO HoverHandler on this root item
    // resetting it: an item is a ToolTipArea (an Item that accepts hover), which takes the hover away from the root
    // beneath it, so a root-level "unhovered -> -1" fired the instant the pointer went from a gap onto an icon.
    // Indices are positions in the unified row: start = 0, tasks follow, peek last.
    property int hoveredIndex: -1
    function scaleFor(i) {
        if (!dock.magnify || dock.hoveredIndex < 0) return 1.0
        var d = Math.abs(i - dock.hoveredIndex)
        return d === 0 ? dock.peak : (d === 1 ? dock.near : (d === 2 ? dock.far : 1.0))
    }

    // ---------------------------------------------------------------- backend
    TaskManager.VirtualDesktopInfo { id: virtualDesktopInfo }
    TaskManager.ActivityInfo { id: activityInfo }
    TaskManager.TasksModel {
        id: tasksModel
        virtualDesktop: virtualDesktopInfo.currentDesktop
        activity: activityInfo.currentActivity
        filterByVirtualDesktop: Plasmoid.configuration.showOnlyCurrentDesktop
        filterByActivity: Plasmoid.configuration.showOnlyCurrentActivity
        filterByScreen: false
        filterNotMinimized: false
        filterHidden: true
        sortMode: TaskManager.TasksModel.SortManual
        launchInPlace: true                 // a launcher becomes its running window in place (icons-only behaviour)
        separateLaunchers: false
        groupMode: Plasmoid.configuration.groupApps ? TaskManager.TasksModel.GroupApplications : TaskManager.TasksModel.GroupDisabled
        groupInline: false
        groupingWindowTasksThreshold: -1
        onLauncherListChanged: Plasmoid.configuration.launchers = launcherList
        Component.onCompleted: launcherList = Plasmoid.configuration.launchers
    }
    readonly property int taskCount: repeater.count          // task rows actually rendered (= tasksModel.count; the harness may swap the model)
    function itemAt(i) {                                    // unified row index -> the item (start, task or peek)
        if (dock.startCount && i === 0) return startItem
        var t = i - dock.startCount
        if (t < repeater.count) return repeater.itemAt(t)
        return dock.peekCount && t === repeater.count ? peekItem : null
    }

    // Activate like the icons-only task manager: launchers launch, the active window minimises, a minimised or
    // inactive window comes forward, a group cycles through its windows.
    function activate(i) {
        var idx = tasksModel.makeModelIndex(i)
        var A = TaskManager.AbstractTasksModel
        if (tasksModel.data(idx, A.IsLauncher) === true) { tasksModel.requestActivate(idx); return "launch" }
        if (tasksModel.data(idx, A.IsGroupParent) === true) {
            var n = tasksModel.rowCount(idx), active = -1
            for (var c = 0; c < n; c++) if (tasksModel.data(tasksModel.makeModelIndex(i, c), A.IsActive) === true) { active = c; break }
            tasksModel.requestActivate(tasksModel.makeModelIndex(i, n > 0 ? (active + 1) % n : 0))
            return "cycle"
        }
        if (tasksModel.data(idx, A.IsActive) === true && tasksModel.data(idx, A.IsMinimized) !== true) { tasksModel.requestToggleMinimized(idx); return "minimize" }
        tasksModel.requestActivate(idx)
        return "activate"
    }

    Row {
        id: row
        anchors.centerIn: parent
        height: dock.height
        spacing: dock.gap
        ExtraItem {   // Fab OS start button: the launcher menu
            id: startItem
            visible: dock.startCount > 0
            slot: 0
            icon: "start-here"                    // the icon theme's launcher tile: the Fab OS mark on a dark tile
            source: dock.tileSource("start-here") // its SVG at the exact box size (see tileSource)
            iconScale: dock.tileScale
            tip: "Fab OS"; tipSub: "Apps, files and power (Meta key)"
            onClicked: dock.run(dock.startCommand)
        }
        Repeater {
            id: repeater
            model: tasksModel
            delegate: TaskItem { }
        }
        ExtraItem {   // peek at the desktop (KWin's Show Desktop; the bottom-right hot corner does the same)
            id: peekItem
            visible: dock.peekCount > 0
            slot: dock.startCount + repeater.count
            icon: "user-desktop"
            tileBackground: true                  // neutral tile behind the monochrome glyph
            iconScale: dock.tileScale
            tip: "Peek at the desktop"; tipSub: "Click again to bring the windows back (Meta+D)"
            onClicked: dock.run(dock.peekCommand)
        }
    }
    readonly property Item start: startItem
    readonly property Item peek: peekItem
    Accessible.role: Accessible.ToolBar
    Accessible.name: "Dock"
}
