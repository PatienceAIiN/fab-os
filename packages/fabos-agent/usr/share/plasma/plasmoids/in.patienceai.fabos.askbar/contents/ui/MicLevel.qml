import QtQuick
import org.kde.kirigami as Kirigami

// Microphone level meter (1.0-8): five rounded bars that rise with `level` (0..1, from the listen run's progress file,
// relative to the noise floor) — red while the bar is only hearing the room, the accent colour once speech was heard.
// The middle bar is the tallest so the meter reads as a voice, not a graph.
Row {
    id: meter
    property real level: 0
    property bool speech: false
    property int barWidth: 3
    property int barHeight: 14
    spacing: 2
    height: barHeight
    Repeater {
        model: 5
        Rectangle {
            readonly property real weight: [0.55, 0.8, 1.0, 0.8, 0.55][index]
            width: meter.barWidth
            anchors.verticalCenter: parent.verticalCenter
            height: Math.max(3, Math.round(meter.barHeight * Math.min(1, 0.15 + meter.level * weight)))
            radius: width / 2
            color: meter.speech ? Kirigami.Theme.highlightColor : Kirigami.Theme.negativeTextColor
            Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            Behavior on color { ColorAnimation { duration: 200 } }
        }
    }
}
