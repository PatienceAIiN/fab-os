import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import "status.js" as Status

// "Tiles" page of the Fab OS quick settings: every tile the pane can show, in order, with a checkbox (shown / hidden),
// a size (Small = one column of the three-column grid, Medium = two, Wide = the full row) and up / down arrows; "Reset
// to default" restores the shipped layout. The same model is edited in the pane itself (pencil: drag to reorder, size
// cycle, remove). Stored as JSON in cfg_tilesJson (see status.js parseTiles / tilesJson).
KCM.SimpleKCM {
    id: page
    property string cfg_tilesJson
    property string cfg_tilesJsonDefault: ""
    // the other entries of the group, declared so the framework does not warn (they belong to the Bar page)
    property string cfg_barSize
    property bool cfg_magnify
    property bool cfg_showSpeed
    property int cfg_pollSeconds

    readonly property var tiles: Status.parseTiles(page.cfg_tilesJson)
    function save(arr) { page.cfg_tilesJson = Status.tilesJson(arr) }

    ColumnLayout {
        spacing: 8
        QQC2.Label {
            Layout.fillWidth: true
            text: "Tiles in the pane, top to bottom, in a grid of three columns: a small tile takes one column, a medium one two, a wide one the whole row. You can also press the pencil in the pane and drag the tiles around."
            wrapMode: Text.WordWrap
            opacity: 0.8
        }
        Repeater {
            model: page.tiles.length
            delegate: RowLayout {
                id: rowItem
                required property int index
                readonly property var tile: page.tiles[index]
                readonly property var def: Status.tileDef(tile.id)
                Layout.fillWidth: true
                spacing: 8
                QQC2.CheckBox {
                    checked: rowItem.tile.enabled
                    text: rowItem.def ? rowItem.def.title : rowItem.tile.id
                    Layout.preferredWidth: 180
                    onToggled: page.save(Status.setTileEnabled(page.tiles, rowItem.tile.id, checked))
                }
                QQC2.ComboBox {
                    model: ["Small", "Medium", "Wide"]
                    currentIndex: Math.max(0, Status.SIZES.indexOf(rowItem.tile.size))
                    Layout.preferredWidth: 120
                    onActivated: page.save(Status.setTileSize(page.tiles, rowItem.tile.id, Status.SIZES[currentIndex]))
                }
                QQC2.ToolButton { icon.name: "arrow-up"; enabled: rowItem.index > 0; onClicked: page.save(Status.moveTile(page.tiles, rowItem.index, rowItem.index - 1)); QQC2.ToolTip.text: "Move up"; QQC2.ToolTip.visible: hovered }
                QQC2.ToolButton { icon.name: "arrow-down"; enabled: rowItem.index < page.tiles.length - 1; onClicked: page.save(Status.moveTile(page.tiles, rowItem.index, rowItem.index + 1)); QQC2.ToolTip.text: "Move down"; QQC2.ToolTip.visible: hovered }
                Item { Layout.fillWidth: true }
            }
        }
        Item { Layout.preferredHeight: 8 }
        QQC2.Button {
            text: "Reset to default"
            icon.name: "edit-undo"
            onClicked: page.cfg_tilesJson = ""
        }
        QQC2.Label {
            Layout.fillWidth: true
            text: "Brightness shows only with a controllable backlight, Battery with a battery or power profiles, Power profile only with power-profiles-daemon, Night light only when KWin offers it. The footer always carries the network line (interface · IP · speed), so the Network speed tile is off by default."
            wrapMode: Text.WordWrap
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            opacity: 0.7
        }
    }
}
