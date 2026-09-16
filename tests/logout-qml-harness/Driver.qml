import QtQuick

// Headless driver for the Fab OS leave screen. tests/logout-screen-test.sh copies the look-and-feel package into a temp
// package (id in.patienceai.fabos.leavetest), appends ONE Loader line to that COPY of Logout.qml which loads this file
// and hands over the root item and its ids, then runs the REAL ksmserver-logout-greeter (greeter-run.sh: offscreen or
// inside a virtual kwin_wayland, PLASMA_SESSION_GUI_TEST=1 so maysd is true without logind) and prompts a shutdown over
// D-Bus. Two situations are driven, told apart by what TasksModel delivers:
//   apps     offscreen there is no Wayland, TasksModel stays empty and the systemd-unit fallback runs through the real
//            session-apps.sh with a stub systemctl and stub .desktop entries on the harness paths (confirmLogoutCountdown=12
//            in the harness ksmserverrc proves the config path);
//   windows  under kwin_wayland the greeter sees the real window list (our .desktop entry grants it the protocol): the
//            rows, the app names and the modified marker in a title are checked against testwin.py's two windows.
// Prints PASS/FAIL lines, PATH <apps|windows>, WIN rows, renders /out/logout-<path>.png and /out/logout-unsaved.png,
// then prints "HARNESS DONE failures=N" (greeter-run.sh kills the greeter once it sees that marker).
Item {
    id: h
    property var root: null
    property var tasksModel: null
    property var fallbackModel: null
    property var card: null
    property var titleLabel: null
    property var subtitleLabel: null
    property var countdownLabel: null
    property var primaryButton: null
    property var cancelButton: null
    property var appsList: null
    property var appsHeader: null
    property var emptyLabel: null
    property var unsavedWarning: null
    property var unavailableLabel: null
    property var fallbackNote: null
    property var allOptions: null
    property var actions: null
    property var footnote: null
    property int failures: 0
    property int stage: 0
    property string path: ""
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function grab(item, file, next) { item.grabToImage(function (r) { r.saveToFile(file); console.log("RENDER " + file + " " + Math.round(item.width) + "x" + Math.round(item.height)); next() }) }
    function rows() { const out = []; for (let i = 0; i < fallbackModel.count; i++) { const r = fallbackModel.get(i); out.push(r.display + "/" + r.decoration + "/" + r.count) } return out.join(",") }
    function windows() {   // the titles TasksModel delivers (Qt::DisplayRole), in list order
        const out = []
        for (let i = 0; i < tasksModel.count; i++) out.push({ title: String(tasksModel.data(tasksModel.index(i, 0), Qt.DisplayRole) || "") })
        return out
    }

    onFootnoteChanged: if (root && tasksModel && fallbackModel && card && titleLabel && subtitleLabel && countdownLabel && primaryButton && cancelButton && appsList && appsHeader && emptyLabel && unsavedWarning && unavailableLabel && fallbackNote && allOptions && actions && footnote) waitSettled.start()

    // stage 0: wait until the screen has settled (900 ms) and the helper answered, at most 6 s
    Timer {
        id: waitSettled
        interval: 200; repeat: true
        property int ticks: 0
        onTriggered: { ticks++; if ((root.settled && root.helperDone) || ticks > 30) { stop(); h.run() } }
    }

    function run() {
        // the greeter's contract
        const signals = ["logoutRequested", "haltRequested", "haltUpdateRequested", "suspendRequested", "rebootRequested", "rebootRequested2", "rebootUpdateRequested", "cancelRequested", "lockScreenRequested", "cancelSoftwareUpdateRequested"]
        let missing = []; for (let i = 0; i < signals.length; i++) if (typeof root[signals[i]] !== "function") missing.push(signals[i])
        check(missing.length === 0, "root declares the 10 greeter signals" + (missing.length ? " (missing " + missing.join(",") + ")" : ""))
        check(root.mode === 2 && !root.showAll, "promptShutDown gives mode 2 (shut down), not the all-options grid (mode=" + root.mode + ")")
        check(titleLabel.text === "Shut down", "title reads 'Shut down' (got '" + titleLabel.text + "')")
        check(subtitleLabel.text.indexOf("asks each app to save its work before turning off") > 0, "subtitle: apps are asked to save before turning off")
        check(root.width >= 640 && root.height >= 400 && card.width <= root.width - 48 && card.height <= root.height - 32, "card fits the screen (" + Math.round(card.width) + "x" + Math.round(card.height) + " in " + root.width + "x" + root.height + ")")
        check(!allOptions.visible && actions.visible, "single-action layout: Cancel + primary row, no tile grid")
        check(footnote.text.indexOf("Ask before closing") === 0 && footnote.text.indexOf("Fab Settings") > 0, "footnote names 'Ask before closing' and Fab Settings")
        check(root.settled && root.helperDone, "settled + helper answered within 6 s (settled=" + root.settled + " helper=" + root.helperDone + ")")
        check(root.canShutdown && primaryButton.visible && primaryButton.text === "Shut down now" && primaryButton.focus && !unavailableLabel.visible,
              "maysd=true: 'Shut down now' primary, focused, no 'not allowed' text (canShutdown=" + root.canShutdown + " activeFocus=" + primaryButton.activeFocus + ")")
        check(cancelButton.visible && cancelButton.text === "Cancel", "Cancel button present")

        // the unsaved heuristic (titles editors really produce)
        const yes = ["Untitled * — Kate", "*notes.txt — Editor", "index.html [modified] — App", "● app.ts - Code", "•draft.md", "report.odt (modified)", "main.c*", "draft.txt* — Test Editor"]
        const no = ["Mozilla Firefox", "C++ pointers — Docs", "5 * 3 — Calculator", "Pictures — Dolphin", "", "Fab Settings", "notes.txt — Test Editor"]
        let bad = []
        for (let i = 0; i < yes.length; i++) if (!root.looksUnsaved(yes[i])) bad.push("miss:" + yes[i])
        for (let i = 0; i < no.length; i++) if (root.looksUnsaved(no[i])) bad.push("false:" + no[i])
        check(bad.length === 0, "unsaved heuristic: " + yes.length + " modified titles flagged, " + no.length + " plain titles not" + (bad.length ? " (" + bad.join("; ") + ")" : ""))

        h.path = root.appsSource
        console.log("PATH " + h.path)
        if (root.useTasks) {
            // the real window list (virtual kwin_wayland): testwin.py shows "draft.txt* — Test Editor" and "notes.txt — Test Editor"
            const w = windows()
            for (let i = 0; i < w.length; i++) console.log("WIN " + w[i].title + " | unsaved=" + root.looksUnsaved(w[i].title))
            let modified = 0, clean = 0
            for (let i = 0; i < w.length; i++) { if (w[i].title.indexOf("draft.txt*") === 0) modified++; if (w[i].title.indexOf("notes.txt") === 0 && !root.looksUnsaved(w[i].title)) clean++ }
            check(root.windowCount >= 2 && modified === 1 && clean === 1, "window list has both test windows (" + root.windowCount + " listed: " + w.map(x => x.title).join(" ; ") + ")")
            check(root.unsavedCount >= 1, "the modified title is counted as unsaved (unsavedCount=" + root.unsavedCount + ")")
            check(appsList.visible && appsList.count === tasksModel.count && !emptyLabel.visible, "list shows the rows, empty state hidden")
            check(appsHeader.text === "Open apps · " + root.windowCount, "header counts the windows ('" + appsHeader.text + "')")
            check(!fallbackNote.visible, "no fallback note when titles are visible")
            check(unsavedWarning.visible && !root.countdownActive && countdownLabel.text.indexOf("paused") > 0, "unsaved work: warning shown, countdown paused ('" + countdownLabel.text + "')")
        } else {
            // the systemd-unit fallback through the real helper (stub systemctl + stub .desktop files on the harness paths)
            check(tasksModel.count === 0 && root.windowCount === 0, "no Wayland window list offscreen (TasksModel empty, count=" + tasksModel.count + ")")
            check(root.useFallback && root.appsSource === "apps", "fallback path active (source=" + root.appsSource + ")")
            const r = rows()
            check(fallbackModel.count === 3, "helper listed 3 applications from 5 units (" + r + ")")
            check(r.indexOf("Test Editor/test-editor/2") >= 0, "two kate services collapse into one row with 2 instances, name + icon from the .desktop entry")
            check(r.indexOf("Test Browser/test-browser/1") >= 0, "firefox scope: '-<pid>' stripped, entry found")
            check(r.indexOf("com.example.no-desktop/application-x-executable/1") >= 0, "unit with no .desktop entry: id shown, generic icon, \\x2d unescaped")
            check(r.indexOf("discover") < 0, "autostart unit (no window) skipped")
            check(appsList.count === 3 && appsList.visible && !emptyLabel.visible, "list shows the 3 rows, empty state hidden")
            check(appsHeader.text === "Open apps · 3", "header counts them ('" + appsHeader.text + "')")
            check(fallbackNote.visible, "fallback note says titles are not visible here")
            check(root.countdownSeconds === 12 && root.remaining <= 12 && root.remaining >= 8, "confirmLogoutCountdown=12 read through kreadconfig6 (seconds=" + root.countdownSeconds + " remaining=" + root.remaining + ")")
            check(root.unsavedCount === 0 && !unsavedWarning.visible, "no unsaved flag without window titles")
            check(root.countdownActive && countdownLabel.text.indexOf("Shutting down in ") === 0 && countdownLabel.text.indexOf("Press any key to wait") > 0, "countdown running: '" + countdownLabel.text + "'")
        }

        grab(root, "/out/logout-" + (root.useTasks ? "windows" : "shutdown") + ".png", function () {
            // simulated unsaved state: warning row + chip, countdown held
            root.unsavedCount = 2
            h.stage = 1
            settle.start()
        })
    }
    Timer {
        id: settle
        interval: 250
        onTriggered: {
            if (h.stage === 1) {
                check(unsavedWarning.visible && unsavedWarning.height > 0, "unsaved: warning row appears")
                check(!root.countdownActive, "unsaved: countdown never runs")
                check(countdownLabel.text.indexOf("paused") > 0, "unsaved: countdown text says paused ('" + countdownLabel.text + "')")
                grab(root, "/out/logout-unsaved.png", function () {
                    root.unsavedCount = 0
                    root.hold()
                    check(root.held && !root.countdownActive && countdownLabel.text.indexOf("Take your time") === 0, "held (key / hover): countdown stops for good ('" + countdownLabel.text + "')")
                    console.log("HARNESS DONE failures=" + h.failures)
                })
            }
        }
    }
}
