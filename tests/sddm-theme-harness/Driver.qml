// Render driver for tests/sddm-theme-test.sh. A copy of the theme gets ONE Loader line appended to Main.qml that hands the
// root item and a few ids to this file (the theme itself is untouched). It runs inside sddm-greeter-qt6 --test-mode
// (offscreen), so `sddm`, `userModel`, `sessionModel`, `keyboard` and `config` are the greeter's real objects; only the
// login round-trip is missing (test mode has no daemon socket), so the wrong-password feedback is staged by hand.
// Stage renders grab the theme root at a laptop panel size (1366x768, wider than test mode's 800x800 window); the sheets
// (session / power) are Popups, which live in the window overlay, so those two grab the window's content item at the
// window's own size.
import QtQuick

Item {
    id: drv
    property Item rootItem
    property Item passwordField
    property Item errorLabel
    property var shakeAnim
    property var sessionSheet
    property var powerSheet
    property string outDir: "/work/build/sddm-theme-test"
    property int w: 1366
    property int h: 768

    function save(res, name) { var ok = res.saveToFile(outDir + "/" + name + ".png"); console.log("HARNESS saved " + name + " " + ok) }
    function grab(name, next) { rootItem.grabToImage(function(res) { save(res, name); next() }) }
    function grabWindow(name, sheet, next) {
        var win = rootItem.Window.window
        console.log("HARNESS sheet " + name + " opened=" + sheet.opened + " visible=" + sheet.visible + " geo=" + sheet.x + "," + sheet.y + " " + sheet.width + "x" + sheet.height
                    + " window=" + (win ? win.width + "x" + win.height : "none"))
        if (!win) { grab(name, next); return }
        win.contentItem.grabToImage(function(res) { save(res, name); next() })
    }
    property var pending: []
    Timer { id: pause; interval: 500; onTriggered: { var f = drv.pending.shift(); if (f) f() } }
    function after(ms, f) { pending.push(f); pause.interval = ms; pause.restart() }

    Timer {
        interval: 1800; running: true
        onTriggered: {
            var win = rootItem.Window.window
            var ww = win ? win.width : drv.w, wh = win ? win.height : drv.h
            rootItem.width = drv.w; rootItem.height = drv.h
            console.log("HARNESS users=" + userModel.count + " sessions=" + sessionModel.count + " stage=" + rootItem.stage + " window=" + ww + "x" + wh)
            after(400, function() { grab("greeter-users", function() {
                if (rootItem.stage === "users") rootItem.pickUser(0)
                after(400, function() { grab("greeter-password", function() {
                    passwordField.text = "correct horse"; passwordField.reveal = true
                    after(300, function() { grab("greeter-password-reveal", function() {
                        passwordField.reveal = false; passwordField.text = ""
                        errorLabel.text = qsTr("Wrong password. Try again."); shakeAnim.restart()
                        after(120, function() { grab("greeter-error", function() {
                            // a PAM information message (pam_fprintd's prompt while a fingerprint reader with an enrolled
                            // finger is asked first, 1.0-8): the same line in the neutral hint style, not red
                            errorLabel.info = true; errorLabel.text = "Place your finger on the fingerprint reader"
                            after(120, function() { grab("greeter-info", function() {
                            errorLabel.text = ""
                            rootItem.stage = "username"
                            after(400, function() { grab("greeter-username", function() {
                                // sheets: back to the window's size so the overlay grab shows them
                                rootItem.back(); rootItem.width = ww; rootItem.height = wh
                                powerSheet.open()
                                after(500, function() { grabWindow("greeter-power", powerSheet, function() {
                                    powerSheet.close(); sessionSheet.open()
                                    after(500, function() { grabWindow("greeter-session", sessionSheet, function() {
                                        sessionSheet.close()
                                        console.log("HARNESS done"); Qt.quit()
                                    }) })
                                }) })
                            }) })
                            }) })
                        }) })
                    }) })
                }) })
            }) })
        }
    }
}
