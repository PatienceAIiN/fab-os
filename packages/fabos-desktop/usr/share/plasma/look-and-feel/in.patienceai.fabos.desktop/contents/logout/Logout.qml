/*
    @DISTRO_NAME@ leave screen: the confirmation that appears before shutting down, restarting or logging out.

    plasma-workspace's ksmserver-logout-greeter loads this file as the look-and-feel package's "logoutmainscript"
    (contents/logout/Logout.qml). The greeter's contract, read from the 6.6.6 binary in the image (UTF-16 literals) and
    from Breeze's own Logout.qml:
      context properties  sdtype (KWorkSpace::ShutdownType: -1 every option, 0 log out, 1 restart, 2 shut down, 3 log out),
                          maysd, canLogout, softwareUpdatePending, spdMethods {StandbyState, SuspendState, HibernateState},
                          rebootToFirmwareSetup, rebootToBootLoaderMenu, rebootToBootLoaderEntry, screenGeometry
      root signals        logoutRequested haltRequested haltUpdateRequested suspendRequested(int) rebootRequested
                          rebootRequested2(int) rebootUpdateRequested cancelRequested lockScreenRequested cancelSoftwareUpdateRequested
    The ShutdownType enum object is registered by the greeter binary itself, so this file compares plain numbers and
    reads every context property through typeof: it also loads in a runtime that sets none of them (tests/logout-screen-test.sh).
    If this file fails to compile the greeter logs "Trying default theme" and shows Breeze's screen instead; the test
    fails on exactly that line.

    What it shows, and why (docs/design/SHUTDOWN.md):
      * the open applications, one row per window, from libtaskmanager's TasksModel (the same backend as the dock).
        KWin hands the Wayland window list only to processes whose .desktop entry asks for org_kde_plasma_window_management;
        fabos-desktop ships that entry for the greeter (usr/share/applications/in.patienceai.fabos.logout-greeter.desktop).
        If the list is still empty after the settle time, session-apps.sh lists the applications from the systemd user
        units Plasma starts them in (names, no titles) and the screen says that unsaved work cannot be flagged that way.
      * an "Unsaved" flag on windows whose title carries the usual modified markers (Qt's [*] placeholder rendered as a
        leading or trailing asterisk, KDE's "[modified]", GTK's "(modified)", the VS Code / GNOME bullets). Any flagged
        window pauses the countdown: the machine never turns itself off over work that looks unsaved. Apps that do not
        mark their titles are not caught; the text says so and the buttons stay under the user's control.
      * a countdown (ksmserverrc [General] confirmLogoutCountdown, default 30 s, 0 = wait for a click) that also stops
        for good on any key press or a hover over the buttons, so an approaching hand never loses the race.
      * the safe choice as the default while work looks unsaved: keyboard focus moves to Cancel and the primary button
        reads "Shut down anyway". On Wayland an app's own Cancel in its save dialog does not stop the logout: KWin posts
        "The following applications did not close … Logging out anyway in 2 minutes" and carries on unless the user
        clicks Cancel Logout there (seen in the VM, docs/design/SHUTDOWN.md). The decision has to be made here.
    Design tokens: card radius 24, inner card 20, controls 12, Inter through the system font, colours from the
    Complementary colour set so the accent follows the user's scheme; the scrim is the brand ink at 86 %.
*/
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.coreaddons as KCoreAddons
import org.kde.taskmanager as TaskManager
import org.kde.plasma.plasma5support as P5Support
import org.kde.plasma.private.sessions as Sessions

