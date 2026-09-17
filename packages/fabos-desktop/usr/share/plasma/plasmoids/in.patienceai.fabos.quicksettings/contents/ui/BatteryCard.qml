import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support
import "status.js" as Status

// Battery card (two columns of the grid): the battery glyph, the percentage in Inter 28/700, the state and time left
// ("On battery · 3.2 hours left"), the active Performance mode as an accent pill, a chevron to the full battery applet,
// and under them the segmented control. With fabos-tuning installed (docs/PERFORMANCE.md) the control offers the five
// Performance modes — Power saver · Balanced · Performance · Gaming · Server — read back from
// `fabos-perf-mode status --json` (never assumed) and switched with `fabos-perf-mode set`; each is a real set of
// power-profiles-daemon / KWin / PowerDevil / wake-word settings. Without that command (an older system, the QML
// harness) it falls back to the three power-profiles-daemon profiles through the applet's setProfile. On a machine
// without a battery but with power profiles the card reads "Power"; without either the tile is not available
// (main.qml tileAvailable). Radius 16, 4 % tint, hover lift; colours from Kirigami.Theme.
// Idle cost: the mode is probed once at load, after each switch, when the power profile the status probe reports
// changes, and at most every 10 s while the pointer is over the card — never on a timer.
Rectangle {
    id: card
    property var st: ({})
    property bool available: true
    signal setProfile(string id)
    signal expand()

    implicitHeight: 96
    radius: 16
    readonly property color tc: Kirigami.Theme.textColor
    readonly property color accent: Kirigami.Theme.highlightColor
    readonly property bool hovered: hover.hovered
    readonly property bool hasProfiles: st.profile !== undefined && String(st.profile).length > 0
    readonly property Item bigNumber: pct          // harness hooks
    readonly property Item segmented: segments
    readonly property Item modePill: pill
    color: Qt.rgba(tc.r, tc.g, tc.b, hovered ? 0.08 : 0.04)
    border.width: 1
    border.color: Qt.rgba(tc.r, tc.g, tc.b, 0.06)
    scale: hovered ? 1.02 : 1.0
    transformOrigin: Item.Center
    Behavior on color { ColorAnimation { duration: 160 } }
    Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    // ---- Performance modes (fabos-perf-mode) with the power-profiles-daemon profiles as the fallback
    readonly property var modes: [
        { id: "power-saver", icon: "battery-profile-powersave",   label: "Power saver", tip: "Lowest power draw. The wake word rests while on battery." },
        { id: "balanced",    icon: "battery-profile-balanced",    label: "Balanced",    tip: "The everyday default." },
        { id: "performance", icon: "battery-profile-performance", label: "Performance", tip: "Full CPU speed." },
        { id: "gaming",      icon: "input-gaming",                label: "Gaming",      tip: "Full speed; tearing and variable refresh allowed; light desktop effects." },
        { id: "server",      icon: "network-server",              label: "Server",      tip: "Never sleeps or blanks the screen; no desktop effects; wake word off." } ]
    readonly property var profiles: [
        { id: "power-saver", icon: "battery-profile-powersave",   label: "Power saver", tip: "" },
        { id: "balanced",    icon: "battery-profile-balanced",    label: "Balanced",    tip: "" },
        { id: "performance", icon: "battery-profile-performance", label: "Performance", tip: "" } ]
    property var mode: ({})                        // the last `fabos-perf-mode status --json`
    property string pendingMode: ""                // the segment just clicked, until the status confirms it
    readonly property bool hasModes: mode !== null && typeof mode.mode === "string" && mode.ppd_available !== false
    readonly property string activeMode: hasModes ? (pendingMode || String(mode.mode)) : ""
    readonly property string statusCmd: "fabos-perf-mode status --json 2>/dev/null"
    property real lastProbe: 0
    property string lastProfile: ""
    function modeLabel(id) { for (var i = 0; i < modes.length; i++) if (modes[i].id === id) return modes[i].label; return "" }
    function modeTip(id) { for (var i = 0; i < modes.length; i++) if (modes[i].id === id) return modes[i].tip; return "" }
    P5Support.DataSource {
        id: modeSrc
        engine: "executable"
        onNewData: (source, data) => {
            disconnectSource(source)
            var out = String(data["stdout"] || "").trim(), j = null
            try { j = out.charAt(0) === "{" ? JSON.parse(out.split("\n")[0]) : null } catch (e) { j = null }
            card.mode = j || {}
            card.pendingMode = ""
        }
    }
    function probeMode() { card.lastProbe = Date.now(); modeSrc.connectSource(card.statusCmd) }
    function setMode(id) { card.pendingMode = id; modeSrc.connectSource("fabos-perf-mode set " + id + " >/dev/null 2>&1; " + card.statusCmd) }
    Component.onCompleted: probeMode()
    onStChanged: { var p = String(st.profile || ""); if (p !== card.lastProfile) { card.lastProfile = p; if (Date.now() - card.lastProbe > 2000) probeMode() } }
    onHoveredChanged: if (hovered && Date.now() - card.lastProbe > 10000) probeMode()

    function stateLine(s) {
        if (!s.hasBattery) return card.hasProfiles || card.hasModes ? "Plugged in" : ""
        var t = s.batStatus === "Charging" ? "Charging" : s.batStatus === "Full" ? "Fully charged" : "On battery"
        // next to the mode pill the line is short: "3.2 hours left" -> "3.2 h left", "45 minutes" -> "45 min"
        var when = card.hasModes ? String(s.batTime).replace(" hours", " h").replace(" minutes", " min") : s.batTime
        if (s.batTime) t += " · " + when + (s.batStatus === "Charging" ? " to full" : " left")
        return t
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12; anchors.leftMargin: 14
        spacing: 8
        RowLayout {   // glyph · 87% · state · [mode pill] · chevron
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
            Rectangle {   // the active Performance mode, as an accent pill (the segments below are icons only)
                id: pill
                visible: card.hasModes && card.activeMode !== ""
                Layout.preferredHeight: 22
                Layout.preferredWidth: pillText.implicitWidth + 16
                radius: 11
                color: Qt.rgba(card.accent.r, card.accent.g, card.accent.b, 0.16)
                Text { id: pillText; anchors.centerIn: parent; text: card.modeLabel(card.activeMode); color: card.accent; font.family: "Inter"; font.pixelSize: 11; font.weight: Font.DemiBold }
                HoverHandler { id: pillHover }
                QQC2.ToolTip.visible: pillHover.hovered
                QQC2.ToolTip.text: "Performance mode: " + card.modeLabel(card.activeMode) + " — " + card.modeTip(card.activeMode)
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                Accessible.role: Accessible.StaticText
                Accessible.name: "Performance mode " + card.modeLabel(card.activeMode)
            }
            SmallButton { icon: "arrow-right"; iconSize: 16; implicitWidth: 32; implicitHeight: 32; tip: "Battery details"; onClicked: card.expand() }
        }
        Rectangle {   // segmented control: equal segments, the active one accent-filled; modes (5, icons) or ppd profiles (3, icon + label)
            id: segments
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            visible: card.hasModes || card.hasProfiles
            radius: 11
            color: Qt.rgba(card.tc.r, card.tc.g, card.tc.b, 0.06)
            readonly property var items: card.hasModes ? card.modes : card.profiles
            readonly property int count: items.length
            readonly property string activeId: card.hasModes ? card.activeMode : String(card.st.profile || "")
            readonly property int current: { for (var i = 0; i < items.length; i++) if (items[i].id === activeId) return i; return -1 }
            readonly property bool labels: !card.hasModes || width >= count * 78   // five labelled segments need ~390 px; icons alone otherwise
            Rectangle {   // the accent thumb glides to the active segment (160 ms)
                visible: segments.current >= 0
                x: 2 + segments.current * (segments.width - 4) / segments.count
                y: 2; width: (segments.width - 4) / segments.count; height: parent.height - 4; radius: 9
                color: card.accent
                Behavior on x { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            }
            Row {
                anchors.fill: parent; anchors.margins: 2
                Repeater {
                    model: segments.items
                    delegate: Item {
                        required property var modelData
                        required property int index
                        readonly property bool current: segments.current === index
                        readonly property color fg: current ? Kirigami.Theme.highlightedTextColor : card.tc
                        width: (segments.width - 4) / segments.count; height: parent.height
                        Row {
                            anchors.centerIn: parent; spacing: 6
                            Kirigami.Icon { anchors.verticalCenter: parent.verticalCenter; width: segments.labels ? 15 : 17; height: width; source: modelData.icon; isMask: true; color: fg }
                            Text { visible: segments.labels; anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: fg; font.family: "Inter"; font.pixelSize: 12; font.weight: current ? Font.DemiBold : Font.Medium }
                        }
                        MouseArea { id: segArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: card.hasModes ? card.setMode(modelData.id) : card.setProfile(modelData.id) }
                        QQC2.ToolTip.visible: segArea.containsMouse && (!current || !segments.labels)
                        QQC2.ToolTip.text: (current ? modelData.label : "Switch to " + modelData.label) + (modelData.tip ? " — " + modelData.tip : "")
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
