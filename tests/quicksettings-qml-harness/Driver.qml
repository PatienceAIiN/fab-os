import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.notificationmanager as NotificationManager
import "../ui/status.js" as Status

// Headless driver for the quick-settings applet. tests/desktop-applets-qml-test.sh copies the plasmoid into a temp
// package and appends one Loader line to that COPY of main.qml which loads this file and hands over the ids. Feeds
// status.sh-shaped text (the first release's key=value form, still parsed) and /proc/net samples (moving, then idle:
// the rate must stay on the bar as "0 kB/s"), opens the slide-down pane while SAMPLING the dialog window's size and
// the card's y every 11 ms (the no-blink proof: the window never changes size while the card moves), drives the tile
// edit mode (programmatic drag through the delegate's dragTo, size toggle, remove / add back, reset -> tilesJson),
// then the notification pane after a real org.freedesktop.Notifications.Notify call on the session bus (28 gridUnits,
// 56 px rows, 32 px icon, 13 px body, dismiss reachable), Do Not Disturb, the bar size, and the close animation
// (sampled the same way). Renders /out/quicksettings-{bar,pane,edit}.png and /out/notifications-pane.png and prints
// PASS/FAIL lines + "HARNESS DONE failures=N".
// Under a virtual kwin_wayland (tests/dock-qml-harness/kwin-session.sh, QT_QPA_PLATFORM=wayland) it goes on to drive a
// REAL pointer through fakeinput.py (KWin's org_kde_kwin_fake_input): the main window is made fullscreen with the bar
// pinned to its top edge so scene coordinates are screen coordinates; the pointer hovers the network indicator
// (glyph-only magnify, rendered to /out/quicksettings-bar-hover.png), clicks the bell, hovers a history row, moves onto
// its dismiss cross and clicks it.
Item {
    id: h
    property var root: null
    property var pane: null
    property var bar: null
    property var history: null
    property var settingsPane: null
    property var notifPane: null
    property var tilesArea: null
    property var tileRepeater: null
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
    function tileItem(id) { for (var i = 0; i < tileRepeater.count; i++) { var it = tileRepeater.itemAt(i); if (it && it.tileId === id) return it } return null }

    // ---- window-size / card-position sampler (the no-blink proof)
    property var samples: []
    Timer { id: sampler; interval: 11; repeat: true; onTriggered: { var c = pane.cardItem; h.samples.push({ w: pane.width, h: pane.height, y: c.y, o: c.opacity, p: pane.openProgress }); if (h.samples.length >= 24) stop() } }
    function startSampling() { h.samples = []; sampler.restart() }
    function analyseSamples(label, opening) {
        var s = h.samples, ws = {}, hs = {}, ys = {}, mono = true, n = s.length
        for (var i = 0; i < n; i++) {
            ws[s[i].w] = true; hs[s[i].h] = true; ys[Math.round(s[i].y)] = true
            if (i > 0 && (opening ? s[i].y < s[i - 1].y - 0.01 : s[i].y > s[i - 1].y + 0.01)) mono = false
        }
        var nw = Object.keys(ws).length, nh = Object.keys(hs).length, ny = Object.keys(ys).length
        console.log("INFO " + label + ": " + n + " samples; window sizes " + Object.keys(ws).join("/") + " x " + Object.keys(hs).join("/") + "; card y " + s.map(function (x) { return Math.round(x.y) }).join(" "))
        check(n >= 20, label + ": at least 20 frames sampled during the 220 ms animation (" + n + ")")
        check(nw === 1 && nh === 1 && s[0].w > 0 && s[0].h > 0, label + ": the dialog window kept ONE width and ONE height across every sampled frame (" + Object.keys(ws)[0] + "x" + Object.keys(hs)[0] + ")")
        check(ny >= 8 && mono, label + ": the card's y moved through " + ny + " distinct values, monotonic (" + (opening ? "up to 0" : "down to -height") + ")")
        var c = pane.cardItem
        if (opening) check(s[0].y < -c.height * 0.3 && s[n - 1].y === 0 && s[0].o < 0.7 && near(s[n - 1].o, 1, 0.001), label + ": y from " + Math.round(s[0].y) + " to 0, opacity " + s[0].o.toFixed(2) + " -> 1")
        else check(s[0].y > -c.height * 0.7 && s[n - 1].y <= -c.height + 1 && s[n - 1].o < 0.01, label + ": y from " + Math.round(s[0].y) + " to " + Math.round(s[n - 1].y) + " (= -height " + c.height + "), opacity -> 0")
    }

    // ---- real pointer (wayland session only): one fakeinput.py process per move / click, then 600 ms to settle
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
    Component { id: backdrop; Rectangle { z: -1; anchors.fill: parent; color: Kirigami.Theme.backgroundColor } }
    function grab(item, file) {
        h.grabs++
        if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item)
        item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- })
    }
    onProbeChanged: if (root && pane && bar && history && notifList && tileRepeater && probe) startTimer.start()
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
    property int notifHeightBefore: 0

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
        root.applyNet(h.net(1000 + 2400000, 500 + 160000), 5000)   // two seconds later, not one byte moved
        check(root.speedVisible === true && netInd.text === "↓ 0 kB/s  ↑ 0 kB/s", "idle link: the rate STAYS on the bar as 0 kB/s (" + netInd.text + ")")
        root.applyNet(h.net(1000 + 2400000 + 160000, 500 + 160000 + 24000), 7000)
        check(netInd.text === "↓ 80 kB/s  ↑ 12 kB/s", "traffic again: " + netInd.text)
        check(netInd.icon === "network-wireless-signal-good-locked", "wifi glyph by signal: " + netInd.icon)
        check(batInd.visible && batInd.text === "87%" && batInd.icon === "battery-090", "battery glyph + percentage: " + batInd.icon + " " + batInd.text)
        check(root.glyph === 18 && root.textPx === 12 && root.clockPx === 13 && batInd.textPx === 13, "medium: glyph 18, text 12, battery text = clock 13")
        check(volInd.visible === false, "volume glyph hidden while unchanged and unmuted")
        check(root.dnd === false, "do not disturb off at start")
        check(netInd.scale === 1 && batInd.scale === 1 && netInd.glyphScale === 1 && typeof netInd.hovered === "boolean" && netInd.hovered === false, "indicators rest unscaled; magnify is on the glyph only (item scale stays 1)")
        check(pane.visible === false && root.paneMode === "closed", "pane hidden at start (imperative visibility, no dead binding)")
        // the tile model before the pane opens
        check(root.tiles.length === 12 && root.tiles[0].id === "wifi" && root.tiles[2].id === "volume" && root.tiles[2].size === "wide", "12 tiles in the default order from tilesJson")
        check(root.shownTiles.length === 11 && root.tileRect("nightlight") === null && root.tileRect("brightness") !== null && root.tileRect("battery") !== null, "night light hidden while KWin does not report it; brightness (backlight fed) and battery (fed) shown: " + root.shownTiles.length + " tiles")
        check(root.tileLayout.items.length === 11 && root.tileLayout.height > 300 && root.settingsHeight === 2 * root.cardPad + root.headerH + 8 + root.tileLayout.height + 8 + root.footerH, "settings card height is arithmetic on the tile layout (" + root.settingsHeight + " px)")
        stage1b.start()
    }
    Timer { id: stage1b; interval: 300; onTriggered: {
        grab(h.wayland ? h.strip : root, "/out/quicksettings-bar.png")
        h.startSampling()
        root.openPane("settings")
        check(root.paneMode === "settings" && pane.visible === true, "settings pane opens (dialog visible)")
        check(pane.backgroundHints === 0, "dialog window is transparent (backgroundHints NoBackground = " + pane.backgroundHints + ")")
        stage2.start()
    } }
    Timer { id: stage2; interval: 700; onTriggered: {
        h.analyseSamples("open", true)
        var c = pane.cardItem
        check(near(pane.openProgress, 1, 0.001) && c.y === 0, "slide-down finished (openProgress 1, card y 0)")
        check(c.width === Kirigami.Units.gridUnit * 21 && c.height === root.settingsHeight, "card " + c.width + "x" + c.height + " = 21 gridUnits x settingsHeight")
        check(pane.mainItem.width === c.width + 2 * pane.shadow && pane.mainItem.height === c.height + pane.shadow && pane.width === pane.mainItem.width && pane.height === pane.mainItem.height, "window = card + shadow margins (" + pane.width + "x" + pane.height + ")")
        check(c.bottomLeftRadius === 24 && c.bottomRightRadius === 24 && c.topLeftRadius === 0 && c.border.width === 1, "card drawn by the applet: radius 24 at the bottom corners, hairline")
        var col = (c.width - 2 * root.cardPad - 8) / 2
        var wifi = tileItem("wifi"), bt = tileItem("bluetooth"), vol = tileItem("volume"), night = tileItem("nightlight")
        check(wifi && wifi.visible && wifi.x === 0 && wifi.y === 0 && wifi.width === col, "Wi-Fi tile at the first slot, half a row wide (" + wifi.width + ")")
        check(bt && bt.x === col + 8 && bt.y === 0, "Bluetooth beside it")
        check(vol && vol.x === 0 && vol.y === 68 && vol.width === c.width - 2 * root.cardPad, "volume row wide below them")
        check(night && night.visible === false, "night light delegate exists but has no slot")
        check(tilesArea.height === root.tileLayout.height, "tiles area = layout height " + tilesArea.height)
        check(wifi.content && wifi.content.enabled === true && wifi.frame.visible === false && wifi.editBar.visible === false, "tiles are live outside edit mode (no frame, no handles)")
        grab(pane.mainItem, "/out/quicksettings-pane.png")
        stage2b.start()
    } }
    Timer { id: stage2b; interval: 400; onTriggered: {
        root.editing = true
        var wifi = tileItem("wifi"), bt = tileItem("bluetooth")
        check(wifi.frame.visible && wifi.editBar.visible && wifi.content.enabled === false, "edit mode: accent frame + size/remove controls, the tile's own controls inert")
        check(tileItem("nightlight").visible === true && root.shownTiles.length === 12, "edit mode shows every enabled tile, the unavailable one dimmed (" + tileItem("nightlight").opacity + ")")
        // programmatic drag: Wi-Fi dropped on Bluetooth's slot takes it; Bluetooth shifts to the first slot
        var target = bt.rect
        wifi.dragTo(target.x, target.y)
        check(root.tiles[0].id === "bluetooth" && root.tiles[1].id === "wifi", "drag reorders live: " + root.tiles.slice(0, 3).map(function (t) { return t.id }).join(","))
        root.dropTiles()
        check(root.cfg.tilesJson.indexOf('[{"id":"bluetooth"') === 0, "drop persists the order in tilesJson")
        root.saveTiles(Status.toggleTileSize(root.tiles, "wifi"))
        stage2c.start()
    } }
    Timer { id: stage2c; interval: 400; onTriggered: {
        var c = pane.cardItem, wifi = tileItem("wifi")
        check(wifi.wide && wifi.rect.w === c.width - 2 * root.cardPad && wifi.rect.y === 68 && wifi.rect.x === 0, "size toggle: Wi-Fi is now a wide tile on its own row (" + wifi.rect.w + " px at y " + wifi.rect.y + ")")
        check(pane.height === c.height + pane.shadow && c.height === root.settingsHeight, "window follows the card while open (instant, no animation): " + pane.height)
        root.saveTiles(Status.setTileEnabled(root.tiles, "screenshot", false))
        check(root.hiddenTiles.length === 1 && root.hiddenTiles[0].id === "screenshot" && tileItem("screenshot").visible === false, "remove: Screenshot leaves the grid")
        stage2d.start()
    } }
    Timer { id: stage2d; interval: 500; onTriggered: {   // the tiles have finished their 160 ms re-flow and the chip row is laid out
        check(root.addRowH > 0 && pane.cardItem.height === root.settingsHeight && pane.height === pane.cardItem.height + pane.shadow, "removed tiles come back as chips under the grid (add row " + root.addRowH + " px); the card and window follow (" + pane.height + ")")
        grab(pane.mainItem, "/out/quicksettings-edit.png")
        stage2e.start()
    } }
    Timer { id: stage2e; interval: 400; onTriggered: {
        root.saveTiles(Status.setTileEnabled(root.tiles, "screenshot", true))
        check(root.hiddenTiles.length === 0 && tileItem("screenshot").visible === true, "add back: Screenshot returns")
        root.saveTiles(Status.defaultTiles())
        check(root.tiles[0].id === "wifi" && !root.tiles[0].size.match(/wide/) && root.cfg.tilesJson === Status.tilesJson(Status.defaultTiles()), "reset to default: order, sizes and tilesJson back to the shipped layout")
        root.editing = false
        check(tileItem("wifi").frame.visible === false && tileItem("wifi").content.enabled === true, "done: frames gone, tiles live again")
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
        shell.connectSource("gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications --method org.freedesktop.Notifications.Notify 'Fab OS Updates' 0 'system-software-update' 'Update ready' 'Fab OS 1.0 revision 5 is ready to install.' '[]' \"{'desktop-entry': <'org.kde.discover'>}\" 1000")   // 1 s timeout: the badge counts notifications whose popup has gone
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
        var c = pane.cardItem
        check(root.paneMode === "notifications" && pane.visible, "bell pane open")
        check(c.width === Kirigami.Units.gridUnit * 28 && pane.width === c.width + 2 * pane.shadow, "notification pane 28 gridUnits wide (" + c.width + ")")
        check(notifList.count === 1 && notifList.visible, "history list has the row")
        var row = notifList.itemAtIndex(0)
        check(row && row.summaryText === "Update ready" && row.bodyText.indexOf("revision 5") >= 0, "row reads summary/body roles: " + (row ? row.summaryText : "no row"))
        if (row) {   // the dismiss cross must be reachable with the mouse: its hover area spans the whole row and the button lies inside it
            check(row.height >= 56 && row.appIcon.width === 32 && row.bodyLabel.font.pixelSize === 13, "row " + row.height + " px (>= 56), app icon 32, body 13 px")
            var btn = row.dismissButton, area = row.hoverArea, b = btn.mapToItem(row, 0, 0)
            check(area.x === 0 && area.y === 0 && area.width === row.width && area.height === row.height, "row hover area spans the whole row (" + area.width + "x" + area.height + " of " + row.width + "x" + row.height + ")")
            check(b.x >= 0 && b.x + btn.width <= row.width && b.y >= 0 && b.y + btn.height <= row.height && btn.width >= 24, "dismiss button lies inside the hover area (x " + Math.round(b.x) + " w " + btn.width + " of " + row.width + ")")
            check(typeof btn.hovered === "boolean" && btn.hovered === false && row.rowHovered === false && btn.visible === false, "dismiss button hidden until the row or the button itself is hovered")
            check(row.history === history && row.history !== null, "row.history is the Notifications model (not the delegate's own property)")
        }
        check(root.unread === 0, "opening the pane marks it read")
        check(c.height === root.notifHeight && root.notifHeight <= Math.round(root.screenH * 0.6) && root.notifListH >= 56, "card sized to the history (" + c.height + " px, list " + root.notifListH + "), capped at 60 % of the screen")
        h.notifHeightBefore = root.notifHeight
        root.toggleDnd()
        check(root.dnd === true, "do not disturb on")
        stage6.start()
    } }
    Timer { id: stage6; interval: 500; onTriggered: {
        var until = root.notifSettings.notificationsInhibitedUntil
        check(root.dnd === true && until && until.getTime() > Date.now() + 300 * 24 * 3600 * 1000, "do not disturb persisted after save + live reload (until " + until + ")")
        check(root.notifHeight === h.notifHeightBefore + 46 && pane.cardItem.height === root.notifHeight, "do-not-disturb banner adds one 40 px row to the card (" + root.notifHeight + " px)")
        grab(pane.mainItem, "/out/notifications-pane.png")   // rendered on the next frame: keep the banner until then
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
        check(root.lastSync.indexOf("writeConfig(\"barSize\", \"large\")") > 0 && root.lastSync.indexOf("in.patienceai.fabos.clock") > 0 && root.lastSync.indexOf("in.patienceai.fabos.dock") > 0, "size change queued the clock/dock sync script (Fab OS clock barSize + dock magnify)")
        check(root.lastSync.indexOf("magnification") < 0 && root.lastSync.indexOf("writeConfig(\"magnify\", true)") > 0, "sync writes the shared magnify switch only, never the dock's magnification strength")
        root.cfg.barSize = "medium"
        h.startSampling()
        root.closePane()
        stage8.start()
    } }
    Timer { id: stage8; interval: 600; onTriggered: {
        h.analyseSamples("close", false)
        check(root.paneMode === "closed" && pane.visible === false, "pane hidden after the slide-up")
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
