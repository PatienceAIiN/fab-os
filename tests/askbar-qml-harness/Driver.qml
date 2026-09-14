import QtQuick
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import "../ui" as Ask

// Headless driver for the ask bar. tests/askbar-qml-test.sh copies the plasmoid into a temp package, appends one
// Loader line to that COPY of main.qml which loads this file and hands over the root item and its ids, then runs the
// package through plasmawindowed with QT_QPA_PLATFORM=offscreen. The driver feeds main.qml the JSON the daemon would
// return for one task and checks the conversation rows it builds; every ConvoDelegate kind and every AiMark state are
// instantiated too. Prints PASS/FAIL lines and "HARNESS DONE failures=N", renders /out/askbar-{bar,panel,feed}.png, then
// stops plasmawindowed.
Item {
    id: h
    property var bar: null
    property var convo: null
    property var dialog: null
    property var field: null
    property var list: null
    property int failures: 0
    property int grabsPending: 0
    function check(cond, msg) { if (cond) console.log("PASS " + msg); else { h.failures++; console.log("FAIL " + msg) } }
    function kinds() { var k = []; for (var i = 0; i < convo.count; i++) k.push(convo.get(i).kind); return k.join(",") }
    function rowAt(i) { return convo.get(i) }
    onListChanged: if (bar && convo && dialog && field && list) startTimer.start()
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
            steps.push({ id: 105, task_id: 7, kind: "answer", name: "user", input: "", output: "Use ~/Notes", risk: "", decision: "" })
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

    function run() {
        check(bar.onDesktop === true, "150 px strip => desktop mode")
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
        field.text = ""
        // drive the state machine exactly as onNewData would
        bar.pendingRequest = "Open the editor and type hello"
        bar.onCreated(false, 0, { id: 7, status: "queued", parent_id: null })
        check(bar.rootTaskId === 7 && bar.taskId === 7, "task 7 becomes root + polled task")
        check(bar.panelMode === "open", "panel opens on create (" + bar.panelMode + ")")
        check(dialog.visible === true, "dialog window is visible")
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
        check(kinds() === "user,tools,step,step,step,approval,question,user,user,assistant", "phase 3 rows: " + kinds())
        check(rowAt(9).status === "final" && rowAt(9).text.indexOf("**Fab Editor**") > 0, "assistant row promoted to final (no duplicate final row)")
        check(rowAt(4).status === "denied" && rowAt(4).subtitle === "Not allowed — skipped", "denied run_shell shows the friendly reason")
        check(rowAt(1).expanded === false && rowAt(2).shown === false, "groups fold when the task finishes")
        check(bar.taskStatus === "done" && !bar.taskActive && bar.resultText.length > 0, "task done, result captured")
        check(bar.markState === "done", "mark shows done (" + bar.markState + ")")
        bar.toggleGroup(0)
        check(rowAt(1).expanded === true && rowAt(2).shown === true, "chip click expands the group")
        bar.ingest(taskJson(3))
        check(rowAt(1).expanded === true, "a trailing poll does not re-fold an expanded group")
        // follow-up threads under the root
        bar.pendingRequest = "and now save it"
        bar.onCreated(true, 0, { id: 8, status: "queued", parent_id: 7 })
        check(bar.rootTaskId === 7 && bar.taskId === 8 && kinds().split(",").length === 11, "follow-up keeps root 7, polls 8, appends one user row (" + kinds() + ")")
        bar.ingest({ id: 8, status: "failed", result: null, error: "boom", steps: [{ id: 200, task_id: 8, kind: "error", name: "", input: "", output: "The model declined this request (policy)." }], approvals: [], questions: [] })
        check(rowAt(11).kind === "error" && bar.markState === "error", "failed task shows the error row and an amber mark (" + bar.markState + ")")
        check(rowAt(1).expanded === true && rowAt(2).shown === true, "a later task finishing does not fold the earlier group the user opened")
        bar.handle("retry", 0, 0, JSON.stringify({ id: 9, retry_of: 8, parent_id: 7 }))
        check(bar.taskId === 9 && bar.rootTaskId === 7, "retry of a follow-up keeps the root")
        bar.handle("retry", 0, 0, JSON.stringify({ id: 10, retry_of: 7, parent_id: null }))
        check(bar.rootTaskId === 10, "retry of the root re-roots the conversation")
        bar.handle("voicestatus", 0, 127, "")
        check(bar.voiceChecked && !bar.voiceAvailable, "missing fabos-voice => mic disabled")
        bar.handle("voicestatus", 0, 0, JSON.stringify({ wake: false, listening: false, stt: "whisper.cpp", tts: "espeak-ng", mic: true }))
        check(bar.voiceAvailable, "fabos-voice status with STT => mic enabled")
        bar.handle("listen", 0, 3, "")
        check(bar.voiceHint === "I did not catch that. Tap the mic and try again.", "exit 3 => try-again hint")
        bar.typeInto("save it as notes")            // the voice transcript path: typewriter into the field, then submit
        bar.handle("status", 0, 0, JSON.stringify({ mode: "auto", provider_ready: false, ai_enabled: true, tasks: {}, pending_approvals: 0 }))
        check(!bar.configured && bar.status.indexOf("No AI provider") === 0, "status without a provider shows the settings hint")
        bar.panelMode = "min"
        check(bar.panelMode === "min" && dialog.visible, "minimized pill keeps the dialog")
        bar.panelMode = "open"
        bar.taskStatus = "running"; bar.taskId = 10          // show the live state (spinner, typing dots) in the render
        grabTimer.start()
    } }
    Timer { id: grabTimer; interval: 1200; onTriggered: {
        check(field.text === "save it as notes", "typewriter revealed the whole transcript (" + field.text + ")")
        h.grabsPending = 2
        bar.grabToImage(function (r) { r.saveToFile("/out/askbar-bar.png"); console.log("PASS grabbed bar"); if (--h.grabsPending === 0) feedTimer.start() })
        dialog.mainItem.grabToImage(function (r) { r.saveToFile("/out/askbar-panel.png"); console.log("PASS grabbed panel (scrolled to the end)"); if (--h.grabsPending === 0) feedTimer.start() })
    } }
    Timer { id: feedTimer; interval: 100; onTriggered: { list.follow = false; list.positionViewAtBeginning(); feedGrab.start() } }
    Timer { id: feedGrab; interval: 400; onTriggered: {
        check(list.atYBeginning, "list scrolled to the top for the feed render")
        dialog.mainItem.grabToImage(function (r) { r.saveToFile("/out/askbar-feed.png"); console.log("PASS grabbed feed (top: request, chip, live step cards)"); closeTimer.start() })
    } }
    Timer { id: closeTimer; interval: 200; onTriggered: { bar.closePanel(); finish.start() } }
    Timer { id: finish; interval: 700; onTriggered: {
        check(bar.panelMode === "closed" && !dialog.visible, "closePanel hides the dialog after the shrink animation (" + bar.panelMode + ")")
        console.log("HARNESS DONE failures=" + h.failures)
        killer.connectSource("kill -TERM $PPID 2>/dev/null || pkill -x plasmawindowed")
    } }
}
