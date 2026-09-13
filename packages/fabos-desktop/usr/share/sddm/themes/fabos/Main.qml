import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import SddmComponents 2.0

// Fab OS greeter. Resolution-independent by construction: the background is a 3840x2160 render drawn with
// PreserveAspectCrop (never stretched), the mark is the vector fabos.svg rasterised at 2x its on-screen size, and the
// wordmark is live Inter text — nothing here is a fixed-size bitmap that would blur on a Full-HD or HiDPI panel.
Rectangle {
    id: root
    width: 1920; height: 1080
    color: "#0E1116"
    property int sessionIndex: sessionModel.lastIndex
    TextConstants { id: textConstants }

    Image { anchors.fill: parent; source: "background.png"; fillMode: Image.PreserveAspectCrop; smooth: true; mipmap: true; asynchronous: false }
    Rectangle { anchors.fill: parent; color: "#000000"; opacity: 0.25 }

    Connections {
        target: sddm
        function onLoginFailed() { errorText.text = textConstants.loginFailed; password.text = ""; shake.start() }
        function onLoginSucceeded() { errorText.text = "" }
    }

    // clock
    Column {
        anchors { top: parent.top; topMargin: 72; horizontalCenter: parent.horizontalCenter }
        spacing: 4
        Text { id: clock; anchors.horizontalCenter: parent.horizontalCenter; color: "white"; font.family: "Inter"; font.pixelSize: 84; font.weight: Font.Light
               text: Qt.formatTime(new Date(), "hh:mm") }
        Text { anchors.horizontalCenter: parent.horizontalCenter; color: "#C9D1DC"; font.family: "Inter"; font.pixelSize: 18
               text: Qt.formatDate(new Date(), "dddd, d MMMM") }
        Timer { interval: 1000; running: true; repeat: true; onTriggered: clock.text = Qt.formatTime(new Date(), "hh:mm") }
    }

    // login card
    Rectangle {
        id: card
        width: 380; height: 300; radius: 18
        anchors.centerIn: parent; anchors.verticalCenterOffset: 40
        color: "#161B22"; opacity: 0.94; border.color: "#2A313B"; border.width: 1
        SequentialAnimation { id: shake
            NumberAnimation { target: card; property: "anchors.horizontalCenterOffset"; to: -12; duration: 50 }
            NumberAnimation { target: card; property: "anchors.horizontalCenterOffset"; to: 12; duration: 50 }
            NumberAnimation { target: card; property: "anchors.horizontalCenterOffset"; to: 0; duration: 50 } }
        Column {
            anchors { fill: parent; margins: 28 } spacing: 14
            // the Fab OS mark: vector, rendered at 2x the shown size so it is crisp on HiDPI too; no tile behind it
            Image { source: "mark.svg"; width: 56; height: 56; sourceSize: Qt.size(112, 112); anchors.horizontalCenter: parent.horizontalCenter; smooth: true; mipmap: true }
            TextField { id: user; width: parent.width; placeholderText: textConstants.userName; text: userModel.lastUser
                        font.family: "Inter"; font.pixelSize: 15; color: "white"; placeholderTextColor: "#8892A0"
                        background: Rectangle { radius: 10; color: "#0E1116"; border.color: user.activeFocus ? "#6E9BFF" : "#2A313B" }
                        KeyNavigation.tab: password; Keys.onReturnPressed: password.forceActiveFocus() }
            TextField { id: password; width: parent.width; placeholderText: textConstants.password; echoMode: TextInput.Password
                        font.family: "Inter"; font.pixelSize: 15; color: "white"; placeholderTextColor: "#8892A0"; focus: true
                        background: Rectangle { radius: 10; color: "#0E1116"; border.color: password.activeFocus ? "#6E9BFF" : "#2A313B" }
                        Keys.onReturnPressed: sddm.login(user.text, password.text, sessionIndex)
                        Keys.onEnterPressed: sddm.login(user.text, password.text, sessionIndex) }
            Button { width: parent.width; height: 42; text: textConstants.login
                     font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold
                     contentItem: Text { text: parent.text; font: parent.font; color: "#0E1116"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                     background: Rectangle { radius: 10; color: parent.down ? "#5A86E8" : "#6E9BFF" }
                     onClicked: sddm.login(user.text, password.text, sessionIndex) }
            Text { id: errorText; anchors.horizontalCenter: parent.horizontalCenter; color: "#F0655D"; font.family: "Inter"; font.pixelSize: 13 }
        }
    }

    // footer: session chooser + power
    RowLayout {
        anchors { bottom: parent.bottom; bottomMargin: 28; horizontalCenter: parent.horizontalCenter } spacing: 18
        ComboBox { id: session; model: sessionModel; textRole: "name"; currentIndex: sessionModel.lastIndex
                   onActivated: root.sessionIndex = currentIndex; font.family: "Inter"; implicitWidth: 220 }
        Button { text: textConstants.suspend; font.family: "Inter"; visible: sddm.canSuspend; onClicked: sddm.suspend() }
        Button { text: textConstants.reboot; font.family: "Inter"; visible: sddm.canReboot; onClicked: sddm.reboot() }
        Button { text: textConstants.shutdown; font.family: "Inter"; visible: sddm.canPowerOff; onClicked: sddm.powerOff() }
    }
    // wordmark as live text (Inter Bold + Medium), not a bitmap: sharp at any DPI. Names come from brand.conf at build time.
    Row {
        anchors { left: parent.left; bottom: parent.bottom; margins: 28 } spacing: 8; opacity: 0.85
        Text { id: wordmark; text: "@DISTRO_NAME@"; color: "white"; font.family: "Inter"; font.pixelSize: 22; font.weight: Font.Bold }
        Text { text: "by @VENDOR_NAME@"; color: "#C9D1DC"; font.family: "Inter"; font.pixelSize: 14; font.weight: Font.Medium; anchors.baseline: wordmark.baseline }
    }
}
