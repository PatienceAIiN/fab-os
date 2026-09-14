import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// Settings page of the Fab OS quick settings. One "Bar size" for the indicator glyphs, their text and (through the
// shell scripting API, applied by main.qml when the value changes) the stock clock; the hover-magnify switch and the
// dock magnification are pushed to the dock applet the same way, so the bar and the dock share one setting.
KCM.SimpleKCM {
    id: page
    property string cfg_barSize
    property bool cfg_magnify
    property string cfg_magnification
    property bool cfg_showSpeed
    property int cfg_pollSeconds
    // defaults the KCM framework looks for (one per entry)
    property string cfg_barSizeDefault: "medium"
    property bool cfg_magnifyDefault: true
    property string cfg_magnificationDefault: "normal"
    property bool cfg_showSpeedDefault: true
    property int cfg_pollSecondsDefault: 10

    readonly property var sizes: ["small", "medium", "large"]
    readonly property var strengths: ["subtle", "normal", "strong"]

    Kirigami.FormLayout {
        QQC2.ComboBox {
            id: sizeBox
            Kirigami.FormData.label: "Bar size:"
            model: ["Small", "Medium", "Large"]
            currentIndex: Math.max(0, page.sizes.indexOf(page.cfg_barSize))
            onActivated: page.cfg_barSize = page.sizes[currentIndex]
        }
        QQC2.Label {
            text: "Small: 16 px glyphs, clock 12 · Medium: 18 px, clock 13 · Large: 22 px, clock 15. The clock and the dock follow this setting."
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.CheckBox {
            id: magnifyBox
            Kirigami.FormData.label: "Hover:"
            text: "Magnify on hover (bar indicators and dock icons)"
            checked: page.cfg_magnify
            onToggled: page.cfg_magnify = checked
        }
        QQC2.ComboBox {
            id: strengthBox
            Kirigami.FormData.label: "Dock magnification:"
            enabled: magnifyBox.checked
            model: ["Subtle", "Normal", "Strong"]
            currentIndex: Math.max(0, page.strengths.indexOf(page.cfg_magnification))
            onActivated: page.cfg_magnification = page.strengths[currentIndex]
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.CheckBox {
            Kirigami.FormData.label: "Network:"
            text: "Show download / upload speed while data flows"
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
    }
}
