import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import "../ui" as Ask

// Headless driver for the ask bar. tests/askbar-qml-test.sh copies the plasmoid into a temp package, appends one
// Loader line to that COPY of main.qml which loads this file and hands over the root item and its ids, then runs the
// package through plasmawindowed with QT_QPA_PLATFORM=offscreen. The driver feeds main.qml the JSON the daemon would
// return for one task and checks the conversation rows it builds, the in-applet response panel (geometry under the
// card, growth / fold animation, no PlasmaCore.Dialog on the desktop, no pointer handler on the transparent strip),
// the microphone feedback path (a real `fabos-voice listen-once` inside the image) and the remembered conversation.
// Every ConvoDelegate kind and every AiMark state are instantiated too. Prints PASS/FAIL lines and
// "HARNESS DONE failures=N", renders /out/askbar-{bar,panel,feed}.png, then stops plasmawindowed.
Item {
    id: h
    property var bar: null
    property var convo: null
    property var card: null
    property var panel: null
    property var panelMain: null
    property var popup: null
    property var statusText: null
    property var field: null
    property var list: null
    property int failures: 0
    property int grabsPending: 0
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function kinds() { var k = []; for (var i = 0; i < convo.count; i++) k.push(convo.get(i).kind); return k.join(",") }
    function rowAt(i) { return convo.get(i) }
    // plasmawindowed opens the applet at its Layout.minimum size (396x128 here) while the desktop layout gives the strip
    // sh*0.66: the harness sizes the applet like a small home screen before the checks (700x640; Screen is 800x600 offscreen)
    onListChanged: if (bar && convo && card && panel && panelMain && popup && statusText && field && list) {
        bar.Layout.minimumWidth = 700; bar.Layout.minimumHeight = 640     // plasmawindowed sizes its window from these hints
        var w = h.Window.window; if (w) { w.minimumWidth = 700; w.minimumHeight = 640; w.width = 700; w.height = 640 }
        startTimer.start()
    }
    Timer { id: startTimer; interval: 400; onTriggered: h.run() }
    P5Support.DataSource { id: killer; engine: "executable"; connectedSources: [] }

    // static instances of every helper component (compile + instantiate check)
    Row {
        y: 1000; spacing: 8
        Ask.AiMark { width: 32; height: 32; markState: "idle" }
        Ask.AiMark { width: 32; height: 32; markState: "thinking" }
        Ask.AiMark { width: 32; height: 32; markState: "listening" }
        Ask.AiMark { width: 32; height: 32; markState: "done" }
        Ask.AiMark { width: 32; height: 32; markState: "error"; showDot: true }
        Ask.AiMark { width: 32; height: 32; markState: "idle"; awake: false }
        Ask.TypingDots { }
        Ask.Spinner { width: 18; height: 18 }
        Ask.IconButton { icon: "edit-copy"; tip: "Copy" }
        Ask.IconButton { icon: "dialog-cancel"; tip: "Deny"; danger: true; active: false }
        Ask.IconButton { icon: "audio-input-microphone"; tip: "Mic"; dim: true }
    }

    // every ConvoDelegate kind, outside a ListView (required properties set explicitly)
    Component { id: delegateComp; Ask.ConvoDelegate { } }
    function makeRow(o) {
        var base = { index: 0, kind: "note", key: "k", name: "", title: "", running: "", done: "", subtitle: "", narration: "", icon: "", iconFallback: "",
                     appIcon: false, status: "", text: "", typed: "", risk: "", group: -1, shown: true, count: 0, expanded: false, approvalId: 0, questionId: 0, current: false }
        for (var k in o) base[k] = o[k]
        base.width = 600
        return delegateComp.createObject(h, base)
    }

    // the daemon's JSON for one task at successive moments (1 running, 2 waiting_approval, 2.5 waiting_user, 3 done)
    function taskJson(phase) {
        var steps = [
            { id: 101, task_id: 7, kind: "tool_call", name: "open_app", input: JSON.stringify({ app: "kate", args: ["~/Documents/note.txt"] }), output: phase >= 2 ? JSON.stringify({ launched: "kate", pid: 44 }) : "", risk: "LOW", decision: "auto-approved", narration: "Opening Fab Editor for you now." }
        ]
        if (phase >= 2) {
            steps.push({ id: 102, task_id: 7, kind: "tool_call", name: "type_text", input: JSON.stringify({ text: "Hello from Fab OS", press_enter: true }), output: phase >= 3 ? JSON.stringify({ typed: 17 }) : "", risk: "LOW", decision: "auto-approved", narration: "Typing your note into Fab Editor." })
            steps.push({ id: 103, task_id: 7, kind: "tool_call", name: "run_shell", input: JSON.stringify({ command: "rm -rf ~/old-notes" }), output: phase >= 3 ? JSON.stringify({ error: "Denied by user/policy (HIGH: deletes files)" }) : "", risk: "HIGH", decision: phase >= 3 ? "denied" : "" })
        }
        if (phase >= 2.5) steps.push({ id: 104, task_id: 7, kind: "question", name: "ask_user", input: "Which folder should I use instead?", output: "", risk: "", decision: "" })
        if (phase >= 3) {
            steps.push({ id: 105, task_id: 7, kind: "answer", name: "user", input: "Use ~/Notes", output: "", risk: "", decision: "" })   // Store.step(task, "answer", "user", text) => the text is `input`
            steps.push({ id: 106, task_id: 7, kind: "assistant", name: "fake", input: "", output: "Done. I opened **Fab Editor** and typed your note.\n\n- opened `note.txt`\n- typed 17 characters\n\n```bash\nls ~/Notes\n```", risk: "", decision: "" })
            steps.push({ id: 107, task_id: 7, kind: "final", name: "", input: "", output: "Done. I opened **Fab Editor** and typed your note.\n\n- opened `note.txt`\n- typed 17 characters\n\n```bash\nls ~/Notes\n```", risk: "", decision: "" })
        }
        var approvals = phase >= 2 ? [{ id: 9, task_id: 7, step_id: 103, tool: "run_shell", input: JSON.stringify({ command: "rm -rf ~/old-notes" }), risk: "HIGH", reason: "deletes files", status: phase >= 3 ? "denied" : "pending" }] : []
        var questions = phase >= 3 ? [{ id: 5, task_id: 7, question: "Which folder should I use instead?", answer: "Use ~/Notes" }]
                      : (phase >= 2.5 ? [{ id: 5, task_id: 7, question: "Which folder should I use instead?", answer: null }] : [])
        var status = phase >= 3 ? "done" : (phase >= 2.5 ? "waiting_user" : (phase >= 2 ? "waiting_approval" : "running"))
        return { id: 7, title: "Open the editor and type hello", request: "Open the editor and type hello", status: status,
                 result: phase >= 3 ? "Done. I opened **Fab Editor** and typed your note." : null, steps: steps, approvals: approvals, questions: questions, watches: [] }
    }

    // ---- the transparent strip: every MouseArea in the applet must lie inside the card or the panel
    function inside(it, host) {
        var p = it.mapToItem(host, 0, 0)
        return p.x >= -0.5 && p.y >= -0.5 && p.x + it.width <= host.width + 0.5 && p.y + it.height <= host.height + 0.5
    }
    function strayMouseAreas() {
        var bad = []
        function walk(it) {
            for (var i = 0; i < it.children.length; i++) {
                var c = it.children[i]
                if (c === h) continue
                var s = String(c)
                if (s.indexOf("QQuickMouseArea") === 0 && c.width > 0 && c.height > 0 && !(inside(c, card) || inside(c, panel))) bad.push(s)
                walk(c)
            }
        }
        walk(bar)
        return bad
    }
    function rootHandlers() {   // pointer handlers attached to the root item itself would cover the whole strip
        var found = []
        for (var i = 0; i < bar.data.length; i++) { var s = String(bar.data[i]); if (/^QQuick(Hover|Tap|Point|Drag|Pinch|Wheel)Handler/.test(s)) found.push(s) }
        return found
    }

    function run() {
        var w = h.Window.window
        console.log("window " + (w ? Math.round(w.width) + "x" + Math.round(w.height) : "none") + ", applet " + Math.round(bar.width) + "x" + Math.round(bar.height) + ", screen " + Screen.width + "x" + Screen.height
                    + ", card " + Math.round(card.width) + "x" + Math.round(card.height) + " at " + Math.round(card.x) + "," + Math.round(card.y) + ", maxPanelHeight " + bar.maxPanelHeight)
        check(bar.onDesktop === true, "tall strip => desktop mode")
        check(bar.height >= card.height + 8 + 120, "the strip leaves room for the panel under the card (" + Math.round(bar.height - card.height) + " px)")
        check(card.y === 0, "card sits at the top of the strip")
        check(popup.active === false && popup.item === null, "no PlasmaCore.Dialog exists on the desktop (popup loader inactive)")
        check(panel.x === card.x && panel.width === card.width, "panel takes the card's x and width")
        check(panel.y === card.y + card.height + 8, "panel.y == card.y + card.height + 8 (" + panel.y + ")")
        check(panel.height === 0 && !panel.visible, "panel is folded (0 px, hidden) with no conversation")
        check(bar.childAt(2, 2) === null && bar.childAt(2, bar.height - 2) === null, "empty strip corners have no item under the pointer")
        check(rootHandlers().length === 0, "no pointer handler on the strip itself")
        check(bar.markState === "idle" || bar.markState === "error", "mark idle/error at start with no daemon (" + bar.markState + ")")
        var rows = [
            makeRow({ kind: "user", text: "Open the editor and type hello", status: "request" }),
            makeRow({ kind: "assistant", text: "Hi **there** `x`\n\n- a\n- b\n\n```py\nprint(1)\n```", status: "final" }),
            makeRow({ kind: "tools", count: 3, expanded: true, status: "running", group: 0 }),
            makeRow({ kind: "step", name: "open_app", running: "Opening Fab Editor", done: "Opened Fab Editor", icon: "kate", iconFallback: "window-new", appIcon: true, status: "running", narration: "Opening Fab Editor for you now.", current: true, group: 0 }),
            makeRow({ kind: "step", name: "type_text", running: "Typing into Fab Editor", done: "Typed into Fab Editor", icon: "input-keyboard", iconFallback: "system-run", status: "running", typed: "Hello from Fab OS", subtitle: "…then Enter", group: 0 }),
            makeRow({ kind: "step", name: "run_shell", running: "Running a command", done: "Ran a command", icon: "utilities-terminal", iconFallback: "system-run", status: "denied", subtitle: "Not allowed — skipped", group: 0 }),
            makeRow({ kind: "approval", approvalId: 9, title: "Running a command", risk: "HIGH", narration: "deletes files", status: "pending", icon: "utilities-terminal", iconFallback: "system-run" }),
            makeRow({ kind: "question", questionId: 5, text: "Which folder should I use instead?", status: "pending" }),
            makeRow({ kind: "error", text: "The model declined this request (policy)." }),
            makeRow({ kind: "note", text: "Trying again…" })
        ]
        var ok = true
        for (var i = 0; i < rows.length; i++) { if (!rows[i]) ok = false; else rows[i].y = 2000 }
        check(ok, "every ConvoDelegate kind instantiates (" + rows.length + " kinds)")
        // the microphone, for real: fabos-voice listen-once runs inside the image (no PipeWire / no capture device there)
        bar.startListening()
        check(bar.listening === true && bar.markState === "listening", "mic tap starts listening: red dot state on the mark")
        check(statusText.visible && statusText.text === "Listening…", "status line says Listening… while recording")
        // a submit with no daemon: curl fails, the request stays in the bar (checked in stage 2)
        field.text = "Open the editor and type hello"
        bar.daemonUp = true; bar.configured = true; bar.aiEnabled = true
        bar.submit()
        check(bar.sending === true && field.text === "", "submit posts and clears the field while sending")
        stage2.start()
    }
    Timer { id: stage2; interval: 2500; onTriggered: {
        check(bar.sending === false && field.text === "Open the editor and type hello", "no daemon: request restored to the bar, status says so (" + bar.status.slice(0, 40) + ")")
        check(bar.panelMode === "closed", "no panel without a task")
        check(bar.listening === false && bar.voiceHint.length > 0, "real fabos-voice listen-once failed here and the reason is in the status line: \"" + bar.voiceHint + "\"")
        check(statusText.visible && statusText.text === bar.voiceHint, "status line shows the voice reason")
        // synthetic CLI outcomes: the CLI's own last stderr line is what the user reads
        bar.handle("listen", 0, 4, "", "No microphone found on this computer.")
        check(bar.voiceHint === "No microphone found on this computer.", "exit 4: stderr line becomes the status (" + bar.voiceHint + ")")
        bar.handle("listen", 0, 4, "", "chime\nSpeech recognition is not available: no offline model and no cloud provider key.")
        check(bar.voiceHint === "Speech recognition is not available: no offline model and no cloud provider key.", "exit 4: the LAST stderr line is used")
        bar.handle("listen", 0, 127, "", "sh: 1: fabos-voice: not found")
        check(bar.voiceHint === "Voice is not installed on this machine (fabos-voice is missing)", "exit 127 => voice not installed")
        bar.handle("listen", 0, 3, "", "Sorry, I did not catch that. Say it once more?")
        check(bar.voiceHint === "I did not catch that. Tap the mic and try again.", "exit 3 => try-again hint")
        bar.handle("listen", 0, 1, "", "Traceback…\nRuntimeError: boom")
        check(bar.voiceHint === "Voice did not work just now — RuntimeError: boom", "other failures quote the last stderr line")
        bar.voiceHint = ""
        // fabos-voice status is cached for 30 s only
        bar.handle("voicestatus", 0, 127, "")
        check(bar.voiceChecked && !bar.voiceAvailable && bar.voiceReason === "Voice is not installed on this machine", "missing fabos-voice => mic dimmed with a reason")
        bar.handle("voicestatus", 0, 0, JSON.stringify({ wake: false, listening: false, stt: "whisper.cpp", tts: "espeak-ng", mic: false }))
        check(bar.voiceAvailable && bar.voiceReason.indexOf("No microphone found") === 0, "STT but no mic => reason names the microphone (" + bar.voiceReason + ")")
        bar.handle("voicestatus", 0, 0, JSON.stringify({ wake: false, listening: false, stt: "whisper.cpp", tts: "espeak-ng", mic: true }))
        check(bar.voiceAvailable && bar.voiceReason === "", "fabos-voice status with STT + mic => mic ready")
        var s0 = bar.serial
        bar.refreshVoice(false)
        check(bar.serial === s0, "a fresh status (< 30 s) is not asked again")
        bar.voiceStatusAt = Date.now() - 31000
        bar.refreshVoice(false)
        check(bar.serial === s0 + 1, "a status older than 30 s is asked again (a mic can be plugged in later)")
        field.text = ""
        // drive the state machine exactly as onNewData would
        bar.pendingRequest = "Open the editor and type hello"
        bar.onCreated(false, 0, { id: 7, status: "queued", parent_id: null })
        check(bar.rootTaskId === 7 && bar.taskId === 7, "task 7 becomes root + polled task")
        check(bar.panelMode === "open", "panel opens on create (" + bar.panelMode + ")")
        check(panel.height < 10, "panel starts at 0 px and grows (height now " + panel.height + ")")
        var saved = bar.savedTask()
        check(saved.root === 7 && saved.task === 7, "conversation remembered in Plasmoid.configuration (" + JSON.stringify(saved) + ")")
        check(kinds() === "user", "one user row after create: " + kinds())
        bar.ingest(taskJson(1))
        check(kinds() === "user,tools,step", "phase 1 rows: " + kinds())
        check(rowAt(2).status === "running" && rowAt(2).running === "Opening Fab Editor" && rowAt(2).icon === "kate" && rowAt(2).current === true, "open_app step is running/current with the app icon")
        check(rowAt(2).narration === "Opening Fab Editor for you now.", "narration carried onto the step row")
        check(bar.markState === "thinking", "mark thinking while running (" + bar.markState + ")")
        bar.ingest(taskJson(1))
        check(kinds() === "user,tools,step", "re-ingesting the same JSON appends nothing: " + kinds())
        bar.ingest(taskJson(2))
        check(kinds() === "user,tools,step,step,step,approval", "phase 2 rows: " + kinds())
        check(rowAt(2).status === "done" && rowAt(2).current === false, "open_app flipped to done, no longer current")
        check(rowAt(3).typed === "Hello from Fab OS" && rowAt(3).running === "Typing into Fab Editor", "type_text step carries the typed text and the target app")
        check(rowAt(4).status === "pending" && rowAt(4).subtitle === "", "run_shell awaiting approval, raw command hidden (ui.show_raw off)")
        check(rowAt(5).kind === "approval" && rowAt(5).risk === "HIGH" && rowAt(5).status === "pending" && rowAt(5).approvalId === 9, "approval card pending with HIGH risk")
        check(rowAt(1).count === 3 && rowAt(1).status === "running", "group chip counts 3 actions while running")
        check(bar.taskStatus === "waiting_approval" && bar.taskActive, "task waiting_approval is active")
        bar.decide(9, "denied")
        check(rowAt(5).status === "denied", "local Deny marks the approval row")
        bar.ingest(taskJson(2.5))
        check(kinds() === "user,tools,step,step,step,approval,question", "waiting_user adds one question row: " + kinds())
        bar.answer("Use ~/Notes")
        check(rowAt(6).status === "answered" && rowAt(7).kind === "user" && rowAt(7).status === "answer", "answer marks the question and appends the user's answer")
        bar.ingest(taskJson(3))
        check(kinds() === "user,tools,step,step,step,approval,question,user,assistant", "phase 3 rows (the daemon's answer step binds to the locally appended answer; no blank duplicate pill): " + kinds())
        check(rowAt(7).key === "t7s105" && rowAt(7).text === "Use ~/Notes", "answer row is now keyed by the daemon's step id")
        check(rowAt(8).status === "final" && rowAt(8).text.indexOf("**Fab Editor**") > 0, "assistant row promoted to final (no duplicate final row)")
        check(rowAt(4).status === "denied" && rowAt(4).subtitle === "Not allowed — skipped", "denied run_shell shows the friendly reason")
        check(rowAt(1).expanded === false && rowAt(2).shown === false, "groups fold when the task finishes")
        check(bar.taskStatus === "done" && !bar.taskActive && bar.resultText.length > 0, "task done, result captured")
        check(bar.markState === "done", "mark shows done (" + bar.markState + ")")
        bar.toggleGroup(0)
        check(rowAt(1).expanded === true && rowAt(2).shown === true, "chip click expands the group")
        bar.ingest(taskJson(3))
        check(rowAt(1).expanded === true, "a trailing poll does not re-fold an expanded group")
        var ext = taskJson(3)   // an answer given elsewhere (Fab AI Controls / CLI) has no local row: it is appended once, from `input`
        ext.steps.push({ id: 108, task_id: 7, kind: "answer", name: "user", input: "Answered from Fab AI Controls", output: "", risk: "", decision: "" })
        bar.ingest(ext)
        check(convo.count === 10 && rowAt(9).kind === "user" && rowAt(9).status === "answer" && rowAt(9).text === "Answered from Fab AI Controls", "external answer appended once from input (" + kinds() + ")")
        // follow-up threads under the root
        bar.pendingRequest = "and now save it"
        bar.onCreated(true, 0, { id: 8, status: "queued", parent_id: 7 })
        check(bar.rootTaskId === 7 && bar.taskId === 8 && kinds().split(",").length === 11, "follow-up keeps root 7, polls 8, appends one user row (" + kinds() + ")")
        saved = bar.savedTask()
        check(saved.root === 7 && saved.task === 8, "remembered ids follow the follow-up (" + JSON.stringify(saved) + ")")
        bar.ingest({ id: 8, status: "failed", result: null, error: "boom", steps: [{ id: 200, task_id: 8, kind: "error", name: "", input: "", output: "The model declined this request (policy)." }], approvals: [], questions: [] })
        check(rowAt(11).kind === "error" && bar.markState === "error", "failed task shows the error row and an amber mark (" + bar.markState + ")")
        check(rowAt(1).expanded === true && rowAt(2).shown === true, "a later task finishing does not fold the earlier group the user opened")
        bar.handle("retry", 0, 0, JSON.stringify({ id: 9, retry_of: 8, parent_id: 7 }))
        check(bar.taskId === 9 && bar.rootTaskId === 7, "retry of a follow-up keeps the root")
        bar.handle("retry", 0, 0, JSON.stringify({ id: 10, retry_of: 7, parent_id: null }))
        check(bar.rootTaskId === 10, "retry of the root re-roots the conversation")
        bar.typeInto("save it as notes")            // the voice transcript path: typewriter into the field, then submit
        bar.handle("status", 0, 0, JSON.stringify({ mode: "auto", provider_ready: false, ai_enabled: true, tasks: {}, pending_approvals: 0 }))
        check(!bar.configured && bar.status.indexOf("No AI provider") === 0, "status without a provider shows the settings hint")
        // Escape / a click on the mark fold the panel to the pill and unfold it again
        bar.toggleCollapse()
        check(bar.panelMode === "min", "mark click / Escape folds the panel to the pill")
        bar.toggleCollapse()
        check(bar.panelMode === "open", "a second click unfolds it")
        bar.panelMode = "min"
        check(bar.panelMode === "min" && panel.contentTarget === 40, "minimized: the panel's target is the 40 px pill (" + panel.contentTarget + ")")
        minTimer.start()
    } }
    Timer { id: minTimer; interval: 600; onTriggered: {
        check(panel.visible && Math.abs(panel.height - 40) <= 1, "panel stays inside the applet and folds to the pill on Minimize (" + panel.height + " px)")
        check(panel.y === card.y + card.height + 8 && panel.width === card.width, "pill keeps the panel's place under the card")
        bar.panelMode = "open"
        bar.taskStatus = "running"; bar.taskId = 10          // show the live state (spinner, typing dots) in the render
        grabTimer.start()
    } }
    Timer { id: grabTimer; interval: 1200; onTriggered: {
        check(field.text === "save it as notes", "typewriter revealed the whole transcript (" + field.text + ")")
        check(panel.height > 100 && Math.abs(panel.height - panel.contentTarget) <= 1 && panel.height <= bar.maxPanelHeight, "panel height animated to its content (" + panel.height + " of max " + bar.maxPanelHeight + " px)")
        check(panel.y + panel.height <= bar.height, "panel ends inside the strip")
        var stray = strayMouseAreas()
        check(stray.length === 0, "every MouseArea lies inside the card or the panel (stray: " + stray.length + ")")
        check(bar.childAt(2, 2) === null, "top-left of the strip is still empty with the panel open")
        h.grabsPending = 2
        bar.grabToImage(function (r) { r.saveToFile("/out/askbar-bar.png"); console.log("PASS grabbed bar (the whole strip: card + panel stack)"); if (--h.grabsPending === 0) feedTimer.start() })
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-panel.png"); console.log("PASS grabbed panel (scrolled to the end)"); if (--h.grabsPending === 0) feedTimer.start() })
    } }
    Timer { id: feedTimer; interval: 100; onTriggered: { list.follow = false; list.positionViewAtBeginning(); feedGrab.start() } }
    Timer { id: feedGrab; interval: 400; onTriggered: {
        check(list.atYBeginning, "list scrolled to the top for the feed render")
        panel.grabToImage(function (r) { r.saveToFile("/out/askbar-feed.png"); console.log("PASS grabbed feed (top: request, chip, live step cards)"); closeTimer.start() })
    } }
    Timer { id: closeTimer; interval: 200; onTriggered: {
        // Edit prompt while the panel shrinks: the next submit must be a fresh task, and a task created inside the
        // 300 ms shrink window must cancel the pending close and re-open the panel for the new conversation
        bar.configured = true; bar.aiEnabled = true; bar.taskStatus = "failed"
        check(bar.markState === "error", "failed task tints the mark amber while the panel is open (" + bar.markState + ")")
        bar.editPrompt()
        check(field.text === bar.taskRequest && field.text.length > 0 && bar.closing && bar.panelMode === "open" && !bar.followUp, "edit prompt: request back in the bar, panel shrinking, next submit is not a follow-up")
        bar.pendingRequest = "Open the editor and type hi"
        bar.onCreated(false, 0, { id: 12, status: "queued", parent_id: null })
        check(!bar.closing && bar.panelMode === "open" && bar.rootTaskId === 12 && bar.taskId === 12 && kinds() === "user", "task created during the shrink: close cancelled, conversation re-rooted (" + kinds() + ")")
        raceTimer.start()
    } }
    Timer { id: raceTimer; interval: 500; onTriggered: {
        check(bar.panelMode === "open" && panel.visible && bar.openProgress > 0.99, "panel still open 500 ms later (pending close was cancelled; progress " + bar.openProgress.toFixed(2) + ")")
        bar.taskStatus = "failed"
        bar.closePanel(); finish.start()
    } }
    Timer { id: finish; interval: 700; onTriggered: {
        check(bar.panelMode === "closed" && !panel.visible && panel.height === 0, "closePanel folds the panel away after the shrink animation (" + bar.panelMode + ", " + panel.height + " px)")
        check(bar.taskStatus === "" && bar.markState === "idle", "dismissing the panel forgets the failed task: the mark returns to idle (" + bar.markState + ")")
        var saved = bar.savedTask()
        check(saved.root === 0 && saved.task === 0, "a dismissed conversation is forgotten in Plasmoid.configuration (" + JSON.stringify(saved) + ")")
        // remembered conversation across a re-layout: GET /tasks/{saved} on load
        bar.handle("restore", 21, 0, JSON.stringify({ id: 21, status: "done", request: "old one", result: "x", steps: [], approvals: [], questions: [] }))
        check(bar.panelMode === "closed" && bar.savedTask().task === 0, "a remembered task that already finished is forgotten, the panel stays closed")
        var live = taskJson(1); live.id = 21; live.parent_id = null
        bar.handle("restore", 21, 0, JSON.stringify(live))
        check(bar.panelMode === "open" && bar.rootTaskId === 21 && bar.taskId === 21 && kinds() === "user,tools,step", "a remembered task that is still active reopens the panel with its request + live rows (" + kinds() + ")")
        check(bar.savedTask().task === 21 && bar.savedTask().root === 21, "and is remembered again")
        // compact (panel) form: only then does a PlasmaCore.Dialog exist, and the same conversation moves into it
        popup.active = true
        check(popup.item !== null && popup.item.mainItem !== null && panelMain.parent === popup.item.mainItem, "compact form: PlasmaCore.Dialog created and the conversation moved into it")
        check(popup.item.visible === true, "compact popup is visible while the conversation is open")
        popup.active = Qt.binding(function () { return bar.compact })
        check(popup.item === null && panelMain.parent === panel, "back on the desktop: popup gone, conversation back inside the applet")
        console.log("HARNESS DONE failures=" + h.failures)
        killer.connectSource("kill -TERM $PPID 2>/dev/null || pkill -x plasmawindowed")
    } }
}
