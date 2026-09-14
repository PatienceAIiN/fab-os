import QtQuick
import org.kde.plasma.private.brightnesscontrolplugin as Brightness

// Screen brightness through powerdevil's org.kde.ScreenBrightness D-Bus service (the same plugin the stock brightness
// applet uses; /sys/class/backlight is root-only for writes). Loaded through a Loader so a missing plugin only
// disables the slider. `percent` mirrors the first display; setPercent() writes every display proportionally.
Item {
    id: bridge
    readonly property bool available: control.isBrightnessAvailable && rep.count > 0
    property int percent: -1
    function setPercent(pct) {
        for (var i = 0; i < rep.count; i++) {
            var it = rep.itemAt(i)
            if (it && it.max > 0) control.setBrightness(it.name, Math.max(1, Math.round(it.max * pct / 100)))
        }
    }
    function sync() {
        var it = rep.count > 0 ? rep.itemAt(0) : null
        bridge.percent = (it && it.max > 0) ? Math.round(it.value * 100 / it.max) : -1
    }
    Brightness.ScreenBrightnessControl { id: control; isSilent: true }
    Repeater {
        id: rep
        model: control.displays
        delegate: Item {
            required property string displayName
            required property int brightness
            required property int maxBrightness
            readonly property string name: displayName
            readonly property int value: brightness
            readonly property int max: maxBrightness
            onValueChanged: bridge.sync()
            onMaxChanged: bridge.sync()
            Component.onCompleted: bridge.sync()
            Component.onDestruction: bridge.sync()
        }
    }
}
