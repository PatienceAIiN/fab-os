import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.notificationmanager as NotificationManager
import "status.js" as Status

// Fab OS quick settings (top bar). One compact indicator group — network glyph with the live ↓/↑ speed (always on the
// bar while a link is up; "0 kB/s" when idle), Bluetooth, volume (while muted / just changed), battery glyph +
// percentage, bell with unread badge — and one PlasmaCore.Dialog holding either the settings tiles or the notification
// history.
//
// No-blink slide-down: the dialog window is TRANSPARENT (backgroundHints NoBackground) and opens at its FINAL size at
// once; only the card inside it moves — y from -height to 0 and opacity 0 -> 1 in 220 ms OutCubic; closing reverses and
// hides the window when the animation has ended. A window that changes size while it animates is re-rasterised by the
// compositor on every frame (the "blink"); this one never changes size while anything moves. The rounded card is drawn
// here: background colour @ 96 %, hairline, radius 24 at the bottom corners, a soft shadow from a second translucent
// rectangle. Switching settings <-> notifications while open is one instant resize plus a 160 ms cross-fade.
//
// Tiles: Plasmoid.configuration.tilesJson (status.js TILES / parseTiles / layoutTiles) — every tile has an order, a
// size (small = half a row, wide = a full row) and enabled; the pencil toggles edit mode: drag to reorder (DragHandler,
// slot from layoutTiles + tileAt), size toggle, remove, "+ tile" chips for the removed ones, reset. The same model is
// edited on the "Tiles" settings page.
//
// Data: contents/code/status.sh through the Plasma5Support executable engine — ONE script call, ONE JSON line. While the
// pane is closed the periodic call is the --light probe (kernel readings + nmcli's ACTIVE,SIGNAL for the Wi-Fi glyph)
// every `pollSeconds` (default 5 s) and the full probe (NetworkManager, BlueZ, volume, power profile, night light)
// every `fullSeconds` (30 s) plus once whenever a light probe sees the link change; while the pane is open the full
// probe runs every 2 s; after each action the full probe runs once, 400 ms later. Budget: docs/LOW-RAM.md "Idle budget".
// Actions: nmcli radio wifi, bluetoothctl power, wpctl set-volume/set-mute, powerprofilesctl set, kwriteconfig6
// (night light), powerdevil's ScreenBrightness D-Bus (BrightnessBridge.qml). Notifications + Do Not Disturb:
// org.kde.notificationmanager (the model the stock history uses; same process, same server). The stock applets stay
// reachable: a tile's chevron opens `plasmawindowed <applet>`.
PlasmoidItem {
    id: root
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Layout.minimumWidth: bar.implicitWidth + 8
    Layout.preferredWidth: bar.implicitWidth + 8
    Layout.fillHeight: true
    Kirigami.Theme.colorSet: Kirigami.Theme.Window
    Kirigami.Theme.inherit: false

    // ---------------------------------------------------------------- size + hover settings
    readonly property var cfg: Plasmoid.configuration      // reachable from other files (the harness)
    readonly property string barSize: Plasmoid.configuration.barSize
    readonly property bool magnify: Plasmoid.configuration.magnify
    readonly property int glyph: barSize === "small" ? 16 : (barSize === "large" ? 22 : 18)
    readonly property int textPx: barSize === "small" ? 11 : (barSize === "large" ? 13 : 12)
    readonly property int clockPx: Status.clockSizeFor(barSize)
    readonly property int screenH: Screen.height > 0 ? Screen.height : 1080
    onBarSizeChanged: syncTimer.restart()
    onMagnifyChanged: syncTimer.restart()
    // The clock is a separate applet and Plasmoid.configuration of another applet is not writable from QML, so the new
    // size (and the shared "magnify on hover" switch for the dock) go through plasmashell's own scripting API on D-Bus.
    // Ownership: this applet owns the bar size and the magnify switch; the dock owns its magnification strength and
    // writes the switch back the same way when it is changed on the dock's page (an unchanged value re-triggers nothing).
    Timer { id: syncTimer; interval: 300; onTriggered: root.run(Status.syncCommand(root.barSize, root.magnify)) }
    property string lastSync: ""

    // ---------------------------------------------------------------- state
    property var st: Status.empty()
    property real down: 0
    property real up: 0
    property var netPrev: null
    property real netPrevAt: 0
    readonly property bool speedVisible: Plasmoid.configuration.showSpeed && st.iface !== ""   // always while a link is up, 0 kB/s included
    property bool volumeFlash: false
    property string paneMode: "closed"       // closed | settings | notifications
    property string paneContent: "settings"  // what the card holds: set when a pane opens, KEPT while it closes and after (the window must not resize while the card slides up)
    property bool closing: false
    property bool dnd: false
    property bool paneAutoHide: true        // harness sets false (offscreen windows never activate)
    property bool autoRefresh: true         // probe the machine (periodic + after each action); the harness sets false and feeds state itself
    readonly property string statusScript: Qt.resolvedUrl("../code/status.sh").toString().replace(/^file:\/\//, "")
    // `timeout 8` around the whole script: a stuck daemon never leaves a probe behind (the script itself bounds each tool)
    readonly property string statusCmd: "timeout 8 sh " + root.statusScript + (root.paneMode !== "closed" ? " --pane" : "")   // full probe
    readonly property string lightCmd: "timeout 8 sh " + root.statusScript + " --light"                                        // kernel readings + Wi-Fi signal
    readonly property bool paneOpen: root.paneMode !== "closed"
    readonly property int unread: history.unreadNotificationsCount

    // ---------------------------------------------------------------- data sources (one script, adaptive cadence)
    P5Support.DataSource {   // periodic: the light probe every pollSeconds while closed, the full probe every 2 s while open
        id: poll
        engine: "executable"
        interval: root.paneOpen ? 2000 : Math.max(5, Plasmoid.configuration.pollSeconds) * 1000
        connectedSources: root.autoRefresh ? [root.paneOpen ? root.statusCmd : root.lightCmd] : []   // switching the source runs it at once
        onNewData: (source, data) => root.applyStatus(String(data["stdout"] || ""))
    }
    P5Support.DataSource {   // one-shot full probe: 400 ms after each action, and every 30 s while the pane is closed
        id: probe
        engine: "executable"
        onNewData: (source, data) => { disconnectSource(source); root.applyStatus(String(data["stdout"] || "")) }
    }
    readonly property int fullSeconds: 30
    Timer { id: fullTimer; interval: root.fullSeconds * 1000; repeat: true; running: root.autoRefresh && !root.paneOpen; onTriggered: root.refresh() }
    P5Support.DataSource {   // one-shot actions; the full status is re-read 400 ms after each finishes
        id: actions
        engine: "executable"
        onNewData: (source, data) => { disconnectSource(source); refreshTimer.restart() }
    }
    Timer { id: refreshTimer; interval: 400; onTriggered: root.refresh() }
    function run(cmd) { root.lastSync = cmd.indexOf("evaluateScript") >= 0 ? cmd : root.lastSync; actions.connectSource(cmd) }
    function launch(cmd) { root.run(Status.detach(cmd)) }
    function refresh() { if (!root.autoRefresh) return; probe.disconnectSource(root.statusCmd); probe.connectSource(root.statusCmd) }

    // one probe result (JSON line; the first release's key=value form is still understood). A --light line refreshes
    // the kernel readings and the Wi-Fi signal inside the last full state; every probe feeds the counters for the rate.
    function applyStatus(text) {
        var s = Status.parseStatus(text)
        if (s.light) {
            var linkChanged = Status.linkChanged(root.st, s)
            s = Status.mergeLight(root.st, s)
            if (linkChanged && root.autoRefresh && !root.paneOpen) refreshTimer.restart()   // joined / left a network elsewhere: one full probe now
        }
        else if (root.st.hasAudio && s.hasAudio && (s.volume !== root.st.volume || s.muted !== root.st.muted)) root.flashVolume()
        root.st = s
        root.applyNet(Status.counters(s), Date.now())
        if (!s.light) root.refreshDnd()
    }
    // sample = {iface, rx, tx} (from a probe) or the older "/proc/net/route --- /proc/net/dev" text (the harness feeds that)
    function applyNet(sample, now) {
        var cur = typeof sample === "string" ? Status.parseNet(sample) : sample
        if (root.netPrev) { var r = Status.rates(root.netPrev, cur, now - root.netPrevAt); root.down = r.down; root.up = r.up }
        root.netPrev = cur; root.netPrevAt = now
        if (cur.iface !== root.st.iface) { var s = Object.assign({}, root.st); s.iface = cur.iface; root.st = s }
    }
    function flashVolume() { root.volumeFlash = true; volumeTimer.restart() }
    Timer { id: volumeTimer; interval: 3000; onTriggered: root.volumeFlash = false }

    // volume: slider drags are coalesced to one wpctl call per 120 ms
    property int pendingVolume: -1
    function setVolume(pct) { root.pendingVolume = Math.max(0, Math.min(100, Math.round(pct))); volumeFlush.restart() }
    Timer { id: volumeFlush; interval: 120; onTriggered: { if (root.pendingVolume >= 0) { root.run("wpctl set-volume @DEFAULT_AUDIO_SINK@ " + (root.pendingVolume / 100).toFixed(2)); var s = Object.assign({}, root.st); s.volume = root.pendingVolume; root.st = s; root.flashVolume() } root.pendingVolume = -1 } }
    function toggleMute() { root.run("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"); var s = Object.assign({}, root.st); s.muted = !s.muted; root.st = s; root.flashVolume() }
    function toggleWifi() { root.run("nmcli radio wifi " + (root.st.wifiRadio === false ? "on" : "off")) }
    function toggleBluetooth() { root.run("bluetoothctl power " + (root.st.btPowered === false ? "on" : "off")) }
    function setProfile(p) { root.run("powerprofilesctl set " + p); var s = Object.assign({}, root.st); s.profile = p; root.st = s }
    function toggleNight() { var c = Status.nightCommand(root.st); if (!c) return; root.run(c); var s = Object.assign({}, root.st); s.nightEnabled = !s.nightEnabled; s.nightRunning = s.nightEnabled; root.st = s }
    property int pendingBrightness: -1
    function setBrightness(pct) { root.pendingBrightness = pct; brightnessFlush.restart() }
    Timer { id: brightnessFlush; interval: 80; onTriggered: if (root.pendingBrightness >= 0 && brightnessBridge.item) brightnessBridge.item.setPercent(root.pendingBrightness) }
    Loader { id: brightnessBridge; source: "BrightnessBridge.qml"; asynchronous: true }
    readonly property bool brightnessOk: brightnessBridge.status === Loader.Ready && brightnessBridge.item && brightnessBridge.item.available
    readonly property int brightnessPct: root.brightnessOk ? brightnessBridge.item.percent : (root.st.blMax > 0 ? Math.round(root.st.blCur * 100 / root.st.blMax) : -1)

    // ---------------------------------------------------------------- notifications + do not disturb
    NotificationManager.Settings { id: notificationSettings; live: true }
    readonly property var notifSettings: notificationSettings
    NotificationManager.Notifications {
        id: history
        showExpired: true
        showDismissed: true
        showJobs: true
        blacklistedDesktopEntries: notificationSettings.historyBlacklistedApplications
        blacklistedNotifyRcNames: notificationSettings.historyBlacklistedServices
        urgencies: NotificationManager.Notifications.CriticalUrgency | NotificationManager.Notifications.NormalUrgency
                 | (notificationSettings.lowPriorityHistory ? NotificationManager.Notifications.LowUrgency : 0)
        groupMode: NotificationManager.Notifications.GroupDisabled
    }
    function refreshDnd() {
        var until = notificationSettings.notificationsInhibitedUntil
        root.dnd = (until && !isNaN(until.getTime()) && until.getTime() > Date.now()) || notificationSettings.notificationsInhibitedByApplication === true
    }
    function toggleDnd() {
        if (root.dnd) {
            notificationSettings.notificationsInhibitedUntil = undefined
            notificationSettings.revokeApplicationInhibitions()
            notificationSettings.notificationSoundsInhibited = false
        } else {
            var d = new Date(); d.setFullYear(d.getFullYear() + 1)   // "until turned off", as the stock applet does
            notificationSettings.notificationsInhibitedUntil = d
        }
        notificationSettings.save()
        root.refreshDnd()
    }
    Connections { target: notificationSettings; function onSettingsChanged() { root.refreshDnd() } }
    Component.onCompleted: { root.refreshDnd(); root.refresh() }   // one full probe at start; the periodic light probe then keeps the kernel readings fresh

    // ---------------------------------------------------------------- the tiles (order / size / enabled), edit mode
    property var tiles: Status.parseTiles(Plasmoid.configuration.tilesJson)
    Connections { target: Plasmoid.configuration; function onTilesJsonChanged() { var t = Status.parseTiles(Plasmoid.configuration.tilesJson); if (Status.tilesJson(t) !== Status.tilesJson(root.tiles)) root.tiles = t } }
    function saveTiles(arr) { root.tiles = arr; Plasmoid.configuration.tilesJson = Status.tilesJson(arr) }
    property bool editing: false
    property string dragId: ""                 // the tile being dragged, "" otherwise
    function tileAvailable(id) {              // tiles that need hardware or a daemon hide while it is absent (edit mode shows them all)
        var st = root.st
        if (id === "brightness") return root.brightnessPct >= 0
        if (id === "battery") return st.hasBattery
        if (id === "powerprofile") return st.profile.length > 0
        if (id === "nightlight") return st.nightEnabled !== null
        return true
    }
    readonly property var shownTiles: {
        var st = root.st, bp = root.brightnessPct, editing = root.editing, out = []   // read here so the binding follows them
        for (var i = 0; i < root.tiles.length; i++) { var t = root.tiles[i]; if (t.enabled !== false && (editing || root.tileAvailable(t.id))) out.push(t) }
        return out
    }
    readonly property var hiddenTiles: root.tiles.filter(function (t) { return t.enabled === false })
    readonly property int cardPad: 10
    readonly property int settingsWidth: Kirigami.Units.gridUnit * 21
    readonly property int notifWidth: Kirigami.Units.gridUnit * 28
    readonly property var tileLayout: Status.layoutTiles(root.shownTiles, root.settingsWidth - 2 * root.cardPad, 8)
    function tileRect(id) { return Status.rectFor(root.tileLayout, id) }
    function tileSize(id) { for (var i = 0; i < root.tiles.length; i++) if (root.tiles[i].id === id) return root.tiles[i].size; return "small" }
    function reorderTo(id, targetId) { if (id !== targetId) root.tiles = Status.moveTileTo(root.tiles, id, targetId) }   // live while dragging
    function dropTiles() { root.saveTiles(root.tiles) }                                                                  // persisted at release
    readonly property int headerH: 36
    readonly property int footerH: 36
    readonly property int addRowH: root.editing && root.hiddenTiles.length > 0 ? addFlow.implicitHeight + 8 : 0
    readonly property int settingsHeight: 2 * root.cardPad + root.headerH + 8 + root.tileLayout.height + root.addRowH + 8 + root.footerH
    // notifications: header 40, banner while Do Not Disturb, rows (56 px minimum) up to 60 % of the screen, then a scroll
    readonly property int notifMaxList: Math.round(root.screenH * 0.6) - (2 * root.cardPad + 46 + (root.dnd ? 46 : 0))
    readonly property int notifListH: history.count === 0 ? 64 : Math.max(56, Math.min(Math.round(notifList.contentHeight), root.notifMaxList))
    readonly property int notifHeight: 2 * root.cardPad + 40 + 6 + (root.dnd ? 46 : 0) + root.notifListH

    // ---------------------------------------------------------------- pane open / close (imperative: the dialog's
    // visibility is set here and in closeTimer only, so the slide-up can finish before the window hides)
    function openPane(mode) {
        closeTimer.stop(); root.closing = false
        if (root.paneMode === "closed") { openBehavior.enabled = false; pane.openProgress = 0; openBehavior.enabled = true }
        root.paneMode = mode
        root.paneContent = mode
        pane.visible = true              // at its final size: mainItem is bound to the card's size, which does not change while the card moves
        pane.openProgress = 1
        if (mode === "notifications") history.lastRead = new Date()
        // no refresh() here: paneOpen switches the periodic source to the full probe, which runs it at once
    }
    function closePane() {
        if (root.paneMode === "closed") return
        history.lastRead = new Date()
        root.editing = false
        root.closing = true; pane.openProgress = 0; closeTimer.restart()
    }
    function togglePane(mode) { if (root.paneMode === mode && !root.closing) root.closePane(); else root.openPane(mode) }
    Timer { id: closeTimer; interval: 230; onTriggered: { root.closing = false; root.paneMode = "closed"; pane.visible = false } }   // = the slide duration + one frame

    // ---------------------------------------------------------------- the bar
    PlasmaCore.ToolTipArea {
        anchors.fill: parent
        mainText: "Quick settings"
        subText: Status.wifiLine(root.st) + "\n" + Status.batteryLine(root.st) + (root.st.hasAudio ? "\nVolume " + root.st.volume + "%" + (root.st.muted ? " (muted)" : "") : "")
        active: root.paneMode === "closed"
    }
    Row {
        id: bar
        anchors.centerIn: parent
        height: parent.height
        spacing: root.barSize === "small" ? 6 : (root.barSize === "large" ? 10 : 8)

        Indicator {   // network + live speed (always while a link is up)
            id: netInd
            icon: Status.wifiIcon(root.st)
            text: root.speedVisible ? Status.speedText(root.down, root.up) : ""
            glyphSize: root.glyph; textPx: root.textPx; magnify: root.magnify
            tip: Status.wifiLine(root.st)
            onClicked: root.togglePane("settings")
        }
        Indicator {   // bluetooth: only while the adapter is on
            id: btInd
            visible: root.st.btPresent && root.st.btPowered !== false
            icon: Status.bluetoothIcon(root.st)
            glyphSize: root.glyph; magnify: root.magnify
            tip: "Bluetooth · " + Status.bluetoothLine(root.st)
            onClicked: root.togglePane("settings")
        }
        Indicator {   // volume: while muted or for 3 s after a change; wheel adjusts
            id: volInd
            visible: root.st.hasAudio && (root.st.muted || root.volumeFlash)
            icon: Status.volumeIcon(root.st.volume, root.st.muted)
            glyphSize: root.glyph; magnify: root.magnify
            tip: root.st.muted ? "Muted" : "Volume " + root.st.volume + "%"
            onClicked: root.togglePane("settings")
            onWheel: (delta) => root.setVolume(root.st.volume + (delta > 0 ? 5 : -5))
        }
        Indicator {   // battery glyph + percentage in the clock's Inter size
            id: batInd
            visible: root.st.hasBattery
            icon: Status.batteryIcon(root.st.batPct, root.st.batStatus)
            text: root.st.batPct >= 0 ? root.st.batPct + "%" : ""
            glyphSize: root.glyph; textPx: root.clockPx; textWeight: Font.DemiBold; textOpacity: 1.0; magnify: root.magnify
            tip: Status.batteryLine(root.st)
            onClicked: root.togglePane("settings")
        }
        Indicator {   // bell: unread badge, crossed while Do Not Disturb
            id: bellInd
            icon: root.dnd ? "notifications-disabled" : "notifications"
            badge: root.dnd ? 0 : root.unread
            glyphSize: root.glyph; magnify: root.magnify
            tip: root.dnd ? "Do Not Disturb is on" : (root.unread > 0 ? root.unread + " new notification" + (root.unread === 1 ? "" : "s") : "Notifications")
            onClicked: root.togglePane("notifications")
        }
    }

    // ---------------------------------------------------------------- slide-down pane: transparent window, animated card
    PlasmaCore.Dialog {
        id: pane
        visualParent: bar
        location: Plasmoid.location
        type: PlasmaCore.Dialog.AppletPopup
        hideOnWindowDeactivate: root.paneAutoHide
        backgroundHints: PlasmaCore.Dialog.NoBackground
        visible: false
        onVisibleChanged: if (!visible && root.paneMode !== "closed" && !root.closing) { history.lastRead = new Date(); root.editing = false; root.paneMode = "closed" }

        property real openProgress: 0
        Behavior on openProgress { id: openBehavior; NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        readonly property int shadow: 14                                     // room for the shadow beside and under the card
        readonly property bool notif: root.paneContent === "notifications"
        readonly property int cardWidth: notif ? root.notifWidth : root.settingsWidth
        readonly property int cardHeight: notif ? root.notifHeight : root.settingsHeight
        readonly property int radius: 24
        readonly property Item cardItem: card

        mainItem: Item {
            id: paneMain
            Kirigami.Theme.colorSet: Kirigami.Theme.Window
            Kirigami.Theme.inherit: false
            width: pane.cardWidth + 2 * pane.shadow
            height: pane.cardHeight + pane.shadow
            clip: true                                                        // the card slides in from above the window's top edge

            Rectangle {   // soft shadow: a second translucent rectangle a little larger and lower than the card
                x: card.x - 2; y: card.y + 3
                width: card.width + 4; height: card.height + 2
                bottomLeftRadius: pane.radius + 2; bottomRightRadius: pane.radius + 2
                color: Qt.rgba(0, 0, 0, 0.16)
                opacity: card.opacity
            }
            Rectangle {   // the card itself: y -height -> 0 and opacity 0 -> 1; the window around it never changes size while it moves
                id: card
                x: pane.shadow
                y: Math.round(-height * (1 - pane.openProgress))
                width: pane.cardWidth
                height: pane.cardHeight
                opacity: pane.openProgress
                color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g, Kirigami.Theme.backgroundColor.b, 0.96)
                border.width: 1
                border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
                topLeftRadius: 0; topRightRadius: 0
                bottomLeftRadius: pane.radius; bottomRightRadius: pane.radius

                // ================================================ settings: header · tiles · (+ chips) · footer
                Item {
                    id: settingsPane
                    anchors.fill: parent
                    anchors.margins: root.cardPad
                    visible: opacity > 0
                    opacity: root.paneContent === "settings" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 160 } }
                    readonly property int contentHeight: root.settingsHeight

                    RowLayout {   // header: title, reset (edit mode), pencil / done
                        id: settingsHeader
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        height: root.headerH
                        spacing: 4
                        Text { Layout.leftMargin: 6; text: root.editing ? "Arrange tiles" : "Quick settings"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 14; font.weight: Font.DemiBold }
                        Text { visible: root.editing; text: "drag · resize · remove"; color: Kirigami.Theme.textColor; opacity: 0.5; font.family: "Inter"; font.pixelSize: 11 }
                        Item { Layout.fillWidth: true }
                        SmallButton { visible: root.editing; icon: "edit-undo"; tip: "Reset to default"; onClicked: root.saveTiles(Status.defaultTiles()) }
                        SmallButton { id: editButton; icon: root.editing ? "checkmark" : "document-edit"; tip: root.editing ? "Done" : "Edit tiles: drag to arrange, resize, remove"; onClicked: root.editing = !root.editing }
                    }

                    Item {   // the tiles, positioned from status.js layoutTiles; one delegate per known tile, shown when it has a slot
                        id: tilesArea
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: settingsHeader.bottom; anchors.topMargin: 8
                        height: root.tileLayout.height
                        Repeater {
                            id: tileRepeater
                            model: Status.TILES.length            // fixed: a reorder never re-creates a delegate (a drag would die with it)
                            delegate: Item {
                                id: tw
                                required property int index
                                readonly property string tileId: Status.TILES[index].id
                                readonly property var rect: root.tileRect(tileId)
                                readonly property bool wide: root.tileSize(tileId) === "wide"
                                readonly property bool available: root.tileAvailable(tileId)
                                readonly property bool dragging: drag.active
                                readonly property Item content: contentLoader.item
                                readonly property Item editBar: editControls      // harness hooks
                                readonly property Item frame: editFrame
                                visible: rect !== null
                                x: dragging ? tw.dragX : (rect ? rect.x : 0)
                                y: dragging ? tw.dragY : (rect ? rect.y : 0)
                                width: rect ? rect.w : 0
                                height: rect ? rect.h : 0
                                z: dragging ? 10 : 0
                                opacity: available || !root.editing ? 1 : 0.5
                                Behavior on x { enabled: root.editing && !tw.dragging; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                Behavior on y { enabled: root.editing && !tw.dragging; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                Behavior on width { enabled: root.editing; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                property real dragX: 0
                                property real dragY: 0
                                property point origin: Qt.point(0, 0)
                                function dragTo(px, py) {   // pointer-driven (DragHandler) or the harness: follow, and take the slot under the centre
                                    tw.dragX = px; tw.dragY = py
                                    var over = Status.tileAt(root.tileLayout.items, px + tw.width / 2, py + tw.height / 2)
                                    if (over && over !== tw.tileId) root.reorderTo(tw.tileId, over)
                                }
                                DragHandler {
                                    id: drag
                                    enabled: root.editing
                                    target: null
                                    onActiveChanged: {
                                        if (active) { tw.origin = Qt.point(tw.rect.x, tw.rect.y); tw.dragX = tw.origin.x; tw.dragY = tw.origin.y; root.dragId = tw.tileId }
                                        else { root.dragId = ""; root.dropTiles() }
                                    }
                                    onTranslationChanged: if (active) tw.dragTo(tw.origin.x + translation.x, tw.origin.y + translation.y)
                                }
                                Loader {
                                    id: contentLoader
                                    anchors.fill: parent
                                    enabled: !root.editing                 // edit mode: the tile's own controls are inert, the drag and the overlay act
                                    sourceComponent: tw.tileId === "volume" ? volumeRow : tw.tileId === "brightness" ? brightnessRow : tw.tileId === "battery" ? batteryRow : tw.tileId === "netspeed" ? netRow : toggleTile
                                    onLoaded: { item.tileId = tw.tileId; item.wide = Qt.binding(function () { return tw.wide }); item.available = Qt.binding(function () { return tw.available }) }
                                }
                                Rectangle {   // edit-mode frame
                                    id: editFrame
                                    visible: root.editing
                                    anchors.fill: parent
                                    radius: 14
                                    color: "transparent"
                                    border.width: tw.dragging ? 2 : 1
                                    border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, tw.dragging ? 0.9 : 0.55)
                                }
                                Kirigami.Icon {   // grab handle
                                    visible: root.editing
                                    anchors.left: parent.left; anchors.leftMargin: 2; anchors.verticalCenter: parent.verticalCenter
                                    width: 14; height: 14
                                    source: "handle-sort"; isMask: true; color: Kirigami.Theme.textColor; opacity: 0.55
                                }
                                Row {   // size toggle + remove
                                    id: editControls
                                    visible: root.editing
                                    anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 3
                                    spacing: 0
                                    SmallButton { icon: tw.wide ? "view-restore" : "view-fullscreen"; iconSize: 13; implicitWidth: 24; implicitHeight: 24; tip: tw.wide ? "Make small (half a row)" : "Make wide (a full row)"; onClicked: root.saveTiles(Status.toggleTileSize(root.tiles, tw.tileId)) }
                                    SmallButton { icon: "dialog-close"; iconSize: 13; implicitWidth: 24; implicitHeight: 24; tip: "Remove from the pane"; onClicked: root.saveTiles(Status.setTileEnabled(root.tiles, tw.tileId, false)) }
                                }
                            }
                        }
                    }

                    Flow {   // edit mode: the removed tiles as "+ name" chips
                        id: addFlow
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: tilesArea.bottom; anchors.topMargin: 8
                        visible: root.editing && root.hiddenTiles.length > 0
                        spacing: 6
                        Repeater {
                            model: root.hiddenTiles.length
                            delegate: Rectangle {
                                required property int index
                                readonly property var tile: root.hiddenTiles[index]
                                readonly property var def: Status.tileDef(tile.id)
                                width: chipText.implicitWidth + 30; height: 28; radius: 12
                                color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, chipArea.containsMouse ? 0.16 : 0.08)
                                Kirigami.Icon { anchors.left: parent.left; anchors.leftMargin: 8; anchors.verticalCenter: parent.verticalCenter; width: 12; height: 12; source: "list-add"; isMask: true; color: Kirigami.Theme.textColor }
                                Text { id: chipText; anchors.left: parent.left; anchors.leftMargin: 22; anchors.verticalCenter: parent.verticalCenter; text: parent.def ? parent.def.title : parent.tile.id; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 12 }
                                MouseArea { id: chipArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.saveTiles(Status.setTileEnabled(root.tiles, parent.tile.id, true)) }
                                Accessible.role: Accessible.Button
                                Accessible.name: "Add " + (def ? def.title : tile.id)
                            }
                        }
                    }

                    RowLayout {   // footer
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        height: root.footerH
                        spacing: 4
                        SmallButton { icon: "settings-configure"; iconSize: 18; tip: "System Settings"; onClicked: root.launch("systemsettings") }
                        Text { text: "System Settings"; color: Kirigami.Theme.textColor; opacity: 0.7; font.family: "Inter"; font.pixelSize: 12 }
                        Item { Layout.fillWidth: true }
                        Text { text: "Bar: " + root.barSize.charAt(0).toUpperCase() + root.barSize.slice(1); color: Kirigami.Theme.textColor; opacity: 0.55; font.family: "Inter"; font.pixelSize: 11 }
                        SmallButton { icon: "arrow-right"; tip: "Configure the bar (size, tiles, magnify)"; onClicked: { root.closePane(); Plasmoid.internalAction("configure").trigger() } }
                    }
                }

                // ================================================ notification history (28 gridUnits wide, rows 56 px, up to 60 % of the screen)
                ColumnLayout {
                    id: notifPane
                    anchors.fill: parent
                    anchors.margins: root.cardPad
                    spacing: 6
                    visible: opacity > 0
                    opacity: root.paneContent === "notifications" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 160 } }

                    RowLayout {
                        Layout.fillWidth: true; Layout.preferredHeight: 40; Layout.leftMargin: 8; spacing: 6
                        Text { text: "Notifications"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold }
                        Text { visible: history.count > 0; text: history.count; color: Kirigami.Theme.textColor; opacity: 0.5; font.family: "Inter"; font.pixelSize: 13 }
                        Item { Layout.fillWidth: true }
                        SmallButton { icon: root.dnd ? "notifications-disabled" : "notifications"; iconSize: 18; implicitWidth: 32; implicitHeight: 32; tip: root.dnd ? "Turn Do Not Disturb off" : "Do Not Disturb"; onClicked: root.toggleDnd() }
                        Rectangle {   // "Clear all": a labelled pill, reachable at any pane width
                            id: clearAll
                            Layout.preferredWidth: clearText.implicitWidth + 34; Layout.preferredHeight: 30
                            radius: 12
                            readonly property bool active: history.count > 0
                            opacity: active ? 1 : 0.45
                            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, clearArea.containsMouse && active ? 0.16 : 0.08)
                            Kirigami.Icon { anchors.left: parent.left; anchors.leftMargin: 9; anchors.verticalCenter: parent.verticalCenter; width: 14; height: 14; source: "edit-clear-all"; isMask: true; color: Kirigami.Theme.textColor }
                            Text { id: clearText; anchors.left: parent.left; anchors.leftMargin: 26; anchors.verticalCenter: parent.verticalCenter; text: "Clear all"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 12; font.weight: Font.Medium }
                            MouseArea { id: clearArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: if (clearAll.active) history.clear(NotificationManager.Notifications.ClearExpired) }
                            Accessible.role: Accessible.Button
                            Accessible.name: "Clear all notifications"
                        }
                        SmallButton { icon: "settings-configure"; iconSize: 18; implicitWidth: 32; implicitHeight: 32; tip: "Notification settings"; onClicked: root.launch("kcmshell6 kcm_notifications") }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: 40; radius: 14
                        visible: root.dnd
                        color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.18)
                        Text { anchors.centerIn: parent; text: "Do Not Disturb is on — popups are held here"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 13 }
                    }
                    ListView {
                        id: notifList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: history.count > 0
                        clip: true
                        model: history
                        spacing: 2
                        boundsBehavior: Flickable.StopAtBounds
                        delegate: NotificationRow { history: notifList.model }   // NOT `history: history`: inside the delegate that name is its own property
                        add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 200 } }
                        QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: notifList.contentHeight > notifList.height ? QQC2.ScrollBar.AlwaysOn : QQC2.ScrollBar.AsNeeded }
                    }
                    Text {
                        visible: history.count === 0
                        Layout.fillWidth: true; Layout.fillHeight: true
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: "No notifications"
                        color: Kirigami.Theme.textColor; opacity: 0.55; font.family: "Inter"; font.pixelSize: 13
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- tile components (chosen per id by the delegate's Loader)
    Component {   // generic toggle tile: Wi-Fi, Bluetooth, Notifications, Do Not Disturb, Power profile, Night light, Screenshot, Settings
        id: toggleTile
        Tile {
            property string tileId: ""
            property bool wide: true
            property bool available: true
            readonly property var st: root.st
            icon: tileId === "wifi" ? Status.wifiIcon(st) : tileId === "bluetooth" ? Status.bluetoothIcon(st) : tileId === "notifications" ? "notifications"
                : tileId === "dnd" ? "notifications-disabled" : tileId === "powerprofile" ? Status.profileIcon(st.profile) : tileId === "nightlight" ? "redshift-status-off"
                : tileId === "screenshot" ? "camera-photo-symbolic" : "settings-configure"
            title: Status.tileDef(tileId) ? Status.tileDef(tileId).title : tileId
            detail: tileId === "wifi" ? Status.wifiLine(st) : tileId === "bluetooth" ? Status.bluetoothLine(st)
                : tileId === "notifications" ? (root.unread > 0 ? root.unread + " new" : (history.count > 0 ? history.count + " in history" : "None"))
                : tileId === "dnd" ? (root.dnd ? "On until turned off" : "Off")
                : tileId === "powerprofile" ? (Status.profileLabel(st.profile) || "Unavailable")
                : tileId === "nightlight" ? Status.nightLine(st, false)
                : tileId === "screenshot" ? "Capture the screen" : "All settings"
            on: tileId === "wifi" ? st.wifiRadio === true : tileId === "bluetooth" ? (st.btPresent && st.btPowered === true) : tileId === "dnd" ? root.dnd
              : tileId === "nightlight" ? st.nightEnabled === true : false
            actionEnabled: available && (tileId === "wifi" ? st.wifiRadio !== null : tileId === "bluetooth" ? st.btPresent : tileId === "powerprofile" ? st.profile.length > 0
                         : tileId === "nightlight" ? st.nightEnabled !== null : true)
            expandable: tileId === "wifi" || tileId === "bluetooth" || tileId === "powerprofile" || tileId === "nightlight"
            onToggled: {
                if (tileId === "wifi") root.toggleWifi()
                else if (tileId === "bluetooth") root.toggleBluetooth()
                else if (tileId === "notifications") root.openPane("notifications")
                else if (tileId === "dnd") root.toggleDnd()
                else if (tileId === "powerprofile") root.setProfile(Status.nextProfile(st.profile))
                else if (tileId === "nightlight") root.toggleNight()
                else if (tileId === "screenshot") { root.closePane(); root.launch("spectacle") }
                else if (tileId === "settings") { root.closePane(); root.launch("systemsettings") }
            }
            onExpand: {
                if (tileId === "wifi") root.launch("plasmawindowed org.kde.plasma.networkmanagement")
                else if (tileId === "bluetooth") root.launch("plasmawindowed org.kde.plasma.bluetooth")
                else if (tileId === "powerprofile") root.launch("plasmawindowed org.kde.plasma.battery")
                else if (tileId === "nightlight") root.launch("kcmshell6 kcm_nightlight")
            }
        }
    }
    Component {   // volume: mute · slider · percent · chevron (Audio devices)
        id: volumeRow
        Rectangle {
            id: volumeTile
            property string tileId: ""
            property bool wide: true
            property bool available: true
            radius: 14
            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 4; spacing: 6
                SmallButton { icon: Status.volumeIcon(root.st.volume, root.st.muted); iconSize: 20; tip: root.st.muted ? "Unmute" : "Mute"; active: root.st.hasAudio; onClicked: root.toggleMute() }
                QQC2.Slider {
                    id: volumeSlider
                    Layout.fillWidth: true
                    from: 0; to: 100; stepSize: 1
                    enabled: root.st.hasAudio
                    value: root.st.volume < 0 ? 0 : root.st.volume
                    onMoved: root.setVolume(value)
                    Accessible.name: "Volume"
                }
                Text { text: root.st.hasAudio ? Math.round(volumeSlider.value) + "%" : "No audio"; color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 12; Layout.preferredWidth: volumeTile.wide ? 46 : 34; horizontalAlignment: Text.AlignRight }
                SmallButton { visible: volumeTile.wide; icon: "arrow-right"; tip: "Audio devices"; onClicked: root.launch("plasmawindowed org.kde.plasma.volume") }
            }
        }
    }
    Component {   // brightness (only with a backlight): glyph · slider · percent · chevron (Display settings)
        id: brightnessRow
        Rectangle {
            id: brightnessTile
            property string tileId: ""
            property bool wide: true
            property bool available: true
            radius: 14
            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 4; spacing: 6
                Kirigami.Icon { source: "video-display-brightness"; isMask: true; color: Kirigami.Theme.textColor; opacity: 0.7; Layout.preferredWidth: 20; Layout.preferredHeight: 20; Layout.leftMargin: 4 }
                QQC2.Slider {
                    id: brightnessSlider
                    Layout.fillWidth: true
                    from: 1; to: 100; stepSize: 1
                    enabled: root.brightnessOk
                    value: Math.max(1, root.brightnessPct)
                    onMoved: root.setBrightness(value)
                    Accessible.name: "Brightness"
                    QQC2.ToolTip.visible: !root.brightnessOk && hovered
                    QQC2.ToolTip.text: "Brightness control is not available (powerdevil not running)"
                }
                Text { text: Math.round(brightnessSlider.value) + "%"; color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 12; Layout.preferredWidth: brightnessTile.wide ? 46 : 34; horizontalAlignment: Text.AlignRight }
                SmallButton { visible: brightnessTile.wide; icon: "arrow-right"; tip: "Display settings"; onClicked: root.launch("plasmawindowed org.kde.plasma.brightness") }
            }
        }
    }
    Component {   // battery: glyph · "Battery" + line · power-profile chips (wide only) · chevron (Battery details)
        id: batteryRow
        Rectangle {
            id: batteryTile
            property string tileId: ""
            property bool wide: true
            property bool available: true
            radius: 14
            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 4; spacing: 10
                Kirigami.Icon { source: Status.batteryIcon(root.st.batPct, root.st.batStatus); isMask: true; color: Kirigami.Theme.textColor; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Text { text: root.st.hasBattery ? "Battery" : "Power"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Text { text: Status.batteryLine(root.st); color: Kirigami.Theme.textColor; opacity: 0.75; font.family: "Inter"; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
                }
                Row {   // power profile chips (powerprofilesctl)
                    spacing: 4
                    visible: batteryTile.wide && root.st.profile.length > 0
                    Repeater {
                        model: [ { id: "power-saver", icon: "battery-profile-powersave", tip: "Power saver" }, { id: "balanced", icon: "battery-profile-balanced", tip: "Balanced" }, { id: "performance", icon: "battery-profile-performance", tip: "Performance" } ]
                        delegate: Rectangle {
                            required property var modelData
                            readonly property bool current: root.st.profile === modelData.id
                            width: 30; height: 30; radius: 12
                            color: current ? Kirigami.Theme.highlightColor : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, chipArea.containsMouse ? 0.16 : 0.0)
                            Behavior on color { ColorAnimation { duration: 140 } }
                            Kirigami.Icon { anchors.centerIn: parent; width: 16; height: 16; source: parent.modelData.icon; isMask: true; color: parent.current ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor }
                            MouseArea { id: chipArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.setProfile(parent.modelData.id) }
                            QQC2.ToolTip.visible: chipArea.containsMouse
                            QQC2.ToolTip.text: modelData.tip
                            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                        }
                    }
                }
                SmallButton { icon: "arrow-right"; tip: "Battery details"; onClicked: root.launch("plasmawindowed org.kde.plasma.battery") }
            }
        }
    }
    Component {   // network speed: glyph · interface · IP · ↓ ↑ (always shown while a link is up)
        id: netRow
        Rectangle {
            id: netTile
            property string tileId: ""
            property bool wide: true
            property bool available: true
            radius: 14
            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                Kirigami.Icon { source: root.st.connType === "wired" ? "network-wired" : "network-wireless-connected"; isMask: true; color: Kirigami.Theme.textColor; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                Text { visible: netTile.wide; Layout.fillWidth: true; elide: Text.ElideRight; color: Kirigami.Theme.textColor; opacity: 0.85; font.family: "Inter"; font.pixelSize: 12
                       text: root.st.iface ? root.st.iface + (root.st.ip4 ? " · " + root.st.ip4 : "") : "No network" }
                Text { Layout.fillWidth: !netTile.wide; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 12; font.weight: Font.Medium; elide: Text.ElideRight
                       text: root.st.iface ? Status.speedText(root.down, root.up) : "No network" }
            }
        }
    }
}
