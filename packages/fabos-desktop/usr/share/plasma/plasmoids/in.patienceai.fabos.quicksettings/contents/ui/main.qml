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

// Fab OS quick settings (top bar). One compact indicator group — network glyph with live ↓/↑ speed, Bluetooth,
// volume (while muted / just changed), battery glyph + percentage, bell with unread badge — and one PlasmaCore.Dialog
// that slides down from the bar (height 0 -> content, 240 ms OutCubic; the FabOS dialog background rounds the bottom
// corners at radius 24) holding either the settings tiles or the notification history.
//
// Data: contents/code/status.sh through the Plasma5Support executable engine every `pollSeconds` (default 10 s) and
// right after each action; /proc/net/{route,dev} every 2 s for the rate. Actions: nmcli radio wifi, bluetoothctl
// power, wpctl set-volume/set-mute, powerprofilesctl set, powerdevil's ScreenBrightness D-Bus (BrightnessBridge.qml).
// Notifications + Do Not Disturb: org.kde.notificationmanager (the model the stock history uses; same process, same
// server). The stock applets stay reachable: each tile's chevron opens `plasmawindowed <applet>`.
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
    readonly property string magnification: Plasmoid.configuration.magnification
    readonly property int glyph: barSize === "small" ? 16 : (barSize === "large" ? 22 : 18)
    readonly property int textPx: barSize === "small" ? 11 : (barSize === "large" ? 13 : 12)
    readonly property int clockPx: Status.clockSizeFor(barSize)
    onBarSizeChanged: syncTimer.restart()
    onMagnifyChanged: syncTimer.restart()
    onMagnificationChanged: syncTimer.restart()
    // The clock is a separate applet and Plasmoid.configuration of another applet is not writable from QML, so the new
    // size (and the magnify settings for the dock) go through plasmashell's own scripting API on D-Bus — the mechanism
    // /usr/lib/fabos/tray-defaults already uses at login.
    Timer { id: syncTimer; interval: 300; onTriggered: root.run(Status.syncCommand(root.barSize, root.magnify, root.magnification)) }
    property string lastSync: ""

    // ---------------------------------------------------------------- state
    property var st: Status.empty()
    property real down: 0
    property real up: 0
    property var netPrev: null
    property real netPrevAt: 0
    property int zeroSamples: 99
    readonly property bool speedVisible: Plasmoid.configuration.showSpeed && st.iface !== "" && zeroSamples < 5
    property bool volumeFlash: false
    property string paneMode: "closed"       // closed | settings | notifications
    property bool closing: false
    property bool dnd: false
    property bool paneAutoHide: true        // harness sets false (offscreen windows never activate)
    property bool autoRefresh: true         // re-probe status.sh after each action (harness sets false)
    readonly property string statusScript: Qt.resolvedUrl("../code/status.sh").toString().replace(/^file:\/\//, "")
    readonly property string statusCmd: "sh " + root.statusScript
    readonly property string netCmd: "cat /proc/net/route; echo ---; cat /proc/net/dev"
    readonly property int unread: history.unreadNotificationsCount

    // ---------------------------------------------------------------- data sources
    P5Support.DataSource {
        id: poll
        engine: "executable"
        interval: Math.max(5, Plasmoid.configuration.pollSeconds) * 1000
        onNewData: (source, data) => { if (source === root.statusCmd) root.applyStatus(String(data["stdout"] || "")) }
        Component.onCompleted: connectSource(root.statusCmd)
    }
    P5Support.DataSource {
        id: netPoll
        engine: "executable"
        interval: 2000
        connectedSources: [root.netCmd]
        onNewData: (source, data) => root.applyNet(String(data["stdout"] || ""), Date.now())
    }
    P5Support.DataSource {   // one-shot actions; the status is re-read 400 ms after each finishes
        id: actions
        engine: "executable"
        onNewData: (source, data) => { disconnectSource(source); refreshTimer.restart() }
    }
    Timer { id: refreshTimer; interval: 400; onTriggered: if (root.autoRefresh) root.refresh() }
    function run(cmd) { root.lastSync = cmd.indexOf("evaluateScript") >= 0 ? cmd : root.lastSync; actions.connectSource(cmd) }
    function launch(cmd) { root.run(Status.detach(cmd)) }
    function refresh() { poll.disconnectSource(root.statusCmd); poll.connectSource(root.statusCmd) }

    function applyStatus(text) {
        var s = Status.parseStatus(text)
        if (root.st.hasAudio && s.hasAudio && (s.volume !== root.st.volume || s.muted !== root.st.muted)) root.flashVolume()
        root.st = s
        root.refreshDnd()
    }
    function applyNet(text, now) {
        var cur = Status.parseNet(text)
        if (root.netPrev) {
            var r = Status.rates(root.netPrev, cur, now - root.netPrevAt)
            root.down = r.down; root.up = r.up
            root.zeroSamples = (r.down < 1 && r.up < 1) ? Math.min(root.zeroSamples + 1, 99) : 0
        }
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
    Component.onCompleted: root.refreshDnd()

    // ---------------------------------------------------------------- pane open / close
    function openPane(mode) {
        closeTimer.stop(); root.closing = false
        if (root.paneMode === "closed") { openBehavior.enabled = false; pane.openProgress = 0; openBehavior.enabled = true }
        root.paneMode = mode
        pane.visible = true
        pane.openProgress = 1
        if (mode === "notifications") history.lastRead = new Date()
        root.refresh()
    }
    function closePane() {
        if (root.paneMode === "closed") return
        history.lastRead = new Date()
        root.closing = true; pane.openProgress = 0; closeTimer.restart()
    }
    function togglePane(mode) { if (root.paneMode === mode && !root.closing) root.closePane(); else root.openPane(mode) }
    Timer { id: closeTimer; interval: 260; onTriggered: { root.closing = false; root.paneMode = "closed"; pane.visible = false } }

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

        Indicator {   // network + live speed
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

    // ---------------------------------------------------------------- slide-down pane
    PlasmaCore.Dialog {
        id: pane
        visualParent: bar
        location: Plasmoid.location
        type: PlasmaCore.Dialog.AppletPopup
        hideOnWindowDeactivate: root.paneAutoHide
        backgroundHints: PlasmaCore.Dialog.StandardBackground
        visible: root.paneMode !== "closed"
        onVisibleChanged: if (!visible && root.paneMode !== "closed" && !root.closing) { history.lastRead = new Date(); root.paneMode = "closed" }

        property real openProgress: 0
        Behavior on openProgress { id: openBehavior; NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
        readonly property real contentTarget: root.paneMode === "notifications" ? notifPane.implicitHeight : settingsPane.implicitHeight
        property real paneHeight: contentTarget
        Behavior on paneHeight { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
        readonly property int paneWidth: Kirigami.Units.gridUnit * 21

        mainItem: Item {
            id: paneMain
            Kirigami.Theme.colorSet: Kirigami.Theme.Window
            Kirigami.Theme.inherit: false
            width: pane.paneWidth
            height: Math.max(8, Math.round(pane.paneHeight * pane.openProgress))
            clip: true

            // ================================================ settings tiles
            ColumnLayout {
                id: settingsPane
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 6
                spacing: 8
                visible: root.paneMode === "settings"
                opacity: visible ? pane.openProgress : 0

                GridLayout {
                    columns: 2; columnSpacing: 8; rowSpacing: 8
                    Layout.fillWidth: true
                    Tile { Layout.fillWidth: true; icon: Status.wifiIcon(root.st); title: "Wi-Fi"; detail: Status.wifiLine(root.st)
                           on: root.st.wifiRadio === true; actionEnabled: root.st.wifiRadio !== null
                           onToggled: root.toggleWifi(); onExpand: root.launch("plasmawindowed org.kde.plasma.networkmanagement") }
                    Tile { Layout.fillWidth: true; icon: Status.bluetoothIcon(root.st); title: "Bluetooth"; detail: Status.bluetoothLine(root.st)
                           on: root.st.btPresent && root.st.btPowered === true; actionEnabled: root.st.btPresent
                           onToggled: root.toggleBluetooth(); onExpand: root.launch("plasmawindowed org.kde.plasma.bluetooth") }
                }

                // volume
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 48; radius: 14
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
                        Text { text: root.st.hasAudio ? Math.round(volumeSlider.value) + "%" : "No audio"; color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 12; Layout.preferredWidth: 46; horizontalAlignment: Text.AlignRight }
                        SmallButton { icon: "arrow-right"; tip: "Audio devices"; onClicked: root.launch("plasmawindowed org.kde.plasma.volume") }
                    }
                }

                // brightness (only with a backlight)
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 48; radius: 14
                    visible: root.brightnessPct >= 0
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
                        Text { text: Math.round(brightnessSlider.value) + "%"; color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 12; Layout.preferredWidth: 46; horizontalAlignment: Text.AlignRight }
                        SmallButton { icon: "arrow-right"; tip: "Display settings"; onClicked: root.launch("plasmawindowed org.kde.plasma.brightness") }
                    }
                }

                // battery + power profile
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 64; radius: 14
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
                            visible: root.st.profile.length > 0
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

                // network speed row
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 44; radius: 14
                    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                        Kirigami.Icon { source: root.st.connType === "wired" ? "network-wired" : "network-wireless-connected"; isMask: true; color: Kirigami.Theme.textColor; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                        Text { Layout.fillWidth: true; elide: Text.ElideRight; color: Kirigami.Theme.textColor; opacity: 0.85; font.family: "Inter"; font.pixelSize: 12
                               text: root.st.iface ? root.st.iface + (root.st.ip4 ? " · " + root.st.ip4 : "") : "No network" }
                        Text { color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 12; font.weight: Font.Medium
                               text: root.st.iface ? Status.speedText(root.down, root.up) : "" }
                    }
                }

                GridLayout {
                    columns: 2; columnSpacing: 8; rowSpacing: 8
                    Layout.fillWidth: true
                    Tile { Layout.fillWidth: true; icon: "notifications"; title: "Notifications"; expandable: false
                           detail: root.unread > 0 ? root.unread + " new" : (history.count > 0 ? history.count + " in history" : "None")
                           onToggled: root.openPane("notifications") }
                    Tile { Layout.fillWidth: true; icon: "notifications-disabled"; title: "Do Not Disturb"; expandable: false
                           detail: root.dnd ? "On until turned off" : "Off"; on: root.dnd
                           onToggled: root.toggleDnd() }
                }

                RowLayout {   // footer
                    Layout.fillWidth: true; Layout.topMargin: 2; Layout.bottomMargin: 2; spacing: 4
                    SmallButton { icon: "configure"; iconSize: 18; tip: "System Settings"; onClicked: root.launch("systemsettings") }
                    Text { text: "System Settings"; color: Kirigami.Theme.textColor; opacity: 0.7; font.family: "Inter"; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    Text { text: "Bar: " + root.barSize.charAt(0).toUpperCase() + root.barSize.slice(1); color: Kirigami.Theme.textColor; opacity: 0.55; font.family: "Inter"; font.pixelSize: 11 }
                    SmallButton { icon: "arrow-right"; tip: "Configure the bar (size, magnify)"; onClicked: { root.closePane(); Plasmoid.internalAction("configure").trigger() } }
                }
            }

            // ================================================ notification history
            ColumnLayout {
                id: notifPane
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 6
                spacing: 6
                visible: root.paneMode === "notifications"
                opacity: visible ? pane.openProgress : 0

                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 8; spacing: 4
                    Text { text: "Notifications"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Text { visible: history.count > 0; text: history.count; color: Kirigami.Theme.textColor; opacity: 0.5; font.family: "Inter"; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    SmallButton { icon: root.dnd ? "notifications-disabled" : "notifications"; tip: root.dnd ? "Turn Do Not Disturb off" : "Do Not Disturb"; onClicked: root.toggleDnd() }
                    SmallButton { icon: "edit-clear-all"; tip: "Clear history"; active: history.count > 0; onClicked: history.clear(NotificationManager.Notifications.ClearExpired) }
                    SmallButton { icon: "configure"; tip: "Notification settings"; onClicked: root.launch("kcmshell6 kcm_notifications") }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 40; radius: 14
                    visible: root.dnd
                    color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.18)
                    Text { anchors.centerIn: parent; text: "Do Not Disturb is on — popups are held here"; color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 12 }
                }
                ListView {
                    id: notifList
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, Math.round(Screen.height * 0.5))
                    clip: true
                    model: history
                    spacing: 2
                    boundsBehavior: Flickable.StopAtBounds
                    delegate: NotificationRow { history: history }
                    add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 200 } }
                }
                Text {
                    visible: history.count === 0
                    Layout.fillWidth: true; Layout.topMargin: 18; Layout.bottomMargin: 24
                    horizontalAlignment: Text.AlignHCenter
                    text: "No notifications"
                    color: Kirigami.Theme.textColor; opacity: 0.55; font.family: "Inter"; font.pixelSize: 13
                }
            }
        }
    }
}
