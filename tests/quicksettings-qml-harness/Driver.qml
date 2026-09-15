import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.notificationmanager as NotificationManager
import "../ui/status.js" as Status

// Headless driver for the quick-settings applet. tests/desktop-applets-qml-test.sh copies the plasmoid into a temp
// package and appends one Loader line to that COPY of main.qml which loads this file and hands over the ids. Feeds
// status.sh-shaped text — the first release's key=value line (still parsed), then the JSON line the shipped script
// prints, which also carries KWin's night light — and /proc/net samples (moving, then idle: the rate must stay on the
// bar as "0 kB/s"), opens the slide-down pane while SAMPLING the dialog window's size, the
// card's y and the tiles' stagger every 11 ms for 480 ms (the no-blink proof: the window never changes size while the
// card moves or the tiles stagger in), checks the v3 geometry (36 gridUnits, 12 px right margin, padding 20, the
// three-column grid, Inter 15/600 titles, the 28/700 battery number, the 36 px slider thumb, the footer's network
// line), drives the tile edit mode (programmatic drag through the delegate's dragTo, size cycle, remove / add back,
// reset -> tilesJson), then the notification pane after a real org.freedesktop.Notifications.Notify call on the
// session bus (same width, 64 px rows, 36 px icon, 14 px body, dismiss reachable), Do Not Disturb, the bar size, and
// the close animation (sampled the same way, 160 ms). Renders /out/quicksettings-{bar,open-mid,open-end,edit,
// notifications}.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
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
    function lastPlaced() { var best = null; for (var i = 0; i < tileRepeater.count; i++) { var it = tileRepeater.itemAt(i); if (it && it.rect && (!best || it.order > best.order)) best = it } return best }

    // ---- window-size / card-position / stagger sampler (the no-blink proof)
    property var samples: []
    property int sampleMax: 44
    Timer { id: sampler; interval: 11; repeat: true; onTriggered: {
        var c = pane.cardItem, first = tileItem("wifi"), last = lastPlaced()
        h.samples.push({ w: pane.width, h: pane.height, y: c.y, o: c.opacity, p: pane.openProgress, t0: first ? first.introOpacity : 1, tn: last ? last.introOpacity : 1, dy: first ? first.introDy : 0 })
        if (h.samples.length >= h.sampleMax) stop()
    } }
    function startSampling(n) { h.samples = []; h.sampleMax = n; sampler.restart() }
    function analyseSamples(label, opening) {
        var s = h.samples, ws = {}, hs = {}, ys = {}, mono = true, n = s.length
        for (var i = 0; i < n; i++) {
            ws[s[i].w] = true; hs[s[i].h] = true; ys[Math.round(s[i].y)] = true
            if (i > 0 && (opening ? s[i].y < s[i - 1].y - 0.01 : s[i].y > s[i - 1].y + 0.01)) mono = false
        }
        var nw = Object.keys(ws).length, nh = Object.keys(hs).length, ny = Object.keys(ys).length
        console.log("INFO " + label + ": " + n + " samples over ~" + (n * 11) + " ms; window sizes " + Object.keys(ws).join("/") + " x " + Object.keys(hs).join("/") + "; card y " + s.map(function (x) { return Math.round(x.y) }).join(" "))
        check(n >= (opening ? 40 : 20), label + ": at least " + (opening ? 40 : 20) + " frames sampled (" + n + ")")
        check(nw === 1 && nh === 1 && s[0].w > 0 && s[0].h > 0, label + ": the dialog window kept ONE width and ONE height across every sampled frame (" + Object.keys(ws)[0] + "x" + Object.keys(hs)[0] + ") — no blink")
        check(ny >= 6 && mono, label + ": the card's y moved through " + ny + " distinct values, monotonic (" + (opening ? "up to 0" : "down to -0.35 height") + ")")
        var c = pane.cardItem, h0 = c.height, curveOk = true, worst = 0
        // timing-independent: whatever frame the first tick catches, y and opacity must be ONE progress on the specified
        // curve y = -0.35 x height x (1 - opacity) (y is rounded to whole pixels by the applet)
        for (var q = 0; q < n; q++) { var d = Math.abs(s[q].y - (-0.35 * h0 * (1 - s[q].o))); worst = Math.max(worst, d); if (d > 1.5) curveOk = false }
        check(curveOk, label + ": every sampled frame's card y = -0.35 x " + h0 + " x (1 - opacity) within 1.5 px (worst " + worst.toFixed(2) + "): one progress drives y and opacity from the -0.35 x height start")
        if (opening) {
            check(s[0].y < -h0 * 0.05 && s[n - 1].y === 0 && near(s[n - 1].o, 1, 0.001), label + ": caught in flight at y " + Math.round(s[0].y) + ", opacity " + s[0].o.toFixed(2) + " (full travel " + Math.round(-0.35 * h0) + "), ends at y 0, opacity 1")
            console.log("INFO " + label + ": first tile intro opacity " + s.map(function (x) { return x.t0.toFixed(2) }).join(" ") + "\nINFO " + label + ": last tile intro opacity " + s.map(function (x) { return x.tn.toFixed(2) }).join(" "))
            var staggered = false, riseSeen = false
            for (var k = 0; k < n; k++) { if (s[k].t0 > s[k].tn + 0.2) staggered = true; if (s[k].dy > 2) riseSeen = true }
            check(staggered && riseSeen && near(s[n - 1].t0, 1, 0.001) && near(s[n - 1].tn, 1, 0.001), label + ": tiles stagger in (the first tile ahead of the last by > 0.2 opacity at some frame, an 8 px rise seen) and all end at opacity 1")
        } else {
            check(s[0].y > Math.round(-0.35 * h0) + 1 && s[n - 1].y <= Math.round(-0.35 * h0) + 1 && s[n - 1].o < 0.01, label + ": caught in flight at y " + Math.round(s[0].y) + ", ends at " + Math.round(s[n - 1].y) + " (= -0.35 x " + h0 + "), opacity -> 0 within 160 ms")
        }
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
    function grab(item, file) {   // one backdrop per ITEM (several renders of the pane share it)
        h.grabs++
        var key = String(item)
        if (!h.backdrops[key]) h.backdrops[key] = backdrop.createObject(item)
        item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- })
    }
    onProbeChanged: if (root && pane && bar && history && notifList && tileRepeater && probe) startTimer.start()
    Timer { id: startTimer; interval: 700; onTriggered: h.stage1() }
    P5Support.DataSource { id: shell; engine: "executable"; onNewData: (source, data) => { disconnectSource(source); console.log("SHELL exit=" + data["exit code"] + " out=" + String(data["stdout"] || "").trim() + " err=" + String(data["stderr"] || "").trim().slice(0, 120)) } }

    readonly property string status: [
        "wifi_radio=enabled", "conn=802-11-wireless|wlp2s0|Home Net", "wifi=*:Home Net:78:WPA2", "iface=wlp2s0", "ip4=10.0.0.5/24",
        "bt_present=yes", "bt_powered=yes", "bt_connected=1", "volume=Volume: 0.45", "bat_pct=87", "bat_status=Discharging",
        "bat_time=3.2 hours", "profile=balanced", "bl_cur=45528", "bl_max=64764", ""].join("\n")
    // the same machine as ONE JSON line, the form contents/code/status.sh prints today (status.js fromJson); it adds
    // KWin's night light, so the Night light tile takes its slot in row 5
    readonly property string statusJson: JSON.stringify({
        net: { iface: "wlp2s0", rx: 1000, tx: 500 }, wifi_quality: -1,
        battery: { pct: 87, status: "Discharging", time: "3.2 hours" }, backlight: { cur: 45528, max: 64764 },
        wifi: "yes:78:Home Net:WPA2", devs: ["wlp2s0:wifi:connected:Home Net", "enp3s0:ethernet:unavailable:"], ip4: "10.0.0.5/24",
        bt: { present: true, powered: true, connected: 1 }, volume: "Volume: 0.45", profile: "balanced",
        night: { enabled: false, running: false } })
    function net(rx, tx) {
        return "Iface\tDestination\tGateway\n" + "wlp2s0\t00000000\t3B03EC0A\t0003\t0\t0\t600\t00000000\t0\t0\t0\n---\n"
             + "Inter-|Receive|Transmit\n face |bytes packets\n    lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\nwlp2s0: " + rx + " 4 0 0 0 0 0 0 " + tx + " 9 0 0 0 0 0 0\n"
    }
    property int notifHeightBefore: 0
    readonly property int col: Math.floor((root.contentWidth - 2 * root.tileGap) / 3)   // one grid column

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
        check(root.st.wifiSignal === 78 && root.st.batPct === 87 && root.st.volume === 45 && root.st.nightEnabled === null, "legacy key=value status still parsed (wifi 78, battery 87, volume 45; night light unknown)")
        root.applyStatus(h.statusJson)
        check(root.st.wifiSignal === 78 && root.st.batPct === 87 && root.st.volume === 45 && root.st.ip4 === "10.0.0.5" && root.st.btConnected === 1 && root.st.wifiRadio === true && root.st.connType === "wifi" && root.st.nightEnabled === false, "status.sh's JSON line applied (same state + KWin's night light: off)")
        root.applyNet(h.net(1000, 500), 1000)
        root.applyNet(h.net(1000 + 2400000, 500 + 160000), 3000)
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
        // the tile model before the pane opens: three-column defaults
        check(root.tiles.length === 12 && root.tiles[0].id === "wifi" && root.tiles[0].size === "medium" && root.tiles[1].id === "bluetooth" && root.tiles[1].size === "small" && root.tiles[2].id === "volume" && root.tiles[2].size === "wide" && root.tiles[4].id === "battery" && root.tiles[4].size === "medium", "12 tiles in the default order from tilesJson: Wi-Fi medium (2 cols), Bluetooth small, volume wide, battery medium")
        check(root.tiles.filter(function (t) { return t.enabled }).length === 9 && root.tiles.filter(function (t) { return !t.enabled }).map(function (t) { return t.id }).join(",") === "notifications,powerprofile,netspeed", "9 tiles on by default; Notifications, Power profile and Network speed are optional extras")
        check(root.shownTiles.length === 9 && root.tileRect("nightlight") !== null && root.tileRect("brightness") !== null && root.tileRect("battery") !== null, "night light (KWin reports it), brightness (backlight fed) and battery (fed) shown: " + root.shownTiles.length + " tiles")
        check(root.paneUnits === 36 && root.settingsWidth === Kirigami.Units.gridUnit * 36 && root.cardPad === 20 && root.tileGap === 12, "pane geometry at Medium: 36 gridUnits = " + root.settingsWidth + " px, padding 20, 12 px gaps")
        check(root.tileLayout.items.length === 9 && root.tileLayout.height === 76 + 12 + 64 + 12 + 64 + 12 + 96 + 12 + 76 && root.settingsHeight === 2 * root.cardPad + root.tileLayout.height + root.footerGap + root.footerH, "settings card height is arithmetic on the tile layout (grid " + root.tileLayout.height + " px, card " + root.settingsHeight + " px)")
        stage1b.start()
    }
    Timer { id: stage1b; interval: 300; onTriggered: {
        grab(h.wayland ? h.strip : root, "/out/quicksettings-bar.png")
        h.startSampling(44)
        root.openPane("settings")
        check(root.paneMode === "settings" && pane.visible === true, "settings pane opens (dialog visible)")
        check(pane.backgroundHints === 0, "dialog window is transparent (backgroundHints NoBackground = " + pane.backgroundHints + ")")
        midGrab.start()
        stage2.start()
    } }
    Timer { id: midGrab; interval: 70; onTriggered: {   // mid-slide: the card still rising, the first tiles fading in
        var c = pane.cardItem
        console.log("INFO open-mid at ~70 ms: card y " + Math.round(c.y) + " opacity " + c.opacity.toFixed(2) + " first tile " + (tileItem("wifi") ? tileItem("wifi").introOpacity.toFixed(2) : "-"))
        check(c.y < -4 && c.opacity < 0.95 && c.opacity > 0.05, "mid-slide frame captured while the card is still moving (y " + Math.round(c.y) + ", opacity " + c.opacity.toFixed(2) + ")")
        grab(pane.mainItem, "/out/quicksettings-open-mid.png")
    } }
    Timer { id: stage2; interval: 900; onTriggered: {
        h.analyseSamples("open", true)
        var c = pane.cardItem
        check(near(pane.openProgress, 1, 0.001) && c.y === 0, "slide-down finished (openProgress 1, card y 0)")
        check(c.width === Kirigami.Units.gridUnit * 36 && c.height === root.settingsHeight, "card " + c.width + "x" + c.height + " = 36 gridUnits x settingsHeight")
        check(pane.mainItem.width === c.width + pane.shadow + pane.edge && pane.mainItem.height === c.height + pane.shadow && pane.width === pane.mainItem.width && pane.height === pane.mainItem.height, "window = card + shadow room + right margin (" + pane.width + "x" + pane.height + ")")
        check(pane.mainItem.width - (c.x + c.width) === 12 && pane.edge === 12, "card's right edge 12 px inside the window (= 12 px from the bar's right edge once the window is clamped to the screen edge)")
        check(c.bottomLeftRadius === 24 && c.bottomRightRadius === 24 && c.topLeftRadius === 0 && c.border.width === 1, "card drawn by the applet: radius 24 at the bottom corners, square top against the bar, hairline")
        var col = h.col, cw = root.contentWidth
        var wifi = tileItem("wifi"), bt = tileItem("bluetooth"), vol = tileItem("volume"), bri = tileItem("brightness"), bat = tileItem("battery"), dnd = tileItem("dnd"), shot = tileItem("screenshot"), set = tileItem("settings"), night = tileItem("nightlight")
        check(wifi && wifi.visible && wifi.x === 0 && wifi.y === 0 && wifi.width === 2 * col + 12 && wifi.height === 76 && wifi.span === 2, "row 1: Wi-Fi spans two columns (" + wifi.width + " x " + wifi.height + ")")
        check(bt && bt.x === 2 * (col + 12) && bt.y === 0 && bt.width === cw - bt.x && bt.span === 1, "row 1: Bluetooth in the third column, flush with the right edge (" + bt.width + ")")
        check(vol && vol.x === 0 && vol.y === 88 && vol.width === cw && vol.height === 64, "row 2: volume slider full width at y 88 (64 px)")
        check(bri && bri.x === 0 && bri.y === 164 && bri.width === cw && bri.height === 64, "row 3: brightness slider full width at y 164")
        check(bat && bat.x === 0 && bat.y === 240 && bat.width === 2 * col + 12 && bat.height === 96 && dnd && dnd.x === 2 * (col + 12) && dnd.y === 240 && dnd.height === 96, "row 4: battery card two columns (96 px) + Do Not Disturb stretched to the row height")
        check(night && night.visible && night.x === 0 && night.y === 348 && night.width === col && shot && shot.x === col + 12 && shot.y === 348 && shot.width === col && set && set.x === 2 * (col + 12) && set.y === 348 && set.width === cw - set.x, "row 5: Night light, Screenshot, Settings one column each (the last flush with the right edge)")
        check(night.content && night.content.on === false && night.content.detail === "Off" && night.content.actionEnabled === true, "night light tile reads KWin's state: off, toggleable")
        check(tilesArea.height === root.tileLayout.height, "tiles area = layout height " + tilesArea.height)
        check(wifi.content && wifi.content.enabled === true && wifi.frame.visible === false && wifi.editBar.visible === false, "tiles are live outside edit mode (no frame, no handles)")
        // type + controls
        check(wifi.content.glyphCircle.width === 40 && wifi.content.radius === 16 && wifi.content.on === true && wifi.content.detail === "Home Net · 78%", "Wi-Fi tile: 40 px glyph circle, radius 16, accent while on, 'Home Net · 78%'")
        check(bt.content.detail === "1 connected" && bt.content.on === true, "Bluetooth (one column) uses the short line: " + bt.content.detail)
        check(vol.content.thumb.width === 36 && vol.content.thumb.height === 36 && vol.content.valueText === "45%" && vol.content.sliderItem.value === 45, "volume row: 36 px thumb, 45%")
        check(bri.content.valueText === "70%" && bri.content.sliderItem.enabled === false, "brightness row reads the backlight (70%); the slider is disabled without powerdevil here")
        check(bat.content.bigNumber.text === "87%" && bat.content.bigNumber.font.pixelSize === 28 && bat.content.bigNumber.font.weight === Font.Bold && bat.content.segmented.visible && bat.content.segmented.current === 1, "battery card: 87% in Inter 28/700, segmented control on Balanced")
        var f = settingsPane.footerItem
        check(f.networkText.text === "wlp2s0 · 10.0.0.5" && f.speedLabel.text === "↓ 80 kB/s  ↑ 12 kB/s" && f.pill.visible && f.pencil.visible && f.height === 44, "footer: 'wlp2s0 · 10.0.0.5' + live ↓ ↑, pencil, 'All settings' pill (44 px)")
        check(dnd.content.title === "Do Not Disturb" && dnd.content.on === false, "Do Not Disturb tile off")
        grab(pane.mainItem, "/out/quicksettings-open-end.png")
        stage2b.start()
    } }
    Timer { id: stage2b; interval: 400; onTriggered: {
        root.applyStatus(h.status)   // the legacy line again: night light unknown -> its tile leaves the grid (8 tiles) and edit mode must still show it, dimmed
        check(root.shownTiles.length === 8 && root.tileRect("nightlight") === null, "a state without KWin's night light hides the tile (8 tiles)")
        root.editing = true
        var wifi = tileItem("wifi"), bt = tileItem("bluetooth")
        check(wifi.frame.visible && wifi.editBar.visible && wifi.content.enabled === false, "edit mode: accent frame + size/remove controls, the tile's own controls inert")
        check(tileItem("nightlight").visible === true && near(tileItem("nightlight").opacity, 0.5) && root.shownTiles.length === 9, "edit mode shows every enabled tile, the unavailable one dimmed (" + tileItem("nightlight").opacity + ")")
        check(settingsPane.footerItem.pill.visible && settingsPane.footerItem.pencil.visible === false, "footer swaps to the edit controls (Done pill, no pencil)")
        // programmatic drag: Wi-Fi (two columns wide) carried until its CENTRE lies over Bluetooth's slot takes that slot; Bluetooth shifts to the first
        var target = bt.rect
        wifi.dragTo(target.x + target.w / 2 - wifi.width / 2, target.y + target.h / 2 - wifi.height / 2)
        check(root.tiles[0].id === "bluetooth" && root.tiles[1].id === "wifi", "drag reorders live: " + root.tiles.slice(0, 3).map(function (t) { return t.id }).join(","))
        root.dropTiles()
        check(root.cfg.tilesJson.indexOf('[{"id":"bluetooth"') === 0, "drop persists the order in tilesJson")
        root.saveTiles(Status.toggleTileSize(root.tiles, "wifi"))
        stage2c.start()
    } }
    Timer { id: stage2c; interval: 400; onTriggered: {
        var c = pane.cardItem, wifi = tileItem("wifi"), bt = tileItem("bluetooth")
        check(wifi.size === "wide" && wifi.rect.w === root.contentWidth && wifi.rect.y === 88 && wifi.rect.x === 0 && bt.rect.x === 0 && bt.rect.y === 0 && bt.rect.w === h.col, "size cycle: Wi-Fi medium -> wide takes its own row (" + wifi.rect.w + " px at y " + wifi.rect.y + "); Bluetooth alone in row 1")
        check(pane.height === c.height + pane.shadow && c.height === root.settingsHeight, "window follows the card while open (instant, no animation): " + pane.height)
        root.saveTiles(Status.setTileEnabled(root.tiles, "screenshot", false))
        check(root.hiddenTiles.length === 4 && root.hiddenTiles.map(function (t) { return t.id }).indexOf("screenshot") >= 0 && tileItem("screenshot").visible === false, "remove: Screenshot leaves the grid (" + root.hiddenTiles.length + " chips: the three optional tiles + Screenshot)")
        stage2d.start()
    } }
    Timer { id: stage2d; interval: 500; onTriggered: {   // the tiles have finished their 160 ms re-flow and the chip row is laid out
        check(root.addRowH > 0 && settingsPane.chips.visible && pane.cardItem.height === root.settingsHeight && pane.height === pane.cardItem.height + pane.shadow, "removed tiles come back as chips under the grid (add row " + root.addRowH + " px); the card and window follow (" + pane.height + ")")
        grab(pane.mainItem, "/out/quicksettings-edit.png")
        stage2e.start()
    } }
    Timer { id: stage2e; interval: 400; onTriggered: {
        root.saveTiles(Status.setTileEnabled(root.tiles, "screenshot", true))
        check(root.hiddenTiles.length === 3 && tileItem("screenshot").visible === true, "add back: Screenshot returns")
        root.saveTiles(Status.defaultTiles())
        check(root.tiles[0].id === "wifi" && root.tiles[0].size === "medium" && root.cfg.tilesJson === Status.tilesJson(Status.defaultTiles()), "reset to default: order, sizes and tilesJson back to the shipped layout")
        root.editing = false
        check(tileItem("wifi").frame.visible === false && tileItem("wifi").content.enabled === true, "done: frames gone, tiles live again")
        root.applyStatus(h.statusJson)   // KWin's night light known again: the tile is back
        check(root.shownTiles.length === 9 && tileItem("nightlight").visible === true && near(tileItem("nightlight").opacity, 1), "night light back in the grid once the probe reports it")
        // volume slider path: coalesced wpctl call + glyph flash
        root.setVolume(60)
        stage3.start()
    } }
    Timer { id: stage3; interval: 400; onTriggered: {
        check(root.st.volume === 60 && volInd.visible === true && volInd.icon === "audio-volume-medium" && tileItem("volume").content.valueText === "60%", "volume set -> state 60, glyph flashes, row reads 60%")
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
        check(c.width === root.settingsWidth && c.width === Kirigami.Units.gridUnit * 36 && pane.width === c.width + pane.shadow + pane.edge, "notification pane is the same 36 gridUnits wide (" + c.width + ")")
        check(notifList.count === 1 && notifList.visible, "history list has the row")
        var row = notifList.itemAtIndex(0)
        check(row && row.summaryText === "Update ready" && row.bodyText.indexOf("revision 5") >= 0, "row reads summary/body roles: " + (row ? row.summaryText : "no row"))
        if (row) {   // the dismiss cross must be reachable with the mouse: its hover area spans the whole row and the button lies inside it
            check(row.height >= 64 && row.appIcon.width === 36 && row.bodyLabel.font.pixelSize === 14, "row " + row.height + " px (>= 64), app icon 36, body 14 px")
            var btn = row.dismissButton, area = row.hoverArea, b = btn.mapToItem(row, 0, 0)
            check(area.x === 0 && area.y === 0 && area.width === row.width && area.height === row.height, "row hover area spans the whole row (" + area.width + "x" + area.height + " of " + row.width + "x" + row.height + ")")
            check(b.x >= 0 && b.x + btn.width <= row.width && b.y >= 0 && b.y + btn.height <= row.height && btn.width >= 24, "dismiss button lies inside the hover area (x " + Math.round(b.x) + " w " + btn.width + " of " + row.width + ")")
            check(typeof btn.hovered === "boolean" && btn.hovered === false && row.rowHovered === false && btn.visible === false, "dismiss button hidden until the row or the button itself is hovered")
            check(row.history === history && row.history !== null, "row.history is the Notifications model (not the delegate's own property)")
        }
        check(root.unread === 0, "opening the pane marks it read")
        check(c.height === root.notifHeight && root.notifHeight <= Math.round(root.screenH * 0.6) && root.notifListH >= 64, "card sized to the history (" + c.height + " px, list " + root.notifListH + "), capped at 60 % of the screen")
        h.notifHeightBefore = root.notifHeight
        root.toggleDnd()
        check(root.dnd === true, "do not disturb on")
        stage6.start()
    } }
    Timer { id: stage6; interval: 500; onTriggered: {
        var until = root.notifSettings.notificationsInhibitedUntil
        check(root.dnd === true && until && until.getTime() > Date.now() + 300 * 24 * 3600 * 1000, "do not disturb persisted after save + live reload (until " + until + ")")
        check(root.notifHeight === h.notifHeightBefore + 52 && pane.cardItem.height === root.notifHeight, "do-not-disturb banner adds one 44 px row (+ 8 gap) to the card (" + root.notifHeight + " px)")
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
        check(root.paneUnits === 40 && root.settingsWidth === Kirigami.Units.gridUnit * 40 && pane.cardItem.width === root.settingsWidth, "large bar: the pane widens to 40 gridUnits (" + root.settingsWidth + " px)")
        check(root.lastSync.indexOf("writeConfig(\"barSize\", \"large\")") > 0 && root.lastSync.indexOf("in.patienceai.fabos.clock") > 0 && root.lastSync.indexOf("in.patienceai.fabos.dock") > 0, "size change queued the clock/dock sync script (Fab OS clock barSize + dock magnify)")
        check(root.lastSync.indexOf("magnification") < 0 && root.lastSync.indexOf("writeConfig(\"magnify\", true)") > 0, "sync writes the shared magnify switch only, never the dock's magnification strength")
        root.cfg.barSize = "small"
        check(root.paneUnits === 32 && pane.cardItem.width === Kirigami.Units.gridUnit * 32, "small bar: 32 gridUnits (" + pane.cardItem.width + " px)")
        root.cfg.barSize = "medium"
        h.startSampling(24)
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
