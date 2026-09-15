import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// The cloud hint chip — "Using the built-in model. For the best results use a cloud model" · Choose · × — shown while
// GET /status reports provider == "local". ONE component in two places: under the field inside the card on the desktop,
// and as the first row of the popup's header in the compact (panel) form, where the 36 px card has no room for it.
// Radius 12, alternateBackgroundColor, the 12 % hairline, the info glyph in the accent; Inter 12.
Rectangle {
    id: chip
    property color hairline: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
    readonly property alias label: hintText          // the harness reads the wording
    readonly property alias chooseItem: chooseText   // and fires Choose through its clicked()
    signal choose()
    signal dismiss()
    implicitHeight: 30
    radius: 12
    color: Kirigami.Theme.alternateBackgroundColor
    border.color: chip.hairline; border.width: 1
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 4
        spacing: 8
        Kirigami.Icon { Layout.preferredWidth: 14; Layout.preferredHeight: 14; source: "dialog-information"; isMask: true; color: Kirigami.Theme.highlightColor }
        Text {
            id: hintText
            Layout.fillWidth: true
            text: "Using the built-in model. For the best results use a cloud model"
            color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 12; elide: Text.ElideRight
        }
        Text {
            id: chooseText
            text: "Choose"
            color: Kirigami.Theme.highlightColor; font.family: "Inter"; font.pixelSize: 12; font.weight: Font.DemiBold
            opacity: chooseArea.containsMouse ? 1.0 : 0.85
            signal clicked()
            onClicked: chip.choose()
            MouseArea { id: chooseArea; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: chooseText.clicked() }
        }
        IconButton { icon: "window-close"; tip: "Dismiss for now"; size: 22; iconSize: 14; onClicked: chip.dismiss() }
    }
}
