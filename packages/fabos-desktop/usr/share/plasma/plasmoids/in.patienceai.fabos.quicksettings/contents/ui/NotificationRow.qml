import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// One row of the notification history (data from NotificationManager.Notifications; role names are the model's
// enum names without "Role", first letter lower-case). Click runs the default action; the cross closes it.
Item {
    id: row
    required property int index
    required property var model
    property var history: null
    property string summaryText: model.summary || ""
    property string bodyText: (model.body || "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim()
    readonly property bool isJob: model.type === 2   // NotificationManager.Notifications.JobType
    function ago(d) {
        if (!d || isNaN(d.getTime ? d.getTime() : NaN)) return ""
        var s = Math.max(0, (Date.now() - d.getTime()) / 1000)
        if (s < 60) return "just now"
        if (s < 3600) return Math.floor(s / 60) + " min ago"
        if (s < 86400) return Math.floor(s / 3600) + " h ago"
        return Qt.formatDate(d, "ddd d MMM")
    }
    width: ListView.view ? ListView.view.width : 320
    implicitHeight: content.implicitHeight + 16

    Rectangle { anchors.fill: parent; radius: 12; color: Kirigami.Theme.textColor; opacity: rowArea.containsMouse ? 0.06 : 0; Behavior on opacity { NumberAnimation { duration: 120 } } }
    RowLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 8; anchors.rightMargin: 4
        spacing: 10
        Kirigami.Icon {
            Layout.preferredWidth: 24; Layout.preferredHeight: 24; Layout.alignment: Qt.AlignTop
            source: row.model.applicationIconName || row.model.iconName || "notifications"
            fallback: "notifications"
        }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 1
            Text { text: row.summaryText.length > 0 ? row.summaryText : (row.model.applicationName || "Notification"); color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { visible: text.length > 0; text: row.isJob ? (row.model.percentage + "%" + (row.bodyText ? " · " + row.bodyText : "")) : row.bodyText
                   color: Kirigami.Theme.textColor; opacity: 0.8; font.family: "Inter"; font.pixelSize: 12; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: (row.model.applicationName || "") + (row.model.applicationName ? " · " : "") + row.ago(row.model.created); color: Kirigami.Theme.textColor; opacity: 0.55; font.family: "Inter"; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
        }
        SmallButton { icon: "dialog-close"; tip: "Dismiss"; iconSize: 14; Layout.alignment: Qt.AlignTop; visible: rowArea.containsMouse || hovered
                      property bool hovered: false
                      onClicked: if (row.history) row.history.close(row.history.index(row.index, 0)) }
    }
    MouseArea {
        id: rowArea
        anchors.fill: parent
        anchors.rightMargin: 36
        hoverEnabled: true
        z: -1
        cursorShape: row.model.hasDefaultAction ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (row.history && row.model.hasDefaultAction) row.history.invokeDefaultAction(row.history.index(row.index, 0))
    }
}
