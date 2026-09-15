import QtQuick
import org.kde.plasma.configuration

ConfigModel {
    ConfigCategory {
        name: "Bar"
        icon: "configure"
        source: "configGeneral.qml"
    }
    ConfigCategory {
        name: "Tiles"
        icon: "view-grid"
        source: "configTiles.qml"
    }
}
