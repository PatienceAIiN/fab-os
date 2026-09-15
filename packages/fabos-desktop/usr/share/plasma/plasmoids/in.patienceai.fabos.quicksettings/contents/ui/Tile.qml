import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Quick-settings toggle tile (v3 grid): radius 16, text colour @ 4 % (8 % hovered), accent-filled while `on`; the glyph
// sits in a 40 px circle, then the title (Inter 15/600) over one line of detail (13); an optional chevron opens the full
// standard applet. Hover lifts the tile to scale 1.02 in 120 ms. Colours from Kirigami.Theme only (light and dark).
Rectangle {
    id: tile
    property string icon
    property string title
    property string detail: ""
    property bool on: false
    property bool expandable: true
    property bool actionEnabled: true
    property string tip: ""
    signal toggled()
    signal expand()

    implicitHeight: 76
    radius: 16
    readonly property color tc: Kirigami.Theme.textColor
    readonly property color fg: on ? Kirigami.Theme.highlightedTextColor : tc
    readonly property bool hovered: hover.hovered
    readonly property Item glyphCircle: circle      // harness hook
    color: on ? (hovered ? Qt.lighter(Kirigami.Theme.highlightColor, 1.08) : Kirigami.Theme.highlightColor)
              : Qt.rgba(tc.r, tc.g, tc.b, hovered ? 0.08 : 0.04)
    border.width: on ? 0 : 1
    border.color: Qt.rgba(tc.r, tc.g, tc.b, 0.06)
    opacity: actionEnabled ? 1 : 0.55
    scale: hovered ? 1.02 : 1.0
    transformOrigin: Item.Center
    Behavior on color { ColorAnimation { duration: 160 } }
    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14; anchors.rightMargin: tile.expandable ? 40 : 12
        spacing: 12
        Rectangle {   // glyph circle
            id: circle
            Layout.preferredWidth: 40; Layout.preferredHeight: 40
            radius: 20
            color: tile.on ? Qt.rgba(tile.fg.r, tile.fg.g, tile.fg.b, 0.22) : Qt.rgba(tile.tc.r, tile.tc.g, tile.tc.b, 0.08)
            Behavior on color { ColorAnimation { duration: 160 } }
            Kirigami.Icon { anchors.centerIn: parent; width: 22; height: 22; source: tile.icon; isMask: true; color: tile.fg }
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 2
            Text { text: tile.title; color: tile.fg; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: tile.detail; visible: text.length > 0; color: tile.fg; opacity: 0.75; font.family: "Inter"; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
        }
    }
    MouseArea {   // the tile body toggles
        id: mainArea
        anchors.fill: parent
        anchors.rightMargin: tile.expandable ? 36 : 0
        cursorShape: Qt.PointingHandCursor
        onClicked: if (tile.actionEnabled) tile.toggled()
    }
    Item {   // the chevron opens the full applet
        visible: tile.expandable
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        anchors.rightMargin: 6
        width: 32
        Rectangle { anchors.centerIn: parent; width: 28; height: 28; radius: 14; color: Qt.rgba(tile.fg.r, tile.fg.g, tile.fg.b, expandArea.containsMouse ? 0.18 : 0.0); Behavior on color { ColorAnimation { duration: 120 } } }
        Kirigami.Icon { anchors.centerIn: parent; width: 16; height: 16; source: "arrow-right"; isMask: true; color: tile.fg; opacity: 0.85 }
        MouseArea { id: expandArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: tile.expand() }
        Accessible.role: Accessible.Button
        Accessible.name: "Open " + tile.title + " details"
    }
    HoverHandler { id: hover }
    Accessible.role: Accessible.CheckBox
    Accessible.name: tile.title
    Accessible.checked: tile.on
}
