import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/modes.js" as Modes

// The Research / Computer use switch (docs/design/MODES.md): a pill track that recolours to the accent while a round knob
// slides across — 200 ms OutCubic for both, every number from Modes.TOKENS (the same block Fab AI Controls paints from).
// `checked` is owned by the caller (the strip's state): a click or Space/Enter only emits toggled(on); the caller persists
// it and writes the new state back, which is what moves the knob. Keyboard: Tab focus (accent ring), Space / Enter toggle.
Item {
    id: sw
    property bool checked: false
    property string tip: ""
    property bool active: true
    signal toggled(bool on)
    readonly property var tokens: Modes.TOKENS["switch"]
    readonly property var motion: Modes.TOKENS.motion
    readonly property bool hovered: area.containsMouse
    // 0 = off, 1 = on; a Behavior makes the binding's jump a slide (the knob's x and the colours follow this one number)
    property real progress: checked ? 1 : 0
    Behavior on progress { NumberAnimation { duration: sw.motion.knob_ms; easing.type: Easing.OutCubic } }

    implicitWidth: tokens.width; implicitHeight: tokens.height
    width: implicitWidth; height: implicitHeight
    activeFocusOnTab: true
    Accessible.role: Accessible.CheckBox
    Accessible.name: sw.tip
    Accessible.checked: sw.checked
    Accessible.onToggleAction: sw.toggle()
    Accessible.onPressAction: sw.toggle()
    function toggle() { if (sw.active) sw.toggled(!sw.checked) }
    Keys.onSpacePressed: (event) => { sw.toggle(); event.accepted = true }
    Keys.onReturnPressed: (event) => { sw.toggle(); event.accepted = true }
    Keys.onEnterPressed: (event) => { sw.toggle(); event.accepted = true }

    Rectangle {   // track: text @ 28 % when off, the accent when on (the colour follows `progress`, so it moves with the knob)
        id: track
        anchors.fill: parent
        radius: sw.tokens.track_radius
        readonly property color offColor: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, sw.tokens.off_alpha)
        readonly property color onColor: Kirigami.Theme.highlightColor
        readonly property color base: Qt.rgba(offColor.r + (onColor.r - offColor.r) * sw.progress, offColor.g + (onColor.g - offColor.g) * sw.progress,
                                              offColor.b + (onColor.b - offColor.b) * sw.progress, offColor.a + (onColor.a - offColor.a) * sw.progress)
        color: sw.hovered && sw.active ? Qt.lighter(base, 1 + sw.tokens.hover_lift) : base
        opacity: sw.active ? 1 : 0.5
        Behavior on opacity { NumberAnimation { duration: sw.motion.colour_ms } }
    }
    Rectangle {   // knob: slides pad -> width - knob - pad; white on the accent, the surface colour on the grey track
        id: knob
        width: sw.tokens.knob; height: sw.tokens.knob; radius: sw.tokens.knob / 2
        x: Modes.knobX(sw.progress)
        y: Math.round((sw.height - sw.tokens.knob) / 2)
        color: sw.progress > 0.5 ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.backgroundColor
        Behavior on color { ColorAnimation { duration: sw.motion.colour_ms; easing.type: Easing.OutCubic } }
        scale: area.pressed && sw.active ? 0.92 : 1.0
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    }
    Rectangle {   // keyboard focus ring: the accent, 1.5 px, outside the track
        anchors.fill: parent; anchors.margins: -3
        radius: sw.tokens.track_radius + 3
        color: "transparent"
        border.color: Kirigami.Theme.highlightColor; border.width: sw.tokens.focus_ring
        visible: sw.activeFocus
    }
    MouseArea {
        id: area
        anchors.fill: parent
        anchors.margins: -4                                   // a little slack around a 22 px control
        hoverEnabled: true
        cursorShape: sw.active ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: { sw.forceActiveFocus(); sw.toggle() }
    }
    QQC2.ToolTip.visible: area.containsMouse && sw.tip.length > 0
    QQC2.ToolTip.text: sw.tip
    QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
}
