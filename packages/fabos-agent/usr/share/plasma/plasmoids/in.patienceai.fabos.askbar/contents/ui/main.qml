import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// "Ask me to do anything…" — the Fab OS agent's front door on the home screen.
// On the desktop the applet is a full-width transparent strip and this card centres itself from the real screen
// width, so it is centred on every monitor size. In a panel it collapses to one compact row.
PlasmoidItem {
    id: root
    Kirigami.Theme.colorSet: Kirigami.Theme.Window
    Kirigami.Theme.inherit: false
    preferredRepresentation: fullRepresentation
    Layout.preferredWidth: Kirigami.Units.gridUnit * 34
    Layout.minimumWidth: Kirigami.Units.gridUnit * 22
    Layout.fillHeight: true
    readonly property bool compact: height < Kirigami.Units.gridUnit * 3.4
    readonly property bool onDesktop: !compact
    Plasmoid.backgroundHints: onDesktop ? PlasmaCore.Types.NoBackground : PlasmaCore.Types.DefaultBackground

    property string status: ""
    property bool configured: true
    property bool busy: false
    property bool sending: false
    readonly property bool showStatus: !configured || busy || sending

    function shellQuote(s) { return "'" + s.replace(/'/g, "'\\''") + "'" }

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            var out = (data["stdout"] || "").trim()
            disconnectSource(source)
            if (source.indexOf("fabos do ") === 0) {
                root.sending = false
                root.status = out.length ? out : "Task started"
                root.busy = true
            } else if (source.indexOf("fabos status") === 0) {
                root.configured = out.indexOf("not configured") === -1
                root.busy = /running|queued|awaiting|approval|waiting/.test(out)
                root.status = root.configured ? (root.busy ? out : "") : "No AI provider yet — click here to open Fab Command Center → Settings"
            }
        }
    }
    Timer { interval: 3000; running: true; repeat: true; triggeredOnStart: true
            onTriggered: exec.connectSource("fabos status --brief 2>/dev/null") }

    function openSettings(prefill) {
        exec.connectSource("setsid -f fabos-command-center --settings" + (prefill ? " --prefill " + root.shellQuote(prefill) : "") + " >/dev/null 2>&1; echo opened")
    }
    function submit() {
        var t = field.text.trim()
        if (!t.length || root.sending) return
        if (!root.configured) {
            // check the real configuration at click time, tell the user, and take them straight to Settings
            root.status = "No AI provider is configured yet — opening Fab Command Center → Settings so you can add one (Claude, OpenAI, Gemini or a local model). Your request is kept."
            root.openSettings(t)
            return
        }
        root.sending = true
        exec.connectSource("fabos do " + root.shellQuote(t) + " 2>&1 | head -1")
        field.text = ""
    }

    Item {
        id: card
        // position of this applet inside the desktop view → lets the card centre on the physical screen
        readonly property real appletScreenX: { var p = root.mapToItem(null, 0, 0); return root.width + Screen.width > 0 ? p.x : 0 }
        width: root.onDesktop ? Math.min(760, Math.round(Screen.width * 0.6)) : root.width
        height: root.onDesktop ? Math.min(root.height, Kirigami.Units.gridUnit * 6.2) : root.height
        x: root.onDesktop ? Math.max(0, Math.round((Screen.width - width) / 2 - appletScreenX)) : 0
        y: root.onDesktop ? Math.round((root.height - height) / 2) : 0

        Rectangle {   // Material-expressive surface: large radius, tinted, hairline border
            anchors.fill: parent
            visible: root.onDesktop
            radius: 24
            color: Kirigami.Theme.backgroundColor
            opacity: 0.92
            border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12); border.width: 1
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: root.onDesktop ? Kirigami.Units.gridUnit : Kirigami.Units.smallSpacing
            anchors.rightMargin: root.onDesktop ? Kirigami.Units.gridUnit : Kirigami.Units.smallSpacing
            anchors.topMargin: root.onDesktop ? Kirigami.Units.largeSpacing : Kirigami.Units.smallSpacing
            anchors.bottomMargin: root.onDesktop ? Kirigami.Units.largeSpacing : Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing
            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: Kirigami.Units.smallSpacing * 2
                Kirigami.Icon {
                    source: "fabos"
                    Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium + 6
                    Layout.preferredHeight: Layout.preferredWidth
                    Rectangle {   // state dot: green = ready, amber = working, red = not configured
                        width: 9; height: 9; radius: 4.5; anchors.right: parent.right; anchors.bottom: parent.bottom
                        color: !root.configured ? "#F0655D" : (root.busy || root.sending ? "#E0A64B" : "#3FCB7E")
                        border.color: Kirigami.Theme.backgroundColor; border.width: 1.5
                        Behavior on color { ColorAnimation { duration: 250 } }
                    }
                }
                QQC2.TextField {
                    id: field
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.compact ? Math.max(Kirigami.Units.gridUnit * 1.6, root.height - Kirigami.Units.smallSpacing * 2) : Kirigami.Units.gridUnit * 2.4
                    placeholderText: "Ask me to do anything…"
                    font.family: "Inter"; font.pixelSize: 16; color: Kirigami.Theme.textColor; placeholderTextColor: Kirigami.Theme.disabledTextColor
                    leftPadding: 16; rightPadding: 16; verticalAlignment: TextInput.AlignVCenter
                    background: Rectangle {
                        radius: height / 2; color: Kirigami.Theme.alternateBackgroundColor
                        border.color: field.activeFocus ? Kirigami.Theme.highlightColor : Kirigami.Theme.disabledTextColor; border.width: field.activeFocus ? 1.5 : 1
                        Behavior on border.color { ColorAnimation { duration: 180 } }
                    }
                    onAccepted: root.submit()
                }
                Rectangle {   // Do it — animated pill
                    id: go
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 5.6
                    Layout.preferredHeight: field.height
                    radius: height / 2
                    color: goArea.pressed ? Qt.darker(Kirigami.Theme.highlightColor, 1.2) : (goArea.containsMouse ? Qt.lighter(Kirigami.Theme.highlightColor, 1.15) : Kirigami.Theme.highlightColor)
                    scale: goArea.pressed ? 0.95 : (goArea.containsMouse ? 1.04 : 1.0)
                    opacity: field.text.trim().length || root.sending ? 1.0 : 0.7
                    Behavior on color { ColorAnimation { duration: 160 } }
                    Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
                    Behavior on opacity { NumberAnimation { duration: 160 } }
                    Text { anchors.centerIn: parent; text: root.sending ? "" : "Do it"; color: Kirigami.Theme.highlightedTextColor; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold }
                    // the mark spins on the accent pill while sending: monochrome variant recoloured to the pill's text colour
                    // (the identity icon "fabos" is itself accent-coloured and would vanish here)
                    Kirigami.Icon { anchors.centerIn: parent; width: 18; height: 18; source: "fabos-symbolic"; isMask: true; color: Kirigami.Theme.highlightedTextColor; visible: root.sending
                        RotationAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: root.sending } }
                    MouseArea { id: goArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.submit() }
                }
            }
            Text {
                id: statusText
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.iconSizes.smallMedium + 6 + Kirigami.Units.smallSpacing * 2
                visible: !root.compact && root.showStatus && root.status.length > 0
                text: root.status
                wrapMode: Text.WordWrap; maximumLineCount: 2
                color: root.configured ? Kirigami.Theme.disabledTextColor : Kirigami.Theme.neutralTextColor
                font.family: "Inter"; font.pixelSize: 12; elide: Text.ElideRight
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.configured ? exec.connectSource("setsid -f fabos-command-center >/dev/null 2>&1; echo opened") : root.openSettings("") }
            }
        }
        QQC2.ToolTip.visible: root.compact && root.showStatus && root.status.length > 0 && hoverHandler.hovered
        QQC2.ToolTip.text: root.status
        HoverHandler { id: hoverHandler }
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.RightButton | Qt.MiddleButton; z: -1
                    onClicked: exec.connectSource("setsid -f fabos-command-center >/dev/null 2>&1; echo opened") }
    }
}
