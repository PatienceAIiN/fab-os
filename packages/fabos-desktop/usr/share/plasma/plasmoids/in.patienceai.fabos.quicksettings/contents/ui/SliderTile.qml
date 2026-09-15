import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Full-width slider row of the quick-settings grid (volume, brightness): glyph button at the left (the volume one
// mutes), a slider with a 6 px track and a 36 px round thumb (background colour, hairline, soft shadow), the value as
// "45%" (Inter 15/600), a chevron to the full standard applet. Radius 16, 4 % tint, hover lift like every tile;
// colours from Kirigami.Theme.
Rectangle {
    id: row
    property string icon
    property string valueText: ""
    property real value: 0
    property real from: 0
    property real to: 100
    property bool sliderEnabled: true
    property bool glyphClickable: false     // the volume row: the glyph mutes
    property string glyphTip: ""
    property string chevronTip: ""
    property bool expandable: true
    property string disabledTip: ""         // shown over a disabled slider (brightness without powerdevil)
    signal moved(real value)
    signal glyphClicked()
    signal expand()

    implicitHeight: 64
    radius: 16
    readonly property color tc: Kirigami.Theme.textColor
    readonly property bool hovered: hover.hovered
    readonly property Item sliderItem: slider        // harness hooks
    readonly property Item thumb: slider.handle
    color: Qt.rgba(tc.r, tc.g, tc.b, hovered ? 0.08 : 0.04)
    border.width: 1
    border.color: Qt.rgba(tc.r, tc.g, tc.b, 0.06)
    scale: hovered ? 1.02 : 1.0
    transformOrigin: Item.Center
    Behavior on color { ColorAnimation { duration: 160 } }
    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 10; anchors.rightMargin: row.expandable ? 6 : 14
        spacing: 8
        SmallButton {
            id: glyphButton
            icon: row.icon; iconSize: 22; implicitWidth: 40; implicitHeight: 40
            tip: row.glyphTip
            active: row.sliderEnabled
            enabled: row.glyphClickable
            onClicked: row.glyphClicked()
        }
        QQC2.Slider {
            id: slider
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            from: row.from; to: row.to; stepSize: 1
            enabled: row.sliderEnabled
            value: row.value
            onMoved: row.moved(value)
            Accessible.name: row.glyphTip
            QQC2.ToolTip.visible: !row.sliderEnabled && row.disabledTip.length > 0 && hovered
            QQC2.ToolTip.text: row.disabledTip
            background: Rectangle {   // 6 px track, the filled part in the accent
                x: slider.leftPadding; y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: slider.availableWidth; height: 6; radius: 3
                color: Qt.rgba(row.tc.r, row.tc.g, row.tc.b, 0.12)
                Rectangle { width: Math.max(height, slider.visualPosition * parent.width); height: parent.height; radius: 3; color: Kirigami.Theme.highlightColor; opacity: slider.enabled ? 1 : 0.4 }
            }
            handle: Item {   // 36 px thumb: soft shadow + a background-colour disc with a hairline
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: 36; height: 36
                Rectangle { anchors.fill: parent; anchors.topMargin: 2; radius: 18; color: Qt.rgba(0, 0, 0, 0.16) }
                Rectangle {
                    anchors.fill: parent; radius: 18
                    color: Kirigami.Theme.backgroundColor
                    border.width: 1; border.color: Qt.rgba(row.tc.r, row.tc.g, row.tc.b, slider.pressed ? 0.30 : 0.16)
                    Rectangle { anchors.centerIn: parent; width: slider.pressed ? 14 : 10; height: width; radius: width / 2; color: Kirigami.Theme.highlightColor; opacity: slider.enabled ? 1 : 0.4; Behavior on width { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } } }
                }
            }
        }
        Text {
            text: row.valueText
            color: row.tc; opacity: row.sliderEnabled ? 0.9 : 0.5
            font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold
            Layout.preferredWidth: 54; horizontalAlignment: Text.AlignRight
        }
        SmallButton { visible: row.expandable; icon: "arrow-right"; iconSize: 16; implicitWidth: 36; implicitHeight: 36; tip: row.chevronTip; onClicked: row.expand() }
    }
    HoverHandler { id: hover }
}
