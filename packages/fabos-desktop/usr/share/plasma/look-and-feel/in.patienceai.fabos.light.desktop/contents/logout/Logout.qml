/*
    @DISTRO_NAME@ Light shares the dark leave screen: a leave screen is a dark scrim over the desktop in both schemes
    (like the lock screen), and the accent and text colours come from the Complementary colour set either way.
    This wrapper loads the dark package's Logout.qml and forwards every signal the greeter connects to by name, so the
    two packages never drift. Both packages come from fabos-desktop, so the relative path is stable.
*/
import QtQuick

Item {
    id: root
    readonly property bool haveScreen: typeof screenGeometry !== "undefined" && screenGeometry !== null
    width: haveScreen ? screenGeometry.width : 1280
    height: haveScreen ? screenGeometry.height : 800

    signal logoutRequested()
    signal haltRequested()
    signal haltUpdateRequested()
    signal suspendRequested(int spdMethod)
    signal rebootRequested()
    signal rebootRequested2(int opt)
    signal rebootUpdateRequested()
    signal cancelRequested()
    signal lockScreenRequested()
    signal cancelSoftwareUpdateRequested()

    Loader {
        id: screen
        anchors.fill: parent
        source: Qt.resolvedUrl("../../../in.patienceai.fabos.desktop/contents/logout/Logout.qml")
        onLoaded: {
            const names = ["logoutRequested", "haltRequested", "haltUpdateRequested", "suspendRequested", "rebootRequested",
                           "rebootRequested2", "rebootUpdateRequested", "cancelRequested", "lockScreenRequested", "cancelSoftwareUpdateRequested"]
            for (let i = 0; i < names.length; i++) item[names[i]].connect(root[names[i]])
            console.info("fabos-leave: light wrapper forwarding " + names.length + " signals")
        }
        onStatusChanged: if (status === Loader.Error) console.warn("fabos-leave: light wrapper could not load the shared Logout.qml from " + source)
    }
}
