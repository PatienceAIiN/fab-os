import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// Settings page of the Fab OS dock. The quick-settings applet in the top bar pushes "Magnify on hover" and the
// magnification here too (through plasmashell's scripting API), so the bar and the dock share one setting.
KCM.SimpleKCM {
    id: page
    property bool cfg_magnify
    property string cfg_magnification
    property bool cfg_groupApps
    property bool cfg_showOnlyCurrentDesktop
    property bool cfg_showOnlyCurrentActivity
    property int cfg_maxIconSize
    property var cfg_launchers: []
    property bool cfg_magnifyDefault: true
    property string cfg_magnificationDefault: "normal"
    property bool cfg_groupAppsDefault: true
    property bool cfg_showOnlyCurrentDesktopDefault: false
    property bool cfg_showOnlyCurrentActivityDefault: true
    property int cfg_maxIconSizeDefault: 48
    property var cfg_launchersDefault: []

    readonly property var strengths: ["subtle", "normal", "strong"]

    Kirigami.FormLayout {
        QQC2.CheckBox {
            id: magnifyBox
            Kirigami.FormData.label: "Hover:"
            text: "Magnify on hover"
            checked: page.cfg_magnify
            onToggled: page.cfg_magnify = checked
        }
        QQC2.ComboBox {
            Kirigami.FormData.label: "Magnification:"
            enabled: magnifyBox.checked
            model: ["Subtle (1.3x)", "Normal (1.6x)", "Strong (1.9x)"]
            currentIndex: Math.max(0, page.strengths.indexOf(page.cfg_magnification))
            onActivated: page.cfg_magnification = page.strengths[currentIndex]
        }
        QQC2.Label {
            text: "Resting icons shrink so the magnified one still fits the dock; the top bar's quick settings change these two values as well."
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.SpinBox {
            Kirigami.FormData.label: "Largest resting icon:"
            from: 24; to: 96
            value: page.cfg_maxIconSize
            onValueModified: page.cfg_maxIconSize = value
            textFromValue: function(v) { return v + " px" }
            valueFromText: function(t) { return parseInt(t, 10) }
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.CheckBox {
            Kirigami.FormData.label: "Windows:"
            text: "One icon per application (click cycles its windows)"
            checked: page.cfg_groupApps
            onToggled: page.cfg_groupApps = checked
        }
        QQC2.CheckBox {
            text: "Only from the current virtual desktop"
            checked: page.cfg_showOnlyCurrentDesktop
            onToggled: page.cfg_showOnlyCurrentDesktop = checked
        }
        QQC2.CheckBox {
            text: "Only from the current activity"
            checked: page.cfg_showOnlyCurrentActivity
            onToggled: page.cfg_showOnlyCurrentActivity = checked
        }
        QQC2.Label {
            Kirigami.FormData.label: "Pinned apps:"
            text: page.cfg_launchers.length + " pinned. Right-click an icon in the dock to pin or unpin it; drag is not supported yet."
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
