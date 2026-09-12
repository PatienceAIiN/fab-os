import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// "Ask me to do…" — lives in its own centred floating panel under the top bar. Hands the request to the Fab OS
// agent (fabos-agentd) through the `fabos` CLI. Status is shown only when it matters (not configured / working).
PlasmoidItem {
    id: root
    Kirigami.Theme.colorSet: Kirigami.Theme.Window
    Kirigami.Theme.inherit: false
    preferredRepresentation: fullRepresentation
    Layout.preferredWidth: Kirigami.Units.gridUnit * 34
    Layout.minimumWidth: Kirigami.Units.gridUnit * 22
    Layout.maximumWidth: Kirigami.Units.gridUnit * 40
    Layout.fillHeight: true
    readonly property bool compact: height < Kirigami.Units.gridUnit * 3.4   // inside a panel: one row, status as tooltip

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
                root.status = root.configured ? (root.busy ? out : "") : "AI provider not configured — click here to open Command Center → Settings"
            }
        }
    }
    Timer { interval: 3000; running: true; repeat: true; triggeredOnStart: true
            onTriggered: exec.connectSource("fabos status --brief 2>/dev/null") }

    function submit() {
        var t = field.text.trim()
        if (!t.length || root.sending) return
        if (!root.configured) {
            // check the real configuration at click time, tell the user, and take them straight to Settings
            root.status = "No AI provider is configured yet — opening Fab Command Center → Settings so you can add one (Claude, OpenAI, Gemini or a local model)."
            exec.connectSource("setsid -f fabos-command-center --settings --prefill " + root.shellQuote(t) + " >/dev/null 2>&1; echo opened")
            return
        }
        root.sending = true
        exec.connectSource("fabos do " + root.shellQuote(t) + " 2>&1 | head -1")
        field.text = ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: Kirigami.Units.smallSpacing
        anchors.rightMargin: Kirigami.Units.smallSpacing
        anchors.topMargin: root.compact ? Kirigami.Units.smallSpacing : 0
        anchors.bottomMargin: root.compact ? Kirigami.Units.smallSpacing : 0
        spacing: 2
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
                Layout.preferredHeight: root.compact ? Math.max(Kirigami.Units.gridUnit * 1.6, root.height - Kirigami.Units.smallSpacing * 2) : Kirigami.Units.gridUnit * 2.2
                placeholderText: "Ask me to do anything…"
                font.family: "Inter"; font.pixelSize: 15; color: Kirigami.Theme.textColor; placeholderTextColor: Kirigami.Theme.disabledTextColor
                leftPadding: 14; rightPadding: 14; verticalAlignment: TextInput.AlignVCenter
                background: Rectangle {
                    radius: height / 2; color: Kirigami.Theme.backgroundColor
                    border.color: field.activeFocus ? Kirigami.Theme.highlightColor : Kirigami.Theme.disabledTextColor; border.width: field.activeFocus ? 1.5 : 1
                    Behavior on border.color { ColorAnimation { duration: 180 } }
                }
                onAccepted: root.submit()
            }
            // Do it — animated pill button
            Rectangle {
                id: go
                Layout.preferredWidth: Kirigami.Units.gridUnit * 5.2
                Layout.preferredHeight: field.height
                radius: height / 2
                color: goArea.pressed ? Qt.darker(Kirigami.Theme.highlightColor, 1.2) : (goArea.containsMouse ? Qt.lighter(Kirigami.Theme.highlightColor, 1.15) : Kirigami.Theme.highlightColor)
                scale: goArea.pressed ? 0.95 : (goArea.containsMouse ? 1.04 : 1.0)
                opacity: field.text.trim().length || root.sending ? 1.0 : 0.65
                Behavior on color { ColorAnimation { duration: 160 } }
                Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
                Behavior on opacity { NumberAnimation { duration: 160 } }
                Text {
                    anchors.centerIn: parent
                    text: root.sending ? "" : "Do it"
                    color: Kirigami.Theme.highlightedTextColor; font.family: "Inter"; font.pixelSize: 14; font.weight: Font.DemiBold
                }
                Kirigami.Icon {   // spinner while sending
                    anchors.centerIn: parent; width: 18; height: 18; source: "fabos"; visible: root.sending
                    RotationAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: root.sending }
                }
                MouseArea {
                    id: goArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: root.submit()
                }
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
            font.family: "Inter"; font.pixelSize: 11; elide: Text.ElideRight
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 200 } }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: exec.connectSource("setsid -f fabos-command-center >/dev/null 2>&1; echo opened") }
        }
    }
    QQC2.ToolTip.visible: root.compact && root.showStatus && root.status.length > 0 && hoverHandler.hovered
    QQC2.ToolTip.text: root.status
    HoverHandler { id: hoverHandler }
    // right-click / middle-click anywhere: open Command Center
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.RightButton | Qt.MiddleButton; z: -1
                onClicked: exec.connectSource("setsid -f fabos-command-center >/dev/null 2>&1; echo opened") }
}
