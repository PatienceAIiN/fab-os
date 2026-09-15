import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "status.js" as Status

// Battery card (two columns of the grid): the battery glyph, the percentage in Inter 28/700, the state and time left
// ("Discharging · 3.2 hours left"), a chevron to the full battery applet, and under them the power-profile segmented
// control (power-profiles-daemon: Power saver · Balanced · Performance; the active segment is accent-filled). On a
// machine without a battery but with power profiles the card reads "Power" and shows the control alone; without either
// the tile is not available (main.qml tileAvailable). Radius 16, 4 % tint, hover lift; colours from Kirigami.Theme.
Rectangle {
    id: card
    property var st: ({})
    property bool available: true
    signal setProfile(string id)
    signal expand()

    implicitHeight: 96
    radius: 16
    readonly property color tc: Kirigami.Theme.textColor
    readonly property bool hovered: hover.hovered
    readonly property bool hasProfiles: st.profile !== undefined && String(st.profile).length > 0
    readonly property Item bigNumber: pct          // harness hooks
    readonly property Item segmented: segments
    color: Qt.rgba(tc.r, tc.g, tc.b, hovered ? 0.08 : 0.04)
    border.width: 1
    border.color: Qt.rgba(tc.r, tc.g, tc.b, 0.06)
    scale: hovered ? 1.02 : 1.0
    transformOrigin: Item.Center
    Behavior on color { ColorAnimation { duration: 160 } }
    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    function stateLine(s) {
        if (!s.hasBattery) return card.hasProfiles ? "Plugged in" : ""
        var t = s.batStatus === "Charging" ? "Charging" : s.batStatus === "Full" ? "Fully charged" : "On battery"
        if (s.batTime) t += " · " + s.batTime + (s.batStatus === "Charging" ? " to full" : " left")
        return t
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12; anchors.leftMargin: 14
        spacing: 8
        RowLayout {   // glyph · 87% · state · chevron
            Layout.fillWidth: true
            spacing: 10
            Kirigami.Icon { source: card.st.hasBattery ? Status.batteryIcon(card.st.batPct, card.st.batStatus) : "battery-profile-balanced"; isMask: true; color: card.tc; Layout.preferredWidth: 26; Layout.preferredHeight: 26 }
            Text {
                id: pct
                text: card.st.hasBattery ? card.st.batPct + "%" : "Power"
                color: card.tc; font.family: "Inter"; font.pixelSize: 28; font.weight: Font.Bold
                renderType: Text.NativeRendering
            }
            Text {
                Layout.fillWidth: true
                text: card.stateLine(card.st)
                color: card.tc; opacity: 0.75; font.family: "Inter"; font.pixelSize: 13
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }
            SmallButton { icon: "arrow-right"; iconSize: 16; implicitWidth: 32; implicitHeight: 32; tip: "Battery details"; onClicked: card.expand() }
        }
        Rectangle {   // segmented control: three equal segments, the active one accent-filled; hidden without power-profiles-daemon
            id: segments
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            visible: card.hasProfiles
            radius: 11
            color: Qt.rgba(card.tc.r, card.tc.g, card.tc.b, 0.06)
            readonly property var profiles: [ { id: "power-saver", icon: "battery-profile-powersave", label: "Power saver" }, { id: "balanced", icon: "battery-profile-balanced", label: "Balanced" }, { id: "performance", icon: "battery-profile-performance", label: "Performance" } ]
            readonly property int current: { for (var i = 0; i < profiles.length; i++) if (profiles[i].id === card.st.profile) return i; return -1 }
            Rectangle {   // the accent thumb glides to the active segment (160 ms)
                visible: segments.current >= 0
                x: 2 + segments.current * (segments.width - 4) / 3
                y: 2; width: (segments.width - 4) / 3; height: parent.height - 4; radius: 9
                color: Kirigami.Theme.highlightColor
                Behavior on x { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            }
            Row {
                anchors.fill: parent; anchors.margins: 2
                Repeater {
                    model: segments.profiles
                    delegate: Item {
                        required property var modelData
                        required property int index
                        readonly property bool current: segments.current === index
                        readonly property color fg: current ? Kirigami.Theme.highlightedTextColor : card.tc
                        width: (segments.width - 4) / 3; height: parent.height
                        Row {
                            anchors.centerIn: parent; spacing: 6
                            Kirigami.Icon { anchors.verticalCenter: parent.verticalCenter; width: 15; height: 15; source: modelData.icon; isMask: true; color: fg }
                            Text { anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: fg; font.family: "Inter"; font.pixelSize: 12; font.weight: current ? Font.DemiBold : Font.Medium }
                        }
                        MouseArea { id: segArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: card.setProfile(modelData.id) }
                        QQC2.ToolTip.visible: segArea.containsMouse && !current
                        QQC2.ToolTip.text: "Switch to " + modelData.label
                        QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: modelData.label
                        Accessible.checked: current
                    }
                }
            }
        }
    }
    HoverHandler { id: hover }
    Accessible.role: Accessible.Grouping
    Accessible.name: "Battery"
}
