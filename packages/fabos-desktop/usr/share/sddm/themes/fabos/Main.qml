// @DISTRO_NAME@ greeter (SDDM 0.21, Qt 6). Fedora/GDM-like: a dark blurred backdrop, date + time top-centre, keyboard layout
// and Caps Lock top-right, a centred user tile that reveals the password field, "Not listed?" for a typed username, the
// wordmark bottom-centre, a gear (session) and a power menu bottom-right.
//
// Imports are QUALIFIED on purpose: QtQuick.Controls and SddmComponents both export Button/TextField/ComboBox, and an
// unqualified pair made `Button` resolve to SddmComponents.Button, which has no `background` -> "Cannot assign to
// non-existent property" -> SDDM's red fallback theme (what the 1.0-5 laptop install showed). tests/sddm-theme-test.sh
// loads this file in sddm-greeter-qt6 --test-mode and fails on any QML error.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import SddmComponents as Sddm

Item {
    id: root
    width: 1920; height: 1080
    focus: true

    // ---- tokens (dark greeter; the accent pair comes from brand.conf: #3B6EF5 light / #6E9BFF on dark) ----
    readonly property color accent: "#6E9BFF"
    readonly property color accentStrong: "#3B6EF5"
    readonly property color textPrimary: "#FFFFFF"
    readonly property color textSecondary: "#C9D1DC"
    readonly property color danger: "#F0655D"
    readonly property color surface: Qt.rgba(1, 1, 1, 0.10)
    readonly property color surfaceHover: Qt.rgba(1, 1, 1, 0.16)
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.18)
    readonly property string uiFont: "@UI_FONT@"
    readonly property int rControl: 12
    readonly property int rField: 14
    readonly property int rCard: 20
    readonly property int rPopup: 24

    // ---- state ----
    property int sessionIndex: sessionModel.lastIndex
    property string selectedUser: userModel.lastUser
    property string selectedRealName: ""
    property url selectedIcon: ""
    property int selectedRow: -1
    property bool busy: false
    // "users": the tile(s); "password": tile + password field; "username": typed username + password
    property string stage: userModel.count > 0 ? "users" : "username"
    readonly property bool compact: height < 800

    Sddm.TextConstants { id: textConstants }

    function pickUser(row) {
        selectedRow = row
        var n = tileRepeater.itemAt(row)
        if (n) { selectedUser = n.userName; selectedRealName = n.realName; selectedIcon = n.iconSource }
        stage = "password"; errorText.text = ""
        password.text = ""; password.forceActiveFocus()
    }
    function back() {
        if (userModel.count > 0) { stage = "users"; errorText.text = ""; password.text = ""; tilesFocus.forceActiveFocus() }
    }
    // keyboard focus back to what the stage needs (the tile, the empty username field or the password field), called
    // when a sheet closes or the backdrop is clicked, so Enter keeps opening the password field / signing in after
    // the mouse used the power or the session menu
    function refocus() {
        if (stage === "users") tilesFocus.forceActiveFocus()
        else if (stage === "username" && userField.text.length === 0) userField.forceActiveFocus()
        else password.forceActiveFocus()
    }
    function doLogin() {
        if (busy) return
        var user = stage === "username" ? userField.text.trim() : selectedUser
        if (user.length === 0) { userField.forceActiveFocus(); return }
        busy = true; errorText.text = ""
        sddm.login(user, password.text, sessionIndex)
    }

    Connections {
        target: sddm
        function onLoginFailed() {
            busy = false
            errorText.text = qsTr("Wrong password. Try again.")
            password.text = ""; shake.restart(); password.forceActiveFocus()
        }
        function onLoginSucceeded() { busy = false; errorText.text = "" }
        function onInformationMessage(message) { errorText.text = message }
    }

    Component.onCompleted: {
        // pre-select the last user's tile so Enter alone opens the password field
        for (var i = 0; i < tileRepeater.count; i++) {
            var t = tileRepeater.itemAt(i)
            if (t && t.userName === userModel.lastUser) { selectedRow = i; selectedRealName = t.realName; selectedIcon = t.iconSource }
        }
        if (selectedRow < 0 && tileRepeater.count > 0) { selectedRow = 0; var f = tileRepeater.itemAt(0); selectedUser = f.userName; selectedRealName = f.realName; selectedIcon = f.iconSource }
        if (stage === "users") tilesFocus.forceActiveFocus(); else userField.forceActiveFocus()
        fadeIn.start()
    }
    // smooth hand-off from the (retained) boot splash: the greeter fades in over it
    opacity: 0
    NumberAnimation { id: fadeIn; target: root; property: "opacity"; to: 1; duration: 450; easing.type: Easing.OutCubic }

    // ================================================================ backdrop
    Image {
        anchors.fill: parent
        source: config.background ? Qt.resolvedUrl(config.background) : Qt.resolvedUrl("backdrop.png")
        fillMode: Image.PreserveAspectCrop; smooth: true; mipmap: true; asynchronous: false
    }
    Rectangle { anchors.fill: parent; color: "#000000"; opacity: 0.28 }
    // click on empty space closes popups / returns to the tiles
    MouseArea { anchors.fill: parent; onClicked: { sessionPopup.close(); powerPopup.close(); refocus() } }

    // ================================================================ reusable pieces
    component Glyph: Image {
        property string name
        property real size: 20
        source: name.length > 0 ? "icons/" + name + ".svg" : ""; width: size; height: size
        sourceSize: Qt.size(Math.round(size * 2), Math.round(size * 2)); smooth: true; mipmap: true
        fillMode: Image.PreserveAspectFit
    }
    component IconButton: QQC2.AbstractButton {
        id: ib
        property string glyph
        property real glyphSize: 20
        property color tint: root.surface
        property bool filled: true
        implicitWidth: 40; implicitHeight: 40
        hoverEnabled: true
        focusPolicy: Qt.NoFocus   // pointer target: never steals keyboard focus from the tile / field
        background: Rectangle {
            radius: ib.width / 2
            color: ib.down ? root.surfaceHover : (ib.hovered || ib.visualFocus ? root.surfaceHover : (ib.filled ? ib.tint : "transparent"))
            border.width: ib.visualFocus ? 2 : 0; border.color: root.accent
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        contentItem: Item { Glyph { anchors.centerIn: parent; name: ib.glyph; size: ib.glyphSize; opacity: ib.enabled ? 0.92 : 0.4 } }
        QQC2.ToolTip.visible: hovered && text.length > 0; QQC2.ToolTip.text: text; QQC2.ToolTip.delay: 600
    }
    component Pill: Rectangle {
        property string glyph
        property string label
        property bool clickable: false
        readonly property alias hovered: pillArea.containsMouse
        signal clicked()
        height: 32; radius: 16; color: pillArea.containsMouse && clickable ? root.surfaceHover : root.surface
        implicitWidth: pillRow.implicitWidth + 24
        Row { id: pillRow; anchors.centerIn: parent; spacing: 6
            Glyph { name: glyph; size: 16; anchors.verticalCenter: parent.verticalCenter; opacity: 0.9 }
            Text { text: label; color: root.textPrimary; font { family: root.uiFont; pixelSize: 13; weight: Font.Medium } anchors.verticalCenter: parent.verticalCenter } }
        MouseArea { id: pillArea; anchors.fill: parent; hoverEnabled: true; enabled: clickable; cursorShape: Qt.PointingHandCursor; onClicked: parent.clicked() }
    }
    // round avatar drawn through a Canvas (works on the GL and the software scene graph alike); person glyph when the
    // picture cannot be read (SDDM's greeter runs as `sddm` and homes are 0750, so most ~/.face.icon are unreadable)
    component Avatar: Item {
        id: av
        property url source
        property int size: 96
        property bool ready: false
        width: size; height: size
        Rectangle { anchors.fill: parent; radius: width / 2; color: root.surface; border.width: 1; border.color: root.hairline }
        Canvas {
            id: cv; anchors.fill: parent; antialiasing: true
            Component.onCompleted: if (String(av.source).length > 0) loadImage(av.source)
            onImageLoaded: { av.ready = isImageLoaded(av.source); requestPaint() }
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                if (!av.ready) return
                ctx.save(); ctx.beginPath(); ctx.arc(width / 2, height / 2, width / 2 - 1, 0, 2 * Math.PI); ctx.closePath(); ctx.clip()
                ctx.drawImage(av.source, 0, 0, width, height); ctx.restore()
            }
        }
        Connections { target: av; function onSourceChanged() { av.ready = false; if (String(av.source).length > 0) cv.loadImage(av.source); cv.requestPaint() } }
        Glyph { anchors.centerIn: parent; name: "person"; size: av.size * 0.5; visible: !av.ready; opacity: 0.75 }
    }
    component Field: QQC2.TextField {
        id: f
        property real trailingWidth: 0
        implicitHeight: 48; leftPadding: 16; rightPadding: 12 + trailingWidth; verticalAlignment: TextInput.AlignVCenter
        font { family: root.uiFont; pixelSize: 15 }
        color: root.textPrimary; placeholderTextColor: Qt.rgba(1, 1, 1, 0.55)
        selectionColor: root.accentStrong; selectedTextColor: "#FFFFFF"
        passwordCharacter: "•"
        background: Rectangle {
            radius: root.rField
            color: f.activeFocus ? Qt.rgba(1, 1, 1, 0.14) : Qt.rgba(1, 1, 1, 0.10)
            border.width: f.activeFocus ? 2 : 1; border.color: f.activeFocus ? root.accent : root.hairline
            Behavior on border.color { ColorAnimation { duration: 150 } }
        }
    }
    component LinkText: Text {
        signal clicked()
        color: linkArea.containsMouse ? root.textPrimary : root.textSecondary
        font { family: root.uiFont; pixelSize: 14; weight: Font.Medium; underline: linkArea.containsMouse }
        MouseArea { id: linkArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: parent.clicked() }
    }
    component MenuRow: QQC2.AbstractButton {
        id: mr
        property string glyph
        property bool current: false
        property string detail: ""
        implicitHeight: 44; implicitWidth: Math.max(220, rowContent.implicitWidth + 32)
        hoverEnabled: true
        background: Rectangle { radius: root.rControl; color: mr.down || mr.hovered || mr.visualFocus ? root.surfaceHover : "transparent" }
        contentItem: RowLayout {
            id: rowContent; spacing: 12
            Glyph { name: mr.glyph; size: 20; opacity: 0.9; visible: mr.glyph.length > 0 }
            ColumnLayout { spacing: 0; Layout.fillWidth: true
                Text { text: mr.text; color: root.textPrimary; font { family: root.uiFont; pixelSize: 14; weight: Font.Medium } elide: Text.ElideRight; Layout.fillWidth: true }
                Text { text: mr.detail; visible: mr.detail.length > 0; color: root.textSecondary; font { family: root.uiFont; pixelSize: 12 } elide: Text.ElideRight; Layout.fillWidth: true } }
            Glyph { name: "check"; size: 18; visible: mr.current; opacity: 0.95 }
        }
    }
    component Spinner: Item {
        id: sp
        width: 22; height: 22
        Canvas {
            anchors.fill: parent; antialiasing: true
            onPaint: { var c = getContext("2d"); c.reset(); c.lineWidth = 2.5; c.strokeStyle = root.textPrimary; c.lineCap = "round"
                       c.beginPath(); c.arc(width / 2, height / 2, width / 2 - 2, 0, 1.4 * Math.PI); c.stroke() }
        }
        RotationAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: sp.visible }
    }

    // ================================================================ clock (top centre)
    Column {
        anchors { top: parent.top; topMargin: Math.round(root.height * (compact ? 0.06 : 0.08)); horizontalCenter: parent.horizontalCenter }
        spacing: 2
        Text { id: clockText; anchors.horizontalCenter: parent.horizontalCenter; color: root.textPrimary
               font { family: root.uiFont; pixelSize: compact ? 56 : 72; weight: Font.Light } text: Qt.formatTime(new Date(), "hh:mm") }
        Text { id: dateText; anchors.horizontalCenter: parent.horizontalCenter; color: root.textSecondary
               font { family: root.uiFont; pixelSize: 17; weight: Font.Medium } text: Qt.formatDate(new Date(), "dddd, d MMMM") }
        Timer { interval: 1000; running: true; repeat: true
                onTriggered: { var d = new Date(); clockText.text = Qt.formatTime(d, "hh:mm"); dateText.text = Qt.formatDate(d, "dddd, d MMMM") } }
    }

    // ================================================================ status (top right): keyboard layout + Caps Lock only.
    // No battery / network: the greeter has no UPower or NM access, and we never show made-up data.
    Row {
        anchors { top: parent.top; right: parent.right; margins: 20 }
        spacing: 8
        Pill { glyph: "keyboard_capslock"; label: qsTr("Caps Lock is on"); visible: keyboard.capsLock }
        Pill {
            id: layoutPill
            readonly property var layouts: keyboard.layouts
            readonly property int count: layouts ? layouts.length : 0
            readonly property var current: count > 0 && keyboard.currentLayout >= 0 && keyboard.currentLayout < count ? layouts[keyboard.currentLayout] : null
            glyph: "keyboard"; visible: count > 0; clickable: count > 1
            label: current ? String(current.shortName).toUpperCase() : ""
            onClicked: keyboard.currentLayout = (keyboard.currentLayout + 1) % count
            QQC2.ToolTip.visible: layoutPill.hovered && current !== null; QQC2.ToolTip.text: current ? current.longName : ""; QQC2.ToolTip.delay: 600
        }
    }

    // ================================================================ centre: user tile / password / username
    Item {
        id: centre
        width: Math.min(root.width - 48, 420); height: childrenRect.height
        anchors { horizontalCenter: parent.horizontalCenter; verticalCenter: parent.verticalCenter }
        anchors.verticalCenterOffset: compact ? 30 : 40
        SequentialAnimation { id: shake
            NumberAnimation { target: centre; property: "anchors.horizontalCenterOffset"; to: -14; duration: 45 }
            NumberAnimation { target: centre; property: "anchors.horizontalCenterOffset"; to: 12; duration: 45 }
            NumberAnimation { target: centre; property: "anchors.horizontalCenterOffset"; to: -8; duration: 45 }
            NumberAnimation { target: centre; property: "anchors.horizontalCenterOffset"; to: 5; duration: 45 }
            NumberAnimation { target: centre; property: "anchors.horizontalCenterOffset"; to: 0; duration: 45 } }

        Column {
            id: stack
            width: parent.width; spacing: 14

            // ---- users stage: one tile per account (the last user pre-selected); Enter opens its password field ----
            Item {
                id: tilesFocus
                width: parent.width; height: visible ? tilesRow.height : 0; visible: stage === "users"
                Keys.onPressed: (event) => {
                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { if (selectedRow >= 0) pickUser(selectedRow); event.accepted = true }
                    else if (event.key === Qt.Key_Left && selectedRow > 0) { selectedRow--; event.accepted = true }
                    else if (event.key === Qt.Key_Right && selectedRow < tileRepeater.count - 1) { selectedRow++; event.accepted = true }
                }
                Flow {
                    id: tilesRow
                    // as wide as the tiles need (up to the column), centred; wraps when there are many accounts
                    anchors.horizontalCenter: parent.horizontalCenter; spacing: 12
                    width: Math.min(parent.width, tileRepeater.count * 132 + Math.max(0, tileRepeater.count - 1) * 12)
                    Repeater {
                        id: tileRepeater
                        model: userModel
                        delegate: Item {
                            id: tile
                            required property int index
                            required property string name
                            required property string realName
                            required property url icon
                            readonly property string userName: name
                            readonly property url iconSource: icon
                            readonly property bool selected: index === selectedRow
                            width: Math.min(tilesRow.width, 132); height: 132
                            Rectangle {
                                anchors.fill: parent; radius: root.rCard
                                color: tileArea.containsMouse || tile.selected ? root.surface : "transparent"
                                border.width: tile.selected && tilesFocus.activeFocus ? 2 : 0; border.color: root.accent
                                Behavior on color { ColorAnimation { duration: 120 } }
                            }
                            Column {
                                anchors.centerIn: parent; spacing: 8
                                Avatar { anchors.horizontalCenter: parent.horizontalCenter; size: 72; source: tile.icon }
                                Text { anchors.horizontalCenter: parent.horizontalCenter; width: tile.width - 16; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                       text: tile.realName.length > 0 ? tile.realName : tile.name; color: root.textPrimary
                                       font { family: root.uiFont; pixelSize: 14; weight: Font.Medium } }
                            }
                            MouseArea { id: tileArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: pickUser(tile.index) }
                        }
                    }
                }
            }

            // ---- password stage: the chosen user, then the field ----
            Column {
                width: parent.width; spacing: 10; visible: stage === "password"
                Avatar { anchors.horizontalCenter: parent.horizontalCenter; size: compact ? 84 : 96; source: root.selectedIcon }
                Text { anchors.horizontalCenter: parent.horizontalCenter; width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                       text: root.selectedRealName.length > 0 ? root.selectedRealName : root.selectedUser; color: root.textPrimary
                       font { family: root.uiFont; pixelSize: 22; weight: Font.DemiBold } }
            }

            // ---- username stage ("Not listed?"): typed account name ----
            Column {
                width: parent.width; spacing: 10; visible: stage === "username"
                Avatar { anchors.horizontalCenter: parent.horizontalCenter; size: compact ? 84 : 96; source: "" }
                Field {
                    id: userField
                    width: Math.min(parent.width, 340); anchors.horizontalCenter: parent.horizontalCenter
                    placeholderText: textConstants.userName; inputMethodHints: Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
                    enabled: !busy
                    Keys.onReturnPressed: password.forceActiveFocus(); Keys.onEnterPressed: password.forceActiveFocus()
                    KeyNavigation.tab: password
                }
            }

            // ---- password field with the eye toggle and a submit arrow; shared by both stages ----
            Item {
                width: Math.min(parent.width, 340); height: 48; anchors.horizontalCenter: parent.horizontalCenter
                visible: stage !== "users"
                Field {
                    id: password
                    anchors.fill: parent
                    property bool reveal: false
                    echoMode: reveal ? TextInput.Normal : TextInput.Password
                    placeholderText: textConstants.password
                    inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoAutoUppercase | Qt.ImhNoPredictiveText
                    enabled: !busy
                    trailingWidth: 40 + 40
                    Keys.onReturnPressed: doLogin(); Keys.onEnterPressed: doLogin()
                    Keys.onEscapePressed: back()
                    KeyNavigation.tab: stage === "username" ? userField : password
                }
                Row {
                    anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter } spacing: 2
                    IconButton {
                        glyph: password.reveal ? "visibility_off" : "visibility"; filled: false; implicitWidth: 36; implicitHeight: 36; glyphSize: 20
                        text: password.reveal ? textConstants.hidePasswordPrompt : textConstants.showPasswordPrompt
                        focusPolicy: Qt.NoFocus
                        onClicked: { password.reveal = !password.reveal; password.forceActiveFocus() }
                    }
                    IconButton {
                        glyph: "arrow_forward"; tint: root.accentStrong; implicitWidth: 36; implicitHeight: 36; glyphSize: 20
                        text: textConstants.login; visible: !busy; focusPolicy: Qt.NoFocus
                        onClicked: doLogin()
                    }
                    Item { width: 36; height: 36; visible: busy; Spinner { anchors.centerIn: parent } }
                }
            }

            // ---- error / info line ----
            Text {
                id: errorText
                width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: root.danger; font { family: root.uiFont; pixelSize: 13; weight: Font.Medium }
                visible: text.length > 0
            }

            // ---- links: "Not listed?" / back to the tiles ----
            Row {
                anchors.horizontalCenter: parent.horizontalCenter; spacing: 24
                LinkText { text: qsTr("Not listed?"); visible: stage !== "username"
                           onClicked: { stage = "username"; errorText.text = ""; password.text = ""; userField.text = ""; userField.forceActiveFocus() } }
                LinkText { text: qsTr("Back"); visible: stage !== "users" && userModel.count > 0; onClicked: back() }
            }
        }
    }

    // ================================================================ wordmark (bottom centre): live text, sharp at any DPI
    Row {
        anchors { bottom: parent.bottom; bottomMargin: 28; horizontalCenter: parent.horizontalCenter } spacing: 10; opacity: 0.9
        Image { source: "mark.svg"; width: 26; height: 26; sourceSize: Qt.size(52, 52); smooth: true; mipmap: true; anchors.verticalCenter: parent.verticalCenter }
        Text { id: wordmark; text: "@DISTRO_NAME@"; color: root.textPrimary; font { family: root.uiFont; pixelSize: 20; weight: Font.Bold } anchors.verticalCenter: parent.verticalCenter }
        Text { text: qsTr("by %1").arg("@VENDOR_NAME@"); color: root.textSecondary; font { family: root.uiFont; pixelSize: 13; weight: Font.Medium } anchors.baseline: wordmark.baseline }
    }

    // ================================================================ bottom right: session (gear) + power
    Row {
        id: cornerButtons
        anchors { bottom: parent.bottom; right: parent.right; margins: 24 } spacing: 10
        IconButton { id: gearButton; glyph: "settings"; text: textConstants.session; visible: sessionModel.count > 1 || true
                     onClicked: { powerPopup.close(); sessionPopup.opened ? sessionPopup.close() : sessionPopup.open() } }
        IconButton { id: powerButton; glyph: "power_settings_new"; text: qsTr("Power")
                     visible: sddm.canSuspend || sddm.canReboot || sddm.canPowerOff
                     onClicked: { sessionPopup.close(); powerPopup.opened ? powerPopup.close() : powerPopup.open() } }
    }
    component Sheet: QQC2.Popup {
        id: sheet
        property Item anchorItem
        padding: 8; modal: false; focus: true
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        onClosed: root.refocus()
        // above its button, right-aligned to it (bound to the row's and the button's geometry, so it follows resizes;
        // mapToItem() in a binding would be evaluated once, before the row is laid out)
        x: Math.max(16, Math.min(root.width - width - 16, cornerButtons.x + anchorItem.x + anchorItem.width - width))
        y: cornerButtons.y - height - 10
        background: Rectangle { radius: root.rPopup; color: "#1B2028"; opacity: 0.96; border.width: 1; border.color: root.hairline }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 160; easing.type: Easing.OutCubic } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 100 } }
    }
    Sheet {
        id: sessionPopup
        anchorItem: gearButton
        contentItem: ColumnLayout {
            spacing: 2
            Text { text: textConstants.session; color: root.textSecondary; font { family: root.uiFont; pixelSize: 12; weight: Font.DemiBold; capitalization: Font.AllUppercase }
                   leftPadding: 12; topPadding: 6; bottomPadding: 6 }
            Repeater {
                model: sessionModel
                delegate: MenuRow {
                    required property int index
                    required property string name
                    required property string comment
                    Layout.fillWidth: true
                    text: name; detail: comment; current: index === root.sessionIndex
                    onClicked: { root.sessionIndex = index; sessionPopup.close() }
                }
            }
        }
    }
    Sheet {
        id: powerPopup
        anchorItem: powerButton
        contentItem: ColumnLayout {
            spacing: 2
            MenuRow { Layout.fillWidth: true; glyph: "bedtime"; text: textConstants.suspend; visible: sddm.canSuspend; onClicked: { powerPopup.close(); sddm.suspend() } }
            MenuRow { Layout.fillWidth: true; glyph: "restart_alt"; text: qsTr("Restart"); visible: sddm.canReboot; onClicked: { powerPopup.close(); sddm.reboot() } }
            MenuRow { Layout.fillWidth: true; glyph: "power_settings_new"; text: qsTr("Shut down"); visible: sddm.canPowerOff; onClicked: { powerPopup.close(); sddm.powerOff() } }
        }
    }

    // Escape anywhere returns to the tiles
    Keys.onEscapePressed: back()
}
