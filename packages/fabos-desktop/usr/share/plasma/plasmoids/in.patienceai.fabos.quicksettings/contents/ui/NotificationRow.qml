import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// One row of the notification history (data from NotificationManager.Notifications; role names are the model's
// enum names without "Role", first letter lower-case). 64 px minimum, app icon 36, summary (Inter 14/600) and body
// at 14 px, app · time ago at 12. Click runs the default action; the cross closes it. Hover: `rowArea` spans the whole
// row (the button sits above it) and the cross stays visible while the pointer is on the button itself
// (`dismissBtn.hovered`), so it can always be reached with the mouse — a hovered MouseArea takes the hover from the
// row area beneath it.
Item {
    id: row
    required property int index
    required property var model
    property var history: ListView.view ? ListView.view.model : null   // the NotificationManager.Notifications model (close / invokeDefaultAction)
    property string summaryText: model.summary || ""
    property string bodyText: (model.body || "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim()
    readonly property bool isJob: model.type === 2   // NotificationManager.Notifications.JobType
    readonly property bool rowHovered: rowArea.containsMouse || dismissBtn.hovered
    readonly property Item dismissButton: dismissBtn   // harness hooks (tests/quicksettings-qml-harness)
    readonly property Item hoverArea: rowArea
    readonly property Item appIcon: iconItem
    readonly property Item bodyLabel: bodyItem
    function ago(d) {
        if (!d || isNaN(d.getTime ? d.getTime() : NaN)) return ""
        var s = Math.max(0, (Date.now() - d.getTime()) / 1000)
        if (s < 60) return "just now"
        if (s < 3600) return Math.floor(s / 60) + " min ago"
        if (s < 86400) return Math.floor(s / 3600) + " h ago"
        return Qt.formatDate(d, "ddd d MMM")
    }
    width: ListView.view ? ListView.view.width : 600
    implicitHeight: Math.max(64, content.implicitHeight + 16)

    Rectangle { anchors.fill: parent; radius: 16; color: Kirigami.Theme.textColor; opacity: row.rowHovered ? 0.06 : 0; Behavior on opacity { NumberAnimation { duration: 120 } } }
    RowLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 12; anchors.rightMargin: 8
        spacing: 14
        Kirigami.Icon {
            id: iconItem
            Layout.preferredWidth: 36; Layout.preferredHeight: 36; Layout.alignment: Qt.AlignTop
            source: row.model.applicationIconName || row.model.iconName || "notifications"
            fallback: "notifications"
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 2
            Text { text: row.summaryText.length > 0 ? row.summaryText : (row.model.applicationName || "Notification"); color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { id: bodyItem; visible: text.length > 0; text: row.isJob ? (row.model.percentage + "%" + (row.bodyText ? " · " + row.bodyText : "")) : row.bodyText
                   color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 14; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: (row.model.applicationName || "") + (row.model.applicationName ? " · " : "") + row.ago(row.model.created); color: Kirigami.Theme.textColor; opacity: 0.55; font.family: "Inter"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
        }
        SmallButton { id: dismissBtn; icon: "dialog-close"; tip: "Dismiss"; iconSize: 14; implicitWidth: 32; implicitHeight: 32; Layout.alignment: Qt.AlignTop; visible: row.rowHovered
                      onClicked: if (row.history) row.history.close(row.history.index(row.index, 0)) }
    }
    MouseArea {   // below the content (z -1): the dismiss button's own MouseArea takes clicks on it first
        id: rowArea
        anchors.fill: parent
        hoverEnabled: true
        z: -1
        cursorShape: row.model.hasDefaultAction ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (row.history && row.model.hasDefaultAction) row.history.invokeDefaultAction(row.history.index(row.index, 0))
    }
}
