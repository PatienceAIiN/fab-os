import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import "../code/modes.js" as Modes

// The Research · Computer use strip (docs/design/MODES.md): [globe] Research (switch)  ·  [keyboard] Computer use (switch).
// ONE component in two places — under the field inside the card on the desktop, and as the popup's header row in the compact
// (panel) form — and the same geometry Fab AI Controls draws (Modes.TOKENS). The strip owns no state: `state` comes from the
// bar (the chat's effective values, or the defaults when no chat is open) and toggled(key, on) asks the bar to persist it.
RowLayout {
    id: strip
    property var state: Modes.defaults()          // {research: bool, computer_use: bool}
    property int threadId: 0                      // > 0: the open chat's root task (the tooltips say where the choice is kept)
    property bool active: true
    property bool dense: false                    // the compact popup: no labels, icons + switches only
    signal toggled(string key, bool on)
    readonly property var tokens: Modes.TOKENS.strip
    readonly property alias switches: repeater    // the harness reaches the switches through the Repeater's items
    spacing: tokens.gap
    implicitHeight: tokens.height
    Repeater {
        id: repeater
        model: Modes.TOKENS.modes
        delegate: RowLayout {
            id: one
            required property var modelData
            readonly property string key: modelData.key
            readonly property bool on: strip.state && strip.state[modelData.key] === true
            readonly property alias sw: control
            spacing: strip.tokens.label_gap
            Layout.preferredHeight: strip.tokens.height
            Kirigami.Icon {
                Layout.preferredWidth: strip.tokens.icon; Layout.preferredHeight: strip.tokens.icon
                source: one.modelData.icon
                isMask: true
                color: one.on ? Kirigami.Theme.highlightColor : Kirigami.Theme.textColor
                opacity: one.on ? 1.0 : 0.55
                Behavior on color { ColorAnimation { duration: Modes.TOKENS.motion.colour_ms; easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: Modes.TOKENS.motion.colour_ms } }
            }
            Text {
                id: label
                visible: !strip.dense
                text: one.modelData.label
                color: Kirigami.Theme.textColor
                opacity: one.on ? 0.9 : 0.6
                font.family: "Inter"; font.pixelSize: strip.tokens.font_px; font.weight: Font.Medium
                Behavior on opacity { NumberAnimation { duration: Modes.TOKENS.motion.colour_ms } }
                MouseArea {   // the label is part of the control: a click on the word toggles too
                    anchors.fill: parent; anchors.margins: -4
                    cursorShape: strip.active ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: control.toggle()
                }
            }
            ModeSwitch {
                id: control
                checked: one.on
                active: strip.active
                tip: Modes.tooltip(one.key, one.on, strip.threadId)
                onToggled: (on) => strip.toggled(one.key, on)
            }
        }
    }
}
