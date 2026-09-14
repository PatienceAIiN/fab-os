import QtQuick
import QtQuick.Shapes
import org.kde.kirigami as Kirigami

// Original Fab OS AI mark, drawn in QML (no image assets): a soft accent orb, a slowly orbiting ring of three
// short arcs and a breathing glow. Static frame for docs: brand/logo/fabos-ai-mark.svg.
//   idle       slow breathe (4 s) + very slow orbit           thinking   1.2 s orbit + pulse
//   listening  ring pulses at 0.9 s + small red dot            done       one expanding ring that fades
//   error      amber tint (Kirigami.Theme.neutralTextColor)   awake=false stops every loop (idle > 30 s)
Item {
    id: mark
    property string markState: "idle"          // idle | thinking | listening | done | error
    property bool awake: true
    property bool showDot: false               // extra state dot (red = not configured)
    property color dotColor: Kirigami.Theme.negativeTextColor
    property color accent: Kirigami.Theme.highlightColor
    property color amber: Kirigami.Theme.neutralTextColor
    property color tone: markState === "error" ? amber : accent
    readonly property bool busy: markState === "thinking" || markState === "listening"
    readonly property int periodMs: markState === "thinking" ? 1200 : (markState === "listening" ? 900 : 4000)
    readonly property real unit: Math.min(width, height) / 32

    implicitWidth: 32; implicitHeight: 32
    Behavior on tone { ColorAnimation { duration: 320 } }

    onMarkStateChanged: {
        if (markState === "done") doneRing.restart()
        if (breathe.running) breathe.restart()
        if (orbit.running) orbit.restart()
    }

    // glow: breathing halo behind the orb
    Rectangle {
        id: glow
        anchors.centerIn: parent
        width: 22 * mark.unit; height: width; radius: width / 2
        color: mark.tone; opacity: 0.22
        SequentialAnimation {
            id: breathe
            running: mark.awake && mark.visible
            loops: Animation.Infinite
            NumberAnimation { target: glow; property: "scale"; from: 1.0; to: mark.busy ? 1.45 : 1.25; duration: mark.periodMs / 2; easing.type: Easing.InOutSine }
            NumberAnimation { target: glow; property: "scale"; to: 1.0; duration: mark.periodMs / 2; easing.type: Easing.InOutSine }
        }
    }

    // ring: three short arcs orbiting the orb
    Shape {
        id: ring
        anchors.centerIn: parent
        width: 30 * mark.unit; height: width
        preferredRendererType: Shape.CurveRenderer
        opacity: mark.markState === "listening" ? 1.0 : 0.85
        ShapePath {
            strokeColor: mark.tone; strokeWidth: Math.max(1.5, 2 * mark.unit); fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: ring.width / 2; centerY: ring.height / 2; radiusX: 13 * mark.unit; radiusY: radiusX; startAngle: -90; sweepAngle: 62 }
            PathAngleArc { centerX: ring.width / 2; centerY: ring.height / 2; radiusX: 13 * mark.unit; radiusY: radiusX; startAngle: 30; sweepAngle: 62; moveToStart: true }
            PathAngleArc { centerX: ring.width / 2; centerY: ring.height / 2; radiusX: 13 * mark.unit; radiusY: radiusX; startAngle: 150; sweepAngle: 62; moveToStart: true }
        }
        RotationAnimation {
            id: orbit
            target: ring; property: "rotation"; from: 0; to: 360
            duration: mark.markState === "thinking" ? 1200 : (mark.markState === "listening" ? 2400 : 14000)
            loops: Animation.Infinite
            running: mark.awake && mark.visible
        }
        // listening: the ring pulses at 0.9 s
        SequentialAnimation {
            running: mark.markState === "listening" && mark.awake
            loops: Animation.Infinite
            onRunningChanged: if (!running) ring.scale = 1.0
            NumberAnimation { target: ring; property: "scale"; from: 1.0; to: 1.14; duration: 450; easing.type: Easing.InOutSine }
            NumberAnimation { target: ring; property: "scale"; to: 1.0; duration: 450; easing.type: Easing.InOutSine }
        }
    }

    // orb with a soft highlight
    Rectangle {
        id: orb
        anchors.centerIn: parent
        width: 16 * mark.unit; height: width; radius: width / 2
        color: mark.tone
        Rectangle { x: parent.width * 0.22; y: parent.height * 0.18; width: parent.width * 0.42; height: width; radius: width / 2; color: "white"; opacity: 0.38 }
        SequentialAnimation {
            running: mark.markState === "thinking" && mark.awake
            loops: Animation.Infinite
            onRunningChanged: if (!running) orb.scale = 1.0
            NumberAnimation { target: orb; property: "scale"; from: 1.0; to: 1.12; duration: 600; easing.type: Easing.InOutSine }
            NumberAnimation { target: orb; property: "scale"; to: 1.0; duration: 600; easing.type: Easing.InOutSine }
        }
    }

    // done: a ring expands once and fades
    Rectangle {
        id: doneRingItem
        anchors.centerIn: parent
        width: 18 * mark.unit; height: width; radius: width / 2
        color: "transparent"; border.color: mark.tone; border.width: Math.max(1, 1.5 * mark.unit)
        opacity: 0; scale: 0.7
        ParallelAnimation {
            id: doneRing
            NumberAnimation { target: doneRingItem; property: "scale"; from: 0.7; to: 2.1; duration: 700; easing.type: Easing.OutCubic }
            SequentialAnimation {
                NumberAnimation { target: doneRingItem; property: "opacity"; from: 0.0; to: 0.9; duration: 120 }
                NumberAnimation { target: doneRingItem; property: "opacity"; to: 0.0; duration: 580; easing.type: Easing.OutCubic }
            }
        }
    }

    // small state dot (listening = red recording dot; showDot = e.g. not configured)
    Rectangle {
        visible: mark.showDot || mark.markState === "listening"
        width: 8 * mark.unit + 1; height: width; radius: width / 2
        anchors.right: parent.right; anchors.bottom: parent.bottom
        color: mark.dotColor
        border.color: Kirigami.Theme.backgroundColor; border.width: 1.5
        SequentialAnimation on opacity {
            running: mark.markState === "listening" && mark.awake
            loops: Animation.Infinite
            NumberAnimation { from: 1.0; to: 0.35; duration: 450 }
            NumberAnimation { to: 1.0; duration: 450 }
        }
    }
}
