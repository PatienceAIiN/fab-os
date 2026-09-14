import QtQuick
import org.kde.kirigami as Kirigami

// Three pulsing dots ("the agent is thinking"). Loops only while visible.
Row {
    id: dots
    property color color: Kirigami.Theme.textColor
    property bool running: visible
    spacing: 5
    Repeater {
        model: 3
        Rectangle {
            id: dot
            required property int index
            width: 7; height: 7; radius: 3.5
            color: dots.color; opacity: 0.35
            SequentialAnimation on opacity {
                running: dots.running
                loops: Animation.Infinite
                PauseAnimation { duration: dot.index * 160 }
                NumberAnimation { to: 1.0; duration: 320; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.35; duration: 320; easing.type: Easing.InOutSine }
                PauseAnimation { duration: (2 - dot.index) * 160 + 200 }
            }
        }
    }
}
