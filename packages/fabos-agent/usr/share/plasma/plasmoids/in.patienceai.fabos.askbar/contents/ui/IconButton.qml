import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Round icon button: monochrome glyph at 60 % opacity, 100 % on hover, soft hover disc, tooltip.
// `active` (not `enabled`) gates clicks so the tooltip still explains a disabled control.
Item {
    id: b
    property string icon: ""
    property string tip: ""
    property bool active: true
    property bool danger: false
    property bool positive: false
    property int size: 30
    property int iconSize: 18
    property color glyphColor: danger ? Kirigami.Theme.negativeTextColor : (positive ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.textColor)
    signal clicked()

    implicitWidth: size; implicitHeight: size
    scale: ma.pressed && active ? 0.92 : 1.0
    Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent; radius: width / 2
        color: Kirigami.Theme.textColor
        opacity: !b.active ? 0 : (ma.pressed ? 0.14 : (ma.containsMouse ? 0.08 : 0))
        Behavior on opacity { NumberAnimation { duration: 160 } }
    }
    Kirigami.Icon {
        anchors.centerIn: parent
        width: b.iconSize; height: b.iconSize
        source: b.icon
        isMask: true; color: b.glyphColor
        opacity: !b.active ? 0.3 : (ma.containsMouse ? 1.0 : 0.6)
        Behavior on opacity { NumberAnimation { duration: 160 } }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: b.active ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (b.active) b.clicked()
    }
    HoverHandler { id: hover }
    QQC2.ToolTip.visible: hover.hovered && b.tip.length > 0
    QQC2.ToolTip.text: b.tip
    QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
}