Item {
    id: root
    Kirigami.Theme.inherit: false
    Kirigami.Theme.colorSet: Kirigami.Theme.Complementary
    readonly property bool haveScreen: typeof screenGeometry !== "undefined" && screenGeometry !== null
    width: haveScreen ? screenGeometry.width : 1280
    height: haveScreen ? screenGeometry.height : 800

    // --- greeter API -------------------------------------------------------------------------------------------
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

    // KWorkSpace::ShutdownType as numbers; 3 (ShutdownTypeLogout) is folded into 0 (ShutdownTypeNone = log out)
    readonly property int mode: { const m = typeof sdtype === "undefined" ? 2 : Number(sdtype); return m === 3 ? 0 : (isNaN(m) ? 2 : m) }
    readonly property bool showAll: root.mode === -1
    readonly property bool canShutdown: typeof maysd === "undefined" ? true : maysd === true
    readonly property bool canLogOut: typeof canLogout === "undefined" ? true : canLogout === true
    readonly property bool updatesPending: typeof softwareUpdatePending !== "undefined" && softwareUpdatePending === true
    readonly property bool canSleep: typeof spdMethods !== "undefined" && spdMethods !== null && spdMethods.SuspendState === true
    readonly property bool canHibernate: typeof spdMethods !== "undefined" && spdMethods !== null && spdMethods.HibernateState === true
    readonly property bool toFirmware: typeof rebootToFirmwareSetup !== "undefined" && rebootToFirmwareSetup === true
    readonly property bool toBootMenu: typeof rebootToBootLoaderMenu !== "undefined" && rebootToBootLoaderMenu === true
    readonly property string toBootEntry: typeof rebootToBootLoaderEntry !== "undefined" && rebootToBootLoaderEntry ? String(rebootToBootLoaderEntry) : ""

    readonly property var verbs: ({
        "-1": { title: "Leave",     now: "",              anyway: "",                 ing: "",              noun: "leaving",      icon: "system-log-out",  tail: "before you leave" },
        "0":  { title: "Log out",   now: "Log out now",   anyway: "Log out anyway",   ing: "Logging out",   noun: "logging out",  icon: "system-log-out",  tail: "before you sign out" },
        "1":  { title: "Restart",   now: "Restart now",   anyway: "Restart anyway",   ing: "Restarting",    noun: "the restart",  icon: "system-reboot",   tail: "before restarting" },
        "2":  { title: "Shut down", now: "Shut down now", anyway: "Shut down anyway", ing: "Shutting down", noun: "the shutdown", icon: "system-shutdown", tail: "before turning off" }
    })
    readonly property var verb: root.verbs[String(root.mode)] || root.verbs["2"]
    readonly property bool actionAvailable: root.mode === 0 ? root.canLogOut : ((root.mode === 1 || root.mode === 2) && root.canShutdown)

    // --- open applications -----------------------------------------------------------------------------------------
    TaskManager.TasksModel {
        id: tasksModel
        groupMode: TaskManager.TasksModel.GroupDisabled
        sortMode: TaskManager.TasksModel.SortAlpha
        filterByVirtualDesktop: false
        filterByScreen: false
        filterByActivity: false
        filterMinimized: false
        filterNotMinimized: false
    }
    // The greeter's own window (when it runs --windowed) and startup-feedback rows are not "open apps".
    function isListed(isWindow, appId, appName) {
        if (isWindow === false) return false
        const a = String(appId || "") + " " + String(appName || "")
        return a.indexOf("logout-greeter") < 0 && a.indexOf("@DISTRO_NAME@ leave screen") < 0
    }
    // one QtObject per row so a title that turns "modified" while the screen is up re-counts at once
    Instantiator {
        id: mirror
        model: tasksModel
        delegate: QtObject {
            readonly property string title: String(model.display || "")
            readonly property bool listed: root.isListed(model.IsWindow, model.AppId, model.AppName)
            readonly property bool unsaved: listed && root.looksUnsaved(title)
            onUnsavedChanged: root.recount()
            onListedChanged: root.recount()
        }
        onObjectAdded: (index, object) => root.recount()
        onObjectRemoved: (index, object) => root.recount()
    }
    property int windowCount: 0
    property int unsavedCount: 0
    function recount() {
        let n = 0, u = 0
        for (let i = 0; i < mirror.count; i++) { const o = mirror.objectAt(i); if (o && o.listed) { n++; if (o.unsaved) u++ } }
        root.windowCount = n
        root.unsavedCount = u
    }
    // Title markers that editors use for unsaved work: Qt's [*] placeholder rendered as a leading or trailing asterisk
    // ("*Untitled — Kate", "notes.txt* — App"), KDE's "[modified]", GTK's "(modified)", VS Code's / GNOME's bullets.
    function looksUnsaved(title) {
        const t = String(title || "").trim()
        if (!t) return false
        if (/\[modified\]|\(modified\)/i.test(t)) return true
        if (/^[*●•]/.test(t)) return true
        if (/[*●•]\s*(?:[—–-]\s*[^—–-]{1,60})?$/.test(t)) return true
        return false
    }

    ListModel { id: fallbackModel }     // roles named like TasksModel's so one delegate serves both: display, decoration, AppName, count
    property bool settled: false
    property bool helperDone: false
    Timer { interval: 900; running: true; onTriggered: root.settled = true }
    readonly property bool useTasks: root.windowCount > 0
    readonly property bool useFallback: !root.useTasks && root.settled && fallbackModel.count > 0
    readonly property string appsSource: root.useTasks ? "windows" : (root.useFallback ? "apps" : "none")
    readonly property int openCount: root.useTasks ? root.windowCount : (root.useFallback ? fallbackModel.count : 0)
    onSettledChanged: if (settled) console.info("fabos-leave: apps source=" + root.appsSource + " count=" + root.openCount + " unsaved=" + root.unsavedCount)

    // session-apps.sh: the countdown length from ksmserverrc and the systemd-unit application list (fallback only)
    readonly property string helperPath: { const u = Qt.resolvedUrl("session-apps.sh").toString(); return u.indexOf("file://") === 0 ? decodeURIComponent(u.substring(7)) : "" }
    P5Support.DataSource {
        id: helper
        engine: "executable"
        onNewData: (source, data) => { disconnectSource(source); root.applyHelper(String(data["stdout"] || "")) }
    }
    function applyHelper(out) {
        const lines = String(out).split("\n")
        for (let i = 0; i < lines.length; i++) {
            const f = lines[i].split("\t")
            if (f[0] === "countdown") { const n = parseInt(f[1], 10); if (!isNaN(n) && n >= 0 && n <= 600) root.countdownSeconds = n }
            else if (f[0] === "app" && f.length >= 4) fallbackModel.append({ display: f[2], decoration: f[3], AppName: f[2], AppId: f[1], count: f.length >= 5 ? (parseInt(f[4], 10) || 1) : 1 })
        }
        root.helperDone = true
    }

    // --- countdown -------------------------------------------------------------------------------------------------
    property int countdownSeconds: 30
    property int remaining: 30
    onCountdownSecondsChanged: root.remaining = root.countdownSeconds
    property bool held: false                       // a key press or a hover over the controls stops the clock for good
    readonly property bool countdownActive: !root.showAll && !root.held && root.unsavedCount === 0 && root.countdownSeconds > 0 && root.actionAvailable
    Timer { interval: 1000; repeat: true; running: root.countdownActive; onTriggered: { root.remaining--; if (root.remaining <= 0) root.act() } }
    function hold() { if (!root.held) { root.held = true; console.info("fabos-leave: countdown held") } }
    function act() {
        console.info("fabos-leave: action mode=" + root.mode + " updates=" + root.updatesPending)
        switch (root.mode) {
        case 0: root.logoutRequested(); break
        case 1: if (root.updatesPending) root.rebootUpdateRequested(); else root.rebootRequested(); break
        case 2: if (root.updatesPending) root.haltUpdateRequested(); else root.haltRequested(); break
        }
    }
    readonly property string countdownText: {
        if (root.showAll || !root.actionAvailable) return ""
        if (root.unsavedCount > 0) return "The countdown is paused while work looks unsaved."
        if (root.held || root.countdownSeconds === 0) return "Take your time. Nothing happens until you choose."
        return root.verb.ing + " in " + root.remaining + " s. Press any key to wait."
    }
    Keys.onPressed: (event) => { root.hold() }        // keys reach the root after the focused button; never accepted here, so Escape still cancels

    KCoreAddons.KUser { id: kuser }
    Sessions.SessionsModel { id: otherSessions; includeUnusedSessions: false; includeOwnSession: false }
    QQC2.Action { shortcut: "Escape"; onTriggered: root.cancelRequested() }

    Component.onCompleted: {
        console.info("fabos-leave: ready mode=" + root.mode + " showAll=" + root.showAll + " canShutdown=" + root.canShutdown + " canLogout=" + root.canLogOut + " screen=" + root.width + "x" + root.height)
        if (root.helperPath) helper.connectSource("sh '" + root.helperPath.replace(/'/g, "'\\''") + "' all")
        else root.helperDone = true
    }

    // --- scrim ----------------------------------------------------------------------------------------------------
    Rectangle { anchors.fill: parent; color: "#0E1116"; opacity: 0.86 }
    MouseArea { anchors.fill: parent; onClicked: root.cancelRequested() }

    // --- card -----------------------------------------------------------------------------------------------------
    Kirigami.ShadowedRectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(640, root.width - 48)
        height: Math.min(content.implicitHeight + 56, root.height - 32)
        radius: 24
        color: Qt.alpha(Kirigami.Theme.backgroundColor, 0.96)
        border.color: Qt.rgba(1, 1, 1, 0.10)
        border.width: 1
        shadow.size: 48
        shadow.yOffset: 12
        shadow.color: Qt.rgba(0, 0, 0, 0.55)
        MouseArea { anchors.fill: parent }             // clicks inside the card never reach the scrim's cancel
        opacity: 0; scale: 0.98
        Component.onCompleted: { opacity = 1; scale = 1 }
        Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

        ColumnLayout {
            id: content
            anchors { fill: parent; margins: 28 }
            spacing: 16

            // header: what is about to happen
            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                Rectangle {
                    Layout.preferredWidth: 56; Layout.preferredHeight: 56; radius: 16
                    color: Qt.alpha(Kirigami.Theme.highlightColor, 0.18)
                    Kirigami.Icon { anchors.centerIn: parent; width: 30; height: 30; source: root.verb.icon; color: Kirigami.Theme.highlightColor; isMask: true }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    QQC2.Label {
                        id: titleLabel
                        Layout.fillWidth: true
                        text: root.verb.title
                        font.pixelSize: 28; font.weight: Font.DemiBold
                        color: Kirigami.Theme.textColor
                        elide: Text.ElideRight
                    }
                    QQC2.Label {
                        id: subtitleLabel
                        Layout.fillWidth: true
                        text: "@DISTRO_NAME@ asks each app to save its work " + root.verb.tail + "."
                        font.pixelSize: 14
                        color: Qt.alpha(Kirigami.Theme.textColor, 0.72)
                        wrapMode: Text.WordWrap
                    }
                }
                QQC2.Label {
                    Layout.alignment: Qt.AlignTop
                    visible: kuser.loginName !== ""
                    text: kuser.fullName || kuser.loginName
                    font.pixelSize: 12
                    color: Qt.alpha(Kirigami.Theme.textColor, 0.55)
                }
            }

            // open applications
            Rectangle {
                id: appsCard
                Layout.fillWidth: true
                implicitHeight: appsColumn.implicitHeight + 28
                radius: 20
                color: Qt.rgba(1, 1, 1, 0.05)
                border.color: Qt.rgba(1, 1, 1, 0.07); border.width: 1
                ColumnLayout {
                    id: appsColumn
                    anchors { fill: parent; margins: 14 }
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.Label {
                            id: appsHeader
                            text: root.openCount > 0 ? ("Open apps · " + root.openCount) : "Open apps"
                            font.pixelSize: 13; font.weight: Font.DemiBold
                            color: Qt.alpha(Kirigami.Theme.textColor, 0.72)
                        }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            id: unsavedChip
                            visible: root.unsavedCount > 0
                            implicitWidth: unsavedChipLabel.implicitWidth + 20; implicitHeight: 24; radius: 12
                            color: Qt.alpha(Kirigami.Theme.neutralTextColor, 0.18)
                            QQC2.Label { id: unsavedChipLabel; anchors.centerIn: parent; text: root.unsavedCount === 1 ? "1 unsaved" : root.unsavedCount + " unsaved"; font.pixelSize: 12; font.weight: Font.DemiBold; color: Kirigami.Theme.neutralTextColor }
                        }
                    }
                    ListView {
                        id: appsList
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(root.openCount, 5) * 52
                        visible: root.openCount > 0
                        clip: true
                        interactive: root.openCount > 5
                        boundsBehavior: Flickable.StopAtBounds
                        model: root.useTasks ? tasksModel : fallbackModel
                        QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: root.openCount > 5 ? QQC2.ScrollBar.AlwaysOn : QQC2.ScrollBar.AlwaysOff }
                        delegate: Item {
                            id: row
                            width: appsList.width
                            readonly property bool listed: root.useTasks ? root.isListed(model.IsWindow, model.AppId, model.AppName) : true
                            height: listed ? 52 : 0
                            visible: listed
                            readonly property string title: String(model.display || "")
                            readonly property string appName: String(model.AppName || "")
                            readonly property bool unsaved: root.useTasks && listed && root.looksUnsaved(row.title)
                            readonly property int instances: root.useTasks ? 1 : (model.count || 1)
                            Rectangle { anchors.fill: parent; anchors.leftMargin: -6; anchors.rightMargin: -6; radius: 12; color: Qt.rgba(1, 1, 1, 0.04); visible: index % 2 === 1 }
                            RowLayout {
                                anchors { fill: parent; leftMargin: 4; rightMargin: 4 }
                                spacing: 12
                                Kirigami.Icon { Layout.preferredWidth: 32; Layout.preferredHeight: 32; source: model.decoration; fallback: "application-x-executable" }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    QQC2.Label {
                                        Layout.fillWidth: true
                                        text: root.useTasks ? (row.appName || row.title) : row.title
                                        font.pixelSize: 14; font.weight: Font.DemiBold
                                        color: Kirigami.Theme.textColor
                                        elide: Text.ElideRight
                                    }
                                    QQC2.Label {
                                        Layout.fillWidth: true
                                        visible: text !== ""
                                        text: root.useTasks ? (row.title !== row.appName ? row.title : "") : (row.instances > 1 ? row.instances + " instances running" : "")
                                        font.pixelSize: 12
                                        color: Qt.alpha(Kirigami.Theme.textColor, 0.62)
                                        elide: Text.ElideMiddle
                                    }
                                }
                                Rectangle {
                                    visible: row.unsaved
                                    implicitWidth: rowChipLabel.implicitWidth + 16; implicitHeight: 22; radius: 11
                                    color: Qt.alpha(Kirigami.Theme.neutralTextColor, 0.18)
                                    QQC2.Label { id: rowChipLabel; anchors.centerIn: parent; text: "Unsaved"; font.pixelSize: 11; font.weight: Font.DemiBold; color: Kirigami.Theme.neutralTextColor }
                                }
                            }
                        }
                    }
                    RowLayout {
                        id: emptyRow
                        visible: root.openCount === 0
                        spacing: 10
                        Kirigami.Icon { Layout.preferredWidth: 22; Layout.preferredHeight: 22; source: root.settled ? "checkmark" : "view-refresh"; color: root.settled ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.textColor; isMask: true }
                        QQC2.Label {
                            id: emptyLabel
                            Layout.fillWidth: true
                            text: root.settled ? "No open apps found." : "Looking for open apps…"
                            font.pixelSize: 14
                            color: Qt.alpha(Kirigami.Theme.textColor, 0.72)
                        }
                    }
                    QQC2.Label {
                        id: fallbackNote
                        Layout.fillWidth: true
                        visible: root.useFallback
                        text: "Window titles are not visible from here, so unsaved work cannot be flagged. Please check these apps yourself."
                        font.pixelSize: 12
                        color: Qt.alpha(Kirigami.Theme.textColor, 0.62)
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // warnings
            RowLayout {
                id: unsavedWarning
                Layout.fillWidth: true
                visible: root.unsavedCount > 0
                spacing: 10
                Kirigami.Icon { Layout.preferredWidth: 22; Layout.preferredHeight: 22; Layout.alignment: Qt.AlignTop; source: "data-warning"; color: Kirigami.Theme.neutralTextColor; isMask: true }
                QQC2.Label {
                    id: unsavedLabel
                    Layout.fillWidth: true
                    text: (root.unsavedCount === 1 ? "One window looks unsaved. Save it first: " : root.unsavedCount + " windows look unsaved. Save them first: ")
                          + "if you continue, each app asks you once, and a window left open only delays " + root.verb.noun + ", it does not stop it."
                    font.pixelSize: 13
                    color: Kirigami.Theme.neutralTextColor
                    wrapMode: Text.WordWrap
                }
            }
            QQC2.Label {
                Layout.fillWidth: true
                visible: otherSessions.count > 0 && root.mode !== 0
                text: otherSessions.count === 1 ? "Another user is signed in on this computer and could lose work."
                                                 : otherSessions.count + " other users are signed in on this computer and could lose work."
                font.pixelSize: 13
                color: Kirigami.Theme.neutralTextColor
                wrapMode: Text.WordWrap
            }
            QQC2.Label {
                Layout.fillWidth: true
                visible: root.mode === 1 && (root.toFirmware || root.toBootMenu || root.toBootEntry !== "")
                text: root.toFirmware ? "The computer restarts into its firmware setup." : (root.toBootMenu ? "The computer restarts into the boot menu." : "The computer restarts into " + root.toBootEntry + ".")
                font.pixelSize: 13
                color: Qt.alpha(Kirigami.Theme.textColor, 0.72)
                wrapMode: Text.WordWrap
            }
            QQC2.Label {
                id: unavailableLabel
                Layout.fillWidth: true
                visible: !root.showAll && !root.actionAvailable
                text: root.mode === 0 ? "Logging out is not available for this account right now." : "Shutting down is not allowed for this account right now."
                font.pixelSize: 13
                color: Kirigami.Theme.negativeTextColor
                wrapMode: Text.WordWrap
            }

            // countdown
            QQC2.Label {
                id: countdownLabel
                Layout.fillWidth: true
                visible: text !== ""
                text: root.countdownText
                font.pixelSize: 14
                color: Qt.alpha(Kirigami.Theme.textColor, 0.72)
                wrapMode: Text.WordWrap
            }

            // controls: all options (Ctrl+Alt+Del / "Leave") as tiles, one action as Cancel + primary
            Flow {
                id: allOptions
                Layout.fillWidth: true
                visible: root.showAll
                spacing: 10
                LeaveButton { id: sleepTile; tile: true; visible: root.canSleep; text: "Sleep"; iconName: "system-suspend"; onClicked: root.suspendRequested(2); onInteracted: root.hold() }
                LeaveButton { id: hibernateTile; tile: true; visible: root.canHibernate; text: "Hibernate"; iconName: "system-suspend-hibernate"; onClicked: root.suspendRequested(4); onInteracted: root.hold() }
                LeaveButton { id: restartTile; tile: true; visible: root.canShutdown; text: root.updatesPending ? "Update and restart" : "Restart"; iconName: "system-reboot"
                              onClicked: root.updatesPending ? root.rebootUpdateRequested() : root.rebootRequested(); onInteracted: root.hold() }
                LeaveButton { id: shutdownTile; tile: true; primary: true; visible: root.canShutdown; text: root.updatesPending ? "Update and shut down" : "Shut down"; iconName: "system-shutdown"
                              onClicked: root.updatesPending ? root.haltUpdateRequested() : root.haltRequested(); onInteracted: root.hold(); focus: root.showAll }
                LeaveButton { id: logoutTile; tile: true; visible: root.canLogOut; text: "Log out"; iconName: "system-log-out"; onClicked: root.logoutRequested(); onInteracted: root.hold() }
                LeaveButton { id: cancelTile; tile: true; text: "Cancel"; iconName: "dialog-cancel"; onClicked: root.cancelRequested(); onInteracted: root.hold() }
            }
            RowLayout {
                id: actions
                Layout.fillWidth: true
                Layout.topMargin: 4
                visible: !root.showAll
                spacing: 12
                LeaveButton {
                    id: cancelButton
                    text: "Cancel"
                    iconName: "dialog-cancel"
                    onClicked: root.cancelRequested()
                    onInteracted: root.hold()
                    KeyNavigation.right: skipUpdatesButton.visible ? skipUpdatesButton : primaryButton
                    focus: !root.showAll && (!root.actionAvailable || root.unsavedCount > 0)     // unsaved work: Enter must not leave
                }
                Item { Layout.fillWidth: true }
                LeaveButton {
                    id: skipUpdatesButton
                    visible: root.updatesPending && root.actionAvailable && root.mode !== 0
                    text: root.mode === 1 ? "Restart without updating" : "Shut down without updating"
                    onClicked: root.mode === 1 ? root.rebootRequested() : root.haltRequested()
                    onInteracted: root.hold()
                    KeyNavigation.left: cancelButton
                    KeyNavigation.right: primaryButton
                }
                LeaveButton {
                    id: primaryButton
                    primary: true
                    visible: root.actionAvailable
                    text: root.updatesPending && root.mode !== 0 ? (root.mode === 1 ? "Update and restart" : "Update and shut down") : (root.unsavedCount > 0 ? root.verb.anyway : root.verb.now)
                    iconName: root.verb.icon
                    onClicked: root.act()
                    onInteracted: root.hold()
                    KeyNavigation.left: skipUpdatesButton.visible ? skipUpdatesButton : cancelButton
                    focus: !root.showAll && root.actionAvailable && root.unsavedCount === 0
                }
            }

            // footnote
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 2
                spacing: 8
                Kirigami.Icon { Layout.preferredWidth: 16; Layout.preferredHeight: 16; source: "@LOGO_ICON_NAME@"; fallback: "start-here" }
                QQC2.Label {
                    id: footnote
                    Layout.fillWidth: true
                    text: "Ask before closing is on: @DISTRO_NAME@ shows this screen every time. Fab Settings › Session › Desktop Session."
                    font.pixelSize: 12
                    color: Qt.alpha(Kirigami.Theme.textColor, 0.55)
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
