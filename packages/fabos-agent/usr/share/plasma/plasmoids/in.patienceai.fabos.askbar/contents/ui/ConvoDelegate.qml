import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Templates as T
import org.kde.kirigami as Kirigami
import "agent.js" as Agent

// One row of the inline conversation. `kind` picks the look:
//   user       right-aligned pill                     assistant  plain Markdown-lite text (+ action row when final)
//   tools      "Worked: N actions" chip (group head)  step       live action card (icon · title · narration · typewriter · state)
//   approval   permission card with Allow / Deny      question   inline answer field
//   error      tinted error card                      note       small muted system line
//   image      generated picture: rounded thumbnail (≤ 320 px tall, fitted to the row), caption = prompt, provider label;
//              a tap asks main.qml for the enlarge viewer (text = absolute path, subtitle = prompt, name = provider)
Item {
    id: del
    required property int index
    required property string kind
    required property string key
    required property string name
    required property string title
    required property string running
    required property string done
    required property string subtitle
    required property string narration
    required property string icon
    required property string iconFallback
    required property bool appIcon
    required property string status
    required property string text
    required property string typed
    required property string risk
    required property int group
    required property bool shown
    required property int count
    required property bool expanded
    required property int approvalId
    required property int questionId
    required property bool current

    property bool showRaw: false
    property color codeBg: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
    property color hairline: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
    property real rise: 0                       // the add transition slides new rows from +12 px to 0

    signal decide(int approvalId, string decision)
    signal answer(string answerText)
    signal toggleGroup(int group)
    signal copyText(string copied)
    signal retry()
    signal openExternal()
    signal openImage(string path, string prompt, string provider)

    readonly property bool collapsed: kind === "step" && !shown
    readonly property string mono: "JetBrains Mono"
    readonly property string ui: "Inter"

    // typewriter for type_text steps: reveal ~25 ms per character, only for steps that are still being typed
    property int reveal: 0
    property bool typewriter: false
    readonly property string typedCapped: typed.length > 600 ? typed.substring(0, 600) + "…" : typed
    readonly property string typedShown: typewriter ? typedCapped.substring(0, reveal) + (reveal < typedCapped.length ? "▏" : "") : typedCapped
    NumberAnimation { id: revealAnim; target: del; property: "reveal"; from: 0; to: del.typedCapped.length; duration: Math.min(8000, 25 * Math.max(1, del.typedCapped.length)) }
    Component.onCompleted: if (kind === "step" && typed.length && (status === "running" || status === "pending")) { typewriter = true; revealAnim.start() }

    // the list's right gutter (14 px) is the overlay scrollbar's lane: every row stops short of it, so nothing is ever under the bar
    width: ListView.view ? ListView.view.width - (ListView.view.gutter || 0) : 600
    implicitHeight: collapsed ? 0 : (loader.item ? loader.item.implicitHeight + 8 : 0)
    height: implicitHeight
    clip: kind === "step"
    opacity: collapsed ? 0 : 1
    Behavior on implicitHeight { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
    Behavior on opacity { NumberAnimation { duration: 200 } }

    Loader {
        id: loader
        y: del.rise
        width: parent.width
        sourceComponent: del.kind === "user" ? userComp : del.kind === "assistant" ? assistantComp : del.kind === "tools" ? toolsComp
                       : del.kind === "step" ? stepComp : del.kind === "approval" ? approvalComp : del.kind === "question" ? questionComp
                       : del.kind === "error" ? errorComp : del.kind === "image" ? imageComp : noteComp
    }

    // ---- generated image: card (radius 16); the picture itself is drawn ONCE by a Canvas (QPainter into an image buffer,
    // clipped to a radius-12 rounded rectangle — deterministic in every scene-graph backend; a ShaderEffectSource into
    // Kirigami.ShadowedTexture came out upside-down and a MultiEffect layer mask came out blank under the RHI in the
    // headless image). A hidden Image decodes the file (shared pixmap cache) and its implicit size — the decoded picture —
    // drives the box; no sourceSize cap, because Qt scales a raster UP to a requested sourceSize (640 px became 2560 px).
    // Fitted to the row's width and never taller than 320 px, fading in when painted; caption = the prompt, a small
    // provider line; the whole card is a tap target for the enlarge viewer. A file that vanished shows a note.
    Component {
        id: imageComp
        Rectangle {
            id: imgCard
            radius: 16
            color: Kirigami.Theme.alternateBackgroundColor
            border.width: 1
            border.color: imgArea.containsMouse ? Kirigami.Theme.highlightColor : del.hairline
            Behavior on border.color { ColorAnimation { duration: 160 } }
            readonly property int pad: 8
            readonly property int maxThumb: 320
            readonly property bool ready: pic.status === Image.Ready
            readonly property bool broken: pic.status === Image.Error
            // the picture's box: as wide as the card allows, ≤ 320 px tall, the aspect ratio kept (so the rounded corners are the picture's own)
            readonly property real srcW: pic.implicitWidth > 0 ? pic.implicitWidth : 4
            readonly property real srcH: pic.implicitHeight > 0 ? pic.implicitHeight : 3
            readonly property real boxW: Math.max(1, width - pad * 2)
            readonly property real thumbH: broken ? 0 : Math.round(Math.min(maxThumb, boxW * srcH / srcW))
            readonly property real thumbW: broken ? 0 : Math.round(Math.min(boxW, thumbH * srcW / srcH))
            implicitHeight: pad + thumbH + (broken ? 0 : 8) + imgText.implicitHeight + pad + 2
            readonly property bool painted: thumb.painted
            Image {   // decodes the file (asynchronously) and knows its size; never drawn itself
                id: pic
                x: Math.round((imgCard.width - width) / 2); y: imgCard.pad
                width: imgCard.thumbW; height: imgCard.thumbH
                source: del.text.length ? Agent.fileUrl(del.text) : ""
                asynchronous: true
                autoTransform: true
                visible: false
            }
            Canvas {   // the rounded thumbnail: one QPainter pass, re-done only when the box changes
                id: thumb
                x: pic.x; y: pic.y; width: pic.width; height: pic.height
                visible: !imgCard.broken
                renderTarget: Canvas.Image
                renderStrategy: Canvas.Immediate
                property bool painted: false
                readonly property string src: String(pic.source)
                readonly property int rounding: 12
                opacity: painted ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }   // fade-in
                Component.onCompleted: if (src.length) loadImage(src)
                onSrcChanged: { painted = false; if (src.length) loadImage(src) }
                onImageLoaded: requestPaint()
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d"), w = width, h = height, r = rounding
                    ctx.reset()
                    ctx.clearRect(0, 0, w, h)
                    if (w < 2 || h < 2 || !src.length || !isImageLoaded(src)) return
                    ctx.save()
                    ctx.beginPath()
                    ctx.moveTo(r, 0); ctx.lineTo(w - r, 0); ctx.arcTo(w, 0, w, r, r); ctx.lineTo(w, h - r); ctx.arcTo(w, h, w - r, h, r)
                    ctx.lineTo(r, h); ctx.arcTo(0, h, 0, h - r, r); ctx.lineTo(0, r); ctx.arcTo(0, 0, r, 0, r); ctx.closePath()
                    ctx.clip()
                    ctx.drawImage(src, 0, 0, w, h)
                    ctx.restore()
                    painted = true
                }
            }
            Spinner { anchors.centerIn: pic; width: 18; height: 18; visible: !imgCard.ready && !imgCard.broken }
            Rectangle {   // small "enlarge" badge, visible on hover
                anchors.right: pic.right; anchors.top: pic.top; anchors.margins: 8
                width: 26; height: 26; radius: 13
                color: Qt.rgba(0, 0, 0, 0.55)
                visible: imgCard.ready
                opacity: imgArea.containsMouse ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
                Kirigami.Icon { anchors.centerIn: parent; width: 14; height: 14; source: "view-fullscreen"; isMask: true; color: "white" }
            }
            ColumnLayout {
                id: imgText
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: pic.bottom
                anchors.leftMargin: imgCard.pad + 4; anchors.rightMargin: imgCard.pad + 4; anchors.topMargin: imgCard.broken ? 0 : 8
                spacing: 2
                RowLayout {
                    Layout.fillWidth: true
                    visible: imgCard.broken
                    spacing: 8
                    Kirigami.Icon { Layout.preferredWidth: 16; Layout.preferredHeight: 16; source: "image-missing"; isMask: true; color: Kirigami.Theme.neutralTextColor }
                    Text { Layout.fillWidth: true; text: "This image is no longer at " + Agent.basename(del.text); color: Kirigami.Theme.textColor; opacity: 0.7; font.family: del.ui; font.pixelSize: 12; elide: Text.ElideMiddle }
                }
                Text {   // caption = the prompt
                    Layout.fillWidth: true
                    visible: del.subtitle.length > 0
                    text: del.subtitle
                    color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 13; lineHeight: 1.2
                    wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                }
                Text {   // provider label (+ size when known)
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: [Agent.imageProviderLabel(del.name), del.title].filter(function (x) { return x.length }).join(" · ")
                    color: Kirigami.Theme.textColor; opacity: 0.55; font.family: del.ui; font.pixelSize: 11; elide: Text.ElideRight
                }
            }
            MouseArea {
                id: imgArea
                anchors.fill: parent
                hoverEnabled: true
                enabled: imgCard.ready
                cursorShape: imgCard.ready ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: del.openImage(del.text, del.subtitle, del.name)
            }
            QQC2.ToolTip.visible: imgArea.containsMouse && imgCard.ready
            QQC2.ToolTip.text: "Tap to enlarge"
            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
        }
    }

    // ---- user request: right-aligned pill (radius 20, tinted, max 72 % wide)
    Component {
        id: userComp
        Item {
            implicitHeight: pill.height
            Rectangle {
                id: pill
                anchors.right: parent.right
                width: Math.min(userText.implicitWidth + 32, del.width * 0.72)
                height: userText.implicitHeight + 20
                radius: 20
                color: Kirigami.Theme.alternateBackgroundColor
                border.color: del.hairline; border.width: 1
                Text {
                    id: userText
                    anchors.fill: parent; anchors.topMargin: 10; anchors.bottomMargin: 10; anchors.leftMargin: 16; anchors.rightMargin: 16
                    text: del.text; wrapMode: Text.Wrap
                    color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: del.status === "answer" ? 14 : 15; lineHeight: 1.25
                }
            }
        }
    }

    // ---- assistant text: no bubble, Markdown-lite blocks, code blocks as monospace cards, action row when final
    Component {
        id: assistantComp
        Column {
            spacing: 8
            Repeater {
                model: Agent.mdBlocks(del.text, String(del.codeBg))
                delegate: Loader {
                    required property var modelData
                    property var block: modelData
                    width: del.width
                    sourceComponent: block.type === "code" ? codeBlock : textBlock
                }
            }
            Row {
                visible: del.status === "final"
                spacing: 2
                IconButton { icon: "edit-copy"; tip: "Copy"; size: 26; iconSize: 16; onClicked: del.copyText(del.text) }
                IconButton { icon: "view-refresh"; tip: "Try again"; size: 26; iconSize: 16; onClicked: del.retry() }
                IconButton { icon: "window-new"; tip: "Open in Fab AI Controls"; size: 26; iconSize: 16; onClicked: del.openExternal() }
            }
        }
    }
    Component {
        id: textBlock
        Text {
            text: block.html
            textFormat: Text.RichText; wrapMode: Text.Wrap
            color: Kirigami.Theme.textColor; linkColor: Kirigami.Theme.linkColor
            font.family: del.ui; font.pixelSize: 15; lineHeight: 1.25
            onLinkActivated: (link) => Qt.openUrlExternally(link)
            HoverHandler { cursorShape: parent.hoveredLink.length ? Qt.PointingHandCursor : Qt.ArrowCursor }
        }
    }
    // Code keeps its lines: a long line scrolls sideways INSIDE the card (own 6 px overlay bar under the text) and never
    // widens the row or the list. Vertical wheel / drag over a card that fits (or in the vertical direction) still scrolls the chat.
    Component {
        id: codeBlock
        Rectangle {
            id: codeCard
            radius: 12
            color: del.codeBg
            clip: true
            readonly property bool wide: codeText.implicitWidth > codeFlick.width + 0.5      // needs the sideways scroll
            readonly property real overflow: Math.max(0, codeText.implicitWidth - codeFlick.width)
            implicitHeight: codeText.implicitHeight + 20 + (wide ? 10 : 0)                     // room for the bar under the last line
            Flickable {
                id: codeFlick
                anchors.fill: parent; anchors.margins: 10
                contentWidth: Math.max(width, codeText.implicitWidth); contentHeight: height
                flickableDirection: Flickable.HorizontalFlick
                boundsBehavior: Flickable.StopAtBounds
                maximumFlickVelocity: 2000
                interactive: codeCard.wide
                clip: true
                TextEdit {
                    id: codeText
                    width: Math.max(codeFlick.width, implicitWidth)
                    readOnly: true; selectByMouse: true
                    text: block.text; wrapMode: TextEdit.NoWrap; textFormat: TextEdit.PlainText
                    color: Kirigami.Theme.textColor; selectionColor: Kirigami.Theme.highlightColor; selectedTextColor: Kirigami.Theme.highlightedTextColor
                    font.family: del.mono; font.pixelSize: 13
                }
                QQC2.ScrollBar.horizontal: T.ScrollBar {
                    id: hbar
                    policy: codeCard.wide ? T.ScrollBar.AsNeeded : T.ScrollBar.AlwaysOff
                    visible: policy !== T.ScrollBar.AlwaysOff && size > 0 && size < 1
                    implicitHeight: 6; height: 6
                    minimumSize: 0.1
                    padding: 0
                    hoverEnabled: true
                    contentItem: Rectangle {
                        implicitHeight: 6; radius: 3
                        color: Kirigami.Theme.textColor
                        opacity: hbar.pressed ? 0.6 : (hbar.hovered || codeFlick.moving ? 0.5 : 0.28)
                        Behavior on opacity { NumberAnimation { duration: 160 } }
                    }
                }
            }
        }
    }

    // ---- group chip: "Working: N actions" while running, "Worked: N actions" when done; click expands / collapses
    Component {
        id: toolsComp
        Item {
            implicitHeight: chip.height
            Rectangle {
                id: chip
                radius: 12; height: 28
                width: chipRow.implicitWidth + 24
                color: Kirigami.Theme.alternateBackgroundColor
                border.color: chipArea.containsMouse ? Kirigami.Theme.highlightColor : del.hairline; border.width: 1
                Behavior on border.color { ColorAnimation { duration: 160 } }
                Row {
                    id: chipRow
                    anchors.centerIn: parent
                    spacing: 6
                    Spinner { visible: del.status === "running"; width: 12; height: 12; anchors.verticalCenter: parent.verticalCenter }
                    Kirigami.Icon { visible: del.status !== "running"; source: "checkmark"; isMask: true; color: Kirigami.Theme.positiveTextColor; width: 14; height: 14; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: (del.status === "running" ? "Working: " : "Worked: ") + del.count + (del.count === 1 ? " action" : " actions")
                        color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 12; font.weight: Font.Medium
                    }
                    Kirigami.Icon { source: del.expanded ? "arrow-up" : "arrow-down"; width: 12; height: 12; isMask: true; color: Kirigami.Theme.textColor; opacity: 0.7; anchors.verticalCenter: parent.verticalCenter }
                }
                MouseArea { id: chipArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: del.toggleGroup(del.group) }
            }
        }
    }

    // ---- live action card
    Component {
        id: stepComp
        Rectangle {
            id: stepCard
            radius: 14
            implicitHeight: stepRow.implicitHeight + 20
            color: Kirigami.Theme.alternateBackgroundColor
            border.width: del.current ? 1.5 : 1
            border.color: del.current ? Kirigami.Theme.highlightColor : del.hairline
            Behavior on border.color { ColorAnimation { duration: 200 } }
            Component.onCompleted: if (del.name === "open_app" && del.status !== "done") scaleIn.start()
            NumberAnimation { id: scaleIn; target: stepCard; property: "scale"; from: 0.94; to: 1.0; duration: 260; easing.type: Easing.OutCubic }
            RowLayout {
                id: stepRow
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 10; anchors.leftMargin: 12; anchors.rightMargin: 12
                spacing: 10
                Item {
                    Layout.preferredWidth: 26; Layout.preferredHeight: 26; Layout.alignment: Qt.AlignTop
                    Kirigami.Icon {
                        anchors.fill: parent
                        source: del.icon; fallback: del.iconFallback
                        isMask: !del.appIcon; color: Kirigami.Theme.textColor
                        opacity: del.appIcon ? 1.0 : 0.75
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text {
                        Layout.fillWidth: true
                        text: del.status === "done" ? del.done : del.running
                        color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 14; font.weight: Font.Medium
                        wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: del.subtitle.length > 0
                        text: del.subtitle
                        color: del.status === "error" || del.status === "denied" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.textColor
                        opacity: del.status === "error" || del.status === "denied" ? 1.0 : 0.65
                        font.family: del.showRaw && (del.name === "run_shell" || del.name === "write_file" || del.name === "read_file" || del.name === "list_dir" || del.name === "web_fetch") && del.status !== "error" && del.status !== "denied" ? del.mono : del.ui
                        font.pixelSize: 12
                        wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: del.narration.length > 0
                        text: del.narration
                        color: Kirigami.Theme.textColor; opacity: 0.7
                        font.family: del.ui; font.pixelSize: 12; font.italic: true
                        wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        visible: del.typed.length > 0
                        radius: 8
                        color: del.codeBg
                        implicitHeight: typedText.implicitHeight + 12
                        Text {
                            id: typedText
                            anchors.fill: parent; anchors.margins: 6; anchors.leftMargin: 10; anchors.rightMargin: 10
                            text: del.typedShown
                            color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 13
                            wrapMode: Text.Wrap
                        }
                    }
                }
                Item {
                    Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.alignment: Qt.AlignTop
                    Spinner { anchors.fill: parent; visible: del.status === "running" || del.status === "pending"; opacity: del.status === "pending" ? 0.45 : 1.0 }
                    Kirigami.Icon { anchors.fill: parent; visible: del.status === "done"; source: "checkmark"; isMask: true; color: Kirigami.Theme.positiveTextColor }
                    Kirigami.Icon { anchors.fill: parent; visible: del.status === "error" || del.status === "denied"; source: "dialog-cancel"; isMask: true; color: Kirigami.Theme.neutralTextColor }
                }
            }
        }
    }

    // ---- permission request
    Component {
        id: approvalComp
        Rectangle {
            id: apCard
            radius: 16
            implicitHeight: apCol.implicitHeight + 24
            color: Kirigami.Theme.alternateBackgroundColor
            border.width: 1
            border.color: del.status === "pending" ? Qt.rgba(apCard.riskColor.r, apCard.riskColor.g, apCard.riskColor.b, 0.6) : del.hairline
            readonly property color riskColor: del.risk === "LOW" ? Kirigami.Theme.positiveTextColor : (del.risk === "MEDIUM" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.negativeTextColor)
            ColumnLayout {
                id: apCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        radius: 8; implicitHeight: 20; implicitWidth: riskText.implicitWidth + 14
                        color: Qt.rgba(apCard.riskColor.r, apCard.riskColor.g, apCard.riskColor.b, 0.16)
                        Text { id: riskText; anchors.centerIn: parent; text: Agent.riskLabel(del.risk); color: apCard.riskColor; font.family: del.ui; font.pixelSize: 11; font.weight: Font.DemiBold }
                    }
                    Text { Layout.fillWidth: true; text: "Permission needed"; color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight }
                    Text { visible: del.status !== "pending"; text: del.status === "approved" ? "Allowed" : (del.status === "denied" ? "Denied" : (del.status === "expired" ? "Expired" : del.status)); color: Kirigami.Theme.textColor; opacity: 0.7; font.family: del.ui; font.pixelSize: 12 }
                    IconButton { visible: del.status === "pending"; icon: "dialog-cancel"; tip: "Deny"; danger: true; onClicked: del.decide(del.approvalId, "denied") }
                    IconButton { visible: del.status === "pending"; icon: "dialog-ok-apply"; tip: "Allow"; positive: true; onClicked: del.decide(del.approvalId, "approved") }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Kirigami.Icon { Layout.preferredWidth: 18; Layout.preferredHeight: 18; source: del.icon; fallback: del.iconFallback; isMask: !del.appIcon; color: Kirigami.Theme.textColor; opacity: del.appIcon ? 1.0 : 0.75 }
                    Text { Layout.fillWidth: true; text: del.title; color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 13; wrapMode: Text.Wrap }
                }
                Text { Layout.fillWidth: true; visible: del.subtitle.length > 0; text: del.subtitle; color: Kirigami.Theme.textColor; opacity: 0.65; font.family: del.showRaw ? del.mono : del.ui; font.pixelSize: 12; wrapMode: Text.Wrap; maximumLineCount: 4; elide: Text.ElideRight }
                Text { Layout.fillWidth: true; visible: del.narration.length > 0; text: del.narration; color: Kirigami.Theme.textColor; opacity: 0.7; font.family: del.ui; font.pixelSize: 12; font.italic: true; wrapMode: Text.Wrap }
            }
        }
    }

    // ---- question from the agent with an inline answer field
    Component {
        id: questionComp
        Rectangle {
            id: qCard
            radius: 16
            implicitHeight: qCol.implicitHeight + 24
            color: Kirigami.Theme.alternateBackgroundColor
            border.width: 1; border.color: del.status === "pending" ? Kirigami.Theme.highlightColor : del.hairline
            function send() { var t = answerField.text.trim(); if (t.length) { del.answer(t); answerField.text = "" } }
            ColumnLayout {
                id: qCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Kirigami.Icon { Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.alignment: Qt.AlignTop; source: "dialog-question"; isMask: true; color: Kirigami.Theme.highlightColor }
                    Text { Layout.fillWidth: true; text: del.text; color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 14; wrapMode: Text.Wrap }
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: del.status === "pending"
                    spacing: 8
                    QQC2.TextField {
                        id: answerField
                        Layout.fillWidth: true
                        placeholderText: "Type your answer…"
                        font.family: del.ui; font.pixelSize: 14
                        color: Kirigami.Theme.textColor; placeholderTextColor: Kirigami.Theme.disabledTextColor
                        leftPadding: 14; rightPadding: 14
                        background: Rectangle {
                            radius: 14; color: Kirigami.Theme.backgroundColor
                            border.color: answerField.activeFocus ? Kirigami.Theme.highlightColor : del.hairline; border.width: 1
                            Behavior on border.color { ColorAnimation { duration: 160 } }
                        }
                        onAccepted: qCard.send()
                    }
                    IconButton { icon: "document-send"; tip: "Send answer"; positive: true; onClicked: qCard.send() }
                }
                Text { visible: del.status !== "pending"; text: "Answered"; color: Kirigami.Theme.textColor; opacity: 0.6; font.family: del.ui; font.pixelSize: 12 }
            }
        }
    }

    // ---- error
    Component {
        id: errorComp
        Rectangle {
            radius: 14
            implicitHeight: errRow.implicitHeight + 20
            color: Kirigami.Theme.negativeBackgroundColor
            RowLayout {
                id: errRow
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; anchors.leftMargin: 12
                spacing: 10
                Kirigami.Icon { Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.alignment: Qt.AlignTop; source: "dialog-error"; isMask: true; color: Kirigami.Theme.negativeTextColor }
                Text { Layout.fillWidth: true; text: del.text; color: Kirigami.Theme.textColor; font.family: del.ui; font.pixelSize: 13; wrapMode: Text.Wrap; maximumLineCount: 6; elide: Text.ElideRight }
            }
        }
    }

    // ---- muted system note
    Component {
        id: noteComp
        Text {
            text: del.text
            color: Kirigami.Theme.textColor; opacity: 0.55
            font.family: del.ui; font.pixelSize: 12; font.italic: true
            wrapMode: Text.Wrap
        }
    }
}
