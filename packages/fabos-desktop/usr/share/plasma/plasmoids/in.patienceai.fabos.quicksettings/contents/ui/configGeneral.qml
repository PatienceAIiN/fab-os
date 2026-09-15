import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// Settings page of the Fab OS quick settings. One "Bar size" for the indicator glyphs, their text and (through the
// shell scripting API, applied by main.qml when the value changes) the Fab OS clock: one size for everything in the
// bar. The hover-magnify switch is pushed to the dock applet the same way (and the dock writes it back when changed
// there), so the bar and the dock share one switch. The dock's magnification strength lives on the dock's own page.
KCM.SimpleKCM {
    id: page
    property string cfg_barSize
    property bool cfg_magnify
    property bool cfg_showSpeed
    property int cfg_pollSeconds
    property string cfg_tilesJson
    // defaults the KCM framework looks for (one per entry)
    property string cfg_barSizeDefault: "medium"
    property bool cfg_magnifyDefault: true
    property bool cfg_showSpeedDefault: true
    property int cfg_pollSecondsDefault: 5
    property string cfg_tilesJsonDefault: ""

    readonly property var sizes: ["small", "medium", "large"]

    Kirigami.FormLayout {
        QQC2.ComboBox {
            id: sizeBox
            Kirigami.FormData.label: "Bar size:"
            model: ["Small", "Medium", "Large"]
            currentIndex: Math.max(0, page.sizes.indexOf(page.cfg_barSize))
            onActivated: page.cfg_barSize = page.sizes[currentIndex]
        }
        QQC2.Label {
            text: "One size for everything in the bar. Small: 16 px glyphs, text 12 · Medium: 18 px, text 13 · Large: 22 px, text 15. The clock (day, date, time), the battery percentage and the indicators all use it."
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.CheckBox {
            id: magnifyBox
            Kirigami.FormData.label: "Hover:"
            text: "Magnify on hover (bar glyphs and dock icons)"
            checked: page.cfg_magnify
            onToggled: page.cfg_magnify = checked
        }
        QQC2.Label {
            text: "One switch for the bar and the dock. How much the dock magnifies (Subtle / Normal / Strong) is set on the dock's own page: right-click a dock icon → Configure Dock…"
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.CheckBox {
            Kirigami.FormData.label: "Network:"
            text: "Show download / upload speed beside the network glyph (always while a link is up; 0 kB/s when idle)"
            checked: page.cfg_showSpeed
            onToggled: page.cfg_showSpeed = checked
        }
        QQC2.SpinBox {
            Kirigami.FormData.label: "Refresh every:"
            from: 5; to: 120
            value: page.cfg_pollSeconds
            onValueModified: page.cfg_pollSeconds = value
            textFromValue: function(v) { return v + " s" }
            valueFromText: function(t) { return parseInt(t, 10) }
        }
        QQC2.Label {
            text: "The Wi-Fi glyph follows NetworkManager's own signal reading at this cadence (one small nmcli call, no scan)."
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
