import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.notificationmanager as NotificationManager

// Headless driver for the quick-settings applet. tests/desktop-applets-qml-test.sh copies the plasmoid into a temp
// package and appends one Loader line to that COPY of main.qml which loads this file and hands over the ids. Feeds
// status.sh-shaped text (the first release's key=value form, still parsed) and two /proc/net samples, opens the slide-down pane (settings, then notifications after a
// real org.freedesktop.Notifications.Notify call on the session bus), toggles Do Not Disturb and the bar size, renders
// /out/quicksettings-{bar,pane,notifications}.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
// Under a virtual kwin_wayland (tests/dock-qml-harness/kwin-session.sh, QT_QPA_PLATFORM=wayland) it goes on to drive a
// REAL pointer through fakeinput.py (KWin's org_kde_kwin_fake_input): the main window is made fullscreen with the bar
// pinned to its top edge so scene coordinates are screen coordinates; the pointer hovers the network indicator
// (glyph-only magnify, rendered to /out/quicksettings-bar-hover.png), then hovers a history row, moves onto its
// dismiss cross and clicks it.
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
    property var probe: null
    property int failures: 0
    property int grabs: 0
    property var backdrops: ({})
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function near(a, b, eps) { return Math.abs(a - b) <= (eps || 0.02) }

    // ---- real pointer (wayland session only): one fakeinput.py process per move / click, then 350 ms to settle
    readonly property bool wayland: Qt.platform.pluginName === "wayland"
    readonly property string injector: Qt.resolvedUrl("fakeinput.py").toString().replace(/^file:\/\//, "")
    property var afterPointer: null
    property int pointerMoves: 0
    property bool reported: false
    property real netWidth: 0
    P5Support.DataSource { id: pointer; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("POINTER exit=" + data["exit code"] + " " + String(data["stdout"] || "").trim() + " " + String(data["stderr"] || "").trim().slice(0, 300)); settle.start() } }
    Timer { id: settle; interval: 600; onTriggered: { var f = h.afterPointer; h.afterPointer = null; if (f) f() } }
    readonly property string python: "PYTHONHOME=/usr /tmp/fabos-pointer"   // the trusted private interpreter copy made by kwin-session.sh
    function movePointer(x, y, then) { h.pointerMoves++; h.afterPointer = then; pointer.connectSource(h.python + " " + h.injector + " move " + Math.round(x) + " " + Math.round(y)) }
    function clickPointer(x, y, then) { h.pointerMoves++; h.afterPointer = then; pointer.connectSource(h.python + " " + h.injector + " click " + Math.round(x) + " " + Math.round(y)) }
    Component { id: stripComp; Item { } }   // plain wrapper for the bar under wayland (a grab backdrop must not be a Row child)
    property Item strip: null
    function screenPos(item, fx, fy) { return item.mapToItem(null, item.width * fx, item.height * fy) }   // main window is fullscreen at 0,0
    function panePos(item, fx, fy) { var p = item.mapToItem(null, item.width * fx, item.height * fy); return Qt.point(pane.x + p.x, pane.y + p.y) }   // the dialog window sits at pane.x/y
    Component { id: backdrop; Rectangle { z: -1; anchors.fill: parent; radius: 24; color: Kirigami.Theme.backgroundColor } }
    function grab(item, file) {
        h.grabs++
        if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item)
        item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- })
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
        if (poll) poll.connectedSources = []                // the fed state must not be replaced by the container's real probe
        if (probe) probe.connectedSources = []              // (the periodic light/full probe and the one-shot full probe)
        var win = root.Window.window
        if (h.wayland && win) {   // screen coordinates for the pointer: fullscreen window, the bar in a 40 px strip along its top edge
            h.strip = stripComp.createObject(root, { width: Qt.binding(function() { return bar.implicitWidth + 32 }), height: 40 })
            h.strip.anchors.top = root.top; h.strip.anchors.horizontalCenter = root.horizontalCenter
            bar.parent = h.strip   // its own centerIn/height bindings now follow the strip
            win.visibility = Window.FullScreen
        } else if (win) { win.width = 520; win.height = 40 }
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
        check(netInd.scale === 1 && batInd.scale === 1 && netInd.glyphScale === 1 && typeof netInd.hovered === "boolean" && netInd.hovered === false, "indicators rest unscaled; magnify is on the glyph only (item scale stays 1)")
        check(pane.visible === false && root.paneMode === "closed", "pane hidden at start (imperative visibility, no dead binding)")
        stage1b.start()
    }
    Timer { id: stage1b; interval: 300; onTriggered: {
        grab(h.wayland ? h.strip : root, "/out/quicksettings-bar.png")
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
        if (row) {   // the dismiss cross must be reachable with the mouse: its hover area spans the whole row and the button lies inside it
            var btn = row.dismissButton, area = row.hoverArea, b = btn.mapToItem(row, 0, 0)
            check(area.x === 0 && area.y === 0 && area.width === row.width && area.height === row.height, "row hover area spans the whole row (" + area.width + "x" + area.height + " of " + row.width + "x" + row.height + ")")
            check(b.x >= 0 && b.x + btn.width <= row.width && b.y >= 0 && b.y + btn.height <= row.height && btn.width >= 24, "dismiss button lies inside the hover area (x " + Math.round(b.x) + " w " + btn.width + " of " + row.width + ")")
            check(typeof btn.hovered === "boolean" && btn.hovered === false && row.rowHovered === false && btn.visible === false, "dismiss button hidden until the row or the button itself is hovered")
            check(row.history === history && row.history !== null, "row.history is the Notifications model (not the delegate's own property)")
        }
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
        check(root.lastSync.indexOf("magnification") < 0 && root.lastSync.indexOf("writeConfig(\"magnify\", true)") > 0, "sync writes the shared magnify switch only, never the dock's magnification strength")
        root.cfg.barSize = "medium"
        root.closePane()
        stage8.start()
    } }
    Timer { id: stage8; interval: 500; onTriggered: {
        check(root.paneMode === "closed" && pane.visible === false, "pane closed after the shrink")
        check(root.glyph === 18, "back to medium")
        if (h.wayland) stageP0.start(); else done.start()
    } }

    // ---- real pointer stages (wayland session only)
    Timer { id: stageP0; interval: 300; onTriggered: {
        shell.connectSource("PYTHONHOME=/usr nohup /tmp/fabos-pointer " + h.injector + " hold 120 >/tmp/xdg/hold.log 2>&1 &")   // one device for the whole phase
        stageP1.start()
    } }
    Timer { id: stageP1; interval: 900; onTriggered: {
        var p = screenPos(netInd, 0.5, 0.5); h.netWidth = netInd.width
        console.log("INFO pointer test: root " + root.width + "x" + root.height + " visibility " + root.Window.window.visibility + "; network indicator centre " + Math.round(p.x) + "," + Math.round(p.y))
        movePointer(root.width / 2, root.height - 30, function() {   // warm-up: the seat's pointer enters the window somewhere neutral first
        movePointer(p.x, p.y, function() {
            check(netInd.hovered === true, "real pointer over the network indicator: hovered")
            check(near(netInd.glyphScale, 1.25) && netInd.scale === 1 && netInd.width === h.netWidth, "hover magnifies the glyph only (glyph " + netInd.glyphScale.toFixed(2) + ", item scale " + netInd.scale + ", width " + netInd.width + " unchanged)")
            check(batInd.hovered === false && bellInd.hovered === false, "neighbours not hovered")
            grab(h.strip, "/out/quicksettings-bar-hover.png")
            stageP2.start()
        }) })
    } }
    Timer { id: stageP2; interval: 300; onTriggered: {
        movePointer(root.width / 2, root.height - 30, function() {
            check(netInd.hovered === false && near(netInd.glyphScale, 1.0, 0.05), "pointer away: glyph back to 1.0 (hovered " + netInd.hovered + ", glyph " + netInd.glyphScale.toFixed(2) + ")")
            var bell = screenPos(bellInd, 0.5, 0.5)
            clickPointer(bell.x, bell.y, function() {   // a real click on the bell opens the notifications pane (Indicator TapHandler)
                check(root.paneMode === "notifications" && pane.visible, "real click on the bell indicator opened the notifications pane (paneMode " + root.paneMode + ")")
                if (root.paneMode !== "notifications") root.openPane("notifications")
                stageP3.start()
            })
        })
    } }
    Timer { id: stageP3; interval: 700; onTriggered: {
        var row = notifList.itemAtIndex(0)
        check(row && notifList.count === 1 && pane.visible, "history row present for the pointer test (" + notifList.count + ")")
        if (!row) { done.start(); return }
        var body = panePos(row, 0.3, 0.5)
        console.log("INFO pane window at " + pane.x + "," + pane.y + " " + pane.width + "x" + pane.height + " flags " + pane.flags + "; row body target " + Math.round(body.x) + "," + Math.round(body.y))
        row.dismissButton.clicked.connect(function() { console.log("INFO dismiss button clicked (row.history " + (row.history === history ? "ok" : row.history) + ", history " + history.count + " before close)") })
        movePointer(body.x, body.y, function() {
            check(row.rowHovered === true && row.hoverArea.containsMouse === true && row.dismissButton.visible === true, "pointer on the row body: row hovered, dismiss cross shown")
            var cross = panePos(row.dismissButton, 0.5, 0.5)
            movePointer(cross.x, cross.y, function() {
                check(row.dismissButton.hovered === true && row.dismissButton.visible === true && row.rowHovered === true, "pointer on the dismiss cross itself: still visible + hovered (button " + row.dismissButton.hovered + ", row area " + row.hoverArea.containsMouse + ")")
                clickPointer(cross.x, cross.y, function() { stageP4.start() })
            })
        })
    } }
    Timer { id: stageP4; interval: 600; onTriggered: {
        check(history.count === 0 && notifList.count === 0, "clicking the cross dismissed the notification (history " + history.count + ")")
        if (history.count > 0) {   // diagnose: is it the click path or the model call?
            var idx = history.index(0, 0)
            console.log("INFO direct history.close: idx valid " + (idx && idx.valid) + " id " + history.data(idx, 257) + " expired " + history.data(idx, 267) + " server valid " + NotificationManager.Server.valid)
            history.close(idx)
            stageP5.start(); return
        }
        root.closePane()
        done.start()
    } }
    Timer { id: stageP5; interval: 500; onTriggered: {
        console.log("INFO after direct close: history.count=" + history.count + " list " + notifList.count)
        root.closePane()
        done.start()
    } }
    Timer { id: holderReport; interval: 1; onTriggered: shell.connectSource("cat /tmp/xdg/hold.log; pgrep -fc 'fabos-pointer .* hold' || true") }
    Timer { id: done; interval: 800; repeat: true; onTriggered: {
        if (h.grabs > 0) return
        if (h.wayland && !h.reported) { h.reported = true; holderReport.start(); return }   // one more tick: log the holder's state
        console.log("HARNESS DONE failures=" + h.failures)
        stop(); killer.connectSource("pkill -x plasmawindowed")
    } }
    P5Support.DataSource { id: killer; engine: "executable" }
}
