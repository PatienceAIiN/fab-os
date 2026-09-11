import QtQuick 2.15
Rectangle {
    id: root; color: "#0E1116"
    property int stage
    onStageChanged: if (stage == 1) intro.running = true
    Image { anchors.fill: parent; source: "images/background.png"; fillMode: Image.PreserveAspectCrop; opacity: 0.85 }
    Item {
        anchors.centerIn: parent; width: 160; height: 240
        Image { id: mark; source: "images/mark.png"; width: 128; height: 128; anchors.horizontalCenter: parent.horizontalCenter; smooth: true
                RotationAnimation on rotation { from: 0; to: 360; duration: 2400; loops: Animation.Infinite; running: true } }
        Image { source: "images/wordmark.png"; anchors { top: mark.bottom; topMargin: 28; horizontalCenter: parent.horizontalCenter } height: 40; fillMode: Image.PreserveAspectFit; smooth: true }
        Rectangle { id: bar; width: 220; height: 3; radius: 2; color: "#2A313B"; anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            Rectangle { height: parent.height; radius: 2; color: "#6E9BFF"; width: (root.stage / 6) * parent.width
                        Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.InOutQuad } } } }
    }
    OpacityAnimator { id: intro; target: root; from: 0; to: 1; duration: 200 }
}
