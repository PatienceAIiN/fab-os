import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import "agent.js" as Agent

// "Ask me to do anything…" — the Fab OS agent's front door on the home screen.
// On the desktop the applet is a full-width transparent strip and this card centres itself from the real screen
// width, so it is centred on every monitor size. In a panel it collapses to one compact row.
// Answers arrive INLINE: "Do it" creates the task and opens a response panel (a PlasmaCore.Dialog anchored to the
// card, growing downward) with the request, a live feed of what the agent does, approvals, questions and the
// result. Nothing else opens; "Open in Fab AI Controls" is an explicit click.
PlasmoidItem {
    id: root
    Kirigami.Theme.colorSet: Kirigami.Theme.Window
    Kirigami.Theme.inherit: false
    preferredRepresentation: fullRepresentation
    Layout.preferredWidth: Kirigami.Units.gridUnit * 34
    Layout.minimumWidth: Kirigami.Units.gridUnit * 22
    Layout.fillHeight: true
    readonly property bool compact: height < Kirigami.Units.gridUnit * 3.4
    readonly property bool onDesktop: !compact
    Plasmoid.backgroundHints: onDesktop ? PlasmaCore.Types.NoBackground : PlasmaCore.Types.DefaultBackground

    // ---- bar / daemon state
    property string status: ""
    property bool configured: true
    property bool aiEnabled: true
    property bool daemonUp: true
    property int daemonMisses: 0
    property bool busy: false                 // any task active anywhere (GET /status)
    property bool sending: false
    property string pendingRequest: ""
    readonly property bool showStatus: !configured || !aiEnabled || !daemonUp || (busy && panelMode === "closed") || sending || status.length > 0

    // ---- this conversation
    property int rootTaskId: 0                // first task; follow-ups carry parent_id = rootTaskId
    property int taskId: 0                    // task polled right now (a retry or follow-up replaces it)
    property string taskStatus: ""
    property string taskRequest: ""
    property string taskTitle: ""
    property string resultText: ""
    property string panelMode: "closed"       // closed | open | min
    property bool closing: false              // closePanel() called; panelMode stays "open"/"min" for the 300 ms shrink
    readonly property bool followUp: panelMode !== "closed" && !closing && rootTaskId > 0   // the next submit threads under rootTaskId
    property bool showRaw: false              // daemon setting ui.show_raw
    property var seen: ({})                   // "t<task>s<step>" / "a<approval>" / "q<question>" -> model row
    property int serial: 0
    property int groupSerial: 0
    property int currentGroup: -1
    property int groupHeaderRow: -1
    property int lastStepRow: -1
    property int lastAssistantRow: -1
    property int collapsedTask: 0             // task whose tool groups were folded when it finished (fold once)
    property int taskFirstGroup: 0            // first tool group of the task polled now (only its groups fold when it ends)
    property string lastApp: ""
    property string toast: ""
    readonly property bool taskActive: Agent.isActive(taskStatus)

    // ---- voice
    property bool voiceChecked: false
    property bool voiceAvailable: false
    property bool listening: false
    property string voiceHint: ""
    property string typeBuffer: ""
    property real typeReveal: 0

    // ---- mark
    property bool markAwake: true
    readonly property string markState: listening ? "listening"
                                      : (!configured || !aiEnabled || (taskStatus === "failed" && panelMode !== "closed") ? "error"
                                      : (taskActive || sending ? "thinking"
                                      : (taskStatus === "done" && panelMode !== "closed" ? "done" : "idle")))

    readonly property color hairline: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
    readonly property color codeBg: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
    readonly property int maxPanelHeight: Math.min(640, Math.max(240, Math.round(Screen.height * 0.62) - 150))

    function shellQuote(s) { return Agent.shellQuote(s) }
    function wake() { root.markAwake = true; sleepTimer.restart() }
    // the daemon writes status=done BEFORE the final step, so poll twice more after a task stops to catch trailing rows
    onTaskActiveChanged: { wake(); if (!taskActive && taskId > 0) { trailingPoll1.restart(); trailingPoll2.restart() } }
    onSendingChanged: wake()
    onListeningChanged: wake()
    onPanelModeChanged: { wake(); if (panelMode === "min" || panelMode === "open") { closeTimer.stop(); root.closing = false; root.openProgress = 1 } }

    Timer { id: sleepTimer; interval: 30000; onTriggered: if (!root.taskActive && !root.sending && !root.listening) root.markAwake = false; else restart() }
    Timer { id: hintTimer; interval: 6000; onTriggered: root.voiceHint = "" }
    Timer { id: toastTimer; interval: 1600; onTriggered: root.toast = "" }
    // panel fully shrunk: forget the task in the bar so the mark returns to idle (the task itself carries on in the daemon)
    Timer { id: closeTimer; interval: 300; onTriggered: { root.closing = false; root.panelMode = "closed"; root.taskStatus = "" } }
    Timer { id: stickyTimer; interval: 10000; onTriggered: root.stickyStatus = false }
    Timer { id: trailingPoll1; interval: 1500; onTriggered: if (root.taskId > 0 && root.panelMode !== "closed") root.api("poll", root.taskId, "GET", "/tasks/" + root.taskId) }
    Timer { id: trailingPoll2; interval: 4000; onTriggered: if (root.taskId > 0 && root.panelMode !== "closed") root.api("poll", root.taskId, "GET", "/tasks/" + root.taskId) }
    property bool stickyStatus: false
    function say(msg) { root.status = msg; root.stickyStatus = true; stickyTimer.restart() }

    // ---------------------------------------------------------------- daemon I/O (curl through the executable engine)
    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source)
            var tag = Agent.parseTag(source)
            if (!tag.kind.length) return
            var code = parseInt(data["exit code"]); if (isNaN(code)) code = 0
            root.handle(tag.kind, tag.ref, code, String(data["stdout"] || ""))
        }
    }
    function run(kind, ref, cmd) { root.serial++; exec.connectSource(Agent.tagged(kind, ref, root.serial, cmd)) }
    function api(kind, ref, method, path, body) { root.run(kind, ref, Agent.apiCommand(method, path, body)) }

    Timer { interval: 4000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.api("status", 0, "GET", "/status") }
    Timer {
        id: pollTimer
        interval: 1500; repeat: true; triggeredOnStart: true
        running: root.panelMode !== "closed" && root.taskId > 0 && root.taskActive
        onTriggered: root.api("poll", root.taskId, "GET", "/tasks/" + root.taskId)
    }
    function pollSoon() { if (pollTimer.running) pollTimer.restart() }

    function handle(kind, ref, code, out) {
        var j = Agent.parseJson(out)
        switch (kind) {
        case "status": root.onStatus(j); break
        case "settings": if (j) root.showRaw = String(j["ui.show_raw"] || "") === "true"; break
        case "create": case "follow": root.onCreated(kind === "follow", code, j); break
        case "poll": if (ref === root.taskId && j && j.id === root.taskId) root.ingest(j); break
        case "cancel": if (ref === root.taskId && j && j.status === "cancelled") root.taskStatus = "cancelled"; break
        case "retry": if (j && j.id) { if (!j.parent_id) root.rootTaskId = j.id; root.switchTask(j.id, "Trying again…") } break
        case "approve": if (j && j.ok === false) root.note("That request had already been decided."); root.pollSoon(); break
        case "answer": root.pollSoon(); break
        case "voicestatus": { var v = Agent.voiceInfo(code, out); root.voiceChecked = true; root.voiceAvailable = v.available; break }
        case "listen": root.onListened(code, out); break
        }
    }

    function onStatus(j) {
        if (!j || j.mode === undefined) {
            root.daemonMisses++
            if (root.daemonMisses >= 2) { root.daemonUp = false; root.status = "The Fab OS agent service is not running yet — click here to open Fab AI Controls" }
            return
        }
        root.daemonMisses = 0; root.daemonUp = true
        root.configured = !!j.provider_ready
        root.aiEnabled = j.ai_enabled === undefined ? true : !!j.ai_enabled
        if (j.ui_show_raw !== undefined) root.showRaw = j.ui_show_raw === true || String(j.ui_show_raw) === "true"
        var t = j.tasks || {}
        var n = (t.running || 0) + (t.queued || 0) + (t.waiting_approval || 0) + (t.waiting_user || 0)
        root.busy = n > 0
        if (!root.configured) root.status = "No AI provider yet — click here to add one in Fab AI Controls → Settings"
        else if (!root.aiEnabled) root.status = "System-Wide AI is off — click here to turn it on in Fab AI Controls"
        else if (root.busy && root.panelMode === "closed") {
            var parts = []
            if (t.running) parts.push(t.running + " running")
            if (t.queued) parts.push(t.queued + " queued")
            if (t.waiting_approval || j.pending_approvals) parts.push((t.waiting_approval || j.pending_approvals) + " waiting for your permission")
            if (t.waiting_user) parts.push(t.waiting_user + " waiting for your answer")
            root.status = parts.join(" · ") + " — click here to see them in Fab AI Controls"
        } else if (!root.sending && !root.stickyStatus) root.status = ""
    }

    // ---------------------------------------------------------------- submit / follow-up / lifecycle
    function submit() {
        var t = field.text.trim()
        if (!t.length || root.sending) return
        root.wake()
        if (!root.daemonUp) { root.say("The Fab OS agent service is not running — click here to open Fab AI Controls. Your request stays in the bar."); return }
        if (!root.configured) { root.say("No AI provider is configured yet — click here to add one (or a local model) in Fab AI Controls → Settings. Your request stays in the bar."); return }
        if (!root.aiEnabled) { root.say("System-Wide AI is off — click here to turn it on in Fab AI Controls. Your request stays in the bar."); return }
        root.sending = true
        root.pendingRequest = t
        var follow = root.followUp
        var body = { request: t }
        if (follow) body.parent_id = root.rootTaskId
        root.api(follow ? "follow" : "create", 0, "POST", "/tasks", body)
        field.text = ""
    }
    function onCreated(follow, code, j) {
        root.sending = false
        if (!j || !j.id) {
            root.say(j && j.error ? String(j.error) : (code !== 0 ? "Could not reach the Fab OS agent service" : "The agent did not accept that request"))
            field.text = root.pendingRequest
            return
        }
        root.status = ""; root.stickyStatus = false
        var reopen = root.panelMode === "closed" || root.closing   // a task created while the panel shrinks (Edit prompt) re-opens it
        if (!follow) { root.resetConversation(); root.rootTaskId = j.id }
        root.taskRequest = root.pendingRequest
        convo.append(root.row({ kind: "user", key: "u" + j.id, text: root.pendingRequest, status: "request" }))
        root.switchTask(j.id, "")
        if (reopen) root.openPanel()
        else if (root.panelMode === "min") root.panelMode = "open"
    }
    function switchTask(id, noteText) {
        root.taskId = id; root.taskStatus = "queued"; root.resultText = ""
        root.currentGroup = -1; root.groupHeaderRow = -1; root.lastStepRow = -1; root.taskFirstGroup = root.groupSerial
        if (noteText.length) root.note(noteText)
        root.wake()
        if (pollTimer.running) pollTimer.restart()
    }
    function resetConversation() {
        convo.clear(); root.seen = ({})
        root.groupSerial = 0; root.currentGroup = -1; root.groupHeaderRow = -1; root.lastStepRow = -1; root.lastAssistantRow = -1
        root.lastApp = ""; root.resultText = ""; root.taskStatus = ""; root.rootTaskId = 0; root.taskId = 0
        list.follow = true
    }
    function cancelTask() { if (root.taskId > 0) root.api("cancel", root.taskId, "POST", "/tasks/" + root.taskId + "/cancel") }
    function retryTask() { if (root.taskId > 0 && !root.taskActive) root.api("retry", root.taskId, "POST", "/tasks/" + root.taskId + "/retry") }
    function editPrompt() { field.text = root.taskRequest; root.closePanel(); field.forceActiveFocus(); field.cursorPosition = field.text.length }
    function copyResult() { root.copyText(root.resultText) }
    function copyText(s) { if (!s.length) return; copyHelper.text = s; copyHelper.selectAll(); copyHelper.copy(); copyHelper.text = ""; root.flash("Copied") }
    function flash(s) { root.toast = s; toastTimer.restart() }
    function openExternal() { root.run("open", 0, "setsid -f fabos-command-center" + (root.taskId > 0 ? " --task " + root.taskId : "") + " >/dev/null 2>&1; echo opened") }
    function openControls(args) { root.run("open", 0, "setsid -f fabos-command-center " + args + " >/dev/null 2>&1; echo opened") }
    function decide(approvalId, decision) {
        var idx = root.seen["a" + approvalId]
        if (idx !== undefined && idx >= 0) convo.setProperty(idx, "status", decision)
        root.api("approve", approvalId, "POST", "/approvals/" + approvalId, { decision: decision })
    }
    function answer(text) {
        for (var i = convo.count - 1; i >= 0; i--) { var r = convo.get(i); if (r.kind === "question" && r.status === "pending") { convo.setProperty(i, "status", "answered"); break } }
        convo.append(root.row({ kind: "user", key: "ans" + root.serial, text: text, status: "answer" }))
        root.api("answer", root.taskId, "POST", "/tasks/" + root.taskId + "/answer", { text: text })
    }
    function note(text) { convo.append(root.row({ kind: "note", key: "n" + root.serial + convo.count, text: text })) }

    // ---------------------------------------------------------------- panel open / close (height grows from the bar)
    property real openProgress: 0
    Behavior on openProgress { id: openBehavior; NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
    function openPanel() {
        closeTimer.stop(); root.closing = false
        openBehavior.enabled = false; root.openProgress = 0; openBehavior.enabled = true
        root.panelMode = "open"
        root.api("settings", 0, "GET", "/settings")
        root.openProgress = 1
    }
    function closePanel() { if (root.panelMode === "closed" || root.closing) return; root.closing = true; root.openProgress = 0; closeTimer.restart() }

    // ---------------------------------------------------------------- task JSON -> conversation rows (append-only, no rebuild)
    function row(o) {
        return { kind: o.kind || "note", key: o.key || "", name: o.name || "", title: o.title || "", running: o.running || "", done: o.done || "",
                 subtitle: o.subtitle || "", narration: o.narration || "", icon: o.icon || "", iconFallback: o.iconFallback || "", appIcon: !!o.appIcon,
                 status: o.status || "", text: o.text || "", typed: o.typed || "", risk: o.risk || "", group: o.group === undefined ? -1 : o.group,
                 shown: o.shown === undefined ? true : !!o.shown, count: o.count || 0, expanded: !!o.expanded, approvalId: o.approvalId || 0,
                 questionId: o.questionId || 0, current: !!o.current }
    }
    function ingest(t) {
        root.taskStatus = String(t.status || "")
        root.resultText = String(t.result || "")
        root.taskTitle = String(t.title || "")
        var steps = t.steps || [], i, s, key
        for (i = 0; i < steps.length; i++) {
            s = steps[i]; key = "t" + t.id + "s" + s.id
            if (root.seen[key] !== undefined) { if (s.kind === "tool_call" && root.seen[key] >= 0) root.updateStep(root.seen[key], s); continue }
            root.appendStep(t, s, key)
        }
        var aps = t.approvals || []
        for (i = 0; i < aps.length; i++) {
            var a = aps[i]; key = "a" + a.id
            if (root.seen[key] !== undefined) { if (root.seen[key] >= 0) convo.setProperty(root.seen[key], "status", String(a.status)); continue }
            if (a.status !== "pending") { root.seen[key] = -1; continue }
            var d = Agent.describeStep({ name: a.tool, input: a.input }, root.showRaw, root.lastApp)
            root.seen[key] = convo.count
            convo.append(root.row({ kind: "approval", key: key, approvalId: a.id, title: d.running, subtitle: d.subtitle, narration: String(a.reason || ""),
                                    risk: String(a.risk || ""), status: "pending", icon: d.icon, iconFallback: d.iconFallback, appIcon: d.appIcon }))
        }
        var qs = t.questions || []
        for (i = 0; i < qs.length; i++) {
            var q = qs[i]; key = "q" + q.id
            if (root.seen[key] !== undefined) { if (q.answer && root.seen[key] >= 0) convo.setProperty(root.seen[key], "status", "answered"); continue }
            if (q.answer) { root.seen[key] = -1; continue }
            root.seen[key] = convo.count
            convo.append(root.row({ kind: "question", key: key, questionId: q.id, text: String(q.question || ""), status: "pending" }))
        }
        if (!root.taskActive && root.collapsedTask !== root.taskId) { root.collapsedTask = root.taskId; root.collapseGroups() }
    }
    function appendStep(t, s, key) {
        var k = String(s.kind || "")
        if (k === "tool_call") {
            if (root.currentGroup < 0) {
                root.currentGroup = root.groupSerial++
                root.groupHeaderRow = convo.count
                convo.append(root.row({ kind: "tools", key: "g" + root.currentGroup, group: root.currentGroup, count: 0, expanded: true, status: "running" }))
            }
            var d = Agent.describeStep(s, root.showRaw, root.lastApp), st = Agent.stepStatus(s)
            if (s.name === "open_app" && d.app.length) root.lastApp = d.app
            if (root.lastStepRow >= 0) convo.setProperty(root.lastStepRow, "current", false)
            root.seen[key] = convo.count; root.lastStepRow = convo.count
            convo.append(root.row({ kind: "step", key: key, name: d.name, running: d.running, done: d.done,
                                    subtitle: st === "error" || st === "denied" ? Agent.stepError(s, root.showRaw) : d.subtitle,
                                    narration: String(s.narration || ""), icon: d.icon, iconFallback: d.iconFallback, appIcon: d.appIcon, status: st,
                                    typed: d.typed, risk: String(s.risk || ""), group: root.currentGroup, shown: true, current: st === "running" || st === "pending" }))
            convo.setProperty(root.groupHeaderRow, "count", convo.get(root.groupHeaderRow).count + 1)
            return
        }
        if (k === "assistant") {
            root.closeGroup()
            var txt = String(s.output || "")
            if (!txt.trim().length) { root.seen[key] = -1; return }
            root.seen[key] = convo.count; root.lastAssistantRow = convo.count
            convo.append(root.row({ kind: "assistant", key: key, text: txt, status: "text" }))
            return
        }
        if (k === "final") {
            root.closeGroup()
            var f = String(s.output || "").trim()
            if (!f.length) { root.seen[key] = -1; return }
            if (root.lastAssistantRow >= 0 && convo.get(root.lastAssistantRow).text.trim() === f) { convo.setProperty(root.lastAssistantRow, "status", "final"); root.seen[key] = root.lastAssistantRow; return }
            root.seen[key] = convo.count; root.lastAssistantRow = convo.count
            convo.append(root.row({ kind: "assistant", key: key, text: f, status: "final" }))
            return
        }
        if (k === "error") { root.closeGroup(); root.seen[key] = convo.count; convo.append(root.row({ kind: "error", key: key, text: String(s.output || s.input || "Something went wrong") })); return }
        if (k === "answer") {                                 // the daemon keeps the user's answer in `input` (Store.step 4th argument)
            var at = String(s.input || s.output || "")
            for (var ai = convo.count - 1; ai >= 0; ai--) {   // answered from the inline card: bind the row appended locally, never duplicate it
                var ar = convo.get(ai)
                if (ar.kind === "user" && ar.status === "answer" && ar.key.indexOf("ans") === 0 && ar.text === at) { convo.setProperty(ai, "key", key); root.seen[key] = ai; return }
            }
            if (!at.trim().length) { root.seen[key] = -1; return }
            root.seen[key] = convo.count; convo.append(root.row({ kind: "user", key: key, text: at, status: "answer" })); return
        }
        if (k === "compact") { root.seen[key] = convo.count; convo.append(root.row({ kind: "note", key: key, text: "Tidied up earlier results to keep going" })); return }
        if (k === "watch_hit") { root.seen[key] = convo.count; convo.append(root.row({ kind: "note", key: key, text: "Watch fired: " + Agent.plainSummary(s.output, 120) })); return }
        root.seen[key] = -1                                   // "question" steps: the card comes from t.questions (has id + answer)
    }
    function updateStep(idx, s) {
        var st = Agent.stepStatus(s), r = convo.get(idx)
        if (r.status === st) return
        convo.setProperty(idx, "status", st)
        if (st === "error" || st === "denied") convo.setProperty(idx, "subtitle", Agent.stepError(s, root.showRaw))
        convo.setProperty(idx, "current", st === "running" || st === "pending")
    }
    function closeGroup() {
        if (root.currentGroup < 0) return
        if (root.groupHeaderRow >= 0) convo.setProperty(root.groupHeaderRow, "status", "done")
        if (root.lastStepRow >= 0) convo.setProperty(root.lastStepRow, "current", false)
        root.currentGroup = -1; root.groupHeaderRow = -1
    }
    function collapseGroups() {   // fold the finished task's groups only; earlier turns keep whatever the user opened
        root.closeGroup()
        for (var i = 0; i < convo.count; i++) {
            var r = convo.get(i)
            if (r.group < root.taskFirstGroup) continue
            if (r.kind === "tools" && r.expanded) convo.setProperty(i, "expanded", false)
            else if (r.kind === "step") { if (r.shown) convo.setProperty(i, "shown", false); if (r.current) convo.setProperty(i, "current", false) }
        }
    }
    function toggleGroup(g) {
        var open = false
        for (var i = 0; i < convo.count; i++) {
            var r = convo.get(i)
            if (r.kind === "tools" && r.group === g) { open = !r.expanded; convo.setProperty(i, "expanded", open) }
            else if (r.kind === "step" && r.group === g) convo.setProperty(i, "shown", open)
        }
    }

    // ---------------------------------------------------------------- voice input (fabos-voice; never records without the click)
    Component.onCompleted: { root.run("voicestatus", 0, "fabos-voice status 2>/dev/null"); sleepTimer.start() }
    function startListening() {
        if (!root.voiceAvailable || root.listening || root.sending) return
        root.wake(); root.listening = true; root.voiceHint = ""
        root.run("listen", 0, "fabos-voice listen-once --timeout 10 2>/dev/null")
    }
    function onListened(code, out) {
        root.listening = false
        var t = out.trim()
        if (code === 0 && t.length) { root.typeInto(t); return }
        if (code === 4 || code === 127) { root.voiceAvailable = false; root.voiceHint = "Voice is not available on this machine"; hintTimer.restart(); return }
        root.voiceHint = code === 3 || code === 0 ? "I did not catch that. Tap the mic and try again." : "Voice did not work just now. Tap the mic to try again."
        hintTimer.restart()
    }
    function typeInto(t) { root.typeBuffer = t; typeAnim.stop(); typeAnim.to = t.length; typeAnim.duration = Math.min(2500, 25 * t.length); typeAnim.start() }
    NumberAnimation { id: typeAnim; target: root; property: "typeReveal"; from: 0; to: 1; duration: 500; onFinished: root.submit() }
    onTypeRevealChanged: if (typeAnim.running) field.text = root.typeBuffer.substring(0, Math.round(root.typeReveal))

    ListModel { id: convo }
    TextEdit { id: copyHelper; visible: false; width: 1; height: 1 }

    // ================================================================ the bar
    Item {
        id: card
        // position of this applet inside the desktop view → lets the card centre on the physical screen
        readonly property real appletScreenX: { var p = root.mapToItem(null, 0, 0); return root.width + Screen.width > 0 ? p.x : 0 }
        width: root.onDesktop ? Math.min(760, Math.round(Screen.width * 0.6), Math.max(320, root.width)) : root.width
        height: root.onDesktop ? Math.min(root.height, Kirigami.Units.gridUnit * 6.2) : root.height
        x: root.onDesktop ? Math.max(0, Math.round((Screen.width - width) / 2 - appletScreenX)) : 0
        y: root.onDesktop ? Math.round((root.height - height) / 2) : 0

        Rectangle {   // Material-expressive surface: large radius, tinted, hairline border
            anchors.fill: parent
            visible: root.onDesktop
            radius: 24
            color: Kirigami.Theme.backgroundColor
            opacity: 0.92
            border.color: root.hairline; border.width: 1
        }
        HoverHandler { id: cardHover; onHoveredChanged: if (hovered) root.wake() }

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: root.onDesktop ? Kirigami.Units.gridUnit : Kirigami.Units.smallSpacing
            anchors.rightMargin: root.onDesktop ? Kirigami.Units.gridUnit : Kirigami.Units.smallSpacing
            anchors.topMargin: root.onDesktop ? Kirigami.Units.largeSpacing : Kirigami.Units.smallSpacing
            anchors.bottomMargin: root.onDesktop ? Kirigami.Units.largeSpacing : Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing
            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: Kirigami.Units.smallSpacing * 2
                AiMark {   // original animated Fab OS AI mark (state folded into its motion and tint)
                    id: mark
                    Layout.preferredWidth: root.compact ? 22 : 32
                    Layout.preferredHeight: Layout.preferredWidth
                    markState: root.markState
                    awake: root.markAwake
                    showDot: !root.configured || !root.daemonUp
                }
                QQC2.TextField {
                    id: field
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.compact ? Math.max(Kirigami.Units.gridUnit * 1.6, root.height - Kirigami.Units.smallSpacing * 2) : Kirigami.Units.gridUnit * 2.4
                    placeholderText: root.listening ? "Listening… speak now" : (root.voiceHint.length ? root.voiceHint : "Ask me to do anything…")
                    font.family: "Inter"; font.pixelSize: 16; color: Kirigami.Theme.textColor; placeholderTextColor: Kirigami.Theme.disabledTextColor
                    leftPadding: 16; rightPadding: 16; verticalAlignment: TextInput.AlignVCenter
                    background: Rectangle {
                        radius: height / 2; color: Kirigami.Theme.alternateBackgroundColor
                        border.color: field.activeFocus ? Kirigami.Theme.highlightColor : Kirigami.Theme.disabledTextColor; border.width: field.activeFocus ? 1.5 : 1
                        Behavior on border.color { ColorAnimation { duration: 180 } }
                    }
                    onAccepted: root.submit()
                    onActiveFocusChanged: if (activeFocus) { root.wake(); if (root.voiceHint.length) root.voiceHint = "" }
                }
                IconButton {   // microphone: records only on click; disabled with an explanation when no speech backend exists
                    id: micButton
                    icon: "audio-input-microphone"
                    size: root.compact ? Math.max(22, field.height - 4) : 36
                    iconSize: root.compact ? 16 : 20
                    active: root.voiceAvailable && !root.listening && !root.sending
                    danger: root.listening
                    tip: !root.voiceAvailable ? "Voice is not available on this machine" : (root.listening ? "Listening… speak now" : "Speak your request")
                    onClicked: root.startListening()
                    Rectangle {   // soft accent ring while listening
                        anchors.fill: parent; radius: width / 2; color: "transparent"
                        border.color: Kirigami.Theme.negativeTextColor; border.width: 1.5
                        opacity: root.listening ? 0.9 : 0
                        scale: root.listening ? 1.0 : 0.8
                        Behavior on opacity { NumberAnimation { duration: 200 } }
                        Behavior on scale { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                    }
                }
                Rectangle {   // Do it — animated pill
                    id: go
                    Layout.preferredWidth: root.compact ? Kirigami.Units.gridUnit * 3.6 : Kirigami.Units.gridUnit * 5.6
                    Layout.preferredHeight: field.height
                    radius: height / 2
                    color: goArea.pressed ? Qt.darker(Kirigami.Theme.highlightColor, 1.2) : (goArea.containsMouse ? Qt.lighter(Kirigami.Theme.highlightColor, 1.15) : Kirigami.Theme.highlightColor)
                    scale: goArea.pressed ? 0.95 : (goArea.containsMouse ? 1.04 : 1.0)
                    opacity: field.text.trim().length || root.sending ? 1.0 : 0.7
                    Behavior on color { ColorAnimation { duration: 160 } }
                    Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
                    Behavior on opacity { NumberAnimation { duration: 160 } }
                    Text { anchors.centerIn: parent; text: root.sending ? "" : (root.followUp ? "Send" : "Do it"); color: Kirigami.Theme.highlightedTextColor; font.family: "Inter"; font.pixelSize: 15; font.weight: Font.DemiBold }
                    Spinner { anchors.centerIn: parent; width: 18; height: 18; color: Kirigami.Theme.highlightedTextColor; visible: root.sending }
                    MouseArea { id: goArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.submit() }
                }
            }
            Text {
                id: statusText
                Layout.fillWidth: true
                Layout.leftMargin: (root.compact ? 22 : 32) + Kirigami.Units.smallSpacing * 2
                visible: !root.compact && root.showStatus && root.status.length > 0
                text: root.status
                wrapMode: Text.WordWrap; maximumLineCount: 2
                color: root.configured && root.aiEnabled && root.daemonUp ? Kirigami.Theme.disabledTextColor : Kirigami.Theme.neutralTextColor
                font.family: "Inter"; font.pixelSize: 12; elide: Text.ElideRight
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: root.openControls(root.configured && root.aiEnabled ? "" : "--settings" + (field.text.trim().length ? " --prefill " + root.shellQuote(field.text.trim()) : "")) }
            }
        }
        QQC2.ToolTip.visible: root.compact && root.showStatus && root.status.length > 0 && hoverHandler.hovered
        QQC2.ToolTip.text: root.status
        HoverHandler { id: hoverHandler }
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.RightButton | Qt.MiddleButton; z: -1
                    onClicked: root.openControls("") }
    }

    // ================================================================ inline response panel (grows downward from the bar)
    PlasmaCore.Dialog {
        id: panel
        visualParent: card
        location: root.onDesktop ? PlasmaCore.Types.TopEdge : Plasmoid.location
        type: PlasmaCore.Dialog.AppletPopup
        hideOnWindowDeactivate: false
        backgroundHints: PlasmaCore.Dialog.StandardBackground
        visible: root.panelMode !== "closed"

        readonly property real contentTarget: root.panelMode === "min" ? minPill.implicitHeight
                                            : Math.min(root.maxPanelHeight, panelHeader.implicitHeight + 10 + list.contentHeight + 4)
        property real panelHeight: contentTarget
        Behavior on panelHeight { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }

        mainItem: Item {
            id: panelMain
            Kirigami.Theme.colorSet: Kirigami.Theme.Window
            Kirigami.Theme.inherit: false
            width: root.onDesktop ? Math.max(320, card.width - panel.margins.left - panel.margins.right) : Math.min(560, Math.round(Screen.width * 0.5))
            height: Math.max(8, Math.round(panel.panelHeight * root.openProgress))
            clip: true

            // ---- expanded conversation
            Item {
                id: panelBody
                anchors.fill: parent
                anchors.margins: 4
                visible: opacity > 0
                opacity: root.panelMode === "open" ? root.openProgress : 0
                Behavior on opacity { NumberAnimation { duration: 200 } }

                RowLayout {   // status line left, icon controls right
                    id: panelHeader
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    spacing: 2
                    Spinner { visible: root.taskActive; running: root.panelMode === "open"; Layout.preferredWidth: 14; Layout.preferredHeight: 14; Layout.leftMargin: 6 }
                    Kirigami.Icon { visible: !root.taskActive && root.taskStatus === "done"; source: "checkmark"; isMask: true; color: Kirigami.Theme.positiveTextColor; Layout.preferredWidth: 16; Layout.preferredHeight: 16; Layout.leftMargin: 6 }
                    Kirigami.Icon { visible: !root.taskActive && (root.taskStatus === "failed" || root.taskStatus === "cancelled"); source: "dialog-cancel"; isMask: true; color: Kirigami.Theme.neutralTextColor; Layout.preferredWidth: 16; Layout.preferredHeight: 16; Layout.leftMargin: 6 }
                    Text {
                        Layout.fillWidth: true; Layout.leftMargin: 6
                        text: root.toast.length ? root.toast : Agent.statusLabel(root.taskStatus)
                        color: Kirigami.Theme.textColor; opacity: 0.7
                        font.family: "Inter"; font.pixelSize: 12; font.weight: Font.Medium; elide: Text.ElideRight
                    }
                    IconButton { icon: "media-playback-stop"; tip: "Stop"; visible: root.taskActive; onClicked: root.cancelTask() }
                    IconButton { icon: "view-refresh"; tip: root.taskActive ? "Retry (available when finished)" : "Retry"; active: !root.taskActive && root.taskId > 0; onClicked: root.retryTask() }
                    IconButton { icon: "document-edit"; tip: "Edit prompt"; onClicked: root.editPrompt() }
                    IconButton { icon: "edit-copy"; tip: root.resultText.length ? "Copy result" : "Copy result (nothing yet)"; active: root.resultText.length > 0; onClicked: root.copyResult() }
                    IconButton { icon: "arrow-up"; tip: "Minimize"; onClicked: root.panelMode = "min" }
                    IconButton { icon: "window-new"; tip: "Open in Fab AI Controls"; onClicked: root.openExternal() }
                }

                ListView {   // the conversation for THIS task (+ follow-ups); rows are appended, never rebuilt
                    id: list
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: panelHeader.bottom; anchors.topMargin: 6; anchors.bottom: parent.bottom
                    anchors.leftMargin: 8; anchors.rightMargin: 8
                    clip: true
                    spacing: 0
                    boundsBehavior: Flickable.StopAtBounds
                    model: convo
                    property bool follow: true
                    onMovementEnded: follow = atYEnd
                    onFlickEnded: follow = atYEnd
                    onCountChanged: if (follow) Qt.callLater(list.positionViewAtEnd)
                    onContentHeightChanged: if (follow && !moving) Qt.callLater(list.positionViewAtEnd)
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: QQC2.ScrollBar.AsNeeded }
                    footer: Item {
                        width: list.width
                        height: root.taskActive ? 30 : 8
                        Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                        TypingDots { anchors.left: parent.left; anchors.leftMargin: 6; anchors.verticalCenter: parent.verticalCenter; visible: root.taskActive && root.panelMode === "open"; color: Kirigami.Theme.textColor }
                    }
                    add: Transition {
                        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 260; easing.type: Easing.OutCubic }
                        NumberAnimation { property: "rise"; from: 12; to: 0; duration: 260; easing.type: Easing.OutCubic }
                    }
                    delegate: ConvoDelegate {
                        showRaw: root.showRaw
                        codeBg: root.codeBg
                        hairline: root.hairline
                        onDecide: (approvalId, decision) => root.decide(approvalId, decision)
                        onAnswer: (answerText) => root.answer(answerText)
                        onToggleGroup: (group) => root.toggleGroup(group)
                        onCopyText: (copied) => root.copyText(copied)
                        onRetry: root.retryTask()
                        onOpenExternal: root.openExternal()
                    }
                }
            }

            // ---- minimized: one-line status pill (click to reopen)
            Item {
                id: minPill
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                implicitHeight: 40
                visible: opacity > 0
                opacity: root.panelMode === "min" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 200 } }
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 4
                    spacing: 8
                    Spinner { visible: root.taskActive; running: root.panelMode === "min"; Layout.preferredWidth: 14; Layout.preferredHeight: 14 }
                    Kirigami.Icon { visible: !root.taskActive && root.taskStatus === "done"; source: "checkmark"; isMask: true; color: Kirigami.Theme.positiveTextColor; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                    Kirigami.Icon { visible: !root.taskActive && root.taskStatus !== "done"; source: "dialog-cancel"; isMask: true; color: Kirigami.Theme.neutralTextColor; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                    Text {
                        Layout.fillWidth: true
                        text: Agent.statusLabel(root.taskStatus) + (root.taskTitle.length ? " · " + root.taskTitle : "")
                        color: Kirigami.Theme.textColor; opacity: 0.85
                        font.family: "Inter"; font.pixelSize: 13; elide: Text.ElideRight
                    }
                    IconButton { icon: "arrow-down"; tip: "Show"; size: 28; iconSize: 16; onClicked: root.panelMode = "open" }
                    IconButton { icon: "window-close"; tip: "Dismiss"; size: 28; iconSize: 16; onClicked: root.closePanel() }
                }
                MouseArea { anchors.fill: parent; z: -1; cursorShape: Qt.PointingHandCursor; onClicked: root.panelMode = "open" }
            }
        }
    }
}
