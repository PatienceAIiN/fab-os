import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// Quick-settings tile: rounded (radius 14) surface, accent-filled while `on`; glyph, title, one-line detail and an
// optional chevron that opens the full standard applet. Colours from Kirigami.Theme only (light and dark schemes).
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

    implicitHeight: 60
    radius: 14
    readonly property color fg: on ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
    readonly property color tint: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, mainArea.containsMouse ? 0.14 : 0.08)
    color: on ? (mainArea.containsMouse ? Qt.lighter(Kirigami.Theme.highlightColor, 1.1) : Kirigami.Theme.highlightColor) : tint
    opacity: actionEnabled ? 1 : 0.55
    Behavior on color { ColorAnimation { duration: 160 } }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12; anchors.rightMargin: tile.expandable ? 34 : 10
        spacing: 10
        Kirigami.Icon { source: tile.icon; isMask: true; color: tile.fg; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 1
            Text { text: tile.title; color: tile.fg; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: tile.detail; visible: text.length > 0; color: tile.fg; opacity: 0.75; font.family: "Inter"; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
        }
    }
    MouseArea {   // the tile body toggles
        id: mainArea
        anchors.fill: parent
        anchors.rightMargin: tile.expandable ? 32 : 0
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: if (tile.actionEnabled) tile.toggled()
    }
    Item {   // the chevron opens the full applet
        visible: tile.expandable
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 32
        Rectangle { anchors.centerIn: parent; width: 26; height: 26; radius: 13; color: Qt.rgba(tile.fg.r, tile.fg.g, tile.fg.b, expandArea.containsMouse ? 0.18 : 0.0); Behavior on color { ColorAnimation { duration: 120 } } }
        Kirigami.Icon { anchors.centerIn: parent; width: 16; height: 16; source: "arrow-right"; isMask: true; color: tile.fg; opacity: 0.85 }
        MouseArea { id: expandArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: tile.expand() }
        Accessible.role: Accessible.Button
        Accessible.name: "Open " + tile.title + " details"
    }
    Accessible.role: Accessible.CheckBox
    Accessible.name: tile.title
    Accessible.checked: tile.on
}
