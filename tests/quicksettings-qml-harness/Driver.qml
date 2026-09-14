import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.notificationmanager as NotificationManager

// Headless driver for the quick-settings applet. tests/desktop-applets-qml-test.sh copies the plasmoid into a temp
// package and appends one Loader line to that COPY of main.qml which loads this file and hands over the ids. Feeds
// status.sh-shaped text and two /proc/net samples, opens the slide-down pane (settings, then notifications after a
// real org.freedesktop.Notifications.Notify call on the session bus), toggles Do Not Disturb and the bar size, renders
// /out/quicksettings-{bar,pane,notifications}.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
Item {
    id: h
    property var root: null
    property var pane: null
    property var bar: null
    property var history: null
    property var settingsPane: null
    property var notifPane: null
    property var notifList: null
    property var batInd: null
    property var netInd: null
    property var volInd: null
    property var bellInd: null
    property var poll: null
    property var netPoll: null
    property int failures: 0
    property int grabs: 0
    property var backdrops: ({})
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function near(a, b, eps) { return Math.abs(a - b) <= (eps || 0.02) }
    Component { id: backdrop; Rectangle { z: -1; anchors.fill: parent; radius: 24; color: Kirigami.Theme.backgroundColor } }
    function grab(item, file) {
        h.grabs++
        if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item)
        item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + r.image.width + "x" + r.image.height); h.grabs-- })
    }
    onNotifListChanged: if (root && pane && bar && history && notifList) startTimer.start()
    Timer { id: startTimer; interval: 700; onTriggered: h.stage1() }
    P5Support.DataSource { id: shell; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("SHELL exit=" + data["exit code"] + " out=" + String(data["stdout"] || "").trim() + " err=" + String(data["stderr"] || "").trim().slice(0, 120)) } }

    readonly property string status: [
        "wifi_radio=enabled", "conn=802-11-wireless|wlp2s0|Home Net", "wifi=*:Home Net:78:WPA2", "iface=wlp2s0", "ip4=10.0.0.5/24",
        "bt_present=yes", "bt_powered=yes", "bt_connected=1", "volume=Volume: 0.45", "bat_pct=87", "bat_status=Discharging",
        "bat_time=3.2 hours", "profile=balanced", "bl_cur=45528", "bl_max=64764", ""].join("\n")
    function net(rx, tx) {
        return "Iface\tDestination\tGateway\n" + "wlp2s0\t00000000\t3B03EC0A\t0003\t0\t0\t600\t00000000\t0\t0\t0\n---\n"
             + "Inter-|Receive|Transmit\n face |bytes packets\n    lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\nwlp2s0: " + rx + " 4 0 0 0 0 0 0 " + tx + " 9 0 0 0 0 0 0\n"
    }

    function stage1() {
        root.paneAutoHide = false
        root.autoRefresh = false
        if (poll) poll.disconnectSource(root.statusCmd)   // the fed state must not be replaced by the container's real probe
        if (netPoll) netPoll.disconnectSource(root.netCmd)
        var win = root.Window.window
        if (win) { win.width = 520; win.height = 40 }
        root.applyStatus(h.status)
        root.applyNet(h.net(1000, 500), 1000)
        root.applyNet(h.net(1000 + 2400000, 500 + 160000), 3000)
        check(root.st.wifiSignal === 78 && root.st.batPct === 87 && root.st.volume === 45, "status applied (wifi 78, battery 87, volume 45)")
        check(root.speedVisible === true && netInd.text === "↓ 1.2 MB/s  ↑ 80 kB/s", "speed text beside the network glyph: " + netInd.text)
        check(netInd.icon === "network-wireless-signal-good-locked", "wifi glyph by signal: " + netInd.icon)
        check(batInd.visible && batInd.text === "87%" && batInd.icon === "battery-090", "battery glyph + percentage: " + batInd.icon + " " + batInd.text)
        check(root.glyph === 18 && root.textPx === 12 && root.clockPx === 13 && batInd.textPx === 13, "medium: glyph 18, text 12, battery text = clock 13")
        check(volInd.visible === false, "volume glyph hidden while unchanged and unmuted")
        check(root.dnd === false, "do not disturb off at start")
        stage1b.start()
    }
    Timer { id: stage1b; interval: 300; onTriggered: {
        grab(root, "/out/quicksettings-bar.png")
        root.openPane("settings")
        check(root.paneMode === "settings" && pane.visible === true, "settings pane opens (dialog visible)")
        stage2.start()
    } }
    Timer { id: stage2; interval: 700; onTriggered: {
        check(near(pane.openProgress, 1, 0.001), "slide-down finished (openProgress 1)")
        check(pane.mainItem.height >= settingsPane.implicitHeight - 1 && settingsPane.implicitHeight > 200, "pane height reached its content (" + pane.mainItem.height + " px)")
        check(pane.mainItem.width === pane.paneWidth, "pane width " + pane.paneWidth)
        grab(pane.mainItem, "/out/quicksettings-pane.png")
        // volume slider path: coalesced wpctl call + glyph flash
        root.setVolume(60)
        stage3.start()
    } }
    Timer { id: stage3; interval: 400; onTriggered: {
        check(root.st.volume === 60 && volInd.visible === true && volInd.icon === "audio-volume-medium", "volume set -> state 60, glyph flashes")
        root.toggleMute()
        check(root.st.muted === true && volInd.icon === "audio-volume-muted", "mute toggles the glyph")
        root.toggleMute()
        // a real notification on the session bus
        shell.connectSource("gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications --method org.freedesktop.Notifications.Notify 'Fab OS Updates' 0 'system-software-update' 'Update ready' 'Fab OS 1.0 revision 3 is ready to install.' '[]' \"{'desktop-entry': <'org.kde.discover'>}\" 1000")   // 1 s timeout: the badge counts notifications whose popup has gone
        stage4.start()
    } }
    Timer { id: stage4; interval: 1800; onTriggered: {
        console.log("INFO Server.valid=" + NotificationManager.Server.valid + " history.count=" + history.count + " active=" + history.activeNotificationsCount + " unread=" + root.unread + " historyBlacklist=" + JSON.stringify(root.notifSettings.historyBlacklistedApplications))
        check(history.count === 1, "notification history received the Notify call (count 1)")
        check(root.unread === 0 && history.activeNotificationsCount === 1, "still a popup: active 1, unread 0")
        history.expire(history.index(0, 0))   // the stock popup does this when its timeout ends; then the badge counts it
        stage4b.start()
    } }
    Timer { id: stage4b; interval: 300; onTriggered: {
        check(root.unread === 1 && bellInd.badge === 1, "popup gone -> bell badge 1 (unread " + root.unread + ")")
        root.openPane("notifications")
        stage5.start()
    } }
    Timer { id: stage5; interval: 700; onTriggered: {
        check(root.paneMode === "notifications" && pane.visible, "bell pane open")
        check(notifList.count === 1, "history list has the row")
        var row = notifList.itemAtIndex(0)
        check(row && row.summaryText === "Update ready" && row.bodyText.indexOf("revision 3") >= 0, "row reads summary/body roles: " + (row ? row.summaryText : "no row"))
        check(root.unread === 0, "opening the pane marks it read")
        check(pane.mainItem.height >= notifPane.implicitHeight - 1, "pane re-sized to the history (" + pane.mainItem.height + " px)")
        root.toggleDnd()
        check(root.dnd === true, "do not disturb on")
        stage6.start()
    } }
    Timer { id: stage6; interval: 500; onTriggered: {
        var until = root.notifSettings.notificationsInhibitedUntil
        check(root.dnd === true && until && until.getTime() > Date.now() + 300 * 24 * 3600 * 1000, "do not disturb persisted after save + live reload (until " + until + ")")
        check(notifPane.implicitHeight > 140, "do-not-disturb banner is in the pane (" + notifPane.implicitHeight + " px)")
        grab(pane.mainItem, "/out/quicksettings-notifications.png")   // rendered on the next frame: keep the banner until then
        stage6b.start()
    } }
    Timer { id: stage6b; interval: 400; onTriggered: {
        root.toggleDnd()
        check(root.dnd === false, "do not disturb off again")
        root.cfg.barSize = "large"
        stage7.start()
    } }
    Timer { id: stage7; interval: 500; onTriggered: {
        check(root.glyph === 22 && root.textPx === 13 && root.clockPx === 15 && batInd.textPx === 15, "large: glyph 22, text 13, battery text 15")
        check(root.lastSync.indexOf("writeConfig(\"fontSize\", 15)") > 0 && root.lastSync.indexOf("in.patienceai.fabos.dock") > 0, "size change queued the clock/dock sync script")
        root.cfg.barSize = "medium"
        root.closePane()
        stage8.start()
    } }
    Timer { id: stage8; interval: 500; onTriggered: {
        check(root.paneMode === "closed" && pane.visible === false, "pane closed after the shrink")
        check(root.glyph === 18, "back to medium")
        done.start()
    } }
    Timer { id: done; interval: 800; repeat: true; onTriggered: {
        if (h.grabs > 0) return
        console.log("HARNESS DONE failures=" + h.failures)
        stop(); killer.connectSource("pkill -x plasmawindowed")
    } }
    P5Support.DataSource { id: killer; engine: "executable" }
}
