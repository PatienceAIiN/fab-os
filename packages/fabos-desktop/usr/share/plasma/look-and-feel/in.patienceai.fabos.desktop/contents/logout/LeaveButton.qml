/*
    @DISTRO_NAME@ leave-screen button. Control radius 12, 48 px tall (a 104 x 96 tile with the icon above the label in
    the all-options grid), filled with the scheme's accent for the one primary action and a translucent surface for
    the rest; a 2 px focus ring; Enter / Space activate. interacted() fires on hover and on any key so the screen can
    hold its countdown ("press any key to wait").
*/
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Item {
    id: button
    property string text
    property string iconName
    property bool primary: false
    property bool tile: false
    signal clicked()
    signal interacted()
    readonly property bool hovered: mouse.containsMouse
    readonly property color accent: Kirigami.Theme.highlightColor
    implicitWidth: tile ? 104 : Math.max(132, label.implicitWidth + (iconName !== "" ? 20 + 8 : 0) + 40)
    implicitHeight: tile ? 96 : 48
    Layout.preferredWidth: implicitWidth
    Layout.preferredHeight: implicitHeight
    width: implicitWidth
    height: implicitHeight
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: text
    Keys.onPressed: (event) => {
        button.interacted()
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) { button.clicked(); event.accepted = true }
    }
    onHoveredChanged: if (hovered) button.interacted()

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: 12
        color: button.primary ? (mouse.pressed ? Qt.darker(button.accent, 1.18) : (button.hovered ? Qt.lighter(button.accent, 1.10) : button.accent))
                              : Qt.rgba(1, 1, 1, mouse.pressed ? 0.20 : (button.hovered ? 0.15 : 0.09))
        border.width: button.activeFocus ? 2 : 0
        border.color: button.primary ? Qt.rgba(1, 1, 1, 0.9) : button.accent
        Behavior on color { ColorAnimation { duration: 120 } }
    }
    Column {
        visible: button.tile
        anchors.centerIn: parent
        spacing: 8
        Kirigami.Icon { anchors.horizontalCenter: parent.horizontalCenter; width: 30; height: 30; source: button.iconName; color: button.primary ? "white" : Kirigami.Theme.textColor; isMask: true }
        QQC2.Label { anchors.horizontalCenter: parent.horizontalCenter; width: button.width - 12; horizontalAlignment: Text.AlignHCenter; text: button.text; font.pixelSize: 12; font.weight: Font.DemiBold; color: button.primary ? "white" : Kirigami.Theme.textColor; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
    }
    Row {
        visible: !button.tile
        anchors.centerIn: parent
        spacing: 8
        Kirigami.Icon { visible: button.iconName !== ""; anchors.verticalCenter: parent.verticalCenter; width: 20; height: 20; source: button.iconName; color: button.primary ? "white" : Kirigami.Theme.textColor; isMask: true }
        QQC2.Label { id: label; anchors.verticalCenter: parent.verticalCenter; text: button.text; font.pixelSize: 14; font.weight: Font.DemiBold; color: button.primary ? "white" : Kirigami.Theme.textColor }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: button.clicked() }
}
