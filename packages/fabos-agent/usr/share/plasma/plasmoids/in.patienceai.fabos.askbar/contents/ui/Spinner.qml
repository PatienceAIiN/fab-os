import QtQuick
import QtQuick.Shapes
import org.kde.kirigami as Kirigami

// Small accent arc spinner for a step that is still running (only spins while visible and shown).
// An Item wraps the Shape so the arc geometry follows the Item's size (a Shape sizing itself from its own path would loop).
Item {
    id: spin
    property color color: Kirigami.Theme.highlightColor
    property bool running: visible
    implicitWidth: 16; implicitHeight: 16
    Shape {
        id: arc
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: spin.color; strokeWidth: 2; fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: spin.width / 2; centerY: spin.height / 2; radiusX: Math.max(1, spin.width / 2 - 1.5); radiusY: radiusX; startAngle: 0; sweepAngle: 270 }
        }
        RotationAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: spin.running && spin.visible }
    }
}
