import QtQuick 2.15
// Fab OS session splash (KSplash). Background: the 3840x2160 render, PreserveAspectCrop, never stretched.
// Mark: the vector fabos.svg rasterised at 2x its on-screen size; wordmark: live Inter text. Nothing is a small bitmap
// scaled up, so the splash is as sharp on a 1366x768 laptop as on a 4K panel.
Rectangle {
    id: root; color: "#0E1116"
    property int stage
    onStageChanged: if (stage == 1) intro.running = true
    Image { anchors.fill: parent; source: "images/background.png"; fillMode: Image.PreserveAspectCrop; opacity: 0.85; smooth: true; mipmap: true }
    Item {
        anchors.centerIn: parent; width: 220; height: 250
        Image { id: mark; source: "images/mark.svg"; width: 128; height: 128; sourceSize: Qt.size(256, 256); anchors.horizontalCenter: parent.horizontalCenter; smooth: true; mipmap: true
                RotationAnimation on rotation { from: 0; to: 360; duration: 2400; loops: Animation.Infinite; running: true } }
        Column {
            id: wordmark; anchors { top: mark.bottom; topMargin: 24; horizontalCenter: parent.horizontalCenter } spacing: 2
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "@DISTRO_NAME@"; color: "white"; font.family: "Inter"; font.pixelSize: 30; font.weight: Font.Bold }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "by @VENDOR_NAME@"; color: "#C9D1DC"; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium }
        }
        Rectangle { id: bar; width: 220; height: 3; radius: 2; color: "#2A313B"; anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            Rectangle { height: parent.height; radius: 2; color: "#6E9BFF"; width: (root.stage / 6) * parent.width
                        Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } } } }
    }
    OpacityAnimator { id: intro; target: root; from: 0; to: 1; duration: 200 }
}
