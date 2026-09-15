import QtQuick
import QtQuick.Window
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// Headless driver for the Fab OS clock. Loaded by one Loader line appended to a COPY of main.qml
// (tests/desktop-applets-qml-test.sh). Checks the one-line text ("ddd d MMM · time"), Inter 700 at the bar size's px
// (12 / 13 / 15), the on-the-minute timer, the date/time format settings and the month popup; renders
// /out/clock-bar.png and prints PASS/FAIL lines + "HARNESS DONE failures=N".
Item {
    id: h
    property var root: null
    property var label: null
    property var popup: null
    property var minuteTimer: null
    property int failures: 0
    property int grabs: 0
    property var backdrops: ({})
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    Component { id: backdrop; Rectangle { z: -1; anchors.fill: parent; color: Kirigami.Theme.backgroundColor } }
    function grab(item, file) { h.grabs++; if (!h.backdrops[file]) h.backdrops[file] = backdrop.createObject(item); item.grabToImage(function(r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); h.grabs-- }) }
    onMinuteTimerChanged: if (root && label && popup && minuteTimer) startTimer.start()
    Timer { id: startTimer; interval: 700; onTriggered: h.stage1() }

    function stage1() {
        var win = root.Window.window
        if (win) { win.width = 240; win.height = 36 }   // the 2-gridUnit top bar
        var now = new Date()
        var expected = Qt.formatDate(now, "ddd d MMM") + " · " + Qt.formatTime(now, root.timeFormat)
        console.log("INFO clock text: '" + root.text + "' (expected '" + expected + "'), locale short format '" + Qt.locale().timeFormat(Locale.ShortFormat) + "' -> '" + root.autoTimeFormat + "', px " + root.px + ", timer interval " + minuteTimer.interval)
        check(root.text === expected || root.text === Qt.formatDate(new Date(), "ddd d MMM") + " · " + Qt.formatTime(new Date(), root.timeFormat), "one line: day, date and time — " + root.text)
        check(/^[A-Za-z]{3} \d{1,2} [A-Za-z]{3} · \d{1,2}[:.]\d{2}( ?[AaPp][Mm])?$/.test(root.text), "format ddd d MMM · time, no seconds")
        check(!/s/.test(root.timeFormat) && root.timeFormat.length >= 4, "the region's short time format without seconds: '" + root.timeFormat + "'")
        check(root.barSize === "medium" && root.px === 13 && label.font.pixelSize === 13, "medium bar size -> Inter 13 px (the indicators' table)")
        check(label.font.family === "Inter" && label.font.weight === Font.Bold, "Inter 700")
        check(Math.abs(root.wantedWidth - (label.implicitWidth + 12)) < 1 && label.implicitWidth > 60, "applet width follows the text (" + root.wantedWidth + " px for " + label.implicitWidth.toFixed(1) + " px of text)")
        check(minuteTimer.running && !minuteTimer.repeat && minuteTimer.interval > 0 && minuteTimer.interval <= 60030, "ticks once per minute, re-armed to the next minute boundary (" + minuteTimer.interval + " ms to go)")
        check(popup.visible === false && root.popupOpen === false, "month popup closed at start")
        stage1b.start()
    }
    Timer { id: stage1b; interval: 400; onTriggered: {   // the window has taken its bar-like size: render, then change the size
        console.log("INFO render box " + root.width + "x" + root.height)
        grab(root, "/out/clock-bar.png")
        root.cfg.barSize = "small"
        stage2.start()
    } }
    Timer { id: stage2; interval: 300; onTriggered: {
        check(root.px === 12 && label.font.pixelSize === 12, "small -> 12 px")
        root.cfg.barSize = "large"
        stage3.start()
    } }
    Timer { id: stage3; interval: 300; onTriggered: {
        check(root.px === 15 && label.font.pixelSize === 15, "large -> 15 px")
        root.cfg.barSize = "medium"
        root.cfg.timeFormat = "24h"
        stage4.start()
    } }
    Timer { id: stage4; interval: 300; onTriggered: {
        check(root.px === 13 && /\d{2}:\d{2}$/.test(root.text), "24-hour setting: " + root.text)
        root.cfg.timeFormat = "12h"
        stage5.start()
    } }
    Timer { id: stage5; interval: 300; onTriggered: {
        check(/\d{1,2}:\d{2} (AM|PM|am|pm)$/.test(root.text), "12-hour setting: " + root.text)
        root.cfg.timeFormat = "auto"
        root.cfg.showDate = false
        stage6.start()
    } }
    Timer { id: stage6; interval: 300; onTriggered: {
        check(root.text.indexOf(" · ") < 0 && root.text === Qt.formatTime(new Date(), root.timeFormat), "date off: time only (" + root.text + ")")
        root.cfg.showDate = true
        root.cfg.dateFormat = "dddd d MMMM"
        stage7.start()
    } }
    Timer { id: stage7; interval: 300; onTriggered: {
        check(root.text.indexOf(Qt.formatDate(new Date(), "dddd d MMMM") + " · ") === 0, "custom date format: " + root.text)
        root.cfg.dateFormat = "ddd d MMM"
        root.togglePopup()
        stage8.start()
    } }
    Timer { id: stage8; interval: 500; onTriggered: {
        check(popup.visible === true && root.popupOpen === true, "click opens the month popup")
        check(popup.mainItem.width === Kirigami.Units.gridUnit * 22 && popup.mainItem.height === Kirigami.Units.gridUnit * 21, "popup 22 x 21 gridUnits (" + popup.mainItem.width + "x" + popup.mainItem.height + ")")
        root.togglePopup()
        stage9.start()
    } }
    Timer { id: stage9; interval: 300; onTriggered: {
        check(popup.visible === false, "second click closes it")
        done.start()
    } }
    Timer { id: done; interval: 500; repeat: true; onTriggered: {
        if (h.grabs > 0) return
        console.log("HARNESS DONE failures=" + h.failures)
        stop(); killer.connectSource("pkill -x plasmawindowed")
    } }
    P5Support.DataSource { id: killer; engine: "executable" }
}
