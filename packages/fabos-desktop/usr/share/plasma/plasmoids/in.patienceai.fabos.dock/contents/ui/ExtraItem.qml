import QtQuick
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami

// A dock item that is not a task: the Fab OS start button (first) and "peek at the desktop" (last). Same box, same
// magnification and bounce as a TaskItem (its `slot` is its index in the dock's unified row), one action on click.
// Both are TILES like the Fab OS app icons and are drawn at dock.tileScale of the box: the start button is the icon
// theme's launcher tile (start-here = the Fab OS mark on a dark tile); peek draws a neutral tile (text colour @ 14 %)
// behind the monochrome desktop glyph — so every item in the row has the same visible extent.
PlasmaCore.ToolTipArea {
    id: item
    required property int slot
    property string icon
    property string source: ""            // a file URL (a tile's SVG) drawn instead of the theme icon name
    property string tip: ""
    property string tipSub: ""
    property real iconScale: 1.0          // visible-extent normalisation (dock.tileScale)
    property bool tileBackground: false   // draw the neutral tile behind a monochrome glyph
    signal clicked()

    property real s: dock.scaleFor(slot)
    Behavior on s { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    property real bounce: 1.0
    readonly property Item glyph: glyphItem
    function launchBounce() { bounceAnim.restart() }
    SequentialAnimation {
        id: bounceAnim
        NumberAnimation { target: item; property: "bounce"; to: 1.15; duration: 150; easing.type: Easing.OutCubic }
        NumberAnimation { target: item; property: "bounce"; to: 1.0; duration: 150; easing.type: Easing.InCubic }
    }

    width: Math.round(dock.baseSize * s)
    height: dock.height
    mainText: tip
    subText: tipSub
    active: dock.hoveredIndex === slot

    Item {   // the icon box (tile-sized), centred in the slot like TaskItem's icon
        id: glyphItem
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: dock.dotSpace + Math.round((dock.baseSize * item.s - width) / 2)
        width: Math.round(dock.baseSize * item.s * item.iconScale)
        height: width
        scale: item.bounce
        transformOrigin: Item.Bottom
        Rectangle {   // neutral tile (peek): the same rounded square as the Fab OS app tiles (rx 16 of 64)
            visible: item.tileBackground
            anchors.fill: parent
            radius: width * 0.25
            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.14)
            border.width: 1
            border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.10)
        }
        Image {   // a tile's SVG (the start button): rasterised once at the peak size, scaled down at rest (see TaskItem)
            visible: item.source.length > 0
            anchors.fill: parent
            source: item.source
            sourceSize: Qt.size(dock.tilePeakPx, dock.tilePeakPx)
            fillMode: Image.PreserveAspectFit
            smooth: true; mipmap: true
        }
        Kirigami.Icon {   // a theme glyph (peek's monochrome desktop) inside the neutral tile
            visible: item.source.length === 0
            anchors.centerIn: parent
            width: item.tileBackground ? Math.round(parent.width * 0.62) : parent.width
            height: width
            source: item.source.length === 0 ? item.icon : ""
            isMask: item.tileBackground
            color: Kirigami.Theme.textColor
        }
    }
    HoverHandler { onHoveredChanged: if (hovered) dock.hoveredIndex = item.slot; else if (dock.hoveredIndex === item.slot) dock.hoveredIndex = -1 }
    TapHandler { acceptedButtons: Qt.LeftButton; onTapped: { item.launchBounce(); item.clicked() } }
    Accessible.role: Accessible.Button
    Accessible.name: item.tip
}
