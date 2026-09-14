import QtQuick
import QtQuick.Controls as QQC2
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami
import org.kde.taskmanager as TaskManager

// One dock icon. `s` is the magnification for this index (from the dock's hoveredIndex); width and the icon size
// follow it through one animated property, so the Row re-flows as neighbours grow. Bounce on launch, running dot,
// tooltip with the window title(s), left = activate / minimise / cycle, middle = new instance, right = context menu.
PlasmaCore.ToolTipArea {
    id: item
    required property int index
    required property var model
    readonly property bool running: model.IsWindow === true || model.IsGroupParent === true
    readonly property bool pinned: model.IsLauncher === true || model.HasLauncher === true
    readonly property int childCount: model.ChildCount || 0

    property real s: dock.scaleFor(index)
    Behavior on s { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    property real bounce: 1.0
    property string lastAction: ""

    width: Math.round(dock.baseSize * s)
    height: dock.height
    mainText: model.display || ""
    subText: model.IsGroupParent ? item.childCount + " windows" : (model.IsLauncher ? (model.GenericName || "Click to open") : (model.AppName && model.AppName !== model.display ? model.AppName : ""))
    icon: model.decoration
    active: dock.hoveredIndex === index

    function launchBounce() { bounceAnim.restart() }
    SequentialAnimation {
        id: bounceAnim
        NumberAnimation { target: item; property: "bounce"; to: 1.15; duration: 150; easing.type: Easing.OutCubic }
        NumberAnimation { target: item; property: "bounce"; to: 1.0; duration: 150; easing.type: Easing.InCubic }
    }
    function publishGeometry() {
        try {
            var p = item.mapToItem(null, 0, 0)
            tasksModel.requestPublishDelegateGeometry(tasksModel.makeModelIndex(index), Qt.rect(p.x, p.y, item.width, item.height), item)
        } catch (e) {}
    }
    Timer { id: publishTimer; interval: 200; onTriggered: item.publishGeometry() }
    onXChanged: publishTimer.restart()
    onWidthChanged: publishTimer.restart()
    Component.onCompleted: publishTimer.restart()

    Kirigami.Icon {
        id: glyph
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: dock.dotSpace
        width: Math.round(dock.baseSize * item.s)
        height: width
        source: item.model.decoration
        fallback: "application-x-executable"
        scale: item.bounce
        transformOrigin: Item.Bottom
        opacity: item.model.IsMinimized === true ? 0.6 : 1.0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        SequentialAnimation on opacity {   // startup feedback: pulse while the app is launching
            running: item.model.IsStartup === true
            loops: Animation.Infinite
            alwaysRunToEnd: true
            NumberAnimation { to: 0.35; duration: 450; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 450; easing.type: Easing.InOutSine }
        }
    }
    Rectangle {   // running indicator; a wider pill for a group, accent when active, attention colour when demanding
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        visible: item.running
        width: item.model.IsGroupParent === true && item.childCount > 1 ? 12 : 5
        height: 3; radius: 1.5
        color: item.model.IsDemandingAttention === true ? Kirigami.Theme.negativeTextColor : (item.model.IsActive === true ? Kirigami.Theme.highlightColor : Kirigami.Theme.textColor)
        opacity: item.model.IsActive === true || item.model.IsDemandingAttention === true ? 1 : 0.6
        Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    }

    HoverHandler { onHoveredChanged: if (hovered) dock.hoveredIndex = item.index; else if (dock.hoveredIndex === item.index) dock.hoveredIndex = -1 }
    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: { item.lastAction = dock.activate(item.index); if (item.lastAction === "launch") item.launchBounce() }
    }
    TapHandler {
        acceptedButtons: Qt.MiddleButton
        onTapped: { tasksModel.requestNewInstance(tasksModel.makeModelIndex(item.index)); item.launchBounce(); item.lastAction = "new" }
    }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: item.openMenu()
    }
    property bool menuOpen: false
    function openMenu() { menu.visualParent = item; item.menuOpen = true; menu.openRelative() }
    function closeMenu() { menu.close(); item.menuOpen = false }
    Connections { target: menu; function onStatusChanged() { if (menu.status === 3) item.menuOpen = false } }   // DialogStatus.Closed

    // Own context menu on the TasksModel requests (the stock task manager's menu lives inside its compiled applet and
    // is not importable): new window, minimise / restore, pin / unpin, close, configure.
    PlasmaExtras.Menu {
        id: menu
        placement: PlasmaExtras.Menu.TopPosedLeftAlignedPopup
        PlasmaExtras.MenuItem {
            text: "New Window"; icon: "window-new"
            visible: item.model.IsLauncher === true || item.model.CanLaunchNewInstance === true
            onClicked: { tasksModel.requestNewInstance(tasksModel.makeModelIndex(item.index)); item.launchBounce() }
        }
        PlasmaExtras.MenuItem {
            text: item.model.IsMinimized === true ? "Restore" : "Minimise"; icon: "window-minimize"
            visible: item.model.IsWindow === true && item.model.IsMinimizable === true
            onClicked: tasksModel.requestToggleMinimized(tasksModel.makeModelIndex(item.index))
        }
        PlasmaExtras.MenuItem { separator: true; visible: item.model.IsLauncher === true || item.model.CanLaunchNewInstance === true || item.model.IsWindow === true }
        PlasmaExtras.MenuItem {
            text: item.pinned ? "Unpin from Dock" : "Pin to Dock"; icon: item.pinned ? "window-unpin" : "window-pin"
            visible: !!item.model.LauncherUrlWithoutIcon && String(item.model.LauncherUrlWithoutIcon).length > 0
            onClicked: item.pinned ? tasksModel.requestRemoveLauncher(item.model.LauncherUrlWithoutIcon) : tasksModel.requestAddLauncher(item.model.LauncherUrlWithoutIcon)
        }
        PlasmaExtras.MenuItem {
            text: item.model.IsGroupParent === true ? "Close All" : "Close"; icon: "window-close"
            visible: item.model.IsClosable === true
            onClicked: tasksModel.requestClose(tasksModel.makeModelIndex(item.index))
        }
        PlasmaExtras.MenuItem { separator: true }
        PlasmaExtras.MenuItem {
            text: "Configure Dock…"; icon: "configure"
            onClicked: Plasmoid.internalAction("configure").trigger()
        }
    }
    Accessible.role: Accessible.Button
    Accessible.name: item.mainText
}
