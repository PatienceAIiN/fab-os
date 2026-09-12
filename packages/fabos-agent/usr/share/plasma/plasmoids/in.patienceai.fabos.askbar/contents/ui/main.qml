import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// "Ask me to do…" bar that lives on the Fab OS desktop. Hands the request to the Fab OS agent (fabos-agentd)
// through the `fabos` CLI and shows the agent's live status underneath.
PlasmoidItem {
    id: root
    preferredRepresentation: fullRepresentation
    Layout.minimumWidth: Kirigami.Units.gridUnit * 24
    Layout.minimumHeight: Kirigami.Units.gridUnit * 5
    Layout.preferredWidth: Kirigami.Units.gridUnit * 36
    Layout.preferredHeight: Kirigami.Units.gridUnit * 5.4

    property string status: "Fab OS agent"
    property string lastTask: ""

    function shellQuote(s) { return "'" + s.replace(/'/g, "'\\''") + "'" }

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            var out = (data["stdout"] || "").trim()
            disconnectSource(source)
            if (source.indexOf("fabos do ") === 0) {
                root.lastTask = out.length ? out : "Task started"
                root.status = root.lastTask
            } else if (source.indexOf("fabos status") === 0 && out.length) {
                root.status = out
            }
        }
    }
    Timer { interval: 4000; running: true; repeat: true; triggeredOnStart: true
            onTriggered: exec.connectSource("fabos status --brief 2>/dev/null || echo 'Fab OS agent is starting…'") }

    Rectangle {
        anchors.fill: parent
        radius: 18
        color: Qt.rgba(0.086, 0.106, 0.133, 0.92)
        border.color: field.activeFocus ? "#6E9BFF" : "#2A313B"
        border.width: 1

        RowLayout {
            id: row
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
            spacing: 10
            Kirigami.Icon { source: "fabos"; Layout.preferredWidth: 30; Layout.preferredHeight: 30 }
            QQC2.TextField {
                id: field
                Layout.fillWidth: true
                placeholderText: "Ask me to do…   e.g. open editor, write hi and mail it to someone@example.com"
                font.family: "Inter"; font.pixelSize: 15; color: "white"; placeholderTextColor: "#8892A0"
                background: Rectangle { radius: 10; color: "#0E1116"; border.color: field.activeFocus ? "#6E9BFF" : "#2A313B" }
                onAccepted: {
                    var t = text.trim()
                    if (!t.length) return
                    root.status = "Sending to the agent…"
                    exec.connectSource("fabos do " + root.shellQuote(t) + " 2>&1 | head -1")
                    text = ""
                }
            }
            QQC2.Button {
                text: "Do it"
                Layout.preferredWidth: Kirigami.Units.gridUnit * 5; Layout.preferredHeight: field.height
                font.family: "Inter"; font.weight: Font.DemiBold
                contentItem: Text { text: parent.text; font: parent.font; color: "#0E1116"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 10; color: parent.down ? "#5A86E8" : "#6E9BFF" }
                onClicked: field.accepted()
            }
            QQC2.ToolButton {
                icon.name: "view-list-details"
                QQC2.ToolTip.text: "Open Command Center (history, approvals, settings)"
                QQC2.ToolTip.visible: hovered
                onClicked: exec.connectSource("setsid -f fabos-command-center >/dev/null 2>&1; echo opened")
            }
        }
        Text {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 52; rightMargin: 14; bottomMargin: 7 }
            text: root.status
            color: "#9AA4B2"; font.family: "Inter"; font.pixelSize: 11; elide: Text.ElideRight
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: exec.connectSource("setsid -f fabos-command-center >/dev/null 2>&1; echo opened") }
        }
    }
}
