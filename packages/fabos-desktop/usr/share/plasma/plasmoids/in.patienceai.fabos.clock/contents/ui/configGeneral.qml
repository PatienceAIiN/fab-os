import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// Settings page of the Fab OS clock. The size is the bar's one setting (owned by the quick-settings page, which pushes
// it here through plasmashell's scripting API), so this page only reports it; the date and time formats are the clock's own.
KCM.SimpleKCM {
    id: page
    property string cfg_barSize
    property bool cfg_showDate
    property string cfg_dateFormat
    property string cfg_timeFormat
    property string cfg_barSizeDefault: "medium"
    property bool cfg_showDateDefault: true
    property string cfg_dateFormatDefault: "ddd d MMM"
    property string cfg_timeFormatDefault: "auto"

    readonly property var formats: ["auto", "24h", "12h"]

    Kirigami.FormLayout {
        QQC2.Label {
            Kirigami.FormData.label: "Size:"
            text: page.cfg_barSize.charAt(0).toUpperCase() + page.cfg_barSize.slice(1) + " (Inter " + (page.cfg_barSize === "small" ? 12 : page.cfg_barSize === "large" ? 15 : 13) + " px)"
        }
        QQC2.Label {
            text: "One size for everything in the bar: change it under Quick settings → Bar (click the indicators at the right end of the bar, then the arrow in the pane's footer). The clock and the indicators follow together."
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.CheckBox {
            Kirigami.FormData.label: "Date:"
            text: "Show the day and date before the time"
            checked: page.cfg_showDate
            onToggled: page.cfg_showDate = checked
        }
        QQC2.TextField {
            Kirigami.FormData.label: "Date format:"
            enabled: page.cfg_showDate
            text: page.cfg_dateFormat
            placeholderText: "ddd d MMM"
            onTextEdited: page.cfg_dateFormat = text.length > 0 ? text : "ddd d MMM"
        }
        QQC2.Label {
            text: "Qt format: ddd = Mon, d = 15, MMM = Sep, MMMM = September, yyyy = 2026. Preview: " + Qt.formatDate(new Date(), page.cfg_dateFormat.length > 0 ? page.cfg_dateFormat : "ddd d MMM")
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        QQC2.ComboBox {
            Kirigami.FormData.label: "Time:"
            model: ["As the region says", "24-hour (10:47)", "12-hour (10:47 am)"]
            currentIndex: Math.max(0, page.formats.indexOf(page.cfg_timeFormat))
            onActivated: page.cfg_timeFormat = page.formats[currentIndex]
        }
    }
}
