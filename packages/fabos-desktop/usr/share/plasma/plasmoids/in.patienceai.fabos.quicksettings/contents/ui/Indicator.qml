import QtQuick
import org.kde.kirigami as Kirigami

// One bar indicator: a FabOS monochrome glyph, optional Inter text beside it and an optional badge. Follows the
// system colour scheme (Kirigami.Theme). Hover magnify (scale 1.25, 160 ms OutCubic) when `magnify` is on.
Item {
    id: ind
    property string icon
    property string text: ""
    property int glyphSize: 18
    property int textPx: 12
    property int textWeight: Font.Medium
    property real textOpacity: 0.9
    property int badge: 0
    property bool magnify: true
    property string tip: ""
    signal clicked()
    signal wheel(int delta)

    implicitWidth: row.implicitWidth
    implicitHeight: Math.max(glyphSize, parent ? parent.height : glyphSize)
    scale: hover.hovered && magnify ? 1.25 : 1.0
    transformOrigin: Item.Center
    Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    Accessible.role: Accessible.Button
    Accessible.name: tip.length > 0 ? tip : text

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 3
        Kirigami.Icon {
            id: glyph
            width: ind.glyphSize; height: ind.glyphSize
            anchors.verticalCenter: parent.verticalCenter
            source: ind.icon
            isMask: true
            color: Kirigami.Theme.textColor
            Rectangle {   // unread badge
                visible: ind.badge > 0
                anchors.right: parent.right; anchors.top: parent.top
                anchors.rightMargin: -5; anchors.topMargin: -4
                width: Math.max(13, badgeText.implicitWidth + 6); height: 13; radius: 6.5
                color: Kirigami.Theme.highlightColor
                Text { id: badgeText; anchors.centerIn: parent; text: ind.badge > 99 ? "99+" : ind.badge; color: Kirigami.Theme.highlightedTextColor; font.family: "Inter"; font.pixelSize: 9; font.weight: Font.Bold }
            }
        }
        Text {
            visible: ind.text.length > 0
            anchors.verticalCenter: parent.verticalCenter
            text: ind.text
            color: Kirigami.Theme.textColor
            opacity: ind.textOpacity
            font.family: "Inter"; font.pixelSize: ind.textPx; font.weight: ind.textWeight
            renderType: Text.NativeRendering
        }
    }
    HoverHandler { id: hover }
    TapHandler { onTapped: ind.clicked() }
    WheelHandler { onWheel: (ev) => ind.wheel(ev.angleDelta.y) }
}
