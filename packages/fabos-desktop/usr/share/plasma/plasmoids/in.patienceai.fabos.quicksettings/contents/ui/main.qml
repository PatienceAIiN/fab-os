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
// The pane (v3): a dropdown card 36 gridUnits wide at the Medium bar size (32 / 40 at Small / Large), top edge flush
// with the bar, radius 24 at the bottom corners, aligned to the bar's right edge with a 12 px margin (the transparent
// dialog window is clamped to the screen edge; the card sits `edge` px inside it). Padding 20, a THREE-column tile grid
// with 12 px gaps (status.js layoutTiles): Wi-Fi (2 cols) + Bluetooth · volume slider (full width) · brightness slider ·
// Battery card (2 cols: percentage 28/700, state, time, power-profile segmented control) + Do Not Disturb · Night light ·
// Screenshot · Settings; footer: the network row (interface · IP · live ↓ ↑) and Edit (pencil) + All settings. Inter:
// titles 15/600, detail 13, big numbers 28/700. Tiles radius 16, 4 % tint, accent when on, 120 ms hover lift; every
// colour from Kirigami.Theme. The notification history is the same width with 64 px rows and 14 px body text.
//
// No-blink slide-down: the dialog window is TRANSPARENT (backgroundHints NoBackground) and opens at its FINAL size at
// once; only the card inside it moves — y from -0.35 × height to 0 and opacity 0 -> 1 in 220 ms OutCubic, the tiles
// staggering in behind it (30 ms apart, 160 ms, 8 px rise + fade); closing reverses in 160 ms and hides the window when
// the animation has ended. A window that changes size while it animates is re-rasterised by the compositor on every
// frame (the "blink"); this one never changes size while anything moves (the harness samples the window size every
// 11 ms through the open, the stagger and the close). The rounded card is drawn here: background colour @ 96 %,
// hairline, a soft shadow from a second translucent rectangle. Switching settings <-> notifications while open is one
// instant resize plus a 160 ms cross-fade.
//
// Tiles: Plasmoid.configuration.tilesJson (status.js TILES / parseTiles / layoutTiles) — every tile has an order, a
// size (small = one column, medium = two, wide = the full row) and enabled; the pencil toggles edit mode: drag to
// reorder (DragHandler, slot from layoutTiles + tileAt), size cycle, remove, "+ tile" chips for the removed ones, reset.
// The same model is edited on the "Tiles" settings page.
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
        if (id === "battery") return st.hasBattery || st.profile.length > 0     // a desktop with power profiles keeps the card as "Power"
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
    // ---- geometry: 36 gridUnits wide at Medium (about 650 px), 32 / 40 at Small / Large; padding 20; three columns, 12 px gaps
    readonly property int paneUnits: root.barSize === "small" ? 32 : (root.barSize === "large" ? 40 : 36)
    readonly property int settingsWidth: Kirigami.Units.gridUnit * root.paneUnits
    readonly property int notifWidth: root.settingsWidth                     // the notification pane is the same width
    readonly property int cardPad: 20
    readonly property int tileGap: 12
    readonly property int contentWidth: root.settingsWidth - 2 * root.cardPad
    readonly property var tileLayout: Status.layoutTiles(root.shownTiles, root.contentWidth, root.tileGap)
    function tileRect(id) { return Status.rectFor(root.tileLayout, id) }
    function tileSize(id) { for (var i = 0; i < root.tiles.length; i++) if (root.tiles[i].id === id) return root.tiles[i].size; return "small" }
    function reorderTo(id, targetId) { if (id !== targetId) root.tiles = Status.moveTileTo(root.tiles, id, targetId) }   // live while dragging
    function dropTiles() { root.saveTiles(root.tiles) }                                                                  // persisted at release
    readonly property int footerH: 44
    readonly property int footerGap: 16
    readonly property int addRowH: root.editing && root.hiddenTiles.length > 0 ? addFlow.implicitHeight + 12 : 0
    readonly property int settingsHeight: 2 * root.cardPad + root.tileLayout.height + root.addRowH + root.footerGap + root.footerH
    // notifications: header 44, a 44 px banner while Do Not Disturb, rows (64 px minimum) up to 60 % of the screen, then a scroll
    readonly property int notifHeaderH: 44
    readonly property int notifBannerH: root.dnd ? 44 + 8 : 0
    readonly property int notifMaxList: Math.round(root.screenH * 0.6) - (2 * root.cardPad + root.notifHeaderH + 8 + root.notifBannerH)
    readonly property int notifListH: history.count === 0 ? 96 : Math.max(64, Math.min(Math.round(notifList.contentHeight), root.notifMaxList))
    readonly property int notifHeight: 2 * root.cardPad + root.notifHeaderH + 8 + root.notifBannerH + root.notifListH

    // ---------------------------------------------------------------- pane open / close (imperative: the dialog's
    // visibility is set here and in closeTimer only, so the slide-up can finish before the window hides)
    signal tilesIntro()                        // the tiles stagger in (30 ms apart) when the settings pane opens
    function openPane(mode) {
        closeTimer.stop(); root.closing = false
        var fromClosed = root.paneMode === "closed", wasContent = root.paneContent
        if (fromClosed) { openBehavior.enabled = false; pane.openProgress = 0; openBehavior.enabled = true }
        root.paneMode = mode
        root.paneContent = mode
        pane.visible = true              // at its final size: mainItem is bound to the card's size, which does not change while the card moves
        pane.openProgress = 1
        if (mode === "settings" && (fromClosed || wasContent !== "settings")) root.tilesIntro()
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
    Timer { id: closeTimer; interval: 170; onTriggered: { root.closing = false; root.paneMode = "closed"; pane.visible = false } }   // = the 160 ms slide-up + one frame

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
        Behavior on openProgress { id: openBehavior; NumberAnimation { duration: root.closing ? 160 : 220; easing.type: Easing.OutCubic } }
        readonly property int shadow: 14                                     // room for the shadow at the left of and under the card
        readonly property int edge: 12                                       // the card's right edge sits 12 px inside the window: the window is clamped to the bar's right (screen) edge
        readonly property bool notif: root.paneContent === "notifications"
        readonly property int cardWidth: notif ? root.notifWidth : root.settingsWidth
        readonly property int cardHeight: notif ? root.notifHeight : root.settingsHeight
        readonly property int radius: 24
        readonly property Item cardItem: card

        mainItem: Item {
            id: paneMain
            Kirigami.Theme.colorSet: Kirigami.Theme.Window
            Kirigami.Theme.inherit: false
            width: pane.cardWidth + pane.shadow + pane.edge
            height: pane.cardHeight + pane.shadow
            clip: true                                                        // the card slides in from above the window's top edge

            Rectangle {   // soft shadow: a second translucent rectangle a little larger and lower than the card
                x: card.x - 2; y: card.y + 3
                width: card.width + 4; height: card.height + 2
                bottomLeftRadius: pane.radius + 2; bottomRightRadius: pane.radius + 2
                color: Qt.rgba(0, 0, 0, 0.16)
                opacity: card.opacity
            }
            Rectangle {   // the card itself: y -0.35 × height -> 0 and opacity 0 -> 1; the window around it never changes size while it moves
                id: card
                x: pane.shadow
                y: Math.round(-0.35 * height * (1 - pane.openProgress))
                width: pane.cardWidth
                height: pane.cardHeight
                opacity: pane.openProgress
                color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g, Kirigami.Theme.backgroundColor.b, 0.96)
                border.width: 1
                border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
                topLeftRadius: 0; topRightRadius: 0
                bottomLeftRadius: pane.radius; bottomRightRadius: pane.radius
                readonly property color tc: Kirigami.Theme.textColor

                // ================================================ settings: tiles · (+ chips) · footer
                Item {
                    id: settingsPane
                    anchors.fill: parent
                    anchors.margins: root.cardPad
                    visible: opacity > 0
                    opacity: root.paneContent === "settings" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 160 } }
                    readonly property int contentHeight: root.settingsHeight
                    readonly property Item footerItem: footer      // harness hooks
                    readonly property Item chips: addFlow

                    Item {   // the tiles, positioned from status.js layoutTiles; one delegate per known tile, shown when it has a slot
                        id: tilesArea
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        height: root.tileLayout.height
                        Repeater {
                            id: tileRepeater
                            model: Status.TILES.length            // fixed: a reorder never re-creates a delegate (a drag would die with it)
                            delegate: Item {
                                id: tw
                                required property int index
                                readonly property string tileId: Status.TILES[index].id
                                readonly property var rect: root.tileRect(tileId)
                                readonly property string size: root.tileSize(tileId)
                                readonly property int span: rect ? rect.span : Status.spanOf(size)
                                readonly property int order: rect ? rect.n : 0         // position among the placed tiles: the stagger index
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
                                opacity: (available || !root.editing ? 1 : 0.5) * tw.introOpacity
                                transform: Translate { y: tw.introDy }              // the stagger rise: a transform, so x/y stay the layout's
                                Behavior on x { enabled: root.editing && !tw.dragging; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                Behavior on y { enabled: root.editing && !tw.dragging; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                Behavior on width { enabled: root.editing; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                Behavior on height { enabled: root.editing; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                // stagger-in when the pane opens: 30 ms per tile, then 160 ms of 8 px rise + fade (OutCubic)
                                property real introDy: 0
                                property real introOpacity: 1
                                SequentialAnimation {
                                    id: intro
                                    PauseAnimation { duration: tw.order * 30 }
                                    ParallelAnimation {
                                        NumberAnimation { target: tw; property: "introDy"; to: 0; duration: 160; easing.type: Easing.OutCubic }
                                        NumberAnimation { target: tw; property: "introOpacity"; to: 1; duration: 160; easing.type: Easing.OutCubic }
                                    }
                                }
                                function playIntro() { intro.stop(); tw.introDy = 8; tw.introOpacity = 0; intro.restart() }
                                Connections { target: root; function onTilesIntro() { if (tw.rect) tw.playIntro() } }
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
                                    onLoaded: { item.tileId = tw.tileId; item.span = Qt.binding(function () { return tw.span }); item.available = Qt.binding(function () { return tw.available }) }
                                }
                                Rectangle {   // edit-mode frame
                                    id: editFrame
                                    visible: root.editing
                                    anchors.fill: parent
                                    radius: 16
                                    color: "transparent"
                                    border.width: tw.dragging ? 2 : 1
                                    border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, tw.dragging ? 0.9 : 0.55)
                                }
                                Kirigami.Icon {   // grab handle
                                    visible: root.editing
                                    anchors.left: parent.left; anchors.leftMargin: 3; anchors.verticalCenter: parent.verticalCenter
                                    width: 14; height: 14
                                    source: "handle-sort"; isMask: true; color: Kirigami.Theme.textColor; opacity: 0.55
                                }
                                Row {   // size cycle + remove
                                    id: editControls
                                    visible: root.editing
                                    anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 4
                                    spacing: 0
                                    SmallButton { icon: tw.size === "wide" ? "view-restore" : "view-fullscreen"; iconSize: 13; implicitWidth: 24; implicitHeight: 24; tip: Status.sizeLabel(tw.size) + " → " + Status.sizeLabel(Status.nextSize(tw.size)); onClicked: root.saveTiles(Status.toggleTileSize(root.tiles, tw.tileId)) }
                                    SmallButton { icon: "dialog-close"; iconSize: 13; implicitWidth: 24; implicitHeight: 24; tip: "Remove from the pane"; onClicked: root.saveTiles(Status.setTileEnabled(root.tiles, tw.tileId, false)) }
                                }
                            }
                        }
                    }

                    Flow {   // edit mode: the removed tiles as "+ name" chips
                        id: addFlow
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: tilesArea.bottom; anchors.topMargin: 12
                        visible: root.editing && root.hiddenTiles.length > 0
                        spacing: 8
                        Repeater {
                            model: root.hiddenTiles.length
                            delegate: Rectangle {
                                required property int index
                                readonly property var tile: root.hiddenTiles[index]
                                readonly property var def: Status.tileDef(tile.id)
                                width: chipText.implicitWidth + 34; height: 32; radius: 12
                                color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, chipArea.containsMouse ? 0.14 : 0.06)
                                Kirigami.Icon { anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter; width: 12; height: 12; source: "list-add"; isMask: true; color: Kirigami.Theme.textColor }
                                Text { id: chipText; anchors.left: parent.left; anchors.leftMargin: 25; anchors.verticalCenter: parent.verticalCenter; text: parent.def ? parent.def.title : parent.tile.id; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium }
                                MouseArea { id: chipArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.saveTiles(Status.setTileEnabled(root.tiles, parent.tile.id, true)) }
                                Accessible.role: Accessible.Button
                                Accessible.name: "Add " + (def ? def.title : tile.id)
                            }
                        }
                    }

                    RowLayout {   // footer: the network row (interface · IP · live ↓ ↑) and Edit (pencil) + All settings; while editing: hint · reset · settings · Done
                        id: footer
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        height: root.footerH
                        spacing: 8
                        readonly property Item networkText: netText   // harness hooks
                        readonly property Item speedLabel: speedText
                        readonly property Item pill: footerPill
                        readonly property Item pencil: editButton
                        Kirigami.Icon {
                            visible: !root.editing
                            Layout.leftMargin: 8; Layout.preferredWidth: 18; Layout.preferredHeight: 18
                            source: root.st.connType === "wired" ? "network-wired" : (root.st.iface ? Status.wifiIcon(root.st) : "network-wireless-disconnected")
                            isMask: true; color: card.tc; opacity: 0.8
                        }
                        Text {
                            id: netText
                            visible: !root.editing
                            text: root.st.iface ? root.st.iface + (root.st.ip4 ? " · " + root.st.ip4 : "") : "No network"
                            color: card.tc; opacity: 0.8; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium
                            elide: Text.ElideRight; Layout.maximumWidth: 260
                        }
                        Text {
                            id: speedText
                            visible: !root.editing && root.st.iface !== ""
                            text: Status.speedText(root.down, root.up)
                            color: card.tc; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.DemiBold
                        }
                        Text { visible: root.editing; Layout.leftMargin: 8; text: "Drag to arrange · size · remove"; color: card.tc; opacity: 0.6; font.family: "Inter"; font.pixelSize: 13 }
                        Item { Layout.fillWidth: true }
                        SmallButton { visible: root.editing; icon: "edit-undo"; iconSize: 18; implicitWidth: 36; implicitHeight: 36; tip: "Reset to default"; onClicked: root.saveTiles(Status.defaultTiles()) }
                        SmallButton { visible: root.editing; icon: "configure"; iconSize: 18; implicitWidth: 36; implicitHeight: 36; tip: "Bar size, tiles and refresh settings"; onClicked: { root.closePane(); Plasmoid.internalAction("configure").trigger() } }
                        SmallButton { id: editButton; visible: !root.editing; icon: "document-edit"; iconSize: 18; implicitWidth: 36; implicitHeight: 36; tip: "Edit tiles: drag to arrange, resize, remove"; onClicked: root.editing = true }
                        Rectangle {   // "All settings" (System Settings) at rest; "Done" (accent) while editing
                            id: footerPill
                            Layout.preferredWidth: pillText.implicitWidth + 46; Layout.preferredHeight: 36
                            radius: 12
                            readonly property color fg: root.editing ? Kirigami.Theme.highlightedTextColor : card.tc
                            color: root.editing ? (pillArea.containsMouse ? Qt.lighter(Kirigami.Theme.highlightColor, 1.08) : Kirigami.Theme.highlightColor)
                                                : Qt.rgba(card.tc.r, card.tc.g, card.tc.b, pillArea.containsMouse ? 0.12 : 0.06)
                            Behavior on color { ColorAnimation { duration: 140 } }
                            Kirigami.Icon { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; source: root.editing ? "checkmark" : "settings-configure"; isMask: true; color: footerPill.fg }
                            Text { id: pillText; anchors.left: parent.left; anchors.leftMargin: 36; anchors.verticalCenter: parent.verticalCenter; text: root.editing ? "Done" : "All settings"; color: footerPill.fg; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium }
                            MouseArea { id: pillArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: { if (root.editing) root.editing = false; else { root.closePane(); root.launch("systemsettings") } } }
                            Accessible.role: Accessible.Button
                            Accessible.name: pillText.text
                        }
                    }
                }

                // ================================================ notification history (the pane's width, rows 64 px, up to 60 % of the screen)
                ColumnLayout {
                    id: notifPane
                    anchors.fill: parent
                    anchors.margins: root.cardPad
                    spacing: 8
                    visible: opacity > 0
                    opacity: root.paneContent === "notifications" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 160 } }

                    RowLayout {
                        Layout.fillWidth: true; Layout.preferredHeight: root.notifHeaderH; Layout.leftMargin: 6; spacing: 8
                        Text { text: "Notifications"; color: card.tc; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold }
                        Text { visible: history.count > 0; text: history.count; color: card.tc; opacity: 0.5; font.family: "Inter"; font.pixelSize: 13 }
                        Item { Layout.fillWidth: true }
                        SmallButton { icon: root.dnd ? "notifications-disabled" : "notifications"; iconSize: 18; implicitWidth: 36; implicitHeight: 36; tip: root.dnd ? "Turn Do Not Disturb off" : "Do Not Disturb"; onClicked: root.toggleDnd() }
                        Rectangle {   // "Clear all": a labelled pill, reachable at any pane width
                            id: clearAll
                            Layout.preferredWidth: clearText.implicitWidth + 40; Layout.preferredHeight: 36
                            radius: 12
                            readonly property bool active: history.count > 0
                            opacity: active ? 1 : 0.45
                            color: Qt.rgba(card.tc.r, card.tc.g, card.tc.b, clearArea.containsMouse && active ? 0.12 : 0.06)
                            Kirigami.Icon { anchors.left: parent.left; anchors.leftMargin: 11; anchors.verticalCenter: parent.verticalCenter; width: 16; height: 16; source: "edit-clear-all"; isMask: true; color: card.tc }
                            Text { id: clearText; anchors.left: parent.left; anchors.leftMargin: 31; anchors.verticalCenter: parent.verticalCenter; text: "Clear all"; color: card.tc; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium }
                            MouseArea { id: clearArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: if (clearAll.active) history.clear(NotificationManager.Notifications.ClearExpired) }
                            Accessible.role: Accessible.Button
                            Accessible.name: "Clear all notifications"
                        }
                        SmallButton { icon: "settings-configure"; iconSize: 18; implicitWidth: 36; implicitHeight: 36; tip: "Notification settings"; onClicked: root.launch("kcmshell6 kcm_notifications") }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: 44; radius: 14
                        visible: root.dnd
                        color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.18)
                        Text { anchors.centerIn: parent; text: "Do Not Disturb is on — popups are held here"; color: card.tc; font.family: "Inter"; font.pixelSize: 14 }
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
                    Item {   // empty state
                        visible: history.count === 0
                        Layout.fillWidth: true; Layout.fillHeight: true
                        Column {
                            anchors.centerIn: parent; spacing: 6
                            Kirigami.Icon { anchors.horizontalCenter: parent.horizontalCenter; width: 28; height: 28; source: "notifications"; isMask: true; color: card.tc; opacity: 0.35 }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "No notifications"; color: card.tc; opacity: 0.55; font.family: "Inter"; font.pixelSize: 14 }
                        }
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- tile components (chosen per id by the delegate's Loader)
    Component {   // generic toggle tile: Wi-Fi, Bluetooth, Do Not Disturb, Night light, Screenshot, Settings, Notifications, Power profile
        id: toggleTile
        Tile {
            property string tileId: ""
            property int span: 1
            property bool available: true
            readonly property var st: root.st
            icon: tileId === "wifi" ? Status.wifiIcon(st) : tileId === "bluetooth" ? Status.bluetoothIcon(st) : tileId === "notifications" ? "notifications"
                : tileId === "dnd" ? "notifications-disabled" : tileId === "powerprofile" ? Status.profileIcon(st.profile) : tileId === "nightlight" ? "redshift-status-off"
                : tileId === "screenshot" ? "camera-photo-symbolic" : "settings-configure"
            title: Status.tileDef(tileId) ? Status.tileDef(tileId).title : tileId
            detail: tileId === "wifi" ? Status.wifiLine(st) : tileId === "bluetooth" ? (span > 1 ? Status.bluetoothLine(st) : Status.bluetoothShort(st))
                : tileId === "notifications" ? (root.unread > 0 ? root.unread + " new" : (history.count > 0 ? history.count + " in history" : "None"))
                : tileId === "dnd" ? (root.dnd ? "On until turned off" : "Off")
                : tileId === "powerprofile" ? (Status.profileLabel(st.profile) || "Unavailable")
                : tileId === "nightlight" ? Status.nightLine(st, false)
                : tileId === "screenshot" ? "Capture the screen" : "System Settings"
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
    Component {   // volume: mute glyph · slider (36 px thumb) · percent · chevron (Audio devices)
        id: volumeRow
        SliderTile {
            property string tileId: ""
            property int span: 3
            property bool available: true
            icon: Status.volumeIcon(root.st.volume, root.st.muted)
            glyphClickable: true
            glyphTip: root.st.muted ? "Unmute" : "Mute"
            sliderEnabled: root.st.hasAudio
            value: root.st.volume < 0 ? 0 : root.st.volume
            valueText: root.st.hasAudio ? Math.round(sliderItem.value) + "%" : "—"
            chevronTip: "Audio devices"
            expandable: span > 1
            onMoved: (v) => root.setVolume(v)
            onGlyphClicked: root.toggleMute()
            onExpand: root.launch("plasmawindowed org.kde.plasma.volume")
        }
    }
    Component {   // brightness (only with a backlight): glyph · slider · percent · chevron (Display settings)
        id: brightnessRow
        SliderTile {
            property string tileId: ""
            property int span: 3
            property bool available: true
            icon: "video-display-brightness"
            glyphTip: "Brightness"
            from: 1
            sliderEnabled: root.brightnessOk
            value: Math.max(1, root.brightnessPct)
            valueText: Math.round(sliderItem.value) + "%"
            disabledTip: "Brightness control is not available (powerdevil not running)"
            chevronTip: "Display settings"
            expandable: span > 1
            onMoved: (v) => root.setBrightness(v)
            onExpand: root.launch("plasmawindowed org.kde.plasma.brightness")
        }
    }
    Component {   // battery card: percentage 28/700 · state · time · power-profile segmented control · chevron (Battery details)
        id: batteryRow
        BatteryCard {
            property string tileId: ""
            property int span: 2
            st: root.st
            onSetProfile: (p) => root.setProfile(p)
            onExpand: root.launch("plasmawindowed org.kde.plasma.battery")
        }
    }
    Component {   // network speed tile (optional; the footer carries the same line): glyph · interface · IP · ↓ ↑
        id: netRow
        Rectangle {
            id: netTile
            property string tileId: ""
            property int span: 3
            property bool available: true
            radius: 16
            readonly property color tc: Kirigami.Theme.textColor
            color: Qt.rgba(tc.r, tc.g, tc.b, 0.04)
            border.width: 1; border.color: Qt.rgba(tc.r, tc.g, tc.b, 0.06)
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 10
                Kirigami.Icon { source: root.st.connType === "wired" ? "network-wired" : "network-wireless-connected"; isMask: true; color: netTile.tc; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                Text { visible: netTile.span > 1; Layout.fillWidth: true; elide: Text.ElideRight; color: netTile.tc; opacity: 0.85; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium
                       text: root.st.iface ? root.st.iface + (root.st.ip4 ? " · " + root.st.ip4 : "") : "No network" }
                Text { Layout.fillWidth: netTile.span === 1; color: netTile.tc; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight
                       text: root.st.iface ? Status.speedText(root.down, root.up) : "No network" }
            }
        }
    }
}
