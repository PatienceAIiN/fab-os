import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Round icon button (radius 12 control) for pane headers and rows; text-colour tint on hover, tooltip on hover.
Item {
    id: b
    property string icon
    property string tip: ""
    property int iconSize: 16
    property bool active: true
    signal clicked()
    implicitWidth: 28; implicitHeight: 28
    opacity: active ? 1 : 0.4
    Rectangle {
        anchors.fill: parent; radius: 12
        color: Kirigami.Theme.textColor
        opacity: ma.pressed ? 0.16 : (ma.containsMouse ? 0.09 : 0)
        Behavior on opacity { NumberAnimation { duration: 140 } }
    }
    Kirigami.Icon { anchors.centerIn: parent; width: b.iconSize; height: b.iconSize; source: b.icon; isMask: true; color: Kirigami.Theme.textColor; opacity: ma.containsMouse ? 1 : 0.7 }
    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: if (b.active) b.clicked() }
    QQC2.ToolTip.visible: ma.containsMouse && b.tip.length > 0
    QQC2.ToolTip.text: b.tip
    QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
    Accessible.role: Accessible.Button
    Accessible.name: b.tip
}
