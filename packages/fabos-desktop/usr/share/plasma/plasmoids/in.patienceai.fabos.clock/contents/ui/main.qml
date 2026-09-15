import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.workspace.calendar as PlasmaCalendar
import org.kde.kirigami as Kirigami

// Fab OS clock (top bar). One bold Inter line — "Mon 15 Sep · 10:47" — in the bar's size (small 12 / medium 13 / large 15
// px, the same table as the quick-settings indicators and the battery percentage), following the system colour scheme.
// It ticks once per minute, on the minute (the timer is re-armed to the next minute boundary: no 1 Hz work). Click
// opens a month view (org.kde.plasma.workspace.calendar, the stock clock's own calendar component) in a dialog.
// The size lives in Plasmoid.configuration.barSize; the quick-settings applet owns that setting and pushes a change
// here through plasmashell's scripting API (status.js syncScript), so both applets change together.
PlasmoidItem {
    id: root
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    readonly property int wantedWidth: label.implicitWidth + 12
    Layout.minimumWidth: wantedWidth
    Layout.preferredWidth: wantedWidth
    Layout.fillHeight: true
    Kirigami.Theme.colorSet: Kirigami.Theme.Window
    Kirigami.Theme.inherit: false

    readonly property var cfg: Plasmoid.configuration      // reachable from the harness
    readonly property string barSize: Plasmoid.configuration.barSize
    readonly property int px: barSize === "small" ? 12 : (barSize === "large" ? 15 : 13)
    readonly property string dateFormat: Plasmoid.configuration.dateFormat.length > 0 ? Plasmoid.configuration.dateFormat : "ddd d MMM"
    // "auto" = the region's short time format WITHOUT seconds (the C locale's short format carries ":ss"; a bar clock never shows seconds)
    readonly property string autoTimeFormat: Qt.locale().timeFormat(Locale.ShortFormat).replace(/[:.]\s?s+/g, "").replace(/\s+/g, " ").trim()
    readonly property string timeFormat: Plasmoid.configuration.timeFormat === "24h" ? "HH:mm" : (Plasmoid.configuration.timeFormat === "12h" ? "h:mm AP" : root.autoTimeFormat)
    property date now: new Date()
    readonly property string timeText: Qt.formatTime(root.now, root.timeFormat)
    readonly property string dateText: Qt.formatDate(root.now, root.dateFormat)
    readonly property string text: (Plasmoid.configuration.showDate ? root.dateText + " · " : "") + root.timeText
    readonly property bool popupOpen: popup.visible

    // tick on the minute: re-arm to the next boundary each time (a resume from sleep fires it at once, then re-arms)
    function tick() {
        var d = new Date()
        root.now = d
        minuteTimer.interval = 60000 - (d.getSeconds() * 1000 + d.getMilliseconds()) + 30
        minuteTimer.restart()
    }
    Timer { id: minuteTimer; interval: 1000; repeat: false; onTriggered: root.tick() }
    Component.onCompleted: root.tick()

    Text {
        id: label
        anchors.centerIn: parent
        text: root.text
        color: Kirigami.Theme.textColor
        font.family: "Inter"
        font.pixelSize: root.px
        font.weight: Font.Bold
        renderType: Text.NativeRendering
        Accessible.role: Accessible.StaticText
        Accessible.name: root.text
    }
    PlasmaCore.ToolTipArea {
        anchors.fill: parent
        mainText: Qt.formatDate(root.now, Qt.locale().dateFormat(Locale.LongFormat))
        subText: Qt.formatTime(root.now, Locale.LongFormat)
        active: !popup.visible
    }
    TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: root.togglePopup() }
    function togglePopup() { popup.visible = !popup.visible }

    // month view: the stock calendar component in a FabOS dialog (bottom corners radius 24 from the dialog background)
    PlasmaCore.Dialog {
        id: popup
        visualParent: root
        location: Plasmoid.location
        type: PlasmaCore.Dialog.AppletPopup
        hideOnWindowDeactivate: true
        visible: false
        mainItem: Item {
            Kirigami.Theme.colorSet: Kirigami.Theme.Window
            Kirigami.Theme.inherit: false
            width: Kirigami.Units.gridUnit * 22
            height: Kirigami.Units.gridUnit * 21
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6
                Text {
                    Layout.fillWidth: true; Layout.leftMargin: 6
                    text: Qt.formatDate(root.now, "dddd d MMMM yyyy")
                    color: Kirigami.Theme.textColor; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.Bold
                }
                Text {
                    Layout.fillWidth: true; Layout.leftMargin: 6
                    text: Qt.formatTime(root.now, Locale.LongFormat)
                    color: Kirigami.Theme.textColor; opacity: 0.7; font.family: "Inter"; font.pixelSize: 12
                }
                PlasmaCalendar.MonthView {
                    id: monthView
                    Layout.fillWidth: true; Layout.fillHeight: true
                    today: root.now
                    firstDayOfWeek: Qt.locale().firstDayOfWeek
                    showWeekNumbers: false
                }
            }
        }
        onVisibleChanged: if (visible) { root.tick(); monthView.resetToToday() }
    }
}
