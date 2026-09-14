import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.taskmanager as TaskManager

// Fab OS dock: the libtaskmanager TasksModel (the backend of the stock task manager — launchers from this applet's
// config, one icon per application, activity / virtual-desktop filters) rendered as a Row of icons with macOS-like
// magnification: the hovered icon scales to `peak`, its neighbours to `near` / `far` (160 ms OutCubic), and because
// each item's width follows its scale the row re-flows and icons never overlap. Resting icons are sized so the
// magnified one still fits the panel height (a panel clips its applets). Scale only — no shaders, no per-icon effects.
PlasmoidItem {
    id: dock
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Layout.minimumWidth: row.implicitWidth
    Layout.preferredWidth: row.implicitWidth
    Layout.fillHeight: true

    // ---------------------------------------------------------------- settings
    readonly property var cfg: Plasmoid.configuration      // reachable from other files (the harness)
    readonly property bool magnify: Plasmoid.configuration.magnify
    readonly property string magnification: Plasmoid.configuration.magnification
    readonly property real peak: magnification === "subtle" ? 1.3 : (magnification === "strong" ? 1.9 : 1.6)
    readonly property real near: magnification === "subtle" ? 1.15 : (magnification === "strong" ? 1.45 : 1.3)
    readonly property real far: magnification === "subtle" ? 1.05 : (magnification === "strong" ? 1.15 : 1.1)
    readonly property int dotSpace: 5                       // room under the icon for the running indicator
    readonly property int avail: Math.max(24, Math.round(dock.height) - dotSpace)
    readonly property int baseSize: Math.min(Plasmoid.configuration.maxIconSize, magnify ? Math.max(20, Math.floor(avail / peak)) : avail)
    readonly property int gap: 2

    // ---------------------------------------------------------------- hover state (the harness sets hoveredIndex directly)
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
    readonly property int count: tasksModel.count
    function itemAt(i) { return repeater.itemAt(i) }

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
        Repeater {
            id: repeater
            model: tasksModel
            delegate: TaskItem { }
        }
    }
    HoverHandler { id: rowHover; onHoveredChanged: if (!hovered) dock.hoveredIndex = -1 }
    Accessible.role: Accessible.ToolBar
    Accessible.name: "Dock"
}
