import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "agent.js" as Agent

// Enlarged view of a generated image, opened by a tap on its card: the picture fitted on a dark scrim (a photo viewer's
// scrim is dark in both colour schemes; every word on it is white), the prompt and provider above it, one control row
// under it — Save as · Copy image · Open in Fab Photos · Set as wallpaper · Regenerate · Close (Esc). main.qml owns the
// window (a PlasmaCore.Dialog, 80 % of the screen) and runs the shell actions through its executable DataSource; this
// item only asks (`action`) and shows the outcome (`outcome`). Every control that depends on a binary degrades: missing
// = disabled at 40 % with a tooltip naming what is missing (wl-clipboard for Copy image, Fab Photos / xdg-open for
// Open, plasma-apply-wallpaperimage for the wallpaper); Save as uses the QtQuick.Dialogs file dialog when the module
// loads and otherwise copies into ~/Pictures and says where.
FocusScope {
    id: v
    property string path: ""
    property string prompt: ""
    property string provider: ""
    property var bins: ({})                   // {"wl-copy": true, "gwenview": true, ...} — the probe main.qml runs when the viewer opens
    property bool binsKnown: false
    property string toast: ""
    readonly property bool hasCopy: !!bins["wl-copy"]
    readonly property bool hasOpen: !!bins["gwenview"] || !!bins["xdg-open"]
    readonly property bool hasWallpaper: !!bins["plasma-apply-wallpaperimage"]
    readonly property bool fileDialogReady: saveLoader.status === Loader.Ready && saveLoader.item !== null
    readonly property bool imageReady: big.status === Image.Ready
    readonly property string sizeLabel: imageReady ? Math.round(big.implicitWidth) + " × " + Math.round(big.implicitHeight) : ""   // implicit = the decoded size
    readonly property alias saveBtn: saveBtn
    readonly property alias copyBtn: copyBtn
    readonly property alias openBtn: openBtn
    readonly property alias wallBtn: wallBtn
    readonly property alias regenBtn: regenBtn
    readonly property alias closeBtn: closeBtn
    readonly property alias big: big
    signal closeRequested()
    signal action(string kind, string command)   // main.qml runs `command` (POSIX sh) and calls outcome(kind, code, stdout)
    signal regenerate()

    focus: true
    Keys.onEscapePressed: (event) => { v.closeRequested(); event.accepted = true }
    function flash(s) { v.toast = s; toastTimer.restart() }
    Timer { id: toastTimer; interval: 2400; onTriggered: v.toast = "" }

    // ---- actions (the shell runs in main.qml; the outcome comes back below)
    function saveAs() {
        if (v.fileDialogReady) {
            var up = v.path.replace(/\/[^\/]+\/[^\/]+$/, "")              // /home/u/Pictures/Fab OS/x.png -> /home/u/Pictures
            var dir = up.length && up !== v.path ? up : v.path.replace(/\/[^\/]+$/, "")
            saveLoader.item.currentFolder = Agent.fileUrl(dir)
            saveLoader.item.selectedFile = Agent.fileUrl(dir + "/" + Agent.basename(v.path))
            saveLoader.item.open()
        } else v.saveCopy()
    }
    function saveCopy() { v.action("vsave", Agent.saveCopyCommand(v.path)) }
    function saveTo(dest) { if (dest.length) v.action("vsave", Agent.copyToCommand(v.path, dest)) }
    function copyImage() { if (v.hasCopy) v.action("vcopy", Agent.copyImageCommand(v.path)) }
    function openPhotos() { var c = Agent.openImageCommand(v.path, v.bins); if (c.length) v.action("vopen", c) }
    function setWallpaper() { if (v.hasWallpaper) v.action("vwall", Agent.wallpaperCommand(v.path)) }
    function outcome(kind, code, out) {
        var line = Agent.lastLine(out)
        switch (kind) {
        case "vsave": v.flash(code === 0 && line.length ? "Saved to " + line : "Could not save the image"); break
        case "vcopy": v.flash(code === 0 ? "Image copied" : "Could not copy the image"); break
        case "vopen": v.flash(code === 0 ? (line === "gwenview" ? "Opened in Fab Photos" : "Opened in your image viewer") : "Could not open the image"); break
        case "vwall": v.flash(code === 0 ? "Wallpaper set" : "Could not set the wallpaper"); break
        }
    }

    Loader {   // the QtQuick.Dialogs file dialog, optional (see SaveDialog.qml)
        id: saveLoader
        source: "SaveDialog.qml"
        onLoaded: item.chosen.connect(v.saveTo)
    }

    Rectangle {   // scrim
        anchors.fill: parent
        radius: 24
        color: Qt.rgba(0.04, 0.05, 0.08, 0.96)
        border.color: Qt.rgba(1, 1, 1, 0.08); border.width: 1
    }
    MouseArea { anchors.fill: parent; onClicked: v.closeRequested() }   // a click on the scrim closes; header, picture and controls swallow theirs
    MouseArea { anchors.fill: head }                                     // (declared after the scrim's area, so above it; not inside the layouts)
    MouseArea { anchors.fill: controls }

    RowLayout {   // header: prompt + provider, Close
        id: head
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 20; anchors.bottomMargin: 0
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text { Layout.fillWidth: true; text: v.prompt.length ? v.prompt : Agent.basename(v.path); color: "white"; opacity: 0.92; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.Medium; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
            Text { Layout.fillWidth: true; text: [Agent.imageProviderLabel(v.provider), v.sizeLabel, Agent.basename(v.path)].filter(function (x) { return x.length }).join(" · "); color: "white"; opacity: 0.55; font.family: "Inter"; font.pixelSize: 12; elide: Text.ElideMiddle }
        }
        IconButton { id: closeBtn; icon: "window-close"; tip: "Close (Esc)"; size: 36; iconSize: 20; glyphColor: "white"; onClicked: v.closeRequested() }
    }

    Item {   // the picture, fitted
        id: stage
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: head.bottom; anchors.bottom: controls.top
        anchors.margins: 20; anchors.topMargin: 14; anchors.bottomMargin: 14
        Image {
            id: big
            anchors.fill: parent
            source: v.path.length ? Agent.fileUrl(v.path) : ""
            asynchronous: true                     // decoded at the file's size (a sourceSize cap would scale a small file UP)
            autoTransform: true
            fillMode: Image.PreserveAspectFit
            smooth: true
            opacity: status === Image.Ready ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
            MouseArea { anchors.fill: parent }     // a click on the picture stays here
        }
        Spinner { anchors.centerIn: parent; width: 28; height: 28; color: "white"; visible: big.status === Image.Loading }
        RowLayout {
            anchors.centerIn: parent
            visible: big.status === Image.Error
            spacing: 8
            Kirigami.Icon { Layout.preferredWidth: 22; Layout.preferredHeight: 22; source: "image-missing"; isMask: true; color: "white" }
            Text { text: "This image is no longer at " + v.path; color: "white"; opacity: 0.8; font.family: "Inter"; font.pixelSize: 13 }
        }
    }

    Text {   // outcome toast, above the controls
        anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: controls.top; anchors.bottomMargin: 10
        text: v.toast
        visible: opacity > 0
        opacity: v.toast.length ? 0.9 : 0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        color: "white"; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium
    }

    // ---- control row: pill buttons (radius 12, white @ 10 %, 18 % on hover); a control whose binary is missing is
    // disabled at 40 % and its tooltip says what is missing
    component ViewerButton: Item {
        id: b
        property string icon: ""
        property string label: ""
        property string tip: ""
        property bool active: true
        property string reason: ""                 // tooltip when !active
        signal clicked()
        implicitWidth: row.implicitWidth + 28; implicitHeight: 36
        opacity: active ? 1.0 : 0.4
        Behavior on opacity { NumberAnimation { duration: 160 } }
        Rectangle {
            anchors.fill: parent; radius: 12
            color: Qt.rgba(1, 1, 1, b.active && bma.pressed ? 0.26 : (b.active && bma.containsMouse ? 0.18 : 0.10))
            Behavior on color { ColorAnimation { duration: 140 } }
        }
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 8
            Kirigami.Icon { width: 18; height: 18; anchors.verticalCenter: parent.verticalCenter; source: b.icon; fallback: "image-x-generic"; isMask: true; color: "white" }
            Text { anchors.verticalCenter: parent.verticalCenter; text: b.label; color: "white"; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium }
        }
        MouseArea { id: bma; anchors.fill: parent; hoverEnabled: true; cursorShape: b.active ? Qt.PointingHandCursor : Qt.ArrowCursor; onClicked: if (b.active) b.clicked() }
        HoverHandler { id: bh }
        QQC2.ToolTip.visible: bh.hovered && (b.active ? b.tip.length > 0 : b.reason.length > 0)
        QQC2.ToolTip.text: b.active ? b.tip : b.reason
        QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
    }
    Flow {
        id: controls
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: 20; anchors.topMargin: 0
        spacing: 8
        ViewerButton { id: saveBtn; icon: "document-save-as"; label: "Save as"; active: v.imageReady
                       tip: v.fileDialogReady ? "Save a copy where you choose" : "Save a copy in your Pictures folder"; reason: "Waiting for the image"; onClicked: v.saveAs() }
        ViewerButton { id: copyBtn; icon: "edit-copy"; label: "Copy image"; active: v.hasCopy && v.imageReady
                       tip: "Copy the picture to the clipboard"; reason: v.binsKnown ? "Copying needs wl-clipboard (wl-copy), which is not installed" : "Checking what this machine can do…"; onClicked: v.copyImage() }
        ViewerButton { id: openBtn; icon: "viewimage"; label: "Open in Fab Photos"; active: v.hasOpen
                       tip: v.bins["gwenview"] ? "Open the file in Fab Photos" : "Open the file in your image viewer"; reason: v.binsKnown ? "Fab Photos (gwenview) is not installed" : "Checking what this machine can do…"; onClicked: v.openPhotos() }
        ViewerButton { id: wallBtn; icon: "preferences-desktop-wallpaper"; label: "Set as wallpaper"; active: v.hasWallpaper && v.imageReady
                       tip: "Use this picture as the desktop wallpaper"; reason: v.binsKnown ? "plasma-apply-wallpaperimage is not available on this machine" : "Checking what this machine can do…"; onClicked: v.setWallpaper() }
        ViewerButton { id: regenBtn; icon: "view-refresh"; label: "Regenerate"; tip: "Ask for a new picture from the same prompt"; onClicked: v.regenerate() }
        ViewerButton { icon: "window-close"; label: "Close"; tip: "Esc"; onClicked: v.closeRequested() }
    }
}
